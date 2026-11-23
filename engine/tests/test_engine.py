from pathlib import Path

import numpy as np
import pytest
from fontTools.ttLib import TTFont

from fontmanip.api import make_font, to_bytes
from fontmanip.compat import align, compatibilise, resample
from fontmanip.effects import REGISTRY, effects_schema
from fontmanip.fontio import glyph_loops, is_island, load_font
from fontmanip.raster import Frame
from fontmanip.recipe import DEFAULT_RECIPE, Recipe, Stop

FONT = Path(__file__).parents[2] / "fonts" / "SourceSans3-Regular.otf"
TEXT = "eagsB8i%@ Ferro AV"


@pytest.fixture(scope="module")
def font():
    return load_font(FONT)


def square(x0, y0, s, cw=True):
    c = np.array([[x0, y0], [x0, y0 + s], [x0 + s, y0 + s], [x0 + s, y0]], float)
    return c if cw else c[::-1]


def test_winding_nonzero_matches_area():
    fr = Frame(0, 0, 100, 100, 1.0)
    w = fr.winding([square(10, 10, 50)])
    assert abs(np.count_nonzero(w) - 2500) < 120
    # hole with opposite orientation cancels
    w2 = fr.winding([square(10, 10, 50), square(20, 20, 20, cw=False)])
    assert abs(np.count_nonzero(w2) - (2500 - 400)) < 160


def test_trace_roundtrip_orientation():
    fr = Frame(0, 0, 100, 100, 1.0)
    mask = fr.fill([square(10, 10, 60), square(30, 30, 20, cw=False)])
    loops = fr.trace(mask, smooth=0)
    assert sorted(is_island(l) for l in loops) == [False, True]
    assert np.count_nonzero(fr.fill(loops) ^ mask) < 0.03 * mask.sum()


def test_align_recovers_shift():
    c = resample(np.array([[0, 0], [0, 10], [10, 10], [10, 0]], float), 40)
    assert np.allclose(align(c, np.roll(c, 7, axis=0)), c)


def test_compatibilise_topology_change():
    fr = Frame(-20, -20, 140, 140, 1.0)
    one = [square(0, 0, 100)]
    two = [square(0, 0, 40), square(60, 60, 40)]            # split
    hole = [square(0, 0, 100), square(30, 30, 40, cw=False)]  # counter appears
    stops = [one, two, hole]
    masks = [fr.fill(s) for s in stops]
    out, flags = compatibilise(fr, stops, masks, step=5)
    assert len(flags) == len(out[0])
    shapes = [[len(c) for c in m] for m in out]
    assert shapes[0] == shapes[1] == shapes[2]
    for m, t in zip(out, masks):  # each master reproduces its target
        assert np.count_nonzero(fr.fill(m) ^ t) < 0.05 * t.sum()


def test_glyph_loops_orientation(font):
    loops = glyph_loops(font.getGlyphSet(), font.getBestCmap()[ord("o")])
    assert sorted(is_island(l) for l in loops) == [False, True]


def test_effects_schema_and_all_effects_run(font):
    assert {e["name"] for e in effects_schema()} == set(REGISTRY)
    stops = [Stop(position=0, name="A")] + [
        Stop(position=i + 1, name=n, effects=[{"type": n}]) for i, n in enumerate(sorted(REGISTRY))]
    r = Recipe(stops=stops)
    out = make_font(font, r, text="oa", jobs=1)
    assert not out.report["missing"]


def test_default_recipe_builds_valid_variable_font(font, tmp_path):
    out = make_font(font, DEFAULT_RECIPE, text=TEXT, jobs=1)
    rep = out.report
    assert not rep["missing"], rep
    assert min(rep["fit_mean"]) > 0.97
    p = tmp_path / "t.woff2"
    p.write_bytes(to_bytes(out.font, "woff2"))
    f = TTFont(p)
    assert [a.axisTag for a in f["fvar"].axes] == ["DCAY"]
    assert [f["name"].getDebugName(i.subfamilyNameID) for i in f["fvar"].instances] == \
        [s.name for s in DEFAULT_RECIPE.stops]
    assert "GPOS" in f and "GSUB" in f and "STAT" in f
    src_cmap = font.getBestCmap()
    assert all(src_cmap[ord(c)] == f.getBestCmap()[ord(c)] for c in set(TEXT))
    # advances preserved (kerning stays valid)
    for g in f.getGlyphOrder():
        assert f["hmtx"][g][0] == font["hmtx"][g][0]
    assert f["name"].getDebugName(1) == DEFAULT_RECIPE.output.family


def test_recipe_validation():
    with pytest.raises(ValueError):
        Recipe(stops=[Stop(position=0, name="a")])
    with pytest.raises(ValueError):
        Recipe(stops=[Stop(position=10, name="a"), Stop(position=0, name="b")])
    with pytest.raises(ValueError):
        Stop(position=0, name="a", effects=[{"type": "nope"}])


def test_schema_kinds():
    for e in effects_schema():
        for p in e["params"]:
            if p["kind"] == "choice":
                assert isinstance(p["options"], list) and p["default"] in p["options"], (e["name"], p)
            else:
                assert "options" not in p, (e["name"], p)


def test_corners_survive_resampling():
    from fontmanip.compat import resample_corners
    pts, flags = resample_corners(square(0, 0, 100), 20, unit=1.0)
    corners = {tuple(np.round(p).astype(int)) for p in pts[flags]}
    assert corners == {(0, 0), (0, 100), (100, 100), (100, 0)}


def test_source_stop_is_faithful(font):
    """DCAY 0 (smooth outline mode, default) must closely match the original, incl. corners."""
    from fontmanip.pipeline import process_glyph
    cm = font.getBestCmap()
    for ch in "EHkxog&":
        r = process_glyph(font, DEFAULT_RECIPE, cm[ord(ch)])
        assert r.fit[0] > 0.985, (ch, r.fit[0])


def test_previous_stop_without_effects_uses_previous_shape(font):
    from fontmanip.pipeline import process_glyph
    r = Recipe(stops=[Stop(position=0, name="a"),
                      Stop(position=50, name="px", effects=[{"type": "pixelate", "columns": 6, "rows": 3}]),
                      Stop(position=100, name="same", base="previous")])
    res = process_glyph(font, r, font.getBestCmap()[ord("o")])
    assert res.fit[2] > 0.97  # target is the pixel shape, not the source


def test_licence_names_and_fstype_preserved(font):
    out = make_font(font, DEFAULT_RECIPE, text="ab", jobs=1)
    for nid in (0, 13, 14):
        src = font["name"].getDebugName(nid)
        if src:
            assert out.font["name"].getDebugName(nid) == src
    assert out.font["OS/2"].fsType == font["OS/2"].fsType
    assert out.report["morph_mean"] and len(out.report["morph_mean"]) == len(DEFAULT_RECIPE.stops) - 1


def test_quadratic_variable_input_with_offcurve_only_contours():
    """glyf VF input (UPM 2000), pinned; dots are all-off-curve contours after a save/reload."""
    from fontmanip.fontio import font_to_bytes
    from fontmanip.pipeline import process_glyph
    p = FONT.parent / "Fraunces-Variable.ttf"
    f = load_font(font_to_bytes(load_font(p, {"opsz": 144, "wght": 400})))
    assert "fvar" not in f and f["head"].unitsPerEm == 2000
    for g in ("period", "dieresis", "o"):
        r = process_glyph(f, DEFAULT_RECIPE, g)
        assert min(r.fit) > 0.94, (g, r.fit)


def test_nested_shapes_survive_splitting():
    """® = ring around an R: split pieces may contain holes; DCAY 0 must stay faithful."""
    from fontmanip.pipeline import process_glyph
    f = load_font(FONT.parent / "Fraunces-Variable.ttf", {"opsz": 144, "wght": 400})
    r = process_glyph(f, DEFAULT_RECIPE, "registered")
    assert r.fit[0] > 0.95, r.fit


def test_axis_default_and_base_transform(font):
    import json
    r = Recipe.model_validate(json.loads((Path(__file__).parents[1] / "recipes" / "motion-smear.json").read_text()))
    out = make_font(font, r, text="To", jobs=1)
    ax = out.font["fvar"].axes[0]
    assert ax.defaultValue == 0 and ax.minValue == r.stops[0].position
    g = font.getBestCmap()[ord("T")]
    assert out.font["hmtx"][g][0] != font["hmtx"][g][0]  # base width scales advances
    assert not out.report["missing"]


@pytest.mark.parametrize("preset", sorted((Path(__file__).parents[1] / "recipes").glob("*.json")),
                         ids=lambda p: p.stem)
def test_presets_build(font, preset):
    r = Recipe.load(preset)
    out = make_font(font, r, text="ag", jobs=1)
    assert not out.report["missing"]
    assert min(out.report["fit_mean"]) > 0.9


def test_svg_export_matches_axis_and_shaping(font):
    import re
    from fontmanip.svgexport import render_svg
    out = make_font(font, DEFAULT_RECIPE, text="AV To", jobs=1)
    raw = to_bytes(out.font)
    svg0 = render_svg(raw, ["AV To"], [("0", {"DCAY": 0})])
    svg100 = render_svg(raw, ["AV To", "AV"], [("a", {"DCAY": 100}), ("b", {"DCAY": 50})], label=True)
    assert svg0.startswith("<svg") and svg0.count("<path") == 4          # space has no outline
    assert svg100.count("<path") == 12 and svg100.count("<text") == 2     # 2 rows x (4 + 2) glyphs
    d0 = re.findall(r' d="([^"]+)"', svg0)[0]
    d100 = re.findall(r' d="([^"]+)"', svg100)[0]
    assert d0 != d100                                                   # outlines follow the axis
    # kerning applied: "AV" pair is tighter than the sum of advances
    xs = [float(m) for m in re.findall(r'translate\(([-\d.]+) ', svg0)]
    adv_a = font["hmtx"][font.getBestCmap()[ord("A")]][0]
    assert xs[1] - xs[0] < adv_a


def test_frame_margin_covers_effect_reach(font):
    """Long smears must not be clipped by the raster frame edge."""
    from fontmanip.pipeline import process_glyph
    r = Recipe(stops=[Stop(position=0, name="a"),
                      Stop(position=100, name="b", effects=[
                          {"type": "smear", "length": 400, "direction": "both", "variation": 0, "level": 0.3}])])
    g = process_glyph(font, r, font.getBestCmap()[ord("o")])
    xs = np.concatenate([c[:, 0] for c in g.masters[1]])
    adv = font["hmtx"][font.getBestCmap()[ord("o")]][0]
    assert xs.min() < -250 and xs.max() > adv + 250   # streaks extend well past the old 150u margin
    assert g.fit[1] > 0.97
