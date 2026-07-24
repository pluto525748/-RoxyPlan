from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock, RLock, local
from typing import Dict, Iterator, Optional


_LOCKS: Dict[str, RLock] = {}
_LOCKS_GUARD = Lock()
_THREAD_STATE = local()
_CORRUPT_BACKUPS = set()
_CORRUPT_BACKUPS_GUARD = Lock()


def _process_lock(key: str) -> RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, RLock())


@contextmanager
def json_transaction(
    data_path: Path,
    lock_dir: Path,
    *,
    timeout_seconds: float = 5.0,
) -> Iterator[None]:
    """Serialize one JSON read-modify-write cycle across local processes."""

    path = Path(data_path)
    key = str(path.resolve()).lower()
    held = getattr(_THREAD_STATE, "held_json_locks", set())
    with _process_lock(key):
        if key in held:
            yield
            return

        target_lock_dir = Path(lock_dir)
        target_lock_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        lock_path = target_lock_dir / f"{digest}.lock"
        lock_file = lock_path.open("a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            _acquire_os_lock(lock_file, timeout_seconds)
            next_held = set(held)
            next_held.add(key)
            _THREAD_STATE.held_json_locks = next_held
            try:
                yield
            finally:
                _THREAD_STATE.held_json_locks = held
                _release_os_lock(lock_file)
        finally:
            lock_file.close()


def load_json_document(
    path: Path,
    fallback: Dict[str, object],
    *,
    label: str,
    lock_dir: Path,
    backup_dir: Optional[Path] = None,
) -> Dict[str, object]:
    target = Path(path)
    if not target.exists():
        return deepcopy(fallback)
    try:
        with json_transaction(target, lock_dir):
            loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
        print(f"[{label}] load failed: {target.name}: InvalidDocument", flush=True)
        if backup_dir is not None:
            backup_corrupt_file(target, backup_dir, label=label, lock_dir=lock_dir)
        return deepcopy(fallback)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"[{label}] load failed: {target.name}: {type(error).__name__}", flush=True)
        if backup_dir is not None:
            backup_corrupt_file(target, backup_dir, label=label, lock_dir=lock_dir)
        return deepcopy(fallback)
    except (OSError, TimeoutError) as error:
        print(f"[{label}] load failed: {target.name}: {type(error).__name__}", flush=True)
        return deepcopy(fallback)


def atomic_write_json(
    path: Path,
    data: Dict[str, object],
    *,
    label: str,
    lock_dir: Path,
    trailing_newline: bool = True,
) -> bool:
    target = Path(path)
    temporary: Optional[Path] = None
    try:
        with json_transaction(target, lock_dir):
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            temporary = Path(temp_name)
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            if trailing_newline:
                payload += "\n"
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        return True
    except (OSError, TimeoutError) as error:
        print(f"[{label}] save failed: {target.name}: {type(error).__name__}", flush=True)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def backup_corrupt_file(
    path: Path,
    backup_dir: Path,
    *,
    label: str,
    lock_dir: Path,
) -> Optional[Path]:
    target = Path(path)
    if not target.exists():
        return None
    try:
        with json_transaction(target, lock_dir):
            stat = target.stat()
            signature = (str(target.resolve()).lower(), stat.st_mtime_ns, stat.st_size)
            with _CORRUPT_BACKUPS_GUARD:
                if signature in _CORRUPT_BACKUPS:
                    return None
            destination_dir = Path(backup_dir)
            destination_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = destination_dir / f"corrupt_{target.stem}_{timestamp}{target.suffix}"
            suffix = 1
            while backup.exists():
                backup = destination_dir / (
                    f"corrupt_{target.stem}_{timestamp}_{suffix}{target.suffix}"
                )
                suffix += 1
            backup.write_bytes(target.read_bytes())
            with _CORRUPT_BACKUPS_GUARD:
                _CORRUPT_BACKUPS.add(signature)
        print(f"[{label}] corrupt backup created: {target.name}", flush=True)
        return backup
    except (OSError, TimeoutError) as error:
        print(f"[{label}] corrupt backup failed: {type(error).__name__}", flush=True)
        return None


def _acquire_os_lock(handle, timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for local JSON lock")
            time.sleep(0.05)


def _release_os_lock(handle) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
