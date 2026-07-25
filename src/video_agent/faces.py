"""Face-count signal via macOS Vision (VNDetectFaceRectanglesRequest).

Cast-composition signal: a change in on-screen face count across sampled
timestamps marks entrances/exits/two-shot cuts — a speaker-separation aid.
Labels stay visual-only (no identity guessing). The macOS Vision backend is
installed by default.
"""

from __future__ import annotations

from .capture import capture_frames
from .workspace import Workspace


def _detect_faces(image_path: str) -> list[list[float]]:
    """[[x, y, w, h] normalized] for each detected face."""
    try:
        import Quartz  # pyright: ignore[reportMissingImports]
        import Vision  # pyright: ignore[reportMissingImports]
        from Foundation import (  # pyright: ignore[reportMissingImports]
            NSURL,  # pyright: ignore[reportAttributeAccessIssue]
        )
    except ImportError:
        raise RuntimeError(
            "Vision face backend unavailable — macOS installs it by default; "
            "rerun scripts/install.sh from the same release source"
        ) from None
    url = NSURL.fileURLWithPath_(image_path)
    create_source = Quartz.CGImageSourceCreateWithURL  # pyright: ignore[reportAttributeAccessIssue]
    create_image = Quartz.CGImageSourceCreateImageAtIndex  # pyright: ignore[reportAttributeAccessIssue]
    request_type = Vision.VNDetectFaceRectanglesRequest  # pyright: ignore[reportAttributeAccessIssue]
    handler_type = Vision.VNImageRequestHandler  # pyright: ignore[reportAttributeAccessIssue]
    src = create_source(url, None)
    image = create_image(src, 0, None)
    request = request_type.alloc().init()
    handler = handler_type.alloc().initWithCGImage_options_(
        image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision face detection failed: {err}")
    boxes = []
    for obs in request.results() or []:
        bb = obs.boundingBox()
        origin, size = (bb[0], bb[1]) if isinstance(bb, tuple) else (
            bb.origin, bb.size)
        x = origin[0] if isinstance(origin, tuple) else origin.x
        y = origin[1] if isinstance(origin, tuple) else origin.y
        w = size[0] if isinstance(size, tuple) else size.width
        h = size[1] if isinstance(size, tuple) else size.height
        boxes.append([round(v, 4) for v in (x, y, w, h)])
    return boxes


# 숏 스케일 밴드(정규화 얼굴 면적비) — 파일럿 실측 경계:
# 애니 클로즈업 0.056 / 데스크 미디엄 0.041·0.017
_CLOSEUP_AREA = 0.05
_MEDIUM_AREA = 0.008


def shot_scale(boxes: list[list[float]]) -> str | None:
    """최대 얼굴 면적비 → 클로즈업/미디엄/롱.

    검출 0이면 None — 후면·가림·원거리에서 얼굴이 안 잡히므로
    '롱샷'과 '얼굴 없음'을 구분할 수 없다(수치는 하한 규약과 동일).
    """
    if not boxes:
        return None
    area = max(b[2] * b[3] for b in boxes)
    if area >= _CLOSEUP_AREA:
        return "클로즈업"
    if area >= _MEDIUM_AREA:
        return "미디엄"
    return "롱"


def count_faces(
    ws: Workspace, ts: list[float], crop: str | None = None
) -> list[dict]:
    """[{t, count, boxes, scale}] per timestamp (frames cached via capture)."""
    results = []
    for t in ts:
        (frame,) = capture_frames(ws, [t], crop=crop, reason="faces-count")
        boxes = _detect_faces(str(frame))
        results.append({"t": round(t, 2), "count": len(boxes),
                        "boxes": boxes, "scale": shot_scale(boxes)})
    return results
