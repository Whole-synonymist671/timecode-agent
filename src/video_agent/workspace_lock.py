"""Stable workspace locks: POSIX flock first, Windows byte-range fallback.

POSIX (macOS·Linux) semantics are unchanged: shared/exclusive ``flock`` on a
persistent sidecar opened read-only, with a directory-inode fallback for
legacy workspaces that predate sidecars (an empty read never creates files).

Windows deviations, chosen to keep mutual exclusion real rather than
pretending parity:

- ``msvcrt.locking`` offers no shared locks, so shared requests degrade to
  exclusive (coarser concurrency, same safety).
- A directory handle cannot be byte-locked, so the legacy fallback locks the
  sidecar path itself and creates it when missing — the one place Windows
  writes where POSIX would not.
- ``LK_LOCK`` gives up after ten one-second retries instead of blocking, so
  blocking acquisition loops over ``LK_NBLCK`` with a short sleep.
"""

from __future__ import annotations

import errno
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import local

if sys.platform == "win32":                                     # pragma: posix no cover
    import msvcrt
else:
    import fcntl

type _InodeKey = tuple[int, int]
type _LogicalTarget = _InodeKey | str

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class _HeldLock:
    logical_target: _LogicalTarget
    exclusive: bool


class _ThreadLockState(local):
    def __init__(self) -> None:
        self.pid = os.getpid()
        self.held: dict[_InodeKey, list[_HeldLock]] = {}


_THREAD_LOCK_STATE = _ThreadLockState()


def _held_locks() -> dict[_InodeKey, list[_HeldLock]]:
    """Return this thread's lock stack, clearing inherited state after fork."""
    pid = os.getpid()
    if _THREAD_LOCK_STATE.pid != pid:
        _THREAD_LOCK_STATE.pid = pid
        _THREAD_LOCK_STATE.held = {}
    return _THREAD_LOCK_STATE.held


def _would_self_deadlock(
    stack: list[_HeldLock],
    logical_target: _LogicalTarget,
    *,
    exclusive: bool,
) -> bool:
    """Reject unsafe upgrades while permitting covered cross-ledger nesting."""
    if not exclusive:
        return False
    same_target = any(
        held.logical_target == logical_target for held in stack
    )
    return same_target or not any(held.exclusive for held in stack)


def _open_lock_fd(root: Path, lock_path: Path) -> tuple[int, bool]:
    """Open the lock target; returns (fd, using_legacy_fallback)."""
    try:
        return os.open(
            lock_path,
            os.O_RDONLY | _O_CLOEXEC | _O_NOFOLLOW,
        ), False
    except FileNotFoundError:
        if sys.platform == "win32":                 # pragma: posix no cover
            # 디렉터리 핸들은 바이트 락이 안 걸린다 — 사이드카를 만들어
            # 그 파일을 잠근다(레거시 워크스페이스 한정 문서화된 편차).
            return os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOINHERIT", 0),
            ), True
        return os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC,
        ), True


def _acquire(lock_fd: int, *, exclusive: bool, blocking: bool) -> None:
    if sys.platform == "win32":                     # pragma: posix no cover
        # 공유 락 부재 → 전부 배타로 강등. LK_LOCK은 10회 후 포기라
        # blocking은 LK_NBLCK 재시도 루프로 구현한다.
        os.lseek(lock_fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not blocking:
                    raise BlockingIOError(
                        errno.EWOULDBLOCK, "lock held elsewhere"
                    ) from exc
                time.sleep(0.05)
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    fcntl.flock(lock_fd, operation)


def _release(lock_fd: int) -> None:
    if sys.platform == "win32":                     # pragma: posix no cover
        os.lseek(lock_fd, 0, os.SEEK_SET)
        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(lock_fd, fcntl.LOCK_UN)


@contextmanager
def stable_workspace_lock(
    root: Path,
    lock_path: Path,
    *,
    exclusive: bool,
    blocking: bool = True,
) -> Iterator[None]:
    """Lock a persistent sidecar or the workspace directory for legacy data."""
    lock_fd, using_legacy_fallback = _open_lock_fd(root, lock_path)
    info = os.fstat(lock_fd)
    inode_key = (info.st_dev, info.st_ino)
    logical_target: _LogicalTarget = (
        lock_path.name if using_legacy_fallback else inode_key
    )
    display_target = os.fspath(lock_path.resolve(strict=False))
    held_locks = _held_locks()
    stack = held_locks.get(inode_key)
    if stack:
        os.close(lock_fd)
        if _would_self_deadlock(
            stack,
            logical_target,
            exclusive=exclusive,
        ):
            raise BlockingIOError(
                errno.EWOULDBLOCK,
                "unsafe nested workspace lock upgrade",
                display_target,
            )
        nested = _HeldLock(logical_target, exclusive)
        stack.append(nested)
        try:
            yield
        finally:
            stack.pop()
        return

    outer = _HeldLock(logical_target, exclusive)
    acquired = False
    try:
        _acquire(lock_fd, exclusive=exclusive, blocking=blocking)
        acquired = True
        held_locks[inode_key] = [outer]
        yield
    finally:
        held_locks.pop(inode_key, None)
        try:
            if acquired:
                # 미획득 상태의 LK_UNLCK는 Windows에서 OSError를 던져
                # 원래 예외(BlockingIOError·KeyboardInterrupt)를 가린다.
                _release(lock_fd)
        finally:
            os.close(lock_fd)
