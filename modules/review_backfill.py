from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

from modules.development_log import get_development_log


class ReviewDataCorruptionError(RuntimeError):
    """Signals that startup review data needs visible user attention."""


class ReviewBackfillRunner:
    """Run the one-date startup backfill without any model dependency."""

    def __init__(
        self,
        growth_manager,
        *,
        now_provider: Callable[[], datetime] = datetime.now,
        diagnostics_dir: Optional[Path] = None,
        notification_callback: Optional[Callable[[str], None]] = None,
        failure_notification_threshold: int = 2,
    ) -> None:
        self.growth_manager = growth_manager
        self.now_provider = now_provider
        default_root = Path(
            getattr(growth_manager, "private_dir", Path.cwd() / "data" / "private")
        )
        self.diagnostics_dir = Path(
            diagnostics_dir or default_root / "diagnostics"
        )
        self.diagnostic_path = self.diagnostics_dir / "review_backfill.jsonl"
        self.state_path = self.diagnostics_dir / "review_backfill_state.json"
        self.notification_callback = notification_callback
        self.failure_notification_threshold = max(
            2, int(failure_notification_threshold)
        )

    def run(self) -> Dict[str, object]:
        target_date = (self.now_provider().date() - timedelta(days=1)).isoformat()
        logger = get_development_log()
        trace = logger.new_trace(source="startup_backfill")
        logger.event(trace, "startup_backfill_started", date=target_date)
        try:
            with logger.bind(trace):
                self._validate_growth_documents()
                result = self.growth_manager.ensure_review_for_date(target_date)
            self._write_failure_count(0)
            fields = {
                "status": result.get("status"), "date": target_date,
                "success": result.get("status") in {"created", "existing", "empty"},
                "persisted": bool(result.get("created")),
                "changed": bool(result.get("created")),
            }
            entry = result.get("entry")
            if isinstance(entry, dict):
                fields["record_id"] = entry.get("uid")
                fields["revision"] = entry.get("revision")
                fields["changed_resource_ids"] = (
                    [entry["uid"]] if result.get("created") and entry.get("uid") else []
                )
                review = entry.get("review")
                if isinstance(review, dict):
                    fields.update({
                        key: review.get(key)
                        for key in ("total", "done", "pending", "action_count")
                    })
            logger.event(trace, "startup_backfill_finished", **fields)
            return dict(result)
        except Exception as error:  # startup must never crash the desktop pet
            count = self._read_failure_count() + 1
            self._write_failure_count(count)
            is_corruption = isinstance(
                error, (ReviewDataCorruptionError, json.JSONDecodeError)
            )
            self._append_failure(
                target_date=target_date,
                error_type=type(error).__name__,
                consecutive_failures=count,
                data_corruption=is_corruption,
            )
            logger.record_exception(trace, error)
            logger.event(
                trace, "startup_backfill_finished", status="failed",
                success=False, persisted=False,
                date=target_date, error_type=type(error).__name__,
            )
            notified = bool(
                is_corruption or count >= self.failure_notification_threshold
            )
            if notified and self.notification_callback is not None:
                self.notification_callback(
                    "昨日成长复盘自动补全连续失败，请稍后在成长面板中检查本地数据。"
                    if not is_corruption
                    else "成长日志数据可能损坏，请先检查本地数据再继续使用自动补全。"
                )
            return {
                "status": "failed",
                "created": False,
                "entry": None,
                "target_date": target_date,
                "error_code": type(error).__name__,
                "consecutive_failures": count,
                "notified": notified,
            }

    def _validate_growth_documents(self) -> None:
        """Detect damaged source JSON before a fallback can look like an empty day."""
        for attribute in ("plan_file", "action_file", "growth_file"):
            raw_path = getattr(self.growth_manager, attribute, None)
            if raw_path is None:
                continue
            path = Path(raw_path)
            if not path.exists():
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ReviewDataCorruptionError("invalid_growth_document") from error
            if not isinstance(document, dict):
                raise ReviewDataCorruptionError("invalid_growth_document")

    def _read_failure_count(self) -> int:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return max(0, int(data.get("consecutive_failures", 0) or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _write_failure_count(self, value: int) -> None:
        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {"consecutive_failures": max(0, int(value))},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.state_path)
        except OSError:
            # Diagnostics failure must not turn a successful backfill into a UI
            # failure or expose a private filesystem path.
            print("[ReviewBackfill] diagnostic_state_write_failed", flush=True)

    def _append_failure(
        self,
        *,
        target_date: str,
        error_type: str,
        consecutive_failures: int,
        data_corruption: bool,
    ) -> None:
        record = {
            "event": "startup_yesterday_review_failed",
            "target_date": target_date,
            "error_type": str(error_type),
            "consecutive_failures": int(consecutive_failures),
            "data_corruption": bool(data_corruption),
            "created_at": self.now_provider().isoformat(timespec="seconds"),
        }
        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            with self.diagnostic_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            print("[ReviewBackfill] diagnostic_append_failed", flush=True)
