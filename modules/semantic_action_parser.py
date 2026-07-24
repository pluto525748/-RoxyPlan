from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional
from uuid import uuid4

from modules.agent_planner import AgentPlanner, INTENT_TOOL_MAP
from modules.local_feature_extractor import LocalFeatureExtractor, LocalFeatures


@dataclass
class ActionCandidate:
    domain: str
    tool_name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    raw_entities: Dict[str, object] = field(default_factory=dict)
    reference_text: str = ""
    confidence: float = 0.0
    explicit_command: bool = False
    requires_confirmation_hint: bool = False
    ambiguities: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    sequence_index: int = 0
    source_intent: str = ""
    action_id: str = field(default_factory=lambda: "action_" + uuid4().hex)

    def to_dict(self) -> Dict[str, object]:
        return {
            "action_id": self.action_id,
            "domain": self.domain,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "raw_entities": dict(self.raw_entities),
            "reference_text": self.reference_text,
            "confidence": self.confidence,
            "explicit_command": self.explicit_command,
            "requires_confirmation_hint": self.requires_confirmation_hint,
            "ambiguities": list(self.ambiguities),
            "depends_on": list(self.depends_on),
            "sequence_index": self.sequence_index,
            "source_intent": self.source_intent,
        }


@dataclass
class SemanticParseResult:
    source: str
    candidates: List[ActionCandidate] = field(default_factory=list)
    plain_chat_probability: float = 1.0
    needs_clarification: bool = False
    global_ambiguities: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    intent_result: Dict[str, object] = field(default_factory=dict)
    local_features: Optional[LocalFeatures] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "candidates": [item.to_dict() for item in self.candidates],
            "plain_chat_probability": self.plain_chat_probability,
            "needs_clarification": self.needs_clarification,
            "global_ambiguities": list(self.global_ambiguities),
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "intent_result": dict(self.intent_result),
            "local_features": (
                self.local_features.to_dict() if self.local_features else {}
            ),
        }


class SemanticActionParser:
    """Single public semantic entry; providers only propose untrusted actions."""

    def __init__(
        self,
        intent_router,
        *,
        feature_extractor: Optional[LocalFeatureExtractor] = None,
        proposal_adapter=None,
        enabled: bool = True,
        max_actions: int = 3,
    ) -> None:
        self.intent_router = intent_router
        self.feature_extractor = feature_extractor or LocalFeatureExtractor()
        self.proposal_adapter = proposal_adapter
        self.enabled = bool(enabled)
        self.max_actions = max(1, min(int(max_actions), 3))

    def parse(
        self,
        text: str,
        context: Optional[Mapping[str, object]] = None,
        *,
        allow_llm: bool = False,
    ) -> SemanticParseResult:
        started = time.perf_counter()
        features = self.feature_extractor.extract(text)
        intent = self.intent_router.route(
            text,
            dict(context or {}),
            allow_llm=allow_llm,
        )
        if not self.enabled:
            return self._result_from_intent(intent, features, started)

        candidates = self._local_candidates(text, intent, features)
        source = str(intent.get("source", "fallback"))
        ambiguities = []
        clarification = str(intent.get("clarification_question", "") or "").strip()
        if clarification:
            ambiguities.append(clarification)
        needs_clarification = bool(clarification) or any(
            item.ambiguities for item in candidates
        )
        return SemanticParseResult(
            source=source,
            candidates=candidates[: self.max_actions],
            plain_chat_probability=0.05 if candidates else 1.0,
            needs_clarification=needs_clarification,
            global_ambiguities=ambiguities,
            provider=str(intent.get("provider", "")),
            model=str(intent.get("model", "")),
            latency_ms=int((time.perf_counter() - started) * 1000),
            intent_result=intent,
            local_features=features,
        )

    def parse_native_response(self, response) -> SemanticParseResult:
        started = time.perf_counter()
        if self.proposal_adapter is None:
            return SemanticParseResult(
                "native_tool_call",
                needs_clarification=True,
                global_ambiguities=["proposal_adapter_unavailable"],
            )
        proposals = self.proposal_adapter.from_native_response(response)
        return self._from_proposals(
            proposals,
            "native_tool_call",
            provider=str(getattr(response, "provider", "")),
            model=str(getattr(response, "model", "")),
            started=started,
        )

    def proposals_from_native(self, response):
        """Compatibility bridge used by the bounded tool loop."""
        if self.proposal_adapter is None:
            return []
        return self.proposal_adapter.from_native_response(response)

    def parse_json_text(
        self,
        raw_text: str,
        *,
        provider: str = "",
        model: str = "",
    ) -> SemanticParseResult:
        started = time.perf_counter()
        if self.proposal_adapter is None:
            return SemanticParseResult(
                "json_fallback",
                needs_clarification=True,
                global_ambiguities=["proposal_adapter_unavailable"],
            )
        proposals = self.proposal_adapter.from_json_text(
            raw_text,
            provider=provider,
        )
        return self._from_proposals(
            proposals,
            "json_fallback",
            provider=provider,
            model=model,
            started=started,
        )

    def proposals_from_json(self, raw_text: str, *, provider: str = ""):
        """Compatibility bridge; JSON and native calls share this parser entry."""
        if self.proposal_adapter is None:
            return []
        return self.proposal_adapter.from_json_text(raw_text, provider=provider)

    def _local_candidates(
        self,
        text: str,
        intent: Dict[str, object],
        features: LocalFeatures,
    ) -> List[ActionCandidate]:
        candidates = []
        base = self._candidate_from_intent(intent, features, 0)
        if base is not None:
            candidates.append(base)

        parts = self.feature_extractor.split_actions(text)
        if len(parts) > 1 and len(candidates) < self.max_actions:
            candidates = []
            for index, part in enumerate(parts[: self.max_actions]):
                routed = self.intent_router.route(part, {}, allow_llm=False)
                item_features = self.feature_extractor.extract(part)
                item = self._candidate_from_intent(routed, item_features, index)
                if item is None:
                    item = self._fallback_candidate(part, item_features, index)
                if item is not None:
                    candidates.append(item)
            if candidates:
                for index, item in enumerate(candidates):
                    item.sequence_index = index

        if not candidates:
            fallback = self._fallback_candidate(text, features, 0)
            if fallback is not None:
                candidates.append(fallback)
        return candidates

    def _candidate_from_intent(
        self,
        intent: Dict[str, object],
        features: LocalFeatures,
        index: int,
    ) -> Optional[ActionCandidate]:
        name = str(intent.get("intent", "chat"))
        entities = intent.get("entities", {})
        entities = dict(entities) if isinstance(entities, Mapping) else {}
        tool = INTENT_TOOL_MAP.get(name)
        if name == "delete_memory" and entities.get("scope") == "all":
            tool = "delete_all_memories"
        if name == "reminder_control":
            tool = "pause_reminders" if entities.get("action") == "pause" else "resume_reminders"
        if name == "dance":
            tool = "play_dance"
        if name == "sleep_pet":
            tool = "sleep_pet"
        if name == "wake_pet":
            tool = "wake_pet"
        if not tool:
            return None
        arguments = AgentPlanner._arguments_for(tool, entities)
        reference = str(
            entities.get("reference_text")
            or features.parsed_time.get("reference_text", "")
            or ""
        )
        explicit = str(intent.get("source", "")) in {"fixed_command", "rule", "memory_query_guard"}
        explicit = explicit and not self._is_vague_wish(features.normalized_text)
        return ActionCandidate(
            domain=self._domain(tool),
            tool_name=tool,
            arguments=arguments,
            raw_entities=entities,
            reference_text=reference,
            confidence=float(intent.get("confidence", 0.0) or 0.0),
            explicit_command=explicit,
            requires_confirmation_hint=bool(intent.get("needs_confirmation", False)),
            ambiguities=[str(intent.get("clarification_question"))]
            if intent.get("clarification_question")
            else [],
            sequence_index=index,
            source_intent=name,
        )

    def _fallback_candidate(
        self,
        text: str,
        features: LocalFeatures,
        index: int,
    ) -> Optional[ActionCandidate]:
        value = str(text).strip()
        normalized = features.normalized_text
        if features.negation_cues:
            return None
        plan_add_cues = any(
            cue in features.operation_cues
            for cue in ("添加", "加入", "加一条", "再加", "安排", "放进", "记到")
        )
        plan_subject = "plan" in features.domain_cues or bool(
            re.search(r"(?:学习|复习|练习|整理|完成|测试|项目|文档|课程|任务)", normalized)
        )
        if (
            plan_subject
            and plan_add_cues
            and not features.query_cues
            and not features.is_question
        ):
            parsed = features.parsed_time
            title = self.feature_extractor.entity_parser.clean_plan_title(value)
            if title:
                arguments = {"title": title}
                if parsed.get("time_period"):
                    arguments["time_slot"] = parsed.get("time_period")
                if parsed.get("duration_minutes") is not None:
                    arguments["duration_minutes"] = parsed.get("duration_minutes")
                return ActionCandidate(
                    "plan",
                    "add_plan",
                    arguments,
                    raw_entities=dict(parsed),
                    confidence=0.92,
                    explicit_command=True,
                    sequence_index=index,
                    source_intent="add_plan",
                )
        if re.search(r"(?:把|请|帮我).{0,10}(?:这个|这段|这件事).{0,8}(?:记录|记成).{0,5}(?:行动|进展|行动记录)", normalized):
            return ActionCandidate(
                "action_log",
                "add_action_log",
                {"content": ""},
                reference_text="previous_user_message",
                confidence=0.92,
                explicit_command=True,
                sequence_index=index,
                source_intent="add_action_log",
            )
        if (
            "action_log" in features.domain_cues
            and any(cue in features.operation_cues for cue in ("记录", "记到"))
            and not features.query_cues
            and not features.is_question
        ):
            content = re.sub(
                r"^(?:请)?(?:帮我)?(?:记录(?:一下)?|记一条(?:行动|进展)?|行动记录[:：]?)",
                "",
                value,
            ).strip(" ：:，,。.!！")
            if content:
                return ActionCandidate(
                    "action_log",
                    "add_action_log",
                    {"content": content},
                    confidence=0.92,
                    explicit_command=True,
                    sequence_index=index,
                    source_intent="add_action_log",
                )
        if self._is_progress_statement(normalized):
            return ActionCandidate(
                "action_log",
                "add_action_log",
                {"content": value.strip("。.!！?？")},
                confidence=0.76,
                explicit_command=False,
                requires_confirmation_hint=True,
                ambiguities=["confirm_action_log_creation"],
                sequence_index=index,
                source_intent="add_action_log",
            )
        if self._is_vague_wish(normalized) and any(
            term in normalized for term in ("学", "整理", "练", "做")
        ):
            parsed = features.parsed_time
            title = self.feature_extractor.entity_parser.clean_plan_title(value)
            missing = []
            if not title or title in {"学一会儿", "学习一会儿", "做一会儿"}:
                missing.append("title")
            if parsed.get("duration_minutes") is None:
                missing.append("duration_minutes")
            arguments = {
                "title": title,
                "time_slot": parsed.get("time_period", ""),
                "duration_minutes": parsed.get("duration_minutes"),
            }
            return ActionCandidate(
                "plan",
                "add_plan",
                arguments,
                raw_entities=dict(parsed),
                confidence=0.66,
                explicit_command=False,
                requires_confirmation_hint=True,
                ambiguities=missing,
                sequence_index=index,
                source_intent="add_plan",
            )
        return None

    def _from_proposals(
        self,
        proposals,
        source: str,
        *,
        provider: str,
        model: str,
        started: float,
    ) -> SemanticParseResult:
        candidates = []
        ambiguities = []
        chat_only = True
        for index, proposal in enumerate(proposals[: self.max_actions]):
            if proposal.kind == "chat":
                continue
            chat_only = False
            if proposal.needs_clarification:
                ambiguities.extend(proposal.warnings or ["invalid_model_action"])
                continue
            candidates.append(
                ActionCandidate(
                    domain=self._domain(proposal.tool_name),
                    tool_name=proposal.tool_name,
                    arguments=dict(proposal.arguments),
                    confidence=proposal.confidence,
                    explicit_command=False,
                    requires_confirmation_hint=False,
                    ambiguities=list(proposal.warnings),
                    sequence_index=index,
                    source_intent=proposal.tool_name,
                    action_id=proposal.proposal_id,
                )
            )
        return SemanticParseResult(
            source=source,
            candidates=candidates,
            plain_chat_probability=1.0 if chat_only else 0.05,
            needs_clarification=bool(ambiguities),
            global_ambiguities=ambiguities,
            provider=provider,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _result_from_intent(
        self,
        intent: Dict[str, object],
        features: LocalFeatures,
        started: float,
    ) -> SemanticParseResult:
        candidate = self._candidate_from_intent(intent, features, 0)
        return SemanticParseResult(
            source=str(intent.get("source", "fallback")),
            candidates=[candidate] if candidate else [],
            plain_chat_probability=0.05 if candidate else 1.0,
            needs_clarification=bool(intent.get("clarification_question")),
            global_ambiguities=[str(intent.get("clarification_question"))]
            if intent.get("clarification_question")
            else [],
            latency_ms=int((time.perf_counter() - started) * 1000),
            intent_result=intent,
            local_features=features,
        )

    @staticmethod
    def _domain(tool: str) -> str:
        if "memory" in tool:
            return "memory"
        if "plan" in tool:
            return "plan"
        if "action_log" in tool:
            return "action_log"
        if "review" in tool or "growth" in tool:
            return "growth"
        if tool in {"play_dance", "sleep_pet", "wake_pet"}:
            return "pet"
        if "conversation" in tool:
            return "conversation"
        return "other"

    @staticmethod
    def _is_vague_wish(text: str) -> bool:
        return bool(re.search(r"(?:我)?(?:今天|下午|晚上|今晚)?(?:想|打算|准备).{0,20}(?:学|做|练|整理)", text))

    @staticmethod
    def _is_progress_statement(text: str) -> bool:
        return bool(
            re.search(
                r"(?:我)?(?:今天|刚才|刚刚|早上|下午)?.{0,12}(?:理解了|学会了|推进了|测试了|听了|完成了)",
                text,
            )
            or re.search(
                r"(?:我)?.{0,6}(?:已经|已|刚才|刚刚).{0,6}(?:理解|学会|掌握|推进|测试|听|完成)",
                text,
            )
        )
