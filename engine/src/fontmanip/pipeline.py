"""Run a recipe over a font's glyphs -> compatible masters (one per stop)."""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

from .compat import compatibilise
from .effects import Ctx, GlyphState, apply_effect
from .fontio import font_info, font_to_bytes, glyph_loops, load_font
from .outline import flatten
from .raster import Frame, sdf, seed_for
from .recipe import Recipe


@dataclass
class GlyphResult:
    name: str
    masters: list[list[np.ndarray]]  # per stop: list of (K,2) int arrays, identical structure
    oncurve: list[np.ndarray]        # per contour: (K,) bool, shared by every stop
    fit: list[float]                 # per stop: IoU(rendered outline, effect target)
    morph: list[float]               # per transition: IoU(midpoint instance, SDF-blend reference)


def _frame_for(loops, advance, info, recipe: Recipe) -> Frame:
    em = info.upm / 1000
    m = recipe.quality.margin * em
    xmin, xmax = min(0.0, 0.0), max(float(advance), 1.0)
    ymin, ymax = float(min(info.descender, 0)), float(max(info.ascender, info.cap_height))
    for l in loops:
        xmin = min(xmin, l[:, 0].min()); xmax = max(xmax, l[:, 0].max())
        ymin = min(ymin, l[:, 1].min()); ymax = max(ymax, l[:, 1].max())
    return Frame(xmin - m, ymin - m, xmax + m, ymax + m, recipe.quality.resolution / info.upm)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = np.count_nonzero(a | b)
    return 1.0 if u == 0 else np.count_nonzero(a & b) / u


def process_glyph(font, recipe: Recipe, name: str) -> GlyphResult:
    info = font_info(font)
    gs = font.getGlyphSet()
    advance = font["hmtx"][name][0]
    src = glyph_loops(gs, name)
    n = len(recipe.stops)
    if not src:
        return GlyphResult(name, [[] for _ in range(n)], [], [1.0] * n, [1.0] * (n - 1))
    frame = _frame_for(src, advance, info, recipe)
    em = info.upm / 1000
    q = recipe.quality
    source_state = GlyphState(frame, loops=src)
    states, loops_per_stop, masks = [], [], []
    for si, stop in enumerate(recipe.stops):
        st = source_state if stop.base == "source" or si == 0 else states[-1]
        for ei, spec in enumerate(stop.effects):
            ctx = Ctx(frame, info, name, advance, seed_for(recipe.seed, si, ei, spec.get("type")), em)
            st = apply_effect(st, spec, ctx)
        states.append(st)
        if not stop.effects and stop.base == "source":
            loops = src  # exact source outline
        else:
            loops = frame.trace(st.mask, smooth=stop.smooth, min_area=q.min_area * em * em)
        loops_per_stop.append(loops)
        masks.append(st.mask)
    masters, corner_flags = compatibilise(frame, loops_per_stop, masks, step=q.step * em, unit=em)
    oncurve = [f if q.outline == "smooth" else np.ones(len(f), bool) for f in corner_flags]
    rounded = [[np.rint(c).astype(np.int32) for c in m] for m in masters]

    def render(contours):
        return frame.fill([flatten(c, on) for c, on in zip(contours, oncurve)])

    fit = [_iou(render(m), t) for m, t in zip(rounded, masks)]
    morph = []
    for a in range(n - 1):
        mid = [(x.astype(float) + y) / 2 for x, y in zip(rounded[a], rounded[a + 1])]
        ref = (sdf(masks[a]) + sdf(masks[a + 1])) < 0
        morph.append(_iou(render(mid), ref))
    return GlyphResult(name, rounded, oncurve, fit, morph)


# ---------------------------------------------------------------- parallel

_W: dict = {}


def _init(font_bytes: bytes, recipe_json: str):
    _W["font"] = load_font(font_bytes)
    _W["recipe"] = Recipe.model_validate_json(recipe_json)


def _work(names: list[str]) -> list[GlyphResult]:
    return [process_glyph(_W["font"], _W["recipe"], n) for n in names]


def run(font, recipe: Recipe, glyphs: Iterable[str] | None = None, jobs: int | None = None,
        progress: Callable[[int, int], None] | None = None) -> dict[str, GlyphResult]:
    names = list(glyphs) if glyphs is not None else font.getGlyphOrder()
    jobs = jobs if jobs is not None else max(1, (os.cpu_count() or 2) - 1)
    out: dict[str, GlyphResult] = {}
    if jobs <= 1 or len(names) < 24:
        for i, n in enumerate(names):
            out[n] = process_glyph(font, recipe, n)
            if progress:
                progress(i + 1, len(names))
        return out
    chunk = max(4, len(names) // (jobs * 8))
    batches = [names[i:i + chunk] for i in range(0, len(names), chunk)]
    done = 0
    with ProcessPoolExecutor(jobs, initializer=_init,
                             initargs=(font_to_bytes(font), recipe.model_dump_json())) as ex:
        for res in ex.map(_work, batches):
            for r in res:
                out[r.name] = r
            done += len(res)
            if progress:
                progress(done, len(names))
    return out


def timed_run(font, recipe, glyphs=None, jobs=None, progress=None):
    t = time.time()
    res = run(font, recipe, glyphs, jobs, progress)
    return res, time.time() - t
