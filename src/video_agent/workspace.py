"""Per-video workspace: manifest, transcript, frames cache, checkpoints, clips."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .fsio import write_text_atomic
from .probe import probe

# 파일시스템·Obsidian 양쪽에서 문제를 일으키는 문자. `[]#^|`는 경로로는
# 허용되지만 위키 링크 문법과 충돌한다.
_UNSAFE_STEM = re.compile(r'[/\\:*?"<>|\[\]#^\x00-\x1f]+')
_DOC_STEM_MAX_CHARS = 80
_FILE_COMPONENT_MAX_BYTES = 255
_LONGEST_DOC_SUFFIX = "-images.md"
_DOC_STEM_MAX_BYTES = (
    _FILE_COMPONENT_MAX_BYTES - len(_LONGEST_DOC_SUFFIX.encode("utf-8"))
)


def doc_stem_from_title(title: str) -> str:
    """Filesystem- and Obsidian-safe stem from a video title, or "" if none.

    Truncation happens on a word boundary when one is near the limit so the
    name does not end mid-word; the result is deterministic for a given title
    so repeated builds keep writing to the same file.
    """
    stem = _UNSAFE_STEM.sub(" ", title or "")
    # 마침표를 떼면 그 앞 공백이 다시 노출되므로 양쪽을 함께 벗긴다
    # (".../제목 ." → "제목"). 뒤따르는 점은 일부 파일시스템에서 잘린다.
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if (
        len(stem) <= _DOC_STEM_MAX_CHARS
        and len(stem.encode("utf-8")) <= _DOC_STEM_MAX_BYTES
    ):
        return stem
    cut = stem[:_DOC_STEM_MAX_CHARS]
    while len(cut.encode("utf-8")) > _DOC_STEM_MAX_BYTES:
        cut = cut[:-1]
    head, sep, _ = cut.rpartition(" ")
    return (head if sep and len(head) >= len(cut) // 2 else cut).strip()


def load_json(path):
    """Best-effort JSON read: None on missing/corrupt — derived layers
    (brief/export/index/wiki/view) must never crash on a partial workspace."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)

    manifest_path = property(lambda self: self.root / "manifest.json")
    transcript_path = property(lambda self: self.root / "transcript.json")
    srt_path = property(lambda self: self.root / "transcript.srt")
    frames_dir = property(lambda self: self.root / "frames")
    checkpoints_path = property(lambda self: self.root / "checkpoints.jsonl")
    checkpoint_lock_path = property(lambda self: self.root / ".checkpoint.lock")
    image_provenance_lock_path = property(
        lambda self: self.root / ".image-provenance.lock"
    )
    sequence_lock_path = property(lambda self: self.root / ".sequences.lock")
    workspace_lock_path = property(lambda self: self.root / ".workspace.lock")
    clips_dir = property(lambda self: self.root / "clips")

    @property
    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())

    @property
    def doc_stem(self) -> str:
        """Base filename for this workspace's markdown, from the video title.

        Obsidian shows the filename, not frontmatter, so a URL-derived
        directory name (`yt-<video-id>`) leaves the vault unreadable — the
        one place the title matters most is the one place it was not used.
        Falls back to the directory name when there is no usable title, and
        stays deterministic so rebuilds do not churn filenames.
        """
        try:
            title = str(self.manifest.get("title") or "").strip()
        except (OSError, ValueError):
            title = ""
        return doc_stem_from_title(title) or self.root.name

    def save_manifest(self, data: dict) -> None:
        write_text_atomic(
            self.manifest_path, json.dumps(data, ensure_ascii=False, indent=2)
        )

    def stamp_tool(self, name: str, info: str) -> None:
        """P1(학습 기반 지각) 도구의 모델·백엔드를 manifest에 남긴다.

        지각 출력은 물리 측정이 아니라 모델 산물이라 버전 의존 —
        어떤 도구가 만든 신호인지 없으면 재현·감사가 불가하다.
        """
        manifest = self.manifest
        tools = dict(manifest.get("tools") or {})
        if tools.get(name) == info:
            return
        tools[name] = info
        manifest["tools"] = tools
        self.save_manifest(manifest)

    @property
    def video(self) -> Path:
        return Path(self.manifest["video"])

    @classmethod
    def create(cls, video: Path, out: Path | None = None) -> "Workspace":
        video = Path(video).resolve()
        root = Path(out) if out else Path.cwd() / "va-out" / video.stem
        ws = cls(root)
        published = ws.manifest_path.is_file()
        ws.frames_dir.mkdir(parents=True, exist_ok=True)
        ws.clips_dir.mkdir(parents=True, exist_ok=True)
        meta = probe(video)
        if not published:
            ws.workspace_lock_path.touch(mode=0o600, exist_ok=True)
            ws.checkpoint_lock_path.touch(mode=0o600, exist_ok=True)
            ws.image_provenance_lock_path.touch(mode=0o600, exist_ok=True)
            ws.sequence_lock_path.touch(mode=0o600, exist_ok=True)
        ws.save_manifest(
            {
                "video": str(video),
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **meta,
            }
        )
        return ws

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        ws = cls(Path(root))
        if not ws.manifest_path.is_file():
            raise FileNotFoundError(
                f"va 워크스페이스가 아닙니다 (manifest.json 없음): {ws.root}"
                " — 경로를 확인하거나 `va ingest`로 먼저 만드십시오"
            )
        return ws
