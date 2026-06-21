from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.llm_client import LLMClient, load_llm_config
from frontend.desktop_pet import DesktopPet


MEMORY_FILE = PROJECT_ROOT / "memory.json"
PERSONALITY_FILE = PROJECT_ROOT / "data" / "roxy_personality.json"
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
CONFIG_FILE = PROJECT_ROOT / "config.json"


def default_memory() -> Dict[str, Any]:
    return {
        "version": 1,
        "profile": {},
        "memories": [],
    }


def default_personality() -> Dict[str, Any]:
    return {
        "version": 1,
        "name": "",
        "personality": "",
        "likes": "",
        "speaking_style": "",
        "rules": [],
        "fallback_reply": "你好，{nickname}。",
    }


def clean_nickname(nickname: str) -> str:
    return nickname.strip(" \t\r\n。.!！?？：:")


def extract_nickname(user_text: str) -> str:
    # 支持“我叫煜”“我的名字叫煜”“叫我煜”等常见昵称表达。
    text = user_text.strip()
    patterns = (
        r"^我叫\s*(.+)$",
        r"^我的名字叫\s*(.+)$",
        r"^我的昵称是\s*(.+)$",
        r"^叫我\s*(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return clean_nickname(match.group(1))
    return clean_nickname(text)


class ChatReplyWorker(QObject):
    finished = Signal(str)

    def __init__(self, llm_client: LLMClient, messages: List[Dict[str, str]]) -> None:
        super().__init__()
        self.llm_client = llm_client
        self.messages = messages

    def run(self) -> None:
        self.finished.emit(self.llm_client.chat(self.messages))


class ChatWindow(QMainWindow):
    """V0.3：带本地长期记忆的聊天窗口。"""

    def __init__(self, pet_controller: Optional[Any] = None) -> None:
        super().__init__()
        self.pet_controller = pet_controller
        self.reply_thread: Optional[QThread] = None
        self.reply_worker: Optional[ChatReplyWorker] = None
        self.memory = self.load_memory()
        self.personality = self.load_personality()
        self.knowledge_files = self.scan_knowledge_files()
        self.llm_client = LLMClient(load_llm_config(CONFIG_FILE))

        self.setWindowTitle("RoxyPlan · 洛琪希")
        self.resize(400, 400)

        container = QWidget()
        container.setObjectName("chatRoot")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("聊天记录会出现在这里。")
        self.transcript.setObjectName("chatTranscript")

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("和洛琪希说点什么吧...")
        self.input_box.setObjectName("chatInput")
        self.input_box.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_message)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        input_layout.addWidget(self.input_box)
        input_layout.addWidget(self.send_button)

        layout.addWidget(self.transcript)
        layout.addLayout(input_layout)

        self.setCentralWidget(container)
        self.apply_chat_style()
        self.greet_user()
        self.log_knowledge_summary()
        QTimer.singleShot(0, self.move_to_bottom_right)

    def apply_chat_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#chatRoot {
                background: #F7F4FF;
                color: #333333;
                font-family: "Microsoft YaHei";
            }
            QTextEdit#chatTranscript {
                background: #FFFFFF;
                border: 1px solid #D8CCFF;
                border-radius: 14px;
                padding: 10px;
                color: #333333;
                font-family: "Microsoft YaHei";
                font-size: 13px;
                selection-background-color: #D8CCFF;
            }
            QLineEdit#chatInput {
                background: #FFFFFF;
                border: 1px solid #D8CCFF;
                border-radius: 16px;
                padding: 9px 12px;
                color: #333333;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }
            QLineEdit#chatInput:focus {
                border: 1px solid #8EA7FF;
            }
            QPushButton#sendButton {
                background: #8EA7FF;
                color: #FFFFFF;
                border: none;
                border-radius: 16px;
                padding: 9px 18px;
                font-family: "Microsoft YaHei";
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#sendButton:hover {
                background: #7894F8;
            }
            QPushButton#sendButton:pressed {
                background: #6F88EA;
            }
            """
        )

    def move_to_bottom_right(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 28, available.bottom() - self.height() - 36)

    def load_memory(self) -> Dict[str, Any]:
        # 程序启动时自动读取 memory.json；如果文件不存在或损坏，就创建默认结构。
        if not MEMORY_FILE.exists():
            memory = default_memory()
            self.save_memory(memory)
            return memory

        try:
            with MEMORY_FILE.open("r", encoding="utf-8") as file:
                memory = json.load(file)
        except (json.JSONDecodeError, OSError):
            memory = default_memory()
            self.save_memory(memory)
            return memory

        if not isinstance(memory, dict):
            memory = default_memory()

        memory.setdefault("version", 1)
        memory.setdefault("profile", {})
        memory.setdefault("memories", [])
        return memory

    def save_memory(self, memory: Optional[Dict[str, Any]] = None) -> None:
        # 所有长期记忆都写入项目根目录的 memory.json，不接入数据库。
        data = self.memory if memory is None else memory
        with MEMORY_FILE.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def load_personality(self) -> Dict[str, Any]:
        # V0.4 从 data/roxy_personality.json 读取规则人格，不接入 AI。
        if not PERSONALITY_FILE.exists():
            personality = default_personality()
            self.save_personality(personality)
            return personality

        try:
            with PERSONALITY_FILE.open("r", encoding="utf-8") as file:
                personality = json.load(file)
        except (json.JSONDecodeError, OSError):
            personality = default_personality()
            self.save_personality(personality)
            return personality

        if not isinstance(personality, dict):
            personality = default_personality()

        personality.setdefault("version", 1)
        personality.setdefault("name", "")
        personality.setdefault("personality", "")
        personality.setdefault("likes", "")
        personality.setdefault("speaking_style", "")
        personality.setdefault("rules", [])
        personality.setdefault("fallback_reply", "你好，{nickname}。")
        return personality

    def save_personality(self, personality: Dict[str, Any]) -> None:
        PERSONALITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with PERSONALITY_FILE.open("w", encoding="utf-8") as file:
            json.dump(personality, file, ensure_ascii=False, indent=2)

    def scan_knowledge_files(self) -> List[str]:
        # V0.5 只扫描 data/knowledge 下的 txt 文件名，不读取、不解析文件内容。
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(path.name for path in KNOWLEDGE_DIR.glob("*.txt") if path.is_file())

    def greet_user(self) -> None:
        nickname = self.memory.get("profile", {}).get("nickname")
        if nickname:
            self.add_message("Roxy", f"欢迎回来，{nickname}。")
            self.log_saved_memories()
        else:
            self.add_message("Roxy", "今天也一起加油吧。")

    def log_saved_memories(self) -> None:
        memories = self.memory.get("memories", [])
        if not memories:
            print("[MEMORY] no saved memories", flush=True)
            return

        memory_texts = []
        for item in memories:
            if isinstance(item, dict):
                content = item.get("content", "")
            else:
                content = str(item)
            if content:
                memory_texts.append(content)

        if memory_texts:
            preview = "；".join(memory_texts[:3])
            print(f"[MEMORY] {len(memory_texts)} memories loaded; preview: {preview}", flush=True)
        else:
            print("[MEMORY] no non-empty saved memories", flush=True)

    def send_message(self) -> None:
        # V0.3 只做本地长期记忆，不接入 AI、数据库、网络或语音。
        user_text = self.input_box.text().strip()
        if not user_text:
            return

        self.record_pet_interaction()
        self.add_message("You", user_text)
        self.input_box.clear()

        if self.handle_memory_command(user_text):
            return

        if self.handle_personality_command(user_text):
            return

        if self.handle_knowledge_command(user_text):
            return

        personality_reply = self.match_personality_rule(user_text)
        if personality_reply is not None:
            self.add_message("Roxy", personality_reply)
            return

        if self.save_first_nickname(user_text):
            return

        self.start_ai_reply(user_text)

    def ask_ai(self, user_text: str) -> str:
        messages = self.build_llm_messages(user_text)
        return self.llm_client.chat(messages)

    def start_ai_reply(self, user_text: str) -> None:
        if self.reply_thread is not None and self.reply_thread.isRunning():
            self.add_message("Roxy", "我还在思考上一条消息，请稍等一下。")
            return

        self.start_pet_thinking()
        messages = self.build_llm_messages(user_text)
        self.reply_thread = QThread(self)
        self.reply_worker = ChatReplyWorker(self.llm_client, messages)
        self.reply_worker.moveToThread(self.reply_thread)
        self.reply_thread.started.connect(self.reply_worker.run)
        self.reply_worker.finished.connect(self.finish_ai_reply)
        self.reply_worker.finished.connect(self.reply_thread.quit)
        self.reply_worker.finished.connect(self.reply_worker.deleteLater)
        self.reply_thread.finished.connect(self.reply_thread.deleteLater)
        self.reply_thread.finished.connect(self.clear_reply_thread)
        self.reply_thread.start()

    def finish_ai_reply(self, reply: str) -> None:
        self.stop_pet_thinking()
        self.add_message("Roxy", reply)

    def clear_reply_thread(self) -> None:
        self.reply_thread = None
        self.reply_worker = None

    def record_pet_interaction(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "record_interaction"):
            self.pet_controller.record_interaction()

    def start_pet_thinking(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "start_thinking"):
            self.pet_controller.start_thinking()

    def stop_pet_thinking(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "stop_thinking"):
            self.pet_controller.stop_thinking()

    def build_llm_messages(self, user_text: str) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": self.build_system_prompt(),
            },
            {
                "role": "user",
                "content": user_text,
            },
        ]

    def build_system_prompt(self) -> str:
        memory_context = self.build_memory_context()
        personality_context = self.build_personality_context()
        knowledge_context = self.build_knowledge_context()
        return "\n\n".join(
            [
                personality_context,
                memory_context,
                knowledge_context,
                "请用当前人格回复用户。优先帮助用户学习、健康、项目、赚钱和长期成长。不要声称你接入了数据库或语音。",
            ]
        )

    def build_personality_context(self) -> str:
        return "\n".join(
            [
                "人格配置：",
                f"名称：{self.personality.get('name') or '未设置'}",
                f"性格：{self.personality.get('personality') or '未设置'}",
                f"喜好：{self.personality.get('likes') or '未设置'}",
                f"说话风格：{self.personality.get('speaking_style') or '未设置'}",
            ]
        )

    def build_memory_context(self) -> str:
        profile = self.memory.get("profile", {})
        nickname = profile.get("nickname") or "未设置"
        memories = []
        for item in self.memory.get("memories", []):
            if isinstance(item, dict):
                content = item.get("content", "")
            else:
                content = str(item)
            if content:
                memories.append(content)

        lines = [
            "长期记忆：",
            f"用户昵称：{nickname}",
        ]
        if memories:
            lines.append("记忆内容：")
            for content in memories:
                lines.append(f"- {content}")
        else:
            lines.append("记忆内容：暂无")
        return "\n".join(lines)

    def build_knowledge_context(self) -> str:
        self.knowledge_files = self.scan_knowledge_files()
        if not self.knowledge_files:
            return "知识文件：暂无"

        lines = ["知识文件名称："]
        for file_name in self.knowledge_files:
            lines.append(f"- {file_name}")
        return "\n".join(lines)

    def match_personality_rule(self, user_text: str) -> Optional[str]:
        # 规则人格：精确匹配用户输入，命中后直接返回配置回复。
        normalized_text = user_text.strip()
        rules = self.personality.get("rules", [])
        for rule in rules:
            if not isinstance(rule, dict):
                continue

            inputs = rule.get("inputs", [])
            if normalized_text in inputs:
                return self.render_reply(str(rule.get("reply", "")))

        return None

    def render_reply(self, template: str) -> str:
        nickname = self.memory.get("profile", {}).get("nickname") or "你"
        values = {
            "nickname": nickname,
            "name": str(self.personality.get("name") or "未设置"),
            "personality": str(self.personality.get("personality") or "未设置"),
            "likes": str(self.personality.get("likes") or "未设置"),
            "speaking_style": str(self.personality.get("speaking_style") or "未设置"),
        }
        reply = template
        for key, value in values.items():
            reply = reply.replace("{" + key + "}", value)
        return reply

    def handle_personality_command(self, user_text: str) -> bool:
        if user_text != "查看人格":
            return False

        lines = [
            "当前人格配置：",
            f"名称：{self.personality.get('name') or '未设置'}",
            f"性格：{self.personality.get('personality') or '未设置'}",
            f"喜好：{self.personality.get('likes') or '未设置'}",
            f"说话风格：{self.personality.get('speaking_style') or '未设置'}",
        ]
        self.add_message("Roxy", "\n".join(lines))
        return True

    def handle_knowledge_command(self, user_text: str) -> bool:
        if user_text != "查看知识":
            return False

        self.knowledge_files = self.scan_knowledge_files()
        self.show_knowledge_summary()
        return True

    def show_knowledge_summary(self) -> None:
        if not self.knowledge_files:
            print("[KNOWLEDGE] loaded 0 txt files", flush=True)
            return

        lines = ["已加载知识："]
        for file_name in self.knowledge_files:
            lines.append(f"* {file_name}")
        self.add_message("Roxy", "\n".join(lines))

    def log_knowledge_summary(self) -> None:
        if not self.knowledge_files:
            print("[KNOWLEDGE] loaded 0 txt files", flush=True)
            return

        file_list = ", ".join(self.knowledge_files)
        print(f"[KNOWLEDGE] loaded {len(self.knowledge_files)} txt files: {file_list}", flush=True)

    def handle_memory_command(self, user_text: str) -> bool:
        # 先判断所有记忆命令，再进入普通聊天或首次昵称逻辑。
        if user_text == "我的记忆":
            self.show_all_memories()
            return True

        if self.forget_memory(user_text):
            return True

        # 用户输入“记住：xxxx”后，把 xxxx 保存到 memory.json。
        prefixes = ("记住：", "记住:")
        matched_prefix = None
        for prefix in prefixes:
            if user_text.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            return False

        content = user_text[len(matched_prefix) :].strip()
        if not content:
            self.add_message("Roxy", "可以告诉我要记住什么，例如：记住：我喜欢洛琪希")
            return True

        memories: List[Any] = self.memory.setdefault("memories", [])
        memories.append(
            {
                "content": content,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.save_memory()
        self.add_message("Roxy", f"我记住了：{content}")
        return True

    def show_all_memories(self) -> None:
        memories = self.memory.get("memories", [])
        memory_texts = []
        for item in memories:
            if isinstance(item, dict):
                content = item.get("content", "")
            else:
                content = str(item)
            if content:
                memory_texts.append(content)

        if not memory_texts:
            self.add_message("Roxy", "我现在还没有保存任何长期记忆。")
            return

        lines = ["我现在记得："]
        for index, content in enumerate(memory_texts, start=1):
            lines.append(f"{index}. {content}")
        self.add_message("Roxy", "\n".join(lines))

    def forget_memory(self, user_text: str) -> bool:
        prefixes = ("忘记：", "忘记:")
        matched_prefix = None
        for prefix in prefixes:
            if user_text.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            return False

        content_to_forget = user_text[len(matched_prefix) :].strip()
        if not content_to_forget:
            self.add_message("Roxy", "可以告诉我要忘记什么，例如：忘记：我喜欢洛琪希")
            return True

        memories: List[Any] = self.memory.setdefault("memories", [])
        kept_memories = []
        removed_count = 0

        for item in memories:
            if isinstance(item, dict):
                content = item.get("content", "")
            else:
                content = str(item)

            if content == content_to_forget:
                removed_count += 1
            else:
                kept_memories.append(item)

        if removed_count == 0:
            self.add_message("Roxy", f"我没有找到这条记忆：{content_to_forget}")
            return True

        self.memory["memories"] = kept_memories
        self.save_memory()
        self.add_message("Roxy", f"我已经忘记了：{content_to_forget}")
        return True

    def save_first_nickname(self, user_text: str) -> bool:
        # 如果还没有昵称，把用户第一条普通输入作为昵称保存。
        profile = self.memory.setdefault("profile", {})
        if profile.get("nickname"):
            return False

        nickname = extract_nickname(user_text)
        if not nickname:
            self.add_message("Roxy", "请告诉我你的昵称，例如：我叫煜")
            return True

        profile["nickname"] = nickname
        profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save_memory()
        self.add_message("Roxy", f"我记住你的昵称了，你叫{nickname}。")
        return True

    def default_reply(self) -> str:
        fallback_reply = str(self.personality.get("fallback_reply", "你好，{nickname}。"))
        return self.render_reply(fallback_reply)

    def add_message(self, sender: str, message: str) -> None:
        # 每条消息都显示本地时间戳，方便用户确认交互顺序。
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.transcript.append(self.render_message_html(sender, message, timestamp))
        self.scroll_to_bottom()

    def render_message_html(self, sender: str, message: str, timestamp: str) -> str:
        safe_message = html.escape(message).replace("\n", "<br>")
        safe_time = html.escape(timestamp)
        normalized_sender = sender.strip().lower()

        if normalized_sender in {"you", "user", "你"}:
            return (
                "<div align='right' style='margin: 8px 0;'>"
                "<div style='color:#777777; font-size:11px; margin-bottom:3px;'>"
                f"{safe_time} · 你"
                "</div>"
                "<span style='display:inline-block; background:#F1EAFE; color:#333333; "
                "border:1px solid #D8CCFF; border-radius:14px; padding:8px 11px; "
                "max-width:260px;'>"
                f"{safe_message}"
                "</span>"
                "</div>"
            )

        if normalized_sender in {"system", "系统"}:
            return (
                "<div align='center' style='margin: 7px 0; color:#999999; font-size:11px;'>"
                f"{safe_time} · {safe_message}"
                "</div>"
            )

        return (
            "<div align='left' style='margin: 8px 0;'>"
            "<div style='color:#777777; font-size:11px; margin-bottom:3px;'>"
            f"{safe_time} · Roxy"
            "</div>"
            "<span style='display:inline-block; background:#EAF4FF; color:#333333; "
            "border:1px solid #D8CCFF; border-radius:14px; padding:8px 11px; "
            "max-width:260px;'>"
            f"{safe_message}"
            "</span>"
            "</div>"
        )

    def scroll_to_bottom(self) -> None:
        # 新消息出现后自动滚动到底部。
        scroll_bar = self.transcript.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RoxyPlan")

    print("[PET] using frontend.desktop_pet.DesktopPet", flush=True)
    pet = DesktopPet(chat_factory=lambda pet_controller: ChatWindow(pet_controller=pet_controller))
    pet.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
