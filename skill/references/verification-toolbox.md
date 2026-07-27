# Verification toolbox

Deep reference for loop §2. `SKILL.md` owns triggers and hard limits; this file
owns backend details, examples, and interpretation.

## Transcript trust before mode selection

Sparse text alone cannot distinguish a quiet video from a stalled decode. Use
manifest evidence:

- `transcript_coverage`: transcribed speech divided by VAD speech. Below 0.5
  with at least 60 seconds of speech signals collapse. Short audio is exempt;
  one sentence dominates its ratio. Never infer “visual-led” from a collapsed
  transcript. Disclose unresolved low coverage instead of forcing a mode.
- `transcript_repair`: automatic recovery ran. After `tail-retranscribe`, sample
  transcript content after the repair boundary.
- `hotwords_rejected`: glossary terms caused repetitive hallucination and were
  removed; domain-term errors may remain.
- `asr_backend`: backend provenance, including MLX fallback, for comparison and
  reproduction.

## Ingest and overview exceptions

- Empty transcript: start with 4–6 evenly spaced overview frames.
- Censored/BGM variants can confuse VAD. When an original exists, transcribe the
  original and visually inspect the variant.
- At least 10 scene changes per minute is a montage signal; prioritize scene
  boundaries.
- For `-00N` split filenames, record that the part may omit start or ending.
- Keep overview gaps at most 7 seconds and each tile at most 112 seconds.
  `va filmstrip --auto` applies this density.
- Even sampling can miss the final 1–2 seconds. Do not hardcode `duration-1`;
  inspect the readable tail chosen by
  `va keyframes <ws> --legible-endcard`.

## Time, direction, and fine detail

- Time-specific questions require the complete span transcript, a 1–2 second
  dense overview, and one frame on each boundary. For actions, answer the
  before→after change.
- For left/right, evaluate camera and subject frames. With no discriminator,
  use camera/viewer coordinates and lower confidence.
- Inspect possessions, clothing, and body state across frames 0.3–0.5 seconds
  apart. For fast or vigorous motion — sports and dancing count, not just
  impacts — use `va capture <ws> -t <t> --sharp --reason <signal>`.
- Keep each verification round to at most 6 captures. When candidates exceed
  the total budget, use `va keyframes <ws> --budget N`.

## Image provenance and support

- `--reason` appends image-ID↔cause-ID records to
  `image-provenance.jsonl`; `captures.json` is legacy input only.
- `va index` rebuilds `<workspace>-images.md` and backlinks from INDEX and scene
  checkpoints to image IDs.
- Image support requires all three: successful decode inside `frames/`, tracked
  provenance, and checkpoint-span overlap. Absolute paths, `..`, external
  symlinks, partial files, and untracked legacy files remain detail-only.
- Rejection codes:
  - `evidence_provenance_missing`: missing or untracked cause record.
  - `evidence_role_not_verification`: overview filmstrip selects candidates but
    cannot prove a claim; confirm with a full-resolution capture.
  - `evidence_time_unavailable`: no timestamp.
  - `evidence_outside_checkpoint`: frame outside the checkpoint span.

## `verification_audit` codes

- `missing_support`: a terminal checkpoint declares no support.
- `legacy_unstructured`: support uses the old free-form shape.
- `artifact_unavailable`: referenced media can no longer be opened.
- `correction_note_missing`: a `corrected` checkpoint omits what changed.

These are non-blocking audit findings; they do not rewrite existing ledgers or
retroactively change readiness.

## Diarization

```bash
va diarize <ws> [--num-speakers N]
```

- Auto backend: with an HF token, try pyannote then fall back to ungated
  sherpa. Without a token, use sherpa immediately.
- Run this before checkpoints, image provenance, sequences, corrections, or
  authored wiki evidence. It advances the transcript revision and refuses to
  run after transcript-dependent evidence exists.
- A current Python-API draft binds and validates its source before the backend
  runs. A pre-revision markerless workspace is read-only; ingest it into a fresh
  output path before diarization.
- Canonical transcript/manifest/diarization rewrites use an fsynced rollback
  journal. A catchable failure rolls back before return; after process loss,
  reopening the workspace completes the rollback before reads continue.
- BGM-heavy entertainment tends to over-segment; prioritize speakers with the
  most speech.
- The agent maps anonymous labels (S0/S1) to visual nameplates or microphone
  flags. Set known cast size with `--num-speakers`.

## OCR

```bash
va ocr <ws> -t 183 -t 520
va ocr <ws> -t 252 --crop 'iw*0.35:ih*0.3:0:ih*0.6'
va ocr <ws> --every 5 --crop 'iw:ih*0.35:0:ih*0.6'
```

- `--every` can replace ASR for BGM stories or on-screen posts. Repeated text is
  merged into `ocr_transcript.json`. Crop known subtitle regions to reduce game
  UI noise, but do not overfit away useful repeated text.
- OCR is enough for clean overlays and nameplates. Confirm stylized or suspect
  text in a full-resolution frame. When captions mirror narration, use OCR to
  correct suspect ASR segments.
- In gameplay, combine periodic killfeed OCR with spoken kill callouts because
  hard-cut scene signals are often weak.
- OCR import failure on supported macOS means incomplete installation; repair
  it. On Linux, inspect frames directly.

## Faces

```bash
va faces <ws> -t <ts...>
```

- Face-count changes signal entrances, exits, or composition shifts.
- Scale uses maximum face-area ratio and can seed `camera-*` tags.
- Bowed, occluded, or rear-facing heads can yield zero. Treat counts and scale
  as lower bounds; zero does not prove a long shot.

## Scene false positives

ffmpeg scene scores are luma-based: they can miss equal-luminance color changes
and mistake lighting flashes for cuts. When detections exceed 10 per minute but
the transcript describes one situation, run
`va scenes --adaptive --color-check`. Color-histogram cross-checks annotate each
detection. A suspicion lowers capture priority; it never auto-deletes a cut,
especially in tone-matched animation.

## Audio events

Sound Analysis proposes laughter, applause, cheering, and screams as learned P1
signals. Its score is classifier confidence, not editorial importance or event
truth. Use it to place candidates, then verify meaning with transcript,
surrounding video, and audio.
