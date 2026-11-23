"""Loading fonts and extracting clean, flattened glyph outlines."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pathops
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer


@dataclass
class FontInfo:
    upm: int
    ascender: int
    descender: int
    x_height: int
    cap_height: int
    family: str


def load_font(source: str | Path | bytes, location: dict[str, float] | None = None) -> TTFont:
    """Load OTF/TTF/WOFF/WOFF2. Variable fonts are pinned to `location` (axis defaults if omitted)."""
    f = TTFont(io.BytesIO(source) if isinstance(source, bytes) else source)
    f.flavor = None
    if "fvar" in f:
        loc = {a.axisTag: a.defaultValue for a in f["fvar"].axes}
        loc.update(location or {})
        f = instancer.instantiateVariableFont(f, loc, inplace=False, updateFontNames=False)
    return f


def font_to_bytes(font: TTFont) -> bytes:
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def font_info(font: TTFont) -> FontInfo:
    os2 = font["OS/2"]
    upm = font["head"].unitsPerEm
    xh = getattr(os2, "sxHeight", 0) or int(upm * 0.5)
    ch = getattr(os2, "sCapHeight", 0) or int(upm * 0.7)
    name = font["name"]
    fam = name.getDebugName(16) or name.getDebugName(1) or "Font"
    return FontInfo(upm, font["hhea"].ascent, font["hhea"].descent, xh, ch, fam)


def _bez(pts: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Evaluate a Bezier (any degree) at many t values."""
    n = len(pts) - 1
    from math import comb

    coef = np.array([comb(n, i) for i in range(n + 1)], float)
    t = ts[:, None]
    basis = coef * (t ** np.arange(n + 1)) * ((1 - t) ** (n - np.arange(n + 1)))
    return basis @ pts


def glyph_loops(glyphset, name: str, flatten_tol: float = 1.0) -> list[np.ndarray]:
    """Decompose, remove overlaps (skia-pathops), flatten to closed polylines (float units).

    Orientation is normalised: islands clockwise (TrueType), holes counter-clockwise.
    """
    rec = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(rec)
    path = pathops.Path()
    pen = path.getPen()
    rec.replay(pen)
    try:
        path.simplify(fix_winding=True, keep_starting_points=False)
    except pathops.PathOpsError:
        pass
    loops: list[np.ndarray] = []
    rp = DecomposingRecordingPen(glyphset)
    path.draw(rp)
    cur: list[np.ndarray] = []
    last = start = None
    for op, args in rp.value:
        if op == "moveTo":
            cur = [np.array([args[0]], float)]
            start = last = args[0]
        elif op == "lineTo":
            cur.append(np.array([args[0]], float))
            last = args[0]
        elif op == "qCurveTo" and args[-1] is None:
            # closed contour made only of off-curve points: implied start = midpoint(last, first)
            offs = [np.asarray(a, float) for a in args[:-1]]
            start = tuple((offs[-1] + offs[0]) / 2)
            cur = [np.array([start], float)]
            pts = [start, *[tuple(o) for o in offs], start]
            for i in range(1, len(pts) - 1):
                p0 = pts[0] if i == 1 else tuple(np.add(pts[i - 1], pts[i]) / 2)
                p2 = pts[-1] if i == len(pts) - 2 else tuple(np.add(pts[i], pts[i + 1]) / 2)
                seg = np.array([p0, pts[i], p2], float)
                length = np.hypot(*np.diff(seg, axis=0).T).sum()
                cur.append(_bez(seg, np.linspace(0, 1, max(2, int(length / (4 * flatten_tol))) + 1)[1:]))
            last = start
        elif op in ("curveTo", "qCurveTo"):
            if op == "qCurveTo":  # expand to quadratic segments
                pts = [last, *args]
                segs = []
                for i in range(1, len(pts) - 1):
                    p0 = pts[0] if i == 1 else tuple(np.add(pts[i - 1], pts[i]) / 2)
                    p2 = pts[-1] if i == len(pts) - 2 else tuple(np.add(pts[i], pts[i + 1]) / 2)
                    segs.append([p0, pts[i], p2])
            else:
                segs = [[last, *args]]
            for seg in segs:
                seg = np.array(seg, float)
                length = np.hypot(*np.diff(seg, axis=0).T).sum()
                n = max(2, int(length / (4 * flatten_tol)))
                cur.append(_bez(seg, np.linspace(0, 1, n + 1)[1:]))
            last = args[-1]
        elif op in ("closePath", "endPath"):
            if cur:
                loop = np.vstack(cur)
                if len(loop) > 1 and np.allclose(loop[0], loop[-1]):
                    loop = loop[:-1]
                if len(loop) >= 3:
                    loops.append(loop)
            cur = []
    return normalise_orientation(loops)


def signed_area(c: np.ndarray) -> float:
    x, y = c[:, 0], c[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def normalise_orientation(loops: list[np.ndarray]) -> list[np.ndarray]:
    """Classify islands/holes by nesting depth and orient islands CW, holes CCW."""
    out = []
    for i, c in enumerate(loops):
        p = c[0]
        depth = sum(1 for j, o in enumerate(loops) if j != i and point_in_poly(p, o))
        island = depth % 2 == 0
        clockwise = signed_area(c) < 0
        out.append(c if clockwise == island else c[::-1].copy())
    return out


def is_island(c: np.ndarray) -> bool:
    return signed_area(c) < 0


def point_in_poly(p, poly: np.ndarray) -> bool:
    x, y = p
    xs, ys = poly[:, 0], poly[:, 1]
    xn, yn = np.roll(xs, -1), np.roll(ys, -1)
    cond = (ys > y) != (yn > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = xs + (y - ys) * (xn - xs) / (yn - ys)
    return bool(np.count_nonzero(cond & (x < xi)) % 2)
