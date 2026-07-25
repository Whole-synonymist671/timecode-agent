# Brand assets

The mark is a camera frame with a timecode colon inside it: four corner
brackets around two stacked dots. It reads as "framing" and as the `:` that
separates timecode fields — the same colon that splits `timecode:agent`.

## Tokens

| Token | Value | Use |
|---|---|---|
| Ink | `#0F100F` | Background on dark surfaces; frame strokes on light ones |
| Paper | `#F3F4F2` | Frame strokes on dark surfaces; background on light ones |
| Signal | `#5AC478` | The two dots, and the colon in the wordmark — nothing else |

Values are sampled from the source artwork, not eyeballed. Signal green is
the accent and stays scarce: if more than the dots and the colon are green,
it has stopped being an accent.

## Files

| File | Use |
|---|---|
| `mark.svg` | Mark for light backgrounds (ink strokes) |
| `mark-dark.svg` | Mark for dark backgrounds (paper strokes) |
| `mark-mono.svg` | Single-colour mark; inherits `currentColor` |
| `app-icon.svg` | 512 px rounded-square icon, dark |
| `app-icon-light.svg` | 512 px rounded-square icon, light |
| `favicon.svg` | 64 px simplified mark — strokes thickened so the dots survive at 16 px |
| `hero.png` | Mark + wordmark lockup on ink |
| `hero-ko.png` | Korean brand lockup and source artwork for social previews |
| `social-preview.png` | 1280×640 GitHub social preview |
| `workspace-preview.png` | Real synthetic `va view` corpus preview for README |

The mark geometry lives on a 288 unit grid: 25 unit strokes with round caps,
70 unit bracket arms, dots of r=30 centred at y=100.5 and y=186.5. Scale it
rather than redrawing it.

## Regeneration

Render the social preview from the approved Korean lockup:

```bash
uv run --python 3.12 --no-dev --with pillow \
  python scripts/render_brand_assets.py \
  --source assets/brand/hero-ko.png \
  --output assets/brand/social-preview.png
```

Build a synthetic corpus page and capture it at the committed viewport:

```bash
preview_root="$(mktemp -d)/homepage-preview"
view_path="$(
  uv run --python 3.12 --no-dev python \
    scripts/render_homepage_preview_fixture.py \
    --output "$preview_root"
)"

preview_log="$(mktemp)"
python3 -m http.server 8765 \
  --bind 127.0.0.1 \
  --directory "$(dirname "$view_path")" \
  >"$preview_log" 2>&1 &
preview_server_pid="$!"
trap 'kill "$preview_server_pid" 2>/dev/null || true' EXIT

sleep 1

playwright-cli open http://127.0.0.1:8765/view.html
playwright-cli resize 1440 900
playwright-cli screenshot \
  --filename assets/brand/workspace-preview.png
playwright-cli close

kill "$preview_server_pid"
wait "$preview_server_pid" 2>/dev/null || true
trap - EXIT
```

The fixture contains only synthetic names, transcripts, checkpoints, and
media paths. Inspect the screenshot before committing it.

## Usage

- Keep clear space of at least one bracket arm (70 units at mark scale) on
  every side. The mark is a frame; crowding it defeats the idea.
- Pair the mark with the wordmark set in a monospace face. The colon is
  always signal green while the rest of the wordmark is ink or paper.
- Use `mark-dark.svg` on anything darker than mid-grey, `mark.svg` otherwise.
  In Markdown, switch with `<picture>` and `prefers-color-scheme` so both
  GitHub themes stay legible.
- Do not recolour the brackets, add a stroke to the dots, rotate the mark, or
  place it on a busy photograph.
