"""Compatibiliser: turn per-stop outlines with arbitrary topology into interpolatable masters.

1. Track contours across consecutive stops (greedy max-overlap matching, same island/hole type).
2. Resample every track to one point count; align start points (FFT cyclic cross-correlation).
3. Where a track doesn't exist, insert a collapsed placeholder (all points on one spot, zero area),
   placed *inside ink* where possible so growth/shrink happens hidden under existing ink.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

import pathops
from skimage.measure import approximate_polygon

from .fontio import is_island, normalise_orientation, signed_area
from .raster import Frame


class Fill:
    """Raster fill of some loops, stored cropped to a box [r0:r1, c0:c1] of the frame.

    Pits/spatter produce hundreds of tiny contours per glyph; full-frame masks for each made the
    compatibiliser memory-bound. Pixels outside the box are all False.
    """
    __slots__ = ("shape", "r0", "r1", "c0", "c1", "sub", "area", "deep")

    def __init__(self, shape: tuple[int, int], r0: int, c0: int, sub: np.ndarray):
        self.shape = shape
        self.r0, self.c0 = r0, c0
        self.r1, self.c1 = r0 + sub.shape[0], c0 + sub.shape[1]
        self.sub = sub
        self.area = int(np.count_nonzero(sub))
        self.deep = None  # cached _deepest() of the whole fill

    def full(self) -> np.ndarray:
        m = np.zeros(self.shape, bool)
        m[self.r0:self.r1, self.c0:self.c1] = self.sub
        return m

    def inter(self, o: "Fill") -> int:
        """Pixel count of self & o."""
        r0, r1 = max(self.r0, o.r0), min(self.r1, o.r1)
        c0, c1 = max(self.c0, o.c0), min(self.c1, o.c1)
        if r0 >= r1 or c0 >= c1:
            return 0
        return int(np.count_nonzero(self.sub[r0 - self.r0:r1 - self.r0, c0 - self.c0:c1 - self.c0]
                                    & o.sub[r0 - o.r0:r1 - o.r0, c0 - o.c0:c1 - o.c0]))


def fill_crop(frame: Frame, loops: list[np.ndarray]) -> Fill:
    """Same pixels as `frame.fill(loops)` (nonzero winding at pixel centres), but only the box that
    can be nonzero is computed and stored. A closed loop crosses every row a net zero times, so
    only rows [rmin, rmax] x cols [cmin, cmax) of the edge crossings can be inside."""
    H, W = frame.H, frame.W
    empty = Fill((H, W), 0, 0, np.zeros((0, 0), bool))
    if not loops:
        return empty
    if len(loops) == 1:
        x0, y0 = frame.to_px(loops[0])
        x1 = np.concatenate([x0[1:], x0[:1]]); y1 = np.concatenate([y0[1:], y0[:1]])
    else:
        pts = [frame.to_px(c) for c in loops]
        x0 = np.concatenate([p[0] for p in pts]); y0 = np.concatenate([p[1] for p in pts])
        x1 = np.concatenate([np.concatenate([p[0][1:], p[0][:1]]) for p in pts])
        y1 = np.concatenate([np.concatenate([p[1][1:], p[1][:1]]) for p in pts])
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    up = y1 > y0
    lo = np.where(up, y0, y1); hi = np.where(up, y1, y0)
    r0 = np.clip(np.ceil(lo), 0, H).astype(np.int64)
    r1 = np.clip(np.ceil(hi), 0, H).astype(np.int64)
    counts = np.maximum(r1 - r0, 0)
    total = int(counts.sum())
    if total == 0:
        return empty
    idx = np.repeat(np.arange(len(counts)), counts)
    offs = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    rows = r0[idx] + offs
    xi = x0[idx] + (rows - y0[idx]) * (x1[idx] - x0[idx]) / (y1[idx] - y0[idx])
    cols = np.clip(np.ceil(xi), 0, W).astype(np.int64)
    rmin, rmax = int(rows.min()), int(rows.max())
    cmin, cmax = int(cols.min()), int(cols.max())
    bw = cmax - cmin
    if bw <= 0:
        return empty
    bh = rmax - rmin + 1
    acc = np.bincount((rows - rmin) * (bw + 1) + (cols - cmin), weights=np.where(up, 1.0, -1.0)[idx],
                      minlength=bh * (bw + 1)).reshape(bh, bw + 1)
    # winding at local col j = sum of crossings with local col > j (suffix sum; exact integers)
    suf = np.cumsum(acc[:, :0:-1], axis=1)[:, ::-1]
    return Fill((H, W), rmin, cmin, suf != 0)


@dataclass
class Track:
    island: bool
    loops: dict[int, np.ndarray] = field(default_factory=dict)
    fills: dict[int, Fill] = field(default_factory=dict)


def perimeter(c: np.ndarray) -> float:
    return float(np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T).sum())


def _arclen(closed: np.ndarray) -> np.ndarray:
    """Cumulative arc length along a closed polyline (first point repeated at the end)."""
    seg = np.diff(closed, axis=0)
    return np.concatenate(([0.0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))))


def resample(c: np.ndarray, k: int) -> np.ndarray:
    closed = np.concatenate([c, c[:1]])
    d = _arclen(closed)
    ts = np.linspace(0, d[-1], k, endpoint=False)
    return np.stack([np.interp(ts, d, closed[:, 0]), np.interp(ts, d, closed[:, 1])], axis=1)


def best_shift(ref: np.ndarray, c: np.ndarray) -> int:
    """Cyclic shift s minimising sum |ref - roll(c, -s)|^2 (both K points)."""
    fr = np.fft.rfft(ref, axis=0)
    fc = np.fft.rfft(c, axis=0)
    corr = np.fft.irfft(np.conj(fr) * fc, n=len(ref), axis=0).sum(axis=1)
    return int(np.argmax(corr))


def align(ref: np.ndarray, c: np.ndarray) -> np.ndarray:
    return np.roll(c, -best_shift(ref, c), axis=0)


def find_corners(loop: np.ndarray, unit: float, thresh_deg: float = 40.0):
    """Fine uniform polyline + indices of corners (turn > thresh over a ~6-unit neighbourhood)."""
    fine, idx = _corners(loop, unit, thresh_deg)[:2]
    return fine, idx


def _corners(loop: np.ndarray, unit: float, thresh_deg: float = 40.0):
    """find_corners() + (perimeter, closed loop, cumulative arc length), all from one pass and with
    the same arithmetic as perimeter() / resample()."""
    closed = np.concatenate([loop, loop[:1]])
    seg = np.diff(closed, axis=0)
    h = np.hypot(seg[:, 0], seg[:, 1])
    per = float(h.sum())
    d = np.concatenate(([0.0], np.cumsum(h)))
    ts = np.linspace(0, d[-1], max(16, int(per / (2 * unit))), endpoint=False)
    fine = np.stack([np.interp(ts, d, closed[:, 0]), np.interp(ts, d, closed[:, 1])], axis=1)
    m = 3
    n = len(fine)
    ext = np.concatenate([fine[-m:], fine, fine[:m]])  # cyclic pad: ext[i + m] == fine[i]
    a = fine - ext[:n]             # fine[i] - fine[i - m]
    b = ext[2 * m:] - fine         # fine[i + m] - fine[i]
    ang = np.degrees(np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1))))
    if not (ang > thresh_deg).any():
        return fine, np.zeros(0, np.intp), per, closed, d
    wmax = np.lib.stride_tricks.sliding_window_view(np.concatenate([ang[-m:], ang, ang[:m]]),
                                                    2 * m + 1).max(1)
    is_max = (ang >= wmax) & (ang > thresh_deg)
    idx = np.flatnonzero(is_max)
    # break ties on plateaus: keep first of consecutive indices
    if len(idx) > 1:
        keep = np.concatenate(([True], np.diff(idx) > m))
        idx = idx[keep]
    return fine, idx, per, closed, d


def resample_corners(loop: np.ndarray, k: int, unit: float, corners=None):
    """K points with a sample exactly on every corner. Returns (points, corner_flags).
    `corners`: precomputed `find_corners(loop, unit)` or `_corners(loop, unit)` result."""
    if corners is None:
        corners = _corners(loop, unit)
    fine, C = corners[:2]
    if len(C) == 0 or len(C) > k:
        if len(corners) == 5:  # reuse the loop's arc length: same numbers as resample(loop, k)
            _, _, _, lc, ld = corners
            ts = np.linspace(0, ld[-1], k, endpoint=False)
            return np.stack([np.interp(ts, ld, lc[:, 0]), np.interp(ts, ld, lc[:, 1])], axis=1), np.zeros(k, bool)
        return resample(loop, k), np.zeros(k, bool)
    closed = np.concatenate([fine, fine[:1]])
    d = _arclen(closed)
    total = d[-1]
    starts = d[C]
    lens = np.diff(np.concatenate([starts, [starts[0] + total]]))
    raw = lens / total * k
    n = np.maximum(1, np.floor(raw).astype(int))
    while n.sum() < k:
        n[np.argmax(raw - n)] += 1
    while n.sum() > k:
        j = np.argmax(np.where(n > 1, n - raw, -np.inf))
        n[j] -= 1
    seg = np.repeat(np.arange(len(n)), n)              # corner span of each output sample
    j = np.arange(k) - np.repeat(np.cumsum(n) - n, n)   # index of the sample within its span
    ts = (starts[seg] + lens[seg] * j / n[seg]) % total
    pts = np.stack([np.interp(ts, d, closed[:, 0]), np.interp(ts, d, closed[:, 1])], axis=1)
    flags = np.zeros(k, bool)
    flags[np.cumsum(n) - n] = True
    return pts, flags


def _deepest(frame: Frame, f: Fill, region: np.ndarray) -> np.ndarray:
    """Glyph-unit centre of the deepest pixel (max EDT, first in raster order) of `region`, a
    non-empty boolean array over f's box. The EDT runs on the region's tight bbox plus a 1px
    background border (clipped to the frame), which is exactly what it sees in the full frame."""
    rows = np.flatnonzero(region.any(axis=1)); cols = np.flatnonzero(region.any(axis=0))
    gr0, gr1 = f.r0 + rows[0], f.r0 + rows[-1] + 1
    gc0, gc1 = f.c0 + cols[0], f.c0 + cols[-1] + 1
    R0, R1 = max(gr0 - 1, 0), min(gr1 + 1, frame.H)
    C0, C1 = max(gc0 - 1, 0), min(gc1 + 1, frame.W)
    win = np.zeros((R1 - R0, C1 - C0), bool)
    win[gr0 - R0:gr1 - R0, gc0 - C0:gc1 - C0] = region[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    depth = ndimage.distance_transform_edt(win)
    r, c = np.unravel_index(int(np.argmax(depth)), depth.shape)
    r, c = r + R0, c + C0
    return np.array([(c + 0.5) / frame.scale + frame.x0, (r + 0.5) / frame.scale + frame.y0])


def _anchor(frame: Frame, own_fill: Fill, safe: np.ndarray | None, fallback) -> np.ndarray:
    """Point to collapse a track onto: deepest pixel of own_fill ∩ safe, else of own_fill.
    `fallback`: point (or zero-arg callable giving one) used when own_fill is empty."""
    f = own_fill
    if f.area:
        region = f.sub if safe is None else f.sub & safe[f.r0:f.r1, f.c0:f.c1]
        n = int(np.count_nonzero(region))
        if n and n < f.area:
            return _deepest(frame, f, region)
        # whole fill is safe (common: specks fully covered by ink at the other stops), or none
        # of it is: both cases use the deepest pixel of the fill itself, cached per fill
        if f.deep is None:
            f.deep = _deepest(frame, f, f.sub)
        return f.deep.copy()
    return fallback() if callable(fallback) else fallback


def _to_path(loops: list[np.ndarray]) -> pathops.Path:
    p = pathops.Path()
    pen = p.getPen()
    for c in loops:
        pen.moveTo(tuple(c[0]))
        for q in c[1:]:
            pen.lineTo(tuple(q))
        pen.closePath()
    return p


def _from_path(p: pathops.Path) -> list[np.ndarray]:
    from fontTools.pens.recordingPen import RecordingPen
    rec = RecordingPen()
    p.draw(rec)
    loops, cur = [], []
    for op, args in rec.value:
        if op == "moveTo":
            cur = [args[0]]
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            cur.extend(a for a in args if a is not None)
        elif op in ("closePath", "endPath"):
            if len(cur) >= 3:
                loops.append(np.array(cur, float))
            cur = []
    return normalise_orientation(loops)


def _labels(seeds: list[np.ndarray]) -> np.ndarray:
    """Partition map: index of the nearest seed region for every pixel."""
    dist = np.stack([ndimage.distance_transform_edt(~m) if m.any() else np.full(m.shape, 1e9)
                     for m in seeds])
    return np.argmin(dist, axis=0)


def _labels_for(lab: np.ndarray, region: np.ndarray, ink: np.ndarray) -> np.ndarray:
    """Give every non-ink pocket inside `region` (counters) wholly to one cell, so cut lines never
    cross a counter (abutting pieces there would leave anti-aliasing seams)."""
    pockets, n = ndimage.label(region & ~ink)
    if n == 0:
        return lab
    sel = pockets > 0
    p = pockets[sel]
    l = lab[sel]
    nl = int(l.max()) + 1
    # majority label per pocket (first max on ties, like bincount().argmax() per pocket)
    owner = np.bincount(p * nl + l, minlength=(n + 1) * nl).reshape(n + 1, nl).argmax(1)
    lab = lab.copy()
    lab[sel] = owner[p]
    return lab


def _cell(frame: Frame, lab: np.ndarray, i: int, ink: np.ndarray, dil_px: int = 2,
          clip: Fill | None = None, pad: int = 3):
    """Cell i as (mask, polygons). Neighbouring cells overlap by a small band, but only on ink,
    so the overlap hides seams without painting into counters.

    `clip`: fill of the loop that will be cut. The polygons are then only traced inside its box
    (+`pad` px): the cell outline elsewhere can't affect loop ∩ cell, and tracing/simplifying the
    whole frame-spanning cell was the main cost of splits."""
    own = lab == i
    cell = own | (ndimage.binary_dilation(own, iterations=dil_px) & ink)
    trace_m = cell
    if clip is not None and clip.area:
        r0, r1 = max(clip.r0 - pad, 0), min(clip.r1 + pad, frame.H)
        c0, c1 = max(clip.c0 - pad, 0), min(clip.c1 + pad, frame.W)
        trace_m = np.zeros_like(cell)
        trace_m[r0:r1, c0:c1] = cell[r0:r1, c0:c1]
    # staircase outlines with thousands of collinear points make pathops fail: simplify
    polys = [approximate_polygon(np.vstack([l, l[:1]]), tolerance=0.5 / frame.scale)[:-1]
             for l in frame.trace(trace_m, smooth=0.0)]
    return cell, polys


def _cut(frame: Frame, loop: np.ndarray, cell, loop_fill: Fill | None = None):
    """loop ∩ cell -> (islands largest first, holes). Vector (exact edges) with a raster fallback.
    Pieces can have holes (a cell wrapping around something); dropping them would fill ink in."""
    mask, polys = cell
    res_loops: list[np.ndarray] = []
    own = (loop_fill if loop_fill is not None else fill_crop(frame, [loop])).full() & mask
    if polys:
        try:
            res = pathops.op(_to_path([loop]), _to_path(polys), pathops.PathOp.INTERSECTION,
                             fix_winding=True)
            res_loops = [c for c in _from_path(res) if abs(signed_area(c)) > 1.0]
        except pathops.PathOpsError:
            res_loops = []
        if res_loops and np.count_nonzero(fill_crop(frame, res_loops).full() ^ own) > 0.03 * max(1, own.sum()):
            res_loops = []  # vector result disagrees with raster truth: distrust it
    if not res_loops:
        res_loops = frame.trace(own, smooth=0.5)
    isl = sorted([c for c in res_loops if is_island(c)], key=lambda c: -abs(signed_area(c)))
    holes = [c for c in res_loops if not is_island(c)]
    return isl, holes


def _boxes(fills: list[Fill]) -> np.ndarray:
    return np.array([(f.r0, f.r1, f.c0, f.c1) for f in fills], np.int64).reshape(-1, 4)


def _touching(bb: np.ndarray, f: Fill) -> np.ndarray:
    """Indices of boxes in `bb` that overlap f's box."""
    return np.flatnonzero((bb[:, 0] < f.r1) & (bb[:, 1] > f.r0) & (bb[:, 2] < f.c1) & (bb[:, 3] > f.c0))


def _split_merge(frame: Frame, tracks: list, alive: list[int], k: int, loops, fills, kinds,
                 masks: list[np.ndarray], frac: float = 0.25):
    """Handle island splits (1 parent -> n children) and merges (n parents -> 1 child) at stop k.

    Returns (assigned_track_ids, used_loop_ids, new_alive).
    """
    isl_t = [ti for ti in alive if tracks[ti].island]
    isl_l = [li for li, kd in enumerate(kinds) if kd]
    if not isl_t or not isl_l:
        return set(), set(), []
    area_t = {ti: max(1, tracks[ti].fills[k - 1].area) for ti in isl_t}
    area_l = {li: max(1, fills[li].area) for li in isl_l}
    # sparse pairwise intersections: pairs whose boxes don't touch are 0 without touching pixels
    inter: dict[tuple[int, int], int] = {}
    bb = _boxes([fills[li] for li in isl_l])
    for ti in isl_t:
        ft = tracks[ti].fills[k - 1]
        if not ft.area:
            continue
        for j in _touching(bb, ft).tolist():
            v = ft.inter(fills[isl_l[j]])
            if v:
                inter[(ti, isl_l[j])] = v
    # best parent / child: first max in isl_t / isl_l order, exactly as max(seq, key=inter) picks
    # (pairs absent from `inter` are 0, and 0-valued choices are never used below)
    best_parent = {li: isl_t[0] for li in isl_l}
    best_child = {ti: isl_l[0] for ti in isl_t}
    bp_v: dict[int, int] = {}
    bc_v: dict[int, int] = {}
    pos_t = {ti: i for i, ti in enumerate(isl_t)}
    pos_l = {li: i for i, li in enumerate(isl_l)}
    for (ti, li) in sorted(inter, key=lambda p: (pos_l[p[1]], pos_t[p[0]])):
        if inter[(ti, li)] > bp_v.get(li, 0):
            bp_v[li] = inter[(ti, li)]; best_parent[li] = ti
    for (ti, li) in sorted(inter, key=lambda p: (pos_t[p[0]], pos_l[p[1]])):
        if inter[(ti, li)] > bc_v.get(ti, 0):
            bc_v[ti] = inter[(ti, li)]; best_child[ti] = li
    assigned, used, new_alive = set(), set(), []

    # splits
    for ti in isl_t:
        kids = [li for li in isl_l if best_parent[li] == ti and inter.get((ti, li), 0) / area_l[li] >= frac]
        if len(kids) < 2:
            continue
        lab = _labels([fills[li].full() for li in kids])
        old = tracks[ti]
        # decide counter ownership ONCE (union of counters over all stops) so it can't flip
        pockets = np.zeros_like(lab, bool)
        for j in old.loops:
            pockets |= old.fills[j].full() & ~masks[j]
        lab = _labels_for(lab, pockets, np.zeros_like(pockets))
        for i, li in enumerate(kids):
            t = Track(True)
            for j, lj in old.loops.items():
                pieces, holes = _cut(frame, lj, _cell(frame, lab, i, masks[j], clip=old.fills[j]), old.fills[j])
                for h in holes:
                    tracks.append(Track(False, {j: h}, {j: fill_crop(frame, [h])}))
                if pieces:
                    t.loops[j] = pieces[0]
                    t.fills[j] = fill_crop(frame, [pieces[0]])
                for extra in pieces[1:]:  # disconnected bits: own short-lived track
                    tracks.append(Track(True, {j: extra}, {j: fill_crop(frame, [extra])}))
            t.loops[k] = loops[li]
            t.fills[k] = fills[li]
            tracks.append(t)
            new_alive.append(len(tracks) - 1)
            used.add(li)
        old.loops.clear()  # retired: its history now lives in the pieces
        assigned.add(ti)

    # merges
    for li in isl_l:
        if li in used:
            continue
        pars = [ti for ti in isl_t if ti not in assigned and best_child[ti] == li
                and inter.get((ti, li), 0) / area_t[ti] >= frac]
        if len(pars) < 2:
            continue
        lab = _labels([tracks[ti].fills[k - 1].full() for ti in pars])
        lab = _labels_for(lab, fills[li].full(), masks[k])  # same for every parent: do it once
        for i, ti in enumerate(pars):
            pieces, holes = _cut(frame, loops[li], _cell(frame, lab, i, masks[k], clip=fills[li]), fills[li])
            for h in holes:
                tracks.append(Track(False, {k: h}, {k: fill_crop(frame, [h])}))
                new_alive.append(len(tracks) - 1)
            if pieces:
                tracks[ti].loops[k] = pieces[0]
                tracks[ti].fills[k] = fill_crop(frame, [pieces[0]])
                new_alive.append(ti)
            for extra in pieces[1:]:
                tracks.append(Track(True, {k: extra}, {k: fill_crop(frame, [extra])}))
                new_alive.append(len(tracks) - 1)
            assigned.add(ti)
        used.add(li)
    return assigned, used, new_alive


def compatibilise(frame: Frame, stops: list[list[np.ndarray]], masks: list[np.ndarray],
                  step: float, unit: float = 1.0, min_points: int = 12, min_score: float = 0.05,
                  split_merge: bool = True):
    """stops[k] = loops at stop k (oriented). masks[k] = ink mask at stop k.

    Returns (masters, oncurve): masters[k] = list of (K,2) arrays per track;
    oncurve = list of (K,) bool arrays per track (corner points, shared by all stops).
    """
    tracks: list[Track] = []
    alive: list[int] = []
    for k, loops in enumerate(stops):
        fills = [fill_crop(frame, [l]) for l in loops]
        kinds = [is_island(l) for l in loops]
        pre_t, pre_l, pre_alive = (_split_merge(frame, tracks, alive, k, loops, fills, kinds, masks)
                                   if k > 0 and split_merge else (set(), set(), []))
        cands = []
        if loops:
            bb = _boxes(fills)
            isl = np.array(kinds, bool)
            free = np.ones(len(loops), bool)
            free[list(pre_l)] = False
            for ti in alive:
                if ti in pre_t:
                    continue
                t = tracks[ti]
                ft = t.fills[k - 1]
                if not ft.area:
                    continue
                for li in _touching(bb, ft).tolist():
                    if isl[li] != t.island or not free[li]:
                        continue
                    f = fills[li]
                    inter = ft.inter(f)
                    if inter:
                        cands.append((inter / (ft.area + f.area - inter), ti, li))
        used_t, used_l, nxt = set(), set(pre_l), list(pre_alive)
        for score, ti, li in sorted(cands, reverse=True):
            if score < min_score or ti in used_t or li in used_l:
                continue
            tracks[ti].loops[k] = loops[li]
            tracks[ti].fills[k] = fills[li]
            used_t.add(ti); used_l.add(li); nxt.append(ti)
        for li, l in enumerate(loops):
            if li not in used_l:
                tracks.append(Track(kinds[li], {k: l}, {k: fills[li]}))
                nxt.append(len(tracks) - 1)
        alive = nxt

    n = len(stops)
    out: list[list[np.ndarray]] = [[] for _ in range(n)]
    flags_out: list[np.ndarray] = []
    for t in tracks:
        if not t.loops:
            continue
        ks = sorted(t.loops)
        corners = {k: _corners(t.loops[k], unit) for k in ks}  # once per loop, reused below
        ncorners = max(len(corners[k][1]) for k in ks)
        K = max(min_points, int(round(max(corners[k][2] for k in ks) / step)),
                int(ncorners * 1.5) + 4)
        res, flg = {}, {}
        for k in ks:
            res[k], flg[k] = resample_corners(t.loops[k], K, unit, corners[k])
        for a, b in zip(ks, ks[1:]):
            sh = best_shift(res[a], res[b])
            res[b] = np.roll(res[b], -sh, axis=0)
            flg[b] = np.roll(flg[b], -sh)
        flags = np.zeros(K, bool)
        for k in ks:
            flags |= flg[k]
        flags_out.append(flags)
        for k in range(n):
            if k in res:
                out[k].append(res[k])
                continue
            near = min(ks, key=lambda j: abs(j - k))
            pt = _anchor(frame, t.fills[near], masks[k], lambda: res[near].mean(0))
            out[k].append(np.repeat(pt[None], K, axis=0))
    return out, flags_out
