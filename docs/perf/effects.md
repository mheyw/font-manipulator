# Effects / raster / outline / pipeline performance

Scope: `effects.py`, `raster.py`, `outline.py`, `pipeline.py` (`compat.py` was optimised separately
by another agent at the same time).

## Result

Every change is **exact**. With `compat.py` frozen to one snapshot, the full `process_glyph` output
of all 7 bench cases × 12 glyphs (masters, on-curve flags, fit, morph) is **bit-identical** to
the original effects/raster/outline/pipeline code. Noise is the only non-bitwise part (it agrees
to about 1e-15), and that never flipped a pixel.

### My share, with compat held fixed (A/B interleaved, best of 3, s/glyph)

| case | original | new | speed-up |
|---|---|---|---|
| default | 0.494 | 0.268 | 1.8× |
| motion-smear | 1.469 | 0.440 | 3.3× |
| mud-inflate | 2.172 | 0.636 | 3.4× |
| melt-drip | 0.683 | 0.179 | 3.8× |
| marker-bleed | 0.501 | 0.147 | 3.4× |
| kitchen-sink | 1.774 | 0.662 | 2.7× |
| fraunces-default | 0.254 | 0.132 | 1.9× |

The machine was heavily loaded (load average 10–38) by the other agent's runs. Absolute numbers are
inflated, but each pair ran back to back.

### Live `bench.py` (my changes plus the other agent's current compat)

Three runs at load average 5–7. The time is the per-case minimum. Quality was identical in all
three runs (deterministic). Baseline values are in brackets.

| case | baseline s/glyph | after s/glyph | total | fit_mean | fit_min | morph_mean | rules |
|---|---|---|---|---|---|---|---|
| default | 0.240 | 0.091 | 2.6× | 0.9904 (0.9904) | 0.9762 (0.9755) | 0.8500 (0.8501) | ok |
| motion-smear | 0.683 | 0.167 | 4.1× | 0.9848 (0.9848) | 0.9429 (0.9429) | 0.9218 (0.9216) | ok |
| mud-inflate | 1.925 | 0.310 | 6.2× | 0.9652 (0.9651) | 0.9526 (0.9526) | 0.8776 (0.8776) | ok |
| melt-drip | 0.402 | 0.107 | 3.8× | 0.9963 (0.9963) | 0.9934 (0.9934) | 0.8849 (0.8849) | ok |
| marker-bleed | 0.364 | 0.091 | 4.0× | 0.9915 (0.9915) | 0.9867 (0.9867) | 0.9580 (0.9580) | ok |
| kitchen-sink | 2.371 | 0.506 | 4.7× | 0.9166 (0.9166) | 0.8377 (0.8376) | 0.7503 (0.7501) | ok |
| fraunces-default | 0.223 | 0.099 | 2.3× | 0.9887 (0.9887) | 0.9707 (0.9700) | 0.8081 (0.8085) | ok |

TOTAL went from 74.5 s to 16.6–24.0 s. The "total" column is the combined effect of both agents.

**The small quality shifts (morph −0.0004 for fraunces, −0.0001 for default) come from
`compat.py`, and this was measured.** I copied the current `compat.py` into both an
original-code snapshot and a new-code snapshot. Running all 84 bench glyphs gave bit-identical
results: 0 glyphs differ in masters, flags, fit or morph. With that same compat, my part alone gives
default 0.354→0.116, motion-smear 1.143→0.225, mud-inflate 1.305→0.350, melt-drip 0.754→0.113,
marker-bleed 0.387→0.107, kitchen-sink 2.703→0.594 and fraunces 0.194→0.109 s/glyph.

## What changed and why

1. **`noise_field` is now separable** (raster.py). The sample points lie on an axis-aligned grid,
   so a periodic bicubic B-spline lookup becomes `Wy @ tex @ Wx.T`, using sparse 4-tap weight
   matrices of size (H×64) and (W×64). This is about 20× faster than `map_coordinates` and agrees
   to about 1e-15. Before the change it was the largest single cost in the effects.
2. **Distance transforms are cropped and one-sided** (raster.py: `sdf_box`, `sdf_pair`,
   `edt_in`, `edt_out`, `sdf_below`. Plain `sdf(mask)` is unchanged).
   - Why it's exact: take the ink bounding box plus a 1 px background ring. Every pixel in that box
     has its nearest ink pixel and its nearest background pixel inside the box (clamp the far point
     onto the ring).
   - Outside the box, only a lower bound is needed, because every consumer only thresholds:
     - grow: `sdf < d`
     - roughen: `sdf < shift(x)`
     - round: opening and closing
     - pits: `sdf < -margin`
     - `_blend`: sign of `(1-w)·sdf(a) + w·sdf(b)` on the common box of both masks
     - the pipeline morph reference: sign of `sdf(a) + sdf(b)` on the union box of all stops, with
       each mask's SDF computed once instead of twice
   - `sdf < t` with t ≥ 0 is `mask | edt_out < t`. With t < 0 it is `edt_in > -t`. So grow, round
     and pits now need half as many EDTs.
3. **Gaussian blurs are cropped** (`raster.blur`). The filter runs on the nonzero box grown by the
   kernel radius, `int(4σ+0.5)`. Inside that region every tap reads the same values as on the full
   frame, because a reflection at an inner crop edge only sees zeros. Outside it the result is
   exactly 0. This covers ink, blur, `_near_ink`, spatter, smear softness, melt swell and melt
   softness.
4. **`Frame.winding` is bbox-local and packed.** Loops are concatenated once, with next-point
   indices instead of a `np.roll` per loop. The scanline accumulation runs only over the crossings'
   row/column box: closed loops cross each row a net zero times, so nothing outside that box can be
   nonzero. The output is the same integers. compat's thousands of single-loop `fill`s get this for
   free. `winding_packed(pts, lengths)` was added.
5. **`trace`** runs marching squares on the ink bbox + 2 px instead of the whole frame. The contour
   coordinates of a binary mask are half-integers, so the shift back is exact. Smoothing and
   signed-area terms are computed on all loops at once with packed prev/next indices. The per-loop
   sums keep the same `np.sum` order, so the results are bit-identical.
6. **Fit/morph rendering** (`outline.PackedFlattener`, pipeline). The quadratic flattening plan
   depends only on the on-curve flags, which every stop and midpoint instance share. It is built
   once per glyph and evaluated as a few vectorised ops. This replaces about 100k Python-level
   `flatten` calls in kitchen-sink and mud-inflate. The output is bit-identical to `flatten`.
7. **`GlyphState.mask` is cached** (states are never mutated in place).

Tests: `engine/tests/test_effects_perf.py` (21 tests) checks each piece against inline
reference implementations:
- old winding vs `winding` / `winding_packed`, including zero-length loops
- `flatten` vs `PackedFlattener`
- old trace vs cropped `trace`, including ink touching the frame edge
- `map_coordinates` noise vs separable noise
- `sdf_below`, `sdf_pair`, `edt_in`, `edt_out` and `blur` vs full-frame scipy
- 16 effect configurations the bench doesn't hit (negative or zero grow, round radius 0, smear at
  an angle, regions < 1, melt and ink reaching the frame edge, and others), run once through the
  cropped helpers and once through full-frame helpers, which must give identical masks

A mutation check (shrinking the crop pad by 2 px) makes the tests fail. The full suite passes
(47 tests).

## What didn't help or wasn't done

- **Noise cache** (memoising identical noise fields within a stop): only about 10% hits (72 of
  780, only repeated `_regions` calls), saving about 2 ms each. I removed it because it isn't worth
  the global state.
- **`find_contours` assembly** (a pure-Python dict/deque loop in skimage) is now most of `trace`.
  A faithful reimplementation would have to reproduce skimage's exact loop order and start points,
  because compat depends on them. That is too risky for the gain. OpenCV's tracer gives different
  vertices and would mean a heavy new dependency, so I rejected it.
- **Gaussian `correlate1d`** for large σ (ink radius 60u gives σ≈19 px) is the main remaining effect
  cost. Faster options (float32, recursive or box approximations, smaller truncation) all change the
  output, so I didn't use them.
- **Memoising SDFs between consecutive effects**: not attempted. After the crop and one-sided
  changes, the only re-transform left is roughen or grow with regions < 1. There `sdf_below` and
  then `sdf_pair` transform the same mask on different boxes. That costs a few ms per such effect,
  and a cache keyed on mask identity is fragile.
- **Recomputing `frame.fill(src)`**: not an issue in my files. The source `GlyphState` is shared by
  every `base: source` stop and caches its rasterised ink lazily, so the fill runs once per glyph.
  compat's per-loop fills are compat's own; they now benefit from the bbox-local `winding`.
- **Smear's `ndimage.rotate`, wave's `map_coordinates` and the `_drag` loop** are small now and were
  left alone.
- **`run()` parallelism**: worker init is trivial (`load_font` from bytes takes about 0 s;
  `font_to_bytes` in the parent takes about 0.8 s once). With load average 10–38 from the other
  agent, a 240-glyph `run(jobs=7)` reached only 2.7× over serial. That measures contention, not
  chunk size, so I left the chunking unchanged. Stops within a glyph chain on the previous stop
  (`base: previous`), so they are not independent work.

## Risks

- **Noise is not bitwise identical** (about 1e-15). A pixel whose field sits exactly on a threshold
  could in principle flip. That never happened across the bench, and the output stays
  deterministic.
- **`sdf_box`, `sdf_pair` and `edt_out`** return 1e6 outside their box or reach (a lower bound).
  They are only valid for the threshold/sign uses documented in their docstrings.
  `sdf_below(mask, t)` is the safe entry point: it is exactly `sdf(mask) < t`.
- **`GlyphState.mask` is cached**, so code must not write into `st.mask` or `st.ink` in place.
- **`trace` returns copies** of slices of a packed array, so nothing aliases (same as before).
