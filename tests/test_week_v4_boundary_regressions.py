"""First live v4 failures: advice, title scope and explicit review saving."""
import json
from pathlib import Path

import pytest

from modules.contracts import AgentResponse
from modules.response_composer import ResponseComposer
from test_beta_online_failure_replay import _build_service, _provider_body


CAPTURES = {c["id"]: c for c in json.loads(
    (Path(__file__).parent / "fixtures/learning_week_v4_boundary_failures.json").read_text(encoding="utf-8")
)["cases"]}


def test_captured_historical_completion_does_not_erase_new_advice():
    raw = CAPTURES["g1-d3-t02"]["raw_responses"][-1]["choices"][0]["message"]["content"]
    response = ResponseComposer().compose(
        AgentResponse("chat", raw), user_text=CAPTURES["g1-d3-t02"]["user"],
        verified_turn_context={"execution": {"performed": False}, "semantic_intent": "chat"},
    )
    assert response.message == raw


def test_exact_single_title_quantities_do_not_become_batch_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    capture = CAPTURES["g1-d4-t06"]
    raw = capture["raw_responses"][0]
    decision = json.loads(raw["choices"][0]["message"]["content"])
    title = decision["entities"]["match_text"]
    service = _build_service(tmp_path, lambda *args: raw)
    chosen = service.growth_manager.add_task(title)
    other = service.growth_manager.add_task("只保留另一项")
    service.handle("查看今天的计划", "quantified-title")
    response = service.handle(capture["user"], "quantified-title")
    assert [(r.tool, r.success) for r in response.tool_results] == [("complete_plan", True)]
    tasks = {t["uid"]: t for t in service.growth_manager.tasks()}
    assert tasks[chosen["uid"]]["done"] is True
    assert tasks[other["uid"]]["done"] is False


@pytest.mark.parametrize("command", [
    "不要把今天的写5个例子标记完成。",
    "把今天的写5个例子和另一项都标记完成。",
])
def test_literal_title_does_not_authorize_negative_or_expanded_scope(tmp_path, monkeypatch, command):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    decision = json.loads(CAPTURES["g1-d4-t06"]["raw_responses"][0]["choices"][0]["message"]["content"])
    decision["entities"]["match_text"] = "写5个例子"
    service = _build_service(tmp_path, lambda *args: _provider_body({"content": json.dumps(decision, ensure_ascii=False)}))
    service.growth_manager.add_task("写5个例子")
    service.growth_manager.add_task("另一项")
    service.handle("查看今天的计划", "scope")
    service.handle(command, "scope")
    assert all(not t["done"] for t in service.growth_manager.tasks())


@pytest.mark.parametrize("claim", [
    "昨天我已经帮你把计划标记完成了。",
    "昨天已经把今天计划全部完成了。",
    "今天的计划已完成。",
])
def test_past_progress_exception_does_not_allow_app_mutation_claims(claim):
    response = ResponseComposer().compose(
        AgentResponse("chat", claim), user_text="给我一点学习建议",
        verified_turn_context={"execution": {"performed": False}, "semantic_intent": "chat"},
    )
    assert response.message != claim


def test_explicit_review_request_saves_actual_zero_progress_too(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    capture = CAPTURES["g1-d3-t15"]
    def transport(method, url, headers, payload, timeout):
        prompt = str(payload["messages"][0]["content"])
        if "You classify one Chinese user message" in prompt:
            return capture["raw_responses"][0]
        return _provider_body({"content": "今天还没有完成计划，也没有计划外行动记录。"})
    service = _build_service(tmp_path, transport)
    task = service.growth_manager.add_task("保留未完成项")
    response = service.handle(capture["user"], "save-review")
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    entry = response.tool_results[0].data["entry"]
    stored = json.loads(service.growth_manager.growth_file.read_text(encoding="utf-8"))["entries"][task["date"]]
    assert entry == stored
    assert not service.growth_manager.tasks()[0]["done"]
