from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, Iterable, List, Mapping, Optional
from uuid import uuid4


SUGGESTION_SNAPSHOT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class SuggestionSnapshotObject:
    """One model suggestion in the exact order shown to the user."""

    stable_id: str
    display_order: int
    title: str
    consumed: bool = False

    @classmethod
    def from_option(cls, value: Mapping[str, object], display_order: int):
        title = str(value.get("title", "") or "").strip()
        if not title:
            return None
        stable_id = str(value.get("id", "") or "").strip() or str(display_order)
        return cls(
            stable_id=stable_id,
            display_order=max(1, int(display_order)),
            title=title,
            consumed=bool(value.get("consumed", False)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        return cls.from_option(
            {
                "id": value.get("stable_id", value.get("id", "")),
                "title": value.get("title", ""),
                "consumed": value.get("consumed", False),
            },
            int(value.get("display_order", 1) or 1),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "display_order": self.display_order,
            "title": self.title,
            "consumed": self.consumed,
        }


@dataclass(frozen=True)
class SuggestionSnapshot:
    """Conversation-scoped list that survives one successful selection."""

    conversation_id: str
    objects: List[SuggestionSnapshotObject] = field(default_factory=list)
    source_kind: str = "assistant_plan"
    source_turn_id: str = ""
    captured_at: str = ""
    snapshot_id: str = ""
    schema_version: str = SUGGESTION_SNAPSHOT_SCHEMA_VERSION
    target_date: str = ""

    @staticmethod
    def numbered_options(text: str) -> List[Dict[str, str]]:
        """Validate a whole flat list without inventing, trimming or renumbering tasks.

        The caller must establish a planning/advice purpose first. A numbered
        list alone is never evidence of a request to change application data.
        """
        source = str(text or "").strip()
        if not source or len(source) > 8000 or "```" in source:
            return []
        lines = source.splitlines()
        numbered = []
        for index, line in enumerate(lines):
            match = re.fullmatch(r"\s*(\d{1,2})([.、)）])(\s*)(.+?)\s*", line)
            if match is not None:
                # Indented numbered children and multiple numbered blocks must
                # not be flattened into a different list than the one displayed.
                if line[:1].isspace():
                    return []
                title = match.group(4)
                if match.group(2) == "." and not match.group(3) and title[:1].isdigit():
                    return []
                numbered.append((index, int(match.group(1)), title))
        if not numbered or len(numbered) > 20:
            return []
        first, last = numbered[0][0], numbered[-1][0]
        positions = {item[0] for item in numbered}
        if any(lines[index].strip() and index not in positions for index in range(first, last + 1)):
            return []
        options = []
        for order, (_, displayed_order, title) in enumerate(numbered, 1):
            if (
                displayed_order != order
                or not title.strip()
                or len(title) > 120
                or re.search(r"[?？]|(?:^|\s)\d+[.、)）]\s", title)
                or re.match(r"\[(?:待完成|已完成|完成)\]", title)
            ):
                return []
            options.append({"id": str(displayed_order), "title": title.strip()})
        return options

    @classmethod
    def for_options(
        cls,
        *,
        conversation_id: str,
        options: Iterable[Mapping[str, object]],
        source_kind: str = "assistant_plan",
        source_turn_id: str = "",
        captured_at: Optional[datetime] = None,
        target_date: str = "",
    ) -> "SuggestionSnapshot":
        objects: List[SuggestionSnapshotObject] = []
        for index, option in enumerate(options, 1):
            if not isinstance(option, Mapping):
                continue
            item = SuggestionSnapshotObject.from_option(option, index)
            if item is not None:
                objects.append(item)
        now = captured_at or datetime.now()
        return cls(
            conversation_id=str(conversation_id).strip(),
            objects=objects,
            source_kind=str(source_kind or "assistant_plan").strip(),
            source_turn_id=str(source_turn_id or "").strip(),
            captured_at=now.isoformat(timespec="seconds"),
            snapshot_id="suggestion_" + uuid4().hex,
            target_date=str(target_date or now.date().isoformat()).strip(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        raw_objects = value.get("objects", [])
        objects: List[SuggestionSnapshotObject] = []
        if isinstance(raw_objects, list):
            for raw in raw_objects:
                if not isinstance(raw, Mapping):
                    continue
                item = SuggestionSnapshotObject.from_dict(raw)
                if item is not None:
                    objects.append(item)
        return cls(
            conversation_id=str(value.get("conversation_id", "")).strip(),
            objects=objects,
            source_kind=str(value.get("source_kind", "assistant_plan")).strip(),
            source_turn_id=str(value.get("source_turn_id", "")).strip(),
            captured_at=str(value.get("captured_at", "")).strip(),
            snapshot_id=str(value.get("snapshot_id", "")).strip(),
            schema_version=str(
                value.get("schema_version", SUGGESTION_SNAPSHOT_SCHEMA_VERSION)
            ).strip()
            or SUGGESTION_SNAPSHOT_SCHEMA_VERSION,
            target_date=str(value.get("target_date", "") or "").strip(),
        )

    def select_position(self, position: int) -> Optional[SuggestionSnapshotObject]:
        return next(
            (item for item in self.objects if item.display_order == int(position)),
            None,
        )

    def mark_consumed(self, stable_id: object) -> "SuggestionSnapshot":
        target = str(stable_id or "").strip()
        return SuggestionSnapshot(
            conversation_id=self.conversation_id,
            objects=[
                SuggestionSnapshotObject(
                    stable_id=item.stable_id,
                    display_order=item.display_order,
                    title=item.title,
                    consumed=item.consumed or item.stable_id == target,
                )
                for item in self.objects
            ],
            source_kind=self.source_kind,
            source_turn_id=self.source_turn_id,
            captured_at=self.captured_at,
            snapshot_id=self.snapshot_id,
            schema_version=self.schema_version,
            target_date=self.target_date,
        )

    def to_model_dict(self) -> Dict[str, object]:
        """Expose only reference-safe fields to semantic classification."""

        return {
            "snapshot_id": self.snapshot_id,
            "source_kind": self.source_kind,
            "target_date": self.target_date,
            "objects": [item.to_dict() for item in self.objects],
        }

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "conversation_id": self.conversation_id,
            "source_kind": self.source_kind,
            "source_turn_id": self.source_turn_id,
            "captured_at": self.captured_at,
            "target_date": self.target_date,
            "objects": [item.to_dict() for item in self.objects],
        }
