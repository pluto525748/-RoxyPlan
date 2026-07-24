import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from frontend.settings_dialog import SettingsDialog
from modules.llm.contracts import ProviderResponse
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore


def test_settings_dialog_masks_key_and_does_not_write_it_to_config():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config_file = root / "pet_config.json"
        config_file.write_text(
            json.dumps({"pet_asset_path": "asset.png", "model_name": "qwen3:4b"}),
            encoding="utf-8",
        )
        secrets = SecretStore(root)
        assert secrets.save_deepseek_key("private-secret-value")
        dialog = SettingsDialog(
            config_file=config_file,
            secret_store=secrets,
            usage_store=ModelUsageStore(root),
        )
        assert dialog.api_key.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.api_key.text() == ""
        assert "private-secret-value" not in dialog.api_key_status.text()
        assert "deepseek_api_key" not in config_file.read_text(encoding="utf-8")
        dialog.close()
    app.processEvents()


def test_usage_store_records_metrics_but_not_prompts():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = ModelUsageStore(root)
        response = ProviderResponse(
            "deepseek",
            "fake-model",
            content="private reply",
            usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            latency_ms=120,
        )
        store.record(response, route_reason="online_default")
        raw = store.path.read_text(encoding="utf-8")
        summary = store.summary()
        assert summary["totals"]["deepseek:fake-model"]["total_tokens"] == 14
        assert "private reply" not in raw
        assert "prompt" not in raw.lower()


def test_env_example_contains_only_placeholder():
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=your_api_key_here" in content
    assert "sk-" not in content


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"model settings tests passed ({len(TESTS)} cases)")
