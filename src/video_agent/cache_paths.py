"""Cross-environment cache location selection."""

from __future__ import annotations

import os
from pathlib import Path


def cache_root() -> Path:
    """Return the configured cache root without creating it."""
    if configured := os.environ.get("VIDEO_AGENT_CACHE_DIR"):
        return Path(configured).expanduser()
    if xdg_root := os.environ.get("XDG_CACHE_HOME"):
        xdg_path = Path(xdg_root).expanduser()
        if xdg_path.is_absolute():
            return xdg_path / "video-agent"
    return Path.home() / ".cache" / "video-agent"
