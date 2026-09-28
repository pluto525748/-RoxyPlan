"""Production-chain replay acceptance runner for RoxyPlan V2.1.

Builds the real AgentService → ConversationService chain with isolated data,
supports multi-turn dialogue, full-chain recording, panel verification,
and dual Stub/DeepSeek modes.

Each case records:
  input → raw model response → SemanticDecision → pending before/after
  → ReferenceResolution → tool_calls → actual write count → ToolResult
  → panel read-back → FinalResponse → request_id log chain
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for p in (str(ROOT), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from server.agent_service import AgentService
from modules.contracts import AgentResponse, ToolResult
from modules.intent_router import LLMIntentParser
from semantic_contract_fixtures import adapt_legacy_v21_semantic_payload


# ── Stub LLM that returns controlled semantic decisions ───────────────────

def _stub_chat_factory(semantic: dict, reply: str = "这是回放测试的自然回复。"):
    """Return a chat() callable that returns *semantic* for the first message
    (the semantic classification prompt) and *reply* for subsequent calls."""

    semantic_json = json.dumps(semantic, ensure_ascii=False)
    call_count = [0]

    def chat(messages, **_kwargs):
        call_count[0] += 1
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            return semantic_json
        return str(reply)

    chat.call_count = call_count
    return chat


# ── Turn record ───────────────────────────────────────────────────────────

@dataclass
class TurnRecord:
    turn_index: int
    user_input: str
    conversation_id: str
    raw_semantic_response: str = ""
    semantic_decision: dict = field(default_factory=dict)
    schema_validation: str = ""
    pending_before: str = ""
    pending_after: str = ""
    reference_resolution: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    actual_write_count: int = 0
    final_response: str = ""
    request_id: str = ""
    diagnostic_stages: list = field(default_factory=list)
    status: str = ""
    passed: bool = True
    failure_reasons: list = field(default_factory=list)


# ── Forbidden chain patterns ──────────────────────────────────────────────

FORBIDDEN_CHAINS = {
    "schema_reject_then_claim": (
        "schema_invalid",
        "fallback",
        "complete",  # 完成式声明
    ),
    "chat_only_then_tool": (
        "chat_only",
        "ToolExecution",
    ),
    "cancel_without_cancel_word": (
        "awaiting_clarification",
        "cancelled",
    ),
    "affirmative_raw_assistant": (
        "previous_assistant_message",
        "save_formal_memory",
    ),
    "no_toolresult_claim": (
        "no_write_success",
        "claim",
    ),
    "duplicate_execution": (
        "duplicate",
    ),
    "tool_success_panel_empty": (
        "tool_success",
        "panel_missing",
    ),
}


# ── Runner ────────────────────────────────────────────────────────────────

class ReplayRunner:
    """Run multi-turn production-chain dialogue acceptance tests."""

    def __init__(self, *, mode: str = "stub"):
        """mode: 'stub' or 'online'."""
        self.mode = mode
        self.online_provider = None
        if mode == "online":
            api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("DEEPSEEK_API_KEY not set for online mode")
            from modules.llm.deepseek_provider import DeepSeekProvider
            from modules.llm.settings import DEFAULT_DEEPSEEK_BASE_URL

            self.online_provider = DeepSeekProvider(
                api_key=api_key,
                base_url=DEFAULT_DEEPSEEK_BASE_URL,
                model_name="deepseek-v4-pro",
                timeout_seconds=120,
                max_retries=1,
            )

    def run_case(
        self,
        case: dict,
        *,
        root: Optional[Path] = None,
        verify_panels: bool = True,
    ) -> dict:
        """Run a single multi-turn case and return the full report."""
        case_id = case["id"]
        turns_config = case["turns"]
        own_root = root is not None

        if root is None:
            self._temp = TemporaryDirectory(prefix=f"roxy-replay-{case_id}-")
            root = Path(self._temp.name)

        conversation_id = f"replay-{case_id}"
        turn_records: List[TurnRecord] = []

        # Build isolated service
        service = self._build_service(root, case)
        # Seed pre-existing data if specified
        self._seed_data(service, case.get("seed", {}))

        for idx, turn_cfg in enumerate(turns_config):
            record = TurnRecord(
                turn_index=idx,
                user_input=turn_cfg["text"],
                conversation_id=conversation_id,
            )

            try:
                self._execute_turn(service, record, turn_cfg, conversation_id)
            except Exception as exc:
                record.passed = False
                record.failure_reasons.append(f"runner_exception:{type(exc).__name__}:{exc}")
                record.status = "error"

            turn_records.append(record)

        # Verify panels after all turns
        panel_results = {}
        if verify_panels:
            panel_results = self._verify_panels(service, case.get("panel_expectations", {}))

        # Audit log chain
        log_audit = self._audit_log_chain(turn_records)

        # Build final report
        all_passed = all(r.passed for r in turn_records) and all(
            v.get("passed", True) for v in panel_results.values()
        )

        report = {
            "case_id": case_id,
            "provenance": case.get("provenance", "unknown"),
            "category": case.get("category", "unknown"),
            "phenomenon": case.get("phenomenon", ""),
            "mode": self.mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "passed": all_passed,
            "turns": [
                {
                    "index": r.turn_index,
                    "user_input": r.user_input,
                    "raw_semantic_response": r.raw_semantic_response[:500],
                    "semantic_decision": r.semantic_decision,
                    "schema_validation": r.schema_validation,
                    "pending_before": r.pending_before,
                    "pending_after": r.pending_after,
                    "reference_resolution": r.reference_resolution,
                    "tool_calls": r.tool_calls,
                    "actual_write_count": r.actual_write_count,
                    "tool_results": r.tool_results,
                    "final_response": r.final_response[:300],
                    "request_id": r.request_id,
                    "diagnostic_stages": r.diagnostic_stages,
                    "status": r.status,
                    "passed": r.passed,
                    "failure_reasons": r.failure_reasons,
                }
                for r in turn_records
            ],
            "panel_results": panel_results,
            "log_audit": log_audit,
        }
        if not own_root:
            self._temp.cleanup()
        return report

    def _build_service(self, root: Path, case: dict) -> AgentService:
        """Build isolated AgentService with the real composition root."""
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Minimal personality stub
        (data_dir / "roxy_personality.json").write_text(
            json.dumps({
                "version": 1, "name": "Roxy",
                "personality": "温和、认真、可靠的学习伙伴。",
                "speaking_style": "自然、简洁、直接。",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        llm = self._make_llm(case)
        service = AgentService(project_root=root, llm_client=llm)
        # Force LLM-based intent routing so our stub/online LLM is consulted
        service.intent_router.configure_llm(
            LLMIntentParser(service.llm_client.chat), True
        )
        service.conversation_service.semantic_decision_compatibility_enabled = False
        return service

    def _make_llm(self, case: dict):
        """Create the LLM client for this case."""
        if self.mode == "stub":
            # Use stub LLM — controlled semantic decisions per turn
            turns_cfg = case.get("turns", [])
            semantics = {}

            class MultiTurnStubLLM:
                def __init__(self, turns, replay_case_id):
                    self.turns = turns
                    self.replay_case_id = str(replay_case_id)
                    self.semantic_count = 0
                    self.reply_count = 0

                def chat(self, messages, **_kwargs):
                    system_text = str(messages[0].get("content", "")) if messages else ""
                    if "You classify one Chinese user message" in system_text:
                        idx = self.semantic_count
                        self.semantic_count += 1
                        if idx < len(self.turns):
                            turn = self.turns[idx]
                            payload = adapt_legacy_v21_semantic_payload(
                                turn.get("semantic", {}),
                                case_id=f"{self.replay_case_id}:turn-{idx + 1}",
                                expected_candidates=turn.get("expect", {}).get(
                                    "tool_calls", []
                                ),
                            )
                            return json.dumps(payload, ensure_ascii=False)
                        fallback = adapt_legacy_v21_semantic_payload(
                            {
                                "mode": "chat",
                                "intent": "chat",
                                "entities": {},
                                "confidence": 0.5,
                                "expected_candidates": [],
                            },
                            case_id=f"{self.replay_case_id}:fallback",
                        )
                        return json.dumps(fallback, ensure_ascii=False)
                    idx = self.reply_count
                    self.reply_count += 1
                    if idx < len(self.turns):
                        return str(self.turns[idx].get("expected_reply", "这是回放测试的自然回复。"))
                    return "这是回放测试的自然回复。"

            return MultiTurnStubLLM(turns_cfg, case.get("id", "unknown-replay"))

        # Online mode: capture raw responses
        provider = self.online_provider

        class CapturingOnlineLLM:
            def __init__(self):
                self.semantic_count = 0
                self.reply_count = 0
                self.semantic_responses = []
                self.reply_responses = []

            def chat(self, messages, **_kwargs):
                system_text = str(messages[0].get("content", "")) if messages else ""
                if "You classify one Chinese user message" in system_text:
                    resp = provider.chat(messages, thinking=False, max_tokens=700)
                    content = resp.content if resp.ok else ""
                    self.semantic_responses.append(content)
                    self.semantic_count += 1
                    return content
                resp = provider.chat(messages, thinking=False, max_tokens=700)
                content = resp.content if resp.ok else ""
                self.reply_responses.append(content)
                self.reply_count += 1
                return content

        return CapturingOnlineLLM()

    def _seed_data(self, service: AgentService, seed: dict):
        """Pre-seed the service with test data."""
        growth = service.growth_manager
        memory = service.memory_manager
        for task_title in seed.get("tasks", []):
            growth.add_task(str(task_title))
        for mem_content in seed.get("memories", []):
            if isinstance(mem_content, dict):
                memory.add_memory(
                    str(mem_content.get("content", "")),
                    category=str(mem_content.get("category", "other")),
                )
            else:
                memory.add_memory(str(mem_content))

    def _execute_turn(
        self,
        service: AgentService,
        record: TurnRecord,
        turn_cfg: dict,
        conversation_id: str,
    ):
        """Execute a single turn through the full production chain."""
        conv = service.conversation_service
        coordinator = service.interaction_coordinator

        # Record pending state before
        state_before = coordinator.current(conversation_id)
        record.pending_before = (
            f"state={state_before.state} kind={state_before.interaction_kind}"
            if state_before.pending else "idle"
        )

        # ── prepare ──
        turn = conv.prepare(
            turn_cfg["text"],
            conversation_id,
            record_history=False,
            allow_llm_intent=True,
        )
        record.request_id = turn.request_id or ""

        # ── semantic decision ──
        intent_result = turn.intent_result
        record.semantic_decision = {
            "mode": str(intent_result.get("mode", "")),
            "intent": str(intent_result.get("intent", "")),
            "source": str(intent_result.get("source", "")),
            "proposed_tool": str(intent_result.get("proposed_tool", "")),
            "candidate_action_count": len(
                intent_result.get("candidate_actions", [])
                if isinstance(intent_result.get("candidate_actions", []), list)
                else []
            ),
        }
        record.schema_validation = str(
            intent_result.get("semantic_diagnostic", "") or ""
        )

        # Capture raw semantic response from stub LLM if available
        llm = service.llm_client
        if hasattr(llm, "semantic_responses") and llm.semantic_responses:
            record.raw_semantic_response = (
                llm.semantic_responses[-1]
                if llm.semantic_responses
                else ""
            )

        # ── complete ──
        response = conv.complete(turn)
        record.status = response.status

        # ── tool results ──
        record.tool_calls = [
            {"tool": item.tool, "success": item.success}
            for item in response.tool_results
        ]
        record.tool_results = [
            {
                "tool": item.tool,
                "success": item.success,
                "status": item.status,
                "operation_kind": item.operation_kind,
            }
            for item in response.tool_results
        ]
        record.actual_write_count = sum(
            1 for item in response.tool_results
            if item.success and item.operation_kind == "write"
        )

        # ── reference resolution ──
        record.reference_resolution = self._extract_reference_info(intent_result)

        # ── final response ──
        record.final_response = str(response.message or "")[:500]

        # ── pending after ──
        state_after = coordinator.current(conversation_id)
        record.pending_after = (
            f"state={state_after.state} kind={state_after.interaction_kind}"
            if state_after.pending else "idle"
        )

        # ── diagnostic stages ──
        records = conv.diagnostic_snapshot()
        matching = [
            r for r in records
            if str(r.get("request_id", "")) == record.request_id
        ]
        if matching:
            diag = matching[-1]
            record.diagnostic_stages = list(diag.get("pipeline", []))
            # Record raw semantic if online
            if (
                not record.raw_semantic_response
                and hasattr(llm, "semantic_responses")
                and llm.semantic_responses
            ):
                record.raw_semantic_response = (
                    llm.semantic_responses[-1]
                    if llm.semantic_responses
                    else ""
                )

        # ── forbidden chain checks ──
        self._check_forbidden_chains(record, turn_cfg, response)

    def _check_forbidden_chains(
        self,
        record: TurnRecord,
        turn_cfg: dict,
        response: AgentResponse,
    ):
        """Check all forbidden chain patterns."""
        diag = record.schema_validation
        stages = record.diagnostic_stages
        reply = record.final_response
        tools = record.tool_calls

        # 1. schema_invalid → fallback → completion claim
        if (
            any(m in diag for m in ("schema_invalid", "json_parse_error"))
            and not tools
        ):
            claim_words = [
                "已保存", "已加入", "已添加", "已安排", "会提醒",
                "记住了", "记下了", "帮你加入", "已经安排",
            ]
            if any(w in reply for w in claim_words):
                record.passed = False
                record.failure_reasons.append(
                    "forbidden:schema_reject_then_claim"
                )

        # 2. chat_only → subsequent tool execution
        chat_mode = (
            record.semantic_decision.get("mode") in ("chat", "chat_only")
            and record.semantic_decision.get("intent") == "chat"
        )
        if chat_mode and tools:
            record.passed = False
            record.failure_reasons.append("forbidden:chat_only_then_tool")

        # 3. pending cancelled without cancel word
        if (
            record.pending_before != "idle"
            and "awaiting_clarification" in record.pending_before
            and record.pending_after == "idle"
        ):
            cancel_words = ["取消", "算了", "不用了", "不要了", "先不"]
            if not any(w in record.user_input for w in cancel_words):
                record.passed = False
                record.failure_reasons.append(
                    "forbidden:cancel_without_cancel_word"
                )

        # 4. previous_assistant_message written
        for item in tools:
            if item.get("tool") == "save_formal_memory":
                ref_info = record.reference_resolution
                if ref_info.get("source") == "previous_assistant_message":
                    record.passed = False
                    record.failure_reasons.append(
                        "forbidden:affirmative_raw_assistant"
                    )

        # 5. No ToolResult → completion claim
        has_write_success = any(
            t.get("success") and t.get("operation_kind") == "write"
            for t in record.tool_results
        )
        if not has_write_success:
            claim_words = [
                "已保存", "已加入", "已添加", "已安排", "会提醒",
                "记住了", "记下了", "帮你加入",
            ]
            if any(w in reply for w in claim_words):
                record.passed = False
                record.failure_reasons.append("forbidden:no_toolresult_claim")

        # 6. ActionClaimGuard blocked but reply still contains claims
        if "还没有执行" in reply and any(
            w in reply for w in ("已保存", "已加入", "记住了")
        ):
            record.passed = False
            record.failure_reasons.append(
                "forbidden:action_claim_guard_ineffective"
            )

        # Apply explicit expectations from case config
        expected = turn_cfg.get("expect", {})
        if "status" in expected and record.status != expected["status"]:
            record.passed = False
            record.failure_reasons.append(
                f"status:expected={expected['status']} actual={record.status}"
            )
        if "tool_calls" in expected:
            exp_tools = expected["tool_calls"]
            actual_tools = [t["tool"] for t in tools]
            if actual_tools != exp_tools:
                record.passed = False
                record.failure_reasons.append(
                    f"tools:expected={exp_tools} actual={actual_tools}"
                )
        if "forbidden_tools" in expected:
            actual_set = {t["tool"] for t in tools}
            forbidden = set(expected["forbidden_tools"])
            if actual_set & forbidden:
                record.passed = False
                record.failure_reasons.append(
                    f"forbidden_tools:{actual_set & forbidden}"
                )
        if "write_count" in expected:
            if record.actual_write_count != expected["write_count"]:
                record.passed = False
                record.failure_reasons.append(
                    f"write_count:expected={expected['write_count']} actual={record.actual_write_count}"
                )
        if "no_claims" in expected and expected["no_claims"]:
            no_claim_words = [
                "记住了", "已保存", "已加入", "已添加", "已安排",
                "会提醒", "记下了",
            ]
            found = [w for w in no_claim_words if w in reply]
            if found:
                record.passed = False
                record.failure_reasons.append(
                    f"forbidden_claims:{found}"
                )

    @staticmethod
    def _extract_reference_info(intent_result: dict) -> dict:
        entities = intent_result.get("entities", {})
        entities = entities if isinstance(entities, dict) else {}
        content = str(entities.get("content", "") or "")
        info = {"source": "", "has_reference": False}
        if content == "previous_assistant_message":
            info["source"] = "previous_assistant_message"
            info["has_reference"] = True
        elif content == "previous_user_message":
            info["source"] = "previous_user_message"
            info["has_reference"] = True
        return info

    def _verify_panels(self, service: AgentService, expectations: dict) -> dict:
        """Verify Growth and Memory panels show expected data."""
        results = {}
        growth = service.growth_manager
        memory = service.memory_manager

        if "plan_count" in expectations:
            tasks = growth.tasks()
            actual = len(tasks)
            expected = expectations["plan_count"]
            results["plan_panel"] = {
                "passed": actual >= expected,
                "expected_min": expected,
                "actual": actual,
                "tasks": [
                    {"title": t.get("title", ""), "done": t.get("done", False)}
                    for t in tasks[:10]
                ],
            }

        if "plan_titles_contain" in expectations:
            titles = {t.get("title", "") for t in growth.tasks()}
            expected_titles = set(expectations["plan_titles_contain"])
            results["plan_titles"] = {
                "passed": expected_titles.issubset(titles)
                or any(
                    any(et in t for t in titles)
                    for et in expected_titles
                ),
                "expected": list(expected_titles),
                "actual": list(titles),
            }

        if "memory_count" in expectations:
            mems = memory.memories()
            actual = len(mems)
            expected = expectations["memory_count"]
            results["memory_panel"] = {
                "passed": actual >= expected,
                "expected_min": expected,
                "actual": actual,
            }

        if "memory_content_contains" in expectations:
            mems = memory.memories()
            contents = {m.get("content", "") for m in mems}
            expected_contents = set(expectations["memory_content_contains"])
            results["memory_content"] = {
                "passed": any(
                    any(ec in c for c in contents)
                    for ec in expected_contents
                ),
                "expected_contains": list(expected_contents),
                "actual_contents": list(contents),
            }

        return results

    def _audit_log_chain(self, turn_records: List[TurnRecord]) -> dict:
        """Audit log chain integrity across all turns."""
        issues = []
        for r in turn_records:
            stages = r.diagnostic_stages
            if not stages:
                issues.append(f"turn_{r.turn_index}:no_diagnostic_stages")
                continue
            # SemanticDecision must appear exactly once
            sd_count = stages.count("SemanticDecision")
            if sd_count != 1:
                issues.append(
                    f"turn_{r.turn_index}:SemanticDecision_count={sd_count}"
                )
            # If tools were executed, ToolExecution must appear
            if r.tool_calls and "ToolExecution" not in str(stages):
                issues.append(
                    f"turn_{r.turn_index}:tools_executed_but_no_ToolExecution_in_log"
                )
            # If chat_only, no ToolExecution
            if not r.tool_calls and "ToolExecution" in str(stages):
                issues.append(
                    f"turn_{r.turn_index}:chat_only_but_ToolExecution_in_log"
                )
        return {"issues": issues, "passed": len(issues) == 0}
