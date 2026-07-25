from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps

CANVAS = (1280, 640)
BACKGROUND = (15, 16, 15, 255)


def render_social_preview(source: Path, destination: Path) -> None:
    with Image.open(source) as opened:
        artwork = ImageOps.contain(
            opened.convert("RGBA"),
            CANVAS,
            Image.Resampling.LANCZOS,
        )
    canvas = Image.new("RGBA", CANVAS, BACKGROUND)
    position = (
        (CANVAS[0] - artwork.width) // 2,
        (CANVAS[1] - artwork.height) // 2,
    )
    canvas.alpha_composite(artwork, position)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(destination, format="PNG", optimize=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render_social_preview(args.source, args.output)


if __name__ == "__main__":
    main()
