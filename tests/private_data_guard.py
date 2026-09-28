from __future__ import annotations

import hashlib
import inspect
import os
import pathlib
import tempfile
import traceback
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROTECTED_RELATIVE_ROOTS = (
    "memory.json",
    "config.json",
    "data/private",
    "data/today_plan.json",
    "data/action_log.json",
    "data/growth_log.json",
    "data/pet_config.json",
    "data/knowledge",
    ".env",
    "apikey.txt",
    "api_key.txt",
    "secret.txt",
    "token.txt",
)

PROTECTED_GLOBS = (".env.*",)


class RepositoryPathViolation(AssertionError):
    pass


@dataclass(frozen=True)
class PathFingerprint:
    kind: str
    size: int
    sha256: str
    mtime_ns: int

    def stable_value(self) -> Tuple[str, int, str]:
        return self.kind, self.size, self.sha256


@dataclass(frozen=True)
class PrivateDataSnapshot:
    project_root: Path
    records: Mapping[str, PathFingerprint]
    manifest_sha256: str


@dataclass(frozen=True)
class SnapshotDifference:
    added: Tuple[str, ...]
    removed: Tuple[str, ...]
    changed: Tuple[str, ...]
    mtime_only: Tuple[str, ...]

    @property
    def has_content_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(directory))
        return True
    except ValueError:
        return False


def protected_reason(path: Path, project_root: Path = PROJECT_ROOT) -> str:
    candidate = _resolved(path)
    root = _resolved(project_root)
    private_dir = root / "data" / "private"
    knowledge_dir = root / "data" / "knowledge"
    protected_files = {
        root / "memory.json",
        root / "config.json",
        root / "data" / "today_plan.json",
        root / "data" / "action_log.json",
        root / "data" / "growth_log.json",
        root / "data" / "pet_config.json",
        root / ".env",
        root / "apikey.txt",
        root / "api_key.txt",
        root / "secret.txt",
        root / "token.txt",
    }
    if candidate in {_resolved(item) for item in protected_files}:
        return "project private file"
    if _is_within(candidate, private_dir):
        return "project data/private"
    if _is_within(candidate, knowledge_dir):
        return "project private knowledge"
    if candidate.parent == root and candidate.name.startswith(".env."):
        return "project environment file"
    return ""


def assert_paths_are_test_isolated(
    paths: Iterable[Optional[Path]],
    *,
    owner: str,
    project_root: Path = PROJECT_ROOT,
) -> None:
    violations = []
    for value in paths:
        if value is None:
            continue
        path = Path(value)
        reason = protected_reason(path, project_root)
        if reason:
            violations.append(f"{path} ({reason})")
    if violations:
        joined = "; ".join(violations)
        raise RepositoryPathViolation(
            f"{owner} resolved to real project data during pytest: {joined}. "
            "Inject a complete temporary Repository instead.\n"
            + _caller_trace()
        )


def _caller_trace() -> str:
    frames = traceback.extract_stack()[:-2]
    relevant = [
        frame
        for frame in frames
        if "private_data_guard.py" not in frame.filename
    ][-8:]
    rendered = "".join(traceback.format_list(relevant)).rstrip()
    return f"write call site:\n{rendered}" if rendered else "write call site unavailable"


def assert_write_target_is_test_isolated(
    path: object,
    *,
    operation: str,
    project_root: Path = PROJECT_ROOT,
) -> None:
    """Fail before a test can create, replace, or remove real private data."""

    try:
        candidate = Path(path)
    except TypeError:
        return
    reason = protected_reason(candidate, project_root)
    if reason:
        raise RepositoryPathViolation(
            f"pytest blocked {operation} for real project data: {candidate} ({reason}).\n"
            + _caller_trace()
        )


def _guard_constructor(
    cls: object,
    path_getter: Callable[[Mapping[str, object]], Sequence[Optional[Path]]],
) -> Tuple[object, Callable[..., object]]:
    original = cls.__init__
    signature = inspect.signature(original)

    @wraps(original)
    def guarded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        assert_paths_are_test_isolated(
            path_getter(bound.arguments),
            owner=f"{cls.__module__}.{cls.__name__}",
        )
        return original(*args, **kwargs)

    cls.__init__ = guarded
    return cls, original


def install_repository_path_guards() -> Callable[[], None]:
    from modules.llm.secret_store import SecretStore
    from modules.llm.usage_store import ModelUsageStore
    from modules.repositories.local_json_chat_repository import LocalJsonChatRepository
    from modules.repositories.local_json_growth_repository import LocalJsonGrowthRepository
    from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
    from modules.today_plan import TodayPlanStore
    from modules.growth import ActionLogStore, GrowthLogStore
    from modules.chat_history_manager import ChatHistoryManager
    from modules.growth_manager import GrowthManager
    from modules.memory_manager import MemoryManager

    originals = []

    def memory_paths(values: Mapping[str, object]) -> Sequence[Optional[Path]]:
        conflict = Path(values["conflict_file"])
        audit = values.get("audit_file") or conflict.parent / "memory_audit.json"
        return (
            Path(values["memory_file"]),
            Path(values["candidate_file"]),
            conflict,
            Path(values["backup_dir"]),
            Path(audit),
            conflict.parent / "locks",
        )

    def growth_paths(values: Mapping[str, object]) -> Sequence[Optional[Path]]:
        private = Path(values["private_dir"])
        return (
            private,
            private / "today_plan.json",
            private / "action_log.json",
            private / "growth_log.json",
            private / "backups",
            private / "locks",
            values.get("legacy_plan_file"),
            values.get("legacy_action_file"),
            values.get("legacy_growth_file"),
        )

    def chat_paths(values: Mapping[str, object]) -> Sequence[Optional[Path]]:
        private = Path(values["private_dir"])
        return (
            private,
            private / "chat_history.json",
            private / "chat_summaries.json",
            private / "backups",
            private / "locks",
        )

    def secret_paths(values: Mapping[str, object]) -> Sequence[Optional[Path]]:
        root = Path(values["project_root"])
        path = values.get("path") or root / "data" / "private" / "llm_secrets.json"
        return (
            Path(path),
            root / "data" / "private" / ".locks",
            root / "data" / "private" / "backups",
        )

    def usage_paths(values: Mapping[str, object]) -> Sequence[Optional[Path]]:
        root = Path(values["project_root"])
        path = values.get("path") or root / "data" / "private" / "model_usage.json"
        return (
            Path(path),
            root / "data" / "private" / ".locks",
            root / "data" / "private" / "backups",
        )

    for cls, getter in (
        (MemoryManager, lambda values: (values.get("memory_file"),)),
        (GrowthManager, lambda values: (values.get("private_dir"),)),
        (ChatHistoryManager, lambda values: (values.get("private_dir"),)),
        (TodayPlanStore, lambda values: (values.get("path"),)),
        (ActionLogStore, lambda values: (values.get("path"),)),
        (GrowthLogStore, lambda values: (values.get("path"),)),
        (LocalJsonMemoryRepository, memory_paths),
        (LocalJsonGrowthRepository, growth_paths),
        (LocalJsonChatRepository, chat_paths),
        (SecretStore, secret_paths),
        (ModelUsageStore, usage_paths),
    ):
        originals.append(_guard_constructor(cls, getter))

    def restore() -> None:
        while originals:
            cls, original = originals.pop()
            cls.__init__ = original

    return restore


def install_real_data_write_firewall() -> Callable[[], None]:
    """Install process-wide write guards for every protected project path.

    Constructor guards catch missing dependency injection early.  This second
    layer catches legacy stores and direct file operations, including atomic
    temporary-file replacement, so a missed constructor cannot mutate private
    data silently.
    """

    originals = []

    def replace_attr(owner: object, name: str, replacement: object) -> None:
        originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    original_path_open = pathlib.Path.open
    original_write_text = pathlib.Path.write_text
    original_write_bytes = pathlib.Path.write_bytes
    original_mkdir = pathlib.Path.mkdir
    original_replace = pathlib.Path.replace
    original_rename = pathlib.Path.rename
    original_unlink = pathlib.Path.unlink
    original_touch = pathlib.Path.touch
    original_os_replace = os.replace
    original_os_rename = os.rename
    original_os_remove = os.remove
    original_os_unlink = os.unlink
    original_mkstemp = tempfile.mkstemp

    @wraps(original_path_open)
    def guarded_path_open(self, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            assert_write_target_is_test_isolated(self, operation=f"Path.open({mode!r})")
        return original_path_open(self, mode, *args, **kwargs)

    @wraps(original_write_text)
    def guarded_write_text(self, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.write_text")
        return original_write_text(self, *args, **kwargs)

    @wraps(original_write_bytes)
    def guarded_write_bytes(self, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.write_bytes")
        return original_write_bytes(self, *args, **kwargs)

    @wraps(original_mkdir)
    def guarded_mkdir(self, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.mkdir")
        return original_mkdir(self, *args, **kwargs)

    @wraps(original_replace)
    def guarded_replace(self, target, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.replace source")
        assert_write_target_is_test_isolated(target, operation="Path.replace target")
        return original_replace(self, target, *args, **kwargs)

    @wraps(original_rename)
    def guarded_rename(self, target, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.rename source")
        assert_write_target_is_test_isolated(target, operation="Path.rename target")
        return original_rename(self, target, *args, **kwargs)

    @wraps(original_unlink)
    def guarded_unlink(self, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.unlink")
        return original_unlink(self, *args, **kwargs)

    @wraps(original_touch)
    def guarded_touch(self, *args, **kwargs):
        assert_write_target_is_test_isolated(self, operation="Path.touch")
        return original_touch(self, *args, **kwargs)

    @wraps(original_os_replace)
    def guarded_os_replace(source, destination, *args, **kwargs):
        assert_write_target_is_test_isolated(source, operation="os.replace source")
        assert_write_target_is_test_isolated(destination, operation="os.replace target")
        return original_os_replace(source, destination, *args, **kwargs)

    @wraps(original_os_rename)
    def guarded_os_rename(source, destination, *args, **kwargs):
        assert_write_target_is_test_isolated(source, operation="os.rename source")
        assert_write_target_is_test_isolated(destination, operation="os.rename target")
        return original_os_rename(source, destination, *args, **kwargs)

    @wraps(original_os_remove)
    def guarded_os_remove(path, *args, **kwargs):
        assert_write_target_is_test_isolated(path, operation="os.remove")
        return original_os_remove(path, *args, **kwargs)

    @wraps(original_os_unlink)
    def guarded_os_unlink(path, *args, **kwargs):
        assert_write_target_is_test_isolated(path, operation="os.unlink")
        return original_os_unlink(path, *args, **kwargs)

    @wraps(original_mkstemp)
    def guarded_mkstemp(*args, **kwargs):
        directory = kwargs.get("dir")
        if directory is None and len(args) >= 3:
            directory = args[2]
        if directory is not None:
            assert_write_target_is_test_isolated(directory, operation="tempfile.mkstemp")
        return original_mkstemp(*args, **kwargs)

    replace_attr(pathlib.Path, "open", guarded_path_open)
    replace_attr(pathlib.Path, "write_text", guarded_write_text)
    replace_attr(pathlib.Path, "write_bytes", guarded_write_bytes)
    replace_attr(pathlib.Path, "mkdir", guarded_mkdir)
    replace_attr(pathlib.Path, "replace", guarded_replace)
    replace_attr(pathlib.Path, "rename", guarded_rename)
    replace_attr(pathlib.Path, "unlink", guarded_unlink)
    replace_attr(pathlib.Path, "touch", guarded_touch)
    replace_attr(os, "replace", guarded_os_replace)
    replace_attr(os, "rename", guarded_os_rename)
    replace_attr(os, "remove", guarded_os_remove)
    replace_attr(os, "unlink", guarded_os_unlink)
    replace_attr(tempfile, "mkstemp", guarded_mkstemp)

    def restore() -> None:
        while originals:
            owner, name, original = originals.pop()
            setattr(owner, name, original)

    return restore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AssertionError(f"Cannot fingerprint protected path {path}: {error}") from error
    return digest.hexdigest()


def _record_path(
    project_root: Path,
    path: Path,
    records: Dict[str, PathFingerprint],
) -> None:
    relative = path.relative_to(project_root).as_posix()
    try:
        stat = path.lstat()
        if path.is_symlink():
            target = os.readlink(str(path))
            records[relative] = PathFingerprint(
                "symlink",
                len(target.encode("utf-8")),
                hashlib.sha256(target.encode("utf-8")).hexdigest(),
                stat.st_mtime_ns,
            )
            return
        if path.is_dir():
            records[relative] = PathFingerprint("directory", 0, "", stat.st_mtime_ns)
            return
        if path.is_file():
            records[relative] = PathFingerprint(
                "file",
                stat.st_size,
                _sha256(path),
                stat.st_mtime_ns,
            )
            return
        records[relative] = PathFingerprint("other", stat.st_size, "", stat.st_mtime_ns)
    except OSError as error:
        raise AssertionError(f"Cannot inspect protected path {path}: {error}") from error


def snapshot_paths(
    project_root: Path,
    relative_roots: Sequence[str],
    *,
    glob_patterns: Sequence[str] = (),
) -> PrivateDataSnapshot:
    root = _resolved(project_root)
    records: Dict[str, PathFingerprint] = {}
    targets = [root / relative for relative in relative_roots]
    for pattern in glob_patterns:
        targets.extend(root.glob(pattern))

    unique_targets = sorted({_resolved(path) for path in targets}, key=lambda item: str(item))
    for target in unique_targets:
        try:
            target.relative_to(root)
        except ValueError as error:
            raise AssertionError(f"Protected path escaped project root: {target}") from error
        relative = target.relative_to(root).as_posix()
        if not target.exists() and not target.is_symlink():
            records[relative] = PathFingerprint("missing", 0, "", 0)
            continue
        _record_path(root, target, records)
        if target.is_dir() and not target.is_symlink():
            try:
                descendants = sorted(target.rglob("*"), key=lambda item: str(item))
            except OSError as error:
                raise AssertionError(
                    f"Cannot enumerate protected directory {target}: {error}"
                ) from error
            for descendant in descendants:
                _record_path(root, descendant, records)

    manifest = hashlib.sha256()
    for relative, fingerprint in sorted(records.items()):
        kind, size, content_hash = fingerprint.stable_value()
        manifest.update(
            f"{relative}\0{kind}\0{size}\0{content_hash}\n".encode("utf-8")
        )
    return PrivateDataSnapshot(root, records, manifest.hexdigest())


def snapshot_real_private_data(
    project_root: Path = PROJECT_ROOT,
) -> PrivateDataSnapshot:
    return snapshot_paths(
        project_root,
        PROTECTED_RELATIVE_ROOTS,
        glob_patterns=PROTECTED_GLOBS,
    )


def compare_snapshots(
    before: PrivateDataSnapshot,
    after: PrivateDataSnapshot,
) -> SnapshotDifference:
    before_keys = set(before.records)
    after_keys = set(after.records)
    added = tuple(sorted(after_keys - before_keys))
    removed = tuple(sorted(before_keys - after_keys))
    changed = []
    mtime_only = []
    for relative in sorted(before_keys & after_keys):
        old = before.records[relative]
        new = after.records[relative]
        if old.stable_value() != new.stable_value():
            changed.append(relative)
        elif old.mtime_ns != new.mtime_ns:
            mtime_only.append(relative)
    return SnapshotDifference(
        added,
        removed,
        tuple(changed),
        tuple(mtime_only),
    )


def assert_real_private_data_unchanged(
    before: PrivateDataSnapshot,
    after: PrivateDataSnapshot,
) -> SnapshotDifference:
    difference = compare_snapshots(before, after)
    if difference.has_content_changes:
        lines: List[str] = [
            "pytest changed real private project data; no automatic cleanup was attempted.",
            f"before_manifest={before.manifest_sha256}",
            f"after_manifest={after.manifest_sha256}",
        ]
        for label, values in (
            ("added", difference.added),
            ("removed", difference.removed),
            ("changed", difference.changed),
        ):
            if values:
                lines.append(f"{label}: " + ", ".join(values))
        raise AssertionError("\n".join(lines))
    return difference
