# 03 — Catalogue of outline-manipulation techniques (for a Ferro-style "decay" generator)

> Compiled 2026-11-23 by a research agent. URLs marked *(memory)* were not seen in a search or fetch during research and should be verified before citing. Every other URL was seen.

Scope: techniques for manipulating quadratic and cubic Bézier glyph outlines, with notes on point compatibility so that stages can become a variable-font "decay" axis.

---

## 0. What the sources say about Ferro

Quoted verbatim from https://plain-form.com/articles/expired-by:

- Magnet Mike is "a vector distortion program imagined and developed by Arman Mohtadji … a shape drawn with a mouse or trackpad is snapped onto a grid whose resolution can be adjusted."
- "Depending on the density of the grid, the resulting forms range from slightly degraded to almost completely abstract."
- The scripts work by "repeatedly forcing letterforms through grids of varying resolutions."
- "unlike a rigid pixel grid, this system does not enforce strict orthogonality or uniform alignment. The grid behaves more like a field of constraints than a fixed structure: oblique tensions, intersecting paths, and local approximations constantly shift the geometry."
- "four stages of transformation, where each step moves the form further away from its source condition", described as "replacing traditional notions of weight with progressive stages of digital decay."

Slanted (https://www.slanted.de/ferro/) credits design to Lucas Descroix and Bonjour Monde, with team members Benjamin Dumond and Arman Mohtadji. Ferro has 24 styles and about 540 glyphs, released 2026.

**Not documented anywhere found:**
- Magnet Mike's actual algorithm. No public repo was found.
- The grid sizes used.
- Whether Ferro is variable or point-compatible.

Everything below about "magnet" behaviour (snap radius, pull strength, attractors) is a **proposed model**, not a description of their tool.

---

## 1. Foundations

### 1.1 Data model

A glyph is a list of contours. Each contour is a cyclic list of points `(x, y, type)`, where `type ∈ {on, offcurve}`, plus a `smooth` flag.

Read and write glyphs with fontTools pens:
- https://fonttools.readthedocs.io/en/latest/pens/index.html
- TransformPen
- ReverseContourPen

Use fontParts or defcon for UFO editing. In JS, opentype.js exposes path commands.

### 1.2 The point-compatibility rule

A technique keeps masters interpolatable only if it is a **pure per-point coordinate map** `p -> f(p)` on a fixed structure. The fixed structure is:
- the same contour count and order
- the same point count and point types
- the same start points
- the same components

**Preserves compatibility:**
- grid snapping and attractor pulls, even when points collapse onto each other
- noise evaluated at point positions
- affine transforms
- lattice/FFD and envelope warps
- interpolation and extrapolation
- normal-direction "fake offset"
- any structural edit (for example splitting every curve at t=0.5) applied identically to the source before the stages diverge

**Breaks compatibility:**
- resampling or flattening by length
- RDP/Visvalingam simplification
- curve re-fitting
- raster-and-retrace
- true offsetting
- boolean operations, including remove-overlaps
- adding extrema per master
- deleting duplicate, collinear or tiny contours per master
- per-master direction correction

**Traps:**
1. Snapping can make two consecutive points coincide. That is still compatible, until a clean-up step deletes the duplicate in one master only.
2. Every stage master must come from the same source topology. Do structural preparation once, on the source.
3. Snapping can collapse or invert a counter (its signed area flips). Interpolation then passes through zero area, and nonzero-winding fill may turn the counter black.
4. Interpolating between stage k and stage k+1 is linear. Intermediate instances are not on any grid, so they look like a "melt". Add intermediate masters if you want stepped behaviour.
5. For TrueType variable fonts, convert cubic to quadratic jointly across all masters with `fontTools.cu2qu.ufo.fonts_to_quadratic` (https://fonttools.readthedocs.io/en/latest/cu2qu/index.html), or ship CFF2 instead.
6. Validate with `fonttools varLib.interpolatable`. It can report false positives.

### 1.3 Determinism

- Use `random.Random(seed_for(font_seed, glyph_name, stage, pass_idx))`.
- Derive seeds with `hashlib` or `zlib.crc32`, not `hash()`, which is randomised per process (PYTHONHASHSEED).
- Prefer coordinate-keyed noise `noise(x*f, y*f, seed)`. Shared shapes then decay consistently across glyphs, and results don't change when the glyph set changes.

---

## 2. Point/vertex grid quantisation ("magnet" snapping)

### 2.1 Orthogonal grid snap

Formula: `q = origin + cell * round((p - origin) / cell)`.

**Parameters:**
- cell size (x and y can differ)
- origin/phase
- which points to snap

**Off-curve policies (the key design choice):**
- (a) Snap off-curves too. Gives kinks; the most "digital" look.
- (b) Carry each handle rigidly with its anchor.
- (c) Retract handles proportionally.
- (d) Collapse handles onto their anchors at higher stages. This keeps the point structure.

**Pitfalls:**
- Small counters collapse or invert.
- Hairlines snap to zero width.
- Overlaps appear.
- Snapping is idempotent on the same grid, so repeated passes must change scale, phase or rotation.

**Compatible:** yes.

### 2.2 Rotated, skewed or arbitrary-basis lattices (oblique tension)

With basis `M = [a b]`: `u = M^-1 (p - o)`, then `q = o + M round(u)`.

For a strongly skewed basis, test the 4 neighbouring lattice points and keep the nearest. Two passes at different angles give the "intersecting paths" effect.

**Compatible:** yes.

### 2.3 Triangular and hex lattices

These give 60°/120° crystalline facets. Use cube-coordinate rounding as described by Red Blob Games:
- https://www.redblobgames.com/grids/hexagons/implementation.html
- https://redblobgames.com/grids/hexagons-v2/more-pixel-to-hex.html

**Compatible:** yes.

### 2.4 Polar grids

Convert to polar coordinates `r, φ` around a centre `c`, then quantise:
- `r' = dr * round(r / dr)`
- `φ' = dφ(r) * round(φ / dφ(r))`, where `dφ = arc / r`

The result looks radial or "lathed". Use a single world-space centre so all glyphs stay consistent. There is a singularity at the centre.

**Compatible:** yes.

### 2.5 Noise-displaced grid

Snap to node `n`, then output `n + A * noise2(n * f, seed)`. Keying the noise by node index keeps shared edges coherent. Keep A ≤ 0.4 cell.

**Compatible:** yes.

### 2.6 Voronoi and attractor fields: "magnet" radius and strength

```
sites = poisson_disc(bbox, min_dist, rng)   # or jittered grid, or hand-placed attractors
for p in points:
    q = nearest(sites, p)                    # KD-tree
    d = |q - p|
    if d < R:
        w = strength * falloff(d / R)
        p' = p + w * (q - p)
```

**Variants:**
- Use K-nearest weighting.
- Use repulsors (negative strength).
- Snap to the nearest *line* from several oblique line families. This gives long straight oblique runs, which is a plausible reading of "oblique tensions".
- Quantise segment angles to multiples of Δθ.

**Pitfalls:** many points converging on one site produce spikes and zero-length segments.

**Compatible:** yes.

### 2.7 Making snapping decay well

- **Pre-subdivide** every segment identically on the source (for example split each curve at t=1/2). More points means snapping produces stair-steps and texture, not just moved corners.
- **Signed-area guard per contour:** if |area| drops below ε or its sign flips, either drop the contour (static output) or re-run that contour with a finer grid (variable output).
- **Thickness-aware strength:** cap displacement at about 0.4 × local half stroke width.

---

## 3. Flatten, quantise, simplify, re-fit

### 3.1 Flattening by arc length

1. Subdivide adaptively.
2. Build a cumulative-length table.
3. Resample every L units, keeping corners.

References:
- https://pomax.github.io/bezierinfo/
- https://pomax.github.io/bezierjs/
- https://raphlinus.github.io/curves/2018/12/28/bezier-arclength.html
- Geomerative UNIFORMLENGTH: https://discourse.processing.org/t/geomerative-text-question/24076/5

**Compatible:** breaks, unless you use a fixed count per segment, applied on the source.

### 3.2 Then quantise (pixel look)

Flatten at L ≤ cell/2, snap, then remove duplicate and collinear points. This gives a true staircase. Keep the duplicates instead if the stage must stay compatible.

### 3.3 Simplification: RDP and Visvalingam-Whyatt

The result is low-poly letters.
- RDP: https://en.wikipedia.org/wiki/Ramer%E2%80%93Douglas%E2%80%93Peucker_algorithm
- Visvalingam-Whyatt (Cartographic Journal 30(1) 1993): https://en.wikipedia.org/wiki/Visvalingam%E2%80%93Whyatt_algorithm

shapely's `simplify(preserve_topology=True)` avoids self-intersections.

**Compatible:** breaks.

### 3.4 Curve re-fitting

**Schneider (Graphics Gems 1990):** a least-squares cubic fit that splits at the point of maximum error. It is used by paper.js `simplify` and Inkscape. Ports:
- fit-curve (JS): https://npmjs.com/package/fit-curve
- simplify-rs: https://docs.rs/crate/simplify-rs/latest/source/src/lib.rs
- Python gist: https://gist.github.com/gumblex/047abdd163c3a3c96b64

**fontTools conversions:**
- qu2cu `quadratic_to_curves(quads, max_err=0.5)`: https://fonttools.readthedocs.io/en/latest/qu2cu/index.html
- cu2qu `curves_to_quadratic` and `fonts_to_quadratic`

**Visual:** gives "melted", smooth decay after snapping or tracing. Split at corners first.

**Compatible:** breaks (except a joint cu2qu).

---

## 4. Raster and retrace

### 4.1 Pipeline

1. **Rasterise** at 12–80 px/em. Use freetype-py, skia-python, PIL, or your own scanline rasteriser. For "oblique pixels", rasterise in a rotated or sheared frame and transform back afterwards.
2. **Pre-process:**
   - Blur σ, then threshold t. t < 0.5 gives bleed; t > 0.5 gives erosion.
   - Morphology (https://scikit-image.org/docs/stable/api/skimage.morphology): opening removes spurs and fine serifs; closing fills ink traps and narrow counters.
   - Add noise before thresholding for ragged edges.
3. **Trace:**
   - Marching squares, `skimage.measure.find_contours(img, 0.5)` (https://scikit-image.org/docs/0.24.x/auto_examples/edges/plot_contours.html). Pad the image first.
   - Potrace (https://potrace.sourceforge.net/potrace.pdf, GPL), with `alphamax` and `opttolerance` controls.
   - VTracer (https://github.com/visioncortex/vtracer), with pixel, polygon and spline modes.
   - Pixel-exact cell-boundary tracing for a true bitmap look.
4. **Scale back** to font units, flip y, re-fit curves and fix direction.

### 4.2 Signed-distance-field variant (recommended)

Build the SDF (EDT inside minus EDT outside), then edit the field:
- add δ for weight
- add noise for roughness
- take min/max for booleans
- blur for rounding

Contour the result at level 0. One operator covers weight, spread, erosion and rounding.

### 4.3 Ink spread and ink traps

- **Spread:** blur, then a low threshold. Acute inner corners fill in.
- **Ink traps:** `closing(shape) − shape` finds where ink pools; cut notches there. This is a proposal with no specific source.

### 4.4 Pitfalls

- Point structure is lost.
- Point counts explode.
- Hole nesting is unknown, so use containment tests or OpenCV `RETR_CCOMP`.
- Winding direction is arbitrary.
- The image must be flipped in y.
- All glyphs must share a world-space pixel phase, or glyphs come out inconsistent.

**Compatible:** breaks. These stages must ship as static instances.

---

## 5. Offsetting, stroking and boolean patterns

### 5.1 Outline offset

**True offset:** Clipper2 (https://angusj.com/clipper2/Docs/Overview.htm, https://github.com/AngusJohnson/Clipper2), or pyclipper.

Editor equivalents:
- Glyphs Offset Curve (https://handbook.glyphsapp.com/filters/built-in/, https://glyphsapp.com/learn/monoline)
- RoboFont Outliner (https://typemytype.github.io)

The offset of a cubic is not a cubic, so every offset is an approximation. See https://blend2d.com/research/precise_offset_curves.pdf.

**Compatible fake offset:** move each on-curve point by `d·n̂` (the averaged normal) and move its handles with it.

**Pitfalls:**
- An offset of more than half the stroke width destroys hairlines and counters.
- Joins add points.
- Self-intersections occur where the curvature radius is smaller than |d|.

### 5.2 Boolean patterns

- **Stencil gaps:** subtract bars at the narrowest points or at seeded positions, possibly oblique.
- **Scanlines:** intersect with stripes (https://handbook.glyphsapp.com/filters/hatch-outline/).
- **Halftone/dot-matrix:** at each lattice node, place a dot with `r = r_max·sqrt(coverage)`.
- **Shatter:** intersect with Voronoi cells and jitter the fragments.

**Libraries:**
- skia-pathops (https://pypi.org/project/skia-pathops)
- booleanOperations (https://github.com/typemytype/booleanOperations)
- RoboFont boolean glyph math (https://www.robofont.com/documentation/topics/boolean-glyphmath)
- paper.js

**Pitfalls:** point explosion and slivers (filter them by area).

**Compatible:** breaks.

---

## 6. Noise displacement, roughen, jitter, bleed, erosion

### 6.1 Coherent noise displacement

`p' = p + A·(n(p·f+s1), n(p·f+s2))`, using simplex or OpenSimplex noise, fBm octaves, and domain warping.

**Normal-only variant:** `p' = p + A·n(p)·n̂`.
- Biased inward, it erodes.
- Biased outward, it bleeds.
- `sign(n)|n|^γ` with γ > 1 gives sparse "ferrofluid" spikes.

**Libraries:**
- opensimplex (https://pypi.org/project/opensimplex)
- noise (https://pypi.python.org/pypi/noise)

**Compatible:** yes, on the original points.

### 6.2 Roughen and jitter

Flatten into segments of length L, then jitter each node by ±(dx, dy). Glyphs' Roughen filter does this (https://handbook.glyphsapp.com/filters/roughen/).

**Compatible:** breaks after flattening; compatible if you jitter only the original points.

### 6.3 Generational erosion

Loop blur → threshold → noise → re-trace, with a different seed and pixel phase each generation. This makes "transmission and reproduction" literal. Make the amplitude thickness-aware.

### 6.4 Prior art

- NaN Glyph-Filters: 28 procedural OFL fonts plus Glyphs scripts, using simplex noise (https://github.com/NaN-xyz/Glyph-Filters). A Glyphs 3 fork is at schriftgestalt/Glyph-Filters.
- mekkablue Noodler: https://github.com/mekkablue/Noodler
- mekkablue Glyphs Scripts: https://github.com/mekkablue/Glyphs-Scripts

---

## 7. Interpolation, extrapolation and transforms

### 7.1 Interpolation and extrapolation

`P(t) = A + t(B−A)`. A value of t > 1 extrapolates, so it over-decays.

**Tools:**
- fontMath
- MutatorMath (https://pypi.org/project/MutatorMath/2.0.2)
- ufoProcessor (https://pypi.org/project/ufoProcessor)
- varLib (no extrapolation in variable fonts)

### 7.2 Skew, condense/extend, contrast

- **Skew:** `x' = x + (y−y0)·tanθ`.
- **Condense/extend:** use weight compensation, as in MutatorScale (https://github.com/frankrolf/MutatorScale).
- **Contrast:** use normal moves that depend on direction.

**Compatible:** yes.

### 7.3 Warps

Envelope (bilinear or Coons) and lattice FFD (Sederberg & Parry, SIGGRAPH '86, https://people.engr.tamu.edu/schaefer/teaching/689_Fall2006/p151-sederberg.pdf).

A seeded jittered lattice per stage is a smooth version of the "field of constraints". Subdivide first for accuracy, and don't let the lattice fold.

**Compatible:** yes.

---

## 8. Iterative and staged application

```
src = prepare(source)    # decompose, extrema, start points, uniform subdivision: structure frozen here
stage[0] = src
for k in 1..4:
    g = stage[k-1]                         # cumulative lineage
    for pass i in passes(k):
        grid = make_grid(kind, cell=c0*growth^k, angle=θ_k_i, phase=rng(seed,k,i))
        g = magnet_snap(g, grid, R=R_k, strength=s_k)
        g = noise_displace(g, A_k, f_k, seed_k)
    stage[k] = g
```

- Snapping is idempotent, so variation must come from changing angle, phase, cell and lattice type. Alternating oblique lattices gives "intersecting paths".
- Cumulative stages read as a lineage; independent stages are easier to tune.
- Raster and boolean stages ship as static fonts.
- Store every parameter in a JSON "recipe" so results are reproducible.

---

## 9. Clean-up afterwards

### 9.1 Operations

**Remove overlaps:**
- `fontTools.ttLib.removeOverlaps` (https://fonttools.readthedocs.io/en/latest/ttLib/removeOverlaps.html)
- `pathops.simplify`
- ufo2ft filters (https://pypi.org/project/ufo2ft/3.9.0/)

Do **not** remove overlaps in variable-font masters.

**Direction:**
- CFF and UFO outer contours are counter-clockwise; TrueType outer contours are clockwise.
- Fix with signed area plus nesting depth, then ReverseContourPen.
- In variable fonts, fix on the source only.

**Static output only:**
- remove tiny contours, duplicate points and collinear points

**Do on the source:**
- add extrema

**Composites:**
- Round coordinates last, then re-check areas.
- Decompose composites before applying world-space operations.

### 9.2 Metrics

- Keeping advance widths fixed keeps kerning valid.
- Alternatively keep LSB/RSB fixed and recompute the advance.
- Transform anchors with the same map.
- Re-check vertical metrics.

---

## 10. Broader variation ideas

Stencil, inline/outline, shadow/extrude, hatching, monoline from a skeleton (`skeletonize` then stroke), noodles, dot-matrix/halftone, shatter, ferrofluid spikes, polar lathe, multi-resolution pixel fonts, rounded corners/chamfers, and confusable-glyph swaps (https://github.com/NaN-xyz/Confusify).

---

## 11. Summary table

| Technique | Visual effect | Point-compatible? | Difficulty | Library |
|---|---|---|---|---|
| Orthogonal grid snap | Faceted, "digital" | Yes | Easy | numpy + pens |
| Rotated/skewed lattice snap | Oblique facets | Yes | Easy | numpy |
| Tri/hex lattice snap | Crystalline facets | Yes | Easy–Med | Red Blob |
| Polar grid snap | Radial/lathed | Yes | Med | numpy |
| Noise-displaced grid | Warped screen | Yes | Med | opensimplex |
| Attractor magnet (R, strength) | Clusters, spikes | Yes | Med | KD-tree |
| Oblique line-family snap | Long oblique runs | Yes | Med | numpy |
| Uniform subdivision on source | Stair-steps after snapping | Yes | Easy | fontTools |
| Arc-length flatten + snap | Pixel staircase | No | Med | own / Bezier.js |
| RDP / Visvalingam | Low-poly | No | Easy | shapely |
| Schneider re-fit | Melted, smooth | No | Med | paper.js / fit-curve |
| cu2qu / qu2cu | Format conversion | Yes, if joint | Easy | fontTools |
| Raster → marching squares | Blobby retrace | No | Med | scikit-image |
| Raster → potrace / vtracer | Smooth or pixel retrace | No | Med | potrace, vtracer |
| Blur / threshold / morphology | Spread, starvation | No | Med | skimage |
| SDF edit + iso-contour | Weight, rough, round | No | Med–Hard | scipy + skimage |
| Ink-trap generation | Notches | No | Hard | skimage + pathops |
| True offset / stroke | Weight, outline | No | Med | Clipper2 |
| Normal "fake offset" | Approximate weight | Yes | Easy | own |
| Boolean stencil / scanline / halftone / shatter | Cuts, stripes, dots | No | Med | skia-pathops |
| Simplex noise displacement | Wobble, bleed, spikes | Yes | Easy | opensimplex |
| Roughen (flatten + jitter) | Rough edges | No | Easy | own |
| Interpolation / extrapolation | Intermediates | Yes (requires it) | Easy | fontMath, varLib |
| Skew / condense / contrast | Variants | Yes | Easy–Med | TransformPen, MutatorScale |
| Envelope / FFD warp | Smooth distortion | Yes | Med | own |
| Staged passes, seeded RNG | Lineage | Yes, if point-maps | Med | own |
| Remove overlaps | Clean static output | No | Easy | skia-pathops |
| Direction / tiny / collinear / extrema clean-up | Valid output | No per master | Easy | fontTools, ufo2ft |

## Sources

Listed inline above. Unverified, from memory: paper.js simplify docs, the opentype.js repo, Geomerative, pyclipper, potracer, shapely, scipy cKDTree/EDT, OpenCV findContours, fontParts correctDirection, freetype-py, skia-python.
