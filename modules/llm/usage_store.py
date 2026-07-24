from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from modules.llm.contracts import ProviderResponse
from modules.repositories.local_json_utils import atomic_write_json, json_transaction, load_json_document


class ModelUsageStore:
    def __init__(self, project_root: Path, path: Optional[Path] = None) -> None:
        self.project_root = Path(project_root)
        self.path = Path(path) if path is not None else (
            self.project_root / "data" / "private" / "model_usage.json"
        )
        self.lock_dir = self.project_root / "data" / "private" / ".locks"
        self.backup_dir = self.project_root / "data" / "private" / "backups"

    def record(self, response: ProviderResponse, *, route_reason: str) -> None:
        try:
            with json_transaction(self.path, self.lock_dir):
                data = self._load()
                totals = data.setdefault("totals", {})
                key = f"{response.provider}:{response.model}"
                item = totals.setdefault(
                    key,
                    {
                        "provider": response.provider,
                        "model": response.model,
                        "requests": 0,
                        "successes": 0,
                        "failures": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "tool_calls": 0,
                        "latency_ms": 0,
                    },
                )
                item["requests"] = int(item.get("requests", 0)) + 1
                bucket = "successes" if response.ok else "failures"
                item[bucket] = int(item.get(bucket, 0)) + 1
                usage = response.usage
                input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
                output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
                total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
                item["input_tokens"] = int(item.get("input_tokens", 0)) + input_tokens
                item["output_tokens"] = int(item.get("output_tokens", 0)) + output_tokens
                item["total_tokens"] = int(item.get("total_tokens", 0)) + total_tokens
                item["tool_calls"] = int(item.get("tool_calls", 0)) + len(response.tool_calls)
                item["latency_ms"] = int(item.get("latency_ms", 0)) + int(response.latency_ms)
                data["updated_at"] = datetime.now().isoformat(timespec="seconds")
                recent = data.setdefault("recent", [])
                recent.append(
                    {
                        "provider": response.provider,
                        "model": response.model,
                        "success": response.ok,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                        "tool_calls": len(response.tool_calls),
                        "latency_ms": response.latency_ms,
                        "route_reason": str(route_reason)[:80],
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                data["recent"] = recent[-100:]
                atomic_write_json(
                    self.path,
                    data,
                    label="ModelUsage",
                    lock_dir=self.lock_dir,
                )
        except (OSError, TimeoutError, TypeError, ValueError):
            print("[ModelUsage] record failed", flush=True)

    def summary(self) -> Dict[str, object]:
        data = self._load()
        return {
            "updated_at": data.get("updated_at", ""),
            "totals": data.get("totals", {}),
            "recent": data.get("recent", [])[-20:],
        }

    def clear(self) -> bool:
        return atomic_write_json(
            self.path,
            self._empty(),
            label="ModelUsage",
            lock_dir=self.lock_dir,
        )

    def _load(self) -> Dict[str, object]:
        return load_json_document(
            self.path,
            self._empty(),
            label="ModelUsage",
            lock_dir=self.lock_dir,
            backup_dir=self.backup_dir,
        )

    @staticmethod
    def _empty() -> Dict[str, object]:
        return {"schema_version": "1.0", "updated_at": "", "totals": {}, "recent": []}
