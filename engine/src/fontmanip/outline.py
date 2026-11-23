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
