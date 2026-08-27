"""Cross-process locked JSON state reads and read-modify-write mutations."""

import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping, TypeVar


State = dict[str, object]
MutationResult = TypeVar("MutationResult")
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def read_json_state(
    path: str | Path,
    defaults: Mapping[str, object] | None = None,
) -> State:
    """Read one state snapshot while excluding concurrent replacement."""
    state_path = Path(path)
    with _state_lock(state_path):
        return _read_unlocked(state_path, defaults or {})


def mutate_json_state(
    path: str | Path,
    mutator: Callable[[State], MutationResult],
    defaults: Mapping[str, object] | None = None,
) -> tuple[State, MutationResult]:
    """Lock, read, mutate, and atomically replace one state document."""
    state_path = Path(path)
    with _state_lock(state_path):
        state = _read_unlocked(state_path, defaults or {})
        result = mutator(state)
        _atomic_write_unlocked(state_path, state)
        return state, result


def replace_json_state(path: str | Path, state: Mapping[str, object]) -> None:
    """Atomically replace a complete state snapshot under the shared lock."""
    state_path = Path(path)
    with _state_lock(state_path):
        _atomic_write_unlocked(state_path, dict(state))


@contextmanager
def _state_lock(state_path: Path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(state_path.resolve()))
    with _PATH_LOCKS_GUARD:
        process_lock = _PATH_LOCKS.setdefault(key, threading.RLock())

    with process_lock:
        lock_path = state_path.with_name(f"{state_path.name}.lock")
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            _acquire_os_lock(lock_file)
            try:
                yield
            finally:
                _release_os_lock(lock_file)


def _acquire_os_lock(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_os_lock(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_unlocked(state_path: Path, defaults: Mapping[str, object]) -> State:
    try:
        with state_path.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("State file must contain a JSON object")
    for name, value in defaults.items():
        state.setdefault(name, value)
    return state


def _atomic_write_unlocked(state_path: Path, state: Mapping[str, object]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = state_path.stat().st_mode
    except FileNotFoundError:
        existing_mode = None

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=state_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.chmod(
            temporary_path,
            stat.S_IMODE(existing_mode) if existing_mode is not None else 0o660,
        )
        os.replace(temporary_path, state_path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
