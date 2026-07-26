# Contributing

Thanks for your interest in TIMECODE-AGENT.

## How this repository works

This public repository is an **assembled snapshot** of a private development
repository — releases land here as reviewed snapshot commits, not as a live
development history. That shapes how contributions flow:

- **Issues and discussions are the primary channel.** Bug reports with a
  reproducible command line, the video characteristics (duration, container,
  frame rate, caption availability — never the media itself), and the full
  stderr output are the most actionable.
- **Pull requests are welcome as proposals.** They are reviewed here, then
  re-landed through the development flow with credit in the commit message,
  rather than merged directly. Small, focused diffs with tests travel best.

## Local development

```bash
git clone https://github.com/mupozg823/timecode-agent.git && cd timecode-agent
uv sync
uv run pytest            # full suite
uv run ruff check src tests
uv run basedpyright src/video_agent
```

Requirements: Python 3.12, `ffmpeg`/`ffprobe` on PATH (`yt-dlp` only for URL
ingest). macOS gets the bundled OCR/faces/sound-event extras; Linux is
supported; Windows is experimental.

## Ground rules

- Every behavior claim in docs must match `--help` output and shipped code —
  CI gates enforce the README/`docs/public` byte mirror and documented-command
  existence in both directions.
- Ledgers are append-only; projections must stay rebuildable. A change that
  makes a derived surface authoritative will be declined.
- Benchmarks quote only reproducible numbers (`benchmarks/run_bench.py
  --set public --fetch` on CC-BY Blender fixtures).
