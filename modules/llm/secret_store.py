from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from modules.repositories.local_json_utils import atomic_write_json, load_json_document


class SecretStore:
    """Local secret storage. Values are never returned by public status APIs."""

    ENVIRONMENT_KEY = "DEEPSEEK_API_KEY"

    def __init__(self, project_root: Path, path: Optional[Path] = None) -> None:
        self.project_root = Path(project_root)
        self.path = Path(path) if path is not None else (
            self.project_root / "data" / "private" / "llm_secrets.json"
        )
        self.lock_dir = self.project_root / "data" / "private" / ".locks"
        self.backup_dir = self.project_root / "data" / "private" / "backups"

    def get_deepseek_key(self) -> Tuple[str, str]:
        environment_value = str(os.environ.get(self.ENVIRONMENT_KEY, "")).strip()
        if environment_value:
            return environment_value, "environment"
        data = self._load()
        value = str(data.get("deepseek_api_key", "")).strip()
        return (value, "private_file") if value else ("", "missing")

    def save_deepseek_key(self, value: str) -> bool:
        key = str(value).strip()
        if not key:
            return self.clear_deepseek_key()
        data = self._load()
        data["deepseek_api_key"] = key
        return atomic_write_json(
            self.path,
            data,
            label="Secrets",
            lock_dir=self.lock_dir,
        )

    def clear_deepseek_key(self) -> bool:
        data = self._load()
        data.pop("deepseek_api_key", None)
        return atomic_write_json(
            self.path,
            data,
            label="Secrets",
            lock_dir=self.lock_dir,
        )

    def status(self) -> Dict[str, object]:
        key, source = self.get_deepseek_key()
        return {
            "configured": bool(key),
            "source": source,
            "masked": self.mask(key) if key else "",
        }

    @staticmethod
    def mask(value: str) -> str:
        text = str(value)
        if len(text) <= 8:
            return "*" * len(text)
        return text[:3] + "*" * min(12, len(text) - 6) + text[-3:]

    def _load(self) -> Dict[str, object]:
        return load_json_document(
            self.path,
            {},
            label="Secrets",
            lock_dir=self.lock_dir,
            backup_dir=self.backup_dir,
        )
