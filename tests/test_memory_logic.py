import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from io import BytesIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

import frontend.pet_app as pet_app
from frontend.pet_app import ChatWindow, default_memory, extract_nickname
from modules.llm_client import LLMClient


def roxy_personality_fixture():
    return {
        "version": 1,
        "name": "Roxy",
        "personality": "成长陪伴桌宠，温和、克制、专注于帮助用户持续成长。",
        "likes": "我还没有自己的喜好呢。",
        "speaking_style": "简洁、温柔、直接，优先给出能帮助用户行动和成长的回复。",
        "rules": [
            {"inputs": ["你好"], "reply": "你好，{nickname}。"},
            {"inputs": ["你是谁"], "reply": "我是{name}，一个成长陪伴桌宠。"},
            {"inputs": ["你喜欢谁"], "reply": "{likes}"},
        ],
        "fallback_reply": "你好，{nickname}，我是{name}。",
    }


def test_extract_nickname_from_sentence():
    assert extract_nickname("我叫煜") == "煜"
    assert extract_nickname("我的名字叫煜") == "煜"
    assert extract_nickname("叫我煜") == "煜"


def test_memory_json_writes_extracted_nickname():
    memory = default_memory()
    memory["profile"]["nickname"] = extract_nickname("我叫煜")

    with tempfile.TemporaryDirectory() as temp_dir:
        memory_file = Path(temp_dir) / "memory.json"
        memory_file.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        loaded = json.loads(memory_file.read_text(encoding="utf-8"))

    assert loaded["profile"]["nickname"] == "煜"


def test_default_reply_can_use_loaded_nickname():
    memory = default_memory()
    memory["profile"]["nickname"] = extract_nickname("我叫煜")

    reply = f"你好，{memory['profile']['nickname']}，我是Roxy。"

    assert reply == "你好，煜，我是Roxy。"


def test_chat_window_memory_lifecycle():
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as temp_dir:
        original_memory_file = pet_app.MEMORY_FILE
        pet_app.MEMORY_FILE = Path(temp_dir) / "memory.json"
        try:
            first_window = ChatWindow()
            assert first_window.save_first_nickname("我叫煜") is True
            assert first_window.memory["profile"]["nickname"] == "煜"
            assert first_window.default_reply() == "你好，煜，我是Roxy。"

            second_window = ChatWindow()
            assert second_window.memory["profile"]["nickname"] == "煜"
            assert second_window.default_reply() == "你好，煜，我是Roxy。"
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            app.processEvents()


def test_missing_memory_json_is_created_from_example():
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as temp_dir:
        original_memory_file = pet_app.MEMORY_FILE
        original_memory_example_file = pet_app.MEMORY_EXAMPLE_FILE
        pet_app.MEMORY_FILE = Path(temp_dir) / "memory.json"
        pet_app.MEMORY_EXAMPLE_FILE = Path(temp_dir) / "memory.example.json"
        pet_app.MEMORY_EXAMPLE_FILE.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profile": {"nickname": "", "preferences": []},
                    "memories": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            window = ChatWindow()

            assert pet_app.MEMORY_FILE.exists()
            loaded = json.loads(pet_app.MEMORY_FILE.read_text(encoding="utf-8"))
            assert loaded["profile"]["nickname"] == ""
            assert loaded["memories"] == []
            assert window.memory["profile"]["nickname"] == ""
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.MEMORY_EXAMPLE_FILE = original_memory_example_file
            app.processEvents()


def test_rule_personality_replies():
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as temp_dir:
        original_memory_file = pet_app.MEMORY_FILE
        original_personality_file = pet_app.PERSONALITY_FILE
        pet_app.MEMORY_FILE = Path(temp_dir) / "memory.json"
        pet_app.PERSONALITY_FILE = Path(temp_dir) / "roxy_personality.json"
        pet_app.PERSONALITY_FILE.write_text(
            json.dumps(roxy_personality_fixture(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            window = ChatWindow()
            window.memory["profile"]["nickname"] = "煜"

            assert window.match_personality_rule("你好") == "你好，煜。"
            assert window.match_personality_rule("你是谁") == "我是Roxy，一个成长陪伴桌宠。"
            assert window.match_personality_rule("你喜欢谁") == "我还没有自己的喜好呢。"
            assert window.match_personality_rule("？") is None
            assert window.default_reply() == "你好，煜，我是Roxy。"
            assert window.handle_personality_command("查看人格") is True
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.PERSONALITY_FILE = original_personality_file
            app.processEvents()


def test_knowledge_scan_loads_txt_and_md_files():
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as temp_dir:
        original_memory_file = pet_app.MEMORY_FILE
        original_personality_file = pet_app.PERSONALITY_FILE
        original_knowledge_dir = pet_app.KNOWLEDGE_DIR
        pet_app.MEMORY_FILE = Path(temp_dir) / "memory.json"
        pet_app.PERSONALITY_FILE = Path(temp_dir) / "roxy_personality.json"
        pet_app.KNOWLEDGE_DIR = Path(temp_dir) / "knowledge"
        pet_app.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        (pet_app.KNOWLEDGE_DIR / "roxy.txt").write_text("Roxy", encoding="utf-8")
        (pet_app.KNOWLEDGE_DIR / "notes.txt").write_text("Notes", encoding="utf-8")
        (pet_app.KNOWLEDGE_DIR / "guide.md").write_text("Guide", encoding="utf-8")
        (pet_app.KNOWLEDGE_DIR / "README.md").write_text("Directory notes", encoding="utf-8")
        (pet_app.KNOWLEDGE_DIR / "ignore.pdf").write_text("Ignore", encoding="utf-8")
        pet_app.PERSONALITY_FILE.write_text(
            json.dumps(roxy_personality_fixture(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            window = ChatWindow()

            assert window.knowledge_file_names() == ["guide.md", "notes.txt", "roxy.txt"]
            assert window.handle_knowledge_command("查看知识") is True
            assert window.handle_knowledge_command("查看人格") is False
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.PERSONALITY_FILE = original_personality_file
            pet_app.KNOWLEDGE_DIR = original_knowledge_dir
            app.processEvents()


def test_llm_client_requires_config():
    client = LLMClient({"base_url": "", "api_key": "", "model": ""})

    assert client.is_configured() is False
    assert client.chat([{"role": "user", "content": "hello"}]) == "大模型接口尚未配置，请检查 config.json。"


def test_llm_client_chat_completions_url():
    client = LLMClient(
        {
            "base_url": "https://api.example.com/v1",
            "api_key": "test-key",
            "model": "test-model",
        }
    )

    assert client.is_configured() is True
    assert client.chat_completions_url() == "https://api.example.com/v1/chat/completions"


def test_llm_client_http_error_includes_full_body():
    client = LLMClient({"base_url": "", "api_key": "", "model": ""})
    body = b'{"error":{"message":"quota exceeded","status":"RESOURCE_EXHAUSTED"}}'
    exc = HTTPError(
        url="https://api.example.com/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=BytesIO(body),
    )

    result = client.format_http_error(exc)

    assert result.startswith("HTTP 429")
    assert '"error"' in result
    assert "quota exceeded" in result


def test_llm_prompt_contains_personality_and_memory():
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as temp_dir:
        original_memory_file = pet_app.MEMORY_FILE
        original_personality_file = pet_app.PERSONALITY_FILE
        original_knowledge_dir = pet_app.KNOWLEDGE_DIR
        original_config_file = pet_app.CONFIG_FILE
        pet_app.MEMORY_FILE = Path(temp_dir) / "memory.json"
        pet_app.PERSONALITY_FILE = Path(temp_dir) / "roxy_personality.json"
        pet_app.KNOWLEDGE_DIR = Path(temp_dir) / "knowledge"
        pet_app.CONFIG_FILE = Path(temp_dir) / "config.json"
        pet_app.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        (pet_app.KNOWLEDGE_DIR / "notes.txt").write_text(
            "RoxyPlan 的 v0.8 重点是记忆规范化和知识库读取。",
            encoding="utf-8",
        )
        (pet_app.KNOWLEDGE_DIR / "guide.md").write_text(
            "学习提醒可以帮助用户保持节奏。",
            encoding="utf-8",
        )
        pet_app.PERSONALITY_FILE.write_text(
            json.dumps(roxy_personality_fixture(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pet_app.CONFIG_FILE.write_text(
            json.dumps({"base_url": "", "api_key": "", "model": ""}, indent=2),
            encoding="utf-8",
        )
        try:
            window = ChatWindow()
            window.memory_manager.update_profile({"nickname": "煜"})
            window.memory_manager.add_memory(
                "我喜欢洛琪希",
                category="preference",
                source="test",
            )
            prompt = window.build_system_prompt("RoxyPlan v0.8 的重点是什么？")

            assert "名称：Roxy" in prompt
            assert "用户昵称：煜" in prompt
            assert "我喜欢洛琪希" in prompt
            assert "scope=preference" in prompt
            assert "文件：notes.txt" in prompt
            assert "记忆规范化和知识库读取" in prompt
            assert window.ask_ai("测试") == "大模型接口尚未配置，请检查 config.json。"
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.PERSONALITY_FILE = original_personality_file
            pet_app.KNOWLEDGE_DIR = original_knowledge_dir
            pet_app.CONFIG_FILE = original_config_file
            app.processEvents()


if __name__ == "__main__":
    test_extract_nickname_from_sentence()
    test_memory_json_writes_extracted_nickname()
    test_default_reply_can_use_loaded_nickname()
    test_chat_window_memory_lifecycle()
    test_missing_memory_json_is_created_from_example()
    test_rule_personality_replies()
    test_knowledge_scan_loads_txt_and_md_files()
    test_llm_client_requires_config()
    test_llm_client_chat_completions_url()
    test_llm_client_http_error_includes_full_body()
    test_llm_prompt_contains_personality_and_memory()
    print("memory logic tests passed")
