"""Persistent all-on runtime policy and measured backend selection."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from .fsio import write_text_atomic


class Feature(StrEnum):
    ASR = "asr"
    OCR = "ocr"
    FACES = "faces"
    AUDIO_EVENTS = "audioevents"
    DIARIZE = "diarize"
    OTIO = "otio"


class Profile(StrEnum):
    BALANCED = "balanced"
    LOW_POWER = "low-power"
    QUALITY = "quality"


class ASRBackend(StrEnum):
    AUTO = "auto"
    FASTER_WHISPER = "faster-whisper"
    MLX = "mlx"


class ClipEncoder(StrEnum):
    AUTO = "auto"
    SOFTWARE = "software"
    H264_VIDEOTOOLBOX = "h264-videotoolbox"
    HEVC_VIDEOTOOLBOX = "hevc-videotoolbox"


def _all_features() -> dict[Feature, bool]:
    return {feature: True for feature in Feature}


@dataclass(frozen=True)
class RuntimeConfig:
    """User policy; capability installation remains independent and all-on."""

    profile: Profile = Profile.BALANCED
    asr_backend: ASRBackend = ASRBackend.AUTO
    clip_encoder: ClipEncoder = ClipEncoder.AUTO
    features: Mapping[Feature, bool] = field(default_factory=_all_features)


def runtime_config_dir() -> Path:
    if configured := os.environ.get("VIDEO_AGENT_CONFIG_DIR"):
        return Path(configured).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "timecode-agent"
    if configured := os.environ.get("XDG_CONFIG_HOME"):
        root = Path(configured).expanduser()
        if root.is_absolute():
            return root / "timecode-agent"
    return Path.home() / ".config" / "timecode-agent"


def runtime_config_path() -> Path:
    return runtime_config_dir() / "runtime.json"


def load_runtime_config() -> RuntimeConfig:
    path = runtime_config_path()
    if not path.is_file():
        return RuntimeConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("root must be an object")
        raw_features = raw.get("features", {})
        if not isinstance(raw_features, dict):
            raise ValueError("features must be an object")
        features = _all_features()
        for feature in Feature:
            enabled = raw_features.get(feature.value)
            if isinstance(enabled, bool):
                features[feature] = enabled
        return RuntimeConfig(
            profile=Profile(str(raw.get("profile", Profile.BALANCED))),
            asr_backend=ASRBackend(
                str(raw.get("asr_backend", ASRBackend.AUTO))
            ),
            clip_encoder=ClipEncoder(
                str(raw.get("clip_encoder", ClipEncoder.AUTO))
            ),
            features=features,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"런타임 설정을 읽을 수 없습니다: {path} ({exc})") from None


def save_runtime_config(config: RuntimeConfig) -> Path:
    path = runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": config.profile.value,
        "asr_backend": config.asr_backend.value,
        "clip_encoder": config.clip_encoder.value,
        "features": {
            feature.value: config.features.get(feature, True)
            for feature in Feature
        },
    }
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def set_runtime_value(
    config: RuntimeConfig,
    key: str,
    value: str,
) -> RuntimeConfig:
    if key == "profile":
        return replace(config, profile=Profile(value))
    if key == "asr-backend":
        return replace(config, asr_backend=ASRBackend(value))
    if key == "clip-encoder":
        return replace(config, clip_encoder=ClipEncoder(value))
    if key.startswith("feature."):
        try:
            feature = Feature(key.removeprefix("feature."))
        except ValueError:
            raise ValueError(f"알 수 없는 기능 설정: {key}") from None
        normalized = value.lower()
        if normalized not in {"on", "off"}:
            raise ValueError("기능 값은 on 또는 off여야 합니다")
        features = dict(config.features)
        features[feature] = normalized == "on"
        return replace(config, features=features)
    raise ValueError(
        "설정 키는 profile, asr-backend, clip-encoder, feature.<name> 중 "
        "하나여야 합니다"
    )


def resolve_asr_backend(
    config: RuntimeConfig,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> ASRBackend:
    if config.asr_backend is not ASRBackend.AUTO:
        return config.asr_backend
    os_name = platform_name or sys.platform
    architecture = machine or platform.machine()
    if (
        config.profile is Profile.LOW_POWER
        and os_name == "darwin"
        and architecture == "arm64"
    ):
        return ASRBackend.MLX
    # Same-source measurement: faster-whisper retained clean output while raw
    # MLX was faster but hallucinated. Balanced and quality stay on the winner.
    return ASRBackend.FASTER_WHISPER


def resolve_clip_encoder(
    config: RuntimeConfig,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> ClipEncoder:
    if config.clip_encoder is not ClipEncoder.AUTO:
        return config.clip_encoder
    os_name = platform_name or sys.platform
    architecture = machine or platform.machine()
    if (
        config.profile is Profile.LOW_POWER
        and os_name == "darwin"
        and architecture == "arm64"
    ):
        return ClipEncoder.HEVC_VIDEOTOOLBOX
    # Software was faster in the measured delivery encode; VideoToolbox is the
    # explicit low-power/concurrent-work choice.
    return ClipEncoder.SOFTWARE


def feature_enabled(feature: Feature, config: RuntimeConfig | None = None) -> bool:
    active = config or load_runtime_config()
    return active.features.get(feature, True)
