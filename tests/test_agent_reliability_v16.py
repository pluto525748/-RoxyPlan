import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner
from modules.chat_history_manager import ChatHistoryManager
from modules.confirmation_manager import ConfirmationManager
from modules.contracts import ToolResult
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter, LLMIntentParser
from modules.knowledge_manager import KnowledgeManager
from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry
from server.agent_service import AgentService


class RecordingLLM:
    def __init__(self, reply="这是普通聊天回复。"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class StructuredIntentLLM(RecordingLLM):
    def __init__(self, payload, reply="结构化意图后的回复。"):
        super().__init__(reply)
        self.payload = payload

    def chat(self, messages):
        self.calls.append(messages)
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return self.payload
        return self.reply


class NoCallLLM(RecordingLLM):
    def chat(self, messages):
        raise AssertionError("deterministic operation must not call the LLM")


def build_service(root: Path, llm=None):
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "roxy_personality.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Roxy",
                "personality": "温和、认真、可靠。",
                "likes": "帮助用户稳定推进。",
                "speaking_style": "自然、简洁、不说教。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    private_dir = data_dir / "private"
    growth = GrowthManager(private_dir)
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private_dir / "backups",
        conflict_file=private_dir / "memory_conflicts.json",
    )
    history = ChatHistoryManager(private_dir)
    service = AgentService(
        project_root=root,
        growth_manager=growth,
        memory_manager=memory,
        chat_history_manager=history,
        llm_client=llm or RecordingLLM(),
    )
    return service, growth, memory, history


def build_core(root: Path):
    growth = GrowthManager(root / "growth")
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=root / "backups",
        conflict_file=root / "conflicts.json",
    )
    registry = create_roxy_tool_registry(growth, memory)
    executor = ToolExecutor(registry, SafetyPolicy())
    return growth, memory, registry, AgentCore(AgentPlanner(registry), executor)


def test_01_fixed_command_executes():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("今日计划：学习线性代数30分钟", "s")
        assert response.status == "completed"
        assert growth.tasks()[0]["duration_minutes"] == 30


def test_02_vague_wish_clarifies():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("我今天下午想学一会儿", "s")
        assert response.status == "clarification"
        assert "学什么" in response.message
        assert growth.tasks() == []


def test_03_idea_requires_confirmation_then_writes():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        pending = service.handle("今天抽点时间学机器学习", "s")
        assert pending.status == "confirmation_required"
        assert growth.tasks() == []
        confirmed = service.handle("确认", "s")
        assert confirmed.status == "completed"
        assert growth.tasks()[0]["title"] == "学习机器学习"


def test_04_missing_action_content_clarifies():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("今天推进不少，帮我记一下", "s")
        assert response.status == "clarification"
        assert growth.records_for_date() == []


def test_05_llm_structured_fallback_is_validated():
    payload = json.dumps(
        {
            "intent": "add_plan",
            "confidence": 0.91,
            "entities": {"tasks": ["练习线性代数20分钟"]},
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
        },
        ensure_ascii=False,
    )
    llm = StructuredIntentLLM(payload)
    router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    result = router.route("给今晚留点时间搞一下线代")
    assert result["intent"] == "add_plan"
    assert result["source"] == "llm"


def test_06_invalid_llm_output_falls_back_to_chat():
    router = IntentRouter(
        LLMIntentParser(StructuredIntentLLM("not-json").chat), enable_llm=True
    )
    assert router.route("这是一条没有规则匹配的表达")["intent"] == "chat"


def test_07_desktop_intent_pass_can_be_deferred():
    with tempfile.TemporaryDirectory() as temp:
        payload = json.dumps(
            {
                "intent": "add_plan",
                "confidence": 0.95,
                "entities": {"tasks": ["阅读算法20分钟"]},
                "needs_confirmation": False,
            },
            ensure_ascii=False,
        )
        llm = StructuredIntentLLM(payload)
        service, growth, _memory, _history = build_service(Path(temp), llm)
        service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
        turn = service.conversation_service.prepare(
            "给今晚留点时间看看算法",
            "s",
            record_history=False,
            allow_llm_intent=False,
        )
        assert turn.resolve_intent_in_worker is True
        response = service.conversation_service.complete(turn)
        assert response.status == "completed"
        assert growth.tasks()[0]["title"] == "阅读算法20分钟"


def test_08_ordinary_chat_does_not_execute_tool():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, growth, _memory, _history = build_service(Path(temp), llm)
        response = service.handle("今天的云看起来很好看", "s")
        assert response.status == "chat"
        assert growth.tasks() == []


def test_09_ordinary_message_interrupts_pending_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        service.handle("今天抽点时间学机器学习", "s")
        service.handle("换个话题吧", "s")
        response = service.handle("确认", "s")
        assert response.status == "failed"
        assert growth.tasks() == []


def test_10_restart_makes_confirmation_explicitly_invalid():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        first, _growth, _memory, _history = build_service(root, NoCallLLM())
        first.handle("今天抽点时间学机器学习", "s")
        second, growth, _memory, _history = build_service(root, NoCallLLM())
        response = second.handle("确认", "s")
        assert response.status == "failed"
        assert "重启" in response.message or "过期" in response.message
        assert growth.tasks() == []


def test_11_failed_tool_never_claims_success():
    with tempfile.TemporaryDirectory() as temp:
        _growth, _memory, registry, core = build_core(Path(temp))
        registry.get("add_plan").handler = lambda title, allow_duplicate=False: ToolResult(
            False, "add_plan", "write_failed", error="write_failed"
        )
        response = core.process(
            "添加计划",
            {"intent": "add_plan", "confidence": 1.0, "entities": {"tasks": ["测试"]}},
        )
        assert response.status == "failed"
        assert "加入今天计划" not in response.message


def test_12_success_without_postcondition_data_is_rejected():
    with tempfile.TemporaryDirectory() as temp:
        _growth, _memory, registry, core = build_core(Path(temp))
        registry.get("add_plan").handler = lambda title, allow_duplicate=False: ToolResult(
            True, "add_plan", "added", {}
        )
        response = core.process(
            "添加计划",
            {"intent": "add_plan", "confidence": 1.0, "entities": {"tasks": ["测试"]}},
        )
        assert response.status == "failed"
        assert "结果校验" in response.message


def test_13_plan_duration_update_uses_last_task():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习30分钟", "s")
        response = service.handle("刚才那个改成50分钟", "s")
        assert response.status == "completed"
        assert growth.tasks()[0]["duration_minutes"] == 50
        assert len(growth.tasks()) == 1


def test_14_plan_reschedule_updates_time():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：整理文档", "s")
        response = service.handle("推迟到晚上", "s")
        assert response.status == "completed"
        assert growth.tasks()[0]["time_slot"] == "晚上"


def test_15_plan_completion_changes_data():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习", "s")
        response = service.handle("这件事做完了", "s")
        assert response.status == "completed"
        assert growth.tasks()[0]["status"] == "completed"


def test_16_completed_plan_core_edit_requires_reopen():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习", "s")
        service.handle("这件事做完了", "s")
        response = service.handle("刚才那个改成50分钟", "s")
        assert response.status == "clarification"
        assert growth.tasks()[0]["duration_minutes"] is None


def test_17_reopen_completed_plan():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习", "s")
        service.handle("这件事做完了", "s")
        response = service.handle("重新打开这个", "s")
        assert response.status == "completed"
        assert growth.tasks()[0]["status"] == "pending"


def test_18_cancel_plan_requires_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：整理文档", "s")
        pending = service.handle("算了，不做这个了", "s")
        assert pending.status == "confirmation_required"
        service.handle("确认", "s")
        assert growth.tasks(include_cancelled=True)[0]["status"] == "cancelled"


def test_19_pronoun_delete_requires_exact_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：整理文档", "s")
        pending = service.handle("把刚才那个删了", "s")
        assert pending.status == "confirmation_required"
        assert "整理文档" in pending.message
        service.handle("确认", "s")
        assert growth.tasks(include_cancelled=True) == []


def test_20_similar_plan_is_not_duplicated_silently():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习30分钟", "s")
        response = service.handle("今日计划：学习机器学习50分钟", "s")
        assert response.status == "clarification"
        assert len(growth.tasks()) == 1
        assert "仍然添加" in response.message


def test_21_user_can_explicitly_keep_similar_plans():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划：学习机器学习30分钟", "s")
        response = service.handle("仍然添加：学习机器学习50分钟", "s")
        assert response.status == "completed"
        assert len(growth.tasks()) == 2


def test_22_ambiguous_task_match_clarifies():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("学习英语30分钟")
        growth.add_task("学习英语听力20分钟")
        response = service.handle("英语学完了", "s")
        assert response.status == "clarification"
        assert all(not item["done"] for item in growth.tasks())


def test_23_action_records_keep_appending():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("记录：完成了上午的阅读", "s")
        service.handle("记录：测试了成长面板", "s")
        assert len(growth.records_for_date()) == 2


def test_24_duplicate_action_is_not_written_twice():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("记录：测试成长面板", "s")
        response = service.handle("记录：测试成长面板", "s")
        assert len(growth.records_for_date()) == 1
        assert "没有重复" in response.message


def test_25_daily_review_regenerates_from_latest_state():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("上午任务")
        growth.complete_by_id(1)
        first = growth.generate_review()
        growth.add_task("下午任务")
        second = growth.generate_review()
        assert first["total"] == 1
        assert second["total"] == 2
        assert second["pending"] == 1


def test_26_growth_log_updates_same_day_entry():
    with tempfile.TemporaryDirectory() as temp:
        _service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("上午任务")
        growth.complete_by_id(1)
        first = growth.save_today_review()
        growth.add_task("下午任务")
        growth.complete_by_id(2)
        second = growth.save_today_review()
        assert first["uid"] == second["uid"]
        assert second["revision"] == 2
        assert second["review"]["done"] == 2
        assert len(growth.entries()) == 1


def test_27_historical_growth_entry_is_not_changed_by_today_save():
    with tempfile.TemporaryDirectory() as temp:
        now = lambda: datetime(2026, 7, 22, 15, 0, 0)
        growth = GrowthManager(Path(temp) / "growth", now_provider=now)
        old = growth.save_today_review("2026-07-21")
        growth.add_task("今天任务")
        growth.save_today_review()
        old_after = next(item for item in growth.entries() if item["date"] == "2026-07-21")
        assert old_after == old


def test_28_health_memory_is_used_for_related_question():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("先根据现在的不适休息一下。")
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我的肠胃比较敏感", category="health")
        service.handle("晚上吃什么比较舒服", "s")
        combined = "\n".join(item["content"] for item in llm.calls[-1])
        assert "肠胃比较敏感" in combined


def test_29_sensitive_memory_does_not_stick_to_next_turn():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("自然回应。")
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我的肠胃比较敏感", category="health")
        service.handle("晚上吃什么", "s")
        service.handle("哈哈", "s")
        combined = "\n".join(item["content"] for item in llm.calls[-1])
        assert "肠胃比较敏感" not in combined


def test_30_unrelated_sleep_and_insult_do_not_restore_health_context():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("自然回应。")
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我的肠胃比较敏感", category="health")
        service.handle("晚上吃什么", "s")
        service.handle("我要睡觉了", "s")
        sleep_context = "\n".join(item["content"] for item in llm.calls[-1])
        service.handle("你这个回答太笨了", "s")
        insult_context = "\n".join(item["content"] for item in llm.calls[-1])
        assert "肠胃比较敏感" not in sleep_context
        assert "肠胃比较敏感" not in insult_context


def test_31_session_suppression_blocks_sensitive_memory():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我的肠胃比较敏感", category="health")
        response = service.handle("不要再提肠胃了", "s")
        assert response.status == "completed"
        service.handle("聊聊机器学习", "s")
        combined = "\n".join(item["content"] for item in llm.calls[-1])
        assert "肠胃比较敏感" not in combined


def test_32_direct_health_question_temporarily_overrides_suppression():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我的肠胃比较敏感", category="health")
        service.handle("不要再提肠胃了", "s")
        service.handle("胃不舒服吃什么", "s")
        combined = "\n".join(item["content"] for item in llm.calls[-1])
        assert "肠胃比较敏感" in combined


def _seed_location_memories(memory):
    memory.add_memory(
        "我是洛阳人",
        category="other",
        scope="stable_identity",
        location="洛阳",
        allow_conflict=True,
        allow_similar=True,
    )
    memory.add_memory(
        "我当前在沈阳上学并在南塔实习",
        category="other",
        scope="current_state",
        location="沈阳",
        allow_conflict=True,
        allow_similar=True,
    )
    memory.add_memory(
        "我以后想逛洛邑古城",
        category="goal",
        scope="future_intent",
        location="洛阳",
        allow_conflict=True,
        allow_similar=True,
    )


def test_33_current_location_beats_stable_identity():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(
            Path(temp) / "memory.json",
            backup_dir=Path(temp) / "backups",
            conflict_file=Path(temp) / "conflicts.json",
        )
        _seed_location_memories(memory)
        results = MemoryRetriever(memory).retrieve("我周末附近有什么地方可以逛？")
        locations = [item["memory"].get("location") for item in results]
        assert "沈阳" in locations
        assert "洛阳" not in locations


def test_34_home_query_uses_stable_identity():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(
            Path(temp) / "memory.json",
            backup_dir=Path(temp) / "backups",
            conflict_file=Path(temp) / "conflicts.json",
        )
        _seed_location_memories(memory)
        results = MemoryRetriever(memory).retrieve("我回老家有什么地方能玩？")
        assert any(
            item["memory"].get("scope") == "stable_identity"
            and item["memory"].get("location") == "洛阳"
            for item in results
        )


def test_35_future_query_uses_future_intent():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(
            Path(temp) / "memory.json",
            backup_dir=Path(temp) / "backups",
            conflict_file=Path(temp) / "conflicts.json",
        )
        _seed_location_memories(memory)
        results = MemoryRetriever(memory).retrieve("以后去洛邑古城怎么玩？")
        assert any(item["memory"].get("scope") == "future_intent" for item in results)


def test_36_current_dialogue_overrides_formal_location_without_silent_write():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory(
            "我当前在沈阳上学",
            category="other",
            scope="current_state",
            location="沈阳",
            allow_conflict=True,
        )
        response = service.handle("我现在已经回洛阳了", "s")
        assert "待审核" in response.message
        assert all(item.get("location") != "洛阳" for item in memory.memories("active"))
        assert service.memory_candidate_manager.pending()[0]["memory_fields"]["location"] == "洛阳"
        messages = service.conversation_service.build_llm_messages("周末附近去哪", "s")
        combined = "\n".join(item["content"] for item in messages)
        assert "现在位于洛阳" in combined
        assert "当前在沈阳" not in combined


def test_37_candidate_is_not_presented_as_formal_memory():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        queued = service.handle("请记住我喜欢晚上学习", "s")
        shown = service.handle("你知道我什么", "s")
        assert "待审核" in queued.message
        assert "正式长期记忆" in shown.message
        assert "我现在记得" not in shown.message


def test_38_unresolved_conflict_is_not_silently_retrieved():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(
            Path(temp) / "memory.json",
            backup_dir=Path(temp) / "backups",
            conflict_file=Path(temp) / "conflicts.json",
        )
        old = memory.add_memory("我喜欢晚上学习", category="preference")["memory"]
        memory.create_relation_conflict(
            old, "我现在更适合早上学习", "preference", relation="conflict"
        )
        results = MemoryRetriever(memory).retrieve("什么时候学习适合我？")
        assert all(item["memory"]["id"] != old["id"] for item in results)


def test_39_delete_all_memory_never_routes_to_plan():
    result = IntentRouter().route("删除所有记忆")
    assert result["intent"] == "delete_memory"
    assert result["entities"]["scope"] == "all"
    assert result["needs_confirmation"] is True


def test_40_batch_delete_summary_has_scope_and_count():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(Path(temp), NoCallLLM())
        memory.add_memory("第一条", category="other")
        memory.add_memory("第二条", category="other")
        response = service.handle("删除所有记忆", "s")
        assert response.status == "confirmation_required"
        assert "2 条" in response.message
        assert memory.memories(status=None)


def test_41_batch_delete_executes_original_saved_operation():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(Path(temp), NoCallLLM())
        memory.add_memory("第一条", category="other")
        service.handle("删除所有记忆", "s")
        response = service.handle("删除", "s")
        assert response.status == "completed"
        assert memory.memories(status=None) == []


def test_42_current_conversation_query_is_deterministic():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), RecordingLLM("第一轮回复"))
        service.handle("我们正在测试会话恢复", "s")
        response = service.handle("你记得刚才说什么", "s")
        assert response.status == "completed"
        assert "测试会话恢复" in response.message


def test_43_sessions_are_isolated_and_history_is_truthful():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, history = build_service(Path(temp), RecordingLLM())
        service.handle("甲会话内容", "a")
        service.handle("乙会话内容", "b")
        assert "甲会话内容" in str(history.messages("a"))
        assert "乙会话内容" not in str(history.messages("a"))
        response = service.handle("你记得以前聊过什么", "b")
        assert response.status == "completed"
        assert "本地会话" in response.message


def test_44_knowledge_relevant_snippet_matches():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / "knowledge"
        directory.mkdir()
        (directory / "ml.md").write_text(
            "梯度下降通过沿负梯度方向更新参数。", encoding="utf-8"
        )
        context = KnowledgeManager(directory).build_context("梯度下降怎么更新")
        assert "负梯度方向" in context


def test_45_unrelated_knowledge_is_not_injected():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / "knowledge"
        directory.mkdir()
        (directory / "ml.md").write_text(
            "梯度下降通过沿负梯度方向更新参数。", encoding="utf-8"
        )
        assert KnowledgeManager(directory).build_context("晚饭吃什么") == ""


def test_46_empty_knowledge_is_safe():
    with tempfile.TemporaryDirectory() as temp:
        manager = KnowledgeManager(Path(temp) / "knowledge")
        assert manager.build_context("机器学习") == ""
        assert manager.file_count() == 0


def test_47_corrupt_knowledge_file_is_skipped():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / "knowledge"
        directory.mkdir()
        (directory / "broken.md").write_bytes(b"\xff\xfe\x00\x81")
        manager = KnowledgeManager(directory)
        assert manager.scan() == []
        assert manager.build_context("broken") == ""


def test_48_confirmation_state_is_isolated_by_conversation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(
            Path(temp), RecordingLLM()
        )
        pending = service.handle("今天抽点时间学机器学习", "session_a")
        assert pending.status == "confirmation_required"
        service.handle("换个话题吧", "session_b")
        confirmed = service.handle("确认", "session_a")
        assert confirmed.status == "completed"
        assert len(growth.tasks()) == 1


def test_49_confirmation_can_only_be_consumed_once_under_concurrency():
    manager = ConfirmationManager()
    manager.create("delete_plan", {"task_id": "task-1"}, "delete", scope="session")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: manager.consume(scope="session"), range(4)))

    assert sum(result is not None for result in results) == 1


def test_50_acquaintance_question_reads_formal_memory_without_llm():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(Path(temp), NoCallLLM())
        memory.add_memory("我正在学习机器学习", category="learning")

        response = service.handle("你认识我吗", "s")

        assert response.status == "completed"
        assert "我正在学习机器学习" in response.message


def test_51_new_learning_interest_clarifies_mode_instead_of_guessing():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        response = service.handle("我要学习唱歌", "s")

        assert response.status == "clarification"
        assert "现在开始" in response.message
        assert "加入今天计划" in response.message
        assert growth.tasks() == []


def test_52_generic_learning_arrangement_asks_for_subject_and_duration():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        response = service.handle("唱完了，给我下午安排学习任务", "s")

        assert response.status == "clarification"
        assert "哪类学习内容" in response.message
        assert "大概时长" in response.message
        assert growth.tasks() == []


def test_53_natural_scheduled_study_block_is_persisted_with_metadata():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        response = service.handle("我下午要安排3个小时的机器学习时间", "s")
        tasks = growth.tasks()

        assert response.status == "completed"
        assert len(tasks) == 1
        assert "机器学习" in str(tasks[0]["title"])
        assert tasks[0]["time_slot"] == "下午"
        assert tasks[0]["duration_minutes"] == 180


def test_54_previous_assistant_suggestion_becomes_candidate_not_formal_memory():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("下午任务：练习发声20分钟。")
        service, _growth, memory, _history = build_service(Path(temp), llm)
        service.handle("我们聊聊唱歌", "s")

        response = service.handle("把你说的这条加入长期记忆", "s")
        candidates = service.memory_candidate_manager.pending()

        assert response.status == "completed"
        assert "待审核记忆" in response.message
        assert "临时安排" in response.message
        assert memory.memories("active") == []
        assert len(candidates) == 1
        assert "练习发声" in str(candidates[0]["content"])


def test_55_generic_learning_words_do_not_select_specific_learning_memory():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(Path(temp) / "memory.json")
        memory.add_memory("我正在学习机器学习", category="learning")

        results = MemoryRetriever(memory).retrieve(
            "给我安排一个学习任务",
            update_usage=False,
        )

        assert results == []


def test_56_specific_learning_topic_still_retrieves_matching_memory():
    with tempfile.TemporaryDirectory() as temp:
        memory = MemoryManager(Path(temp) / "memory.json")
        memory.add_memory("我正在学习机器学习", category="learning")

        results = MemoryRetriever(memory).retrieve(
            "机器学习接下来怎么学",
            update_usage=False,
        )

        assert len(results) == 1


def test_57_chat_instruction_requires_direct_topic_faithful_answers():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("模型能力不能直接换算成人类年龄。")
        service, _growth, _memory, _history = build_service(Path(temp), llm)

        response = service.handle("这个模型相当于人类几岁的智力", "s")
        system_prompt = str(llm.calls[-1][0].get("content", ""))

        assert response.status == "chat"
        assert "先直接回应这一轮真正提出的问题" in system_prompt
        assert "不要擅自把" in system_prompt
        assert "不要强行追加无关练习" in system_prompt


def test_58_daily_plan_and_action_phrases_execute_without_llm():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(
            Path(temp), NoCallLLM()
        )
        plan = service.handle(
            "下午帮我留 50 分钟学特征工程，放进今天要做的事里",
            "s",
        )
        action = service.handle(
            "我今天早上坐地铁时听了三个 AI 前沿电台，把这个记成今天的行动",
            "s",
        )

        assert plan.status == "completed"
        assert growth.tasks()[0]["duration_minutes"] == 50
        assert "特征工程" in growth.tasks()[0]["title"]
        assert action.status == "completed"
        assert "AI 前沿电台" in growth.records_for_date()[0]["content"]


def test_59_explicit_sensitive_memory_only_creates_candidate():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(
            Path(temp), NoCallLLM()
        )
        response = service.handle("这点请记住：我的肠胃比较敏感", "s")

        assert response.status == "completed"
        assert "待审核" in response.message
        assert memory.memories("active") == []


def test_60_past_conversation_query_never_returns_null_text():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(
            Path(temp), NoCallLLM()
        )
        response = service.handle(
            "你记得我以前跟你的对话吗，不是今天的",
            "current",
        )

        assert response.status == "completed"
        assert response.message == "本地还没有可恢复的历史会话。"
        assert response.message.lower() not in {"none", "null"}


def test_61_reasoning_is_not_written_to_chat_history():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("<think>private-chain</think>public-answer")
        service, _growth, _memory, history = build_service(Path(temp), llm)

        response = service.handle("hello", "s")
        stored = json.dumps(history.messages("s"), ensure_ascii=False)

        assert response.message == "public-answer"
        assert "private-chain" not in stored
        assert "public-answer" in stored

        llm.reply = "<think>private-unclosed"
        response = service.handle("hello again", "s")
        stored = json.dumps(history.messages("s"), ensure_ascii=False)

        assert response.message
        assert "private-unclosed" not in response.message
        assert "private-unclosed" not in stored


TESTS = [
    value
    for name, value in sorted(globals().items())
    if name.startswith("test_") and callable(value)
]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"agent reliability v1.6 tests passed ({len(TESTS)} cases)")
