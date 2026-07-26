# Batch ingest dispatch contract

Use this contract for three or more videos when the current harness can run independent workers. Parallelize only ingest and deterministic signal extraction; keep hypothesis formation, visual verification, checkpoint writes, and final selection in the main agent so judgments remain consistent.

- Codex: use the available multi-agent/subagent tool with one video per worker.
- Claude Code: use the Agent tool, or the repository's `.claude/workflows/tca-batch-ingest.js` adapter.
- No worker tool: run the same contract sequentially in the main agent.

## Worker prompt

```text
[objective]
Ingest one video, extract signals, and return its brief. Do not interpret it.

[scope]
source: {video_path_or_url}
workspace: {absolute_workspace}
  # URLs require -o for a stable resume path. Local files default to
  # CWD/va-out/<stem>.
commands:
  - If {ws}/manifest.json exists, never re-ingest; run only `va brief {ws}`.
  - Otherwise run `va ingest "{video}" --model small --signals [-o {ws}]`.

[acceptance]
- {ws}/manifest.json and transcript.json exist after an exit-0 command.
- Return the complete `va brief {ws}` output.

[boundaries]
- No capture, filmstrip, checkpoint writes, or clip extraction.
- Do not access paths outside the assigned workspace.
- Do not update the glossary; the main agent batches that at session end.

[return: one JSON object]
{"workspace": "<absolute path>", "duration_s": <float>,
 "mode": "<mode recommended by brief>", "chapters": <int>,
 "speech_ratio": <float>, "brief_text": "<complete va brief output>",
 "error": null | "<observed failure from stderr; no guesses>"}
```

The main agent uses the returned `brief_text` values to prioritize videos, then resumes each analysis with `va brief <ws>`.
