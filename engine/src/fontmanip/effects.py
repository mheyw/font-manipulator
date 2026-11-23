"""Effect library.

A glyph moves through a stop's effect chain as a `GlyphState`, which holds either
vector loops or a raster ink field (or both) and converts lazily between them.

All length parameters are in **em/1000** ("u") so recipes behave the same for any UPM.
Effects are free to change topology; the compatibiliser makes stops interpolatable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy import ndimage

from .fontio import FontInfo
from .raster import Frame, noise_field, sdf, seed_for


@dataclass
class Ctx:
    frame: Frame
    info: FontInfo
    glyph: str
    advance: float
    seed: int
    em: float  # units per "u" (upm / 1000)

    def u(self, v: float) -> float:
        """em/1000 -> glyph units."""
        return v * self.em

    def px(self, v: float) -> float:
        """em/1000 -> pixels."""
        return v * self.em * self.frame.scale


class GlyphState:
    def __init__(self, frame: Frame, loops: list[np.ndarray] | None = None, ink: np.ndarray | None = None):
        self.frame = frame
        self._loops = loops
        self._ink = ink  # float field, ink where > 0.5

    @property
    def loops(self) -> list[np.ndarray]:
        if self._loops is None:
            self._loops = self.frame.trace(self.ink > 0.5, smooth=0.5)
        return self._loops

    @property
    def ink(self) -> np.ndarray:
        if self._ink is None:
            self._ink = self.frame.fill(self._loops or []).astype(float)
        return self._ink

    @property
    def mask(self) -> np.ndarray:
        return self.ink > 0.5

    def with_ink(self, ink: np.ndarray) -> "GlyphState":
        return GlyphState(self.frame, None, ink)

    def with_loops(self, loops: list[np.ndarray]) -> "GlyphState":
        return GlyphState(self.frame, loops, None)


@dataclass
class Param:
    name: str
    label: str
    default: float | int | str | bool
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""
    options: list[str] | None = None

    def schema(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        d["kind"] = ("bool" if isinstance(self.default, bool) else
                     "choice" if self.options else
                     "int" if isinstance(self.default, int) else "float")
        return d


@dataclass
class Effect:
    name: str
    label: str
    group: str
    fn: Callable[[GlyphState, dict, Ctx], GlyphState]
    params: list[Param] = field(default_factory=list)
    help: str = ""

    def schema(self) -> dict:
        return {"name": self.name, "label": self.label, "group": self.group, "help": self.help,
                "params": [p.schema() for p in self.params]}

    def resolve(self, given: dict) -> dict:
        out = {p.name: p.default for p in self.params}
        for k, v in given.items():
            if k in out:
                out[k] = v
        return out


REGISTRY: dict[str, Effect] = {}


def effect(name, label, group, params, help=""):
    def deco(fn):
        REGISTRY[name] = Effect(name, label, group, fn, params, help)
        return fn
    return deco


def _noise(st: GlyphState, ctx: Ctx, scale: float, salt, octaves=2, per_glyph=True) -> np.ndarray:
    off = (0.0, 0.0)
    if per_glyph:
        h = seed_for("glyph-offset", ctx.glyph)
        off = ((h % 9973) * 3.1, ((h // 9973) % 9973) * 2.7)
    return noise_field(st.frame, max(ctx.u(scale), 1e-3), seed_for(ctx.seed, salt), octaves, off)


# ============================ ink / field effects ============================

def _near_ink(st: GlyphState, reach_px: float) -> np.ndarray:
    """0..1 weight: 1 on/near existing ink, fading to 0 beyond `reach_px` (stops stray specks)."""
    return np.clip(ndimage.gaussian_filter(st.mask.astype(float), max(reach_px, 1.0)) * 4.0, 0.0, 1.0)



@effect("ink", "Ink spread", "ink", [
    Param("radius", "Softness", 14.0, 0, 120, 1, "Blur radius (u). Bigger = rounder, blobbier."),
    Param("level", "Ink level", 0.4, 0.05, 0.95, 0.01, "Threshold. Lower = ink bleeds outward, higher = starved/eroded."),
    Param("noise", "Noise", 0.15, 0, 1, 0.01, "Random ink density variation."),
    Param("scale", "Noise size", 40.0, 2, 400, 1, "Noise feature size (u)."),
    Param("per_glyph", "Vary per glyph", True),
], "Blur → noise → threshold. The core ink/blob operator.")
def fx_ink(st, p, ctx):
    f = ndimage.gaussian_filter(st.ink, ctx.px(p["radius"])) if p["radius"] > 0 else st.ink.copy()
    if p["noise"]:
        w = _near_ink(st, ctx.px(p["radius"]) + ctx.px(p["scale"]) * 0.25)
        f = f + p["noise"] * w * _noise(st, ctx, p["scale"], "ink", 2, p["per_glyph"])
    return st.with_ink((f > p["level"]).astype(float))


@effect("blur", "Blur", "ink", [
    Param("radius", "Radius", 10.0, 0, 150, 1, "Gaussian blur (u). Leaves a soft field; follow with Threshold."),
])
def fx_blur(st, p, ctx):
    return st.with_ink(ndimage.gaussian_filter(st.ink, ctx.px(p["radius"])))


@effect("threshold", "Threshold", "ink", [
    Param("level", "Level", 0.5, 0.01, 0.99, 0.01),
])
def fx_threshold(st, p, ctx):
    return st.with_ink((st.ink > p["level"]).astype(float))


@effect("grain", "Grain", "ink", [
    Param("amount", "Amount", 0.3, 0, 1.5, 0.01, "Adds noise to the ink field (pair with Threshold/Ink)."),
    Param("scale", "Size", 20.0, 1, 300, 1),
    Param("octaves", "Detail", 2, 1, 5, 1),
    Param("per_glyph", "Vary per glyph", True),
])
def fx_grain(st, p, ctx):
    w = _near_ink(st, ctx.px(p["scale"]))
    return st.with_ink(st.ink + p["amount"] * w * _noise(st, ctx, p["scale"], "grain", p["octaves"], p["per_glyph"]))


@effect("spatter", "Spatter", "ink", [
    Param("density", "Density", 0.15, 0, 1, 0.01, "How many ink droplets."),
    Param("size", "Droplet size", 6.0, 1, 60, 0.5, "(u)"),
    Param("reach", "Reach", 60.0, 0, 400, 5, "How far from the ink droplets fly (u)."),
])
def fx_spatter(st, p, ctx):
    n = _noise(st, ctx, p["size"], "spatter", 1, True)
    w = np.clip(ndimage.gaussian_filter(st.mask.astype(float), ctx.px(p["reach"]) / 2 + 1) * 3, 0, 1)
    drops = n * w > (1.6 - 1.2 * p["density"])
    return st.with_ink(np.maximum(st.ink, drops.astype(float)))


@effect("grow", "Grow / shrink", "shape", [
    Param("distance", "Distance", 10.0, -80, 80, 1, "Offset outline (u). Negative erodes."),
])
def fx_grow(st, p, ctx):
    d = sdf(st.mask)
    return st.with_ink((d < ctx.px(p["distance"])).astype(float))


@effect("roughen", "Rough edges", "ink", [
    Param("amount", "Amount", 8.0, 0, 80, 0.5, "Edge displacement (u)."),
    Param("scale", "Size", 18.0, 1, 300, 1),
    Param("octaves", "Detail", 3, 1, 5, 1),
    Param("bias", "Bleed / bite", 0.0, -1, 1, 0.05, "+ spreads ink outward, − bites inward."),
    Param("per_glyph", "Vary per glyph", True),
])
def fx_roughen(st, p, ctx):
    d = sdf(st.mask)
    n = _noise(st, ctx, p["scale"], "rough", p["octaves"], p["per_glyph"])
    amt = ctx.px(p["amount"])
    return st.with_ink((d < amt * (n + p["bias"])).astype(float))


@effect("round", "Round corners", "shape", [
    Param("radius", "Radius", 12.0, 0, 120, 1, "Morphological smoothing (u)."),
    Param("mode", "Mode", "both", options=["both", "outer", "inner"],
          help="outer rounds convex corners, inner fills concave ones."),
])
def fx_round(st, p, ctx):
    r = ctx.px(p["radius"])
    m = st.mask
    if p["mode"] in ("both", "outer"):
        m = sdf(sdf(m) < -r) < r       # opening
    if p["mode"] in ("both", "inner"):
        m = sdf(sdf(m) < r) < -r       # closing
    return st.with_ink(m.astype(float))


@effect("pixelate", "Pixel grid", "grid", [
    Param("columns", "Columns", 10, 2, 60, 1, "Cells across the advance width (Ferro: 15 / 10 / 5)."),
    Param("rows", "Rows per x-height", 4, 1, 30, 1, "Rows are anchored to baseline and x-height."),
    Param("coverage", "Coverage", 0.5, 0.05, 0.95, 0.01, "Fraction of a cell that must be ink."),
    Param("width_mode", "Column mode", "advance", options=["advance", "square"],
          help="advance: per-glyph columns (Ferro). square: cell width = row height."),
])
def fx_pixelate(st, p, ctx):
    f = st.frame
    rh = ctx.info.x_height / p["rows"]
    cw = ctx.advance / p["columns"] if p["width_mode"] == "advance" and ctx.advance > 0 else rh
    ci = np.floor(f.xs() / cw).astype(np.int64)
    ri = np.floor(f.ys() / rh).astype(np.int64)
    ci -= ci.min(); ri -= ri.min()
    nc = ci.max() + 1
    lab = (ri[:, None] * nc + ci[None, :]).ravel()
    cov = np.bincount(lab, weights=st.ink.ravel().clip(0, 1)) / np.maximum(np.bincount(lab), 1)
    return st.with_ink((cov[lab] > p["coverage"]).reshape(f.H, f.W).astype(float))


# ============================ vector effects ============================

@effect("magnet", "Magnet grid (Ferro)", "grid", [
    Param("columns", "Columns", 10, 2, 60, 1, "Nodes across the advance (cell centres)."),
    Param("rows", "Rows per x-height", 4, 1, 30, 1),
    Param("dedupe", "Dedupe", "global", options=["global", "consecutive"],
          help="global = Magnet Mike: revisited nodes are skipped (creates oblique jumps)."),
], "Samples the outline, snaps each sample to the nearest grid node, rebuilds polygons.")
def fx_magnet(st, p, ctx):
    rh = ctx.info.x_height / p["rows"]
    cw = ctx.advance / p["columns"] if ctx.advance > 0 else rh
    out = []
    for c in st.loops:
        nodes = np.c_[(np.floor(c[:, 0] / cw) + 0.5) * cw, np.round(c[:, 1] / rh) * rh]
        keys = [tuple(n) for n in np.round(nodes, 3)]
        seen, pts = set(), []
        for k, n in zip(keys, nodes):
            if p["dedupe"] == "global":
                if k in seen:
                    continue
                seen.add(k)
            elif pts and tuple(np.round(pts[-1], 3)) == k:
                continue
            pts.append(n)
        if len(pts) >= 3:
            out.append(np.array(pts))
    return st.with_loops(out)


@effect("jitter", "Wobble", "distort", [
    Param("amount", "Amount", 12.0, 0, 150, 1, "Point displacement (u)."),
    Param("scale", "Size", 80.0, 5, 600, 5, "Wobble wavelength (u)."),
    Param("per_glyph", "Vary per glyph", True),
])
def fx_jitter(st, p, ctx):
    f = st.frame
    nx = _noise(st, ctx, p["scale"], "jx", 2, p["per_glyph"])
    ny = _noise(st, ctx, p["scale"], "jy", 2, p["per_glyph"])
    amt = ctx.u(p["amount"])
    out = []
    for c in st.loops:
        cx, cy = f.to_px(c)
        dx = ndimage.map_coordinates(nx, [cy, cx], order=1, mode="nearest")
        dy = ndimage.map_coordinates(ny, [cy, cx], order=1, mode="nearest")
        out.append(c + amt * np.c_[dx, dy])
    return st.with_loops(out)


@effect("skew", "Skew / scale", "distort", [
    Param("angle", "Slant (deg)", 0.0, -40, 40, 0.5),
    Param("sx", "Width", 1.0, 0.3, 2.0, 0.01, "Horizontal scale around the advance centre."),
    Param("sy", "Height", 1.0, 0.3, 2.0, 0.01, "Vertical scale from the baseline."),
])
def fx_skew(st, p, ctx):
    t = np.tan(np.radians(p["angle"]))
    cx = ctx.advance / 2
    yc = ctx.info.x_height / 2
    out = []
    for c in st.loops:
        x = (c[:, 0] - cx) * p["sx"] + cx + (c[:, 1] - yc) * t
        out.append(np.c_[x, c[:, 1] * p["sy"]])
    return st.with_loops(out)


def apply_effect(st: GlyphState, spec: dict, ctx: Ctx) -> GlyphState:
    eff = REGISTRY.get(spec.get("type", ""))
    if eff is None:
        raise ValueError(f"unknown effect {spec.get('type')!r}; known: {sorted(REGISTRY)}")
    if spec.get("enabled", True) is False:
        return st
    return eff.fn(st, eff.resolve(spec), ctx)


def effects_schema() -> list[dict]:
    return [e.schema() for e in REGISTRY.values()]
