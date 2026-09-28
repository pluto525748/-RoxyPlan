import asyncio
import os
import sys
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from modules.contracts import AgentResponse
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from server.agent_service import AgentService
from server.main import create_app


class FakeAgentService:
    def handle(self, message, conversation_id):
        return AgentResponse(
            status="chat",
            message=f"收到：{message}",
            conversation_id=conversation_id,
        )


class FakeLLMClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return "这是临时模型回复。"


class FailingLLMClient(FakeLLMClient):
    def chat(self, messages):
        self.calls.append(messages)
        raise AssertionError("deterministic memory queries must not call the model")


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


async def request(app, host, method, path, **kwargs):
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://roxy.test") as client:
        return await client.request(method, path, **kwargs)


def test_health_and_static_files_are_available():
    async def scenario():
        app = create_app(agent_service=FakeAgentService())
        health = await request(app, "192.168.1.20", "GET", "/health")
        index = await request(app, "192.168.1.20", "GET", "/")
        css = await request(app, "192.168.1.20", "GET", "/static/style.css")
        javascript = await request(app, "192.168.1.20", "GET", "/static/app.js")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": "roxy-local-web"}
        assert index.status_code == 200 and "洛琪希" in index.text
        assert css.status_code == 200
        assert javascript.status_code == 200

    asyncio.run(scenario())


def test_empty_message_is_rejected():
    async def scenario():
        app = create_app(agent_service=FakeAgentService())
        with local_token(None):
            response = await request(
                app,
                "127.0.0.1",
                "POST",
                "/v1/agent/requests",
                json={"message": "   ", "conversation_id": "local_default"},
            )
        assert response.status_code == 422

    asyncio.run(scenario())


def test_agent_response_is_serializable_json():
    async def scenario():
        app = create_app(agent_service=FakeAgentService())
        with local_token(None):
            response = await request(
                app,
                "127.0.0.1",
                "POST",
                "/v1/agent/requests",
                json={"message": "你好", "conversation_id": "local_default"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["message"] == "收到：你好"
        assert payload["schema_version"] == "1.0"
        assert payload["request_id"].startswith("req_")

    asyncio.run(scenario())


def test_non_local_request_is_rejected_without_token():
    async def scenario():
        app = create_app(agent_service=FakeAgentService())
        with local_token(None):
            response = await request(
                app,
                "192.168.1.20",
                "POST",
                "/v1/agent/requests",
                json={"message": "你好", "conversation_id": "local_default"},
            )
        assert response.status_code == 403

    asyncio.run(scenario())


def test_configured_token_rejects_wrong_token_and_accepts_correct_token():
    async def scenario():
        app = create_app(agent_service=FakeAgentService())
        payload = {"message": "你好", "conversation_id": "local_default"}
        with local_token("correct-token"):
            wrong = await request(
                app,
                "192.168.1.20",
                "POST",
                "/v1/agent/requests",
                json=payload,
                headers={"X-Roxy-Token": "wrong-token"},
            )
            correct = await request(
                app,
                "192.168.1.20",
                "POST",
                "/v1/agent/requests",
                json=payload,
                headers={"X-Roxy-Token": "correct-token"},
            )
        assert wrong.status_code == 401
        assert correct.status_code == 200

    asyncio.run(scenario())


def test_real_agent_service_reuses_chat_plan_and_memory_core():
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = MemoryManager(
            root / "memory.json",
            backup_dir=root / "private" / "backups",
            conflict_file=root / "private" / "memory_conflicts.json",
        )
        service = AgentService(
            project_root=root,
            growth_manager=growth,
            memory_manager=memory,
            llm_client=FakeLLMClient(),
        )

        plan = service.handle("添加计划：测试局域网页面", "web_test")
        remembered = service.handle("记住：我正在测试 Local Web", "web_test")
        action_log = service.handle("查看记录", "web_test")
        chat = service.handle("这是普通聊天问题", "web_test")

        assert plan.status == "completed"
        assert growth.tasks()[0]["title"] == "测试局域网页面"
        assert remembered.status == "completed"
        assert [item["content"] for item in memory.memories()] == ["我正在测试 Local Web"]
        assert service.memory_candidate_manager.pending() == []
        assert action_log.status == "completed"
        assert "还没有行动记录" in action_log.message
        assert chat.status == "chat"
        assert chat.message == "这是临时模型回复。"
        assert chat.conversation_id == "web_test"


def test_web_chat_uses_personality_and_context_builder():
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        data_dir = root / "data"
        data_dir.mkdir(parents=True)
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
        growth = GrowthManager(root / "private")
        memory = MemoryManager(
            root / "memory.json",
            backup_dir=root / "private" / "backups",
            conflict_file=root / "private" / "memory_conflicts.json",
        )
        memory.add_memory(
            "我正在学习梯度下降和优化算法",
            category="learning",
        )
        llm = FakeLLMClient()
        service = AgentService(
            project_root=root,
            growth_manager=growth,
            memory_manager=memory,
            llm_client=llm,
        )

        response = service.handle("梯度下降应该怎么继续学习", "web_context")
        messages = llm.calls[-1]
        combined = "\n".join(item["content"] for item in messages)

        assert response.status == "chat"
        assert "人格配置" in combined
        assert "温和、认真、可靠的魔法老师" in combined
        assert "我正在学习梯度下降和优化算法" in combined
        assert messages[-1] == {
            "role": "user",
            "content": "梯度下降应该怎么继续学习",
        }


def test_show_memory_uses_verified_model_attempt_with_deterministic_fallback():
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        memory = MemoryManager(
            root / "memory.json",
            backup_dir=root / "private" / "backups",
            conflict_file=root / "private" / "memory_conflicts.json",
        )
        memory.add_memory("我正在学习机器学习", category="learning")
        llm = FailingLLMClient()
        service = AgentService(
            project_root=root,
            growth_manager=GrowthManager(root / "private"),
            memory_manager=memory,
            llm_client=llm,
        )

        response = service.handle("你都记住了我的什么信息", "web_memory")

        assert response.status == "completed"
        assert "你正在学习机器学习" in response.message
        assert llm.calls
        combined = "\n".join(
            str(item.get("content", ""))
            for call in llm.calls
            for item in call
        )
        assert "我正在学习机器学习" in combined


def test_web_chat_does_not_inject_unrelated_sensitive_memory():
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        memory = MemoryManager(
            root / "memory.json",
            backup_dir=root / "private" / "backups",
            conflict_file=root / "private" / "memory_conflicts.json",
        )
        memory.add_memory(
            "我正在学习梯度下降和优化算法",
            category="learning",
        )
        memory.add_memory(
            "我的肠胃比较敏感，不适合吃太油",
            category="health",
        )
        llm = FakeLLMClient()
        service = AgentService(
            project_root=root,
            growth_manager=GrowthManager(root / "private"),
            memory_manager=memory,
            llm_client=llm,
        )

        service.handle("梯度下降为什么这样更新", "web_sensitive")
        combined = "\n".join(item["content"] for item in llm.calls[-1])

        assert "我正在学习梯度下降和优化算法" in combined
        assert "肠胃比较敏感" not in combined


if __name__ == "__main__":
    test_health_and_static_files_are_available()
    test_empty_message_is_rejected()
    test_agent_response_is_serializable_json()
    test_non_local_request_is_rejected_without_token()
    test_configured_token_rejects_wrong_token_and_accepts_correct_token()
    test_real_agent_service_reuses_chat_plan_and_memory_core()
    test_web_chat_uses_personality_and_context_builder()
    test_show_memory_uses_verified_model_attempt_with_deterministic_fallback()
    test_web_chat_does_not_inject_unrelated_sensitive_memory()
    print("local web tests passed")
