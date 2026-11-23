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


@dataclass
class Track:
    island: bool
    loops: dict[int, np.ndarray] = field(default_factory=dict)
    fills: dict[int, np.ndarray] = field(default_factory=dict)


def perimeter(c: np.ndarray) -> float:
    return float(np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T).sum())


def resample(c: np.ndarray, k: int) -> np.ndarray:
    closed = np.vstack([c, c[:1]])
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(closed, axis=0).T))]
    ts = np.linspace(0, d[-1], k, endpoint=False)
    return np.c_[np.interp(ts, d, closed[:, 0]), np.interp(ts, d, closed[:, 1])]


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
    per = perimeter(loop)
    fine = resample(loop, max(16, int(per / (2 * unit))))
    m = 3
    a = fine - np.roll(fine, m, 0)
    b = np.roll(fine, -m, 0) - fine
    ang = np.degrees(np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1))))
    win = np.stack([np.roll(ang, s) for s in range(-m, m + 1)])
    is_max = (ang >= win.max(0)) & (ang > thresh_deg)
    idx = np.flatnonzero(is_max)
    # break ties on plateaus: keep first of consecutive indices
    if len(idx) > 1:
        keep = np.r_[True, np.diff(idx) > m]
        idx = idx[keep]
    return fine, idx


def resample_corners(loop: np.ndarray, k: int, unit: float):
    """K points with a sample exactly on every corner. Returns (points, corner_flags)."""
    fine, C = find_corners(loop, unit)
    if len(C) == 0 or len(C) > k:
        return resample(loop, k), np.zeros(k, bool)
    closed = np.vstack([fine, fine[:1]])
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(closed, axis=0).T))]
    total = d[-1]
    starts = d[C]
    lens = np.diff(np.r_[starts, starts[0] + total])
    raw = lens / total * k
    n = np.maximum(1, np.floor(raw).astype(int))
    while n.sum() < k:
        n[np.argmax(raw - n)] += 1
    while n.sum() > k:
        j = np.argmax(np.where(n > 1, n - raw, -np.inf))
        n[j] -= 1
    ts = np.concatenate([s + L * np.arange(c) / c for s, L, c in zip(starts, lens, n)]) % total
    pts = np.c_[np.interp(ts, d, closed[:, 0]), np.interp(ts, d, closed[:, 1])]
    flags = np.zeros(k, bool)
    flags[np.r_[0, np.cumsum(n)[:-1]]] = True
    return pts, flags


def _anchor(frame: Frame, own_fill: np.ndarray, safe: np.ndarray | None, fallback: np.ndarray) -> np.ndarray:
    """Point to collapse a track onto: deepest pixel of own_fill ∩ safe, else of own_fill."""
    for region in ((own_fill & safe) if safe is not None else None, own_fill):
        if region is not None and region.any():
            depth = ndimage.distance_transform_edt(region)
            r, c = np.unravel_index(int(np.argmax(depth)), depth.shape)
            return np.array([(c + 0.5) / frame.scale + frame.x0, (r + 0.5) / frame.scale + frame.y0])
    return fallback


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
    lab = lab.copy()
    for c in range(1, n + 1):
        sel = pockets == c
        lab[sel] = np.bincount(lab[sel]).argmax()
    return lab


def _cell(frame: Frame, lab: np.ndarray, i: int, ink: np.ndarray, dil_px: int = 2):
    """Cell i as (mask, polygons). Neighbouring cells overlap by a small band, but only on ink,
    so the overlap hides seams without painting into counters."""
    own = lab == i
    cell = own | (ndimage.binary_dilation(own, iterations=dil_px) & ink)
    # staircase outlines with thousands of collinear points make pathops fail: simplify
    polys = [approximate_polygon(np.vstack([l, l[:1]]), tolerance=0.5 / frame.scale)[:-1]
             for l in frame.trace(cell, smooth=0.0)]
    return cell, polys


def _cut(frame: Frame, loop: np.ndarray, cell):
    """loop ∩ cell -> (islands largest first, holes). Vector (exact edges) with a raster fallback.
    Pieces can have holes (a cell wrapping around something); dropping them would fill ink in."""
    mask, polys = cell
    res_loops: list[np.ndarray] = []
    own = frame.fill([loop]) & mask
    if polys:
        try:
            res = pathops.op(_to_path([loop]), _to_path(polys), pathops.PathOp.INTERSECTION,
                             fix_winding=True)
            res_loops = [c for c in _from_path(res) if abs(signed_area(c)) > 1.0]
        except pathops.PathOpsError:
            res_loops = []
        if res_loops and np.count_nonzero(frame.fill(res_loops) ^ own) > 0.03 * max(1, own.sum()):
            res_loops = []  # vector result disagrees with raster truth: distrust it
    if not res_loops:
        res_loops = frame.trace(own, smooth=0.5)
    isl = sorted([c for c in res_loops if is_island(c)], key=lambda c: -abs(signed_area(c)))
    holes = [c for c in res_loops if not is_island(c)]
    return isl, holes


def _split_merge(frame: Frame, tracks: list, alive: list[int], k: int, loops, fills, kinds,
                 masks: list[np.ndarray], frac: float = 0.25):
    """Handle island splits (1 parent -> n children) and merges (n parents -> 1 child) at stop k.

    Returns (assigned_track_ids, used_loop_ids, new_alive).
    """
    isl_t = [ti for ti in alive if tracks[ti].island]
    isl_l = [li for li, kd in enumerate(kinds) if kd]
    if not isl_t or not isl_l:
        return set(), set(), []
    area_t = {ti: max(1, np.count_nonzero(tracks[ti].fills[k - 1])) for ti in isl_t}
    area_l = {li: max(1, np.count_nonzero(fills[li])) for li in isl_l}
    inter = {(ti, li): np.count_nonzero(tracks[ti].fills[k - 1] & fills[li]) for ti in isl_t for li in isl_l}
    best_parent = {li: max(isl_t, key=lambda t: inter[(t, li)]) for li in isl_l}
    best_child = {ti: max(isl_l, key=lambda l: inter[(ti, l)]) for ti in isl_t}
    assigned, used, new_alive = set(), set(), []

    # splits
    for ti in isl_t:
        kids = [li for li in isl_l if best_parent[li] == ti and inter[(ti, li)] / area_l[li] >= frac]
        if len(kids) < 2:
            continue
        lab = _labels([fills[li] for li in kids])
        old = tracks[ti]
        # decide counter ownership ONCE (union of counters over all stops) so it can't flip
        pockets = np.zeros_like(lab, bool)
        for j in old.loops:
            pockets |= old.fills[j] & ~masks[j]
        lab = _labels_for(lab, pockets, np.zeros_like(pockets))
        for i, li in enumerate(kids):
            t = Track(True)
            for j, lj in old.loops.items():
                pieces, holes = _cut(frame, lj, _cell(frame, lab, i, masks[j]))
                for h in holes:
                    tracks.append(Track(False, {j: h}, {j: frame.fill([h])}))
                if pieces:
                    t.loops[j] = pieces[0]
                    t.fills[j] = frame.fill([pieces[0]])
                for extra in pieces[1:]:  # disconnected bits: own short-lived track
                    tracks.append(Track(True, {j: extra}, {j: frame.fill([extra])}))
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
                and inter[(ti, li)] / area_t[ti] >= frac]
        if len(pars) < 2:
            continue
        lab = _labels([tracks[ti].fills[k - 1] for ti in pars])
        for i, ti in enumerate(pars):
            pieces, holes = _cut(frame, loops[li], _cell(frame, _labels_for(lab, fills[li], masks[k]), i, masks[k]))
            for h in holes:
                tracks.append(Track(False, {k: h}, {k: frame.fill([h])}))
                new_alive.append(len(tracks) - 1)
            if pieces:
                tracks[ti].loops[k] = pieces[0]
                tracks[ti].fills[k] = frame.fill([pieces[0]])
                new_alive.append(ti)
            for extra in pieces[1:]:
                tracks.append(Track(True, {k: extra}, {k: frame.fill([extra])}))
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
        fills = [frame.fill([l]) for l in loops]
        kinds = [is_island(l) for l in loops]
        pre_t, pre_l, pre_alive = (_split_merge(frame, tracks, alive, k, loops, fills, kinds, masks)
                                   if k > 0 and split_merge else (set(), set(), []))
        cands = []
        for ti in alive:
            if ti in pre_t:
                continue
            t = tracks[ti]
            ft = t.fills[k - 1]
            for li, f in enumerate(fills):
                if kinds[li] != t.island or li in pre_l:
                    continue
                inter = np.count_nonzero(ft & f)
                if inter:
                    union = np.count_nonzero(ft | f)
                    cands.append((inter / union, ti, li))
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
        ncorners = max(len(find_corners(t.loops[k], unit)[1]) for k in ks)
        K = max(min_points, int(round(max(perimeter(l) for l in t.loops.values()) / step)),
                int(ncorners * 1.5) + 4)
        res, flg = {}, {}
        for k in ks:
            res[k], flg[k] = resample_corners(t.loops[k], K, unit)
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
            pt = _anchor(frame, t.fills[near], masks[k], res[near].mean(0))
            out[k].append(np.repeat(pt[None], K, axis=0))
    return out, flags_out
