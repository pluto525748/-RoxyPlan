from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional
from uuid import uuid4

from modules.agent_planner import AgentPlanner, INTENT_TOOL_MAP
from modules.local_feature_extractor import LocalFeatureExtractor, LocalFeatures


DISABLED_MAIN_CHAT_CANDIDATE_TOOLS = {
    "create_memory_candidate",
    "queue_memory_candidate",
    "list_memory_candidates",
    "show_memory_candidates",
    "accept_memory_candidate",
    "accept_memory_candidates",
    "reject_memory_candidate",
    "reject_memory_candidates",
    "accept_all_memory_candidates",
}


@dataclass(frozen=True)
class SemanticToolCall:
    """One untrusted tool proposal from the single semantic decision."""

    name: str
    arguments: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class SemanticDecision:
    """Canonical boundary between language understanding and program execution.

    The decision describes intent only.  It carries no execution authority:
    BusinessResolver, SafetyPolicy and ToolExecutor remain the sole mutation
    boundary.

    Structured semantic fields (subject / polarity / modality) enable
    deterministic policies such as PlanAuthorizationPolicy to decide
    direct execute vs. create pending vs. no action — without reading
    raw Chinese keywords.
    """

    mode: str
    intent: str
    tool_calls: List[SemanticToolCall] = field(default_factory=list)
    confidence: float = 0.0
    follow_up_target: Optional[str] = None
    reply_hint: Optional[str] = None
    # ── Structured semantic annotation ──────────────────────────
    subject: str = ""
    polarity: str = ""
    modality: str = ""

    VALID_SUBJECTS = {"self", "other", ""}
    VALID_POLARITIES = {"positive", "negative", ""}
    VALID_MODALITIES = {"commitment", "desire", "hypothetical", "question", ""}

    def __post_init__(self) -> None:
        if self.mode not in {"chat_only", "tool_then_reply", "clarify"}:
            object.__setattr__(self, "mode", "chat_only")
        object.__setattr__(self, "intent", str(self.intent or "chat"))
        object.__setattr__(
            self,
            "confidence",
            max(0.0, min(float(self.confidence or 0.0), 1.0)),
        )
        object.__setattr__(
            self,
            "follow_up_target",
            str(self.follow_up_target).strip() if self.follow_up_target else None,
        )
        object.__setattr__(
            self,
            "reply_hint",
            str(self.reply_hint).strip() if self.reply_hint else None,
        )
        # Validate structured fields; default to empty on invalid input
        _sub = str(self.subject or "").strip().lower()
        object.__setattr__(
            self, "subject", _sub if _sub in self.VALID_SUBJECTS else ""
        )
        _pol = str(self.polarity or "").strip().lower()
        object.__setattr__(
            self, "polarity", _pol if _pol in self.VALID_POLARITIES else ""
        )
        _mod = str(self.modality or "").strip().lower()
        object.__setattr__(
            self, "modality", _mod if _mod in self.VALID_MODALITIES else ""
        )

    def to_dict(self) -> Dict[str, object]:
        result = {
            "mode": self.mode,
            "intent": self.intent,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "confidence": self.confidence,
            "follow_up_target": self.follow_up_target,
            "reply_hint": self.reply_hint,
        }
        for key in ("subject", "polarity", "modality"):
            value = getattr(self, key)
            if value:
                result[key] = value
        return result


@dataclass
class ActionCandidate:
    domain: str
    tool_name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    request_mode: str = "execute"
    raw_entities: Dict[str, object] = field(default_factory=dict)
    reference_text: str = ""
    confidence: float = 0.0
    explicit_command: bool = False
    requires_confirmation_hint: bool = False
    ambiguities: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    sequence_index: int = 0
    source_intent: str = ""
    clause_text: str = ""
    command_text: str = ""
    payload_text: str = ""
    command_span: List[tuple[int, int]] = field(default_factory=list)
    protected_payload_span: Optional[tuple[int, int]] = None
    polarity: str = "statement"
    clause_parse_status: str = "not_actionable"
    missing_fields: List[str] = field(default_factory=list)
    ambiguous_reference: List[str] = field(default_factory=list)
    risk_level: str = "low"
    action_id: str = field(default_factory=lambda: "action_" + uuid4().hex)

    def to_dict(self) -> Dict[str, object]:
        return {
            "action_id": self.action_id,
            "domain": self.domain,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "request_mode": self.request_mode,
            "raw_entities": dict(self.raw_entities),
            "reference_text": self.reference_text,
            "confidence": self.confidence,
            "explicit_command": self.explicit_command,
            "requires_confirmation_hint": self.requires_confirmation_hint,
            "ambiguities": list(self.ambiguities),
            "depends_on": list(self.depends_on),
            "sequence_index": self.sequence_index,
            "source_intent": self.source_intent,
            "clause_text": self.clause_text,
            "command_text": self.command_text,
            "payload_text": self.payload_text,
            "command_span": [list(item) for item in self.command_span],
            "protected_payload_span": (
                list(self.protected_payload_span)
                if self.protected_payload_span is not None
                else None
            ),
            "polarity": self.polarity,
            "clause_parse_status": self.clause_parse_status,
            "missing_fields": list(self.missing_fields),
            "ambiguous_reference": list(self.ambiguous_reference),
            "risk_level": self.risk_level,
        }


@dataclass
class SemanticParseResult:
    source: str
    candidates: List[ActionCandidate] = field(default_factory=list)
    request_mode: str = "discuss"
    plain_chat_probability: float = 1.0
    needs_clarification: bool = False
    global_ambiguities: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    intent_result: Dict[str, object] = field(default_factory=dict)
    local_features: Optional[LocalFeatures] = None
    clauses: List[Dict[str, object]] = field(default_factory=list)

    def as_decision(self) -> SemanticDecision:
        """Expose every parser source through one execution-neutral contract."""
        intent = self.intent_result if isinstance(self.intent_result, dict) else {}
        clarification = str(intent.get("clarification_question", "") or "").strip()
        candidate_tool_calls = [
            SemanticToolCall(item.tool_name, dict(item.arguments))
            for item in self.candidates
            if str(item.tool_name).strip()
        ]
        mode = (
            "clarify"
            if self.needs_clarification or clarification
            else "tool_then_reply"
            if candidate_tool_calls
            else "chat_only"
        )
        # A clarification may retain candidates for pending-state context, but
        # it must not grant execution authority to incomplete tool calls.
        tool_calls = [] if mode == "clarify" else candidate_tool_calls
        return SemanticDecision(
            mode=mode,
            intent=str(intent.get("intent", "chat")),
            tool_calls=tool_calls,
            confidence=float(intent.get("confidence", 0.0) or 0.0),
            follow_up_target=intent.get("follow_up_target"),
            reply_hint=clarification or None,
            subject=str(intent.get("subject", "") or ""),
            polarity=str(intent.get("polarity", "") or ""),
            modality=str(intent.get("modality", "") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "candidates": [item.to_dict() for item in self.candidates],
            "request_mode": self.request_mode,
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
            "clauses": [dict(item) for item in self.clauses],
            "decision": self.as_decision().to_dict(),
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
            self._routing_text(text, features),
            dict(context or {}),
            allow_llm=allow_llm,
            payload_text=features.payload_text or None,
        )
        if not self.enabled:
            return self._result_from_intent(intent, features, started)

        candidates = self._local_candidates(text, intent, features, context)
        if features.polarity == "negated" and any(
            self._is_side_effect_tool(item.tool_name) for item in candidates
        ):
            print(
                "[Validation] rejected reason=local_negation_veto",
                flush=True,
            )
            candidates = []
            intent = {
                **intent,
                "intent": "chat",
                "mode": "chat",
                "entities": {},
                "candidate_actions": [],
                "proposed_tool": None,
                "needs_confirmation": False,
                "clarification_question": None,
            }
        if (
            str(intent.get("source", "")) == "llm"
            and str(intent.get("intent", "chat")) == "chat"
        ):
            if candidates:
                print(
                    "[Validation] rejected reason=chat_intent_cannot_emit_tools",
                    flush=True,
                )
            candidates = []
        request_mode = self._request_mode(text, intent, features, candidates)
        if request_mode in {"advice", "discuss"}:
            candidates = [item for item in candidates if self._is_read_tool(item.tool_name)]
            if not candidates:
                intent = {
                    **intent,
                    "intent": "chat",
                    "entities": {},
                    "needs_confirmation": False,
                    "clarification_question": None,
                    "request_mode": request_mode,
                }
        for candidate in candidates:
            candidate.request_mode = request_mode
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
            request_mode=request_mode,
            plain_chat_probability=0.05 if candidates else 1.0,
            needs_clarification=needs_clarification,
            global_ambiguities=ambiguities,
            provider=str(intent.get("provider", "")),
            model=str(intent.get("model", "")),
            latency_ms=int((time.perf_counter() - started) * 1000),
            intent_result=intent,
            local_features=features,
            clauses=self._clause_reports(text, candidates),
        )

    def parse_unified(
        self,
        text: str,
        context: Optional[Mapping[str, object]] = None,
        *,
        allow_llm: bool = False,
        allow_legacy_fallback: bool = False,
    ) -> SemanticParseResult:
        """Build the one decision consumed by the normal conversation path.

        Unlike the compatibility ``parse`` method, this method never asks the
        router's broad keyword fallback to infer an action.  It receives either
        a precise local command or a single structured model decision.
        """
        started = time.perf_counter()
        features = self.feature_extractor.extract(text)
        # ── Supply ToolRegistry parameter docs to the LLM prompt ──────
        if (
            allow_llm
            and self.proposal_adapter is not None
            and hasattr(self.proposal_adapter, "registry")
        ):
            registry = self.proposal_adapter.registry
            docs = registry.model_visible_parameter_docs()
            llm_parser = getattr(self.intent_router, "llm_parser", None)
            if llm_parser is not None and hasattr(llm_parser, "parameter_docs"):
                llm_parser.parameter_docs = docs
        intent = self.intent_router.route_semantic_decision(
            self._routing_text(text, features),
            dict(context or {}),
            allow_llm=allow_llm,
            payload_text=features.payload_text or None,
        )
        if (
            allow_legacy_fallback
            and str(intent.get("source", "")) == "fallback"
        ):
            # Compatibility is deliberately opt-in and used only by offline
            # test/legacy clients.  Standard RoutedLLMClient traffic never
            # enters this path.
            intent = self.intent_router.route(
                self._routing_text(text, features),
                dict(context or {}),
                allow_llm=False,
                payload_text=features.payload_text or None,
            )
            intent["source"] = "compatibility_semantic_fallback"
        candidates = self._decision_candidates(intent, features)
        schedule = self.feature_extractor.entity_parser.extract_plan_schedule(text)
        schedule_items = schedule.get("items", [])
        if (
            str(intent.get("intent", "")) == "add_plan"
            and isinstance(schedule_items, list)
            and len(schedule_items) >= 2
        ):
            candidates = self._schedule_candidates(candidates, schedule_items)
            intent = {
                **intent,
                "entities": {
                    **(
                        dict(intent.get("entities", {}))
                        if isinstance(intent.get("entities"), Mapping)
                        else {}
                    ),
                    "tasks": [dict(item) for item in schedule_items],
                },
                "schedule_unresolved_items": [
                    dict(item) for item in schedule.get("unresolved", [])
                ],
                "schedule_overflow_count": max(
                    0, len(schedule_items) - self.max_actions
                ),
            }
        if features.polarity == "negated" and any(
            self._is_side_effect_tool(item.tool_name) for item in candidates
        ):
            print(
                "[Validation] rejected reason=local_negation_veto",
                flush=True,
            )
            candidates = []
            intent = {
                **intent,
                "intent": "chat",
                "mode": "chat",
                "entities": {},
                "candidate_actions": [],
                "proposed_tool": None,
                "needs_confirmation": False,
                "clarification_question": None,
            }
        if (
            not candidates
            and str(intent.get("intent", "")) == "chat"
            and not features.is_question
            and not features.negation_cues
            and not re.search(
                r"(?:如果|假如|要是|万一|也许|可能|有空|抽空)",
                features.normalized_text,
            )
        ):
            wish_candidate = self._fallback_candidate(text, features, 0)
            if (
                wish_candidate is not None
                and wish_candidate.tool_name == "add_plan"
                and wish_candidate.request_mode == "possible_action"
            ):
                self._set_clause_metadata(wish_candidate, text, features)
                intent = {
                    **intent,
                    "intent": "add_plan",
                    "entities": dict(wish_candidate.arguments),
                    "needs_confirmation": True,
                    "source": "local_wish_guard",
                    "mode": "write",
                    "proposed_tool": "add_plan",
                    "request_mode": "possible_action",
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "desire",
                }
                candidates = [wish_candidate]
                print(
                    "[SemanticDecision] source=local_wish_guard "
                    "intent=add_plan",
                    flush=True,
                )
        if str(intent.get("intent", "")) in {
            "memory_candidate",
            "show_memory_candidates",
            "accept_memory_candidate",
            "accept_memory_candidates",
            "reject_memory_candidate",
            "reject_memory_candidates",
            "accept_all_memory_candidates",
        }:
            print(
                "[Validation] rejected reason=candidate_intent_disabled_in_main_chat",
                flush=True,
            )
            intent = {
                **intent,
                "intent": "chat",
                "entities": {},
                "candidate_actions": [],
                "clarification_question": None,
                "needs_confirmation": False,
                "mode": "chat",
            }
            candidates = []
        if self._misread_plan_suggestion_as_existing_plan_read(features, candidates):
            print(
                "[SemanticDecision] normalized "
                "reason=plan_suggestion_cannot_be_existing_plan_read",
                flush=True,
            )
            intent = {
                **intent,
                "intent": "chat",
                "entities": {},
                "candidate_actions": [],
                "clarification_question": None,
                "needs_confirmation": False,
                "mode": "chat",
                "proposed_tool": None,
                "request_mode": "advice",
            }
            candidates = []
        request_mode = self._request_mode(text, intent, features, candidates)
        for candidate in candidates:
            candidate.request_mode = request_mode
            self._set_clause_metadata(candidate, text, features)
        clarification = str(intent.get("clarification_question", "") or "").strip()
        decision_mode = str(intent.get("mode", "") or "").strip().lower()
        needs_clarification = (
            decision_mode == "clarify" and bool(clarification)
        ) or any(item.ambiguities for item in candidates)
        if str(intent.get("intent", "")) == "chat" and not candidates:
            if needs_clarification:
                print(
                    "[SemanticDecision] normalized reason=chat_without_tools_cannot_clarify",
                    flush=True,
                )
            clarification = ""
            needs_clarification = False
            intent["clarification_question"] = None
            intent["mode"] = "chat"
        return SemanticParseResult(
            source=str(intent.get("source", "semantic_unavailable")),
            candidates=candidates,
            request_mode=request_mode,
            plain_chat_probability=0.05 if candidates else 1.0,
            needs_clarification=needs_clarification,
            global_ambiguities=[clarification] if clarification else [],
            provider=str(intent.get("provider", "")),
            model=str(intent.get("model", "")),
            latency_ms=int((time.perf_counter() - started) * 1000),
            intent_result=intent,
            local_features=features,
            clauses=self._clause_reports(text, candidates),
        )

    @staticmethod
    def _misread_plan_suggestion_as_existing_plan_read(
        features: LocalFeatures,
        candidates: List[ActionCandidate],
    ) -> bool:
        """Keep recommendation purpose separate from reading stored plans.

        The model may over-weight words such as ``列`` and ``今日计划`` and propose
        ``show_plan`` even though the user asked for new candidate suggestions.
        This correction only removes that read when local features confirm both
        the plan domain and advice purpose, and the sentence has no marker that
        refers to existing application data.
        """
        if (
            not candidates
            or any(item.tool_name != "show_plan" for item in candidates)
            or not features.advice_cues
            or features.negation_cues
        ):
            return False
        normalized = str(features.normalized_text or "")
        plan_topic = "plan" in features.domain_cues or bool(
            re.search(r"计划|任务|要做的事|安排", normalized)
        )
        existing_data_scope = bool(
            re.search(
                r"查看|看看|展示|我的(?:今天|今日)?(?:的)?计划|现有|已有|当前(?:的)?计划|"
                r"计划(?:里|中)|还有|剩下|未完成|已完成|完成情况|状态|安排了什么",
                normalized,
            )
        )
        return plan_topic and not existing_data_scope

    def _decision_candidates(
        self,
        intent: Dict[str, object],
        features: LocalFeatures,
    ) -> List[ActionCandidate]:
        """Convert only declared decision actions; never infer another intent."""
        # ``multi_action`` is a collection envelope, not an executable action
        # of its own.  Reserving one of the bounded action slots for that
        # envelope silently dropped the third explicit task from every
        # three-item request.
        is_multi_action = str(intent.get("intent", "")).strip() == "multi_action"
        inputs = [] if is_multi_action else [dict(intent)]
        actions = intent.get("candidate_actions", [])
        if isinstance(actions, list):
            remaining = max(0, self.max_actions - len(inputs))
            for raw in actions[:remaining]:
                if not isinstance(raw, Mapping):
                    continue
                action_intent = str(raw.get("intent", "")).strip()
                entities = raw.get("entities", {})
                if action_intent and isinstance(entities, Mapping):
                    inputs.append(
                        {
                            **intent,
                            "intent": action_intent,
                            "entities": dict(entities),
                            "candidate_actions": [],
                        }
                    )
        candidates = []
        signatures = set()
        for index, item in enumerate(inputs[: self.max_actions]):
            candidate = self._candidate_from_intent(item, features, index)
            if (
                candidate is not None
                and candidate.tool_name not in DISABLED_MAIN_CHAT_CANDIDATE_TOOLS
            ):
                signature = (
                    candidate.tool_name,
                    json.dumps(
                        candidate.arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                )
                if signature in signatures:
                    print(
                        "[Validation] normalized reason=duplicate_semantic_action",
                        flush=True,
                    )
                    continue
                signatures.add(signature)
                candidates.append(candidate)
            elif candidate is not None:
                print(
                    "[Validation] rejected reason=candidate_tool_disabled_in_main_chat",
                    flush=True,
                )
        return candidates

    def _schedule_candidates(
        self,
        original: List[ActionCandidate],
        schedule_items: List[Dict[str, object]],
    ) -> List[ActionCandidate]:
        """Expand one semantic add-plan decision into its bounded task batch."""
        base = next(
            (item for item in original if item.tool_name == "add_plan"),
            None,
        )
        if base is None:
            return original
        expanded: List[ActionCandidate] = []
        for index, item in enumerate(schedule_items[: self.max_actions]):
            arguments = {
                "title": str(item.get("title", "") or "").strip(),
                "duration_minutes": item.get("duration_minutes"),
            }
            arguments = {
                key: value
                for key, value in arguments.items()
                if value not in (None, "", [])
            }
            expanded.append(
                ActionCandidate(
                    domain=base.domain,
                    tool_name=base.tool_name,
                    arguments=arguments,
                    request_mode=base.request_mode,
                    raw_entities=dict(item),
                    reference_text=base.reference_text,
                    confidence=base.confidence,
                    explicit_command=base.explicit_command,
                    requires_confirmation_hint=base.requires_confirmation_hint,
                    ambiguities=list(base.ambiguities),
                    sequence_index=index,
                    source_intent=base.source_intent,
                )
            )
        return expanded

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
        context: Optional[Mapping[str, object]],
    ) -> List[ActionCandidate]:
        candidates = []
        base = self._candidate_from_intent(intent, features, 0)
        if base is not None:
            self._set_clause_metadata(base, text, features)
            candidates.append(base)

        parts = self.feature_extractor.split_actions(text)
        if len(parts) > 1 and len(candidates) < self.max_actions:
            candidates = []
            for index, part in enumerate(parts[: self.max_actions]):
                item_features = self.feature_extractor.extract(part)
                routed = self.intent_router.route(
                    self._routing_text(part, item_features),
                    dict(context or {}),
                    allow_llm=False,
                    payload_text=item_features.payload_text or None,
                )
                item = self._candidate_from_intent(routed, item_features, index)
                if item is None:
                    item = self._fallback_candidate(part, item_features, index)
                if item is not None:
                    self._set_clause_metadata(item, part, item_features)
                    candidates.append(item)
            if candidates:
                for index, item in enumerate(candidates):
                    item.sequence_index = index

        if not candidates:
            fallback = self._fallback_candidate(text, features, 0)
            if fallback is not None:
                self._set_clause_metadata(fallback, text, features)
                candidates.append(fallback)
        return candidates

    @staticmethod
    def _routing_text(text: str, features: LocalFeatures) -> str:
        """Only the envelope command is eligible for routing when payload exists."""
        if features.payload_text and features.command_text:
            return features.command_text
        return text

    @staticmethod
    def _set_clause_metadata(
        candidate: ActionCandidate,
        text: str,
        features: LocalFeatures,
    ) -> None:
        candidate.clause_text = str(text)
        candidate.command_text = features.command_text
        candidate.payload_text = features.payload_text
        candidate.command_span = list(features.command_span)
        candidate.protected_payload_span = features.protected_payload_span
        candidate.polarity = features.polarity
        candidate.clause_parse_status = features.clause_parse_status
        candidate.missing_fields = list(candidate.ambiguities)
        candidate.ambiguous_reference = list(features.reference_expressions)
        candidate.risk_level = (
            "high"
            if features.risk_cues
            else "medium"
            if candidate.tool_name in {
                "add_plan",
                "update_plan",
                "merge_plan",
                "reschedule_plan",
                "complete_plan",
                "add_action_log",
                "create_memory_candidate",
                "save_formal_memory",
            }
            else "low"
        )

    def _clause_reports(
        self,
        text: str,
        candidates: List[ActionCandidate],
    ) -> List[Dict[str, object]]:
        clauses = self.feature_extractor.split_actions(text)
        reports = []
        for index, clause in enumerate(clauses):
            features = self.feature_extractor.extract(clause)
            items = [item for item in candidates if item.sequence_index == index]
            status = features.clause_parse_status
            if items:
                status = (
                    "clarification_required"
                    if any(item.missing_fields or item.ambiguous_reference for item in items)
                    else "parsed"
                )
            reports.append(
                {
                    "original_text": clause,
                    "parse_status": status,
                    "action_candidates": [item.to_dict() for item in items],
                    "missing_fields": [
                        field
                        for item in items
                        for field in item.missing_fields
                    ],
                    "ambiguous_reference": list(features.reference_expressions),
                    "risk_level": (
                        "high" if features.risk_cues else "medium" if items else "low"
                    ),
                }
            )
        return reports

    def _candidate_from_intent(
        self,
        intent: Dict[str, object],
        features: LocalFeatures,
        index: int,
    ) -> Optional[ActionCandidate]:
        name = str(intent.get("intent", "chat"))
        entities = intent.get("entities", {})
        entities = dict(entities) if isinstance(entities, Mapping) else {}
        # An explicit date in the current sentence outranks stale model or
        # conversation context.  This is entity correction, not intent routing.
        if name == "show_plan" and features.date_expressions:
            parsed_date = str(features.parsed_time.get("date", "") or "").strip()
            if parsed_date:
                entities["date"] = parsed_date
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
        registry = (
            self.proposal_adapter.registry
            if self.proposal_adapter is not None
            and hasattr(self.proposal_adapter, "registry")
            else None
        )
        arguments = AgentPlanner._arguments_for(tool, entities, registry=registry)
        arguments = self._normalize_candidate_arguments(tool, arguments)
        reference = str(
            entities.get("reference_text")
            or features.parsed_time.get("reference_text", "")
            or ""
        )
        structured_request_mode = str(intent.get("request_mode", "") or "").strip()
        request_mode = (
            structured_request_mode
            if structured_request_mode in {
                "query", "execute", "possible_action", "advice", "discuss"
            }
            else self._candidate_mode(name, tool, intent, features)
        )
        explicit_source = str(intent.get("source", "")) in {
            "fixed_command",
            "rule",
            "memory_query_guard",
            "command_envelope",
        }
        explicit_semantic_write = bool(intent.get("explicit_command", False))
        explicit = (
            explicit_source or explicit_semantic_write
        ) and not self._is_vague_wish(features.normalized_text)
        return ActionCandidate(
            domain=self._domain(tool),
            tool_name=tool,
            arguments=arguments,
            request_mode=request_mode,
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

    def _normalize_candidate_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> Dict[str, object]:
        """Normalize user-facing fields before they enter pending state."""
        normalized = dict(arguments or {})
        if tool_name == "add_plan" and isinstance(normalized.get("title"), str):
            title = self.feature_extractor.entity_parser.clean_plan_title(
                str(normalized["title"])
            )
            if title:
                normalized["title"] = title
        return normalized

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
                    request_mode="execute",
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
                request_mode="execute",
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
                    request_mode="execute",
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
                request_mode="possible_action",
                confidence=0.76,
                explicit_command=False,
                requires_confirmation_hint=True,
                ambiguities=["confirm_action_log_creation"],
                sequence_index=index,
                source_intent="add_action_log",
            )
        if self._is_vague_wish(normalized) and any(
            term in normalized for term in ("学", "入门", "了解", "探索", "整理", "练", "做")
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
                request_mode="possible_action",
                raw_entities=dict(parsed),
                confidence=0.66,
                explicit_command=False,
                requires_confirmation_hint=True,
                ambiguities=missing,
                sequence_index=index,
                source_intent="add_plan",
            )
        return None

    @staticmethod
    def _is_side_effect_tool(tool_name: str) -> bool:
        return tool_name in {
            "add_plan", "complete_plan", "delete_plan", "update_plan", "merge_plan",
            "reschedule_plan", "reopen_plan", "cancel_plan", "add_action_log",
            "create_memory_candidate", "request_add_memory", "queue_memory_candidate",
            "save_formal_memory", "archive_memory", "restore_memory", "delete_memory",
            "accept_memory_candidate", "reject_memory_candidate",
            "accept_memory_candidates", "reject_memory_candidates",
            "resolve_memory_conflict",
        }

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
                    arguments=self._normalize_candidate_arguments(
                        proposal.tool_name,
                        proposal.arguments,
                    ),
                    request_mode="execute",
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
            request_mode="execute" if candidates else "discuss",
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
            request_mode=candidate.request_mode if candidate else "discuss",
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

    @classmethod
    def _request_mode(
        cls,
        text: str,
        intent: Mapping[str, object],
        features: LocalFeatures,
        candidates: List[ActionCandidate],
    ) -> str:
        normalized = features.normalized_text
        structured_request_mode = str(
            intent.get("request_mode", "") or ""
        ).strip()
        if structured_request_mode in {
            "query", "execute", "possible_action", "advice", "discuss"
        }:
            return structured_request_mode
        if features.cancellation_cues or (
            features.negation_cues
            and re.search(r"(?:跳|表演|执行|添加|记录|保存|删除|修改)", normalized)
        ):
            return "cancellation"
        if candidates and all(cls._is_read_tool(item.tool_name) for item in candidates):
            return "query"
        explicit_write = bool(
            re.search(
                r"(?:加入|添加到|放进|记到|记录为|标记完成|删除|归档|恢复|改成|调整为)",
                normalized,
            )
            or re.search(
                r"(?:计划|任务|行动记录|记忆).{0,10}(?:加入|添加|记录|完成|删除|更新)",
                normalized,
            )
        )
        if features.advice_cues and not explicit_write:
            return "advice"
        if re.search(r"(?:该|应该).*(?:先|选).*(?:还是|或者)", normalized) and not explicit_write:
            return "advice"
        if re.search(
            r"(?:规划|设计).*(?:学习)?(?:思路|方案|路径)|"
            r"(?:学习|入门).*(?:思路|方案|路径)",
            normalized,
        ) and not explicit_write:
            return "advice"
        if re.search(r"(?:你觉得|怎么|如何|为什么|有什么好处|聊聊|推荐)", normalized) and not explicit_write:
            return "advice" if re.search(r"(?:学|安排|建议|计划|入门|选择|推荐)", normalized) else "discuss"
        if cls._is_possible_action(normalized, intent, features) and not explicit_write:
            return "possible_action"
        if candidates:
            return "execute"
        return "discuss"

    @staticmethod
    def _candidate_mode(
        intent_name: str,
        tool_name: str,
        intent: Mapping[str, object],
        features: LocalFeatures,
    ) -> str:
        del intent_name
        if SemanticActionParser._is_read_tool(tool_name):
            return "query"
        if SemanticActionParser._is_possible_action(
            features.normalized_text,
            intent,
            features,
        ):
            return "possible_action"
        return "execute"

    @staticmethod
    def _is_possible_action(
        normalized: str,
        intent: Mapping[str, object],
        features: LocalFeatures,
    ) -> bool:
        return bool(
            intent.get("clarification_question")
            or (
                re.search(
                    r"(?:我)?(?:想|打算|准备)(?:开始)?(?:学|学习|练习|做|整理|入门|了解|探索|尝试)",
                    normalized,
                )
                and not re.search(
                    r"(?:加入|添加|放进|记到)(?:今天)?(?:计划|任务|清单)",
                    normalized,
                )
            )
            or (
                features.time_period_expressions
                and re.search(r"(?:想|打算|准备).*(?:学|学习|练习)", normalized)
                and not features.duration_expressions
            )
        )

    @staticmethod
    def _is_read_tool(tool_name: str) -> bool:
        return str(tool_name) in {
            "show_plan",
            "inspect_plan_duplicates",
            "show_action_log",
            "generate_daily_review",
            "show_growth_log",
            "list_memories",
            "show_memory",
            "search_memories",
            "search_memory",
            "list_memory_candidates",
            "show_memory_candidates",
            "list_memory_conflicts",
            "show_memory_conflicts",
            "list_archived_memories",
            "show_recent_conversation",
            "show_conversation_history",
        }

    def validate_decision(
        self,
        decision: SemanticDecision,
        *,
        llm_callable=None,
        registry=None,
        require_model_visible: bool = True,
    ):
        """Run the full normalize→validate→repair pipeline on a SemanticDecision.

        Returns a PipelineResult that the caller can branch on:
        - EXECUTE → use decision.tool_calls directly
        - CLARIFY → show clarification_message to the user
        - REJECT → report reject_reason, no tools were executed

        *llm_callable* is an optional fn(prompt: str) -> str for model repair.
        *registry* is the ToolRegistry; when omitted, the method attempts to
        derive one from proposal_adapter or intent_router.
        """
        from modules.semantic_pipeline import SemanticPipeline

        if registry is None:
            # Try proposal_adapter first.
            if self.proposal_adapter is not None:
                registry = self.proposal_adapter.registry
            # Fall back through intent_router → agent_core executor.
            if registry is None:
                router = getattr(self, "intent_router", None)
                if router is not None:
                    agent_core = getattr(router, "agent_core", None)
                    if agent_core is not None:
                        executor = getattr(agent_core, "executor", None)
                        if executor is not None:
                            registry = getattr(executor, "registry", None)
        if registry is None:
            from modules.semantic_pipeline import PipelineOutcome, PipelineResult

            return PipelineResult(
                outcome=PipelineOutcome.REJECT,
                reject_reason="registry_unavailable",
            )

        # Resolve the LLM callable for model repair if not explicitly provided.
        if llm_callable is None:
            llm_parser = getattr(self, "intent_router", None)
            if llm_parser is not None:
                llm_parser = getattr(llm_parser, "llm_parser", None)
            if llm_parser is not None:
                chat_fn = getattr(llm_parser, "chat_callable", None)
                if chat_fn is not None:
                    llm_callable = lambda prompt: chat_fn(
                        [{"role": "user", "content": prompt}]
                    )

        pipeline = SemanticPipeline(
            registry,
            llm_callable=llm_callable,
            require_model_visible=require_model_visible,
        )
        return pipeline.process(decision)

    @staticmethod
    def _is_vague_wish(text: str) -> bool:
        return bool(
            re.search(
                r"(?:我)?(?:今天|下午|晚上|今晚)?(?:想要?|希望|打算).{0,20}(?:学|入门|了解|探索|尝试|做|练|整理)",
                text,
            )
        )

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
