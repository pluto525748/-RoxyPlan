import json
from pathlib import Path

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from server.agent_service import AgentService


class RecordingLLM:
    def __init__(self, reply="这是普通聊天测试回复。"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class NoCallLLM(RecordingLLM):
    def chat(self, messages):
        raise AssertionError("deterministic interaction must not call the LLM")


def build_service(root: Path, llm=None):
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "roxy_personality.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Roxy",
                "personality": "温和、认真、可靠的学习伙伴。",
                "speaking_style": "自然、简洁、直接。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    private = data_dir / "private"
    growth = GrowthManager(private)
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
        audit_file=private / "memory_audit.json",
    )
    history = ChatHistoryManager(private)
    service = AgentService(
        project_root=root,
        growth_manager=growth,
        memory_manager=memory,
        chat_history_manager=history,
        llm_client=llm or RecordingLLM(),
    )
    return service, growth, memory, history
