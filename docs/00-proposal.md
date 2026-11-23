# Proposal: Font Manipulator (v2, updated 2026-11-23 after discussion)

## Build status (2026-11-23)

**Built:** engine (CLI), local server, React UI. See the root `README.md` to run it.

**Verified:**
- 15 engine tests.
- End-to-end UI run (`npm run e2e`), no console errors: slider, parameter edit, typing, inspector, add stop + effect, pin a variable source, export and download.
- OTS sanitises both final WOFF2s, and every table decompiles.

| Font | Glyphs | Build (CLI) | WOFF2 | Stop fit (mean) | Morph score (mean, per transition) |
|---|---|---|---|---|---|
| Source Sans 3 (CFF, UPM 1000), 4-stop default | 2,478 | 147 s | 2.46 MB | 99.3 / 97.7 / 100 / 98.7 % | 0.97 / 0.78 / 0.86 |
| Fraunces VF pinned opsz 144 wght 400 (glyf, UPM 2000) | 698 | 46 s | 0.92 MB | 98.1 / 97.5 / 100 / 99.0 % | 0.82 / 0.80 / 0.85 |

How to read these:
- **Stop fit** = IoU of the rendered TrueType outline against the effect's target. The lowest DCAY-0 scores (~0.83–0.9 on ° ¤ × _ in Fraunces) are rasterisation error on strokes 1–2 px wide; they were checked visually and match the source.
- **Morph** = IoU of the 50 % in-between against an SDF-blend reference. That reference is only a proxy for a good morph.
- **Split/merge effect:** Fraunces full font, before → after split/merge-aware matching = 0.59 / 0.75 / 0.80 → 0.82 / 0.80 / 0.85.
- **Preview latency (uncached):** about 0.6–2 s for 11–21 glyphs; about 0.2 s when cached. That misses the < 1 s target for big edits.

**Implemented since the proposal:**
- Corner-preserving resampling, with on-curve corner flags.
- Smooth (quadratic off-curve) outline mode.
- Split/merge-aware matching: pathops cuts with a raster fallback. Counter ownership is decided once per split, so cuts never cross counters, and holes of cut pieces are kept.
- Licence/copyright name records and `fsType` copied from the source; Mac name records dropped.
- Variable-source pinning in the UI.

**Reference-driven effects (2026-11-23):** four reference images (kept locally in `examples/`, git-ignored) were
approximated as presets. Side-by-side renders are in `examples/comparisons/`.

| Preset | Reference look | New pieces it needed | Inter, all 2,933 glyphs (est.) |
|---|---|---|---|
| Motion smear | wide heavy italic dragged sideways, wavy bands, ragged | `smear`, `wave`, recipe `base` transform (scales advances), `axis.default` | ~10 min |
| Mud inflate | puffy inflated letters, counters → dots, pitted grain | `pits`; uses grow + round | ~25 min (pits = many contours) |
| Melt drip | localised patches melt into blobs and drip down | `melt`, `regions` on ink | ~6 min |
| Marker bleed | sparse patches swell like soaked marker, odd doubled letter | `regions` on grow/echo, SDF-based patch blending | ~6 min |

Notes:
- **Regional effects blend shapes, not fields.** The SDF of clean and affected shapes is interpolated across patch edges; mixing raw fields made spurs and cracks.
- **A font only knows one glyph at a time.** Patches can't span words or lines as in the posters, and a given letter is damaged the same way every time it appears. Possible later: OpenType alternates cycled with `calt`/`rand`.
- **Pits are white specks.** A font is binary, so it can't reproduce grey print mottling.
- **Performance:** placeholder anchoring and contour matching are now cropped to bounding boxes (the worst glyph went from 13.7 s to 1.7 s).

**Known limitations:**
- The **smooth → pixel transition is the weakest** (morph about 0.78): around DCAY 50 the default recipe looks messy, with pieces popping and counters flickering. In-betweens across big topology changes are morphs and can't be fresh effect results.
- When a counter probably *opens* (a hairline breaks under ink), the closed counter can only shrink to a dot. In-betweens near that stop can briefly darken it (seen in Fraunces around DCAY 16, and the small ® in Source Sans). Workaround: add a stop in between, or use gentler ink on hairline fonts.
- Morph min is 0 for a few glyphs (tiny marks that vanish completely).
- WOFF2 for a full 2.5k-glyph font is about 2.4 MB, above the 2 MB target. Raise "Point spacing" or use fewer stops.
- Split/merge matching roughly doubled build time.
- UI export of Fraunces with 5 stops took 64–157 s across two runs; the cause of the variance is unverified.
- **Not built yet:**
  - static-instance export
  - HTML specimen
  - full glyph grid
  - OTS run on every export (only checked manually)
  - Pyodide/in-browser mode
  - multi-axis recipes

## Decisions (from discussion)

| Topic | Decision |
|---|---|
| Scope | **Broad effects playground**, not just a Ferro clone |
| Form factor | **Local app**: Python engine + React UI on localhost |
| Output | **Variable fonts** with a custom decay axis |
| Look | **Ink-y / blob-ish** is the goal; sharp pixels are a first pass or an intermediate stage |
| Algorithms | **Rewritten** from descriptions; no GPL code ported |
| UI | **React** (Vite + TypeScript) |
| Coverage | **Every glyph** in the font |
| Mirage licence | *Still open.* We develop on bundled OFL fonts, and Mirage can be loaded by you. See `research/04`. |

## 1. Evidence summary

- **Ferro's method** (research/01): dense sampling → nearest grid node → polygon, with per-glyph `advance/N` columns and rows anchored to the vertical zones. This is measured and inferred from public webfonts; Ferro's own scripts are not public.
- **Font I/O** (research/02): fontTools is the only lossless writer. Variable-font masters must share an identical point structure.
- **Algorithms** (research/03): point-maps keep compatibility. Raster, retrace and booleans don't, unless adapted.
- **Spike 3 (`evidence/spike3_variable_decay_axis.py`, `evidence/ofl-sourcesans-variable-dcay.png`)**: a real variable font with a `DCAY` axis from 0 to 100, built from 4 masters (source → ink bleed → pixel grid → pixels melted into blobs). All 68 test glyphs interpolate and the font renders in Chrome. Intermediate values give attractive "melts".
  - Defects to fix: white notches and hairlines where snapped outlines fold back on themselves.
  - Ink is slow: about 0.4 s per glyph per master, unoptimised.

## 2. Core idea: free-form effects, then a "compatibiliser" (tested in spikes 4–6)

> This **overrides** the "static font per stage" recommendation in research/02 and /03, at your request.

Variable fonts interpolate point positions, so every stage must have the same contours and points in the same order. The design was tested in three steps.

**Spike 4: "shrink-wrap" (slide fixed points onto each target shape).**
- Works when the shape keeps its topology: ink bleed reached 97.6% overlap with the target, with zero folds in the in-betweens.
- **Fails** when an effect splits or merges shapes: pixels reached only 54%, with pieces lost and folds. One loop of points can only wrap one piece.
- Verdict: no-go as the general mechanism.

**Spike 5: the design we'll build.**
1. **Effects produce any shape they like.** Typically: rasterise the glyph (nonzero winding, the same rule browsers use) → field operations (blur, threshold, noise, pixel grid, erode/dilate, …) → trace the edges back to outlines.
2. **The compatibiliser then makes the stages interpolatable:**
   - Matches contours across neighbouring stages by overlap.
   - Resamples each matched track to the same point count.
   - Aligns start points.
   - Gives unmatched contours a **collapsed placeholder** (all points at one spot, zero area) in stages where they don't exist. A new blob grows out of a dot as you drag the slider; a closing counter shrinks to nothing.
3. Results on hard glyphs (e a g s B 8 i % @ F r o):
   - **Every stage matches its target at 99.7–99.9%.**
   - All glyphs vary in the built variable font, and it renders correctly in Chrome.
   - The **blob stage visibly works** (`evidence/ofl-spike5-compatibiliser.png`; drag the slider in `evidence/variable-demo/index.html`).

**In-between quality** (overlap with an "ideal morph" reference):

| Transition | Overlap | Reading |
|---|---|---|
| Similar shapes (source → bleed) | 95–98% | Good |
| Pixels → blobs | 86–89% | OK |
| Smooth → pixels | 54–65% | Weak: visible popping as blocks spawn from dots |

An auto-inserted in-between master (spike 6) helped only a little. Planned improvements:
- Split/merge-aware matching: spawn new pieces from the parent's edge, not from their own centre.
- Recipe guidance: neighbouring stops should be topologically similar.
- Optional stepped glyph swaps (`rvrn`) for hard jumps.

**Measured costs:**
- **Speed:** about 0.1 s per glyph for 4 masters, i.e. about 3–4 minutes for 2,000 glyphs on one core; parallel processing is easy. (The 0.46 s per call noise bottleneck was fixed with a precomputed noise texture.)
- **File size:** about 3.2 KB per glyph with 4 masters at the current density, i.e. **about 6 MB for a full font**. Needs reducing via sparser sampling and curve smoothing (quadratic off-curve points). Target ≤ 2 MB WOFF2.

**Hypotheses, not yet tested:**
- Multi-axis designspaces (e.g. separate INK × GRID axes).
- Keeping a variable input font's own axes (Mirage `wght`) alongside `DCAY`.

**Inherent trade-offs of variable fonts:**
- Shapes can't truly split or merge mid-slider; they grow from or shrink to dots.
- In-between values are morphs, not fresh effect results.
- Files are larger than a single static font.

## 3. The app

```
font-manipulator/
  engine/      Python 3.13 (uv): fontTools, numpy, scipy, scikit-image, opensimplex, skia-pathops
    io.py          load OTF/TTF/WOFF/WOFF2; pin variable inputs to an instance
    skeleton.py    decompose + resample
    effects/       snap.py, noise.py, warp.py, field.py (ink/blob/pixel/erode), ...
    recipe.py      recipe schema (pydantic) → masters
    build.py       masters → variable TTF/WOFF2 (+ statics), naming, STAT, validation
    cli.py         `fontmanip build font.otf recipe.json`
  server/      FastAPI: upload font, preview, export job + progress
  web/         React + Vite + TS
  fonts/       bundled OFL fonts for development and demos
  docs/
```

**UI (first version)**
- **Left panel:**
  - Font picker / drop zone.
  - Axis stops list (add, reorder, remove).
  - Per-stop effect stack, with parameters as sliders and a seed.
- **Centre:**
  - Live type tester using the actual compiled preview variable font, loaded with the `FontFace` API.
  - A big **DCAY slider** plus play/animate.
  - Custom text.
- **Right:**
  - Glyph inspector (SVG: skeleton points, masters overlaid).
  - Full glyph grid.
- **Preview loop:**
  - The engine builds a variable font for only the glyphs in the tester text. Target: under 1 s for about 30 glyphs; spike 5 did 13 glyphs in about 1.3 s, unoptimised.
  - The browser swaps it in, so slider scrubbing is native font rendering.
- **Export:** a background job over every glyph with a progress bar, producing a variable TTF + WOFF2, optional static OTFs, `recipe.json` and an HTML specimen.

## 4. Build plan

1. **Engine core:** raster/field effects + trace, the compatibiliser (with split/merge-aware matching), VF build with copied layout tables, validation (OTS, re-open, `varLib.interpolatable`), CLI. Tested on bundled OFL fonts.
2. **Performance and size pass:** multiprocessing and point reduction. Targets (not yet met): a full 2,000-glyph export in about 1–2 minutes, and a WOFF2 of 2 MB or less.
3. **Server + React UI:** stops/effects editor, live preview variable font, slider, glyph inspector, export.
4. **More effects:** lattices, attractors, warps, halftone-ish fields, generational loops, multi-axis.

## 5. Engineering rules

- Use `TTGlyphPointPen` (not `TTGlyphPen`) so duplicate/closing points are never dropped. Spike 3 hit exactly this.
- Never remove overlaps per master. Rely on the compatibiliser's collapsed placeholders plus the glyf overlap flag.
- Rasterise with **nonzero** winding (browsers fill nonzero); measure every recipe with master-vs-target and in-between overlap scores.
- Seed with `hashlib` over (seed, glyph, stop, effect).
- Keep advance widths fixed, so GPOS kerning stays valid.
- Rename the family on every output (nameIDs 1/3/4/6/16/17/25).
- Validate every export: OTS, a fontTools re-open, and a check that every glyph varies (no silently "skipped incompatible" glyphs).

## 6. Remaining open items

- **Mirage:** contacting Dreamtype is up to you. The tool will work with any font you load.
- **Skeleton density vs file size:** dense line skeletons are big. To be measured, then reduced with smarter point placement or quadratic smoothing.
