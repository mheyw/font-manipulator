# docs/

Research and planning for the font-manipulator project: algorithmic variations of fonts, inspired by Plain Form's *Ferro* (https://plain-form.com/articles/expired-by).

- [00-proposal.md](00-proposal.md): the proposed product, architecture, roadmap and open questions. **Start here.**
- research/
  - [01-ferro-and-magnet-mike.md](research/01-ferro-and-magnet-mike.md): the article, Magnet Mike's source code, Ferro's stages reverse-engineered from its webfonts, and precedents.
  - [02-font-file-tooling.md](research/02-font-file-tooling.md): fontTools vs opentype.js vs Pyodide vs Rust; tested round trips; variable-font experiments; validation.
  - [03-outline-manipulation-techniques.md](research/03-outline-manipulation-techniques.md): catalogue of outline algorithms (snapping, lattices, noise, raster/retrace, booleans, warps), with point-compatibility notes.
  - [04-licensing.md](research/04-licensing.md): Dreamtype/Mirage EULA clauses, trial-font norms, OFL rules.
- evidence/: feasibility spikes. The `ofl-sourcesans-*` outputs use Source Sans 3 (OFL). The Mirage-based `spike-*` renders are kept out of the public repo (trial licence); only the scripts are included.
  - `scripts/`: the research agents' test scripts. `snap_cff.py`, `vf_test*.py`, `bool_test.py`, `pyo_*.mjs` and `otjs_test.mjs` back the tooling claims in 02. `grid.py`, `inspect.py`, `sweep.py` and `ys.py` are the Ferro webfont measurements behind 01; the Ferro font files themselves are not included.
  - `spike1_control_point_snap.py` → `spike-grid-snap.png`: Bézier control points snapped to a rotated grid that grows coarser.
  - `spike2_magnet_sampling.py` → `spike-magnet-stages.png`: Ferro-style dense sampling, nearest node and polygon, at N = 15/10/5.

- Variable-font spikes, all on Source Sans 3 (OFL):
  - `spike3_variable_decay_axis.py` → `ofl-sourcesans-variable-dcay.png`: first `DCAY` variable font, using point-map effects.
  - `spike4_shrinkwrap.py` → `ofl-spike4-shrinkwrap.png`: shrink-wrap projection. No-go for topology changes.
  - `spike5_compatibiliser.py` → `ofl-spike5-compatibiliser.png`: raster effects + trace + compatibiliser. **This is the chosen design.**
  - `spike6_auto_inbetween.py`: auto in-between master experiment.
  - `variable-demo/index.html`: open it in a browser and drag the DCAY slider. The font is a 13-glyph test subset.

**Running the spikes**
- Spikes 1–2 need `fonttools`.
- Spikes 3–6 need Python 3.13 with `fonttools numpy scipy scikit-image opensimplex` (Python 3.15 lacks some wheels).
- Spikes 5–6 import spike 4 from `/tmp/vfspike/spike4.py`; copy `spike4_shrinkwrap.py` there first. These are throwaway prototypes, and the real engine will replace them.
