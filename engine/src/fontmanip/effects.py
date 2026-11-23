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
from .raster import Frame, blur, noise_field, sdf_below, sdf_pair, seed_for


@dataclass
class Ctx:
    frame: Frame
    info: FontInfo
    glyph: str
    advance: float
    seed: int
    em: float  # units per "u" (upm / 1000)
    stop_seed: int = 0  # shared by all effects in a stop (so "regions" line up across effects)
    recipe_seed: int = 0  # shared by every stop (patterns that should persist along the axis)

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
        self._mask = None

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
        # cached: effects read it several times (states are immutable; never write into it)
        if self._mask is None:
            self._mask = self.ink > 0.5
        return self._mask

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


def _noise(st: GlyphState, ctx: Ctx, scale: float, salt, octaves=2, per_glyph=True, seed=None) -> np.ndarray:
    off = (0.0, 0.0)
    if per_glyph:
        h = seed_for("glyph-offset", ctx.glyph)
        off = ((h % 9973) * 3.1, ((h // 9973) % 9973) * 2.7)
    return noise_field(st.frame, max(ctx.u(scale), 1e-3), seed_for(ctx.seed if seed is None else seed, salt),
                       octaves, off)


# ============================ ink / field effects ============================

REGION_PARAMS = [
    Param("regions", "Affected area", 1.0, 0.0, 1.0, 0.01,
          "1 = everywhere. Lower = only random patches are hit (localised damage)."),
    Param("region_size", "Patch size", 220.0, 20, 1500, 10, "Size of the affected patches (u)."),
]

_NOISE_SIGMA: dict[int, float] = {}


def _regions(st: GlyphState, ctx: Ctx, p: dict, salt="regions"):
    """0..1 weight field selecting random patches; 1.0 (scalar) when regions == 1."""
    frac = p.get("regions", 1.0)
    if frac >= 0.999:
        return 1.0
    if frac <= 0.001:
        return 0.0
    off = (0.0, 0.0)
    if p.get("per_glyph", True):
        h = seed_for("glyph-offset", ctx.glyph)
        off = ((h % 9973) * 3.1, ((h // 9973) % 9973) * 2.7)
    n = noise_field(st.frame, max(ctx.u(p.get("region_size", 220.0)), 1e-3),
                    seed_for(ctx.stop_seed, salt), 1, off)
    from scipy.stats import norm
    sig = 0.62  # empirical std of the bicubic value-noise texture
    thr = norm.ppf(1 - frac) * sig
    return np.clip((n - thr) / (0.45 * sig) + 0.5, 0.0, 1.0)


def _blend(st: GlyphState, field: np.ndarray, level: float, w) -> GlyphState:
    """Mix an effect's field (ink where field > level) with the untouched ink, by weight w."""
    eff = field > level
    if isinstance(w, float):
        return st.with_ink((eff if w >= 0.5 else st.mask).astype(float))
    # blend the two *shapes* via signed distance: smooth morph across patch edges, no spurs
    # (only the sign is used, so both SDFs are computed on their common ink box: exact)
    da, de = sdf_pair(st.mask, eff)
    d = (1 - w) * da + w * de
    return st.with_ink((d < 0).astype(float))


def _near_ink(st: GlyphState, reach_px: float) -> np.ndarray:
    """0..1 weight: 1 on/near existing ink, fading to 0 beyond `reach_px` (stops stray specks)."""
    return np.clip(blur(st.mask.astype(float), max(reach_px, 1.0)) * 4.0, 0.0, 1.0)



@effect("ink", "Ink spread", "ink", [
    Param("radius", "Softness", 14.0, 0, 120, 1, "Blur radius (u). Bigger = rounder, blobbier."),
    Param("level", "Ink level", 0.4, 0.05, 0.95, 0.01, "Threshold. Lower = ink bleeds outward, higher = starved/eroded."),
    Param("noise", "Noise", 0.15, 0, 1, 0.01, "Random ink density variation."),
    Param("scale", "Noise size", 40.0, 2, 400, 1, "Noise feature size (u)."),
    Param("per_glyph", "Vary per glyph", True),
    *REGION_PARAMS,
], "Blur → noise → threshold. The core ink/blob operator.")
def fx_ink(st, p, ctx):
    f = blur(st.ink, ctx.px(p["radius"])) if p["radius"] > 0 else st.ink.copy()
    if p["noise"]:
        w = _near_ink(st, ctx.px(p["radius"]) + ctx.px(p["scale"]) * 0.25)
        f = f + p["noise"] * w * _noise(st, ctx, p["scale"], "ink", 2, p["per_glyph"])
    return _blend(st, f, p["level"], _regions(st, ctx, p))


@effect("blur", "Blur", "ink", [
    Param("radius", "Radius", 10.0, 0, 150, 1, "Gaussian blur (u). Leaves a soft field; follow with Threshold."),
])
def fx_blur(st, p, ctx):
    return st.with_ink(blur(st.ink, ctx.px(p["radius"])))


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
    w = np.clip(blur(st.mask.astype(float), ctx.px(p["reach"]) / 2 + 1) * 3, 0, 1)
    drops = n * w > (1.6 - 1.2 * p["density"])
    return st.with_ink(np.maximum(st.ink, drops.astype(float)))


@effect("grow", "Grow / shrink", "shape", [
    Param("distance", "Distance", 10.0, -80, 80, 1, "Offset outline (u). Negative erodes."),
    Param("per_glyph", "Vary per glyph", True),
    *REGION_PARAMS,
])
def fx_grow(st, p, ctx):
    # offset = {sdf < distance}, via one cropped one-sided distance transform (identical mask)
    grown = sdf_below(st.mask, ctx.px(p["distance"]))
    return _blend(st, grown.astype(float), 0.5, _regions(st, ctx, p))


@effect("roughen", "Rough edges", "ink", [
    Param("amount", "Amount", 8.0, 0, 80, 0.5, "Edge displacement (u)."),
    Param("scale", "Size", 18.0, 1, 300, 1),
    Param("octaves", "Detail", 3, 1, 5, 1),
    Param("bias", "Bleed / bite", 0.0, -1, 1, 0.05, "+ spreads ink outward, − bites inward."),
    Param("per_glyph", "Vary per glyph", True),
    *REGION_PARAMS,
])
def fx_roughen(st, p, ctx):
    n = _noise(st, ctx, p["scale"], "rough", p["octaves"], p["per_glyph"])
    amt = ctx.px(p["amount"])
    shift = amt * (n + p["bias"])
    # ink where shift - sdf > 0, i.e. sdf < shift: one-sided cropped transforms (identical mask)
    return _blend(st, sdf_below(st.mask, shift).astype(float), 0.5, _regions(st, ctx, p))


@effect("round", "Round corners", "shape", [
    Param("radius", "Radius", 12.0, 0, 120, 1, "Morphological smoothing (u)."),
    Param("mode", "Mode", "both", options=["both", "outer", "inner"],
          help="outer rounds convex corners, inner fills concave ones."),
])
def fx_round(st, p, ctx):
    r = ctx.px(p["radius"])
    m = st.mask
    if p["mode"] in ("both", "outer"):
        m = sdf_below(sdf_below(m, -r), r)   # opening: sdf(sdf(m) < -r) < r
    if p["mode"] in ("both", "inner"):
        m = sdf_below(sdf_below(m, r), -r)   # closing: sdf(sdf(m) < r) < -r
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


def _drag(ink: np.ndarray, axis: int, reverse: bool, decay_px: np.ndarray | float) -> np.ndarray:
    """Ink dragged along an axis: g[i] = max(ink[i], g[i-1] * decay). decay_px = streak length (px),
    a scalar or one value per line across the drag direction."""
    a = np.moveaxis(ink, axis, 0)
    if reverse:
        a = a[::-1]
    d = np.exp(-1.0 / np.maximum(np.asarray(decay_px, float), 0.5))
    if d.ndim:
        d = d.ravel()  # one decay per line across the drag direction
    g = np.empty_like(a)
    g[0] = a[0]
    for i in range(1, len(a)):
        g[i] = np.maximum(a[i], g[i - 1] * d)
    if reverse:
        g = g[::-1]
    return np.moveaxis(g, 0, axis)


@effect("smear", "Smear / motion", "distort", [
    Param("length", "Length", 60.0, 0, 600, 1, "How far ink is dragged (u)."),
    Param("angle", "Angle", 0.0, -90, 90, 1, "0 = horizontal, 90 = vertical."),
    Param("direction", "Direction", "forward", options=["forward", "backward", "both"]),
    Param("variation", "Streak variation", 0.6, 0, 1, 0.01, "Different streak lengths per line."),
    Param("streak_size", "Streak spacing", 30.0, 3, 300, 1, "Size of the variation bands (u)."),
    Param("softness", "Softness", 6.0, 0, 60, 0.5, "Blur after dragging (u)."),
    Param("level", "Ink level", 0.45, 0.05, 0.95, 0.01),
    Param("per_glyph", "Vary per glyph", True),
    *REGION_PARAMS,
], "Drags ink in one direction like a motion blur / wet swipe (streaky, ragged tails).")
def fx_smear(st, p, ctx):
    ink = st.ink.clip(0, 1)
    ang = p["angle"]
    rot = abs(ang) > 0.5
    if rot:
        ink = ndimage.rotate(ink, -ang, reshape=False, order=1, mode="constant")
    n = _noise(st, ctx, p["streak_size"], "smear", 2, p["per_glyph"])
    lines = n.mean(axis=1)  # one value per row (drag is along x)
    L = ctx.px(p["length"]) * np.clip(1 + p["variation"] * 1.6 * lines, 0.05, None)
    out = 0
    if p["direction"] in ("forward", "both"):
        out = np.maximum(out, _drag(ink, 1, False, L[:, None]))
    if p["direction"] in ("backward", "both"):
        out = np.maximum(out, _drag(ink, 1, True, L[:, None]))
    if p["softness"] > 0:
        out = blur(out, ctx.px(p["softness"]))
    if rot:
        out = ndimage.rotate(out, ang, reshape=False, order=1, mode="constant")
    return _blend(st, out, p["level"], _regions(st, ctx, p))


@effect("melt", "Melt / drip", "ink", [
    Param("length", "Drip length", 160.0, 0, 1200, 5, "How far ink runs down (u)."),
    Param("variation", "Drip variation", 0.8, 0, 1, 0.01, "Uneven drips per column."),
    Param("drip_size", "Drip width", 25.0, 3, 300, 1, "Width of the drip variation (u)."),
    Param("swell", "Swell", 25.0, 0, 200, 1, "Ink pooling/blur in melted areas (u)."),
    Param("softness", "Softness", 10.0, 0, 80, 0.5, "Blur after running: rounder, blobbier drips (u)."),
    Param("level", "Ink level", 0.35, 0.05, 0.95, 0.01),
    Param("per_glyph", "Vary per glyph", True),
    *[Param(q.name, q.label, 0.45 if q.name == "regions" else q.default, q.min, q.max, q.step, q.help)
      for q in REGION_PARAMS],
], "Patches of the letter swell and run downward into drips.")
def fx_melt(st, p, ctx):
    ink = st.ink.clip(0, 1)
    if p["swell"] > 0:
        ink = np.maximum(ink, blur(ink, ctx.px(p["swell"])) * 1.6)
    n = _noise(st, ctx, p["drip_size"], "melt", 2, p["per_glyph"])
    cols = n.mean(axis=0)
    L = ctx.px(p["length"]) * np.clip(1 + p["variation"] * 2.0 * cols, 0.02, None)
    out = _drag(ink.clip(0, 1), 0, True, L[None, :])  # rows grow upward, so drip = reverse along y
    out = blur(out, max(1.0, ctx.px(p["softness"])))
    return _blend(st, out, p["level"], _regions(st, ctx, p))


@effect("wave", "Wave / shear bands", "distort", [
    Param("amplitude", "Amplitude", 20.0, 0, 300, 1, "Displacement (u)."),
    Param("wavelength", "Wavelength", 120.0, 10, 2000, 5, "(u)"),
    Param("axis", "Shift", "horizontal", options=["horizontal", "vertical"],
          help="horizontal: rows slide sideways (banding); vertical: columns slide up/down."),
    Param("shape", "Shape", "noise", options=["noise", "sine"]),
])
def fx_wave(st, p, ctx):
    f = st.frame
    R, C = np.mgrid[0:f.H, 0:f.W].astype(float)
    amp = ctx.px(p["amplitude"])
    along = f.ys() if p["axis"] == "horizontal" else f.xs()  # glyph units, shared by all glyphs
    if p["shape"] == "sine":
        ph = (ctx.seed % 1000) / 1000 * 2 * np.pi
        d = np.sin(2 * np.pi * along / ctx.u(p["wavelength"]) + ph)
    else:
        from .raster import _texture
        t = _texture(ctx.seed)
        d = ndimage.map_coordinates(t, [along / ctx.u(p["wavelength"]), np.full_like(along, 7.3)],
                                    order=3, mode="grid-wrap", prefilter=False) * 1.6
    if p["axis"] == "horizontal":
        C = C - amp * d[:, None]
    else:
        R = R - amp * d[None, :]
    return st.with_ink(ndimage.map_coordinates(st.ink, [R, C], order=1, mode="constant"))


@effect("pits", "Pitted grain", "ink", [
    Param("density", "Density", 0.25, 0, 0.9, 0.01, "How much of the ink is pitted."),
    Param("size", "Pit size", 9.0, 2, 80, 0.5, "(u)"),
    Param("margin", "Clean edge", 6.0, 0, 60, 0.5, "Keep pits this far inside the edge (u)."),
    Param("per_glyph", "Vary per glyph", True),
], "Small white specks inside the ink, like a dry stamp or rough paper. Adds many contours.")
def fx_pits(st, p, ctx):
    if p["density"] <= 0:
        return st
    from scipy.stats import norm
    # same pattern at every stop (recipe seed): pits persist and grow along the axis instead of
    # re-rolling, which keeps them matchable across stops (far fewer contours, smoother morphs)
    n = _noise(st, ctx, p["size"], "pits", 2, p["per_glyph"], seed=ctx.recipe_seed)
    holes = n > norm.ppf(1 - p["density"]) * 0.62
    inside = sdf_below(st.mask, -ctx.px(p["margin"]))
    return st.with_ink((st.mask & ~(holes & inside)).astype(float))


@effect("echo", "Echo / misprint", "distort", [
    Param("dx", "Offset x", 12.0, -200, 200, 1, "(u)"),
    Param("dy", "Offset y", -6.0, -200, 200, 1, "(u)"),
    Param("copies", "Copies", 1, 1, 8, 1),
    Param("per_glyph", "Vary per glyph", True),
    *REGION_PARAMS,
], "Union with offset copies, like a doubled print or slipped stamp.")
def fx_echo(st, p, ctx):
    m = st.ink.clip(0, 1)
    out = m.copy()
    for k in range(1, int(p["copies"]) + 1):
        out = np.maximum(out, ndimage.shift(m, (ctx.px(p["dy"]) * k, ctx.px(p["dx"]) * k), order=1))
    return _blend(st, out, 0.5, _regions(st, ctx, p, salt="echo"))


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
