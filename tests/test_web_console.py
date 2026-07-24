import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.knowledge_manager import KnowledgeManager
from modules.llm.contracts import ProviderHealth, ProviderResponse
from modules.llm.routed_client import RoutedLLMClient
from modules.llm.secret_store import SecretStore
from modules.memory_manager import MemoryManager
from server.agent_service import AgentService
from server.main import create_app


class RecordingLLM:
    provider = "ollama"
    base_url = "http://127.0.0.1:11434/v1"
    model = "qwen-test"

    def __init__(self, reply="测试回复"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class StaticProvider:
    supports_tools = True
    supports_thinking = True

    def __init__(self, provider_name, model_name, *, configured=True):
        self.provider_name = provider_name
        self.model_name = model_name
        self.configured = configured

    def health_check(self, *, force=False):
        return ProviderHealth(
            self.provider_name,
            "online" if self.configured else "not_configured",
            self.configured,
            self.model_name,
        )

    def chat(self, messages, **kwargs):
        return ProviderResponse(self.provider_name, self.model_name, content="测试回复")

    def chat_with_tools(self, messages, tools, **kwargs):
        return self.chat(messages, **kwargs)


@contextmanager
def local_token(value):
    previous = os.environ.get("ROXY_LOCAL_TOKEN")
    if value is None:
        os.environ.pop("ROXY_LOCAL_TOKEN", None)
    else:
        os.environ["ROXY_LOCAL_TOKEN"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ROXY_LOCAL_TOKEN", None)
        else:
            os.environ["ROXY_LOCAL_TOKEN"] = previous


async def request(app, method, path, *, host="127.0.0.1", **kwargs):
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://roxy.test") as client:
        return await client.request(method, path, **kwargs)


def build_service(root: Path, *, with_knowledge=False, llm=None, online=False):
    data_dir = root / "data"
    private_dir = data_dir / "private"
    knowledge_dir = data_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "roxy_personality.json").write_text(
        json.dumps(
            {
                "name": "Roxy",
                "personality": "温和可靠的魔法老师。",
                "likes": "帮助用户稳定学习。",
                "speaking_style": "简洁、温柔。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if with_knowledge:
        (knowledge_dir / "emg.md").write_text(
            "肌电项目的推荐采样率是 1000Hz，采集前先检查电极接触。",
            encoding="utf-8",
        )
    growth = GrowthManager(private_dir)
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private_dir / "backups",
        conflict_file=private_dir / "memory_conflicts.json",
    )
    history = ChatHistoryManager(private_dir)
    knowledge = KnowledgeManager(knowledge_dir)
    service = AgentService(
        project_root=root,
        growth_manager=growth,
        memory_manager=memory,
        chat_history_manager=history,
        knowledge_manager=knowledge,
        llm_client=llm or RecordingLLM(),
        status_probe=lambda _client: online,
    )
    return service, growth, memory, history


def test_web_knowledge_context_relevance_and_empty_fallback():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        llm = RecordingLLM()
        service, _growth, _memory, _history = build_service(
            root,
            with_knowledge=True,
            llm=llm,
        )

        service.handle("肌电项目的采样率应该设置多少", "web_knowledge")
        relevant = "\n".join(item["content"] for item in llm.calls[-1])
        assert "1000Hz" in relevant
        assert "本地知识库匹配片段" in relevant

        service.handle("Python 列表怎么追加元素", "web_knowledge")
        unrelated = "\n".join(item["content"] for item in llm.calls[-1])
        assert "1000Hz" not in unrelated
        assert "电极接触" not in unrelated

    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM()
        service, _growth, _memory, _history = build_service(Path(temp), llm=llm)
        response = service.handle("普通聊天", "web_empty_knowledge")
        assert response.status == "chat"
        assert response.message == "测试回复"


def test_model_status_exposes_configuration_state_but_never_api_key():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "private-deepseek-key-for-test"
            secrets = SecretStore(root)
            assert secrets.save_deepseek_key(secret)
            routed = RoutedLLMClient(
                root,
                settings={
                    "default_model": "fake-default",
                    "complex_model": "fake-complex",
                    "offline_model": "fake-local",
                },
                secret_store=secrets,
                deepseek_provider=StaticProvider("deepseek", "fake-default"),
                ollama_provider=StaticProvider("ollama", "fake-local"),
            )
            service, _growth, _memory, _history = build_service(root, llm=routed)
            app = create_app(agent_service=service)

            response = await request(app, "GET", "/v1/model-status")
            assert response.status_code == 200
            payload = response.json()
            serialized = json.dumps(payload, ensure_ascii=False)
            assert payload["api_key_configured"] is True
            assert secret not in serialized
            assert "api_key" not in payload
            assert "masked" not in serialized

    asyncio.run(scenario())


def test_plan_api_and_server_confirmation():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service, growth, _memory, _history = build_service(Path(temp))
            app = create_app(agent_service=service)
            listed = await request(app, "GET", "/v1/plans")
            added = await request(app, "POST", "/v1/plans", json={"title": "测试 Web 计划"})
            task_id = added.json()["data"]["task"]["id"]
            completed = await request(app, "POST", f"/v1/plans/{task_id}/complete")
            pending = await request(app, "DELETE", f"/v1/plans/{task_id}")

            assert listed.status_code == 200
            assert added.json()["success"] is True
            assert completed.json()["data"]["task"]["done"] is True
            assert pending.json()["error"]["code"] == "confirmation_required"
            assert len(growth.tasks()) == 1

            confirmation_id = pending.json()["data"]["confirmation"]["confirmation_id"]
            confirmed = await request(
                app,
                "POST",
                f"/v1/confirmations/{confirmation_id}",
            )
            assert confirmed.json()["success"] is True
            assert growth.tasks() == []

    asyncio.run(scenario())


def test_action_review_growth_and_memory_apis():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service, growth, memory, _history = build_service(Path(temp))
            memory.add_memory("我正在学习机器学习", category="learning", source="test")
            memory.add_memory("我的项目使用肌电传感器", category="project", source="test")
            app = create_app(agent_service=service)

            added = await request(
                app,
                "POST",
                "/v1/actions",
                json={"content": "完成了 Web 控制台测试"},
            )
            actions = await request(app, "GET", "/v1/actions")
            review = await request(app, "GET", "/v1/growth/review")
            saved = await request(app, "POST", "/v1/growth/review/save")
            logs = await request(app, "GET", "/v1/growth/logs")
            memories = await request(app, "GET", "/v1/memories")
            searched = await request(app, "GET", "/v1/memories?query=机器学习")

            assert added.json()["success"] is True
            assert actions.json()["data"]["records"][0]["content"] == "完成了 Web 控制台测试"
            assert review.json()["data"]["review"]["action_count"] == 1
            assert saved.json()["success"] is True
            assert len(logs.json()["data"]["entries"]) == 1
            assert len(memories.json()["data"]["memories"]) == 2
            assert searched.json()["data"]["memories"][0]["category"] == "learning"
            assert "uid" not in memories.json()["data"]["memories"][0]
            assert "use_count" not in memories.json()["data"]["memories"][0]
            assert len(growth.records_for_date()) == 1

    asyncio.run(scenario())


def test_web_session_create_restore_rename_and_isolation():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service, _growth, _memory, history = build_service(Path(temp))
            history.new_session("桌面对话")
            app = create_app(agent_service=service)

            created = await request(
                app,
                "POST",
                "/v1/conversations",
                json={"title": "手机测试"},
            )
            session_id = created.json()["session_id"]
            await request(
                app,
                "POST",
                "/v1/agent/requests",
                json={"message": "记住当前会话上下文", "conversation_id": session_id},
            )
            sessions = await request(app, "GET", "/v1/conversations")
            detail = await request(app, "GET", f"/v1/conversations/{session_id}")
            renamed = await request(
                app,
                "PATCH",
                f"/v1/conversations/{session_id}",
                json={"title": "移动端会话"},
            )

            assert created.status_code == 200
            assert all(item["session_id"].startswith("web_") for item in sessions.json())
            assert len(detail.json()["messages"]) == 2
            assert set(detail.json()["messages"][0]) == {
                "role",
                "content",
                "created_at",
            }
            assert renamed.json()["title"] == "移动端会话"
            assert history.get_session(session_id)["title"] == "移动端会话"

    asyncio.run(scenario())


def test_status_degrades_and_private_apis_require_authorization():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service, _growth, _memory, _history = build_service(
                Path(temp),
                with_knowledge=True,
                online=False,
            )
            app = create_app(agent_service=service)
            with local_token(None):
                status_response = await request(app, "GET", "/v1/status")
            assert status_response.status_code == 200
            assert status_response.json() == {
                "service": "ok",
                "ollama": "offline",
                "model": "qwen-test",
                "knowledge_files": 1,
            }

            with local_token("secret-token"):
                denied = await request(
                    app,
                    "POST",
                    "/v1/plans",
                    host="192.168.1.50",
                    json={"title": "不应写入"},
                    headers={"X-Roxy-Token": "wrong"},
                )
            assert denied.status_code == 401

    asyncio.run(scenario())


def test_web_page_uses_persistent_conversation_and_safe_text_rendering():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service, _growth, _memory, _history = build_service(Path(temp))
            app = create_app(agent_service=service)
            index = await request(app, "GET", "/")
            javascript = await request(app, "GET", "/static/app.js")
            assert index.status_code == 200
            assert "今日计划" in index.text
            assert "长期记忆" in index.text
            assert "待审核" in index.text
            assert "/v1/memory-candidates" in javascript.text
            assert "localStorage" in javascript.text
            assert "roxy_conversation_id" in javascript.text
            assert ".textContent" in javascript.text
            assert ".innerHTML" not in javascript.text

    asyncio.run(scenario())


if __name__ == "__main__":
    test_web_knowledge_context_relevance_and_empty_fallback()
    test_plan_api_and_server_confirmation()
    test_action_review_growth_and_memory_apis()
    test_web_session_create_restore_rename_and_isolation()
    test_status_degrades_and_private_apis_require_authorization()
    test_web_page_uses_persistent_conversation_and_safe_text_rendering()
    print("web console tests passed")
