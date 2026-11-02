"""Beta path and bootstrap checks; all destinations are runner-owned fixtures."""

import json
import os
from pathlib import Path
import sys

import pytest

from modules import runtime_paths as paths


def beta(tmp_path, root=None, install="program"):
    program = tmp_path / install
    return paths.RuntimePaths(program / "_internal", program, root or tmp_path / "user")


def defaults():
    return {"data/pet_config.json": {"test_mode": False},
            "data/pet_tips.json": [], "data/roxy_personality.json": {}, "config.json": {}}


def test_default_and_custom_roots_stay_stable_when_program_and_cwd_move(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ROXYPLAN_MODE", "developer")
    install = tmp_path / "old-install"
    initial = paths.resolve_runtime("beta", resource_root=install, program_root=install)
    assert initial.data_root == tmp_path / "local/RoxyPlan"
    monkeypatch.chdir(tmp_path)
    moved = paths.resolve_runtime("beta", resource_root=tmp_path / "new-install",
                                  program_root=tmp_path / "new-install")
    assert moved.data_root == initial.data_root
    chosen = paths.resolve_runtime("beta", ["--data-root", str(tmp_path / "separate")],
                                   resource_root=install, program_root=install)
    assert chosen.mode == "beta" and chosen.data_root == tmp_path / "separate"
    assert paths.resolve_runtime("developer").mode == "developer"


@pytest.mark.parametrize("args", [
    ["--mode", "developer"], ["--developer"], ["--simulate-days"],
    ["--acceptance-root", "C:/acceptance"], ["--start-date", "2026-10-01"],
    ["--no-development-log"], ["--open-chat"], ["--data-root"],
    ["--data-root", "relative"],
])
def test_beta_rejects_developer_and_ambiguous_arguments(tmp_path, args):
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.resolve_runtime("beta", args, resource_root=tmp_path / "program",
                              program_root=tmp_path / "program")


def test_frozen_source_entry_cannot_return_to_developer(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "package/_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "package/RoxyPlan.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ROXYPLAN_MODE", "developer")
    runtime = paths.resolve_runtime("developer")
    assert runtime.is_beta
    assert runtime.resource_root == tmp_path / "package/_internal"
    assert runtime.program_root == tmp_path / "package"
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.resolve_runtime("developer", ["--simulate-days"])
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.RuntimePaths(runtime.resource_root, runtime.program_root, runtime.data_root, "developer")


@pytest.mark.parametrize("destination", ["program", "program/user", "program/_internal/user", "."])
def test_data_root_rejects_program_resource_children_and_parents(tmp_path, destination):
    with pytest.raises(paths.RuntimeConfigurationError):
        beta(tmp_path, tmp_path / destination)


def test_clean_initialization_preserves_existing_files_and_instance(tmp_path):
    runtime = beta(tmp_path)
    first = paths.initialize_runtime(runtime, defaults())
    settings = runtime.settings_file
    settings.write_text('{"test_mode":true,"pet_scale":1.2}', encoding="utf-8")
    original = settings.read_bytes()
    second = paths.initialize_runtime(beta(tmp_path, install="moved-program"), defaults())
    assert first == second and settings.read_bytes() == original
    effective = runtime.effective_settings(json.loads(original))
    assert effective["test_mode"] is False
    assert effective["interaction_diagnostics_enabled"] is True
    assert effective["pet_scale"] == 1.2
    other = paths.initialize_runtime(beta(tmp_path, tmp_path / "other-user"), defaults())
    assert other["instance_id"] != first["instance_id"]
    assert not (runtime.resource_root / "data").exists()


@pytest.mark.parametrize("filename", ["data/pet_config.json", "config.json", "data/pet_tips.json",
                                      "data/roxy_personality.json", "memory.json",
                                      "data/private/today_plan.json"])
def test_corrupt_files_stop_before_defaults_are_written(tmp_path, filename):
    runtime = beta(tmp_path)
    target = runtime.writable(filename)
    target.parent.mkdir(parents=True)
    target.write_text("broken private value", encoding="utf-8")
    before = {p.relative_to(runtime.data_root): p.read_bytes()
              for p in runtime.data_root.rglob("*") if p.is_file()}
    with pytest.raises(paths.RuntimeConfigurationError):
        paths.initialize_runtime(runtime, defaults())
    assert before == {p.relative_to(runtime.data_root): p.read_bytes()
                      for p in runtime.data_root.rglob("*") if p.is_file()}


def test_unwritable_destination_stops_bootstrap(tmp_path, monkeypatch):
    runtime = beta(tmp_path)
    def deny_probe(*_args, **_kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(paths.tempfile, "TemporaryFile", deny_probe)
    with pytest.raises(PermissionError):
        paths.initialize_runtime(runtime, defaults())
    assert not runtime.settings_file.exists()


def test_engineering_root_cannot_be_reused_as_real_beta_data(tmp_path):
    runtime = beta(tmp_path)
    developer = paths.RuntimePaths(runtime.resource_root, runtime.program_root,
                                   runtime.data_root, "developer", data_source="engineering")
    paths.initialize_runtime(developer, {})
    with pytest.raises(paths.RuntimeConfigurationError, match="cannot be combined"):
        paths.initialize_runtime(runtime, defaults())
    assert not runtime.settings_file.exists()


def test_source_is_independent_of_developer_mode(tmp_path):
    runtime = beta(tmp_path)
    developer = paths.RuntimePaths(runtime.resource_root, runtime.program_root,
                                   runtime.data_root, "developer")
    assert paths.initialize_runtime(developer, {})["data_source"] == "real"
    assert paths.initialize_runtime(runtime, defaults())["data_source"] == "real"
    with pytest.raises(paths.RuntimeConfigurationError, match="engineering"):
        paths.RuntimePaths(runtime.resource_root, runtime.program_root,
                           tmp_path / "engineering", data_source="engineering")


def test_beta_refuses_old_simulation_marker_and_relative_escape(tmp_path):
    runtime = beta(tmp_path)
    runtime.data_root.mkdir()
    runtime.writable("simulation_state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(paths.RuntimeConfigurationError, match="simulated"):
        paths.initialize_runtime(runtime, defaults())
    assert not runtime.settings_file.exists()
    with pytest.raises(paths.RuntimeConfigurationError, match="escapes"):
        runtime.writable("../outside/config.json")


@pytest.mark.skipif(os.name != "nt", reason="Checks Windows file read-only attribute")
def test_readonly_existing_config_is_preserved(tmp_path):
    runtime = beta(tmp_path)
    runtime.settings_file.parent.mkdir(parents=True)
    runtime.settings_file.write_text('{"test_mode":true}', encoding="utf-8")
    before = runtime.settings_file.read_bytes()
    old_mode = runtime.settings_file.stat().st_mode
    try:
        runtime.settings_file.chmod(0o444)
        with pytest.raises(PermissionError):
            paths.initialize_runtime(runtime, defaults())
        assert runtime.settings_file.read_bytes() == before
    finally:
        runtime.settings_file.chmod(old_mode)


def test_linked_data_subdirectory_cannot_escape_root(tmp_path):
    runtime = beta(tmp_path)
    runtime.data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (runtime.data_root / "data").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create directory symlinks")
    with pytest.raises(paths.RuntimeConfigurationError, match="escapes"):
        paths.initialize_runtime(runtime, defaults())
    assert not list(outside.iterdir())


def test_windowed_failure_log_has_locations_without_private_exception_values(tmp_path, monkeypatch):
    runtime = beta(tmp_path)
    private = "synthetic-secret-for-redaction-check"
    try:
        raise RuntimeError(private)
    except RuntimeError as error:
        log = paths.report_startup_failure(runtime, error)
    assert log.is_relative_to(runtime.logs_dir)
    record = json.loads(log.read_text(encoding="utf-8"))
    assert record["error_type"] == "RuntimeError"
    assert record["version"] == paths.VERSION
    assert record["frames"] and private not in log.read_text(encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", None)
        paths.ensure_windowed_streams()
        print(private, flush=True)
        assert not hasattr(sys.stdout, "getvalue")
