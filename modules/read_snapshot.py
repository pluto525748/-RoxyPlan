from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional
from uuid import uuid4


READ_SNAPSHOT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ReadSnapshotObject:
    """One verified object in the order it was shown to the user."""

    stable_id: str
    display_order: int
    title: str
    status: str = ""
    done: bool = False
    date: str = ""
    time_slot: str = ""
    duration_minutes: Optional[int] = None

    @classmethod
    def from_plan(cls, value: Mapping[str, object], display_order: int):
        stable_id = str(value.get("uid") or value.get("id") or "").strip()
        if not stable_id:
            return None
        duration = value.get("duration_minutes")
        try:
            duration_value = int(duration) if duration not in (None, "") else None
        except (TypeError, ValueError):
            duration_value = None
        return cls(
            stable_id=stable_id,
            display_order=max(1, int(display_order)),
            title=str(value.get("title", "")).strip(),
            status=str(value.get("status", "")).strip(),
            done=bool(value.get("done", False)),
            date=str(value.get("date", "")).strip(),
            time_slot=str(value.get("time_slot", "")).strip(),
            duration_minutes=duration_value,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        stable_id = str(value.get("stable_id", "")).strip()
        if not stable_id:
            return None
        return cls.from_plan(
            {
                "uid": stable_id,
                "title": value.get("title", ""),
                "status": value.get("status", ""),
                "done": value.get("done", False),
                "date": value.get("date", ""),
                "time_slot": value.get("time_slot", ""),
                "duration_minutes": value.get("duration_minutes"),
            },
            int(value.get("display_order", 1) or 1),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "display_order": self.display_order,
            "title": self.title,
            "status": self.status,
            "done": self.done,
            "date": self.date,
            "time_slot": self.time_slot,
            "duration_minutes": self.duration_minutes,
        }


@dataclass(frozen=True)
class ReadSnapshot:
    """Conversation-scoped, verified read result used by later references."""

    conversation_id: str
    intent: str
    tool_name: str
    objects: List[ReadSnapshotObject] = field(default_factory=list)
    arguments: Dict[str, object] = field(default_factory=dict)
    captured_at: str = ""
    snapshot_id: str = ""
    schema_version: str = READ_SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def for_plan_list(
        cls,
        *,
        conversation_id: str,
        intent: str,
        tool_name: str,
        tasks: Iterable[Mapping[str, object]],
        arguments: Optional[Mapping[str, object]] = None,
        captured_at: Optional[datetime] = None,
    ) -> "ReadSnapshot":
        objects: List[ReadSnapshotObject] = []
        for index, task in enumerate(tasks, 1):
            if not isinstance(task, Mapping):
                continue
            item = ReadSnapshotObject.from_plan(task, index)
            if item is not None:
                objects.append(item)
        now = captured_at or datetime.now()
        return cls(
            conversation_id=str(conversation_id).strip(),
            intent=str(intent).strip(),
            tool_name=str(tool_name).strip(),
            objects=objects,
            arguments=dict(arguments or {}),
            captured_at=now.isoformat(timespec="seconds"),
            snapshot_id="read_" + uuid4().hex,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        raw_objects = value.get("objects", [])
        objects: List[ReadSnapshotObject] = []
        if isinstance(raw_objects, list):
            for raw in raw_objects:
                if not isinstance(raw, Mapping):
                    continue
                item = ReadSnapshotObject.from_dict(raw)
                if item is not None:
                    objects.append(item)
        arguments = value.get("arguments", {})
        return cls(
            conversation_id=str(value.get("conversation_id", "")).strip(),
            intent=str(value.get("intent", "")).strip(),
            tool_name=str(value.get("tool_name", "")).strip(),
            objects=objects,
            arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
            captured_at=str(value.get("captured_at", "")).strip(),
            snapshot_id=str(value.get("snapshot_id", "")).strip(),
            schema_version=str(
                value.get("schema_version", READ_SNAPSHOT_SCHEMA_VERSION)
            ).strip()
            or READ_SNAPSHOT_SCHEMA_VERSION,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "conversation_id": self.conversation_id,
            "intent": self.intent,
            "tool_name": self.tool_name,
            "captured_at": self.captured_at,
            "arguments": dict(self.arguments),
            "objects": [item.to_dict() for item in self.objects],
        }
