# Output handoff

Deep reference for loop §5. `SKILL.md` owns mandatory boundary self-review,
delivery encoding, and unsupported editor-path bans. This file owns scoring,
examples, and format details.

## Edit-scope convergence (R_edit)

Use R_edit only when the brief names a source interval or event, such as a scene
at 3:16. Open-ended discovery must compare candidates across the full selection
scope; converge globally before choosing a highlight.

**Overlap is not coverage.** `validate_terminal_grounding` requires the entire
cut span to be covered by the union of supported terminal
(`verified`/`corrected`) checkpoints. Verify uncovered regions or narrow the cut.

Only boundary slack is allowed: up to `min(2.0s, cut duration × 10%)` at each
head and tail because word-snapped cuts and human checkpoint spans differ.
Slack never absorbs a hole in the cut body.

When these conditions hold, edit work may begin before global `covered_ratio`
converges. A globally `converged` workspace still requires local verification
when the target interval is `hypothesized`. Summaries and whole-video analysis
still require global convergence.

## Five-axis clip score

Score each axis 0–10, then multiply the weighted sum by 10. Ground emotional
intensity in audioevents/highlights. Snap boundaries immediately before the
first segment and after the last.

| Axis | Entertainment | Informational | Evidence |
|---|---|---|---|
| Hook | 0.25 | 0.30 | Attention in the first 2–3s; bold claim; pattern break |
| Self-contained | 0.20 | 0.25 | Complete without prior context; no cut sentence or action |
| Emotion | 0.30 | 0.20 | Laughter, cheers, screams, or visible reaction beyond transcript |
| Value density | 0.10 | 0.15 | Information/action per second or pacing |
| Payoff | 0.15 | 0.10 | Satisfaction of punchline, reversal, or ending |

## Pareto edit alternatives

Offer 2–3 cuts with distinct intent, trade-offs, and rejected alternatives
instead of one nominal winner.

| Option | Optimizes | Cost |
|---|---|---|
| Continuity | Event, action, and spatial continuity | Slower pace |
| Emotion | Reactions, silence, and emotional arc | Omits some information |
| Tension | Delayed information, pace, hook | Higher visual reorientation cost |

- Every option must pass word snap → `boundary_eval` → visual self-review.
  Options differ in cut selection and order, never boundary quality.
- Promote only the chosen option in the sequence ledger. Record rejection
  reasons for others in `alternatives_rejected`.

## Deterministic boundary metrics

`va boundary-eval <ws> --sequence seq-001 [--json]` —
checks every sequence boundary and rendered join. Treat thresholds as advisory:

- `word_interior`: cuts inside a word; re-snap immediately.
- `tight_tail`: less than 0.12s after a word despite available silence; preserve
  meaningful post-line silence.
- `loud_step`: RMS changes by at least 12dB; inspect room tone or music.
- `loud_join`: assembled audio jumps even when source edges look clean.
- `jump_cut_risk`: SSIM > 0.75 suggests a time jump in similar framing.

Join frames remain in the capture cache for visual self-review. For individual
points use `-t <boundary> [-t ...]`; `-t` and `--sequence` are exclusive.

The edit loop is: Pareto options → word snap → metrics → visual self-review →
re-snap → terminal promotion. Audio detects word/level discontinuity; vision
detects shot intrusion and composition jumps.

## Clip encoding

`va clip <ws> --start 1:23 --end 2:05 --accurate`

- For live work or 4K previews, `--hw hevc` (or h264) uses VideoToolbox on
  supported macOS hardware and lowers CPU load.
- Hardware encoding is slightly less efficient than libx264/265 at equal size.
  Use `--hw` for preview/review; omit it for final delivery and archives.

## 9:16 reframing

`va reframe <ws> <clip> --roi x,y,w,h [--roi ...] --mode pan|split`

- Open one full-resolution frame and estimate each face ROI in pixels, including
  mouth and chin. With a fixed camera, one frame is sufficient.
- One subject: one centered ROI. Two speakers: ask the user to choose motion-led
  `pan` or fixed stacked `split`.
- `pan` prints a speaker timeline to stderr. Compare it with diarization labels
  to support mappings such as S0 = left subject.

## NLE formats

| Format | Contract |
|---|---|
| xml (xmeml v4) | Checkpoints as sequence markers only; verify import in the target NLE |
| otio | Rough cut plus evidence metadata; external media references; verify adapter support |
| fcpxml (1.11) | Spine rough cut plus evidence markers; verify target-version import |
| srt | Subtitle exchange; falls back to OCR pseudo-transcript; never manipulate unofficial editor draft JSON |
| edl (CMX3600) | Cut-list fallback; hard cap of 999 events; unsuitable for marker-heavy long form |
| md | Human-facing scene log |

- With `--ids cp-004,cp-007`, argument order is cut order.
- `--sequence seq-001` exports the sequence ledger to edl, otio, or fcpxml.
  XML is marker-only. The ledger owns trim boundaries and cut order.
