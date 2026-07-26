"""ffprobe wrapper: duration/size/fps for a video file."""

from __future__ import annotations

import json
from pathlib import Path

from .proc import run


def probe(video: Path) -> dict:
    video = Path(video)
    if not video.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    stat_before = video.stat()
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-show_chapters", str(video),
    ]
    res = run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({res.returncode}): {res.stderr.strip()}")
    data = json.loads(res.stdout)
    vstream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if vstream is None:
        raise RuntimeError(f"no video stream in {video}")
    num, _, den = vstream.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    # 스트림 타이밍 원문 보존 — float fps만 남기면 NTSC(30000/1001)가
    # 하류(export)에서 정수 30으로 뭉개져 장편 타임코드가 드리프트한다.
    # avg≠r은 가변 프레임레이트(VFR)의 표준 휴리스틱: CFR 가정 export의
    # 프레임 정렬이 근사가 된다는 사실을 산출물 감사가 알 수 있어야 한다.
    timing = {
        key: vstream[key]
        for key in ("avg_frame_rate", "r_frame_rate", "time_base")
        if vstream.get(key)
    }
    if vstream.get("start_time") is not None:
        try:
            timing["start_time"] = float(vstream["start_time"])
        except (TypeError, ValueError):
            pass
    if vstream.get("nb_frames") is not None:
        try:
            timing["nb_frames"] = int(vstream["nb_frames"])
        except (TypeError, ValueError):
            pass
    avg = timing.get("avg_frame_rate")
    real = timing.get("r_frame_rate")
    if avg and real:
        timing["vfr"] = avg != real
    # container chapters (mp4 chapter atoms / yt-dlp --embed-chapters):
    # a free semantic pre-map — each chapter is a checkpoint draft the agent
    # gets without spending a single frame or transcript read
    chapters = [
        {
            "start": round(float(c.get("start_time", 0.0)), 3),
            "end": round(float(c.get("end_time", 0.0)), 3),
            "title": (c.get("tags") or {}).get("title", ""),
        }
        for c in data.get("chapters", [])
    ]
    meta = {
        "duration": float(data["format"]["duration"]),
        "width": int(vstream["width"]),
        "height": int(vstream["height"]),
        "fps": fps,
        "has_audio": any(
            s.get("codec_type") == "audio" for s in data.get("streams", [])
        ),
    }
    if timing:
        meta["timing"] = timing
    # 색 신호(휘도·색 히스토그램·색 교차확인)의 해석 전제 — HDR/SDR·
    # range 혼동 방지를 위해 관측 조건을 manifest에 남긴다
    color = {
        key: vstream[key]
        for key in ("pix_fmt", "color_space", "color_transfer",
                    "color_primaries", "color_range")
        if vstream.get(key)
    }
    if color:
        meta["color"] = color
    if chapters:
        meta["chapters"] = chapters
    creation = (data["format"].get("tags") or {}).get("creation_time")
    if creation:
        meta["creation_time"] = creation
    # 소스 파일 지문 — 재-ingest가 같은 파일이면 ffprobe를 건너뛰고 기존
    # 관측을 재사용할 수 있게 한다(캐시들과 같은 stat 지문 계약).
    # ffprobe 실행 중 원자 교체가 끼어들면 옛 inode의 관측에 새 파일의
    # 지문이 붙어 낡은 메타데이터가 무기한 재사용된다 — 실행 전후
    # identity(size·mtime_ns·inode)가 같을 때만 기록한다. 지문 부재는
    # 재사용 대상에서 빠져 다음 ingest가 재관측한다(자기 치유).
    try:
        stat_after = video.stat()
    except OSError:
        stat_after = None
    if stat_after is not None and (
        (stat_before.st_size, stat_before.st_mtime_ns, stat_before.st_ino)
        == (stat_after.st_size, stat_after.st_mtime_ns, stat_after.st_ino)
    ):
        meta["source_stat"] = {
            "size": stat_after.st_size, "mtime_ns": stat_after.st_mtime_ns,
        }
    return meta
