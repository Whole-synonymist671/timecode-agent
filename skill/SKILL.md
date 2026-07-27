---
name: timecode-agent
description: Use when a user provides a local file or video URL for timestamped video understanding, editing handoff, or reusable indexing; or asks to recall a scene, speaker, quote, event, or evidence from an existing video-agent corpus (기존 영상 기억·장면·화자·대사 검색), even when no new video is supplied. Do not use for a quick one-shot summary that needs no reusable index or editing output.
license: MIT
---

# TIMECODE-AGENT

Hypothesize from the transcript first, verify only the uncertain moments
visually, then persist the result as checkpoints. Never capture every frame;
state the hypothesis each capture tests. Never promote a number measured on one
device or dataset into a universal rule of this skill.

## References, read only when needed

| Situation | Reference |
|---|---|
| OCR, diarization, faces, scene false positives, precise visual checks | [Verification toolbox](references/verification-toolbox.md) |
| Highlight scoring, boundaries, reframe, NLE handoff | [Output handoff](references/output-handoff.md) |
| Resume, search, glossary, wiki, storage | [Corpus lifecycle](references/corpus-lifecycle.md) |
| Wiki labels, relations, narrative, queries | [Wiki schema](references/wiki-schema.md) |
| Ingesting 3 or more videos | [Batch contract](references/batch-ingest.md) |
| Harnesses other than Claude Code | [Harness matrix](references/harness-matrix.md) |
| Install, backends, profiles, cache | [Runtime setup](references/runtime-setup.md) |

## Request to loop routing

Fold the user's words into a loop first. Step numbers come after.

| User says | Loop | Entry |
|---|---|---|
| new video, understand, analyze, "what is this" | **Ingest** (develop) then **Kubrick** (understand) | step 0: `va ingest --signals`, then `va brief` |
| highlight, shorts, "cut this" | Kubrick, then the cut workflow of **Kuleshov** (edit) | a named span converges on its own (step 4); open-ended discovery waits for global convergence |
| "where was that scene", recall | **Search** | `va search` — **never re-ingest** |
| Premiere, editor handoff, markers | Bridge | `va export` in step 5 |
| listing, wiki, browser, external vault refresh | **Archive** (project knowledge) | `va index`, `va wiki`, `va view`, `va bridge` |

Pipeline: ingest (develop), search (recall), Kubrick (understand), Kuleshov
(edit), cut (assemble), archive (preserve).

Kubrick writes the fact ledger (`checkpoints.jsonl`); Kuleshov writes the edit
decision ledger (`sequences.jsonl`). **Kuleshov never modifies the fact
ledger.** Correct a factual error in the checkpoint, then derive the edit again.

## Requirements

- Use Python 3.12 on macOS or Linux. Windows is experimental support — details
  in [Runtime setup](references/runtime-setup.md).
- Full mode needs a local image-input tool and an image-capable model. If
  either is missing, run the Degraded mode below.

```bash
command -v va
command -v ffmpeg
command -v ffprobe
```

Check this only when the input is an HTTP(S) URL:

```bash
command -v yt-dlp
```

Every feature starts on. Read [Runtime setup](references/runtime-setup.md) only
for install steps, backend prep, run profiles, URL cookies, or cache locations.
A backend import failure is not a missing optional feature — repair it instead
of degrading.

## Loop steps

### 0. ingest — audio analysis to timestamped transcript plus signal batch

```bash
va ingest "<video-path>" --model small --signals
va ingest "<URL>" --signals -o "<absolute-workspace>"
```

Default local workspace is `./va-out/<stem>` under the CWD; URL default is
`./va-out/url-<md5-prefix>`. Always pass an absolute `-o` for URLs so the resume
path stays knowable. If `manifest.json` exists, resume with `va brief <ws>`;
`va ingest` enforces this and also rejects manifest-less directories containing
durable evidence. For URLs prefer the fastest source: manual subtitles >
uploader auto-captions (original language) > whisper. For timestamp precision,
choose `--force-whisper` **on the first ingest** or a new workspace (different
`-o`). A new source or transcript generation always gets a fresh workspace so
old evidence cannot be rebound to it.

### 0-1. Content mode — fix the signal weighting (dogfooding lesson)

The `va brief` recommendation is a placement signal. Confirm the mode against
transcript, highlights, and scenes. **A low `transcript_coverage` can mean a
dead transcript rather than a quiet video** — check before asserting, and read
[Verification toolbox](references/verification-toolbox.md) for the response.

| Mode | Tell | Primary signals |
|---|---|---|
| Speech-driven | dense speech | transcript plus bursts |
| Visual-driven | sparse speech, many bursts and cuts | overview, bursts, scene changes |
| Silent | transcript thin or absent | full overview plus scene changes |
| Text-on-screen | on-screen captions carry it | transcript plus OCR correction |
| Looping motion graphic | scenes <= 1, highlights 0 | one filmstrip pass |

For broadcast footage, check overlays, nameplates, and mic flags in the first
full-res capture. Ingest and overview exceptions are in
[Verification toolbox](references/verification-toolbox.md).

### 1. First-pass inference — transcript only (zero frames)

If the brief has `chapters:`, use those boundaries as draft spans and re-split
only chapters that contradict the transcript. If speakers matter, run
`va diarize <ws>` before any checkpoint; it advances the transcript revision
and refuses after evidence exists. Then split at meaning shifts and record.

```bash
va checkpoint add <ws> --json-file - <<'EOF'
{"id": "cp-001", "span": [0.0, 42.5], "segments": [0,1,2,3],
 "status": "hypothesized",
 "hypothesis": "MC가 퀴즈 규칙을 설명하는 오프닝. 화자 1명 추정",
 "confidence": 0.55}
EOF
```

Short entries also go in as flags — `va checkpoint add <ws> --id cp-001
--span 0 42.5 --status hypothesized --hypothesis "..."` (`--span` also reads
mm:ss, and `--status` enforces a value list). Use the heredoc above when the
body is long or full of quotes and non-ASCII text.

- ids run upward from `cp-001`; spans are in seconds.
- Cover every span so `va status <ws>` reports no gap.
- Write the hypothesis as what changed since the previous span, not a static list.

### 2. Pick verification points — where to use your eyes

Look only where one of these holds: confidence < 0.7, ASR `conf < 0.6`, a
speaker or location change, 20s or more of silence or gap, an ambiguous
referent, a change in who is on screen, a burst or scene change. Diarization
must already be complete; do not mutate the transcript at this stage.

Narrow a long unknown stretch with an overview scan before individual captures.

```bash
va filmstrip <ws> --auto
va filmstrip <ws> --auto --start 120 --end 400
```

Open tiles by absolute path and pick only what is worth a full-res look.
Confirm speaker cues and on-screen text at full-res.

- **Endcard duty**: after the overview, view the one tail frame that
  `--legible-endcard` picked.
- **Time-reference span protocol**: read the whole transcript of that span, a
  dense 1-2s overview, and the frames on both sides of the boundary.
- **Spatial direction dual-reading rule**: weigh both the camera-relative and
  the subject-relative reading. Without evidence, **default to camera (viewer)
  reference** and lower the confidence.
- **Fine detail reading**: compare **3-5 consecutive frames** instead of a
  single image.
- **Capture budget by mode**: speech-driven **<=15 frames**, visual-driven and
  silent **<=25 frames**, and **<=6 captures per round**. Over budget, use
  `va keyframes <ws> --budget N`.
- Reach for `va ocr` on UI text and `va faces` on the on-screen cast first.

Density (cell spacing no more than 7s, one tile 112s or less), the `duration-1`
fallback hazard, OCR crops, and the sharpness gate follow
[Verification toolbox](references/verification-toolbox.md).

### 2-1. Signal-based verification points (two placement signals)

```bash
va highlights <ws> --json      # audio energy burst spans (highlights.json)
va scenes <ws> --json          # scene-change timestamps, no frame extraction (scenes.json)
va audioevents <ws> --json     # learned laughter/applause/cheer/scream candidates — macOS Sound Analysis
```

Signals fix placement (when) only; selection (what) is the agent's judgment.
For silent video prefer scenes; for the editorial meaning of a burst prefer
audioevents. When all three are quiet, spend fewer captures. Read
[Verification toolbox](references/verification-toolbox.md) to separate real
scene cuts from false positives.

### 3. capture, vision double-check, checkpoint update

```bash
va capture <ws> -t 18.2 -t 95.0 --reason "burst-95s"   # 1-2 frames per span under test
```

Leave the triggering signal in `--reason`. Open frames by absolute path.
Compare with the hypothesis: on a match mark `verified`, on a miss mark
`corrected` and record the difference. Reusing an id creates a new revision.
`visual_evidence` is relative to `<ws>`. Support conditions follow
[Verification toolbox](references/verification-toolbox.md).

**Write human-facing fields in human words, in the user's own language.**
`hypothesis`, `situation`, `note`, and `--reason` are read verbatim by
non-developers in the vault. Write what you saw on screen; never substitute tool
names or internal metrics (`full-res`, `span`, `score`, `OCR`, `burst`) — not
"matches the peak burst span (1195-1208 score 163)" but "overlaps the moment the
room gets loudest". The renderer softens some of this, but the ledger is
canonical, so write human words from the start.

### 4. Convergence — self-answer then reflect (VideoAgent ECCV'24 pattern)

Answer from the current index first, then separate the claims that would change
the conclusion from the evidence you lack. Never finish on the model's own
confidence. Finish when the core claims have support you can resolve now, no
related audit warning or unresolved contradiction remains (or the answer names
it), and further observation is unlikely to change the conclusion.

```bash
va status <ws> --json    # covered_ratio == 1.0, no gap, verified_ratio >= 0.6 as the secondary bar
```

`readiness` is a secondary stop signal. Read audit warnings with
`va audit <roots>`: keep going while warnings or evidence gaps remain even at
`converged`. If the budget runs out first, do not force a promotion — state the
`provisional` or unresolved scope in the answer.

**Edit-scope convergence**: only for edit requests with a **named** span or
event. Enter once the whole cut span is covered by terminal plus support (the
gate enforces it), even without global convergence. Open-ended highlight
discovery needs global convergence over the candidate comparison range first.
Details in [Output handoff](references/output-handoff.md).

### 5. Output — understanding, editing, processing

- Cite checkpoint and transcript timestamps in answers. The first route per
  question type follows [Wiki schema](references/wiki-schema.md).
- For highlight and shorts requests, verify the candidates, then offer 2-3
  options with different intent. Scoring, boundaries, and reframe are in
  [Output handoff](references/output-handoff.md).
- Clip: `va clip <ws> --start 1:23 --end 2:05 --accurate`. The `low-power`
  profile, or an explicit `clip-encoder` setting, uses VideoToolbox plus
  AudioToolbox on macOS. Keep software-precise encoding for the `balanced`
  default and for final delivery — deliver without `--hw`, and keep a one-off
  `--hw` for previews and low-power work.
- Cut boundaries: run `va boundary-eval <ws> --sequence <id>`, then open the
  frames on both sides of the boundary and re-snap to a word or segment
  boundary if it is clipped.
- Handoff: `va export <ws> --format xml|otio|fcpxml|srt|edl|md`. For CapCut use
  srt only; manipulating the unofficial draft JSON is forbidden. In
  `--ids cp-004,cp-007` the **listed order is the cut order**. Revision-bound
  NLE handoff requires complete decoded-CFR proof; VFR, unknown, or irregular
  sources reject it. Use a decoded-CFR delivery source or text handoff.
- Record a reusable edit plan with `va sequence add <ws> --json-file -` and hand
  it off with `va export <ws> --format otio --sequence seq-001`. An edit plan
  never modifies the fact ledger.

## Batch and corpus rules

- Find with `va search "<query>"` and open the hit workspace with `va brief`.
- At session end run `va index && va wiki`; check space with `va gc` in report
  mode.
- Corrections, glossary, active wiki, and deletion scope: read
  [Corpus lifecycle](references/corpus-lifecycle.md).
- At 3 or more videos, parallelize ingest and signals only, per
  [Batch contract](references/batch-ingest.md). Hypothesis, verification, and
  judgment stay sequential in the main agent.

## Harness branching (per runtime)

The full loop needs both a tool that opens local images as model input and an
image-capable model. When unsure, start degraded and promote after the first
successful read. Never hardcode a tool name; use the current harness equivalent
(Claude Code: `Read`, Codex: `view_image`). For any other harness read
[Harness matrix](references/harness-matrix.md).

**Degraded mode (no vision)**: use only OCR, faces, audioevents, highlights,
scenes, and diarize results, and hold `hypothesized` plus confidence <= 0.7
except for hard OCR evidence. Separate what someone said from what the video
shows; when speech is the only evidence, write it as a speaker's claim.

## Cautions

- JSON bodies mix quotes and non-ASCII text, so default to `--json-file -` with
  a heredoc (it prevents shell escaping accidents).
- The capture cache reduces regenerating a frame at the same timestamp. Image
  opening and vision inference cost is separate.
- Label people only from on-screen visual cues (captions, badges, position),
  and never guess a real name.
