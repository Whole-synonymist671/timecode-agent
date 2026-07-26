# Harness capability matrix

Check capabilities, not harness names:

1. Can a tool pass an absolute local image path into the model?
2. Can the selected model interpret image input?

If either answer is unknown, start degraded. Follow the current harness docs for
installation and tool names; never assume another harness's discovery path.

| Environment | Known image tool | Gate |
|---|---|---|
| Claude Code | `Read` | Prove the first frame reaches the model |
| Codex | `view_image` | `agents/openai.yaml` is metadata, not a tool grant |
| Other Agent Skills harness | Harness-local tool | Install the full skill in its documented path; prove the first frame |
| No image tool or vision model | None | **Force degraded mode**; use transcript and P0/P1 signals only |

## Shared execution rules

- **Frames and tiles:** pass absolute paths. Relative paths depend on session CWD.
- **Long ingest:** run minute-scale `va ingest` in the background; meanwhile use
  existing workspaces via `va brief` or `va search`.
- **Three or more videos:** follow the [batch contract](batch-ingest.md).
  Parallelize ingest and deterministic signals only; keep judgment sequential.
- **Progress state:** `va status` and `va brief` expose ledger gaps. Do not keep a
  competing task list.

## Model or proxy changes

A new model or API proxy can drop image passthrough even within the same
harness. A successful tool call does not prove visual interpretation. Promote
to the full loop only after the changed model or endpoint reads one frame.
