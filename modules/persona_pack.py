"""Data-only persona pack loading and bounded retrieval helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


class PersonaPackError(ValueError):
    """Raised when a persona pack cannot be used safely."""


REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "persona_id",
    "display_name",
    "version",
    "language",
    "description",
    "files",
    "enabled",
    "provenance",
    "compatibility_version",
    "generator",
    "source_type",
    "copyright_notice",
    "created_at",
}
REQUIRED_FILE_KEYS = {
    "identity",
    "behavior",
    "speaking_style",
    "truth_and_boundaries",
    "relationships",
    "worldview",
    "dialogue_examples",
    "evaluation_cases",
    "assets",
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PersonaPackError(f"cannot read {path.name}: {error}") from error


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as error:
        raise PersonaPackError(f"invalid JSON in {path.name}") from error


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise PersonaPackError(
                f"invalid JSONL in {path.name}:{line_number}"
            ) from error
        if not isinstance(record, dict):
            raise PersonaPackError(f"invalid JSONL object in {path.name}:{line_number}")
        records.append(record)
    return records


def _keywords(text: str) -> set[str]:
    normalized = str(text or "").lower()
    latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    cjk_pairs = {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
        if "\u4e00" <= normalized[index] <= "\u9fff"
        and "\u4e00" <= normalized[index + 1] <= "\u9fff"
    }
    return latin | cjk_pairs


@dataclass(frozen=True)
class PersonaContextMaterial:
    """Bounded, query-relevant material for the single ContextBuilder path."""

    lore: str = ""
    relationships: str = ""
    dialogue_examples: str = ""
    lore_items: int = 0
    relationship_items: int = 0
    dialogue_example_items: int = 0


@dataclass
class PersonaPack:
    root: Path
    manifest: Dict[str, Any]
    identity: str
    behavior: str
    speaking_style: str
    truth_and_boundaries: str
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    worldview: str = ""
    dialogue_examples: List[Dict[str, Any]] = field(default_factory=list)
    evaluation_cases: List[Dict[str, Any]] = field(default_factory=list)
    assets: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "PersonaPack":
        root = Path(root)
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise PersonaPackError(f"missing manifest: {manifest_path}")
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise PersonaPackError("manifest must be an object")
        missing = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest))
        if missing:
            raise PersonaPackError("manifest missing fields: " + ", ".join(missing))
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise PersonaPackError("manifest files must be an object")
        missing_files = sorted(REQUIRED_FILE_KEYS - set(files))
        if missing_files:
            raise PersonaPackError("manifest file mappings missing: " + ", ".join(missing_files))
        for field_name in ("persona_id", "display_name", "version", "language"):
            if not str(manifest.get(field_name, "")).strip():
                raise PersonaPackError(f"manifest {field_name} cannot be empty")
        for key in REQUIRED_FILE_KEYS:
            file_name = str(files.get(key, "")).strip()
            if not file_name or not (root / file_name).is_file():
                raise PersonaPackError(f"required pack file missing: {key}")

        relationships = _read_json(root / str(files["relationships"]))
        assets = _read_json(root / str(files["assets"]))
        if not isinstance(relationships, list):
            raise PersonaPackError("relationships must be a JSON array")
        if not isinstance(assets, dict):
            raise PersonaPackError("assets must be a JSON object")
        return cls(
            root=root,
            manifest=manifest,
            identity=_read_text(root / str(files["identity"])),
            behavior=_read_text(root / str(files["behavior"])),
            speaking_style=_read_text(root / str(files["speaking_style"])),
            truth_and_boundaries=_read_text(root / str(files["truth_and_boundaries"])),
            relationships=[item for item in relationships if isinstance(item, dict)],
            worldview=_read_text(root / str(files["worldview"])),
            dialogue_examples=_read_jsonl(root / str(files["dialogue_examples"])),
            evaluation_cases=_read_jsonl(root / str(files["evaluation_cases"])),
            assets=assets,
        )

    @property
    def persona_id(self) -> str:
        return str(self.manifest["persona_id"])

    @property
    def display_name(self) -> str:
        return str(self.manifest["display_name"])

    def fixed_core(self) -> str:
        """The only persona text included on every turn; lore is deliberately absent."""
        return "\n\n".join(
            (
                f"[Persona: {self.display_name} ({self.persona_id})]",
                "[Identity]\n" + self.identity,
                "[Behavior]\n" + self.behavior,
                "[Speaking style]\n" + self.speaking_style,
                "[Truth and boundaries]\n" + self.truth_and_boundaries,
            )
        )

    def retrieve_context(self, query: str, *, limit: int = 2) -> PersonaContextMaterial:
        """Return only query-relevant distilled facts/examples, never the full pack."""
        terms = _keywords(query)
        normalized_query = str(query or "")
        scene_hints = {
            "user_tired": ("累", "疲惫", "不想学", "没力气"),
            "identity": ("你是谁", "介绍", "身份"),
            "human_status": ("真人", "人类", "ai", "人工智能"),
            "plan_query": ("计划", "待办", "任务"),
        }
        matched_scenes = []
        for scene, hints in scene_hints.items():
            if any(hint in normalized_query.lower() for hint in hints):
                terms.add(scene)
                matched_scenes.append(scene)
        if not terms:
            return PersonaContextMaterial()
        lore_blocks = [block.strip() for block in re.split(r"\n\s*\n", self.worldview) if block.strip()]
        matched_lore = self._rank_texts(lore_blocks, terms, limit)
        matched_relationships = self._rank_records(self.relationships, terms, limit)
        matched_examples = [
            dict(item)
            for item in self.dialogue_examples
            if str(item.get("scene", "")) in matched_scenes
        ][:limit] or self._rank_records(self.dialogue_examples, terms, limit)
        return PersonaContextMaterial(
            lore="\n\n".join(matched_lore),
            relationships="\n".join(
                "- " + str(item.get("summary") or item.get("name") or "")
                for item in matched_relationships
                if str(item.get("summary") or item.get("name") or "").strip()
            ),
            dialogue_examples="\n".join(
                "- scene={scene}; user={user}; assistant={assistant}".format(
                    scene=str(item.get("scene", "")),
                    user=str(item.get("user", "")),
                    assistant=str(item.get("assistant", "")),
                )
                for item in matched_examples
            ),
            lore_items=len(matched_lore),
            relationship_items=len(matched_relationships),
            dialogue_example_items=len(matched_examples),
        )

    @staticmethod
    def _rank_texts(items: Iterable[str], terms: set[str], limit: int) -> List[str]:
        ranked = []
        for index, item in enumerate(items):
            score = len(_keywords(item) & terms)
            if score:
                ranked.append((score, -index, item))
        return [item for _score, _index, item in sorted(ranked, reverse=True)[:limit]]

    @staticmethod
    def _rank_records(
        items: Iterable[Mapping[str, Any]], terms: set[str], limit: int
    ) -> List[Dict[str, Any]]:
        ranked = []
        for index, item in enumerate(items):
            text = " ".join(str(value) for value in item.values())
            score = len(_keywords(text) & terms)
            if score:
                ranked.append((score, -index, dict(item)))
        return [item for _score, _index, item in sorted(ranked, reverse=True)[:limit]]
