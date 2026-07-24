import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.context_builder import ContextBuilder


def test_context_is_bounded_and_current_input_is_not_duplicated():
    builder = ContextBuilder(recent_message_limit=4)
    recent = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"消息{index}"}
        for index in range(8)
    ]
    recent.append({"role": "user", "content": "当前问题"})

    result = builder.build(
        personality_context="人格",
        memory_context="长期记忆",
        session_summary="",
        recent_messages=recent,
        knowledge_context="知识片段",
        current_user_input="当前问题",
        instruction="回答要求",
        memory_count=2,
    )

    conversational = [item for item in result if item["content"].startswith("消息")]
    assert [item["content"] for item in conversational] == ["消息4", "消息5", "消息6", "消息7"]
    assert sum(1 for item in result if item["content"] == "当前问题") == 1
    assert result[-1] == {"role": "user", "content": "当前问题"}


def test_summary_is_injected_before_recent_messages():
    builder = ContextBuilder(recent_message_limit=12)
    result = builder.build(
        personality_context="人格",
        memory_context="记忆",
        session_summary="讨论了测试策略",
        recent_messages=[{"role": "assistant", "content": "最近回复"}],
        knowledge_context="",
        current_user_input="继续",
    )

    summary_index = next(index for index, item in enumerate(result) if "当前会话摘要" in item["content"])
    recent_index = next(index for index, item in enumerate(result) if item["content"] == "最近回复")
    assert summary_index < recent_index
    assert "讨论了测试策略" in result[summary_index]["content"]


def test_empty_history_does_not_fail():
    result = ContextBuilder().build(
        personality_context="人格",
        memory_context="",
        session_summary="",
        recent_messages=[],
        knowledge_context="",
        current_user_input="你好",
    )
    assert result[-1] == {"role": "user", "content": "你好"}


if __name__ == "__main__":
    test_context_is_bounded_and_current_input_is_not_duplicated()
    test_summary_is_injected_before_recent_messages()
    test_empty_history_does_not_fail()
    print("context builder tests passed")
