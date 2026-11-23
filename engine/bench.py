"""Performance + quality benchmark. Run: uv run python bench.py [--quick] [--json out.json]

Single process (deterministic timing). For every recipe: seconds/glyph, and quality numbers that
optimisations must not regress: mean stop fit, mean morph score, interpolation failures.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from fontmanip.fontio import load_font
from fontmanip.pipeline import process_glyph
from fontmanip.recipe import DEFAULT_RECIPE, Recipe

ROOT = Path(__file__).resolve().parent
FONTS = ROOT.parent / "fonts"
CHARS = "aegmoBRS8&%@"

KITCHEN_SINK = Recipe.model_validate({
    "name": "Kitchen sink (stress)", "seed": 4,
    "base": {"width": 1.2, "height": 0.95, "slant": 8, "tracking": 10},
    "stops": [
        {"position": 0, "name": "A", "effects": [{"type": "roughen", "amount": 2, "scale": 4}]},
        {"position": 33, "name": "B", "effects": [
            {"type": "wave", "amplitude": 20, "wavelength": 200},
            {"type": "smear", "length": 50, "direction": "both", "level": 0.42},
            {"type": "ink", "radius": 14, "level": 0.42, "noise": 0.15, "scale": 30, "regions": 0.5},
            {"type": "pits", "density": 0.15, "size": 8, "margin": 8},
            {"type": "roughen", "amount": 5, "scale": 5}]},
        {"position": 66, "name": "C", "effects": [
            {"type": "jitter", "amount": 20, "scale": 120},
            {"type": "pixelate", "columns": 9, "rows": 4},
            {"type": "melt", "length": 150, "regions": 0.5},
            {"type": "spatter", "density": 0.2, "size": 5, "reach": 60},
            {"type": "grain", "amount": 0.4, "scale": 10},
            {"type": "roughen", "amount": 6, "scale": 5}]},
        {"position": 100, "name": "D", "effects": [
            {"type": "grow", "distance": 30},
            {"type": "round", "radius": 60},
            {"type": "ink", "radius": 60, "level": 0.35, "noise": 0.3, "scale": 200},
            {"type": "echo", "dx": 15, "dy": -8, "copies": 2, "regions": 0.4},
            {"type": "pits", "density": 0.25, "size": 6, "margin": 8},
            {"type": "roughen", "amount": 3, "scale": 4}]},
    ],
})

CASES = [
    ("default", DEFAULT_RECIPE, "SourceSans3-Regular.otf", None),
    ("motion-smear", "motion-smear.json", "Inter-Variable.ttf", {"wght": 900}),
    ("mud-inflate", "mud-inflate.json", "Inter-Variable.ttf", {"wght": 700}),
    ("melt-drip", "melt-drip.json", "Inter-Variable.ttf", {"wght": 600}),
    ("marker-bleed", "marker-bleed.json", "Inter-Variable.ttf", {"wght": 400}),
    ("kitchen-sink", KITCHEN_SINK, "Inter-Variable.ttf", {"wght": 700}),
    ("fraunces-default", DEFAULT_RECIPE, "Fraunces-Variable.ttf", {"opsz": 144, "wght": 400}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="first 4 glyphs only")
    ap.add_argument("--only", help="comma-separated case names")
    ap.add_argument("--json")
    a = ap.parse_args()
    chars = CHARS[:4] if a.quick else CHARS
    out = {}
    total = 0.0
    fonts = {}
    for name, rec, fontfile, loc in CASES:
        if a.only and name not in a.only.split(","):
            continue
        r = rec if isinstance(rec, Recipe) else Recipe.load(ROOT / "recipes" / rec)
        key = (fontfile, tuple(sorted((loc or {}).items())))
        if key not in fonts:
            fonts[key] = load_font(FONTS / fontfile, loc)
        f = fonts[key]
        cm = f.getBestCmap()
        names = [cm[ord(c)] for c in chars if ord(c) in cm]
        t = time.perf_counter()
        res = [process_glyph(f, r, n) for n in names]
        dt = time.perf_counter() - t
        total += dt
        fits = np.array([x.fit for x in res])
        morphs = np.array([x.morph for x in res])
        out[name] = {
            "sec_per_glyph": round(dt / len(names), 3),
            "fit_mean": round(float(fits.mean()), 4),
            "fit_min": round(float(fits.min()), 4),
            "morph_mean": round(float(morphs.mean()), 4),
            "points": int(sum(sum(len(c) for c in m) for x in res for m in x.masters[:1])),
            "contours": int(sum(len(x.oncurve) for x in res)),
        }
        print(f"{name:18s} {out[name]['sec_per_glyph']:7.3f}s/glyph  fit {out[name]['fit_mean']:.4f} "
              f"(min {out[name]['fit_min']:.3f})  morph {out[name]['morph_mean']:.4f}  "
              f"contours {out[name]['contours']}", flush=True)
    print(f"TOTAL {total:.1f}s")
    out["_total_seconds"] = round(total, 2)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
