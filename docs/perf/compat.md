# compat.py performance pass

Scope: `engine/src/fontmanip/compat.py` only. `compatibilise(...)` keeps the same signature and return value.
New tests are in `engine/tests/test_compat_perf.py`. `uv run --group dev pytest -q` passes (47 tests at the time of writing, including the other agent's new ones).

## How it was measured

Another agent was optimising effects/raster/outline/pipeline in the same tree at the same time, and the
load average reached about 37 on 8 cores. Wall-clock numbers were therefore very noisy; for example,
melt-drip took 0.27 s/glyph in one run and 0.57 in another with identical code. Three things were used
to get usable numbers:

1. **Isolated compat timing.** The exact `compatibilise` inputs (frame, per-stop loops, masks) were dumped
   for all 7 bench cases x 12 glyphs, using the current effects code. The original `compat.py` and the new
   one were then run back to back on those same inputs, timed with `process_time`. This also checks
   whether the output is bit-identical and recomputes fit and morph the way `pipeline.process_glyph` does.
2. **cProfile** cumulative time of `compatibilise`.
3. **Attribution run.** `bench.py` was run on the current tree twice with the new compat and twice with
   `pipeline.compatibilise` patched back to the original compat. Any quality change between those runs
   comes from compat alone.

### compatibilise alone (same inputs, same run, s/glyph, process time; one run under load, so ratios matter more than absolute values)

| case | original | new | speed-up |
|---|---|---|---|
| default | 0.212 | 0.154 | 1.4x |
| motion-smear | 0.127 | 0.090 | 1.4x |
| mud-inflate | 0.690 | 0.233 | 3.0x |
| melt-drip | 0.038 | 0.027 | 1.4x |
| marker-bleed | 0.011 | 0.006 | 1.8x |
| kitchen-sink | 1.858 | 0.622 | 3.0x |
| fraunces-default | 0.205 | 0.152 | 1.3x |

cProfile cumulative `compatibilise` time, 12 glyphs. Original and new were run back to back, twice, in
one process on the current dump:

| case | original | new |
|---|---|---|
| mud-inflate | 12.56 / 12.40 s | 4.65 / 3.89 s |
| kitchen-sink | 26.91 / 28.10 s | 9.87 / 9.07 s |
| default | 2.83 / 2.77 s | 1.96 / 1.78 s |

### bench.py, whole pipeline (current tree, best of 2 runs; wall clock, noisy)

Quality columns show baseline / new. They are deterministic and the same in both runs.

| case | baseline s/g | tree + original compat | tree + new compat | fit_mean | fit_min | morph_mean | points |
|---|---|---|---|---|---|---|---|
| default | 0.240 | 0.278 | 0.219 | 0.9904 / 0.9904 | 0.9755 / 0.9762 | 0.8501 / 0.8500 | 3608 / 3608 |
| motion-smear | 0.683 | 0.445 | 0.432 | 0.9848 / 0.9848 | 0.9429 / 0.9429 | 0.9216 / 0.9218 | 6645 / 6645 |
| mud-inflate | 1.925 | 1.368 | 0.861 | 0.9651 / 0.9652 | 0.9526 / 0.9526 | 0.8776 / 0.8776 | 81192 / 81192 |
| melt-drip | 0.402 | 0.270 | 0.284 | 0.9963 / 0.9963 | 0.9934 / 0.9934 | 0.8849 / 0.8849 | 3518 / 3519 |
| marker-bleed | 0.364 | 0.263 | 0.263 | 0.9915 / 0.9915 | 0.9867 / 0.9867 | 0.9580 / 0.9580 | 2866 / 2866 |
| kitchen-sink | 2.371 | 3.135 | 1.473 | 0.9166 / 0.9166 | 0.8376 / 0.8377 | 0.7501 / 0.7503 | 123253 / 123262 |
| fraunces-default | 0.223 | 0.292 | 0.214 | 0.9887 / 0.9887 | 0.9700 / 0.9707 | 0.8085 / 0.8081 | 3823 / 3822 |

The largest quality change is fraunces-default morph_mean at -0.0004, well inside the 0.003 limit.
fit_min improved slightly on default and fraunces. Output is deterministic; a test checks this.

## What changed, and why

All of these except item 6 give **bit-identical output** to the original. This was checked by
disabling item 6 in a copy of the final file and comparing it with the original on all 7 cases x 12
glyphs of the current dump: every case was identical. Each change also has a reference-equality test
in `test_compat_perf.py`.

1. **Cropped fills (`Fill`, `fill_crop`).** Each contour's raster fill used to be a full-frame bool mask,
   and every overlap, area and anchor computation scanned the whole frame. Pits and spatter create
   thousands of these per glyph. A fill now stores a bounding box, a sub-mask and its area. `fill_crop`
   runs the same exact scanline-winding algorithm as `Frame.fill`, but only over the box that can be
   nonzero, and gives the same pixels. Full-frame masks are only built where they are really needed
   (`_labels` and `_cut` during splits and merges, which are rare).
2. **Matching without the all-pairs pixel work.** Track-to-loop candidates are pre-filtered with a
   vectorised bounding-box intersection test, so pixels are only compared for pairs whose boxes touch.
   Areas are cached. `_split_merge`'s island x island intersection table is now sparse and built the
   same way. Its "best parent" and "best child" selection reproduces `max(..., key=...)` first-max
   tie-breaking exactly. Before this, the table counted full-frame `&` results for every pair.
3. **`_labels_for` vectorised.** It used one full-frame `pockets == c` pass per pocket, so a split glyph
   with hundreds of pits did hundreds of full-frame passes. This was 9.4 s of kitchen-sink's 33 s in
   the profile. It is now one 2D `bincount` over (pocket, label) plus a row `argmax`, which keeps the
   same tie-break. In merges it also ran once per parent with identical arguments; it now runs once.
4. **Corner detection once per loop.** `find_corners` was called twice per loop, once for the point
   count and once inside `resample_corners`. `_corners` now computes the fine resample, the corner
   indices, the perimeter and the arc length in one pass, using the same arithmetic. That result is
   reused for K, for `resample_corners`, and for the no-corner `resample` fallback. The nine `np.roll`
   calls and the `np.stack` window max became cyclic-pad slicing plus a `sliding_window_view` max.
   `resample_corners` builds its sample positions with one vectorised expression instead of a
   per-corner list comprehension. `np.r_`/`np.c_` were removed from the hot paths because their Python
   overhead mattered.
5. **`_anchor` on crops plus a per-fill cache.** The EDT runs on exactly the window the original used:
   the tight bbox of fill ∩ safe plus a 1px border, clipped to the frame. So the argmax pixel and its
   tie-breaks are the same. When the whole fill is inside the next stop's ink, which is common for specks
   that vanish, or none of it is, the result is the deepest pixel of the fill itself. That is cached on
   the `Fill`, so a track missing from 3 stops does one EDT instead of 3. The fallback (the mean of the
   resampled loop) is now only computed when it is used.
6. **Split cells are traced only around the loop being cut (`_cell(..., clip=fill)`).** This one is
   *not* bit-identical. A cell is a nearest-seed partition of the whole frame. It used to be traced and
   Douglas-Peucker-simplified over the full frame for every (child, stop), and that dominated the
   default and Fraunces cases (pixel-grid splits). Now only the loop's bbox plus 3px is traced. The box
   comes from the loop's pixel-centre fill; the 3px pad covers the sub-pixel overhang of the vector
   loop, so the cell outline outside the box should not affect loop ∩ cell. What changes is the 0.5px
   Douglas-Peucker vertex choice, and pathops gets smaller inputs. This took about 25% off compat time on default.
   Quality: the table above, at most 0.0004 change on any mean.

## Tried, and not kept

- **A thin-region fast path in `_anchor`.** If no pixel has all 4 neighbours inside, every EDT value is 1,
  so the argmax could be taken without running the EDT. It was identical but gave no measurable gain
  (most anchor regions aren't that thin, and EDT on a tiny crop is cheap), so it was removed.
- **Fewer points for tiny contours** (minimum point count of 8 or 10 instead of 12 for specks).
  - It does not help compat time, which is dominated by per-contour overhead, not by K.
  - At 8 points, mud-inflate fit_mean dropped by 0.0039, which breaks the rule.
  - At 10 points it stays within the rules (mud-inflate fit -0.0018, fit_min -0.003; kitchen-sink fit
    -0.0017) and has 12-14% fewer points, so smaller fonts. It uses up most of the quality budget for
    no speed gain, so it was not adopted. It's an easy option if smaller font files matter more:
    `K = max(10, ...)` for contours whose `perimeter / step < min_points`.

- **Not tried:** skipping corner detection for specks. It changes the `oncurve` flags and so the
  rendering, and corner work is now a small share of compat time.

## What's left in compat (kitchen-sink profile after the changes, about 5 s for 12 glyphs at lower load)

- `pathops.op` inside `_cut`, about 1 s. The loop being cut is the dense traced outline; simplifying
  it would change output.
- Per-contour fixed overhead, about 0.5-0.7 s each: `fill_crop`, `_corners`, `resample_corners`, and
  the anchor EDTs (about 19k small EDTs).
- `_labels`: one full-frame EDT per split child, about 0.3-0.5 s.

Going further would mean batching tiny contours across tracks, for example resampling all speck tracks
with the same K in one vectorised call. That is a larger restructuring.

## Risks

- `fill_crop` copies `Frame.winding`'s scanline arithmetic instead of calling it, so it can return just
  the box. If `raster.py` ever changes the fill rule or the pixel-centre convention, `fill_crop` has to
  change with it. `test_fill_crop_matches_frame_fill` checks pixel equality with `Frame.fill`, including
  loops that cross the frame edge, specks and multi-loop fills, so a divergence will fail the tests.
- Item 6 (clipped cell tracing) is lossy. It was validated on 12 glyphs x 7 recipes only. The backstop
  is `_cut`'s existing check: if the vector result differs from the raster truth by more than 3% of
  pixels, `_cut` falls back to the raster trace.
- `Track.fills` now holds `Fill` objects instead of full-frame arrays. Nothing outside compat.py reads
  `Track`; checked with grep.
- `find_corners`, `resample`, `resample_corners`, `align`, `best_shift` and `perimeter` keep their
  signatures. `resample_corners` gained an optional `corners=` argument.
