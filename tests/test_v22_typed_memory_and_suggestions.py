import json

from modules.contracts import AgentResponse
from modules.intent_router import LLMIntentParser
from modules.memory_data_query_guard import MemoryDataQueryGuard
from modules.response_composer import ResponseComposer
from v18_test_support import NoCallLLM, RecordingLLM, build_service


class SequencedMemoryAnswerLLM(RecordingLLM):
    def __init__(self, replies):
        super().__init__("")
        self.replies = list(replies)

    def chat(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


class SuggestionReferenceLLM:
    """Return an intentionally non-canonical ordinal on the follow-up turn."""

    def __init__(self):
        self.semantic_messages = []

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            self.semantic_messages.append(prompt)
            user_message = prompt.rsplit("User message: ", 1)[-1]
            if "第二个也加入" in user_message:
                # Reproduce the production model shape that treated the
                # deictic phrase itself as a title instead of a task_ref.
                entities = {"title": "第二个也"}
                follow_up_target = "suggestion_snapshot"
            else:
                entities = {"title": "你刚才的安排"}
                follow_up_target = "previous_assistant_plan"
            return json.dumps(
                {
                    "mode": "write",
                    "intent": "add_plan",
                    "entities": entities,
                    "proposed_tool": "add_plan",
                    "confidence": 0.99,
                    "follow_up_target": follow_up_target,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "commitment",
                    "request_mode": "execute",
                    "explicit_command": True,
                },
                ensure_ascii=False,
            )
        if "提取可执行的今日计划候选" in prompt:
            return json.dumps(
                {
                    "options": [
                        {"id": "1", "title": "花25分钟看完一小节机器学习内容"},
                        {"id": "2", "title": "用5分钟写下三句话总结"},
                    ]
                },
                ensure_ascii=False,
            )
        return "这是普通聊天回复。"


class SemanticChatLLM:
    def __init__(self, reply):
        self.reply = str(reply)

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "chat",
                    "intent": "chat",
                    "entities": {},
                    "proposed_tool": None,
                    "confidence": 0.99,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "question",
                    "request_mode": "discuss",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return self.reply


class ProgressOfferLLM:
    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "write",
                    "intent": "add_action_log",
                    "entities": {"content": "今天看了一会儿小说"},
                    "proposed_tool": "add_action_log",
                    "confidence": 0.99,
                    "follow_up_target": None,
                    "needs_confirmation": True,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "commitment",
                    "request_mode": "possible_action",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return "我听到了。"


def _production_semantic_service(tmp_path, llm):
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def test_typed_name_read_prefers_formal_memory_over_stale_profile(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.update_profile({"nickname": "我"})
    saved = memory.add_memory("以后叫我提格斯大魔王", category="other")["memory"]

    response = service.handle("我叫什么", "typed-name")

    assert [item.tool for item in response.tool_results] == ["list_memories"]
    assert "提格斯大魔王" in response.message
    assert "不知道" not in response.message
    typed = response.tool_results[0].data["memory_read"]
    assert typed["request"]["query_mode"] == "attribute"
    assert typed["request"]["attribute"] == "preferred_name"
    assert typed["facts"][0]["memory_id"] == int(saved["id"])
    assert memory.get(int(saved["id"]))["use_count"] == 1


def test_typed_food_preference_read_returns_matching_facts_only(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我喜欢吃西瓜", category="preference")
    memory.add_memory("吃羊肉串", category="preference")
    memory.add_memory("我喜欢无压力学习", category="preference")

    response = service.handle("我喜欢吃什么", "typed-food")

    assert "西瓜" in response.message
    assert "羊肉串" in response.message
    assert "无压力学习" not in response.message
    typed = response.tool_results[0].data["memory_read"]
    assert typed["request"]["attribute"] == "preference"
    assert typed["request"]["topic"] == "food"
    assert len(typed["facts"]) == 2


def test_typed_memory_result_is_naturalized_by_model_from_verified_fact(tmp_path):
    llm = SequencedMemoryAnswerLLM(
        [
            json.dumps(
                {
                    "answer": "记得，你喜欢吃西瓜。",
                    "used_memory_ids": [1],
                },
                ensure_ascii=False,
            )
        ]
    )
    service, _growth, memory, _history = build_service(tmp_path, llm)
    memory.add_memory("我喜欢吃西瓜", category="preference")

    response = service.handle("我是不是喜欢吃西瓜", "natural-memory-answer")

    assert response.message == "记得，你喜欢吃西瓜。"
    assert [item.tool for item in response.tool_results] == ["list_memories"]
    assert response.tool_results[0].data["natural_answer_memory_ids"] == [1]
    assert "正式长期记忆里找到了相关记录" not in response.message


def test_overview_gives_model_every_verified_formal_memory_without_output_cap(tmp_path):
    llm = SequencedMemoryAnswerLLM(
        [
            json.dumps(
                {
                    "answer": "我记得你在学机器学习，也喜欢吃西瓜。",
                    "used_memory_ids": [1, 5],
                },
                ensure_ascii=False,
            )
        ]
    )
    service, _growth, memory, _history = build_service(tmp_path, llm)
    entries = (
        ("我正在学习机器学习", "learning"),
        ("我的长期目标是完成RoxyPlan", "goal"),
        ("我习惯早上阅读", "habit"),
        ("我目前在研究桌面交互", "project"),
        ("我喜欢吃西瓜", "preference"),
    )
    for content, category in entries:
        memory.add_memory(content, category=category)

    response = service.handle("你了解我什么", "all-memory-evidence")

    assert response.message == "我记得你在学机器学习，也喜欢吃西瓜。"
    prompt = "\n".join(
        str(item.get("content", ""))
        for item in llm.calls[0]
        if isinstance(item, dict)
    )
    assert all(content in prompt for content, _category in entries)
    assert response.tool_results[0].data["natural_answer_memory_ids"] == [1, 5]


def test_explicit_all_memory_query_retries_until_every_fact_is_covered(tmp_path):
    llm = SequencedMemoryAnswerLLM(
        [
            json.dumps(
                {
                    "answer": "我记得你喜欢吃西瓜，也在学习机器学习。",
                    "used_memory_ids": [1, 2],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "answer": (
                        "我记得你喜欢吃西瓜，也在学习机器学习，"
                        "长期目标是完成RoxyPlan。"
                    ),
                    "used_memory_ids": [1, 2, 3],
                },
                ensure_ascii=False,
            ),
        ]
    )
    service, _growth, memory, _history = build_service(tmp_path, llm)
    memory.add_memory("我喜欢吃西瓜", category="preference")
    memory.add_memory("我正在学习机器学习", category="learning")
    memory.add_memory("我的长期目标是完成RoxyPlan", category="goal")

    response = service.handle(
        "把你知道关于我的全部内容都告诉我",
        "exhaustive-memory-answer",
    )

    assert len(llm.calls) == 2
    assert "西瓜" in response.message
    assert "机器学习" in response.message
    assert "RoxyPlan" in response.message
    assert response.tool_results[0].data["natural_answer_memory_ids"] == [1, 2, 3]


def test_internship_location_question_uses_only_current_location_fact(tmp_path):
    llm = SequencedMemoryAnswerLLM(
        [
            json.dumps(
                {
                    "answer": "知道，你现在在中科院沈阳自动化研究所实习。",
                    "used_memory_ids": [1],
                },
                ensure_ascii=False,
            )
        ]
    )
    service, _growth, memory, _history = build_service(tmp_path, llm)
    memory.add_memory(
        "我现在在中科院沈阳自动化研究所实习",
        category="other",
        scope="current_state",
    )
    memory.add_memory(
        "我要在实习过程中完成求职和代码学习",
        category="project",
    )

    response = service.handle("我在哪里实习你知道吗", "internship-location")
    typed = response.tool_results[0].data["memory_read"]

    assert response.message == "知道，你现在在中科院沈阳自动化研究所实习。"
    assert typed["request"]["attribute"] == "current_state"
    assert typed["request"]["topic"] == "location"
    assert len(typed["facts"]) == 1


def test_named_stable_fact_read_is_not_forced_into_current_state(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    saved = memory.add_memory(
        "我的桌面验收代号是银杏九二一",
        category="other",
        scope="stable_identity",
    )["memory"]
    memory.add_memory("我的备用标记是海棠七号", category="other")

    response = service.handle("我的桌面验收代号是什么？", "typed-custom-fact")

    assert "银杏九二一" in response.message
    assert "海棠七号" not in response.message
    typed = response.tool_results[0].data["memory_read"]
    assert typed["request"]["query_mode"] == "attribute"
    assert typed["request"]["attribute"] == "fact"
    assert typed["request"]["topic"] == "桌面验收代号"
    assert [item["memory_id"] for item in typed["facts"]] == [saved["id"]]


def test_named_fact_parser_does_not_capture_plan_or_review_domains():
    assert MemoryDataQueryGuard().route("我的今日计划是什么？") is None
    assert MemoryDataQueryGuard().route("我的复盘是什么？") is None


def test_generic_assertion_is_not_misclassified_as_location():
    from modules.memory_manager import MemoryManager

    assert MemoryManager.extract_location("我的桌面验收代号是银杏九二一") is None
    assert MemoryManager.extract_location("我是洛阳人") == "洛阳"
    assert MemoryManager.extract_location("我来自杭州") == "杭州"


def test_temporary_memory_stays_stored_but_does_not_participate_in_reads(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    temporary = memory.add_memory(
        "我今天晚上要早睡",
        category="habit",
        scope="temporary_state",
    )["memory"]
    memory.add_memory("我喜欢吃西瓜", category="preference")

    response = service.handle("你知道我什么", "typed-overview")

    assert "西瓜" in response.message
    assert "今天晚上要早睡" not in response.message
    assert memory.get(int(temporary["id"])) is not None
    assert memory.get(int(temporary["id"]))["use_count"] == 0


def test_chat_memory_usage_updates_only_when_final_reply_uses_the_fact(tmp_path):
    ignored_llm = SemanticChatLLM("可以先把桌面整理一下，再决定从哪里开始。")
    service, _growth, memory, _history = _production_semantic_service(
        tmp_path / "ignored", ignored_llm
    )
    saved = memory.add_memory("我喜欢安静的学习环境", category="preference")["memory"]

    service.handle("学习环境怎么安排更适合我", "memory-not-used")

    assert memory.get(int(saved["id"]))["use_count"] == 0

    used_llm = SemanticChatLLM("你喜欢安静的学习环境，可以先关掉消息提醒。")
    service2, _growth2, memory2, _history2 = _production_semantic_service(
        tmp_path / "used", used_llm
    )
    saved2 = memory2.add_memory("我喜欢安静的学习环境", category="preference")["memory"]

    service2.handle("学习环境怎么安排更适合我", "memory-used")

    assert memory2.get(int(saved2["id"]))["use_count"] == 1


def test_second_suggestion_can_be_added_after_first_selection_completed(tmp_path):
    llm = SuggestionReferenceLLM()
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    conversation_id = "suggestion-snapshot"
    history.new_session(session_id=conversation_id)
    history.add_message(
        conversation_id,
        "assistant",
        "1. 花25分钟看完一小节机器学习内容\n2. 用5分钟写下三句话总结",
        intent="chat",
    )

    listed = service.handle("嗯，把你刚刚梳理的安排塞进我今天待办吧", conversation_id)
    assert listed.status == "clarification"
    assert "1." in listed.message and "2." in listed.message

    first = service.handle("第一个，晚上30分钟", conversation_id)
    assert [item.tool for item in first.tool_results] == ["add_plan"]
    assert first.tool_results[0].success is True

    second = service.handle("第二个也加入，晚上15分钟", conversation_id)
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert second.tool_results[0].success is True
    assert [item["title"] for item in growth.tasks()] == [
        "花25分钟看完一小节机器学习内容",
        "用5分钟写下三句话总结",
    ]
    assert [item["duration_minutes"] for item in growth.tasks()] == [30, 15]
    assert [item["time_slot"] for item in growth.tasks()] == ["晚上", "晚上"]
    assert any(
        "suggestion_snapshot" in prompt
        and "花25分钟看完一小节机器学习内容" in prompt
        and "用5分钟写下三句话总结" in prompt
        for prompt in llm.semantic_messages
    )
    state = service.conversation_service.interaction_coordinator.current(conversation_id)
    snapshot = state.last_suggestion_snapshot
    assert [item["consumed"] for item in snapshot["objects"]] == [True, True]

    repeated = service.handle("第一个也加入", conversation_id)
    assert not any(
        item.tool == "add_plan" and item.success
        for item in repeated.tool_results
    )
    assert len(growth.tasks()) == 2

    cross_session = service.handle("第二个也加入", "another-conversation")
    assert not any(
        item.tool == "add_plan" and item.success
        for item in cross_session.tool_results
    )
    assert len(growth.tasks()) == 2


def test_chat_without_tool_result_cannot_claim_action_log_write():
    response = ResponseComposer().compose(
        AgentResponse("chat", "好，我把它记进今天的行动记录。"),
        user_text="好",
    )

    assert "记进今天的行动记录" not in response.message
    assert "表达不够准确" in response.message


def test_suggestion_is_consumed_after_explicit_duplicate_confirmation(tmp_path):
    service, growth, _memory, history = _production_semantic_service(
        tmp_path, SuggestionReferenceLLM()
    )
    conversation_id = "suggestion-duplicate"
    history.new_session(session_id=conversation_id)
    history.add_message(
        conversation_id,
        "assistant",
        "1. 花25分钟看完一小节机器学习内容\n2. 用5分钟写下三句话总结",
        intent="chat",
    )
    growth.add_task("花25分钟看完一小节机器学习内容")
    service.handle("把你刚刚梳理的安排加入今天待办", conversation_id)

    duplicate = service.handle("第一个", conversation_id)
    assert duplicate.status == "clarification"
    assert len(growth.tasks()) == 1

    added = service.handle("仍然添加", conversation_id)

    assert [item.tool for item in added.tool_results] == ["add_plan"]
    assert added.tool_results[0].success is True
    snapshot = service.conversation_service.interaction_coordinator.current(
        conversation_id
    ).last_suggestion_snapshot
    assert [item["consumed"] for item in snapshot["objects"]] == [True, False]
    assert "suggestion_snapshot_selection" not in added.tool_results[0].data["task"]


def test_chat_truth_guard_does_not_block_non_application_narration():
    message = "我把这段经历记录在小说里了。"
    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="你后来怎么处理灵感的？",
    )

    assert response.message == message


def test_typed_memory_guard_only_answers_questions_about_the_user():
    guard = MemoryDataQueryGuard()

    assert guard.route("你叫什么名字") is None
    assert guard.route("你喜欢吃什么") is None
    assert guard.route("你的目标是什么") is None
    assert guard.route("你现在在哪里") is None
    assert guard.route("我叫什么") is not None
    assert guard.route("我喜欢吃什么") is not None
    assert guard.route("把你知道关于我的全部内容都告诉我")["intent"] == "show_memory"
    assert guard.route("查看待审核候选") is None
    assert guard.is_retired_candidate_request("查看待审核候选") is True


def test_typed_memory_guard_supports_explicit_formal_categories_as_a_class():
    guard = MemoryDataQueryGuard()

    expected = {
        "看看项目记忆": "project",
        "我的长期目标记忆有哪些": "goal",
        "列出偏好记忆": "preference",
        "查看习惯记忆": "habit",
    }
    for text, attribute in expected.items():
        routed = guard.route(text)
        assert routed is not None
        assert routed["intent"] == "show_memory"
        assert routed["entities"]["query_mode"] == "attribute"
        assert routed["entities"]["attribute"] == attribute

    assert guard.route("项目记忆是怎么设计的") is None


def test_progress_offer_uses_typed_pending_before_generic_confirmation(tmp_path):
    service, growth, _memory, _history = _production_semantic_service(
        tmp_path,
        ProgressOfferLLM(),
    )

    offered = service.handle("我今天看了一会儿小说", "typed-action-offer")

    assert offered.status == "clarification"
    assert growth.records_for_date() == []
    pending = service.conversation_service.interaction_coordinator.current(
        "typed-action-offer"
    )
    assert pending.interaction_kind == "action_log_offer"
    assert pending.immutable_arguments == {
        "tool_name": "add_action_log",
        "content": "今天看了一会儿小说",
    }

    accepted = service.handle("好", "typed-action-offer")

    assert [item.tool for item in accepted.tool_results] == ["add_action_log"]
    assert accepted.tool_results[0].success is True
    assert [item["content"] for item in growth.records_for_date()] == [
        "今天看了一会儿小说"
    ]


def test_identity_and_overview_share_verified_memory_filter(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.update_profile({"nickname": "旧资料昵称"})
    expired = memory.add_memory(
        "我的名字是过期昵称",
        valid_until="2000-01-01T00:00:00+08:00",
        allow_similar=True,
        allow_conflict=True,
    )["memory"]
    memory.add_memory(
        "我的名字是未来昵称",
        valid_from="2099-01-01T00:00:00+08:00",
        allow_similar=True,
        allow_conflict=True,
    )
    current = memory.add_memory(
        "以后叫我星海",
        allow_similar=True,
        allow_conflict=True,
    )["memory"]

    name = service.handle("我叫什么", "verified-identity")
    overview = service.handle("你知道我什么", "verified-overview")

    assert "星海" in name.message
    assert memory.preferred_name() == "星海"
    assert "过期昵称" not in overview.message
    assert "未来昵称" not in overview.message
    assert memory.get(int(expired["id"]))["use_count"] == 0
    assert memory.get(int(current["id"]))["use_count"] >= 1


def test_typed_memory_read_rejects_incomplete_or_invalid_structure(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我喜欢吃西瓜", category="preference")

    invalid_mode = service.memory_service.read_typed_memory(query_mode="anything")
    missing_attribute = service.memory_service.read_typed_memory(query_mode="attribute")

    assert invalid_mode.success is False
    assert invalid_mode.status == "validation_error"
    assert invalid_mode.data == {}
    assert missing_attribute.success is False
    assert missing_attribute.status == "validation_error"
    assert missing_attribute.data == {}


def test_memory_content_update_refreshes_search_tags(tmp_path):
    _service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    saved = memory.add_memory("我喜欢安静学习", category="preference")["memory"]

    updated = memory.update_memory(
        int(saved["id"]),
        content="我喜欢吃草莓",
    )

    assert "草莓" in updated["tags"]
    assert "安静" not in updated["tags"]
