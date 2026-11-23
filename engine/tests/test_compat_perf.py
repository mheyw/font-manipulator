"""The compatibiliser's fast paths must give exactly what the straightforward full-frame code gives."""
import numpy as np
from scipy import ndimage

from fontmanip.compat import (_anchor, _corners, _labels_for, compatibilise, fill_crop, find_corners,
                              perimeter, resample, resample_corners)
from fontmanip.raster import Frame


def blob(rng, cx, cy, r, n=40, wobble=0.3, cw=True):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rr = r * (1 + wobble * rng.uniform(-1, 1, n))
    c = np.c_[cx + rr * np.cos(t), cy + rr * np.sin(t)]
    return c[::-1].copy() if cw else c  # islands clockwise


def loops_for(rng, k=30):
    out = []
    for _ in range(k):
        r = rng.uniform(0.6, 40)
        out.append(blob(rng, rng.uniform(-30, 230), rng.uniform(-30, 180), r,
                        n=int(rng.integers(5, 60)), cw=bool(rng.integers(0, 2))))
    return out


def test_fill_crop_matches_frame_fill():
    rng = np.random.default_rng(1)
    fr = Frame(0, 0, 200, 150, 1.3)
    loops = loops_for(rng, 60)  # includes specks, loops crossing the frame edge, both windings
    for l in loops:
        f = fill_crop(fr, [l])
        assert np.array_equal(f.full(), fr.fill([l]))
        assert f.area == np.count_nonzero(fr.fill([l]))
    for i in range(0, 60, 6):
        assert np.array_equal(fill_crop(fr, loops[i:i + 6]).full(), fr.fill(loops[i:i + 6]))
    assert fill_crop(fr, []).area == 0
    assert fill_crop(fr, [np.array([[500.0, 500], [501, 500], [501, 501]])]).area == 0  # off-frame


def test_fill_intersection_counts():
    rng = np.random.default_rng(2)
    fr = Frame(0, 0, 200, 150, 1.0)
    fills = [fill_crop(fr, [l]) for l in loops_for(rng, 30)]
    for a in fills:
        for b in fills:
            assert a.inter(b) == np.count_nonzero(a.full() & b.full())


def _labels_for_ref(lab, region, ink):
    pockets, n = ndimage.label(region & ~ink)
    lab = lab.copy()
    for c in range(1, n + 1):
        sel = pockets == c
        lab[sel] = np.bincount(lab[sel]).argmax()
    return lab


def test_labels_for_matches_per_pocket_loop():
    rng = np.random.default_rng(3)
    lab = rng.integers(0, 4, (60, 80))
    lab = ndimage.median_filter(lab, 5)
    region = rng.random((60, 80)) > 0.3
    ink = rng.random((60, 80)) > 0.6
    assert np.array_equal(_labels_for(lab, region, ink), _labels_for_ref(lab, region, ink))
    assert np.array_equal(_labels_for(lab, np.zeros_like(region), ink), lab)


def _anchor_ref(frame, own_fill, safe, fallback):
    for region in ((own_fill & safe) if safe is not None else None, own_fill):
        if region is not None and region.any():
            rows = np.flatnonzero(region.any(axis=1)); cols = np.flatnonzero(region.any(axis=0))
            r0, r1 = max(rows[0] - 1, 0), rows[-1] + 2
            c0, c1 = max(cols[0] - 1, 0), cols[-1] + 2
            depth = ndimage.distance_transform_edt(region[r0:r1, c0:c1])
            r, c = np.unravel_index(int(np.argmax(depth)), depth.shape)
            r, c = r + r0, c + c0
            return np.array([(c + 0.5) / frame.scale + frame.x0, (r + 0.5) / frame.scale + frame.y0])
    return fallback


def test_anchor_matches_full_frame():
    rng = np.random.default_rng(4)
    fr = Frame(0, 0, 200, 150, 1.0)
    safe = fr.fill(loops_for(rng, 12))
    fb = np.array([1.0, 2.0])
    for l in loops_for(rng, 60):
        f = fill_crop(fr, [l])
        for s in (safe, ~safe, np.ones_like(safe), None):
            for _ in range(2):  # second call hits the per-fill cache
                assert np.array_equal(_anchor(fr, f, s, fb), _anchor_ref(fr, f.full(), s, fb))


def _find_corners_ref(loop, unit, thresh_deg=40.0):
    fine = resample(loop, max(16, int(perimeter(loop) / (2 * unit))))
    m = 3
    a = fine - np.roll(fine, m, 0)
    b = np.roll(fine, -m, 0) - fine
    ang = np.degrees(np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1))))
    win = np.stack([np.roll(ang, s) for s in range(-m, m + 1)])
    idx = np.flatnonzero((ang >= win.max(0)) & (ang > thresh_deg))
    if len(idx) > 1:
        idx = idx[np.r_[True, np.diff(idx) > m]]
    return fine, idx


def test_corners_match_reference():
    rng = np.random.default_rng(5)
    sq = np.array([[0, 0], [0, 100], [100, 100], [100, 0]], float)
    for l in [sq, sq[:3]] + loops_for(rng, 40):
        fine, idx = find_corners(l, 1.0)
        rf, ri = _find_corners_ref(l, 1.0)
        assert np.array_equal(fine, rf) and np.array_equal(idx, ri)
        assert _corners(l, 1.0)[2] == perimeter(l)
        for k in (12, 40):
            p1, f1 = resample_corners(l, k, 1.0)
            p2, f2 = resample_corners(l, k, 1.0, _corners(l, 1.0))
            assert np.array_equal(p1, p2) and np.array_equal(f1, f2)


def test_compatibilise_many_specks_deterministic():
    rng = np.random.default_rng(6)
    fr = Frame(-40, -40, 240, 190, 1.0)
    base = [blob(rng, 100, 75, 60, n=80, wobble=0.05)]
    stops = [base]
    for _ in range(3):  # pits (holes) and spatter (islands) that come and go between stops
        pits = [blob(rng, rng.uniform(60, 140), rng.uniform(40, 110), rng.uniform(1, 4), n=8, cw=False)
                for _ in range(25)]
        spat = [blob(rng, rng.uniform(-20, 220), rng.uniform(-20, 170), rng.uniform(0.8, 3), n=8)
                for _ in range(25)]
        spat = [s for s in spat if np.hypot(*(s.mean(0) - [100, 75])) > 80]
        stops.append(base + pits + spat)
    masks = [fr.fill(s) for s in stops]
    a = compatibilise(fr, stops, masks, step=5)
    b = compatibilise(fr, stops, masks, step=5)
    assert len({len(m) for m in a[0]}) == 1
    for ma, mb in zip(a[0], b[0]):
        assert all(np.array_equal(x, y) for x, y in zip(ma, mb))
    assert all(np.array_equal(x, y) for x, y in zip(a[1], b[1]))
