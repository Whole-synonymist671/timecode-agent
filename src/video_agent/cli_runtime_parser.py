"""Argparse surface for persistent runtime policy."""

from __future__ import annotations

import argparse

from . import cli_commands_global as global_commands
from .runtime_config import ASRBackend, ClipEncoder, Profile


def add_runtime_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "runtime",
        help="all-on 기능 상태, Apple 실행 프로필, 백엔드 설정 및 모델 준비",
    )
    actions = parser.add_subparsers(dest="runtime_action", required=True)

    status = actions.add_parser("status", help="설치·활성화·선택 정책 표시")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=global_commands.cmd_runtime)

    setting = actions.add_parser("set", help="프로필·백엔드·기능 on/off 저장")
    setting.add_argument(
        "key",
        help=(
            "profile | asr-backend | clip-encoder | "
            "feature.asr|ocr|faces|audioevents|diarize|otio"
        ),
    )
    setting.add_argument(
        "value",
        help=(
            "profile: "
            + "|".join(profile.value for profile in Profile)
            + "; asr: "
            + "|".join(backend.value for backend in ASRBackend)
            + "; clip: "
            + "|".join(encoder.value for encoder in ClipEncoder)
            + "; feature: on|off"
        ),
    )
    setting.set_defaults(func=global_commands.cmd_runtime)

    reset = actions.add_parser("reset", help="all-on balanced 기본값으로 복원")
    reset.set_defaults(func=global_commands.cmd_runtime)

    prepare = actions.add_parser(
        "prepare",
        help="Whisper·MLX·sherpa 기본 모델을 지금 내려받아 오프라인 준비",
    )
    prepare.add_argument("--json", action="store_true")
    prepare.set_defaults(func=global_commands.cmd_runtime)
