import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, RecordingLLM, build_service


def test_real_dialogue_slot_filling_and_reference_update():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        first = service.handle("我下午想学一会儿", "real-slots")
        second = service.handle("特征工程，一个小时", "real-slots")
        assert first.status == "clarification"
        assert second.status == "completed"
        created = dict(growth.tasks()[0])

        updated = service.handle("刚才那个改成50分钟", "real-slots")
        current = growth.tasks()[0]
        assert updated.status == "completed"
        assert current["uid"] == created["uid"]
        assert current["title"] == created["title"]
        assert current["duration_minutes"] == 50


def test_real_dialogue_unresolved_write_blocks_the_entire_write_batch():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle(
            "把今晚学习计划改成40分钟，再记录今天完成了V1.7接入",
            "real-batch",
        )
        assert response.status in {"clarification", "failed"}
        assert growth.records_for_date() == []
        assert response.tool_results == []
        assert "没有执行其中任何一项" in response.message


def test_real_dialogue_action_log_offer_requires_user_acceptance():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        offered = service.handle("我已经理解随机森林的两个随机性来源", "real-offer")
        assert offered.status in {"clarification", "confirmation_required"}
        assert growth.records_for_date() == []
        accepted = service.handle("可以，记下来吧", "real-offer")
        assert accepted.status == "completed"
        assert len(growth.records_for_date()) == 1


def test_real_dialogue_advice_never_writes():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(
            Path(temp), RecordingLLM("可以先了解基础概念，再做一个小练习。")
        )
        response = service.handle("你觉得我下午学什么好", "real-advice")
        assert response.status == "chat"
        assert growth.tasks() == []


def test_candidate_batch_compatibility_api_uses_true_ids():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.memory_service.set_candidates_enabled(True)
        first = service.memory_service.create_candidate("我喜欢早上安静学习")
        second = service.memory_service.create_candidate("RoxyPlan 是我的长期项目")
        first_id = int(first.data["candidate"]["id"])
        second_id = int(second.data["candidate"]["id"])

        rejected = service.memory_service.reject_candidate(first_id)
        accepted = service.memory_service.accept_candidate(second_id)
        assert rejected.success
        assert accepted.success
        candidates = {
            int(item["id"]): item
            for item in service.memory_service.list_candidates(
                status=None
            ).data["candidates"]
        }
        assert candidates[first_id]["status"] == "rejected"
        assert candidates[second_id]["status"] == "accepted"
        memories = service.memory_service.list_memories().data["memories"]
        assert any("RoxyPlan" in item["content"] for item in memories)


if __name__ == "__main__":
    test_real_dialogue_slot_filling_and_reference_update()
    test_real_dialogue_partial_success_keeps_independent_action()
    test_real_dialogue_action_log_offer_requires_user_acceptance()
    test_real_dialogue_advice_never_writes()
    test_candidate_batch_compatibility_api_uses_true_ids()
    print("V1.8 real dialogue tests passed")
