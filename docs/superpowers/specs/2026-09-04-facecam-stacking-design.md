# Facecam stacking + Create Clip from Moment — design (v0.2.6)

Date: 2026-09-04 · Status: implemented, tag held for the manual render check.

## Problem

The product's signature is "create clip from moment": mark a moment in a VOD,
get a vertical clip with editable captions and the streamer's face positioned
top-centre over the game. As of v0.2.5 the caption stack was done, but
"Facecam Top" was a cosmetic center-crop with a vignette — no compositing code
existed at all.

## Goals

- Real 9:16 composition from the single 1080p source, matching mark's reference
  mockup (2026-09-04): blurred+dimmed cover backdrop → game layer (full width,
  cropped so the facecam corner doesn't show) → rounded-corner facecam crop
  top-centre → karaoke captions in the dark zone below.
- 4K (2160x3840) export from the same guide.
- Guide drawn once over a grabbed frame, remembered across sessions.
- One-click moment flow that never auto-starts long jobs.

## Non-goals (this release)

- Channel-keyed auto-apply of guides (last-used covers the one-layout case).
- `alphamerge` precomputed-mask optimization for the rounded corners (geq is
  fine for short clips; revisit if 4K batch renders feel slow).
- Input-side `-ss` seeking for deep VOD moments (pre-existing decode-from-start
  behavior, unchanged).
- Pillar-1 session inbox / padding / batch download (next release).

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Graph shape | single input, split `[0:v]` inside `-filter_complex` | legacy `reel.py` shape; keeps progress probing + `longform_export` untouched (no second `-i`) |
| Guide model | normalized fractions `{enabled,x,y,w,h,game_mode}` + legacy clamps `[0.08,0.70]` | ported from `54f4148^:reel.py`; resolution-independent → 4K free |
| Game layer | "Avoid facecam" = crop the strip (side column or rows) that loses least game area; "Full frame" opt-in | mark: "so no facecam shows" |
| Rounded corners | `format=yuva420p,geq=a=…` signed-distance-to-rounded-rect on the small facecam stream | no mask asset plumbing; per-pixel cost bounded by the small crop |
| Audio | `loudnorm` lives inside the graph (`[0:a]loudnorm[a]`) | `-af` beside `-filter_complex` is rejected by ffmpeg |
| Captions | `ass=` appended last, unchanged contract | output-pixel-space positioning already geometry-agnostic |
| Trigger | style `facecam_top` + enabled guide + portrait format; else crop fallback | zero regression for every existing path |
| Guide persistence | `settings.json` `facecam_layouts{name}` + `last_facecam_layout_name`, applied once per session | one OBS layout per channel is the real case; survives source swaps by design |
| Moment flow | add clip → set style → jump to Shorts → focus Run | mark chose "I click Run" — no auto-transcription on 6h VODs |

## Architecture

- `native/services/composite.py` (pure): `normalize_facecam_layout`,
  `game_crop_region`, `composite_geometry` (all pixel numbers, fraction-based),
  `build_composite_filter_complex`, `build_composite_export_args`.
- `native/ui/facecam_dialog.py`: `_GuideCanvas` (letterbox-correct frame +
  move/resize/draw rect, TimelineStrip-style hit testing), `_StackPreview`
  (composes via `composite_geometry` at 270x480 — the preview and the render
  share one geometry function), `FacecamGuideDialog`.
- `ProjectState.facecam_layout` + `facecam_layout_changed` (preset-key pattern).
- `MainWindow._clip_export_args` branches to composite; `_probe_source_dims`
  caches ffprobe width/height per source; `_create_moment_clip` drives the flow.
- Ingest: "Create Clip from Moment" (primary) + "Facecam Guide…" buttons,
  `request_create_moment` / `request_facecam_guide` signals (stages stay dumb).
- Shorts: `focus_for_moment()` focuses Run with a hint.

## Testing & verification

- `tests/test_composite.py` (17): normalize clamps, game-crop axis choice,
  geometry numbers at 1080p + 4K + even-dimension invariants, graph layer
  order / crop literals / overlay order / ass-last / maps, arg builder shape
  (no `-vf`, loudnorm in graph).
- State/layout defensive-copy + signal tests; 4K preset tests.
- Offscreen headless: window construction, moment flow, composite-vs-fallback
  branch matrix (shorts/4K/landscape/no-guide).
- ffmpeg smoke: synthetic 1080p → 4K composite render verified visually
  (backdrop blur+dim, game band, rounded transparent-corner facecam).
- **Manual checkpoint (gates the tag):** real VOD — draw guide, create a
  moment, run captions, render 4K, eyeball the stack + burned captions.

## Risks

- geq per-pixel cost at 4K — acceptable for 30-90s clips; `alphamerge` is the
  known fast path if needed.
- Output-side `-ss` decodes from file start on deep VOD moments (pre-existing).
- Audio-less sources now hard-fail on the composite path (`[0:a]` in graph)
  where the old path tolerated them; VOD downloads always carry audio.
