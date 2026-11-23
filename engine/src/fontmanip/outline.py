"""Render TrueType-style contours (on/off-curve points) to polylines and SVG."""
from __future__ import annotations

import numpy as np


def _segments(pts: np.ndarray, on: np.ndarray):
    """Yield ('L', p) / ('Q', ctrl, p) segments of a closed TrueType contour, plus the start point."""
    n = len(pts)
    if n == 0:
        return None, []
    if on.any():
        s = int(np.flatnonzero(on)[0])
        start = pts[s]
        order = [(s + i) % n for i in range(1, n + 1)]
    else:
        start = (pts[-1] + pts[0]) / 2
        order = list(range(n))
    segs, cur_ctrl = [], None
    for i in order:
        p, o = pts[i], on[i]
        if o:
            segs.append(("L", p) if cur_ctrl is None else ("Q", cur_ctrl, p))
            cur_ctrl = None
        else:
            if cur_ctrl is not None:
                mid = (cur_ctrl + p) / 2
                segs.append(("Q", cur_ctrl, mid))
            cur_ctrl = p
    if cur_ctrl is not None:
        segs.append(("Q", cur_ctrl, start))
    return start, segs


def flatten(pts: np.ndarray, on: np.ndarray, samples: int = 4) -> np.ndarray:
    start, segs = _segments(np.asarray(pts, float), np.asarray(on, bool))
    if start is None:
        return np.zeros((0, 2))
    out, cur = [start], start
    ts = np.linspace(0, 1, samples + 1)[1:, None]
    for sg in segs:
        if sg[0] == "L":
            out.append(sg[1]); cur = sg[1]
        else:
            c, p = sg[1], sg[2]
            out.extend((1 - ts) ** 2 * cur + 2 * (1 - ts) * ts * c + ts ** 2 * p)
            cur = p
    return np.array(out[:-1]) if len(out) > 1 else np.array(out)


def svg_path(contours, flags) -> str:
    parts = []
    for c, on in zip(contours, flags):
        start, segs = _segments(np.asarray(c, float), np.asarray(on, bool))
        if start is None or not segs:
            continue
        d = [f"M{start[0]:.0f} {start[1]:.0f}"]
        for sg in segs:
            if sg[0] == "L":
                d.append(f"L{sg[1][0]:.0f} {sg[1][1]:.0f}")
            else:
                d.append(f"Q{sg[1][0]:.0f} {sg[1][1]:.0f} {sg[2][0]:.0f} {sg[2][1]:.0f}")
        parts.append(" ".join(d) + "Z")
    return " ".join(parts)


class PackedFlattener:
    """`flatten` for many contours at once, for a fixed set of on-curve flags.

    The segment structure only depends on the flags, so it is planned once and then evaluated for
    any number of point sets with the same structure (every stop, every midpoint instance) as a
    few vectorised numpy ops. Output is bit-identical to `[flatten(c, on) for ...]` packed end to
    end; use with `Frame.winding_packed(pts, self.lengths)`.
    """

    def __init__(self, flags: list[np.ndarray], samples: int = 4):
        # Every emitted point is (P[a] + P[b]) / 2 (a == b for plain points: exact in floats), or a
        # quadratic sample from three such expressions. Plan both kinds, then where they go.
        la, lb, lpos = [], [], []                     # plain / midpoint emissions
        qs, qc, qe, qpos = [], [], [], []             # quadratic segments: start, ctrl, end exprs
        lengths, pos, base = [], 0, 0
        ns = samples
        for on in flags:
            on = np.asarray(on, bool)
            n = len(on)
            if n == 0:
                lengths.append(0)
                continue
            if on.any():
                s = int(np.flatnonzero(on)[0])
                start = (base + s, base + s)
                order = [(s + i) % n for i in range(1, n + 1)]
            else:
                start = (base + n - 1, base)
                order = list(range(n))
            emitted = [("P", start)]
            cur, cur_ctrl = start, None
            for i in order:
                gi = base + i
                if on[i]:
                    end = (gi, gi)
                    if cur_ctrl is None:
                        emitted.append(("P", end))
                    else:
                        emitted.append(("Q", (cur, cur_ctrl, end)))
                    cur, cur_ctrl = end, None
                else:
                    if cur_ctrl is not None:
                        mid = (cur_ctrl, gi)
                        emitted.append(("Q", (cur, cur_ctrl, mid)))
                        cur = mid
                    cur_ctrl = gi
            if cur_ctrl is not None:
                emitted.append(("Q", (cur, cur_ctrl, start)))
            # flatten drops the final point (it repeats the start) unless only the start exists
            cnt = 0
            items = []
            for kind, e in emitted:
                k = 1 if kind == "P" else ns
                items.append((kind, e, k))
                cnt += k
            keep = cnt - 1 if cnt > 1 else cnt
            p0 = pos
            for kind, e, k in items:
                if kind == "P":
                    if pos - p0 < keep:
                        la.append(e[0]); lb.append(e[1]); lpos.append(pos)
                    pos += 1
                else:
                    qs.append(e[0]); qc.append(e[1]); qe.append(e[2])
                    qpos.append(pos)
                    pos += k
            lengths.append(keep)
            pos = p0 + keep
            base += n
        self.n_points = base
        self.total = pos
        self.lengths = np.array(lengths, np.int64)
        self._la, self._lb = np.array(la, np.int64), np.array(lb, np.int64)
        self._lpos = np.array(lpos, np.int64)
        q = lambda v: np.array(v, np.int64).reshape(-1, 2)
        self._qs, self._qe = q(qs), q(qe)
        self._qc = np.array(qc, np.int64)
        qpos = np.array(qpos, np.int64)
        # each quadratic emits `ns` samples at qpos + 0..ns-1; the very last one of a contour may
        # fall off the end (dropped closing point): mask those out
        qp = qpos[:, None] + np.arange(ns)
        self._qpos = qp
        self._qkeep = qp < self._contour_end(qpos)[:, None]
        self._ts = np.linspace(0, 1, samples + 1)[1:, None]

    def _contour_end(self, positions: np.ndarray) -> np.ndarray:
        ends = np.cumsum(self.lengths)
        return ends[np.searchsorted(ends, positions, side="right")] if len(positions) else positions

    def __call__(self, contours) -> np.ndarray:
        """Packed flattened points (total, 2) for contours matching the planned flags."""
        P = np.concatenate([np.asarray(c, float) for c in contours]) if len(contours) else np.zeros((0, 2))
        out = np.empty((self.total, 2))
        if len(self._la):
            out[self._lpos] = (P[self._la] + P[self._lb]) / 2
        if len(self._qc):
            cur = ((P[self._qs[:, 0]] + P[self._qs[:, 1]]) / 2)[:, None, :]
            c = P[self._qc][:, None, :]
            p = ((P[self._qe[:, 0]] + P[self._qe[:, 1]]) / 2)[:, None, :]
            ts = self._ts[None]
            samp = (1 - ts) ** 2 * cur + 2 * (1 - ts) * ts * c + ts ** 2 * p
            out[self._qpos[self._qkeep]] = samp[self._qkeep]
        return out
