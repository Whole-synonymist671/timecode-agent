"""va gc — workspace storage hygiene: size report + selective purge.

Workspaces accumulate downloaded source media, capture frames and
filmstrips (318MB+ observed). `va gc` reports per-workspace size broken
down into four categories and can purge the regenerable ones:

    media     downloaded source video (source.mp4/webm/mkv) — big
    captures  capture frames + filmstrips (png/jpg; regenerable if media kept)
    clips     extracted deliverable clips
    text      manifest/transcript/checkpoints/corrections/glossary/markers …

`text` is never deleted by a category purge. The whole-workspace purge
(`--purge workspace --keep-days N`) is a separate, explicit removal of
stale workspaces and does drop the whole directory by design.

Every purge is a dry-run unless `--yes` is passed.
"""

from __future__ import annotations

import os
import shutil
import stat
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .workspace import Workspace
from .workspace_discovery import find_workspaces
from .workspace_lock import stable_workspace_lock

CATEGORIES = ("media", "captures", "clips", "text")
FILE_SELECTORS = ("captures", "media", "clips")
VALID_SELECTORS = FILE_SELECTORS + ("workspace",)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".flv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

type _FileIdentity = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _FilePurgeTarget:
    path: Path
    size: int
    identity: _FileIdentity


@dataclass(frozen=True, slots=True)
class _WorkspacePurgeTarget:
    path: Path
    size: int
    age_days: float
    identity: _FileIdentity
    activity_mtime_ns: int


@dataclass(frozen=True, slots=True)
class _DirectoryRemovalResult:
    removed: bool
    remaining_size: int | None = None
    preserved_path: Path | None = None


def _identity(info: os.stat_result) -> _FileIdentity:
    return info.st_dev, info.st_ino


@contextmanager
def _open_directory_nofollow(path: Path) -> Iterator[int]:
    """Open an absolute directory path one no-follow component at a time."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child_fd = os.open(
                component,
                flags,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        yield directory_fd
    finally:
        os.close(directory_fd)


def _unlink_regular_file(
    path: Path,
    expected_identity: _FileIdentity,
) -> bool:
    """Unlink a regular file without following replaced parent symlinks."""
    if os.name != "posix" or not path.is_absolute():
        return False
    try:
        with _open_directory_nofollow(path.parent) as parent_fd:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or _identity(info) != expected_identity
            ):
                return False
            os.unlink(path.name, dir_fd=parent_fd)
    except OSError:
        return False
    return True


def _remove_directory_tree(
    path: Path,
    expected_identity: _FileIdentity,
) -> _DirectoryRemovalResult:
    """Claim then remove a directory without deleting a same-name replacement."""
    if (
        os.name != "posix"
        or not path.is_absolute()
        or not shutil.rmtree.avoids_symlink_attacks
    ):
        return _DirectoryRemovalResult(removed=False)
    claimed_name = f".tca-gc-{uuid.uuid4().hex}"
    claimed_path = path.parent / claimed_name
    claimed = False
    try:
        with _open_directory_nofollow(path.parent) as parent_fd:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or _identity(info) != expected_identity
            ):
                return _DirectoryRemovalResult(removed=False)
            os.rename(
                path.name,
                claimed_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            claimed = True
            claimed_info = os.stat(
                claimed_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(claimed_info.st_mode)
                or _identity(claimed_info) != expected_identity
            ):
                _restore_claimed_directory(
                    parent_fd,
                    claimed_name,
                    path.name,
                    expected_identity,
                )
                preserved_path = _preserved_directory_path(
                    path,
                    claimed_path,
                    expected_identity,
                )
                return _DirectoryRemovalResult(
                    removed=False,
                    remaining_size=_directory_size_if_identity(
                        preserved_path,
                        expected_identity,
                    )
                    if preserved_path is not None
                    else None,
                    preserved_path=preserved_path,
                )
            shutil.rmtree(claimed_name, dir_fd=parent_fd)
            claimed = False
    except OSError:
        remaining_size = (
            _directory_size_if_identity(claimed_path, expected_identity)
            if claimed
            else None
        )
        if claimed:
            try:
                with _open_directory_nofollow(path.parent) as parent_fd:
                    _restore_claimed_directory(
                        parent_fd,
                        claimed_name,
                        path.name,
                        expected_identity,
                    )
            except OSError:
                pass
        preserved_path = _preserved_directory_path(
            path,
            claimed_path,
            expected_identity,
        )
        if remaining_size is None and preserved_path is not None:
            remaining_size = _directory_size_if_identity(
                preserved_path,
                expected_identity,
            )
        return _DirectoryRemovalResult(
            removed=False,
            remaining_size=remaining_size,
            preserved_path=preserved_path,
        )
    return _DirectoryRemovalResult(removed=True, remaining_size=0)


def _restore_claimed_directory(
    parent_fd: int,
    claimed_name: str,
    original_name: str,
    expected_identity: _FileIdentity,
) -> None:
    """Best-effort restore after a failed claimed-tree removal."""
    try:
        claimed_info = os.stat(
            claimed_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return
    if (
        not stat.S_ISDIR(claimed_info.st_mode)
        or _identity(claimed_info) != expected_identity
    ):
        return
    try:
        os.stat(original_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        os.rename(
            claimed_name,
            original_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )


def _preserved_directory_path(
    original_path: Path,
    claimed_path: Path,
    expected_identity: _FileIdentity,
) -> Path | None:
    for candidate in (original_path, claimed_path):
        if _directory_matches(candidate, expected_identity):
            return candidate
    return None


def _directory_size_if_identity(
    path: Path,
    expected_identity: _FileIdentity,
) -> int | None:
    if not _directory_matches(path, expected_identity):
        return None
    try:
        return sum(
            size
            for files in scan_workspace(path).values()
            for _, size in files
        )
    except OSError:
        return None


def categorize(rel_parts: tuple[str, ...], suffix: str) -> str:
    """Bucket a workspace-relative file into one of CATEGORIES.

    clips/ deliverables win over their .mp4 extension; images are
    captures; other videos (source.*) are media; everything else is text.
    """
    top = rel_parts[0] if rel_parts else ""
    if top == "clips":
        return "clips"
    if top == "cache":
        # 파생 미디어 캐시(오디오 wav 등) — 재생성 가능하므로 소스 영상과
        # 같은 media 분류로 회수한다.
        return "media"
    if suffix in IMAGE_EXTS:
        return "captures"
    if suffix in VIDEO_EXTS:
        return "media"
    return "text"


def human(n: int) -> str:
    """Bytes -> short human string (e.g. 61.5M)."""
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"  # unreachable, keeps type checkers happy


def scan_workspace(ws: Path) -> dict[str, list[tuple[Path, int]]]:
    """Category -> [(file, size)] for every regular file under ws."""
    buckets: dict[str, list[tuple[Path, int]]] = {c: [] for c in CATEGORIES}
    for f in ws.rglob("*"):
        if not f.is_file() or f.is_symlink():
            continue
        rel = f.relative_to(ws).parts
        buckets[categorize(rel, f.suffix.lower())].append((f, f.stat().st_size))
    return buckets


def category_sizes(buckets: dict[str, list[tuple[Path, int]]]) -> dict[str, int]:
    return {c: sum(s for _, s in files) for c, files in buckets.items()}


def workspace_mtime(ws: Path) -> float:
    """Newest file mtime in the workspace (last activity)."""
    return _workspace_activity_mtime_ns(ws) / 1_000_000_000


def _workspace_activity_mtime_ns(ws: Path) -> int:
    """Newest regular-file mtime without following workspace symlinks."""
    mtimes: list[int] = []
    for path in ws.rglob("*"):
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            mtimes.append(info.st_mtime_ns)
    if mtimes:
        return max(mtimes)
    return ws.stat(follow_symlinks=False).st_mtime_ns


def parse_selectors(raw: list[str] | None) -> list[str]:
    """Split repeated/comma-joined --purge values, validate, dedupe."""
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw:
        for tok in chunk.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok not in VALID_SELECTORS:
                raise ValueError(
                    f"unknown --purge selector '{tok}' "
                    f"(choose from {', '.join(VALID_SELECTORS)})")
            if tok not in out:
                out.append(tok)
    return out


def format_report(workspaces: list[Path]) -> str:
    """Per-workspace category table + total + a purge hint line."""
    if not workspaces:
        return "no va workspaces found (need a dir with manifest.json)"
    rows: list[tuple[str, dict[str, int]]] = []
    totals = {c: 0 for c in CATEGORIES}
    for ws in workspaces:
        sizes = category_sizes(scan_workspace(ws))
        for c in CATEGORIES:
            totals[c] += sizes[c]
        rows.append((ws.name, sizes))

    name_w = max([len("workspace")] + [len(n) for n, _ in rows])
    cols = CATEGORIES + ("total",)
    col_w = 9
    header = "workspace".ljust(name_w) + "  " + "".join(
        c.rjust(col_w) for c in cols)
    lines = [header, "-" * len(header)]
    for name, sizes in sorted(rows, key=lambda r: -sum(r[1].values())):
        total = sum(sizes.values())
        cells = [human(sizes[c]) for c in CATEGORIES] + [human(total)]
        lines.append(name.ljust(name_w) + "  "
                     + "".join(v.rjust(col_w) for v in cells))
    grand = sum(totals.values())
    tot_cells = [human(totals[c]) for c in CATEGORIES] + [human(grand)]
    lines.append("-" * len(header))
    lines.append("TOTAL".ljust(name_w) + "  "
                 + "".join(v.rjust(col_w) for v in tot_cells))

    purgeable = {c: totals[c] for c in FILE_SELECTORS if totals[c] > 0}
    if purgeable:
        biggest = max(purgeable, key=lambda category: purgeable[category])
        warn = " (재캡처 불가)" if biggest == "media" else ""
        lines.append("")
        lines.append(
            f"hint: `va gc --purge {biggest} --yes` frees "
            f"~{human(totals[biggest])}{warn} — dry-run without --yes")
    return "\n".join(lines)


def plan_file_purge(
    workspaces: list[Path], selectors: list[str],
) -> dict[str, list[_FilePurgeTarget]]:
    """Gather files to remove for captures/media/clips selectors."""
    wanted = [s for s in selectors if s in FILE_SELECTORS]
    plan: dict[str, list[_FilePurgeTarget]] = {c: [] for c in wanted}
    for ws in workspaces:
        buckets = scan_workspace(ws)
        for cat in wanted:
            for path, _ in buckets[cat]:
                try:
                    info = path.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    plan[cat].append(
                        _FilePurgeTarget(
                            path=path,
                            size=info.st_size,
                            identity=_identity(info),
                        )
                    )
    return plan


def plan_workspace_purge(
    workspaces: list[Path], keep_days: float, now: float | None = None,
) -> list[_WorkspacePurgeTarget]:
    """Plan stale workspaces while pinning identity and observed activity."""
    now = time.time() if now is None else now
    stale: list[_WorkspacePurgeTarget] = []
    for ws in workspaces:
        try:
            info = ws.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        activity_mtime_ns = _workspace_activity_mtime_ns(ws)
        mtime = activity_mtime_ns / 1_000_000_000
        if now - mtime > keep_days * 86400:
            size = sum(sum(s for _, s in files)
                       for files in scan_workspace(ws).values())
            stale.append(
                _WorkspacePurgeTarget(
                    path=ws,
                    size=size,
                    age_days=(now - mtime) / 86400,
                    identity=_identity(info),
                    activity_mtime_ns=activity_mtime_ns,
                )
            )
    return stale


def _directory_matches(
    path: Path,
    expected_identity: _FileIdentity,
) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and _identity(info) == expected_identity


@contextmanager
def _workspace_purge_locks(path: Path) -> Iterator[None]:
    """Exclude every workspace command, then hold canonical ledger locks."""
    workspace = Workspace(path)
    with ExitStack() as stack:
        for lock_path in (
            workspace.workspace_lock_path,
            workspace.sequence_lock_path,
            workspace.checkpoint_lock_path,
            workspace.image_provenance_lock_path,
        ):
            stack.enter_context(
                stable_workspace_lock(
                    workspace.root,
                    lock_path,
                    exclusive=True,
                    blocking=False,
                )
            )
        yield


def _remaining_workspace_size(
    target: _WorkspacePurgeTarget,
) -> int | None:
    return _directory_size_if_identity(target.path, target.identity)


def run_gc(
    paths: list[str] | None,
    purge: list[str] | None,
    keep_days: float | None,
    yes: bool,
) -> tuple[str, int]:
    """Orchestrate report/purge and return (text_output, exit_code)."""
    selectors = parse_selectors(purge)
    workspaces = find_workspaces(paths)

    if not selectors:
        return format_report(workspaces), 0

    workspace_keep_days: float | None = None
    if "workspace" in selectors:
        if keep_days is None:
            raise ValueError(
                "--purge workspace requires --keep-days N (age threshold)")
        workspace_keep_days = keep_days

    out: list[str] = []
    dry = not yes
    tag = "DRY-RUN (add --yes to delete)" if dry else "DELETING"
    freed = 0

    file_plan = plan_file_purge(workspaces, selectors)
    for cat in FILE_SELECTORS:
        if cat not in file_plan:
            continue
        files = file_plan[cat]
        subtotal = sum(target.size for target in files)
        out.append(f"[{cat}] {len(files)} files, {human(subtotal)}  {tag}")
        if cat == "media":
            out.append("  warning: 소스 미디어 삭제 — 이후 재캡처 불가")
        for target in files:
            f, s = target.path, target.size
            if dry:
                action = "would remove"
            elif _unlink_regular_file(f, target.identity):
                action = "removed"
                freed += s
            else:
                action = "skipped (stale/unsafe or deletion failed)"
            out.append(f"  {action} {f}  ({human(s)})")

    if workspace_keep_days is not None:
        stale = plan_workspace_purge(workspaces, workspace_keep_days)
        subtotal = sum(target.size for target in stale)
        out.append(f"[workspace] {len(stale)} workspaces > {workspace_keep_days}d, "
                   f"{human(subtotal)}  {tag}")
        for target in stale:
            ws, s, age = target.path, target.size, target.age_days
            if dry:
                action = "would remove"
            else:
                try:
                    if not _directory_matches(ws, target.identity):
                        action = "skipped (stale/unsafe or deletion failed)"
                    else:
                        with _workspace_purge_locks(ws):
                            unchanged = (
                                _workspace_activity_mtime_ns(ws)
                                == target.activity_mtime_ns
                            )
                            if not unchanged:
                                action = "skipped (changed after planning)"
                            else:
                                removal = _remove_directory_tree(
                                    ws,
                                    target.identity,
                                )
                                if removal.removed:
                                    action = "removed"
                                    freed += s
                                else:
                                    remaining = removal.remaining_size
                                    if remaining is None:
                                        remaining = _remaining_workspace_size(
                                            target
                                        )
                                    if (
                                        remaining is not None
                                        and remaining < s
                                    ):
                                        action = (
                                            "partially removed "
                                            "(deletion failed)"
                                        )
                                        freed += s - remaining
                                    else:
                                        action = (
                                            "skipped (stale/unsafe or "
                                            "deletion failed)"
                                        )
                                    if (
                                        removal.preserved_path is not None
                                        and removal.preserved_path != ws
                                    ):
                                        action += (
                                            "; preserved at "
                                            f"{removal.preserved_path}"
                                        )
                except BlockingIOError:
                    action = "skipped (active or lock unavailable)"
                except OSError:
                    action = "skipped (stale/unsafe or deletion failed)"
            out.append(f"  {action} {ws} (age {age:.1f}d, {human(s)})")

    if dry:
        out.append("nothing deleted (dry-run) — re-run with --yes to apply")
    else:
        out.append(f"freed {human(freed)}")
    return "\n".join(out), 0
