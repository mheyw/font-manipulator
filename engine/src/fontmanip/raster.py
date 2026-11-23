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
        loops = [c for c in loops if len(c)]
        if not loops:
            return np.zeros((self.H, self.W), np.int32)
        if len(loops) == 1:
            return self.winding_packed(loops[0], np.array([len(loops[0])]))
        return self.winding_packed(np.concatenate(loops), np.array([len(c) for c in loops]))

    def winding_packed(self, pts: np.ndarray, lengths: np.ndarray) -> np.ndarray:
        """`winding` for loops packed end to end: pts (N, 2), lengths (n_loops,) summing to N."""
        H, W = self.H, self.W
        lengths = np.asarray(lengths, np.int64)
        if len(pts) == 0:
            return np.zeros((H, W), np.int32)
        x0, y0 = self.to_px(np.asarray(pts, float))
        nxt = np.arange(1, len(x0) + 1)
        ends = np.cumsum(lengths)
        nz = lengths > 0
        nxt[ends[nz] - 1] = (ends - lengths)[nz]  # each loop closes back on its first point
        x1, y1 = x0[nxt], y0[nxt]
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
        # winding(r, j) = sum of d over crossings in row r with col > j. Closed loops cross every
        # row a net zero times, so only [rmin, rmax] x [cmin, cmax) can be nonzero: accumulate in
        # that box only (exact: identical integers to a full-frame pass, far less memory traffic
        # for the many small single-loop fills the compatibiliser does).
        dd = d[idx].astype(np.int32)
        rmin, rmax = int(rows.min()), int(rows.max())
        cmin, cmax = int(cols.min()), int(cols.max())
        out = np.zeros((H, W), np.int32)
        bw = cmax - cmin
        if bw <= 0:
            return out
        bh = rmax - rmin + 1
        # suffix sums: acc[r, k] = sum d with local col == k; winding at local col j = sum_{k > j}
        acc = np.bincount((rows - rmin) * (bw + 1) + (cols - cmin), weights=dd,
                          minlength=bh * (bw + 1)).reshape(bh, bw + 1)
        suf = np.cumsum(acc[:, :0:-1], axis=1)[:, ::-1]  # suf[:, j] = sum acc[:, j+1:]
        out[rmin:rmax + 1, cmin:cmax] = np.rint(suf).astype(np.int32)
        return out

    def fill(self, loops: list[np.ndarray]) -> np.ndarray:
        return self.winding(loops) != 0

    # ---- tracing ---------------------------------------------------------
    def trace(self, mask: np.ndarray, smooth: float = 0.5, min_area: float = 0.0) -> list[np.ndarray]:
        """Marching squares -> closed loops in glyph units, islands CW / holes CCW."""
        box = bbox(mask)
        if box is None:
            return []
        r0, r1, c0, c1 = box
        # Marching squares only sees the ink's bounding box (+2 px of padding, as before). Contour
        # coordinates of a binary mask are exact half-integers, so the shift back is exact and the
        # loops are identical to tracing the whole frame.
        m = np.pad(mask[r0:r1, c0:c1].astype(float), 2)
        raw = measure.find_contours(m, 0.5, positive_orientation="high")
        raw = [L for L in raw if len(L) >= 4]
        if not raw:
            return []
        raw = [L[:-1] if _close(L[0], L[-1]) else L for L in raw]
        lens = np.array([len(L) for L in raw], np.int64)
        Lp = np.concatenate(raw)
        rows, cols = (Lp[:, 0] + r0) - 2, (Lp[:, 1] + c0) - 2
        p = np.c_[(cols + 0.5) / self.scale + self.x0, (rows + 0.5) / self.scale + self.y0]
        # smoothing on all loops at once: p[prv] / p[nxt] == np.roll(p, 1 / -1) within each loop
        ends = np.cumsum(lens)
        starts = ends - lens
        nxt = np.arange(1, len(p) + 1)
        nxt[ends - 1] = starts
        prv = np.arange(-1, len(p) - 1)
        prv[starts] = ends - 1
        for _ in range(int(round(smooth * 4))):
            p = 0.5 * p + 0.25 * (p[prv] + p[nxt])
        x, y = p[:, 0], p[:, 1]
        terms = x * y[nxt] - x[nxt] * y  # signed_area integrand, summed per loop (same order)
        thr = max(min_area, 1e-6)
        loops, areas = [], []
        for s0, e0 in zip(starts.tolist(), ends.tolist()):
            a = 0.5 * float(np.sum(terms[s0:e0]))
            if abs(a) < thr:
                continue
            loops.append(p[s0:e0])
            areas.append(a)
        if not loops:
            return []
        # positive_orientation='high' gives every island the same orientation sign.
        island_sign = np.sign(areas[int(np.argmax(np.abs(areas)))])
        out = []
        for p, a in zip(loops, areas):
            island = np.sign(a) == island_sign
            out.append(p.copy() if (a < 0) == island else p[::-1].copy())
        return out


def _close(a: np.ndarray, b: np.ndarray) -> bool:
    """np.allclose for two points, without its overhead."""
    return abs(a[0] - b[0]) <= 1e-8 + 1e-5 * abs(b[0]) and abs(a[1] - b[1]) <= 1e-8 + 1e-5 * abs(b[1])


def bbox(mask: np.ndarray):
    """(r0, r1, c0, c1) half-open bounding box of True pixels, or None if empty."""
    rows = np.flatnonzero(mask.any(axis=1))
    if not len(rows):
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def sdf(mask: np.ndarray) -> np.ndarray:
    """Signed distance in pixels: negative inside, positive outside."""
    if not mask.any():
        return np.full(mask.shape, 1e6)
    if mask.all():
        return np.full(mask.shape, -1e6)
    return ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)


def grow_box(box, pad: int, shape):
    if box is None:
        return None
    r0, r1, c0, c1 = box
    return max(0, r0 - pad), min(shape[0], r1 + pad), max(0, c0 - pad), min(shape[1], c1 + pad)


def sdf_box(mask: np.ndarray, box) -> np.ndarray:
    """`sdf(mask)` computed on `box` only (1e6 outside it). Exact inside the box provided the box
    holds all the ink plus a 1 px ring (or runs to the frame edge): the nearest ink / background
    pixel of any pixel in the box then lies in the box too (clamp the far one onto the ring)."""
    if box is None:  # no ink
        return np.full(mask.shape, 1e6)
    r0, r1, c0, c1 = box
    if r0 == 0 and c0 == 0 and r1 == mask.shape[0] and c1 == mask.shape[1]:
        return sdf(mask)
    c = mask[r0:r1, c0:c1]
    out = np.full(mask.shape, 1e6)
    if not c.any():
        return out
    out[r0:r1, c0:c1] = ndimage.distance_transform_edt(~c) - ndimage.distance_transform_edt(c)
    return out


def sdf_pair(a: np.ndarray, b: np.ndarray):
    """sdf(a), sdf(b) on their common ink box (+1 px ring), 1e6 elsewhere. Any blend
    (1-w)*sdf(a) + w*sdf(b) with 0 <= w <= 1 then has exactly the original sign everywhere:
    exact values inside the box, both terms positive outside it."""
    box = grow_box(bbox(a | b), 1, a.shape)
    return sdf_box(a, box), sdf_box(b, box)


def edt_in(mask: np.ndarray) -> np.ndarray:
    """distance_transform_edt(mask): distance from ink to the nearest background pixel (0 off ink),
    computed on the ink box + 1 px ring only (exact, see `sdf_box`). 1e6 everywhere if all ink."""
    box = grow_box(bbox(mask), 1, mask.shape)
    out = np.zeros(mask.shape)
    if box is None:
        return out
    if mask.all():
        return np.full(mask.shape, 1e6)
    r0, r1, c0, c1 = box
    out[r0:r1, c0:c1] = ndimage.distance_transform_edt(mask[r0:r1, c0:c1])
    return out


def edt_out(mask: np.ndarray, reach: float) -> np.ndarray:
    """distance_transform_edt(~mask) (distance to the nearest ink pixel, 0 on ink), exact wherever
    it is <= reach px; 1e6 beyond the ink box grown by reach (true value > reach there)."""
    box = grow_box(bbox(mask), int(np.ceil(max(reach, 0.0))) + 2, mask.shape)
    if box is None:
        return np.full(mask.shape, 1e6)
    r0, r1, c0, c1 = box
    out = np.full(mask.shape, 1e6)
    out[r0:r1, c0:c1] = ndimage.distance_transform_edt(~mask[r0:r1, c0:c1])
    return out


def sdf_below(mask: np.ndarray, t) -> np.ndarray:
    """Exactly `sdf(mask) < t` (t scalar or per-pixel array), with one-sided, cropped distance
    transforms: on ink sdf = -edt_in, off ink sdf = edt_out."""
    if np.ndim(t) == 0:
        t = float(t)
        if t >= 0:
            if t >= 1e6:
                return sdf(mask) < t
            return mask | (edt_out(mask, t) < t)
        return edt_in(mask) > -t
    hi = float(np.max(t))
    if hi >= 1e6 or float(np.min(t)) <= -1e6:
        return sdf(mask) < t
    inside = edt_in(mask) > -t
    if hi <= 0:  # off-ink pixels have sdf >= 1 > t
        return mask & inside
    return np.where(mask, inside, edt_out(mask, hi) < t)


def blur(a: np.ndarray, sigma: float, truncate: float = 4.0) -> np.ndarray:
    """ndimage.gaussian_filter(a, sigma) (mode reflect), computed only around the nonzero part of a.

    Beyond the kernel radius of the nonzero box the output is exactly 0, and inside the grown box
    every tap reads the same values as on the full frame (reflections at an inner crop edge only
    see zeros), so the result is identical.
    """
    if sigma <= 1e-15:
        return ndimage.gaussian_filter(a, sigma, truncate=truncate)
    lw = int(truncate * float(sigma) + 0.5)
    box = grow_box(bbox(a != 0), lw, a.shape)
    if box is None:
        return np.zeros_like(a, dtype=np.result_type(a.dtype, np.float64))
    r0, r1, c0, c1 = box
    if (r1 - r0) * (c1 - c0) > 0.85 * a.size:
        return ndimage.gaussian_filter(a, sigma, truncate=truncate)
    out = np.zeros(a.shape, np.result_type(a.dtype, np.float64))
    out[r0:r1, c0:c1] = ndimage.gaussian_filter(a[r0:r1, c0:c1], sigma, truncate=truncate)
    return out


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
    xs, ys = frame.xs() + offset[0], frame.ys() + offset[1]
    for o in range(octaves):
        s = scale_units / (2 ** o)
        tex = _texture(seed + o * 7919)
        # The sample points form an axis-aligned grid, so bicubic B-spline interpolation is
        # separable: Wy @ tex @ Wx.T. Same values as map_coordinates(order=3, grid-wrap,
        # prefilter=False) to ~1e-15, ~20x faster.
        out += amp * (_bspline_weights(ys / s) @ tex @ _bspline_weights(xs / s).T)
        total += amp
        amp *= 0.5
    return out / total


def _bspline_weights(u: np.ndarray, period: int = _PERIOD) -> np.ndarray:
    """(len(u), period) matrix of periodic cubic B-spline weights at coordinates u."""
    fl = np.floor(u)
    t = u - fl
    t2 = t * t
    t3 = t2 * t
    w = np.stack([(1 - t) ** 3 / 6, (3 * t3 - 6 * t2 + 4) / 6,
                  (-3 * t3 + 3 * t2 + 3 * t + 1) / 6, t3 / 6], 1)
    cols = (fl.astype(np.int64)[:, None] - 1 + np.arange(4)) % period
    M = np.zeros((len(u), period))
    np.add.at(M, (np.repeat(np.arange(len(u)), 4), cols.ravel()), w.ravel())
    return M
