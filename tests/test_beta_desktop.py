"""Real desktop services with path injection and a client that cannot network."""

from datetime import date
import json
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import frontend.pet_app as pet_app
from modules.development_log import DevelopmentLog
from modules.llm.contracts import ProviderResponse
from modules.runtime_paths import RuntimePaths, VERSION, read_json_strict, write_run_metadata
from scripts.preview_chat import OfflinePreviewClient


class NoNetworkClient(OfflinePreviewClient):
    def __init__(self, root, *, settings, secret_store, usage_store, **_kwargs):
        super().__init__(root)
        self.project_root = root
        self.secret_store = secret_store
        self.usage_store = usage_store
        self.reconfigure(settings)

    def reconfigure(self, settings, **_kwargs):
        self.settings = dict(settings)

    def chat(self, *_args, **_kwargs):
        raise AssertionError("This focused check must not call a model")


@pytest.fixture
def setup_beta(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    program = tmp_path / "program"
    resources = program / "_internal"
    source = Path(pet_app.__file__).resolve().parents[1]
    shutil.copytree(source / "data/personas", resources / "data/personas")
    (resources / "assets/pet").mkdir(parents=True)
    shutil.copy2(source / "assets/pet/roxy_pet_transparent.png",
                 resources / "assets/pet/roxy_pet_transparent.png")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(pet_app, "RoutedLLMClient", NoNetworkClient)
    runtime = RuntimePaths(resources, program, tmp_path / "user-data")
    pets = []
    def build(context=runtime):
        logger = DevelopmentLog(context.writable("logs/development"), enabled=True)
        pet = pet_app._acceptance_desktop_pet(context.data_root, logger, runtime=context)
        logger.event(None, "run_started")
        pet.runtime_metadata = write_run_metadata(context,
                                                 read_json_strict(context.metadata_file), logger)
        pets.append(pet)
        return pet
    yield app, runtime, build
    for pet in pets:
        if pet.chat_window:
            for window in (pet.chat_window.chat_history_dialog, pet.chat_window.memory_dialog):
                if window is not None:
                    window.close()
        for window in (pet.today_growth_dialog, pet.growth_dialog, pet.settings_dialog,
                       pet.chat_window, pet.bubble):
            if window is not None:
                window.close()
        for timer in pet.findChildren(QTimer):
            timer.stop()
        pet.actions.stop_idle_breathing()
        pet.close()
    app.processEvents()


def test_beta_composition_all_paths_real_date_and_settings_reload(setup_beta):
    app, runtime, build = setup_beta
    pet = build()
    settings = json.loads(runtime.settings_file.read_text(encoding="utf-8"))
    assert settings["test_mode"] is False
    settings["test_mode"] = True
    runtime.settings_file.write_text(json.dumps(settings), encoding="utf-8")
    pet.config = pet._load_config()
    assert pet.config["test_mode"] is False
    pet.apply_saved_settings({"test_mode": True, "study_reminder_seconds_test": 1})
    assert pet._study_reminder_interval_seconds() == 25 * 60
    assert pet._sleep_interval_seconds() == 20 * 60
    assert pet._proactive_interval_seconds() == 10 * 60
    assert pet.growth_service.now_provider().date() == date.today()
    pet.open_chat_window()
    chat = pet.chat_window
    chat.apply_chat_settings({"test_mode": True, "interaction_diagnostics_enabled": False})
    assert chat.pet_settings["test_mode"] is False
    assert chat.conversation_service.interaction_diagnostics.enabled
    pet.open_settings_dialog()
    chat.open_memory_dialog()
    destinations = [pet.config_file, pet.tips_file, pet.knowledge_dir,
                    chat.personality_file, chat.knowledge_dir, chat.legacy_config_file,
                    chat.conversation_service.interaction_diagnostics.persist_path,
                    pet.review_backfill_runner.state_path, pet.review_backfill_runner.diagnostic_path]
    for owner in (pet.growth_service.repository, pet.chat_history_manager.repository,
                  chat.memory_service.repository, pet.secret_store, pet.usage_store):
        destinations.extend(value for value in vars(owner).values() if isinstance(value, Path))
    for path in destinations:
        assert path.resolve().is_relative_to(runtime.data_root)
    assert pet.settings_dialog.config_file == runtime.settings_file
    assert pet.settings_dialog.secret_store is pet.secret_store
    assert chat.memory_dialog.memory_service is chat.memory_service
    assert chat.persona_registry.personas_root == runtime.resource_root / "data/personas"
    assert not pet._load_asset_pixmap().isNull()
    assert pet._asset_path.is_relative_to(runtime.resource_root)
    assert not any(path.name in ("memory.json", "pet_config.json", "config.json")
                   for path in runtime.resource_root.rglob("*"))
    assert runtime.settings_file.read_text(encoding="utf-8").find('"test_mode": true') >= 0


def test_feedback_uses_shared_instance_from_both_settings_entries(setup_beta):
    from modules.feedback_export import SOURCE_REAL

    app, runtime, build = setup_beta
    pet = build()
    pet.open_settings_dialog()
    pet.open_chat_window()
    pet.chat_window.open_settings_dialog()
    for settings in (pet.settings_dialog, pet.chat_window.settings_dialog):
        service = settings.feedback_export
        assert service.available
        assert service.data_root == runtime.data_root
        assert service.run_meta.instance_id == pet.runtime_metadata["instance_id"]
        assert service.run_meta.version == runtime.version
        assert service.run_meta.source == SOURCE_REAL


def test_restart_and_move_keep_records_instance_and_daily_session(setup_beta):
    app, runtime, build = setup_beta
    pet = build()
    task = pet.growth_service.add_task("Beta 隔离计划")
    pet.growth_service.complete_by_id(task["id"])
    pet.growth_service.add_record("Beta 隔离行动", duration_minutes=12)
    pet.growth_service.save_today_review()
    pet.open_chat_window()
    chat = pet.chat_window
    chat.memory_service.memory_manager.add_memory("Beta 隔离记忆", category="preference")
    chat.add_message("You", "隔离聊天记录")
    session = chat.current_session_id
    pet.secret_store.save_deepseek_key("synthetic-beta-key")
    pet.usage_store.record(ProviderResponse(provider="deepseek", model="deepseek-chat"),
                           route_reason="isolated fixture")
    (runtime.writable("data/knowledge") / "synthetic.md").write_text("隔离知识", encoding="utf-8")
    original = {p.relative_to(runtime.data_root): p.read_bytes()
                for p in runtime.data_root.rglob("*.json") if "logs" not in p.parts}
    moved_program = runtime.program_root.with_name("moved-program")
    shutil.copytree(runtime.resource_root, moved_program / "_internal")
    moved = RuntimePaths(moved_program / "_internal", moved_program, runtime.data_root)
    restarted = build(moved)
    restarted.open_chat_window()
    assert restarted.chat_window.current_session_id == session
    assert restarted.growth_service.tasks()[0]["uid"] == task["uid"]
    assert restarted.growth_service.tasks()[0]["done"]
    assert restarted.growth_service.records_for_date()[0]["duration_minutes"] == 12
    assert restarted.chat_window.memory_manager.search("Beta 隔离记忆")
    assert restarted.chat_window.knowledge_files
    assert restarted.runtime_metadata["instance_id"] == pet.runtime_metadata["instance_id"]
    assert restarted.runtime_metadata["run_id"] != pet.runtime_metadata["run_id"]
    assert restarted.runtime_metadata["version"] == VERSION
    assert restarted.runtime_metadata["data_source"] == "real"
    # Reopening does not replace business/config/credential documents.
    for name, content in original.items():
        assert (runtime.data_root / name).read_bytes() == content


def test_beta_thin_entry_logs_startup_errors_without_showing_qt(tmp_path, monkeypatch):
    import roxy_main
    program = tmp_path / "install"
    runtime = RuntimePaths(program, program, tmp_path / "user")
    monkeypatch.setattr(roxy_main, "resolve_runtime", lambda *_args, **_kwargs: runtime)
    def fail(**_kwargs):
        raise RuntimeError("synthetic secret should not be logged")
    monkeypatch.setattr(pet_app, "main", fail)
    messages = []
    monkeypatch.setattr(roxy_main, "show_startup_error", lambda error, path: messages.append(path))
    assert roxy_main.main([]) == 1
    assert messages[0].is_relative_to(runtime.logs_dir)
    assert "synthetic secret" not in messages[0].read_text(encoding="utf-8")


def test_beta_main_refuses_developer_args_and_failed_required_log(tmp_path, monkeypatch):
    program = tmp_path / "program"
    runtime = RuntimePaths(program, program, tmp_path / "user")
    with pytest.raises(ValueError):
        pet_app.main(runtime=runtime, argv=["--acceptance-root", str(tmp_path / "engineering")])
    assert not runtime.data_root.exists()
    monkeypatch.setattr(pet_app, "configure_development_log",
                        lambda *_args, **_kwargs: DevelopmentLog(enabled=False))
    with pytest.raises(OSError, match="diagnostic"):
        pet_app.main(runtime=runtime, argv=[])
