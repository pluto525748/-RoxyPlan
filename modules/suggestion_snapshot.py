from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, Iterable, List, Mapping, Optional
from uuid import uuid4


SUGGESTION_SNAPSHOT_SCHEMA_VERSION = "1.0"
# A displayed item contains an action heading AND its complete note, not just
# a short title. Bound the whole captured reply/options to keep state and model
# reference context finite; never truncate a body or return a partial list.
# There is no shared public-reply character cap in the current chat sanitizer.
MAX_SUGGESTION_TEXT_CHARS = 16 * 1024
MAX_SUGGESTION_OPTIONS = 64


@dataclass(frozen=True)
class SuggestionSnapshotObject:
    """One model suggestion in the exact order shown to the user."""

    stable_id: str
    display_order: int
    title: str
    consumed: bool = False
    source_plan_uid: str = ""
    source_plan_date: str = ""

    def plan_arguments(self) -> Dict[str, object]:
        """Split only a clearly structured displayed suggestion."""
        from modules.chinese_entity_parser import ChineseEntityParser

        source = self.title.strip()
        match = re.fullmatch(
            r"(?P<title>[^，,。]{2,40})[，,]\s*"
            r"(?P<duration>\d+|[一二两三四五六七八九十]+)\s*分钟"
            r"(?:[。.]\s*(?P<note>.+))?",
            source,
        )
        if match is not None:
            duration = ChineseEntityParser._duration_minutes(match.group("duration") + "分钟")
            if duration is not None:
                result: Dict[str, object] = {
                    "title": match.group("title").strip(),
                    "duration_minutes": duration,
                }
                note = str(match.group("note") or "").strip()
                if note:
                    result["note"] = note
                return result
        # Another common displayed form puts the duration before the action:
        # "接着 25 分钟做练习：自己写几个例子……". The colon is the
        # boundary between a short action and its full explanation.
        heading = re.fullmatch(r"(?P<head>[^：:]{2,50})[：:](?P<note>.+)", source)
        if heading is not None:
            head = heading.group("head").strip()
            duration = ChineseEntityParser._duration_minutes(head)
            if duration is not None:
                # In "用8分钟写代码。备注：核对输出", the label introduces
                # the note, not the action title. Only strip 用 when it governs
                # the leading duration; "用Python练习" remains part of the task.
                head = re.sub(r"[。.]?\s*备注\s*$", "", head).strip()
                head = re.sub(
                    r"^用\s*(?=(?:\d+|[一二两三四五六七八九十]+)\s*分钟)", "", head,
                )
                title = re.sub(r"^(?:先用|接着|然后用|然后|最后|先|再)\s*", "", head)
                title = re.sub(
                    r"(?:\d+|[一二两三四五六七八九十]+)\s*分钟\s*", "", title,
                    count=1,
                ).strip(" ，,。:：")
                note = heading.group("note").strip()
                if 2 <= len(title) <= 40 and note:
                    return {"title": title, "duration_minutes": duration, "note": note}
        # A longer displayed suggestion is often prose whose final clause is
        # the explicit task total, e.g. "选一段两分钟左右的材料……，15分钟。".
        # Preceding durations describe material or sub-activities.  Only the
        # terminal standalone clause is a total; keep the whole displayed body
        # as the title so no part of the confirmed suggestion is truncated.
        total = re.search(
            r"[，,；;]\s*(?:总共|一共|共)?\s*"
            r"(?P<duration>\d+|[一二两三四五六七八九十]+)\s*分钟\s*[。.!！]?$",
            source,
        )
        if total is not None:
            duration = ChineseEntityParser._duration_minutes(
                total.group("duration") + "分钟"
            )
            if duration is not None:
                return {"title": source, "duration_minutes": duration}
        return {"title": source}

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
            source_plan_uid=str(value.get("source_plan_uid", "") or ""),
            source_plan_date=str(value.get("source_plan_date", "") or ""),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        return cls.from_option(
            {
                "id": value.get("stable_id", value.get("id", "")),
                "title": value.get("title", ""),
                "consumed": value.get("consumed", False),
                "source_plan_uid": value.get("source_plan_uid", ""),
                "source_plan_date": value.get("source_plan_date", ""),
            },
            int(value.get("display_order", 1) or 1),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "display_order": self.display_order,
            "title": self.title,
            "consumed": self.consumed,
            "source_plan_uid": self.source_plan_uid,
            "source_plan_date": self.source_plan_date,
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
    common_time_slot: str = ""
    list_purpose: str = "today_tasks"

    @staticmethod
    def displayed_list_purpose(text: str, source_request: str = "", request_mode: str = "") -> str:
        """Classify local provenance, not subject keywords or the advice label.

        Only the framing outside a validated flat list may identify a question
        about the user's needs. Numeric multi-day horizons and stage headings
        identify an outline; they do not authorize saving its numbered items.
        The existing guided-learning mode already identifies teaching content.
        """
        if request_mode == "guided_learning":
            return "explanation"
        options = SuggestionSnapshot.numbered_options(text)
        if not options:
            return "unstructured"
        framing = "\n".join(line.strip() for line in str(text).splitlines()
                          if not re.fullmatch(r"\s*\d+[.、)）]\s*.+", line))
        # These are clarification slots, independent of Python/exams or any
        # other topic. Inspect framing, never words inside individual tasks.
        slot = r"(?:目标|方向|用途|目的|基础|水平|经验|偏好|需求)"
        # Acknowledging a known goal is not asking the user for a goal. Keep
        # clauses separate, and require a real question or selection verb;
        # the 选 inside 候选 cannot turn today's tasks into direction options.
        clauses = re.split(r"[，,。！!；;\n]|(?<=[？?])", framing)
        if any(re.search(
            rf"(?:什么|哪(?:个|种|些)|怎样的).{{0,8}}{slot}"
            rf"|{slot}.{{0,12}}[？?]$"
            rf"|^(?:请)?(?:(?:你|您)(?:可以|需要)?|我(?:想|需要)?)?(?:先)?"
            rf"(?:选择|挑选|选(?:一个|一项|一下)?|告诉我|确认).{{0,16}}{slot}"
            rf"|{slot}(?:里|中|之间|当中).{{0,8}}(?:选择|挑选|选(?:一个|一项|一下))",
            clause.strip(),
        ) for clause in clauses):
            return "clarification_options"
        if any(re.match(r"第[\d一二两三四五六七八九十]+(?:至[\d一二两三四五六七八九十]+)?(?:天|周|阶段)", item["title"])
               for item in options) or re.search(r"分阶段|长期(?:规划|方案|计划)", framing):
            return "long_term_outline"
        request = re.sub(r"\s+", "", source_request)
        horizons = list(re.finditer(r"([\d一二两三四五六七八九十百]+)(?:个)?(天|周|月).{0,24}?(?:计划|方案|课程)", request))
        horizons += list(re.finditer(
            r"(?:规划|安排)(?:一下)?(?:未来|接下来)?"
            r"([\d一二两三四五六七八九十百]+)(?:个)?(天|周|月)", request,
        ))
        from modules.chinese_entity_parser import ChineseEntityParser
        if any((ChineseEntityParser._number(match.group(1)) or 0) * {"天": 1, "周": 7, "月": 30}[match.group(2)] > 1
               for match in horizons) or re.search(r"长期(?:规划|方案|计划)", request):
            return "long_term_outline"
        if re.match(r"^(?:请|帮我|给我|向我)?(?:解释|讲解|介绍|说明)", request):
            return "explanation"
        return "discussion" if request_mode == "discussion_continuation" else "today_tasks"

    @staticmethod
    def numbered_options(text: str) -> List[Dict[str, str]]:
        """Validate a whole flat list without inventing, trimming or renumbering tasks.

        The caller must establish a planning/advice purpose first. A numbered
        list alone is never evidence of a request to change application data.
        """
        source = str(text or "").strip()
        if not source or len(source) > MAX_SUGGESTION_TEXT_CHARS or "```" in source:
            return []
        lines = source.splitlines()
        numbered = []
        for index, line in enumerate(lines):
            match = re.fullmatch(r"\s*(\d+)([.、)）])(\s*)(.+?)\s*", line)
            if match is not None:
                # Reject huge numeric labels before conversion, and bound the
                # number of objects without silently taking the first N items.
                if len(match.group(1)) > len(str(MAX_SUGGESTION_OPTIONS)):
                    return []
                # Indented numbered children and multiple numbered blocks must
                # not be flattened into a different list than the one displayed.
                if line[:1].isspace():
                    return []
                title = match.group(4)
                if match.group(2) == "." and not match.group(3) and title[:1].isdigit():
                    return []
                numbered.append((index, int(match.group(1)), title))
                if len(numbered) > MAX_SUGGESTION_OPTIONS:
                    return []
        if not numbered:
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
        common_time_slot: str = "",
        list_purpose: str = "today_tasks",
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
            common_time_slot=str(common_time_slot or "").strip(),
            list_purpose=str(list_purpose or "").strip(),
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
            common_time_slot=str(value.get("common_time_slot", "") or "").strip(),
            list_purpose=str(value.get("list_purpose", "today_tasks") or "").strip(),
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
                    source_plan_uid=item.source_plan_uid,
                    source_plan_date=item.source_plan_date,
                )
                for item in self.objects
            ],
            source_kind=self.source_kind,
            source_turn_id=self.source_turn_id,
            captured_at=self.captured_at,
            snapshot_id=self.snapshot_id,
            schema_version=self.schema_version,
            target_date=self.target_date,
            common_time_slot=self.common_time_slot,
            list_purpose=self.list_purpose,
        )

    def to_model_dict(self) -> Dict[str, object]:
        """Expose only reference-safe fields to semantic classification."""

        return {
            "snapshot_id": self.snapshot_id,
            "source_kind": self.source_kind,
            "list_purpose": self.list_purpose,
            "target_date": self.target_date,
            "common_time_slot": self.common_time_slot,
            "objects": [{
                "stable_id": item.stable_id,
                "display_order": item.display_order,
                "title": item.title,
                "consumed": item.consumed,
            } for item in self.objects],
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
            "common_time_slot": self.common_time_slot,
            "objects": [item.to_dict() for item in self.objects],
            "list_purpose": self.list_purpose,
        }
