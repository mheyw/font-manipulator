# Performance pass (2026-11-23)

Benchmark: `cd engine && uv run python bench.py`. It runs 12 hard glyphs × 7 cases, single process, and reports
seconds/glyph plus quality (stop fit, morph). Before: `baseline.json`. After: `after.json`.

| case | before s/glyph | after s/glyph | speed-up | Δ fit_mean | Δ morph_mean |
|---|---|---|---|---|---|
| default (Source Sans) | 0.240 | 0.112 | 2.1× | 0.0000 | −0.0001 |
| motion-smear | 0.683 | 0.199 | 3.4× | 0.0000 | +0.0002 |
| mud-inflate | 1.925 | 0.356 | 5.4× | +0.0001 | 0.0000 |
| melt-drip | 0.402 | 0.173 | 2.3× | 0.0000 | 0.0000 |
| marker-bleed | 0.364 | 0.130 | 2.8× | 0.0000 | 0.0000 |
| kitchen-sink (stress) | 2.371 | 0.733 | 3.2× | 0.0000 | +0.0002 |
| fraunces-default | 0.223 | 0.137 | 1.6× | 0.0000 | −0.0004 |
| **total** | 74.5 s | 22.1 s | 3.4× | | |

Measured under a load average of about 9 from other processes; the agents measured 2.3–6.2× with less load.

**What was done:**
- **compat.py** (agent report: `compat.md`): cropped per-contour fills, bounding-box-pruned matching, vectorised
  counter labelling, single-pass corner detection, cropped and cached anchors, cells traced locally. About 3× on
  speck-heavy recipes.
- **effects / raster / outline / pipeline** (agent report: `effects.md`): noise via separable matrix products
  (~20×), bounding-box-cropped distance transforms and blurs, packed winding, cropped tracing, reused flattening
  plans. Equivalence tests compare against the original code; bit-identical output except noise at about 1e-15.
- **After the agents:**
  - Master glyphs are built straight from arrays, not through a pen: the font-build step for 143 pitted glyphs
    went from 18 s to 2.7 s.
  - Pits use one pattern for the whole recipe, so they persist and grow along the axis instead of re-rolling at
    each stop. Mud inflate contours halved (6,462 → 3,166), its file is about 27 % smaller, and fit/morph improved
    slightly (0.9651 → 0.9673 / 0.8776 → 0.8801). This is an intentional behaviour change, so other recipes with
    pits get a different random pattern.

**Still heavy:**
- Pitted recipes are large: every pit is real geometry in every master. Mud inflate on Inter is about 1.2 MB
  WOFF2 per 143 glyphs.
- Remaining hotspots:
  - skimage contour assembly (pure Python)
  - large-radius blurs
  - skia-pathops cuts
  - fontTools gvar delta computation
