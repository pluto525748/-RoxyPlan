from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence

from modules.action_claim_guard import ActionClaimGuard
from modules.agent_core import AgentCore
from modules.contracts import (
    AgentResponse,
    ProposedAction,
    ToolMessage,
    ToolResult,
)
from modules.llm.contracts import ProviderResponse
from modules.llm.routed_client import RoutedLLMClient
from modules.llm.response_sanitizer import sanitize_public_reply, sanitize_public_text
from modules.tool_executor import ToolExecutor
from modules.tool_registry import ToolRegistry


class ModelActionAdapter:
    """Normalize untrusted provider output without executing any tool."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def from_native_response(self, response: ProviderResponse) -> List[ProposedAction]:
        if not response.tool_calls:
            return [
                ProposedAction(
                    "",
                    kind="chat",
                    source="native_tool_call",
                    raw_provider_type=response.provider,
                    confidence=1.0,
                )
            ]
        return [
            self._normalize(
                call.name,
                call.arguments,
                call_id=call.tool_call_id,
                source="native_tool_call",
                provider=response.provider,
            )
            for call in response.tool_calls
        ]

    def from_json_text(
        self,
        raw_text: str,
        *,
        provider: str = "",
    ) -> List[ProposedAction]:
        text = str(raw_text).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [
                ProposedAction(
                    "",
                    kind="clarification",
                    source="json_fallback",
                    raw_provider_type=provider,
                    needs_clarification=True,
                    warnings=["invalid_json"],
                )
            ]
        if not isinstance(payload, dict):
            return [
                ProposedAction(
                    "",
                    kind="clarification",
                    source="json_fallback",
                    raw_provider_type=provider,
                    needs_clarification=True,
                    warnings=["json_root_not_object"],
                )
            ]
        kind = str(payload.get("kind", "")).strip().lower()
        if kind == "chat":
            return [
                ProposedAction(
                    "",
                    kind="chat",
                    source="json_fallback",
                    raw_provider_type=provider,
                    confidence=1.0,
                )
            ]
        if kind == "clarification":
            question = str(payload.get("question", "")).strip()
            return [
                ProposedAction(
                    "",
                    kind="clarification",
                    source="json_fallback",
                    raw_provider_type=provider,
                    needs_clarification=True,
                    warnings=[question[:240]] if question else ["model_requested_clarification"],
                )
            ]
        actions = payload.get("actions")
        if not isinstance(actions, list):
            actions = [payload] if payload.get("tool_name") else []
        if not actions:
            return [
                ProposedAction(
                    "",
                    kind="clarification",
                    source="json_fallback",
                    raw_provider_type=provider,
                    needs_clarification=True,
                    warnings=["missing_actions"],
                )
            ]
        proposals: List[ProposedAction] = []
        for index, item in enumerate(actions[:6]):
            if not isinstance(item, dict):
                proposals.append(
                    ProposedAction(
                        "",
                        kind="clarification",
                        source="json_fallback",
                        raw_provider_type=provider,
                        needs_clarification=True,
                        warnings=["action_not_object"],
                    )
                )
                continue
            arguments = item.get("arguments", {})
            proposals.append(
                self._normalize(
                    str(item.get("tool_name", "")),
                    arguments if isinstance(arguments, dict) else {},
                    call_id=str(item.get("call_id", "")).strip() or f"json_call_{index + 1}",
                    source="json_fallback",
                    provider=provider,
                    extra_warning=(
                        "arguments_not_object"
                        if not isinstance(arguments, dict)
                        else ""
                    ),
                )
            )
        return proposals

    def json_fallback_messages(
        self,
        messages: Sequence[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        return [
            {
                "role": "system",
                "content": self.registry.json_fallback_instruction(),
            },
            *[dict(item) for item in messages],
        ]

    def _normalize(
        self,
        tool_name: str,
        arguments: object,
        *,
        call_id: str,
        source: str,
        provider: str,
        extra_warning: str = "",
    ) -> ProposedAction:
        name = str(tool_name).strip()
        args = dict(arguments) if isinstance(arguments, dict) else {}
        warnings = [extra_warning] if extra_warning else []
        tool = self.registry.get(name)
        if tool is None or not tool.enabled or not tool.model_visible:
            warnings.append("tool_not_allowed")
            return ProposedAction(
                name,
                args,
                confidence=0.0,
                source=source,
                raw_provider_type=provider,
                needs_clarification=True,
                warnings=warnings,
                kind="clarification",
                call_id=call_id,
            )
        if "__invalid_json__" in args or "__invalid_arguments__" in args:
            warnings.append("invalid_arguments")
            return ProposedAction(
                name,
                {},
                confidence=0.1,
                source=source,
                raw_provider_type=provider,
                needs_clarification=True,
                warnings=warnings,
                kind="clarification",
                call_id=call_id,
            )
        valid, normalized, error = ToolExecutor._validate_arguments(
            tool.parameters_schema, args
        )
        missing = [
            field
            for field, rules in tool.parameters_schema.items()
            if bool(rules.get("required")) and field not in args
        ]
        if not valid:
            warnings.append(str(error or "invalid_arguments"))
        return ProposedAction(
            name,
            normalized if valid else args,
            confidence=0.92 if valid else 0.2,
            source=source,
            raw_provider_type=provider,
            needs_clarification=not valid,
            warnings=warnings,
            missing_fields=missing,
            kind="action" if valid else "clarification",
            call_id=call_id,
        )


class ModelToolCallLoop:
    """Bounded model/tool loop; ToolExecutor remains the only execution boundary."""

    REPAIRABLE_ERRORS = {
        "missing_parameter",
        "invalid_parameter_type",
        "unexpected_parameter",
        "invalid_parameter_enum",
        "parameter_out_of_range",
        "invalid_arguments",
    }

    def __init__(
        self,
        client: RoutedLLMClient,
        registry: ToolRegistry,
        agent_core: AgentCore,
        *,
        max_tool_rounds: int = 2,
        max_tool_calls: int = 3,
        max_parameter_repairs: int = 1,
        claim_guard: Optional[ActionClaimGuard] = None,
        proposal_adapter: Optional[ModelActionAdapter] = None,
        semantic_parser=None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.agent_core = agent_core
        self.max_tool_rounds = max(1, min(int(max_tool_rounds), 3))
        self.max_tool_calls = max(1, min(int(max_tool_calls), 6))
        self.max_parameter_repairs = max(0, min(int(max_parameter_repairs), 1))
        self.claim_guard = claim_guard or ActionClaimGuard()
        self.proposal_adapter = proposal_adapter or ModelActionAdapter(registry)
        self.semantic_parser = semantic_parser

    def complete(
        self,
        *,
        user_text: str,
        messages: List[Dict[str, object]],
        conversation_id: str,
        intent_result: Dict[str, object],
    ) -> Optional[AgentResponse]:
        request_id = str(intent_result.get("request_id", "")).strip()
        if not self.client.can_use_online_tools():
            if self.client.can_use_json_actions():
                return self._json_fallback(
                    user_text, messages, conversation_id, intent_result
                )
            return None
        schemas = self.registry.model_tool_schemas()
        if not schemas:
            return None
        route_context = self._route_context(user_text, intent_result)
        response = self.client.chat_response(
            messages,
            route_context=route_context,
            tools=schemas,
        )
        if not response.ok:
            if response.error is not None and response.error.code in {
                "bad_request",
                "invalid_response",
            }:
                return self._json_fallback(
                    user_text, messages, conversation_id, intent_result
                )
            return self._offline_result(
                user_text,
                messages,
                conversation_id,
                response,
                request_id=request_id,
            )
        if not response.tool_calls:
            if self._should_attempt_action_fallback(
                user_text,
                response.content,
                intent_result,
            ):
                fallback_response = self._json_fallback(
                    user_text,
                    messages,
                    conversation_id,
                    intent_result,
                    chat_fallback_text=response.content,
                )
                if fallback_response is not None:
                    return fallback_response
            return AgentResponse(
                "chat",
                self.claim_guard.validate(
                    sanitize_public_reply(response.content),
                    [],
                    user_text=user_text,
                ),
                request_id=request_id or None,
                conversation_id=conversation_id,
            )
        return self._run_native_loop(
            user_text,
            messages,
            conversation_id,
            intent_result,
            response,
            schemas,
        )

    def _run_native_loop(
        self,
        user_text: str,
        messages: List[Dict[str, object]],
        conversation_id: str,
        intent_result: Dict[str, object],
        provider_response: ProviderResponse,
        schemas: List[Dict[str, object]],
    ) -> AgentResponse:
        working_messages = [dict(item) for item in messages]
        all_results: List[ToolResult] = []
        seen_calls = set()
        total_calls = 0
        repair_count = 0

        for _round_index in range(self.max_tool_rounds):
            proposals = (
                self.semantic_parser.proposals_from_native(provider_response)
                if self.semantic_parser is not None
                else self.proposal_adapter.from_native_response(provider_response)
            )
            invalid = [item for item in proposals if item.needs_clarification]
            if invalid:
                disallowed = next(
                    (
                        item
                        for item in invalid
                        if "tool_not_allowed" in item.warnings
                    ),
                    None,
                )
                if disallowed is not None:
                    rejected = ToolResult(
                        False,
                        disallowed.tool_name or "unknown",
                        "工具不存在或未向模型开放",
                        error="tool_not_found",
                        tool_call_id=disallowed.call_id,
                    )
                    return AgentResponse(
                        "failed",
                        "这个工具不在允许列表中，本轮没有执行任何操作。",
                        tool_results=[rejected],
                        conversation_id=conversation_id,
                    )
                repairable = self._can_repair(invalid, repair_count)
                if repairable:
                    repair_count += 1
                    working_messages.append(
                        provider_response.assistant_message(
                            include_reasoning=provider_response.thinking
                        )
                    )
                    for proposal in invalid:
                        working_messages.append(
                            self._proposal_error_message(proposal).to_provider_message()
                        )
                    try:
                        provider_response = self.client.continue_tool_loop(
                            working_messages,
                            model=provider_response.model,
                            thinking=provider_response.thinking,
                            tools=schemas,
                            route_reason="tool_parameter_repair",
                            request_id=str(intent_result.get("request_id", "")),
                        )
                    except Exception:
                        return self._clarification_response(
                            invalid, conversation_id
                        )
                    if not provider_response.ok:
                        return self._clarification_response(invalid, conversation_id)
                    continue
                return self._clarification_response(invalid, conversation_id)

            actions = [item for item in proposals if item.kind == "action"]
            if not actions:
                return AgentResponse(
                    "chat",
                    self.claim_guard.validate(
                        sanitize_public_reply(provider_response.content),
                        all_results,
                        user_text=user_text,
                    ),
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )
            if total_calls + len(actions) > self.max_tool_calls:
                return AgentResponse(
                    "failed",
                    "这一轮需要执行的操作太多，我先停下来。请分成更小的步骤再试。",
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )
            duplicate = next(
                (item.call_id for item in actions if item.call_id in seen_calls), ""
            )
            if duplicate:
                return AgentResponse(
                    "failed",
                    "模型重复提交了同一个工具调用，我已停止本轮操作，避免重复写入。",
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )
            seen_calls.update(item.call_id for item in actions)
            total_calls += len(actions)
            executed = self.agent_core.execute_model_tool_calls(
                user_text,
                [
                    {
                        "name": item.tool_name,
                        "arguments": item.arguments,
                        "tool_call_id": item.call_id,
                    }
                    for item in actions
                ],
                confidence=self._tool_confidence(user_text, intent_result),
                confirmation_scope=conversation_id,
                require_confirmation_for_writes=self._needs_write_confirmation(
                    user_text, intent_result
                ),
            )
            all_results.extend(executed.tool_results)
            if executed.status != "completed":
                executed.conversation_id = conversation_id
                return executed

            working_messages.append(
                provider_response.assistant_message(
                    include_reasoning=provider_response.thinking
                )
            )
            working_messages.extend(
                ToolMessage.from_tool_result(result).to_provider_message()
                for result in executed.tool_results
            )
            try:
                provider_response = self.client.continue_tool_loop(
                    working_messages,
                    model=provider_response.model,
                    thinking=provider_response.thinking,
                    tools=schemas,
                    route_reason=provider_response.route_reason or "tool_loop",
                    request_id=str(intent_result.get("request_id", "")),
                )
            except Exception:
                return AgentResponse(
                    "completed",
                    self.agent_core.success_message(all_results),
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )
            if not provider_response.ok:
                return AgentResponse(
                    "completed",
                    self.agent_core.success_message(all_results),
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )
            if not provider_response.tool_calls:
                final_text = (
                    sanitize_public_text(provider_response.content)
                    or self.agent_core.success_message(all_results)
                )
                return AgentResponse(
                    "completed",
                    self.claim_guard.validate(
                        final_text, all_results, user_text=user_text
                    ),
                    tool_results=all_results,
                    conversation_id=conversation_id,
                )

        return AgentResponse(
            "completed",
            self.agent_core.success_message(all_results),
            tool_results=all_results,
            conversation_id=conversation_id,
        )

    def _json_fallback(
        self,
        user_text: str,
        messages: List[Dict[str, object]],
        conversation_id: str,
        intent_result: Dict[str, object],
        chat_fallback_text: str = "",
    ) -> Optional[AgentResponse]:
        request_id = str(intent_result.get("request_id", ""))
        try:
            response = self.client.json_action_response(
                self.proposal_adapter.json_fallback_messages(messages),
                request_id=request_id,
            )
        except Exception:
            if not chat_fallback_text:
                return None
            return AgentResponse(
                "chat",
                self.claim_guard.validate(
                    sanitize_public_reply(chat_fallback_text),
                    [],
                    user_text=user_text,
                ),
                request_id=request_id or None,
                conversation_id=conversation_id,
            )
        if not response.ok:
            return self._offline_result(
                user_text,
                messages,
                conversation_id,
                response,
                request_id=request_id,
            )
        proposals = (
            self.semantic_parser.proposals_from_json(
                response.content,
                provider=response.provider,
            )
            if self.semantic_parser is not None
            else self.proposal_adapter.from_json_text(
                response.content, provider=response.provider
            )
        )
        if any(item.needs_clarification for item in proposals):
            return self._clarification_response(proposals, conversation_id)
        if all(item.kind == "chat" for item in proposals):
            if chat_fallback_text:
                return AgentResponse(
                    "chat",
                    self.claim_guard.validate(
                        sanitize_public_reply(chat_fallback_text),
                        [],
                        user_text=user_text,
                    ),
                    request_id=request_id or None,
                    conversation_id=conversation_id,
                )
            return None
        actions = [item for item in proposals if item.kind == "action"]
        if not actions or len(actions) > self.max_tool_calls:
            return self._clarification_response(proposals, conversation_id)
        require_write_confirmation = any(
            tool is not None
            and tool.side_effect
            and tool.risk_level in {"medium", "high"}
            and tool.confirmation_policy != "never"
            for tool in (self.registry.get(item.tool_name) for item in actions)
        )
        result = self.agent_core.execute_model_tool_calls(
            user_text,
            [
                {
                    "name": item.tool_name,
                    "arguments": item.arguments,
                    "tool_call_id": item.call_id,
                }
                for item in actions
            ],
            confidence=min(
                [item.confidence for item in actions] or [0.0]
            ),
            confirmation_scope=conversation_id,
            require_confirmation_for_writes=require_write_confirmation,
        )
        result.conversation_id = conversation_id
        return result

    def _offline_result(
        self,
        user_text: str,
        messages: List[Dict[str, object]],
        conversation_id: str,
        failed_response: ProviderResponse,
        *,
        request_id: str = "",
    ) -> AgentResponse:
        fallback = self.client.offline_chat(
            messages,
            reason=(
                failed_response.error.code
                if failed_response.error is not None
                else "online_error"
            ),
            request_id=request_id,
        )
        if fallback.ok:
            return AgentResponse(
                "chat",
                self.claim_guard.validate(
                    sanitize_public_reply(fallback.content),
                    [],
                    user_text=user_text,
                ),
                request_id=request_id or None,
                conversation_id=conversation_id,
            )
        return AgentResponse(
            "failed",
            fallback.error.message
            if fallback.error is not None
            else "在线和本地模型当前都不可用。",
            conversation_id=conversation_id,
        )

    def _can_repair(
        self,
        proposals: Sequence[ProposedAction],
        repair_count: int,
    ) -> bool:
        if repair_count >= self.max_parameter_repairs:
            return False
        for proposal in proposals:
            tool = self.registry.get(proposal.tool_name)
            if tool is not None and tool.risk_level == "high":
                return False
            if not set(proposal.warnings).intersection(self.REPAIRABLE_ERRORS):
                return False
        return True

    @staticmethod
    def _proposal_error_message(proposal: ProposedAction) -> ToolMessage:
        code = next(
            (item for item in proposal.warnings if item), "invalid_arguments"
        )
        return ToolMessage(
            call_id=proposal.call_id,
            tool_name=proposal.tool_name or "unknown",
            status="retryable_error",
            success=False,
            content="工具参数未通过本地校验，请只修正工具名或参数一次。",
            safe_error=code,
            data_summary={"missing_fields": proposal.missing_fields},
        )

    @staticmethod
    def _clarification_response(
        proposals: Sequence[ProposedAction],
        conversation_id: str,
    ) -> AgentResponse:
        missing = sorted(
            {
                field
                for item in proposals
                for field in item.missing_fields
                if field
            }
        )
        if missing:
            message = "我还缺少这些信息：" + "、".join(missing) + "。请补充后再执行。"
        else:
            message = "我还不能安全确定要执行的工具或参数，请换一种更明确的说法。"
        return AgentResponse(
            "clarification", message, conversation_id=conversation_id
        )

    @staticmethod
    def _route_context(
        user_text: str, intent_result: Dict[str, object]
    ) -> Dict[str, object]:
        text = str(user_text)
        return {
            "intent": str(intent_result.get("intent", "chat")),
            "multiple_actions": any(
                term in text for term in ("然后", "并且", "同时", "再把")
            ),
            "multi_tool": any(term in text for term in ("并记录", "并保存", "顺便")),
            "complex_reference": any(
                term in text for term in ("刚才那个", "前一个", "这个改成", "它")
            ),
            "memory_conflict": "冲突" in text,
            "long_review": len(text) > 240 and "复盘" in text,
            "tools_requested": True,
            "request_id": str(intent_result.get("request_id", "")),
        }

    def _should_attempt_action_fallback(
        self,
        user_text: str,
        assistant_text: str,
        intent_result: Dict[str, object],
    ) -> bool:
        if self.claim_guard.contains_action_claim(assistant_text):
            return True
        intent = str(intent_result.get("intent", "chat"))
        if intent not in {"chat", "memory_candidate"}:
            return True

        text = str(user_text).strip().lower()
        if re.search(r"(?:不要|别|不用|不想).{0,8}(?:加入|添加|记录|记住|保存|删除|修改|跳|表演|展示)", text):
            return False
        if re.search(r"(?:怎么|如何|为什么|好处|原理|教程|代码|制作|实现).{0,12}(?:计划|记录|记忆|跳舞|舞蹈)", text):
            return False
        if re.fullmatch(r"你(?:会|能)(?:不会|不能)?跳舞吗", text):
            return False

        operation = re.search(
            r"加入|添加|放进|安排|留\s*\d|记录|记成|记到|记住|保存|"
            r"标记|完成|删除|修改|改成|推迟|归档|恢复|提醒|"
            r"(?:跳|表演|展示).{0,4}(?:舞|一段)",
            text,
        )
        target = re.search(
            r"计划|任务|要做的事|行动|记录|记忆|复盘|提醒|舞|舞蹈|睡眠",
            text,
        )
        request_shape = re.search(r"帮我|给我|请|把|这个|这件事|今天|现在", text)
        return bool(operation and (target or request_shape))

    @staticmethod
    def _tool_confidence(
        user_text: str, intent_result: Dict[str, object]
    ) -> float:
        existing = float(intent_result.get("confidence", 0.0) or 0.0)
        if ModelToolCallLoop._needs_write_confirmation(user_text, intent_result):
            return min(max(existing, 0.7), 0.8)
        return max(existing, 0.9)

    @staticmethod
    def _needs_write_confirmation(
        user_text: str, intent_result: Dict[str, object]
    ) -> bool:
        if str(intent_result.get("source", "")) in {
            "fixed_command",
            "rule",
        } and float(intent_result.get("confidence", 0.0) or 0.0) >= 0.82:
            return False
        text = str(user_text).strip()
        explicit_terms = (
            "加入",
            "添加",
            "安排",
            "记录",
            "标记",
            "完成",
            "删除",
            "保存",
            "归档",
            "恢复",
            "修改",
            "改成",
            "推迟",
            "取消",
            "唤醒",
            "跳舞",
        )
        vague_terms = (
            "想",
            "也许",
            "可能",
            "考虑",
            "要不要",
            "可以吗",
            "怎么样",
        )
        return any(term in text for term in vague_terms) or not any(
            term in text for term in explicit_terms
        )


# Name kept for integrations that want an explicit runner instead of the parser.
ModelActionRunner = ModelToolCallLoop
