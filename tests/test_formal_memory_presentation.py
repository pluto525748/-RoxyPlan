import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from modules.client_action_claim_guard import ClientActionClaimGuard
from v18_test_support import NoCallLLM, RecordingLLM, build_service


def _seed_formal_memories(memory, entries):
    items = []
    for memory_id, (content, category) in enumerate(entries, start=1):
        items.append(
            {
                "id": memory_id,
                "content": content,
                "category": category,
                "importance": 3,
                "status": "active",
                "use_count": 0,
                "created_at": f"2026-07-{min(memory_id + 10, 28):02d}T09:00:00",
                "updated_at": f"2026-07-{min(memory_id + 10, 28):02d}T09:00:00",
            }
        )
    assert memory.repository.save_memories(
        {"version": 2, "profile": {}, "memories": items}
    )


def _desktop_visible_message(response):
    return ClientActionClaimGuard().validate(
        response.message,
        [],
        action_expected=bool(response.client_actions),
    )


def test_formal_memory_with_dance_word_is_read_and_naturally_presented(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我喜欢看你跳舞", category="preference")

    response = service.handle("你知道我什么", "memory-presentation")
    visible = _desktop_visible_message(response)

    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert response.client_actions == []
    assert "你喜欢看我跳舞" in visible
    assert "没有成功派发舞蹈动作" not in visible
    assert all(result.tool != "play_dance" for result in response.tool_results)
    assert "**" not in response.message


def test_multiple_formal_memories_are_summarized_without_internal_labels(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我的长期目标是完成洛琪希项目", category="goal")
    memory.add_memory("我正在学习机器学习", category="learning")
    memory.add_memory("我的肠胃比较敏感", category="health")

    response = service.handle("说说你对我的了解", "memory-summary")

    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert "你的长期目标是完成洛琪希项目" in response.message
    assert "你正在学习机器学习" in response.message
    assert "你的肠胃比较敏感" in response.message
    for internal in ("[goal]", "[learning]", "[health]", "候选", "待审核"):
        assert internal not in response.message
    for heading in ("目标方面", "学习方面", "健康和生活方面", "喜好方面"):
        assert heading not in response.message
    assert not any(line.lstrip().startswith(("1.", "2.", "3.")) for line in response.message.splitlines())


def test_complete_formal_memory_set_becomes_a_deduplicated_natural_portrait(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    _seed_formal_memories(
        memory,
        [
            ("我正在学习机器学习", "learning"),
            ("我的肠胃不稳定", "health"),
            ("我喜欢早上安静学习", "preference"),
            (
                "2026年7月23日，下午学习特征工程，计划时长50分钟（后改为一小时）。",
                "learning",
            ),
            ("我一般早上安静的时候学习效率比较高", "habit"),
            (
                "2026年7月23日，用户已理解随机森林的两个核心概念（随机选择样本和随机选择特征），以及随机森林与随机树的区别；计划把中科院项目代码加入学习计划。",
                "project",
            ),
            ("我最近更适合早上努力学习", "preference"),
            ("完成洛琪希项目是我的长期目标", "goal"),
            (
                "用户正在做洛琪希项目，这是一个AI陪伴计划桌宠，完成它是用户的长期目标。",
                "other",
            ),
            ("第二个", "other"),
            ("我喜欢看你跳舞", "preference"),
        ],
    )
    list_calls = []
    original_list = service.memory_service.read_typed_memory

    def counted_typed_read(*args, **kwargs):
        list_calls.append((args, kwargs))
        return original_list(*args, **kwargs)

    service.memory_service.read_typed_memory = counted_typed_read

    response = service.handle("你知道我什么", "complete-memory-portrait")

    assert list_calls == [
        (
            (),
            {
                "category": None,
                "query_mode": "overview",
                "attribute": "",
                "topic": "",
                "query": "你知道我什么",
            },
        )
    ]
    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert response.client_actions == []
    assert response.status != "chat"
    assert "机器学习" in response.message
    assert "早晨学习" in response.message
    assert "肠胃" in response.message
    assert "长期目标" in response.message
    assert "你喜欢看我跳舞" in response.message
    assert "特征工程" in response.message
    assert "随机森林" in response.message
    assert "第二个" not in response.message
    assert "2026年7月23日" not in response.message
    assert response.message.count("长期目标") == 1
    for heading in ("目标方面", "学习方面", "健康和生活方面", "喜好方面"):
        assert heading not in response.message
    paragraphs = [part for part in response.message.split("\n\n") if part.strip()]
    assert 2 <= len(paragraphs) <= 4

    returned_contents = [
        item["content"] for item in response.tool_results[0].data["memories"]
    ]
    assert "第二个" in returned_contents
    assert returned_contents.count("完成洛琪希项目是我的长期目标") == 1


def test_meta_follow_up_repeats_formal_memory_query_in_same_conversation(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我喜欢看你跳舞", category="preference")

    first = service.handle("你知道我什么", "memory-follow-up")
    second = service.handle(
        "我问你你知道我什么就是想让你表达这个",
        "memory-follow-up",
    )

    assert [result.tool for result in first.tool_results] == ["list_memories"]
    assert [result.tool for result in second.tool_results] == ["list_memories"]
    assert "你喜欢看我跳舞" in second.message
    assert "需要先读取真实记忆数据" not in second.message
    assert "明白，刚才我说得像在念一份档案" in second.message
    assert first.message != second.message
    assert second.client_actions == []


def test_session_request_to_avoid_fixed_format_makes_memory_reply_concise(
    tmp_path,
):
    service, _growth, memory, _history = build_service(
        tmp_path,
        RecordingLLM("好，之后我会更自然、更精简地说。"),
    )
    for content, category in (
        ("我的长期目标是完成RoxyPlan封版", "goal"),
        ("我正在学习机器学习", "learning"),
        ("我最近需要照顾睡眠", "health"),
        ("我喜欢吃西瓜", "preference"),
        ("我习惯晨间阅读", "habit"),
        ("我正在研究桌面交互", "project"),
    ):
        memory.add_memory(content, category=category)
    conversation_id = "memory-concise-session-style"

    acknowledgement = service.handle(
        "不要固定这种版式回答我",
        conversation_id,
    )
    response = service.handle("你了解我什么", conversation_id)
    stored = response.tool_results[0].data["memories"]

    assert acknowledgement.status == "chat"
    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert len(stored) == 6
    assert "从这些事情里" not in response.message
    assert "念一份档案" not in response.message
    assert "\n\n" not in response.message
    assert "RoxyPlan封版" in response.message
    assert "你想问哪一块" in response.message
    assert sum(
        marker in response.message
        for marker in ("RoxyPlan封版", "机器学习", "睡眠", "西瓜", "晨间阅读", "桌面交互")
    ) <= 4
    assert service.conversation_service.state_manager.reference_context(
        conversation_id
    )["response_preferences"]["avoid_fixed_memory_format"] is True


def test_duplicate_formal_memories_are_presented_once(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    _seed_formal_memories(
        memory,
        [
            ("完成洛琪希项目是我的长期目标", "goal"),
            ("完成洛琪希项目是我的长期目标", "goal"),
            ("第二个", "other"),
        ],
    )

    response = service.handle("你记得我什么", "duplicate-memory-presentation")

    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert response.message.count("完成洛琪希项目是你的长期目标") == 1
    assert "第二个" not in response.message


def test_explicit_dance_still_dispatches_one_action(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())

    response = service.handle("跳舞", "exact-dance")

    assert [result.tool for result in response.tool_results] == ["play_dance"]
    assert len(response.client_actions) == 1
    assert response.client_actions[0].name == "play_dance"


def test_ordinary_dance_statement_is_chat_without_action_or_memory_write(tmp_path):
    service, _growth, memory, _history = build_service(
        tmp_path,
        RecordingLLM("我明白了。"),
    )

    response = service.handle("我喜欢看你跳舞", "ordinary-dance-chat")

    assert response.status == "chat"
    assert response.tool_results == []
    assert response.client_actions == []
    assert memory.memories() == []
    assert service.memory_service.list_candidates(status=None).data["candidates"] == []


def test_ordinary_chat_reply_is_displayed_without_markdown_strong_markers(tmp_path):
    service, _growth, _memory, _history = build_service(
        tmp_path,
        RecordingLLM("**你是一个认真投入的人**"),
    )

    response = service.handle("你好", "plain-text-chat")

    assert response.status == "chat"
    assert response.message == "你是一个认真投入的人"


def test_empty_formal_memory_query_has_natural_empty_state(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())

    response = service.handle("你记得我什么", "empty-memory")

    assert [result.tool for result in response.tool_results] == ["list_memories"]
    assert "还没有记住多少关于你的长期信息" in response.message
    assert "请记住" in response.message
    assert "需要先读取" not in response.message


def test_formal_save_reply_uses_roxy_to_user_perspective(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())

    response = service.handle("请记住我喜欢看你跳舞", "save-perspective")

    assert [result.tool for result in response.tool_results] == ["save_formal_memory"]
    assert response.tool_results[0].success is True
    assert "我记住了：你喜欢看我跳舞" in response.message
    assert [item["content"] for item in memory.memories()] == ["我喜欢看你跳舞"]
