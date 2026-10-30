"""Sleep is quiet until explicit user interaction; all paths are isolated."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import frontend.desktop_pet as desktop_pet
from modules.chat_history_manager import ChatHistoryManager
from modules.contracts import ClientAction
from modules.development_log import DevelopmentLog
from modules.growth_manager import GrowthManager
from modules.runtime_paths import RuntimePaths
import modules.review_backfill as review_backfill


SOURCE = Path(__file__).resolve().parents[1]


def runtime_source(tmp_path, monkeypatch):
    if os.environ.get("ROXYPLAN_SLEEP_BASELINE") != "1":
        return desktop_pet
    evidence = SOURCE / "test_workspace/runs/pet-sleep-quiet-20261018/artifacts"
    baseline = json.loads((evidence / "baseline.json").read_text(encoding="utf-8"))
    hashes = {}
    modules = []
    for stem in ("pet_action_manager", "desktop_pet"):
        name = f"frontend/{stem}.py"
        path = evidence / "before" / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert hashes[name] == baseline["hashes"][name]
        spec = importlib.util.spec_from_file_location(f"sleep_baseline_{stem}", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    # Bind the captured baseline manager, never the edited production class.
    modules[1].PetActionManager = modules[0].PetActionManager
    (tmp_path / "baseline-source.json").write_text(json.dumps({
        "head": baseline["head"], "source_sha256": hashes,
    }, indent=2), encoding="utf-8")
    return modules[1]


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    assert instance.platformName() == "offscreen"
    return instance


@pytest.fixture
def quiet_pet(app, tmp_path, monkeypatch):
    runtime_module = runtime_source(tmp_path, monkeypatch)
    root = tmp_path / "isolated"
    private = root / "data/private"
    private.mkdir(parents=True)
    config_file = root / "data/pet_config.json"
    config_file.write_text(json.dumps({
        "test_mode": False, "pet_position": "custom", "use_pet_asset": True,
        "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
        "auto_tips_enabled": True, "auto_tips_min_minutes": 5, "auto_tips_max_minutes": 5,
        "auto_complete_yesterday_review": False, "proactive_enabled": True,
    }), encoding="utf-8")
    tips_file = root / "data/pet_tips.json"
    tips_file.write_text(json.dumps(["合成随机提示", "合成学习提示"]), encoding="utf-8")
    clock = {"now": 10000.0}
    # Replace this module's clock reference, not Python's shared time module.
    monkeypatch.setattr(runtime_module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    pet = runtime_module.DesktopPet(
        config_file=config_file, tips_file=tips_file,
        runtime=RuntimePaths(SOURCE, SOURCE, root, mode="developer", data_source="engineering"),
        growth_service=GrowthManager(private), chat_history_manager=ChatHistoryManager(private),
        knowledge_dir=root / "data/knowledge",
    )
    bubbles, chat_messages, checks, progress_checks = [], [], [], []
    monkeypatch.setattr(pet.bubble, "show_message", lambda text, *_args: bubbles.append(text))
    chat_file = root / "chat-callback.json"
    chat_file.write_text("[]", encoding="utf-8")

    def add_message(role, text):
        chat_messages.append((role, text))
        chat_file.write_text(json.dumps(chat_messages), encoding="utf-8")

    pet.chat_window = SimpleNamespace(
        current_session_id="", isVisible=lambda: True, add_message=add_message,
    )

    def proactive_check(**kwargs):
        checks.append(kwargs)
        if not pet.proactive_manager.enabled or pet.proactive_manager.paused:
            return None
        return {"type": "idle_nudge", "text": "合成主动提醒"}

    def progress_check():
        progress_checks.append(True)
        return {"type": "progress_encourage", "text": "合成完成鼓励"}

    monkeypatch.setattr(pet.proactive_manager, "check", proactive_check)
    monkeypatch.setattr(pet.proactive_manager, "task_completed_reminder", progress_check)
    result = SimpleNamespace(
        pet=pet, clock=clock, bubbles=bubbles, chat_messages=chat_messages,
        checks=checks, progress_checks=progress_checks, chat_file=chat_file,
        config_file=config_file, config_bytes=config_file.read_bytes(),
    )
    yield result
    pet.chat_window = None  # The callback spy is not an owned QWidget.
    pet.close()
    assert all(not timer.isActive() for timer in pet.findChildren(QTimer))


def enter_sleep(context, method):
    pet = context.pet
    if method == "manual":
        result = pet.client_action_dispatcher.dispatch(ClientAction("sleep", source="desktop_menu"))
        assert result.accepted
    elif method == "agent":
        assert pet.agent_sleep()
    else:
        context.clock["now"] += pet._sleep_interval_seconds() + 1
        pet._check_sleep_timeout()
    assert pet.action_manager.current_state == "sleeping" and pet.state == "sleep"


def automatic_callbacks(context):
    pet = context.pet
    pet._show_auto_tip()
    pet._trigger_study_reminder()
    pet._check_proactive_reminder()
    pet._on_plan_completed()
    pet._show_review_backfill_warning("合成后台警告")
    # Also cover a reminder already produced before sleep and delivered late.
    pet._present_proactive_reminder({"type": "idle_nudge", "text": "合成延迟提醒"})


def test_sleep_auto_tip_does_not_show_another_bubble(quiet_pet):
    enter_sleep(quiet_pet, "manual")
    confirmation = list(quiet_pet.bubbles)
    quiet_pet.pet._show_auto_tip()
    assert quiet_pet.bubbles == confirmation
    assert quiet_pet.pet.action_manager.current_state == "sleeping"


def test_sleep_proactive_check_never_wakes_or_writes_chat(quiet_pet):
    enter_sleep(quiet_pet, "manual")
    quiet_pet.pet._check_proactive_reminder()
    assert quiet_pet.pet.action_manager.current_state == "sleeping"
    assert quiet_pet.checks == []
    assert quiet_pet.chat_messages == []


@pytest.mark.parametrize("method", ["manual", "timeout", "agent"])
def test_sleep_entry_silences_all_automatic_reminders_without_chat_writes(quiet_pet, method):
    context, pet = quiet_pet, quiet_pet.pet
    enter_sleep(context, method)
    confirmation = list(context.bubbles)
    assert len(confirmation) == 1
    assert pet._is_sleeping is True
    pet.auto_tip_timer.stop()
    for _ in range(3):
        automatic_callbacks(context)
    assert context.bubbles == confirmation
    assert context.chat_messages == [] and context.chat_file.read_text(encoding="utf-8") == "[]"
    assert context.checks == [] and context.progress_checks == []
    assert pet.is_sleeping and pet.action_manager.current_state == "sleeping"
    assert pet.auto_tip_timer.isActive()  # A future wake must still have scheduling.
    assert pet.study_reminder_timer.isActive() and pet.proactive_timer.isActive()
    assert context.config_file.read_bytes() == context.config_bytes


@pytest.mark.parametrize("method", ["manual", "timeout"])
def test_repeated_sleep_and_timeout_ticks_do_not_repeat_confirmation_or_pose(quiet_pet, method):
    context, pet = quiet_pet, quiet_pet.pet
    enter_sleep(context, method)
    pose_animation = pet.actions._transient_group
    for _ in range(5):
        context.clock["now"] += pet._sleep_interval_seconds() + 1
        pet._check_sleep_timeout()
        assert pet.agent_sleep()
    assert len(context.bubbles) == 1
    assert pet.actions._transient_group is pose_animation
    assert pet._is_sleeping and pet.is_sleeping


@pytest.mark.parametrize("representation", ["flag", "visual", "manager"])
def test_legacy_sleep_representations_are_quiet_before_any_reminder(quiet_pet, representation):
    context, pet = quiet_pet, quiet_pet.pet
    # Simulate the pre-fix representations without relying on the new synchronizer.
    if representation == "flag":
        pet._is_sleeping = True
    elif representation == "visual":
        pet._state = "sleep"
    else:
        pet.action_manager.set_state("sleeping")
    context.clock["now"] += pet._sleep_interval_seconds() + 1
    automatic_callbacks(context)
    pet._check_sleep_timeout()
    assert context.bubbles == [] and context.chat_messages == []
    assert context.checks == [] and context.progress_checks == []
    assert pet.is_sleeping


@pytest.mark.parametrize("method", ["manual", "timeout"])
@pytest.mark.parametrize("wake", ["explicit", "interaction"])
def test_user_wake_refreshes_timeout_and_restores_existing_reminders(quiet_pet, method, wake):
    context, pet = quiet_pet, quiet_pet.pet
    enter_sleep(context, method)
    context.clock["now"] += pet._sleep_interval_seconds() + 1
    if wake == "explicit":
        pet.wake()
    else:
        assert pet.record_interaction()
    assert pet._last_interaction_at == context.clock["now"]
    assert not pet.is_sleeping and not pet._is_sleeping
    pet._check_sleep_timeout()
    assert pet.action_manager.current_state == "idle" and pet.state == "idle"
    context.bubbles.clear()
    pet._show_auto_tip()
    assert len(context.bubbles) == 1
    pet.action_manager._finish_transient()  # Complete the bubble's ordinary nod.
    pet._trigger_study_reminder()
    assert pet.action_manager.current_state == "reminding"
    assert len(context.bubbles) == 2
    for _ in range(6):
        pet.sprite_player.advance()
    pet.action_manager._finish_transient()
    context.clock["now"] += 121
    pet._check_proactive_reminder()
    assert len(context.checks) == 1
    assert context.chat_messages == [("Roxy", "合成主动提醒")]
    assert len(context.bubbles) == 3
    assert context.config_file.read_bytes() == context.config_bytes


def test_direct_study_reminder_does_not_wake_sleeping_pet(quiet_pet):
    enter_sleep(quiet_pet, "manual")
    pet = quiet_pet.pet
    assert pet.action_manager.try_play_action("study_reminder") == (False, "sleeping")
    assert pet.is_sleeping and len(quiet_pet.bubbles) == 1


def test_rejected_timeout_request_does_not_claim_sleep_was_entered(quiet_pet):
    context, pet = quiet_pet, quiet_pet.pet
    pet.action_manager.pause_timers("drag")
    context.clock["now"] += pet._sleep_interval_seconds() + 1
    pet._check_sleep_timeout()
    assert not pet.is_sleeping and not pet._is_sleeping
    assert context.bubbles == []
    pet.action_manager.resume_timers("drag")
    pet._check_sleep_timeout()
    assert pet.is_sleeping and len(context.bubbles) == 1


def test_sleep_wake_does_not_enable_disabled_reminders(quiet_pet):
    context, pet = quiet_pet, quiet_pet.pet
    pet.config.update(auto_tips_enabled=False, proactive_enabled=False)
    pet._restart_auto_tip_timer()
    pet._start_proactive_timer()
    enter_sleep(context, "manual")
    pet.wake()
    context.bubbles.clear()
    pet._show_auto_tip()
    pet._check_proactive_reminder()
    assert context.bubbles == [] and context.chat_messages == []
    assert not pet.auto_tip_timer.isActive() and not pet.proactive_timer.isActive()
    assert pet.config["auto_tips_enabled"] is False and pet.config["proactive_enabled"] is False


def test_sleep_wake_keeps_independent_chat_pause_until_its_owner_resumes(quiet_pet):
    context, pet = quiet_pet, quiet_pet.pet
    pet.pause_proactive_reminders()
    enter_sleep(context, "manual")
    pet.wake()
    assert pet.proactive_manager.paused
    pet._check_proactive_reminder()
    assert context.chat_messages == []
    pet.resume_proactive_reminders()
    pet._check_proactive_reminder()
    assert context.chat_messages == [("Roxy", "合成主动提醒")]


def test_sleep_suppresses_backfill_ui_but_preserves_original_diagnostics(quiet_pet, tmp_path, monkeypatch, capsys):
    enter_sleep(quiet_pet, "manual")
    logger = DevelopmentLog(tmp_path / "logs", enabled=True)
    monkeypatch.setattr(review_backfill, "get_development_log", lambda: logger)
    runner = quiet_pet.pet.review_backfill_runner

    def fail_validation():
        raise RuntimeError("synthetic_backfill_failure")

    monkeypatch.setattr(runner, "_validate_growth_documents", fail_validation)
    runner.run()
    result = runner.run()  # Original threshold sends the second failure to UI.
    assert result["status"] == "failed" and result["notified"]
    rows = [json.loads(line) for line in runner.diagnostic_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[-1]["error_type"] == "RuntimeError"
    assert rows[-1]["consecutive_failures"] == 2
    assert any("startup_backfill_finished" in path.read_text(encoding="utf-8")
               for path in (tmp_path / "logs").rglob("*.jsonl"))
    assert len(quiet_pet.bubbles) == 1 and quiet_pet.chat_messages == []
    assert "UI notification suppressed: sleeping" in capsys.readouterr().out
