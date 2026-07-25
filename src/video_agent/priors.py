"""Source-supplied priors — what is knowable before a single frame decodes.

Every ingest starts by throwing away free information. The uploader already
typed the team names, the competition, the people; a local file already
carries a filename and container tags. ASR then hears those same words for
the first time and guesses. Measured on the 2026-07-25 football clip: the
title spelled both club names out in full, whisper wrote a different
mishearing of each, and the hotword list handed to it held 29 unrelated
terms harvested from other videos and **none of the four correct ones**.

Two distinct jobs come out of this module, and they must not be confused:

**Priors as hotwords.** Terms lifted from this video's own metadata are
per-video by construction, so they do not carry the precision problem the
shared glossary does (0.448, measured 2026-07-25). A term from *this* title
is relevant to *this* audio by definition; a term from another workspace's
corrections is a guess. Priors therefore rank above glossary terms.

**Priors as briefing.** Title, uploader and description let the agent form a
content expectation before reading a transcript at all — cheaper than any
frame and often more reliable than a low-confidence ASR span.

What this module does NOT do: treat the description as fact. Uploaders write
promotion, timestamps, sponsor links and boilerplate. Extraction here is a
*candidate* generator for decoder biasing, not a source of checkpoints.
"""

from __future__ import annotations

import re
from pathlib import Path

# 설명란 상용구 — 링크·타임스탬프·구독 유도는 내용어가 아니다.
_NOISE = re.compile(
    r"https?://\S+|@\S+|#\S+|\b\d{1,2}:\d{2}(?::\d{2})?\b|[▶►★☆♥♡]"
)
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
# 대괄호/괄호 안 머리표(리그 표기 등)는 유지하되 기호만 턴다.
_STRIP = str.maketrans({c: " " for c in "[]()<>{}|·,、/\\\"'“”‘’!?~*_"})
_STOP = {
    "하이라이트", "경기", "영상", "채널", "구독", "좋아요", "알림", "설정",
    "공식", "풀버전", "다시보기", "리그", "라운드", "개막전", "시즌",
    "highlights", "official", "full", "match", "video", "channel",
    "subscribe", "season", "round", "league", "vs", "the", "and", "for",
}
_MAX_TERMS = 12  # 프롬프트 앞자리를 프라이어가 독식하지 않게 하는 상한
# 필드별 지분 — 상한을 한 덩어리로 두면 긴 설명란이 그것을 다 먹고 제목이
# 한 단어도 못 들어간다(설명란이 12어 이상인 흔한 경우에 우선순위가 정확히
# 뒤집힌다). 가장 정제된 필드부터 자리를 확보한다.
_FIELD_SHARE = (("description", 4), ("uploader", 2), ("title", 6))


def _clean(text: str) -> str:
    return _NOISE.sub(" ", text or "").translate(_STRIP)


def extract_prior_terms(*sources: str, cap: int = _MAX_TERMS) -> list[str]:
    """Content-word candidates from title/uploader/description text.

    Order is first-seen, so the title's terms lead — a title is curated and a
    description is not. Stopwords cover the boilerplate that would otherwise
    dominate ("하이라이트", "구독", "official"), and pure numbers are dropped
    since a decoder gains nothing from biasing toward "2026".
    """
    seen: dict[str, None] = {}
    for source in sources:
        for token in _TOKEN.findall(_clean(source)):
            if token.isdigit() or token.lower() in _STOP:
                continue
            seen.setdefault(token, None)
    return list(seen)[:cap]


def read_source_priors(ws_root: Path) -> dict[str, str]:
    """Consume the sidecars yt-dlp wrote, deleting them once folded in.

    Kept as sidecars only between download and manifest write — the manifest
    is the durable home, and leaving stray text files in a workspace invites
    them being mistaken for artifacts.
    """
    out: dict[str, str] = {}
    for field, name in (
        ("title", "title.txt"),
        ("uploader", "uploader.txt"),
        ("description", "description.txt"),
    ):
        path = Path(ws_root) / name
        if not path.is_file():
            continue
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        path.unlink(missing_ok=True)
        # yt-dlp는 값이 없으면 "NA"를 찍는다.
        if value and value != "NA":
            out[field] = value
    return out


def local_priors(video: Path) -> dict[str, str]:
    """Priors for a local file: the filename is the only reliable one.

    Container title/artist tags exist but are usually the encoder's leftovers
    ("Untitled", the muxer name), so only the stem is taken. Separators become
    spaces because "2026-cup-final_A.mp4" carries three words, not one.
    """
    stem = Path(video).stem
    # 숨김 파일(".mp4")은 stem이 확장자 자체가 된다 — 제목이 아니다.
    if not stem or stem.startswith("."):
        return {}
    title = re.sub(r"[._\-]+", " ", stem).strip()
    return {"title": title} if title else {}


def prior_hotwords(manifest: dict, cap: int = _MAX_TERMS) -> str:
    """Hotword string from a manifest's stored priors, highest value last.

    Whisper keeps the tail of its prompt when it truncates, so fields are fed
    least-curated first: description, then uploader, then title. Each field
    holds a reserved share, claimed most-curated first so a duplicated term
    belongs to the better field, and any trimming happens at the head — the
    title must survive both the share split and the cap.
    """
    claimed: dict[str, list[str]] = {}
    seen: set[str] = set()

    def candidates(name: str) -> list[str]:
        return [
            t for t in extract_prior_terms(manifest.get(name, ""), cap=cap)
            if t not in seen
        ]

    # 1차: 각 필드가 자기 지분을 확보한다(정제도 높은 필드부터).
    for name, share in reversed(_FIELD_SHARE):
        terms = candidates(name)[:share]
        seen.update(terms)
        claimed[name] = terms
    # 2차: 지분은 경쟁이 있을 때의 보장선이지 상한이 아니다 — 빈 필드가
    # 남긴 자리는 정제도 높은 필드가 이어받는다.
    for name, _ in reversed(_FIELD_SHARE):
        room = cap - sum(len(v) for v in claimed.values())
        if room <= 0:
            break
        extra = candidates(name)[:room]
        seen.update(extra)
        claimed[name] += extra

    ordered = [t for name, _ in _FIELD_SHARE for t in claimed[name]]
    return " ".join(ordered[-cap:])
