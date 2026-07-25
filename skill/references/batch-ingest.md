# Batch ingest dispatch contract

Use this contract for three or more videos when the current harness can run independent workers. Parallelize only ingest and deterministic signal extraction; keep hypothesis formation, visual verification, checkpoint writes, and final selection in the main agent so judgments remain consistent.

- Codex: use the available multi-agent/subagent tool with one video per worker.
- Claude Code: use the Agent tool, or the repository's `.claude/workflows/tca-batch-ingest.js` adapter.
- No worker tool: run the same contract sequentially in the main agent.

## Worker prompt

```text
[objective]
영상 1편의 ingest+신호 추출만 수행하고 브리핑을 반환하라. 이해·판정은 하지 마라.

[scope]
대상: {video_path_or_url}
워크스페이스: {absolute_workspace}
  # URL이면 -o 필수(재개 경로 고정), 로컬 파일이면 CWD의 ./va-out/<stem> 기본값
명령:
  - {ws}/manifest.json이 이미 있으면 재-ingest 금지 — `va brief {ws}`만 실행
  - 없으면 `va ingest "{video}" --model small --signals [-o {ws}]`

[acceptance]
- {ws}/manifest.json + transcript.json 존재 (명령 exit 0)
- `va brief {ws}` 출력 전문을 반환에 포함

[boundaries]
- 프레임 캡처·필름스트립·체크포인트 기록·클립 추출 금지 (메인 몫)
- 배정된 워크스페이스 밖 접근 금지
- glossary 갱신 금지 (세션 끝에 메인이 일괄)

[return — JSON 한 개]
{"workspace": "<절대경로>", "duration_s": <float>, "mode": "<brief의 mode 추천>",
 "chapters": <int>, "speech_ratio": <float>, "brief_text": "<va brief 출력 전문>",
 "error": null | "<실패 원인 — 추측 금지, 실제 stderr 근거>"}
```

The main agent uses the returned `brief_text` values to prioritize videos, then resumes each analysis with `va brief <ws>`.
