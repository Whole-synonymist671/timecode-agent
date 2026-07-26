# Corpus lifecycle

Read this only to resume, search, update, or clean a multi-video corpus.
`SKILL.md` owns first-pass analysis, `verification-toolbox.md` owns evidence
tools, and `wiki-schema.md` owns labels.

## Resume and search

- If `<ws>/manifest.json` exists, resume with `va brief <ws>`; never re-ingest.
- For scene recall, run `va search "<query>"` before walking workspaces.
  `hypothesized` search hits are navigation hints, not answer evidence.
- End sessions with `va index` to rebuild `va-out/INDEX.md`, scene logs, and
  group hubs. Start the next session from `INDEX.md`.
- `va view` HTML is derived from Markdown ledgers. Deleting it does not damage
  source data; it falls back when media has been cleaned.

## Transcript corrections and glossary

Write `<ws>/corrections.jsonl` only after visual evidence proves an ASR error.

```json
{"span":[12.0,14.2],"asr":"기태 먹었어요","corrected":"큐 키트 먹었어요","basis":"프레임 근거"}
```

- Prefix non-correction annotations in `corrected` with `(` so their words
  cannot enter hotwords.
- Glossary candidates are proper nouns, domain terms, and channel spelling
  conventions. Keep common-word corrections local to the video.
- At session end run `va glossary --all` or `va glossary <ws>...`.
  Inject video-local terms with `va ingest --hotwords "용어1 용어2"`.
- Consider fine-tuning only after the same error recurs at least three times
  and hotwords still fail. Never inject the entire glossary into every video.

## Wiki promotion

- End sessions with `va index && va wiki`.
- Promote only `verified`/`corrected` checkpoints with currently resolvable
  visual or transcript support into the active semantic layer.
- Preserved prose outside ledgers is an input to regeneration. Never discard
  wiki `tca:notes` or scene-log narrative blocks.
- Follow [wiki schema](wiki-schema.md) for labels, relations, narratives, and
  index-first queries.

## Batch

For three or more videos, read the [batch contract](batch-ingest.md).
Parallelize ingest and deterministic signals only. The main agent performs
hypotheses, frame interpretation, checkpoint writes, and final selection.

## Storage hygiene

Inspect size with `va gc` report mode first. Delete only after the user names
the scope.

```bash
va gc --purge captures --yes
va gc --purge media --yes
va gc --purge workspace --keep-days 30 --yes
```

- Without `--yes`, every command is a dry run.
- `captures` removes regenerable captures and filmstrips.
- `media` removes downloaded sources; recapture then requires URL redownload.
- `clips` are deliverables; remove them only when explicitly requested.
- `workspace` requires `--keep-days N`.
- Category purges preserve text ledgers such as manifest, transcript,
  checkpoints, corrections, glossary, and markers.
