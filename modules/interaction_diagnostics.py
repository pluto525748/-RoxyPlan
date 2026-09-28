from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional


class InteractionDiagnostics:
    """Bounded, opt-in diagnostics without retaining private message bodies."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_records: int = 100,
        persist_path: Optional[Path] = None,
    ) -> None:
        self.persist_path = Path(persist_path) if persist_path is not None else None
        self.enabled = bool(enabled)
        self._records = deque(maxlen=max(1, int(max_records)))
        self._pending: Dict[str, Dict[str, object]] = {}

    def begin(
        self,
        conversation_id: str,
        text: str,
        *,
        interaction_state_before: str = "idle",
    ) -> None:
        if not self.enabled:
            return
        raw = str(text or "")
        normalized = "".join(raw.lower().split())
        self._pending[str(conversation_id)] = {
            "normalized_text": {
                "length": len(normalized),
                "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            },
            "interaction_state_before": str(interaction_state_before or "idle"),
            "coordinator_decision": "fresh_turn",
            "route_source": "",
            "local_features": {},
            "semantic_parse_source": "",
            "semantic_decision": {},
            "validation_notes": [],
            "pipeline_diagnostics": {},
            "pipeline_outcome": "",
            "action_candidates": [],
            "resolver_result": [],
            "selected_object_ids": [],
            "confirmation_policy": [],
            "tool_calls": [],
            "tool_results": [],
            "postcondition_result": [],
            "interaction_state_after": "idle",
            "response_source": "",
            "provider": "",
            "model": "",
            "latency_ms": 0,
            "persona_context": {},
            "relevant_summary_provenance": [],
            "context_sections": [],
            "verified_turn_context": {},
        }

    def update(self, conversation_id: str, **values: object) -> None:
        if not self.enabled:
            return
        record = self._pending.get(str(conversation_id))
        if record is None:
            return
        for key, value in values.items():
            if key in record:
                record[key] = self._json_value(value)

    def finalize(
        self,
        conversation_id: str,
        *,
        request_id: str,
        status: str,
    ) -> Optional[Dict[str, object]]:
        if not self.enabled:
            return None
        record = self._pending.pop(str(conversation_id), None)
        if record is None:
            return None
        record["request_id"] = str(request_id or "untracked")
        record["status"] = str(status or "failed")
        record["created_at"] = datetime.now(timezone.utc).isoformat()
        record["pipeline"] = self._pipeline_events(record)
        safe = self._json_value(record)
        self._records.append(safe)
        self._append_persistent(safe)
        return dict(safe)

    def snapshot(self) -> List[Dict[str, object]]:
        if not self.enabled:
            return []
        return [dict(item) for item in self._records]

    def export(self, path: Path) -> bool:
        """Export already-sanitized records only when diagnostics are enabled."""
        if not self.enabled:
            return False
        target = Path(path)
        if target.suffix.lower() != ".json":
            raise ValueError("diagnostic export must use a .json file")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
        temporary.replace(target)
        return True

    def _append_persistent(self, record: Mapping[str, object]) -> None:
        if self.persist_path is None:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self.persist_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            print(
                f"[Diagnostic] persistence_failed error={type(error).__name__}",
                flush=True,
            )

    @staticmethod
    def _pipeline_events(record: Mapping[str, object]) -> List[str]:
        events = ["SemanticDecision", "Validation"]
        resolver_results = record.get("resolver_result", [])
        if isinstance(resolver_results, list) and any(
            isinstance(item, Mapping)
            and str(item.get("reason_code", "")).endswith("_message_resolved")
            for item in resolver_results
        ):
            events.append("ReferenceResolution")
        tool_results = record.get("tool_results", [])
        if isinstance(tool_results, list):
            for item in tool_results:
                if not isinstance(item, Mapping):
                    continue
                tool = str(item.get("tool", "unknown"))
                events.append(f"ToolExecution:{tool}")
                events.append(
                    f"ToolResult:{tool}:{'success' if item.get('success') else 'failed'}"
                )
                audit_action = str(item.get("memory_audit_action", "") or "")
                if audit_action:
                    events.append(f"MemoryAudit:{audit_action}")
        events.append("FinalResponse")
        return events

    @classmethod
    def _json_value(cls, value: object):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            return [cls._json_value(item) for item in value]
        return str(value)
