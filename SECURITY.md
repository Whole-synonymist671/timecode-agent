# Security Policy

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** on this repository
(Security tab → "Report a vulnerability"). Do not open a public issue for
security-sensitive reports.

Best-effort response; this is a solo-maintained project without a formal SLA.

## Scope

TIMECODE-AGENT is a local CLI that feeds untrusted input into media tooling,
so the interesting surface is:

- Crafted media files or URLs that escalate through the bundled `ffmpeg`/
  `ffprobe`/`yt-dlp` invocations (argument injection, path traversal via
  container metadata, subtitle/caption payloads).
- Untrusted strings from media metadata or transcripts landing in generated
  HTML (`va view`), Markdown, or NLE export formats in a way that executes
  or injects on the consuming side.
- Workspace ledger parsing (`checkpoints.jsonl`, `sequences.jsonl`) breaking
  the append-only or fail-closed guarantees when handed hostile files.

Out of scope: vulnerabilities in `ffmpeg`, `yt-dlp`, or model backends
themselves (report upstream), and attacks requiring control of the local
user account.

## Supported versions

Only the latest tagged release and `main` receive fixes.
