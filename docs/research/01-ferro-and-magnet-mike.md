# 01 — Ferro, Magnet Mike, and precedents

> Compiled 2026-11-23 by a research agent.
>
> Legend:
> - **[V]** read in source code or on the page
> - **[M]** measured from Ferro's public webfont files with fontTools (measurement only; they are commercial fonts)
> - **[I]** inference
> - **[S]** search snippet only

**Headline:** Magnet Mike's public code is GPL and short, and it has been read in full. It includes a font version (`magnetface`) and a 2019 "FerroForge" workshop tool by Arman Mohtadji / Bonjour Monde. Ferro's own production scripts are **not** public. Its stage construction was reverse-engineered by measuring the webfonts.

## Article summary (https://plain-form.com/articles/expired-by, Lucas Descroix, 2026-06-06)

- **Starting point:** an unfinished family begun in 2015 at HEAR. Its source is *Annales Suevici* (Martin Crusius, 1595, printed by Nicolas Bassaeus), which mixes roman and italic with Fraktur.
- **First use:** display face for *CodeX* #02 "3615 Love" (PAMAL, media archaeology, the Minitel). References include Eduardo Kac's Videotext Poems.
- **Tool:** Magnet Mike: "a shape drawn … is snapped onto a grid whose resolution can be adjusted". Dense grids give slight degradation; coarse grids give near-abstraction. It was used in workshops and suited plotter output.
- **Method:** this became "a custom set of scripts acting less as drawing tools than as translation machines, repeatedly forcing letterforms through grids of varying resolutions."
- **The grid:** "a field of constraints [rather] than a fixed structure: oblique tensions, intersecting paths, and local approximations constantly shift the geometry."
- **Staging:** "four stages of transformation, where each step moves the form further away from its source condition", "replacing traditional notions of weight with progressive stages of digital decay."
- **Aesthetic:** "Erosion is not treated as damage, but as a productive force." It sets the coldness of systems against a poetic, anti-utilitarian dimension. The name comes from ferrofluids ("unstable spikes and granular formations").

---

## 1. Magnet Mike algorithm

### 1a. v1: browser tool (https://armansansd.net/tools/magnet/index.html) [V]

- **Stack:** p5.js + p5.svg + jQuery UI. Header: "Magnet Mike, 06 july 2018, armansansd, GPL". Repo: https://gitlab.com/armansansd/magnet.
- **Input:** freehand mouse strokes on a 500×600 canvas. Each drag becomes a polyline of raw samples.
- **Grid:** a lattice of **cell centres**. The slider value `n` (1–100) gives n columns × n rows, so cells are `w/n × h/n`: non-square and anisotropic.
- **Snap radius:** half the cell diagonal. In practice every point snaps to its nearest node (Voronoi quantisation). Ties go to iteration order.
- **Dedupe:** order-preserving dedupe over the **whole stroke**. A node can be visited once only. Returning to a used node drops the point, and the polyline jumps to the next new node. This dedupe, plus fast strokes that skip cells, is the main source of the "oblique tensions" and "intersecting paths".
- **Output:** an open polyline through the snapped nodes, made of straight segments only. It is exported as SVG.

```
v1(strokes, n, W=500, H=600):
  cw, ch = W/n, H/n
  nodes = [((i+.5)cw, (j+.5)ch) for i,j in grid]
  for stroke in strokes:
     snapped = [argmin_node(dist(p, node)) for p in stroke]
     out = unique_preserving_order(snapped)       # global, not just consecutive
     draw polyline(out)
```

### 1b. v2 and the font versions [V]

- **Repos:**
  - https://gitlab.com/armansansd/magnetface ("transform any font in a magnet style")
  - https://gitlab.com/armansansd/dataface ("Vector-bending tool for experimental type")
  - https://gitlab.com/armansansd/Ensad-ferro ("FerroForge + Paged.js, workshop given at EnsAD, November 2019")
- **Dependencies:** numpy, svgpathtools, lxml, and FontForge for conversion.
- **Pipeline:** OTF → SVG font (FontForge) → `magnetface.py --grid N` → SVG font → OTF (FontForge). One variant also runs `ChangeWeight(10)`. FerroForge is a PHP upload form that only accepts `grid < 12`.
- **Grid, built per glyph:**
  - Columns: `interval_x = advance_width / N`.
  - Rows: `interval_y = font_height / N`, with `font_height = capHeight + |descent| + (|ascent| − 260)`. The code flags the −260 as a hack.
  - Nodes are N×N cell centres, so cell shape differs per glyph.
- **Sampling:** each segment is sampled at **80 evenly spaced t values**, and each sample snaps to the nearest node.
- **Dedupe:** order-preserving, within each segment and then across the contour. The result is rebuilt as **straight lines** and closed. Empty contours are dropped.
- **Multi-stage:** `magnet_array = [7, 9, 12, 20]` writes one font per resolution, **each computed from the original source**, not chained.

```
magnetface(glyph, N):
  W = glyph.advance; Hf = capH + desc + (asc - 260)
  nodes = [((i+.5)W/N, (j+.5)Hf/N - desc) for i,j in N×N]
  for contour in glyph:
     pts = []
     for seg in contour: pts += unique([nearest(nodes, seg.point(t)) for t in linspace(0,1,80)])
     pts = unique_preserving_order(pts)
     emit closed polygon(pts)
```

- **Artefacts [I]:** zero-area or self-intersecting contours, back-and-forth spurs, vanished counters, and winding that is ignored.
- **Licence:** GPL-3. If code is ported rather than reimplemented from the description, the GPL applies.

### 1c. How Ferro's pipeline probably works (measured from webfonts) [M]/[I]

Files analysed: `ferro.plain-form.com/assets/fonts/Ferro-Regular{I..IV}.woff2` and `Ferro-Italic{I,IV}.woff2`.

- **Format [M]:** CFF, UPM 1000, 669–670 glyphs, **no `fvar`**. These are static fonts. Point structures differ between stages, so stages cannot be interpolated. File size shrinks by stage (28.3k → 21.0k).
- **Stage I [M]:** still has curves. It is the clean source.
- **Stages II–IV [M]:** **lineTo only** (polygons).
- **Lowercase columns [M]:** x values sit on **cell centres of advance/N**, with N = **15, 10, 5** for stages II, III, IV (81–93% of points fit). Example: `o` (advance 550) at stage IV has x ∈ {55, 165, 385, 495} = (k+.5)·110. This matches magnetface's per-glyph centred columns.
- **Capitals columns [M]:** unresolved. The step is close to, but not exactly, advance/N, so caps may use the bounding box.
- **Rows [M]:** y values sit **on vertical-zone lines**, not cell centres.
  - Lowercase: x-height/6, /4, /2.
  - Caps: cap-height/7, /5, /3.
  - Ascender and descender snap to 730 and −200.
  - This zone anchoring is an addition beyond magnetface.
- **Cell shape [M]:** constant aspect ratio across stages. Resolution roughly halves each stage.
- **Obliques [M]:** segments are mostly 0° and 90°, plus a clear 45–75° bin. One-cell diagonal joins (≈66°) are the "oblique tensions".
- **Hairlines [M]:** one-sided 10-unit pairs (490/500, −200/−190). Cause unknown.
- **Extra post-processing [M]/[I]:** stage III `o` has 3 contours where the source has 2. There are degenerate back-tracking edges. Some hand editing is likely.
- **Italic [M]:** uses an upright grid, not a sheared one.
- **Chaining [I]:** stages were likely each computed **from the source**, not chained.

**Inferred Ferro recipe [I]:**
1. For each glyph, set columns to advance/Nx (Nx ∈ {15, 10, 5}) with nodes at cell centres. Anchor rows to baseline, x-height, cap-height, ascender and descender, with {6, 4, 2} rows per x-height.
2. Densely sample each contour and snap every sample to its nearest node.
3. Order-preserving dedupe, then close the polygon. This gives straight segments, including diagonals.
4. Clean up and handle overlaps, plus an unknown hairline step and likely some hand edits.

---

## 2. Ferro facts

- **Publisher and people [V]:** Plain Form, 2026. Design: Lucas Descroix (Bonjour Monde). Team: Benjamin Dumond and Arman Mohtadji.
- **Styles [V]:** Regular, Italic and Black are each released in stages I–IV. Black is a blackletter, named "Fraktur" in the webfonts. Mono versions follow the same scheme.
- **Count [V]:** 24 styles in total (Ferro 12 + Ferro Mono 12).
- **Axes [M]:** none.
- **Features [S]:** ss01, c2sc, dlig, lnum, tnum, frac, zero.
- **Licensing [V]:** tiers are by company size. Free test fonts are available. https://plain-form.com/licensing
- **Specimen (not read):** https://plain-form.com/media/pages/typefaces/ferro/296e42cb9d-1793282455/plainform-ferro-specimen.pdf

---

## 3. Precedents

1. **LettError, Beowolf / RandomFont (1989/90):** a "freakto" routine replaced lineto/curveto in PostScript and nudged points randomly at every print. It came in three intensities (21, 22, 23), so it is an early precedent for discrete stages. https://letterror.com/beowolf
2. **Bonjour Monde, DataFace / Syne Mono:** a vector-bending tool. Syne Mono was made by swapping on-curve and off-curve points. https://gitlab.com/armansansd/dataface, https://fontsinuse.com/typefaces/246219/syne-mono
3. **Climate Crisis (Helsingin Sanomat, 2019):** a variable font with a YEAR axis and 8 masters driven by Arctic sea-ice data. https://design.google/library/climate-crisis
4. **Koruption (Typogama, 2022):** a variable axis from regular to "decomposed/glitched", plus random alternates. https://atypi.org/presentation/exploring-the-limits-of-variable-fonts/
5. **Handjet (Rosetta):** a grid-module variable font whose ELGR axis changes grid resolution. https://github.com/rosettatype/handjet
6. **Glyphs pixel fonts / Round Coordinates:** the naive orthogonal baseline. https://glyphsapp.com/learn/pixelfont

## Sources

- plain-form.com: the article, the typeface page and the licensing page
- ferro.plain-form.com: HTML, CSS, JS and webfonts
- armansansd.net and https://armansansd.net/tools/magnet/
- gitlab.com/armansansd: magnet, magnetface, dataface, Ensad-ferro, plotter-magnet
- Precedent URLs as listed in section 3
