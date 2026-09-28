from __future__ import annotations

import json
from datetime import datetime

from modules.intent_router import LLMIntentParser
from tests.v18_test_support import NoCallLLM, build_service


class CurrentMonthArgumentLLM:
    def chat(self, messages):
        first = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in first:
            return json.dumps(
                {
                    "mode": "read",
                    "intent": "show_growth_log",
                    "entities": {"month": "本月"},
                    "proposed_tool": "show_growth_log",
                    "confidence": 0.99,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "command",
                    "request_mode": "query",
                    "explicit_command": True,
                },
                ensure_ascii=False,
            )
        return "只根据已核验的成长记录回答。"


def test_explicit_timer_request_cannot_be_reinterpreted_as_plan_write(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())

    response = service.handle(
        "现在开始计时25分钟，直接替我启动。",
        "timer-must-not-create-plan",
    )

    assert response.status == "failed"
    assert response.tool_results == []
    assert growth.tasks() == []
    assert "没有启动计时，也没有创建计划" in response.message


def test_relative_current_month_argument_is_normalized_before_growth_read(tmp_path):
    llm = CurrentMonthArgumentLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    growth.save_today_review()

    response = service.handle(
        "查看本月成长日志，只给真实统计。",
        "relative-current-month",
    )

    assert response.status == "completed"
    assert response.tool_results[0].tool == "show_growth_log"
    assert response.tool_results[0].data["month"] == datetime.now().strftime("%Y-%m")
