"""Small, stdlib-only startup contract shared by source, Beta and packaging.

No mode environment variable is used. Frozen entries explicitly select Beta
or the separately built date-advancing study edition.
Resource files are never used as defaults for user records or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from uuid import UUID, uuid4


VERSION = "0.9.0-aic2026-rc.1"
STUDY_VERSION = "0.9.0-aic2026-rc.1-study"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILES = {
    "data/pet_config.json": dict,
    "data/pet_tips.json": list,
    "data/roxy_personality.json": dict,
    "config.json": dict,
}
STORE_FILES = (
    "memory.json", "data/private/today_plan.json", "data/private/action_log.json",
    "data/private/growth_log.json", "data/private/chat_history.json",
    "data/private/chat_summaries.json", "data/private/memory_candidates.json",
    "data/private/memory_conflicts.json", "data/private/memory_audit.json",
    "data/private/llm_secrets.json", "data/private/model_usage.json",
    "data/private/diagnostics/review_backfill_state.json",
)


class RuntimeConfigurationError(ValueError):
    """A chosen path/config cannot be safely used; existing data is preserved."""


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


@dataclass(frozen=True)
class RuntimePaths:
    resource_root: Path
    program_root: Path
    data_root: Path
    mode: str = "beta"
    version: str = VERSION
    data_source: str = "real"

    def __post_init__(self) -> None:
        if self.mode not in {"developer", "beta", "study"}:
            raise RuntimeConfigurationError("Unknown runtime mode")
        if self.data_source not in {"real", "engineering"}:
            raise RuntimeConfigurationError("Unknown data source")
        if self.is_beta and self.data_source != "real":
            raise RuntimeConfigurationError("Beta cannot use engineering data")
        if self.is_study and self.data_source != "engineering":
            raise RuntimeConfigurationError("Date-advancing study requires isolated engineering data")
        if getattr(sys, "frozen", False) and not self.is_packaged:
            raise RuntimeConfigurationError("Frozen applications require Beta mode")
        for name in ("resource_root", "program_root", "data_root"):
            value = Path(getattr(self, name))
            if not value.is_absolute():
                raise RuntimeConfigurationError(f"{name} must be absolute")
            object.__setattr__(self, name, value.resolve())
        if self.is_packaged:
            for root in (self.resource_root, self.program_root):
                if _inside(self.data_root, root) or _inside(root, self.data_root):
                    raise RuntimeConfigurationError("Data and program/resources must be separate")

    @property
    def is_beta(self) -> bool:
        return self.mode == "beta"

    @property
    def is_study(self) -> bool:
        return self.mode == "study"

    @property
    def is_packaged(self) -> bool:
        return self.is_beta or self.is_study

    def writable(self, relative: str) -> Path:
        path = self.data_root / relative
        if not _inside(path.resolve(), self.data_root):
            raise RuntimeConfigurationError("Writable path escapes the data root")
        return path

    @property
    def private_dir(self) -> Path:
        return self.writable("data/private")

    @property
    def settings_file(self) -> Path:
        return self.writable("data/pet_config.json")

    @property
    def logs_dir(self) -> Path:
        return self.writable("logs")

    @property
    def metadata_file(self) -> Path:
        return self.writable("runtime_metadata.json")

    def effective_settings(self, settings: dict) -> dict:
        result = dict(settings)
        if self.is_packaged:
            result["test_mode"] = False
            result["interaction_diagnostics_enabled"] = True
        return result


def default_beta_data_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    if not local.is_absolute():
        raise RuntimeConfigurationError("LOCALAPPDATA must be absolute")
    return local / "RoxyPlan"


def resolve_runtime(entry_mode: str, argv=(), *, resource_root=None,
                    program_root=None) -> RuntimePaths:
    frozen = bool(getattr(sys, "frozen", False))
    mode = ("study" if entry_mode == "study" else "beta") if frozen else entry_mode
    resources = Path(resource_root or (getattr(sys, "_MEIPASS", SOURCE_ROOT)
                                      if frozen else SOURCE_ROOT))
    program = Path(program_root or (Path(sys.executable).resolve().parent
                                   if frozen else SOURCE_ROOT))
    root = SOURCE_ROOT
    if mode in {"beta", "study"}:
        args = list(argv)
        if not args:
            root = (default_beta_data_root().parent / "RoxyPlanTestInstances" / "user-study"
                    if mode == "study" else default_beta_data_root())
        elif len(args) == 2 and args[0] == "--data-root":
            root = Path(args[1])
        else:
            raise RuntimeConfigurationError("Beta accepts only --data-root <absolute directory>")
    return RuntimePaths(resources, program, root, mode,
                        version=STUDY_VERSION if mode == "study" else VERSION,
                        data_source="engineering" if mode == "study" else "real")


def read_json_strict(path: Path, expected_type=dict):
    """Read without fallback writes or including private values in errors."""
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError) as error:
        raise RuntimeConfigurationError(f"Cannot read {path.name}; existing file preserved") from error
    if not isinstance(value, expected_type):
        raise RuntimeConfigurationError(f"Invalid structure in {path.name}; existing file preserved")
    return value


def write_missing_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError:
        pass


def initialize_runtime(runtime: RuntimePaths, defaults: dict) -> dict:
    """Preflight before existing services can replace unreadable JSON defaults."""
    directories = ("data", "data/private", "data/private/backups", "data/private/locks",
                   "data/private/.locks", "data/private/diagnostics", "data/knowledge",
                   "logs", "logs/development", "logs/startup")
    files = {**CONFIG_FILES, **{name: dict for name in STORE_FILES},
             "runtime_metadata.json": dict}
    # Verify all known destinations before creating defaults or business stores.
    for name in (*directories, *files):
        runtime.writable(name)
    if runtime.is_packaged:
        if runtime.is_beta and runtime.writable("simulation_state.json").exists():
            raise RuntimeConfigurationError("A simulated data root cannot be used for Beta")
        for name, expected in files.items():
            path = runtime.writable(name)
            if path.exists():
                read_json_strict(path, expected)
                # Opening for update checks file ACLs/read-only attributes
                # without changing existing contents or timestamps.
                with path.open("r+b"):
                    pass
    if runtime.metadata_file.exists():
        _validate_metadata(read_json_strict(runtime.metadata_file), runtime)
    for name in directories:
        directory = runtime.writable(name)
        directory.mkdir(parents=True, exist_ok=True)
        # Real write probe: os.access alone cannot prove Windows ACLs allow writes.
        with tempfile.TemporaryFile(dir=directory):
            pass
    if runtime.is_packaged:
        for name, value in defaults.items():
            write_missing_json(runtime.writable(name), value)
    write_missing_json(runtime.metadata_file, {
        "runtime_schema_version": 1, "instance_id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "initial_version": runtime.version, "data_source": runtime.data_source,
    })
    metadata = read_json_strict(runtime.metadata_file)
    _validate_metadata(metadata, runtime)
    return metadata


def _validate_metadata(metadata: dict, runtime: RuntimePaths) -> None:
    try:
        UUID(metadata["instance_id"])
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise RuntimeConfigurationError("Invalid local instance metadata; file preserved") from error
    if metadata.get("data_source") != runtime.data_source:
        raise RuntimeConfigurationError("Real and engineering data roots cannot be combined")


def write_run_metadata(runtime: RuntimePaths, metadata: dict, logger) -> dict:
    if runtime.is_packaged and not logger.enabled:
        raise RuntimeConfigurationError("Beta requires background diagnostics")
    record = {
        "runtime_schema_version": 1, "version": runtime.version,
        "mode": runtime.mode, "data_source": runtime.data_source,
        "instance_id": metadata["instance_id"], "run_id": logger.run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if logger.enabled:
        if logger.run_directory is None:
            raise OSError("Required diagnostic log could not be initialized")
        directory = logger.run_directory.resolve()
        if not _inside(directory, runtime.logs_dir.resolve()):
            raise RuntimeConfigurationError("Diagnostic directory escapes data root")
        write_missing_json(directory / "runtime_metadata.json", record)
    return record


def ensure_windowed_streams() -> None:
    # Legacy UI uses print(..., flush=True); windowed builds have None streams.
    # Discard raw prints rather than logging private message/config bodies.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, _DiscardOutput())


class _DiscardOutput(io.TextIOBase):
    def write(self, text: str) -> int:
        return len(text)


def report_startup_failure(runtime: RuntimePaths | None, error: BaseException) -> Path | None:
    """Log only exception type and frame locations, never values/locals/argv."""
    frames = [{"file": Path(frame.filename).name, "line": frame.lineno,
               "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)]
    roots = [runtime] if runtime is not None else []
    try:
        fallback = resolve_runtime("beta")
        if fallback not in roots:
            roots.append(fallback)
    except (OSError, ValueError):
        pass
    for context in roots:
        try:
            directory = context.writable("logs/startup")
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"startup-{uuid4().hex}.json"
            write_missing_json(path, {"version": VERSION, "mode": context.mode,
                                     "error_type": type(error).__name__, "frames": frames,
                                     "time": datetime.now(timezone.utc).isoformat()})
            return path
        except (OSError, ValueError):
            continue
    return None


def show_startup_error(error: BaseException, log_path: Path | None) -> None:
    location = str(log_path) if log_path else "%LOCALAPPDATA%/RoxyPlan（日志目录不可写）"
    message = f"RoxyPlan 启动或运行失败（{type(error).__name__}）。\n请检查数据目录与配置。\n诊断位置：{location}"
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "RoxyPlan", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
