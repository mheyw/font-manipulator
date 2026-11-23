"""High-level entry points used by the CLI and the server."""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
from fontTools import subset as ftsubset
from fontTools.ttLib import TTFont

from .build import build_variable_font, to_bytes, varying_glyph_report
from .outline import svg_path
from .pipeline import GlyphResult, timed_run
from .recipe import Recipe


@dataclass
class BuildOutput:
    font: TTFont
    results: dict[str, GlyphResult]
    seconds: float
    report: dict


def subset_to_text(font: TTFont, text: str) -> TTFont:
    f = copy.deepcopy(font)
    opts = ftsubset.Options()
    opts.layout_features = ["kern", "liga", "calt", "ccmp", "locl"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = True
    opts.hinting = False
    sub = ftsubset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(f)
    return f


def make_font(source: TTFont, recipe: Recipe, text: str | None = None, jobs: int | None = None,
              progress=None) -> BuildOutput:
    src = subset_to_text(source, text) if text else source
    results, secs = timed_run(src, recipe, jobs=jobs, progress=progress)
    vf = build_variable_font(src, recipe, results)
    rep = varying_glyph_report(vf, results)
    fits = np.array([r.fit for r in results.values() if any(len(m) for m in r.masters)]) \
        if results else np.zeros((0, len(recipe.stops)))
    rep["fit_mean"] = [round(float(x), 4) for x in fits.mean(0)] if len(fits) else []
    rep["fit_min"] = [round(float(x), 4) for x in fits.min(0)] if len(fits) else []
    morphs = np.array([r.morph for r in results.values() if r.oncurve]) if results else np.zeros((0, 0))
    rep["morph_mean"] = [round(float(x), 4) for x in morphs.mean(0)] if len(morphs) else []
    rep["morph_min"] = [round(float(x), 4) for x in morphs.min(0)] if len(morphs) else []
    worst = sorted(((min(r.fit), n) for n, r in results.items()))[:5]
    rep["worst_glyphs"] = [{"glyph": n, "fit": round(f, 3)} for f, n in worst]
    rep["glyph_count"] = len(results)
    rep["seconds"] = round(secs, 2)
    return BuildOutput(vf, results, secs, rep)


def glyph_svg_paths(result: GlyphResult) -> list[str]:
    """One SVG path `d` per stop (font units, y-up), rendered exactly as the font will draw it."""
    return [svg_path(m, result.oncurve) for m in result.masters]


__all__ = ["make_font", "subset_to_text", "to_bytes", "glyph_svg_paths", "BuildOutput"]
