"""The raster/effects speed-ups are meant to be *exact*: cropped / one-sided / packed versions must
give the same pixels and points as the straightforward full-frame code they replaced."""
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage
from skimage import measure

from fontmanip import effects as fx
from fontmanip.effects import Ctx, GlyphState, apply_effect
from fontmanip.fontio import font_info, glyph_loops, load_font, signed_area
from fontmanip.outline import PackedFlattener, flatten
from fontmanip.raster import (Frame, _texture, blur, edt_in, edt_out, noise_field, sdf, sdf_below,
                              sdf_pair, seed_for)

FONT = Path(__file__).parents[2] / "fonts" / "SourceSans3-Regular.otf"


# ---------------------------------------------------------------- reference implementations

def winding_ref(fr: Frame, loops):
    H, W = fr.H, fr.W
    if not loops:
        return np.zeros((H, W), np.int32)
    xs0, ys0, xs1, ys1 = [], [], [], []
    for c in loops:
        cx, cy = fr.to_px(c)
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
    return np.rint(np.cumsum(acc.reshape(H, W + 1), axis=1)[:, :W]).astype(np.int32)


def trace_ref(fr: Frame, mask, smooth=0.5, min_area=0.0):
    if not mask.any():
        return []
    raw = measure.find_contours(np.pad(mask.astype(float), 2), 0.5, positive_orientation="high")
    loops = []
    for L in raw:
        if len(L) < 4:
            continue
        if np.allclose(L[0], L[-1]):
            L = L[:-1]
        rows, cols = L[:, 0] - 2, L[:, 1] - 2
        p = np.c_[(cols + 0.5) / fr.scale + fr.x0, (rows + 0.5) / fr.scale + fr.y0]
        for _ in range(int(round(smooth * 4))):
            p = 0.5 * p + 0.25 * (np.roll(p, 1, 0) + np.roll(p, -1, 0))
        if abs(signed_area(p)) < max(min_area, 1e-6):
            continue
        loops.append(p)
    if not loops:
        return []
    areas = [signed_area(p) for p in loops]
    island_sign = np.sign(areas[int(np.argmax(np.abs(areas)))])
    return [p if (a < 0) == (np.sign(a) == island_sign) else p[::-1].copy() for p, a in zip(loops, areas)]


def noise_ref(frame, scale_units, seed, octaves=1, offset=(0.0, 0.0)):
    out = np.zeros((frame.H, frame.W))
    amp, total = 1.0, 0.0
    X, Y = np.meshgrid(frame.xs() + offset[0], frame.ys() + offset[1])
    for o in range(octaves):
        s = scale_units / (2 ** o)
        out += amp * ndimage.map_coordinates(_texture(seed + o * 7919), [Y / s, X / s], order=3,
                                             mode="grid-wrap", prefilter=False)
        total += amp
        amp *= 0.5
    return out / total


def blob_mask(rng, H, W):
    m = np.zeros((H, W), bool)
    for _ in range(rng.integers(1, 4)):
        r0, c0 = rng.integers(0, H), rng.integers(0, W)
        m[r0:r0 + rng.integers(1, 30), c0:c0 + rng.integers(1, 30)] = True
    return m & (rng.random((H, W)) < 0.95)


# ---------------------------------------------------------------- primitives

def test_winding_matches_reference():
    rng = np.random.default_rng(0)
    fr = Frame(0, 0, 100, 80, 1.0)
    for _ in range(500):
        loops = [rng.uniform(-30, 130, (rng.integers(0, 12), 2)) * rng.uniform(0.05, 1) + rng.uniform(-20, 100, 2)
                 for _ in range(rng.integers(0, 5))]
        ref = winding_ref(fr, loops)
        assert np.array_equal(fr.winding(loops), ref)
        lens = np.array([len(c) for c in loops], np.int64)  # zero-length entries included
        pts = np.concatenate(loops) if loops else np.zeros((0, 2))
        assert np.array_equal(fr.winding_packed(pts, lens), ref)


def test_packed_flattener_matches_flatten():
    rng = np.random.default_rng(1)
    for _ in range(500):
        flags = [rng.random(rng.integers(0, 9)) < rng.random() for _ in range(rng.integers(0, 5))]
        cs = [rng.uniform(-500, 500, (len(f), 2)).round() / 2 for f in flags]
        pf = PackedFlattener(flags)
        ref = [flatten(c, f) for c, f in zip(cs, flags)]
        assert [len(r) for r in ref] == pf.lengths.tolist()
        got = pf(cs)
        assert np.array_equal(got, np.concatenate(ref) if ref else np.zeros((0, 2)))


def test_trace_matches_reference():
    rng = np.random.default_rng(2)
    fr = Frame(-30, -50, 70, 40, 0.8)
    for i in range(150):
        m = blob_mask(rng, fr.H, fr.W)
        if i == 0:
            m[:] = True  # ink touching every frame edge
        for smooth, min_area in [(0.5, 0.0), (0.0, 0.0), (1.0, 20.0)]:
            ref, got = trace_ref(fr, m, smooth, min_area), fr.trace(m, smooth, min_area)
            assert len(ref) == len(got)
            assert all(np.array_equal(a, b) for a, b in zip(ref, got))


def test_separable_noise_matches_map_coordinates():
    fr = Frame(-100, -300, 900, 1200, 0.32)
    for scale, octaves, off in [(40.0, 2, (1234.5, 987.6)), (2.0, 3, (30900.1, 26900.7)), (300.0, 1, (0, 0))]:
        np.testing.assert_allclose(noise_field(fr, scale, 77, octaves, off), noise_ref(fr, scale, 77, octaves, off),
                                   rtol=0, atol=1e-12)


def test_cropped_distance_and_blur_are_exact():
    rng = np.random.default_rng(3)
    for i in range(150):
        H, W = rng.integers(5, 90, 2)
        m = blob_mask(rng, H, W)
        if i % 50 == 0:
            m[:] = True
        if i % 50 == 1:
            m[:] = False
        d = sdf(m)
        assert np.array_equal(d < 0, m) or not m.any() or m.all()
        for t in [-7.5, -1, -0.5, 0.0, 0.3, 1, 2.5, 13, 200]:
            assert np.array_equal(d < t, sdf_below(m, t))
        t = rng.normal(0, 4, m.shape)
        assert np.array_equal(d < t, sdf_below(m, t))
        assert np.array_equal(d < -np.abs(t), sdf_below(m, -np.abs(t)))
        if m.any() and not m.all():
            assert np.array_equal(edt_in(m), ndimage.distance_transform_edt(m))
            full = ndimage.distance_transform_edt(~m)
            near = full <= 6
            assert np.array_equal(edt_out(m, 6)[near], full[near]) and (edt_out(m, 6)[~near] > 6).all()
        m2 = blob_mask(rng, H, W)
        a, b = sdf_pair(m, m2)
        for w in [rng.random(m.shape), 0.0, 0.5, 1.0]:
            assert np.array_equal(((1 - w) * d + w * sdf(m2)) < 0, ((1 - w) * a + w * b) < 0)
        f = m * rng.random(m.shape)
        for s in [0.6, 2.3, 7.0, 19.0]:
            assert np.array_equal(blur(f, s), ndimage.gaussian_filter(f, s))


# ---------------------------------------------------------------- effects in context

def _full_frame(monkeypatch):
    """Swap the cropped helpers used by effects for their plain full-frame equivalents."""
    monkeypatch.setattr(fx, "blur", lambda a, s: ndimage.gaussian_filter(a, s))
    monkeypatch.setattr(fx, "sdf_below", lambda m, t: sdf(m) < t)
    monkeypatch.setattr(fx, "sdf_pair", lambda a, b: (sdf(a), sdf(b)))
    monkeypatch.setattr(fx, "noise_field", noise_ref)


CASES = [
    {"type": "grow", "distance": -25}, {"type": "grow", "distance": 80, "regions": 0.5},
    {"type": "grow", "distance": 0}, {"type": "round", "radius": 0}, {"type": "round", "radius": 40},
    {"type": "round", "radius": 30, "mode": "inner"}, {"type": "roughen", "amount": 30, "bias": -0.6},
    {"type": "roughen", "amount": 60, "bias": 1.0, "regions": 0.4}, {"type": "pits", "margin": 0},
    {"type": "ink", "radius": 120, "level": 0.1, "regions": 0.5}, {"type": "blur", "radius": 0},
    {"type": "smear", "angle": 30, "length": 300, "regions": 0.6}, {"type": "melt", "length": 1200},
    {"type": "spatter", "reach": 400}, {"type": "grain"}, {"type": "echo", "regions": 0.3},
]


@pytest.mark.parametrize("spec", CASES, ids=lambda s: "-".join(f"{v}" for v in s.values()))
def test_effects_identical_to_full_frame(spec, monkeypatch):
    font = load_font(FONT)
    info = font_info(font)
    name = font.getBestCmap()[ord("g")]
    src = glyph_loops(font.getGlyphSet(), name)
    em = info.upm / 1000
    fr = Frame(-40, info.descender - 40, font["hmtx"][name][0] + 40, info.ascender + 40, 320 / info.upm)
    ctx = Ctx(fr, info, name, font["hmtx"][name][0], seed_for(1, spec["type"]), em, seed_for(1, "stop"))
    st = GlyphState(fr, loops=src)
    got = apply_effect(apply_effect(st, {"type": "grow", "distance": 10}, ctx), spec, ctx).mask
    with monkeypatch.context() as mp:
        _full_frame(mp)
        ref = apply_effect(apply_effect(st, {"type": "grow", "distance": 10}, ctx), spec, ctx).mask
    assert np.array_equal(got, ref)
