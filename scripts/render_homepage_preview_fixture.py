from __future__ import annotations

import argparse
import json
from pathlib import Path

from video_agent.view import build_view

WORKSPACES = (
    {
        "name": "interview",
        "duration": 612.0,
        "transcript": [
            {
                "id": "seg-001",
                "start": 12.0,
                "end": 18.0,
                "text": "A durable video memory begins with an event.",
            }
        ],
        "checkpoints": [
            {
                "id": "cp-001",
                "span": [12.0, 48.0],
                "status": "verified",
                "hypothesis": "The speaker defines event-based recall.",
                "confidence": 0.92,
                "segments": ["seg-001"],
            }
        ],
    },
    {
        "name": "field-notes",
        "duration": 388.0,
        "transcript": [],
        "checkpoints": [
            {
                "id": "cp-001",
                "span": [0.0, 35.0],
                "status": "hypothesized",
                "hypothesis": "Opening establishes the location.",
                "confidence": 0.61,
            }
        ],
    },
)


def build_demo(destination: Path) -> Path:
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    corpus = destination / "va-out"
    for item in WORKSPACES:
        name = str(item["name"])
        workspace = corpus / name
        workspace.mkdir(parents=True)
        media = workspace / "media" / f"{name}.mp4"
        manifest = {
            "video": str(media),
            "duration": float(item["duration"]),
        }
        workspace.joinpath("manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        workspace.joinpath("transcript.json").write_text(
            json.dumps(item["transcript"], ensure_ascii=False),
            encoding="utf-8",
        )
        checkpoints = item["checkpoints"]
        assert isinstance(checkpoints, list)
        workspace.joinpath("checkpoints.jsonl").write_text(
            "".join(
                json.dumps(checkpoint, ensure_ascii=False) + "\n"
                for checkpoint in checkpoints
            ),
            encoding="utf-8",
        )
    view, count = build_view([str(corpus)])
    if count != len(WORKSPACES):
        raise RuntimeError(
            f"expected {len(WORKSPACES)} workspaces, rendered {count}"
        )
    return view


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_demo(args.output))


if __name__ == "__main__":
    main()
