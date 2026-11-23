"""Per-glyph raster frame: nonzero-winding rasterisation, SDF, coherent noise, tracing."""
from __future__ import annotations

import hashlib

import numpy as np
from scipy import ndimage
from skimage import measure

from .fontio import signed_area


class Frame:
    """Maps glyph units <-> raster pixels. Pixel (r, c) centre sits at px coordinate (c, r)."""

    def __init__(self, xmin: float, ymin: float, xmax: float, ymax: float, scale: float):
        self.scale = scale
        self.x0 = xmin
        self.y0 = ymin
        self.W = max(4, int(np.ceil((xmax - xmin) * scale)))
        self.H = max(4, int(np.ceil((ymax - ymin) * scale)))

    def to_px(self, pts: np.ndarray):
        return (pts[:, 0] - self.x0) * self.scale - 0.5, (pts[:, 1] - self.y0) * self.scale - 0.5

    def xs(self) -> np.ndarray:
        """Glyph-unit x of each pixel column centre."""
        return (np.arange(self.W) + 0.5) / self.scale + self.x0

    def ys(self) -> np.ndarray:
        return (np.arange(self.H) + 0.5) / self.scale + self.y0

    def empty(self) -> np.ndarray:
        return np.zeros((self.H, self.W), bool)

    # ---- rasterisation -------------------------------------------------
    def winding(self, loops: list[np.ndarray]) -> np.ndarray:
        """Exact winding number at every pixel centre (vectorised scanline)."""
        H, W = self.H, self.W
        if not loops:
            return np.zeros((H, W), np.int32)
        xs0, ys0, xs1, ys1 = [], [], [], []
        for c in loops:
            cx, cy = self.to_px(c)
            xs0.append(cx); ys0.append(cy)
            xs1.append(np.roll(cx, -1)); ys1.append(np.roll(cy, -1))
        x0 = np.concatenate(xs0); y0 = np.concatenate(ys0)
        x1 = np.concatenate(xs1); y1 = np.concatenate(ys1)
        keep = y0 != y1
        x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
        d = np.where(y1 > y0, 1, -1)
        lo = np.minimum(y0, y1); hi = np.maximum(y0, y1)
        r0 = np.clip(np.ceil(lo), 0, H).astype(np.int64)
        r1 = np.clip(np.ceil(hi), 0, H).astype(np.int64)
        counts = np.maximum(r1 - r0, 0)
        total = int(counts.sum())
        if total == 0:
            return np.zeros((H, W), np.int32)
        idx = np.repeat(np.arange(len(counts)), counts)
        offs = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
        rows = r0[idx] + offs
        xi = x0[idx] + (rows - y0[idx]) * (x1[idx] - x0[idx]) / (y1[idx] - y0[idx])
        cols = np.clip(np.ceil(xi), 0, W).astype(np.int64)
        dd = d[idx].astype(np.float64)
        acc = np.bincount(rows * (W + 1), weights=dd, minlength=H * (W + 1))
        acc -= np.bincount(rows * (W + 1) + cols, weights=dd, minlength=H * (W + 1))
        acc = acc.reshape(H, W + 1)
        return np.rint(np.cumsum(acc, axis=1)[:, :W]).astype(np.int32)

    def fill(self, loops: list[np.ndarray]) -> np.ndarray:
        return self.winding(loops) != 0

    # ---- tracing ---------------------------------------------------------
    def trace(self, mask: np.ndarray, smooth: float = 0.5, min_area: float = 0.0) -> list[np.ndarray]:
        """Marching squares -> closed loops in glyph units, islands CW / holes CCW."""
        if not mask.any():
            return []
        m = np.pad(mask.astype(float), 2)
        raw = measure.find_contours(m, 0.5, positive_orientation="high")
        loops = []
        for L in raw:
            if len(L) < 4:
                continue
            if np.allclose(L[0], L[-1]):
                L = L[:-1]
            rows, cols = L[:, 0] - 2, L[:, 1] - 2
            p = np.c_[(cols + 0.5) / self.scale + self.x0, (rows + 0.5) / self.scale + self.y0]
            for _ in range(int(round(smooth * 4))):
                p = 0.5 * p + 0.25 * (np.roll(p, 1, 0) + np.roll(p, -1, 0))
            if abs(signed_area(p)) < max(min_area, 1e-6):
                continue
            loops.append(p)
        if not loops:
            return []
        # positive_orientation='high' gives every island the same orientation sign.
        areas = [signed_area(p) for p in loops]
        island_sign = np.sign(areas[int(np.argmax(np.abs(areas)))])
        out = []
        for p, a in zip(loops, areas):
            island = np.sign(a) == island_sign
            out.append(p if (a < 0) == island else p[::-1].copy())
        return out


def sdf(mask: np.ndarray) -> np.ndarray:
    """Signed distance in pixels: negative inside, positive outside."""
    if not mask.any():
        return np.full(mask.shape, 1e6)
    if mask.all():
        return np.full(mask.shape, -1e6)
    return ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)


def seed_for(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


_TEXTURES: dict[int, np.ndarray] = {}
_PERIOD = 64


def _texture(seed: int) -> np.ndarray:
    t = _TEXTURES.get(seed)
    if t is None:
        g = np.random.default_rng(seed).standard_normal((_PERIOD, _PERIOD))
        t = ndimage.spline_filter(g / g.std(), order=3, mode="grid-wrap")
        _TEXTURES[seed] = t
    return t


def noise_field(frame: Frame, scale_units: float, seed: int, octaves: int = 1,
                offset: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Coherent value noise (~N(0, 0.5)) sampled in glyph-unit world space.

    `scale_units` is the feature size in glyph units. Periodic texture, bicubic.
    """
    out = np.zeros((frame.H, frame.W))
    amp, total = 1.0, 0.0
    X, Y = np.meshgrid(frame.xs() + offset[0], frame.ys() + offset[1])
    for o in range(octaves):
        s = scale_units / (2 ** o)
        tex = _texture(seed + o * 7919)
        out += amp * ndimage.map_coordinates(tex, [Y / s, X / s], order=3, mode="grid-wrap",
                                             prefilter=False)
        total += amp
        amp *= 0.5
    return out / total
