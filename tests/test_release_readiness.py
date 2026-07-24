from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.knowledge_manager import KnowledgeManager
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.repositories.local_json_growth_repository import LocalJsonGrowthRepository
from server.agent_service import AgentService


class OfflineLLM:
    provider = "ollama"
    model = "release-test-model"
    base_url = "http://127.0.0.1:1/v1"

    def chat(self, _messages):
        return "本地 Ollama 当前未连接，请启动服务后再试。"


def build_service(root: Path) -> AgentService:
    private = root / "data" / "private"
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
    )
    return AgentService(
        project_root=root,
        growth_manager=GrowthManager(private),
        memory_manager=memory,
        memory_candidate_manager=MemoryCandidateManager(repository=memory.repository),
        chat_history_manager=ChatHistoryManager(private),
        knowledge_manager=KnowledgeManager(root / "data" / "knowledge"),
        llm_client=OfflineLLM(),
        status_probe=lambda _client: False,
    )


def test_ollama_offline_does_not_disable_local_tools():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        state = service.status()
        assert state["ollama"] == "offline"
        reply = service.handle("你好", "web_offline")
        assert "Ollama" in reply.message
        added = service.execute_tool("add_plan", {"title": "离线也能保存的计划"})
        assert added.success is True
        assert service.execute_tool("show_plan").data["tasks"][0]["title"] == "离线也能保存的计划"


def test_empty_and_damaged_knowledge_do_not_break_chat():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        knowledge = root / "data" / "knowledge"
        knowledge.mkdir(parents=True)
        service = build_service(root)
        assert service.handle("普通聊天", "web_empty").status == "chat"

        (knowledge / "damaged.md").write_bytes(b"\xff\xfe\x00broken")
        assert service.knowledge_manager.file_count() == 0
        assert service.handle("另一个普通问题", "web_damaged").status == "chat"


def test_atomic_write_failure_preserves_original_document():
    with tempfile.TemporaryDirectory() as temp:
        repository = LocalJsonGrowthRepository(Path(temp) / "private")
        original = {"version": 1, "days": {"2026-01-01": {"tasks": []}}}
        changed = {"version": 1, "days": {"2026-01-02": {"tasks": []}}}
        assert repository.save_plan(original)
        with patch(
            "modules.repositories.local_json_utils.os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            assert repository.save_plan(changed) is False
        assert json.loads(repository.plan_file.read_text(encoding="utf-8")) == original
        assert not list(repository.plan_file.parent.glob(f".{repository.plan_file.name}.*.tmp"))


def test_two_manager_instances_preserve_concurrent_updates():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        private = root / "private"

        growth_a = GrowthManager(private)
        growth_b = GrowthManager(private)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: pair[0].add_task(pair[1]), [
                (growth_a, "桌面计划"),
                (growth_b, "Web 计划"),
            ]))
        assert {item["title"] for item in growth_a.tasks()} == {"桌面计划", "Web 计划"}

        history_a = ChatHistoryManager(private)
        history_a.new_session(session_id="web_shared")
        history_b = ChatHistoryManager(private)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: pair[0].add_message("web_shared", "user", pair[1]), [
                (history_a, "桌面消息"),
                (history_b, "Web 消息"),
            ]))
        assert {item["content"] for item in history_a.messages("web_shared")} == {"桌面消息", "Web 消息"}

        memory_a = MemoryManager(
            root / "memory.json",
            backup_dir=private / "backups",
            conflict_file=private / "memory_conflicts.json",
        )
        memory_b = MemoryManager(
            root / "memory.json",
            backup_dir=private / "backups",
            conflict_file=private / "memory_conflicts.json",
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: pair[0].add_memory(pair[1], category="project"), [
                (memory_a, "桌面端项目状态"),
                (memory_b, "Web 端项目状态"),
            ]))
        assert {item["content"] for item in memory_a.memories()} == {"桌面端项目状态", "Web 端项目状态"}
        assert len(memory_a.audit_entries()) == 2

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(memory_a.update_profile, {"nickname": "并发昵称"}),
                pool.submit(memory_b.add_memory, "并发新增记忆", category="learning"),
            ]
            [future.result() for future in futures]
        assert memory_a.data["profile"]["nickname"] == "并发昵称"
        assert any(item["content"] == "并发新增记忆" for item in memory_a.memories())

        candidates_a = MemoryCandidateManager(repository=memory_a.repository)
        candidates_b = MemoryCandidateManager(repository=memory_b.repository)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(
                lambda pair: pair[0].add_candidate(pair[1], "preference", pair[1]),
                [(candidates_a, "偏好安静环境"), (candidates_b, "偏好早起学习")],
            ))
        assert len(candidates_a.pending()) == 2


def test_two_processes_preserve_shared_repository_updates():
    with tempfile.TemporaryDirectory() as temp:
        private = Path(temp) / "private"
        GrowthManager(private)
        script = (
            "import sys; from pathlib import Path; "
            "from modules.growth_manager import GrowthManager; "
            "GrowthManager(Path(sys.argv[1])).add_task(sys.argv[2])"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(private), title],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for title in ("desktop process task", "web process task")
        ]
        results = [process.communicate(timeout=15) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], results
        assert {item["title"] for item in GrowthManager(private).tasks()} == {
            "desktop process task",
            "web process task",
        }


def test_corrupt_private_json_is_backed_up_without_silent_overwrite():
    with tempfile.TemporaryDirectory() as temp:
        private = Path(temp) / "private"
        private.mkdir(parents=True)
        plan = private / "today_plan.json"
        history = private / "chat_history.json"
        plan.write_text("{broken", encoding="utf-8")
        history.write_text("{broken", encoding="utf-8")

        GrowthManager(private)
        ChatHistoryManager(private)

        assert plan.read_text(encoding="utf-8") == "{broken"
        assert history.read_text(encoding="utf-8") == "{broken"
        backups = list((private / "backups").glob("corrupt_*.json"))
        assert any("today_plan" in item.name for item in backups)
        assert any("chat_history" in item.name for item in backups)


def test_confirmation_is_runtime_only_and_expires_on_restart():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        first = build_service(root)
        task = first.execute_tool("add_plan", {"title": "需要确认删除"}).data["task"]
        pending = first.execute_tool("delete_plan", {"task_ref": str(task["id"])})
        confirmation_id = pending.data["confirmation"]["confirmation_id"]

        restarted = build_service(root)
        result = restarted.confirm_tool(confirmation_id)
        assert result.success is False
        assert result.error.code == "confirmation_missing_or_expired"
        assert len(restarted.growth_manager.tasks()) == 1


def test_memory_audit_api_redacts_full_content():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_manager.add_memory("不应由审计接口返回的完整敏感正文", category="health")
        payload = service.memory_audit_view()
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "不应由审计接口返回的完整敏感正文" not in serialized
        assert payload["entries"][0]["new_value"]["content_redacted"] is True


def test_import_and_launcher_smoke_checks():
    server_check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import server.main; assert 'PySide6' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert server_check.returncode == 0, server_check.stderr

    desktop_check = subprocess.run(
        [
            sys.executable,
            "-c",
            "from PySide6.QtWidgets import QApplication; import frontend.pet_app; assert QApplication.instance() is None",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert desktop_check.returncode == 0, desktop_check.stderr

    web_launcher = (ROOT / "run_roxy_web.bat").read_text(encoding="utf-8")
    desktop_launcher = (ROOT / "roxy.bat").read_text(encoding="utf-8")
    assert "%~dp0" in web_launcher and "0.0.0.0" in web_launcher
    assert "%~dp0" in desktop_launcher
    assert not re.search(r"[A-Za-z]:[\\/]Users[\\/]", desktop_launcher, re.IGNORECASE)


def test_production_sources_have_no_private_absolute_path():
    patterns = (
        "*.bat",
        "frontend/**/*.py",
        "modules/**/*.py",
        "server/**/*.py",
        "server/web/*.js",
        "server/web/*.html",
    )
    private_path = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE)
    findings = []
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() and private_path.search(path.read_text(encoding="utf-8", errors="ignore")):
                findings.append(path.relative_to(ROOT).as_posix())
    assert findings == []


if __name__ == "__main__":
    test_ollama_offline_does_not_disable_local_tools()
    test_empty_and_damaged_knowledge_do_not_break_chat()
    test_atomic_write_failure_preserves_original_document()
    test_two_manager_instances_preserve_concurrent_updates()
    test_two_processes_preserve_shared_repository_updates()
    test_corrupt_private_json_is_backed_up_without_silent_overwrite()
    test_confirmation_is_runtime_only_and_expires_on_restart()
    test_memory_audit_api_redacts_full_content()
    test_import_and_launcher_smoke_checks()
    test_production_sources_have_no_private_absolute_path()
    print("release readiness tests passed")
