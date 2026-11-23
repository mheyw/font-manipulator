# 02 — Reading, editing and writing font files (state of the art, Nov 2026)

> Compiled 2026-11-23 by a research agent. Hands-on tests were run against Source Sans 3 (CFF, OFL) on macOS:
> - CFF OTF round trip
> - variable-font builds
> - Pyodide
> - opentype.js round trip
>
> Tags:
> - **[tested]** the agent ran it
> - **[read]** taken from the cited source
> - **[unverified]** background knowledge, not checked

## TL;DR

- **Python and fontTools is the only stack found that round-trips a CFF OTF without losing data.** GPOS, GSUB and GDEF survive, widths are correct, and the OTF and WOFF2 outputs are valid [tested].
- **fontTools also runs in the browser via Pyodide.** The same pipeline produced OTS-clean OTF and WOFF2 there [tested].
  - skia-pathops has no Pyodide wheel.
  - `booleanOperations` (pyclipper) works there for overlap removal [tested].
- **opentype.js 2.0.0 writes valid but lossy files** [tested]:
  - always CFF v1, with no subroutines
  - drops GPOS, GDEF and BASE
  - writes a GSUB that fontTools cannot parse
  - has no gvar writer
- **Variable fonts:**
  - Grid-snapping alone keeps masters compatible.
  - Overlap removal breaks compatibility. In one test varLib left 72 of 94 glyphs non-varying without raising an error; with CFF masters it failed outright.
  - Instances between masters are linear blends, so they don't sit on the grid.
  - Default to **static instances per stage**.

---

## 1. Python stack

| Package | Version (date) | Notes |
|---|---|---|
| fontTools | 4.68.0 (2026-10-27), needs Python 3.11+ | https://pypi.org/project/fonttools/ |
| skia-pathops | 0.9.2 (2026-02-16) | Native wheels only, no wasm |
| booleanOperations | 0.10.0 (2026-01-15) | Pure Python plus pyclipper |
| ufoLib2 | 0.19.1 | |
| defcon | 0.12.2 / 0.12.3 | |
| fontParts | 1.1.1 | |
| fontmake / ufo2ft / glyphsLib | 3.12.2 / 3.11.0 / 6.17.2 | |
| cffsubr | 0.4.0 | Re-subroutinizes CFF |

### Recent fontTools changes

- **4.68.0:** the instancer rounds CFF2 output; requires brotli ≥ 1.2.1 (security fix).
- **4.61.0:** adds `--remove-overlaps` for CFF2; fixes a path-traversal issue (CVE-2025-66034).
- **4.59.1:** `varLib.mutator` is deprecated in favour of `varLib.instancer`.

### Pens

https://fonttools.readthedocs.io/en/latest/pens/index.html

- Recording: RecordingPen, RecordingPointPen, `lerpRecordings`.
- Transforming and clean-up: TransformPen, RoundingPen, ReverseContourPen, FilterPen.
- Outline builders: T2CharStringPen (CFF), TTGlyphPen / TTGlyphPointPen (glyf).
- Curve conversion: Cu2QuPen / Cu2QuMultiPen.
- Measurement: BoundsPen, AreaPen, StatisticsPen, PointInsidePen.
- Output: SVGPathPen.

### Modifying a CFF glyph and re-saving [tested]

```python
font = TTFont(src); top = font["CFF "].cff.topDictIndex[0]; cs = top.CharStrings
gs = font.getGlyphSet(); hmtx = font["hmtx"]
for name in font.getGlyphOrder():
    rec = RecordingPen(); gs[name].draw(rec)
    adv = hmtx[name][0]; priv = cs[name].private
    w = None if adv == priv.defaultWidthX else adv - priv.nominalWidthX   # <-- critical
    pen = T2CharStringPen(w, gs)
    ...replay rec.value with transformed points...
    cs[name] = pen.getCharString(private=priv, globalSubrs=cs[name].globalSubrs)
    hmtx[name] = (adv, new_xMin)          # recompute LSB with BoundsPen
removeOverlaps(font, ignoreErrors=True)  # skia-pathops; drops hinting
font.save("out.otf"); font.flavor = "woff2"; font.save("out.woff2")
```

**Pitfall: CFF width encoding.** A charstring stores its width relative to `nominalWidthX`. Passing the raw advance made every glyph's width wrong, and **OTS did not notice**. The fix shown above resolves it.

The re-saved CFF is not subroutinized. Run `cffsubr` if file size matters.

### CFF vs CFF2 vs glyf

- **Static CFF:** keep per-glyph `private`/`globalSubrs` references and apply the width rule above.
- **CFF2:** charstrings carry no widths and use blend operators. Instance first (`instancer`, `downgradeCFF2=True`), or work from sources.
- **glyf:** quadratic outlines.
  - Pass cubic effect output through `Cu2QuPen` before TTGlyphPen.
  - Remove degenerate points.
  - After `recalcBounds`, set LSB = xMin.
- **Bounds:** `TTFont.save` recomputes bbox and maxp by default.

### Hinting

T2CharStringPen emits no hints. `removeOverlaps(..., removeHinting=True)` removes hinting from all glyphs. For TTFs, strip `fpgm`/`prep`/`cvt` or re-hint with ttfautohint.

### Overlap removal and effects

- skia-pathops provides `union`, `difference`, `intersection`, `xor`, `simplify` and `Path.stroke(...)`.
- **Erosion:** `difference(glyph, glyph.stroke(w))` [tested].
- booleanOperations' `removeOverlap` also works [tested].

### Renaming for a new family

- Set nameIDs 1, 4, 6, 16 (and 17), 3, and 25 for variable fonts.
- For CFF, also set `cff.fontNames` and the TopDict FullName and FamilyName.
- For variable fonts, update the STAT and fvar names.

### Builders

- `FontBuilder` (experimental, order-dependent) provides `setupCFF`, `setupCFF2`, `setupGlyf`, `setupFvar`, `setupGvar`, `setupStat`, `addOpenTypeFeatures`.
- `fontTools.subset` subsets fonts.
- `ttLib.woff2` needs brotli.

---

## 2. JavaScript / browser stack

| Library | Version | Read | Write |
|---|---|---|---|
| opentype.js | 2.0.0 (2026-05) | TTF, OTF, CFF2 (parse only), WOFF; WOFF2 needs a decompressor; variable font tables | **CFF v1 only**, no subroutines, no glyf, GPOS, GDEF or gvar |
| fontkit | 3.0.0 (2026-11), ESM | TTF, OTF, WOFF, WOFF2, TTC; variable instances | Subset encoding only |
| Typr.js | Unreleased; last push 2025-08 | Parse, shape, glyphToPath, variable fonts | None |
| harfbuzzjs | 1.6.4 (HarfBuzz 15.0.0) | Shaping, `drawGlyph` / `glyphToPath`, `setVariations` | Subset wasm, including axis pinning |
| fonteditor-core | 2.6.3 | OTF | TTF, WOFF, WOFF2 (converts OTF to TTF) |

**opentype.js round trip [tested]:**
- Dropped GPOS (kerning), GDEF and BASE.
- Wrote a GSUB that OTS accepts but fontTools cannot decompile.
- Kept glyphs, cmap and widths.

**Boolean operations in JS:**
- paper.js 0.17.0 (maintenance has resumed, 2026-11).
- path-bool 1.0.4 (described as early stage).
- Clipper2 (polygons only, strong at offsetting). The main repo has moved to PolyClipping and is C++ only. JS ports: `clipper2-wasm` 0.4.0, `clipper2-ts`.

**WOFF2 in wasm:** `woff2-encoder` 2.0.0 works [tested].

### fontTools in Pyodide [tested]

- Pyodide **314.0.8** (2026-11-13) bundles fonttools 4.62.1, brotli and pyclipper.
- skia-pathops is not available; booleanOperations installs through micropip.
- Snapping 2,478 glyphs and saving OTF and WOFF2 took about 2.7 s. Both outputs passed OTS.
- Startup took about 1.7 s in Node with a warm cache. A browser cold start also downloads several MB.

**Can a browser-only app round-trip an OTF?**
- Pyodide + fontTools: **yes, losslessly**.
- Pure JS: valid but lossy.

---

## 3. Variable fonts with a custom "DCAY" axis

- **Axis tag:** a custom tag must use uppercase letters or digits and start with a letter. `DCAY` is valid (https://learn.microsoft.com/en-us/typography/opentype/spec/dvaraxisreg).
- **Build:** a designspace with an `AxisDescriptor(tag="DCAY")`, then `varLib.build`, or `fontmake -o variable`.

**Experiments [tested]:**

| Masters | Result |
|---|---|
| CFF: hinted original + snapped | **Fail**: hint mismatch |
| CFF: both redrawn, `optimize=True` | **Fail**: the optimiser rewrites degenerate curves |
| CFF: both redrawn, `optimize=False` | **OK**: CFF2 variable font |
| CFF: snapped + removeOverlaps | **Fail** |
| glyf: Cu2Qu once, snap the same point list | **OK** |
| glyf: snapped + removeOverlaps | Builds "OK", but 72 of 94 glyphs **silently** become static |

**Interpolation is not snapping:** midpoints between masters lie off the grid.

**Options:**
- (a) A static font per stage. Simplest and always correct.
- (b) A variable font only for effects that keep the point structure.
- (c) Topology-preserving variants of the effects: collapse points instead of deleting them.
- (d) Fake steps with GSUB feature variations (`rvrn`), which swap alternate glyphs per axis range [untested].

---

## 4. Rust (brief)

- **fontations:** read-fonts 0.47, write-fonts 0.56, skrifa 0.50. **There is no CFF writer.**
- Also available: fontc 2.0.1, kurbo 0.13, linesweeper 0.5 (booleans), norad (UFO).
- Fontspector has a wasm web build.
- Viable for a glyf-only pipeline, but it means more custom code.

## 5. Validation

- **OTS** 9.3.0 is used by Chrome and Firefox. It is necessary but **not sufficient**: it missed the CFF width bug and the bad GSUB.
- **Font Bakery** 1.1.0, or **Fontspector** (Rust, with a wasm build).
- `ttx` dumps for diffing, `diffenator2` for visual diffs, `varLib.interpolatable` for master compatibility.
- **Suggested gates:**
  - OTS
  - re-open the output in fontTools and decompile every table
  - check CFF widths against hmtx
  - Font Bakery `universal`
  - treat varLib incompatibility warnings as errors

## 6. Licensing

- **Trial EULAs usually forbid modification.** Examples:
  - Type Supply: "Modifications to the Trial Fonts … are not allowed."
  - Fontfabric §4
  - Grilli, Dalton Maag and TypeType (from search summaries)
- **Commercial EULAs** often restrict derivative works and may count format conversion as one. Terms vary.
- **Implications for the tool:**
  - Process the user's own fonts under their own licence, with a notice.
  - Don't host or redistribute outputs.
  - On-device processing avoids the service ever receiving licensed font files.
- **SIL OFL 1.1** (https://openfontlicense.org/ofl-faq/):
  - Modification is allowed.
  - A Modified Version may not use a **Reserved Font Name**.
  - Derivatives must stay under the OFL.
  - Even subsetting or converting counts as modification.
  - Example: Source Sans reserves "Source". **Auto-renaming outputs should be the default.**

---

## Architecture options

### A. Python backend (fontTools + skia-pathops + fontmake) with a web UI

**Pros:**
- Most complete feature set.
- Best overlap removal.
- Font Bakery available.

**Cons:**
- User fonts are uploaded to a server, a licensing and privacy problem.
- Previews need round-trips to the server.

### B. Browser-only JS

**Pros:**
- Instant preview.
- No server.

**Cons:**
- Lossy export: no kerning, a GSUB that may not parse, no variable fonts.

### C. Hybrid: JS preview + Pyodide/fontTools Web Worker for export (agent's recommendation)

**Pros:**
- Lossless.
- Fonts stay on the user's device.

**Cons:**
- Pyodide download size and cold start.
- Bundled fontTools lags behind (4.62.1).
- No skia-pathops.
- Effect code risks existing twice. Avoid this by running effects only in Python and streaming SVG back for preview.

Option A can stay as an optional server export for heavy jobs.

**For decay stages:** default to static instances per stage. Offer a variable font only for topology-preserving effects, gated by `varLib.interpolatable`.

---

## Sources

- **fontTools:** https://pypi.org/project/fonttools/ (changelog), https://fonttools.readthedocs.io/en/latest/pens/index.html, https://fonttools.readthedocs.io/en/latest/ttLib/removeOverlaps.html, https://fonttools.readthedocs.io/en/latest/varLib/instancer.html, https://fonttools.readthedocs.io/en/latest/varLib/index.html, https://github.com/fonttools/fonttools/blob/main/Lib/fontTools/fontBuilder.py
- **Boolean and font tooling:** https://github.com/fonttools/skia-pathops/releases, https://pypi.org/project/booleanOperations/, https://github.com/fonttools/ufoLib2, https://github.com/robotools/defcon, https://github.com/robotools/fontParts, https://github.com/googlefonts/fontmake
- **JS libraries:** https://github.com/opentypejs/opentype.js/releases, https://github.com/foliojs/fontkit/releases/tag/v3.0.0, https://github.com/photopea/Typr.js, https://github.com/harfbuzz/harfbuzzjs/releases, https://github.com/harfbuzz/harfbuzzjs/blob/main/examples/harfbuzz-subset.example.node.js, https://github.com/kekee000/fonteditor-core
- **JS geometry:** http://paperjs.org/reference/pathitem/, https://github.com/r-flash/PathBool.js, https://github.com/PolyClipping/Clipper2
- **Pyodide:** https://pyodide.org/en/stable/usage/packages-in-pyodide.html
- **Variable fonts:** https://learn.microsoft.com/en-us/typography/opentype/spec/dvaraxisreg
- **Rust:** https://github.com/googlefonts/fontations, https://developer.chrome.com/blog/memory-safety-fonts, https://github.com/fonttools/fontspector
- **Validation:** https://github.com/khaledhosny/ots, https://github.com/fonttools/fontbakery
- **Licensing:** https://typesupply.com/license/trials, https://www.fontfabric.com/fontfabric-non-commercial-eula-for-trial-fonts/, https://www.grillitype.com/api/v1/download/eula_trials/Grilli-Type-Trial-Fonts-EULA.pdf, https://www.daltonmaag.com/download/dama/TrialLicenceAgreement.pdf, https://typetype.org/de/wp-content/uploads/TypeType-Trial-License.pdf, https://cms-prod.monotype.com/resources/expertise/font-licensing-management-terms-and-definitions, https://openfontlicense.org/open-font-license-official-text/, https://openfontlicense.org/ofl-faq/, https://github.com/adobe-fonts/source-sans/blob/release/LICENSE.md

Test scripts are in `../evidence/scripts/`: `snap_cff.py`, `vf_test.py`, `vf_test2.py`, `bool_test.py`, `pyo_test.mjs`, `pyo_bool.mjs`, `otjs_test.mjs`. They ran against Source Sans 3 (OFL); paths inside the scripts point to `/tmp`.
