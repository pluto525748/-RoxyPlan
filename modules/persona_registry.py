"""Registry for data-only persona packs; it owns neither user data nor conversations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from modules.persona_pack import PersonaPack, PersonaPackError


@dataclass(frozen=True)
class PersonaSelectionResult:
    success: bool
    requested_persona_id: str
    active_persona_id: str
    fallback_used: bool = False
    error_code: str = ""


class PersonaRegistry:
    """Discover validated packs and hold only the active pack identifier."""

    def __init__(
        self,
        personas_root: Path,
        *,
        active_persona_id: str = "roxy",
        fallback_persona_id: str = "roxy",
    ) -> None:
        self.personas_root = Path(personas_root)
        self.fallback_persona_id = str(fallback_persona_id or "roxy")
        self._packs: Dict[str, PersonaPack] = {}
        self._errors: Dict[str, str] = {}
        self._active_persona_id = self.fallback_persona_id
        self.scan()
        self.select(active_persona_id)

    def scan(self) -> None:
        self._packs = {}
        self._errors = {}
        if not self.personas_root.is_dir():
            self._errors["__root__"] = f"persona root not found: {self.personas_root}"
            return
        for manifest_path in sorted(self.personas_root.glob("*/manifest.json")):
            directory_name = manifest_path.parent.name
            try:
                pack = PersonaPack.load(manifest_path.parent)
            except PersonaPackError as error:
                self._errors[directory_name] = str(error)
                continue
            if bool(pack.manifest.get("enabled", True)):
                self._packs[pack.persona_id] = pack

    def available_personas(self) -> List[Dict[str, str]]:
        return [
            {
                "persona_id": pack.persona_id,
                "display_name": pack.display_name,
                "version": str(pack.manifest.get("version", "")),
                "description": str(pack.manifest.get("description", "")),
            }
            for _persona_id, pack in sorted(self._packs.items())
        ]

    @property
    def active_persona_id(self) -> str:
        return self._active_persona_id

    def current_pack(self) -> PersonaPack:
        pack = self._packs.get(self._active_persona_id)
        if pack is not None:
            return pack
        fallback = self._packs.get(self.fallback_persona_id)
        if fallback is None:
            raise PersonaPackError("no usable fallback persona pack: roxy")
        self._active_persona_id = fallback.persona_id
        return fallback

    def select(self, persona_id: str) -> PersonaSelectionResult:
        requested = str(persona_id or "").strip()
        if requested in self._packs:
            self._active_persona_id = requested
            return PersonaSelectionResult(True, requested, requested)
        fallback = self._packs.get(self.fallback_persona_id)
        if fallback is None:
            return PersonaSelectionResult(
                False, requested, "", error_code="fallback_persona_unavailable"
            )
        self._active_persona_id = fallback.persona_id
        return PersonaSelectionResult(
            False,
            requested,
            fallback.persona_id,
            fallback_used=True,
            error_code="persona_not_found",
        )

    def errors(self) -> Dict[str, str]:
        return dict(self._errors)
