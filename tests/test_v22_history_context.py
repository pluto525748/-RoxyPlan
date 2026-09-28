from modules.chat_history_manager import ChatHistoryManager
from tests.v18_test_support import RecordingLLM, build_service


def _saved_summary(history, session_id: str, summary: str) -> None:
    history.new_session(session_id=session_id)
    history.add_message(session_id, "user", "用于建立可核验旧会话来源。")
    assert history.save_summary(session_id, summary)


def test_relevant_history_returns_at_most_two_query_centered_fragments(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    _saved_summary(
        history,
        "format-topic",
        "主要讨论：人格包的导入格式和字段边界。\n"
        "用户决定：周末去买机械键盘。\n"
        "当前状态：今天很疲惫。\n"
        "未完成事项：继续校验人格包 manifest 字段。",
    )
    _saved_summary(
        history,
        "schema-topic",
        "主要讨论：角色包导入时需要固定 schema。\n"
        "用户决定：晚饭吃羊肉串。",
    )
    _saved_summary(
        history,
        "budget-topic",
        "主要讨论：人格包导入的上下文预算。\n"
        "当前状态：跑步后有点累。",
    )

    matches = history.relevant_summaries(
        "人格包的导入格式接下来怎么收口？",
        exclude_session_id="current",
        limit=8,
        char_budget=900,
    )

    assert 1 <= len(matches) <= 2
    assert all("人格包" in item["summary"] or "导入" in item["summary"] for item in matches)
    rendered = "\n".join(item["summary"] for item in matches)
    assert "机械键盘" not in rendered
    assert "羊肉串" not in rendered
    assert "跑步" not in rendered
    assert all(item["time_range"]["start"] for item in matches)


def test_generic_greeting_does_not_pull_an_old_topic_by_boilerplate_overlap(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    _saved_summary(
        history,
        "old-agent-topic",
        "主要讨论：你好啊，今天继续完成 agent 自然语言处理。",
    )

    assert history.relevant_summaries(
        "你好啊，新的一天又见面了",
        exclude_session_id="current",
    ) == []


def test_one_generic_chinese_term_is_not_enough_for_implicit_history(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    _saved_summary(
        history,
        "old-language-topic",
        "主要讨论：英语学习时需要整理生词。",
    )

    assert history.relevant_summaries(
        "我最近在学习摄影构图",
        exclude_session_id="current",
    ) == []


def test_normal_chat_prompt_gets_only_relevant_evidence_with_implicit_use_policy(
    tmp_path,
):
    llm = RecordingLLM("可以从 manifest 的必填字段开始逐项核对。")
    service, _growth, memory, history = build_service(tmp_path, llm)
    _saved_summary(
        history,
        "old-persona-format",
        "主要讨论：人格包的导入格式和 manifest 字段边界。\n"
        "用户决定：晚上去吃羊肉串。\n"
        "当前状态：今天有点累。",
    )
    before_memories = memory.memories()

    messages = service.conversation_service.build_llm_messages(
        "人格包的导入格式我还是没想明白，接下来怎么做？",
        "current-chat",
    )
    combined = "\n".join(item["content"] for item in messages)

    assert "人格包的导入格式和 manifest 字段边界" in combined
    assert "羊肉串" not in combined
    assert "今天有点累" not in combined
    assert "自然衔接" in combined
    assert "当前用户表达" in combined
    assert "不得把旧会话" in combined
    assert memory.memories() == before_memories


def test_current_state_expression_is_not_overridden_by_weak_old_state_match(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    _saved_summary(history, "old-location", "主要讨论：用户现在在沈阳工作。")

    matches = history.relevant_summaries(
        "我现在已经在北京工作了",
        exclude_session_id="current",
    )

    assert matches == []
