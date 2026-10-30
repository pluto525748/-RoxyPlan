"""Packaged date-advancing user observation; only runner-owned instance data."""
from datetime import date, timedelta
import json
import sys
import zipfile

import pytest

from modules import runtime_paths as paths
from modules.simulation_clock import SimulatedBusinessClock


def study(tmp_path):
    return paths.resolve_runtime('study', ['--data-root', str(tmp_path/'instance')],
                                 resource_root=tmp_path/'resources', program_root=tmp_path/'program')


def test_frozen_study_is_explicit_and_regular_beta_remains_restricted(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    chosen = study(tmp_path)
    assert chosen.is_packaged and chosen.is_study and not chosen.is_beta
    assert chosen.data_source == 'engineering' and chosen.version == paths.STUDY_VERSION
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.resolve_runtime('developer', ['--simulate-days'])
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.RuntimePaths(tmp_path/'r', tmp_path/'p', tmp_path/'u', 'developer')


@pytest.mark.parametrize('args', [['--simulate-days'], ['--start-date','2030-01-01'], ['--data-root','relative'], ['--mode','developer']])
def test_study_cannot_open_arbitrary_development_switches(tmp_path,args):
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.resolve_runtime('study', args, resource_root=tmp_path/'r', program_root=tmp_path/'p')


def test_study_separate_default_and_no_data_in_program(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    runtime=paths.resolve_runtime('study', resource_root=tmp_path/'r', program_root=tmp_path/'p')
    assert runtime.data_root == tmp_path/'local/RoxyPlanTestInstances/user-study'
    with pytest.raises(paths.RuntimeConfigurationError, match='separate'):
        paths.resolve_runtime('study', ['--data-root', str(tmp_path/'p/user')],
                              resource_root=tmp_path/'r',program_root=tmp_path/'p')


def test_study_reopens_same_clock_and_refuses_real_data(tmp_path):
    from frontend.pet_app import _initialize_desktop_runtime
    runtime=study(tmp_path)
    clock=SimulatedBusinessClock(runtime.data_root,'2026-10-28')
    first=_initialize_desktop_runtime(runtime)
    assert clock.advance_one_day() == '2026-10-29'
    assert SimulatedBusinessClock(runtime.data_root).today() == '2026-10-29'
    assert _initialize_desktop_runtime(runtime)['instance_id'] == first['instance_id']
    runtime.settings_file.write_text('{"test_mode":true,"unknown_kept":17}',encoding='utf-8')
    _initialize_desktop_runtime(runtime)
    assert json.loads(runtime.settings_file.read_text(encoding='utf-8'))['unknown_kept'] == 17
    assert not runtime.effective_settings({'test_mode':True})['test_mode']
    beta=paths.RuntimePaths(runtime.resource_root,runtime.program_root,runtime.data_root)
    with pytest.raises(paths.RuntimeConfigurationError,match='simulated'):
        paths.initialize_runtime(beta,{})


def test_study_corrupt_record_is_preserved(tmp_path):
    from frontend.pet_app import _initialize_desktop_runtime
    runtime=study(tmp_path)
    SimulatedBusinessClock(runtime.data_root,'2026-10-28')
    path=runtime.writable('memory.json');path.write_text('broken',encoding='utf-8')
    with pytest.raises(paths.RuntimeConfigurationError):
        _initialize_desktop_runtime(runtime)
    assert path.read_text(encoding='utf-8') == 'broken'


def test_study_feedback_retains_manual_date_provenance_and_excludes_key(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    from frontend.pet_app import _initialize_desktop_runtime
    from modules.feedback_export import feedback_export_for_runtime, ExportScope, FeedbackText, SOURCE_STUDY
    runtime=study(tmp_path)
    SimulatedBusinessClock(runtime.data_root,'2026-10-28')
    _initialize_desktop_runtime(runtime)
    runtime.writable('data/private/llm_secrets.json').write_text('{"api_key":"synthetic-private-study-value"}',encoding='utf-8')
    from modules.growth_manager import GrowthManager
    GrowthManager(runtime.private_dir).add_record('整理了一张学习卡片',date='2026-10-28',duration_minutes=8)
    service=feedback_export_for_runtime(runtime)
    scope=ExportScope(date(2026,10,1),date(2026,11,1),include_chat=True,include_memory=True)
    dest=tmp_path/'feedback.zip'
    assert service.export(service.plan(scope),FeedbackText('体验','轻松使用','记录反馈'),dest).ok
    with zipfile.ZipFile(dest) as z:
        assert json.loads(z.read('feedback.json'))['source'] == SOURCE_STUDY
        assert '日期可手动推进' in z.read('README.txt').decode('utf-8')
        assert not any(b'synthetic-private-study-value' in z.read(n) for n in z.namelist())


def test_study_chat_hides_marker_advances_actual_business_clock_and_wires_feedback(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    from PySide6.QtWidgets import QApplication
    from frontend.pet_app import _acceptance_desktop_pet
    from modules.development_log import configure_development_log
    app=QApplication.instance() or QApplication([])
    runtime=study(tmp_path)
    clock=SimulatedBusinessClock(runtime.data_root,'2026-10-28')
    pet=_acceptance_desktop_pet(runtime.data_root,configure_development_log(runtime.logs_dir/'development'),clock,runtime=runtime)
    try:
        pet.open_chat_window();app.processEvents()
        chat=pet.chat_window
        assert chat.simulation_date_label.isHidden()
        assert chat.advance_day_button.text() == '推进一天'
        assert chat.advance_simulation_day()
        assert clock.today() == '2026-10-29'
        chat.open_settings_dialog();app.processEvents()
        assert chat.settings_dialog is not None
        from modules.feedback_export import feedback_export_for_runtime,SOURCE_STUDY
        assert feedback_export_for_runtime(runtime).run_meta.source == SOURCE_STUDY
    finally:
        if pet.chat_window is not None:
            if pet.chat_window.settings_dialog is not None:pet.chat_window.settings_dialog.close()
            pet.chat_window.close()
        for timer in pet.findChildren(__import__('PySide6.QtCore',fromlist=['QTimer']).QTimer):timer.stop()
        pet.close();pet.deleteLater();app.processEvents()


def test_study_feedback_default_range_includes_future_business_and_real_dates(tmp_path):
    from frontend.pet_app import _initialize_desktop_runtime
    from modules.feedback_export import feedback_export_for_runtime
    from modules.growth_manager import GrowthManager
    runtime=study(tmp_path)
    future=date.today()+timedelta(days=21)
    SimulatedBusinessClock(runtime.data_root,future.isoformat())
    _initialize_desktop_runtime(runtime)
    GrowthManager(runtime.private_dir).add_record('完成卡片',date=future.isoformat(),duration_minutes=8)
    service=feedback_export_for_runtime(runtime)
    scope=service.suggested_scope()
    assert scope.covers(future) and scope.covers(date.today())
    assert any(e.archive_path.startswith('growth/') for e in service.plan(scope).included)
