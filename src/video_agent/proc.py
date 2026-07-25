"""Hardened subprocess wrapper — every shell-out gets a timeout.

Measured gap (2026-07-22): 15 subprocess call sites, 0 timeouts — a wedged
ffmpeg/ffprobe on a corrupt file would hang the whole loop forever
(depixelate_v3 subprocess_adapter lesson). Long-running legitimate work
(transcode, downloads) passes an explicit higher ceiling instead of
disabling the guard.
"""

from __future__ import annotations

import subprocess

DEFAULT_TIMEOUT = 600       # transcode-scale ceiling (seconds)
DOWNLOAD_TIMEOUT = 3600     # yt-dlp: long sources on slow links


def run(cmd, *, timeout: float = DEFAULT_TIMEOUT, **kwargs):
    """subprocess.run with a mandatory timeout and a diagnosable failure."""
    try:
        return subprocess.run(cmd, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"명령 시간 초과({timeout:.0f}s): {cmd[0]} — "
            "미디어 손상 또는 인코더 행(hang) 의심") from e
