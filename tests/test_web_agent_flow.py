import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from server.agent_service import AgentService


class RecordingLLM:
    def __init__(self, reply="这是统一会话服务的测试回复。"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class NoCallLLM(RecordingLLM):
    def chat(self, messages):
        raise AssertionError("deterministic requests must not call the LLM")


def build_service(root: Path, llm=None):
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "roxy_personality.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Roxy",
                "personality": "温和、认真、可靠的魔法老师。",
                "likes": "帮助用户稳定学习。",
                "speaking_style": "简洁、温柔、直接。",
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


def test_web_chat_and_context_include_personality():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, _memory, _history = build_service(Path(temp), llm)

        response = service.handle("你好，今天一起学习吧", "web_chat")
        combined = "\n".join(item["content"] for item in llm.calls[-1])

        assert response.status == "chat"
        assert response.message == llm.reply
        assert "人格配置" in combined
        assert "温和、认真、可靠的魔法老师" in combined


def test_web_show_memory_is_deterministic():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(Path(temp), NoCallLLM())
        memory.add_memory("我正在学习机器学习", category="learning")

        response = service.handle("你记得我什么", "web_memory")

        assert response.status == "completed"
        assert "你正在学习机器学习" in response.message


def test_web_plan_add_show_and_complete_flow():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        added = service.handle("今晚学习机器学习30分钟", "web_growth")
        shown = service.handle("看看我的计划", "web_growth")
        completed = service.handle("完成学习计划", "web_growth")

        assert added.status == "completed"
        assert shown.status == "completed"
        assert "学习机器学习30分钟" in shown.message
        assert completed.status == "completed"
        assert growth.tasks()[0]["done"] is True


def test_web_action_log_review_and_growth_log_flow():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        logged = service.handle("记录今天完成了Roxy部署测试", "web_growth")
        records = service.handle("查看记录", "web_growth")
        review = service.handle("今日复盘", "web_growth")
        saved = service.handle("保存今日复盘", "web_growth")
        growth_log = service.handle("查看成长日志", "web_growth")

        assert logged.status == "completed"
        assert growth.records_for_date()[0]["content"] == "今天完成了Roxy部署测试"
        assert "Roxy部署测试" in records.message
        assert review.status == "completed"
        assert saved.status == "completed"
        assert growth_log.status == "completed"


def test_web_daily_review_uses_llm_for_natural_summary_when_available():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("（轻轻点头）今天确实推进了一些事情。")
        service, growth, _memory, _history = build_service(Path(temp), llm)
        growth.add_task("整理项目文档")
        growth.complete_by_id(1)

        response = service.handle("今日复盘", "web_natural_review")

        assert response.status == "completed"
        assert response.message == "（轻轻点头）今天确实推进了一些事情。"
        assert llm.calls
        joined = "\n".join(str(item.get("content", "")) for item in llm.calls[-1])
        assert "已核验的今日复盘 JSON" in joined
        assert "整理项目文档" in joined


def test_web_context_skips_unrelated_sensitive_memory():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, memory, _history = build_service(Path(temp), llm)
        memory.add_memory("我正在学习梯度下降", category="learning")
        memory.add_memory("我的肠胃比较敏感", category="health")

        service.handle("梯度下降怎么理解", "web_context")
        combined = "\n".join(item["content"] for item in llm.calls[-1])

        assert "我正在学习梯度下降" in combined
        assert "我的肠胃比较敏感" not in combined


def test_web_conversation_survives_service_recreation():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        first_llm = RecordingLLM("第一轮回答")
        first, _growth, _memory, _history = build_service(root, first_llm)
        first.handle("我们正在测试统一会话", "local_default")

        second_llm = RecordingLLM("第二轮回答")
        second, _growth, _memory, history = build_service(root, second_llm)
        second.handle("继续刚才的话题", "local_default")
        messages = second_llm.calls[-1]
        combined = "\n".join(item["content"] for item in messages)

        assert "我们正在测试统一会话" in combined
        assert "第一轮回答" in combined
        assert len(history.messages("local_default")) == 4


if __name__ == "__main__":
    test_web_chat_and_context_include_personality()
    test_web_show_memory_is_deterministic()
    test_web_plan_add_show_and_complete_flow()
    test_web_action_log_review_and_growth_log_flow()
    test_web_context_skips_unrelated_sensitive_memory()
    test_web_conversation_survives_service_recreation()
    print("web agent flow tests passed")
