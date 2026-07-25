"""Download ungated default model assets during the all-on installation."""

from __future__ import annotations

import os
import platform
import sys

from .asr_mlx import mlx_model_repo


def prepare_runtime_assets() -> dict[str, str]:
    """Prepare every immediately usable backend; gated pyannote is best-effort."""
    from huggingface_hub import snapshot_download

    status: dict[str, str] = {}
    snapshot_download(repo_id="Systran/faster-whisper-small")
    status["faster-whisper"] = "ready"

    if sys.platform == "darwin" and platform.machine() == "arm64":
        snapshot_download(repo_id=mlx_model_repo("small"))
        status["mlx-whisper"] = "ready"
    else:
        status["mlx-whisper"] = "not-applicable"

    from .diarize import _sherpa_model_dir

    _sherpa_model_dir()
    status["sherpa-diarize"] = "ready"

    token = os.environ.get("HF_TOKEN") or os.environ.get(
        "HUGGING_FACE_HUB_TOKEN"
    )
    if token:
        try:
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=token,
            )
            status["pyannote"] = "ready" if pipeline is not None else "gated"
        except Exception as exc:
            status["pyannote"] = f"gated: {exc}"
    else:
        status["pyannote"] = (
            "gated-token-required; sherpa-diarize is ready"
        )
    return status
