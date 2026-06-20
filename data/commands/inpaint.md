---
name: inpaint
description: Run the localization pipeline that erases English text from ad images (OpenRouter inpainting) and overlays Korean text.
argument-hint: "[image path or directory] [description of what you want to do]"
---

# Image Text Localization (image-inpaint)

Perform image localization using the host's `/Users/unohee/dev/kyte-tools/image-inpaint` tool.
**Run everything via `host_exec`** (the tool, virtualenv, and API keys live on the host; they are not in the sandbox).

> **Detailed reference**: the decision tree, full options, JSON schema, and failure-mode table are
> read by running `cat /Users/unohee/dev/kyte-tools/image-inpaint/SKILL.md` via `host_exec`.
> This document is a summary; when a judgment call is ambiguous, SKILL.md is the source of truth.

## Execution Formula

```bash
source /Users/unohee/dev/mlx_env/bin/activate && cd /Users/unohee/dev/kyte-tools/image-inpaint && image-inpaint <subcommand> ...
```

Three subcommands:
- `erase <input...> [-o output-directory]` — erase English text only. Input is a file or directory.
- `overlay <input...> --layout <layout.json> [-o output-directory]` — overlay Korean text only.
- `localize <input...> --layout <layout.json> [-o output-directory]` — erase then overlay in one go.

## Steps

1. Determine the target image path and the intent from `$ARGUMENTS`. If there's no path, ask the user.
2. If the target is a directory, `ls` it via `host_exec` to count the images, and **if there are more than 5, inform the user of the cost (about $0.03 per image) and run only after user confirmation**.
3. If a Korean overlay is needed, create the layout JSON:
   - If the user doesn't know the coordinates: first run `erase` only, then, along with the result image, point them to `tools/editor.html` (drag-to-place in the browser → export JSON).
   - If the coordinates and text are clear: write the JSON file directly using the schema below.
   ```json
   {"texts": [{"text": "한국어 문구", "x": 540, "y": 200, "size": 64,
               "color": "#FFFFFF", "align": "center", "strokeWidth": 0}]}
   ```
   (coordinates are in original pixels, `align` is left|center|right relative to x, line breaks are \n)
4. Run the relevant subcommand and verify the result from the `OK`/`FAIL` line in stdout.
5. Report the output file path (`<original>_<step>.png`) to the user. If there's a FAIL, pass the original error through as-is.

## Design 1:1 Matching (original English → Korean) — Mandatory Rule

When the user requires "the same font size and position as the original", **you must use the `match` subcommand**.

```bash
image-inpaint match <original-english.png> --text "Korean headline" --text "Korean subline" \
  --blank <same-image-without-text.png> [-o output-directory]
```

- Specify `--text` repeatedly in order, from the top line down. If there's no blank, create one with `erase` and specify it via `--apply-to`.
- **Strictly forbidden**: estimating coordinates/sizes by eye or vision, or "matching Korean width to English width".
  At the same size, Korean ink height is ~20% larger than Latin cap height, so matching width makes the characters ~35% larger (measured in KT-260).
- match pixel-measures the original's ink height, y-center, and stroke weight to compute size/weight/coordinates,
  then re-measures the generated result and outputs a Δ table (PASS/FAIL). **If it's a FAIL, report it as-is and stop.**
- User confirmation: **show all variations bundled together at once and let them choose** — if there are design options (font, etc.),
  use `--grid` to generate around 4 mockups per family as a single labeled grid image (`*_variants.png`) and present it
  (only variations with a meaningful difference — no variations of the same answer). For a single fixed choice, confirm with `*_compare.png`
  (original | mockup side by side) and the Δ table. After confirmation, reuse the
  `*_layout.json` for the remaining images in the same series via `overlay --layout`.
- The default font is **Pretendard** (bundled at `assets/fonts/pretendard/`, weight auto-selected — the user-confirmed value).
  Specify other candidates (Wanted Sans, Gothic A1, etc.) with `--font assets/fonts/<family>`.

## Caution

- Non-square images are automatically handled with square padding → edit → crop. No option needed.
- The model default is `google/gemini-2.5-flash-image`. If you're unhappy with quality, retry with `--model google/gemini-3-pro-image-preview` (about 6x more expensive).
- `OPENROUTER_API_KEY` is in the tool repository's `.env`. If you get a "missing" error, tell the user.
