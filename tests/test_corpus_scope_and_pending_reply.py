"""Two current corpus first deviations, with unchanged actual API responses."""
import json
from pathlib import Path

import pytest

from modules.contracts import AgentResponse
from modules.response_composer import ResponseComposer
from test_beta_online_failure_replay import _build_service


CASES = {c["case_id"]: c for c in json.loads(
    (Path(__file__).parent / "fixtures/beta_corpus_v3_scope_and_pending_reply.json").read_text(encoding="utf-8")
)["cases"]}


def test_planning_a_future_duration_keeps_direction_list_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    case = CASES["bt_d30_02"]
    remaining = list(case["responses"])
    def transport(*args):
        assert remaining, "unexpected extra model call"
        return remaining.pop(0)
    service = _build_service(tmp_path, transport)
    response = service.handle(case["user"], "future-scope")
    state = service.interaction_coordinator.current("future-scope")
    assert response.tool_results == []
    assert state.interaction_kind == "discussion_choice"
    assert not state.last_suggestion_snapshot
    assert not remaining
    selected = service.handle("把第二个方向加入今天计划。", "future-scope")
    assert not selected.tool_results and not service.growth_manager.tasks()


def test_existing_pending_state_does_not_claim_a_new_completed_write():
    case = CASES["btm_03"]
    raw = case["responses"][-1]["choices"][0]["message"]["content"]
    response = ResponseComposer().compose(
        AgentResponse("chat", raw), user_text=case["user"],
        verified_turn_context={"execution": {"performed": False}, "semantic_intent": "chat"},
    )
    assert response.message == raw


@pytest.mark.parametrize("claim", [
    "本地系统已经把这项待完成计划标记完成了。",
    "我已经把待完成计划标记完成了。",
    "已经把待完成计划改到晚上，时长保持30分钟。",
    "我已经把今天计划设置为待完成。",
])
def test_pending_task_words_cannot_hide_an_actual_mutation_claim(claim):
    response = ResponseComposer().compose(
        AgentResponse("chat", claim), user_text="给我建议",
        verified_turn_context={"execution": {"performed": False}, "semantic_intent": "chat"},
    )
    assert response.message != claim
