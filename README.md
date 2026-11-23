# font-manipulator

Turn any font into a **variable font with a "decay" axis**: ink bleed, rough edges, Ferro-style pixel
grids, and inky blobs, all as stops along one slider. Inspired by Plain Form's *Ferro*
(see `docs/`).

Everything runs locally; fonts never leave your machine.

## Requirements

- macOS / Linux (Windows untested)
- [uv](https://docs.astral.sh/uv/) (it fetches Python 3.13 for you)
- Node.js 20+ and npm (only needed for the UI)

## Run it

```bash
# 1. engine + API (Python 3.13 via uv)
cd engine
uv sync --extra server
uv run fontmanip-serve            # http://127.0.0.1:8765  (also serves the built UI)

# 2. UI in dev mode (hot reload), in another terminal
cd web
npm install
npm run dev                       # http://localhost:5173  (proxies /api to :8765)
```

To use the UI without dev mode, build it once (`cd web && npm install && npm run build`), then just run
`fontmanip-serve` and open http://127.0.0.1:8765.

Using it:
1. Pick a bundled font or drop in your own OTF / TTF / WOFF / WOFF2. Variable fonts are pinned to an instance
   with the axis sliders.
2. Pick a **preset** (Ink decay, Motion smear, Mud inflate, Melt drip, Marker bleed), or edit the **stops** on the
   left. Each stop is a stage on the axis with its own effect stack. **Base shape** (width / height / slant /
   tracking) reshapes the source first, including advance widths.
3. Scrub the **DCAY** slider or tick *animate*. Click a letter to inspect its masters on the right.
4. **Build full variable font** → download WOFF2 / TTF / recipe.json.

## CLI

```bash
cd engine
uv run fontmanip recipe > my-recipe.json                    # default recipe to edit
uv run fontmanip build ../fonts/SourceSans3-Regular.otf my-recipe.json -o out --family "My Decay"
uv run fontmanip build FONT RECIPE --text "Hamburgefonts"   # quick subset build
uv run fontmanip effects                                    # list effects + parameters
uv run --group dev pytest                                   # tests
```

UI end-to-end test (server must be running on :8765): `cd web && npm run e2e`.

Outputs: `<Family>-VF.ttf`, `<Family>-VF.woff2`, `recipe.json`, `report.json`.
Use the font in CSS with `font-variation-settings: "DCAY" 50`.

## Effects

| Group | Effects |
|---|---|
| ink | Ink spread, Blur, Threshold, Grain, Spatter, Rough edges, Melt / drip, Pitted grain |
| shape | Grow / shrink, Round corners |
| grid | Pixel grid, Magnet grid (Ferro) |
| distort | Smear / motion, Wave / shear bands, Echo / misprint, Wobble, Skew / scale |

Ink spread, Grow, Rough edges, Smear, Melt and Echo have an **Affected area** control, so they hit random patches
instead of the whole letter. Patches line up across all effects in the same stop.

Presets live in `engine/recipes/*.json` and show up in the UI automatically.

To compare a recipe with a reference image:
`cd engine && uv run python compare_reference.py recipes/melt-drip.json ../fonts/Inter-Variable.ttf "wght=600" "Some text" ref.jpg out.png 64`

## How it works (short)

1. Each **stop** on the axis is a chain of **effects** applied to the original glyph, or to the previous stop.
   Effects are mostly raster operations (blur, noise, threshold, pixel grid, offset), traced back to outlines.
2. A **compatibiliser** matches contours across stops, resamples them to equal point counts, aligns start
   points, and gives shapes that appear or vanish a collapsed placeholder hidden inside ink. That makes
   any topology change interpolatable.
3. fontTools `varLib` builds the variable font. Kerning and features (GPOS/GSUB/GDEF) are copied from the
   source; advance widths are kept; names are rewritten; STAT and named instances are added.

Full research and design notes: `docs/README.md` → `docs/00-proposal.md`.

## Fonts & licences

`fonts/` contains Source Sans 3 (SIL OFL 1.1, Reserved Font Name "Source", so don't call outputs "Source …")
, Fraunces variable and Inter variable (both SIL OFL 1.1). Their licences are next to them.
Only process fonts whose licence allows modification. Many commercial and trial EULAs forbid it, including
the Mirage trial in `Mirage/` (see `docs/research/04-licensing.md`).
