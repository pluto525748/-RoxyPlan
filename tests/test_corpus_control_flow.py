"""Keep exact confirmation synonyms and independent reads out of fresh chat."""
import json
from pathlib import Path

import pytest

from test_beta_online_failure_replay import _build_service


CASES = {c["id"]: c for c in json.loads(
    (Path(__file__).parent / "fixtures/beta_corpus_v3_control_flow.json").read_text(encoding="utf-8")
)["cases"]}


@pytest.mark.parametrize("control,delete", [("确定。", True), ("我不确定。", False), ("你确定吗？", False), ("确定？", False)])
def test_confirmation_scope_not_fresh_delete_intent(tmp_path, monkeypatch, control, delete):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    case = CASES["bt_conf_05"]
    calls = []
    def transport(*args):
        calls.append(args)
        return case["turns"][-1]["responses"][0]
    service = _build_service(tmp_path, transport)
    service.memory_manager.add_memory("我喜欢看书", category="preference")
    service.memory_manager.add_memory("我喜欢吃梨", category="preference")
    preview = service.handle("忘记：我喜欢吃梨", "consent")
    assert preview.status == "confirmation_required"
    before = service.memory_manager.memory_file.read_bytes()
    response = service.handle(control, "consent")
    if delete:
        assert [(r.tool, r.success) for r in response.tool_results] == [("delete_memory", True)]
        assert [m["content"] for m in service.memory_manager.memories()] == ["我喜欢看书"]
        assert not calls  # exact, bounded consent is local; old semantic capture remains in fixture
        again = service.handle(control, "consent")
        assert not again.tool_results
    else:
        assert service.memory_manager.memory_file.read_bytes() == before
        assert not any(r.tool == "delete_memory" and r.success for r in response.tool_results)


def test_explicit_read_after_refinement_is_not_another_advice_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    turns = CASES["btm_02"]["turns"]
    remaining = list(turns[0]["responses"])
    def transport(*args):
        assert remaining, "explicit read must not request another model-generated plan"
        return remaining.pop(0)
    service = _build_service(tmp_path, transport)
    service.handle(turns[0]["user"], "read-now")
    assert not remaining
    response = service.handle(turns[-1]["user"], "read-now")
    assert [(r.tool, r.success) for r in response.tool_results] == [("show_plan", True)]
    assert not service.growth_manager.tasks()
