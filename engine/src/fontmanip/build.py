"""Masters -> variable TrueType font (+ WOFF2), preserving cmap, metrics and layout tables."""
from __future__ import annotations

import copy
import io
import logging
import re

import numpy as np
from fontTools import varLib
from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, InstanceDescriptor, SourceDescriptor
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.builder import buildStatTable
from fontTools.pens.ttGlyphPen import TTGlyphPointPen
from fontTools.ttLib import TTFont
from array import array

from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates, flagOverlapSimple

from .pipeline import GlyphResult
from .recipe import Recipe

LAYOUT_TABLES = ("GDEF", "GSUB", "GPOS")


class BuildError(RuntimeError):
    pass


def _ps(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s)[:40] or "FontManip"


def _empty_glyph() -> Glyph:
    g = Glyph()
    g.numberOfContours = 0
    return g


def _glyph(contours: list[np.ndarray], oncurve: list[np.ndarray]) -> Glyph:
    """Build a simple TrueType glyph straight from point arrays (no per-point pen calls)."""
    pts = np.concatenate(contours).astype(np.int64)
    on = np.concatenate(oncurve).astype(np.uint8)
    g = Glyph()
    g.numberOfContours = len(contours)
    g.coordinates = GlyphCoordinates(pts.tolist())
    g.flags = array("B", on.tolist())
    g.flags[0] |= flagOverlapSimple  # contours overlap by design
    g.endPtsOfContours = (np.cumsum([len(c) for c in contours]) - 1).tolist()
    g.program = ttProgram.Program()
    g.program.fromBytecode(b"")
    return g


def _master(source: TTFont, order: list[str], results: dict[str, GlyphResult], k: int,
            family: str, style: str) -> TTFont:
    upm = source["head"].unitsPerEm
    fb = FontBuilder(upm, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(source.getBestCmap())
    glyphs = {}
    for name in order:
        r = results.get(name)
        glyphs[name] = _glyph(r.masters[k], r.oncurve) if r and r.masters[k] else _empty_glyph()
    fb.setupGlyf(glyphs)
    glyf = fb.font["glyf"]
    hm = source["hmtx"]
    metrics = {}
    for name in order:
        g = glyf[name]
        g.recalcBounds(glyf)
        r = results.get(name)
        metrics[name] = (r.advance if r else hm[name][0], getattr(g, "xMin", 0))
    fb.setupHorizontalMetrics(metrics)
    hh = source["hhea"]
    fb.setupHorizontalHeader(ascent=hh.ascent, descent=hh.descent, lineGap=hh.lineGap)
    fb.setupNameTable({"familyName": family, "styleName": style})
    o = source["OS/2"]
    fb.setupOS2(sTypoAscender=o.sTypoAscender, sTypoDescender=o.sTypoDescender,
                sTypoLineGap=o.sTypoLineGap, usWinAscent=o.usWinAscent, usWinDescent=o.usWinDescent,
                sxHeight=getattr(o, "sxHeight", 0), sCapHeight=getattr(o, "sCapHeight", 0),
                usWeightClass=o.usWeightClass)
    fb.setupPost(keepGlyphNames=True)
    return fb.font


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.msgs: list[str] = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


def build_variable_font(source: TTFont, recipe: Recipe, results: dict[str, GlyphResult],
                        glyph_order: list[str] | None = None, copy_layout: bool = True) -> TTFont:
    order = glyph_order or source.getGlyphOrder()
    fam = recipe.output.family
    ax = recipe.axis
    positions = [s.position for s in recipe.stops]

    ds = DesignSpaceDocument()
    a = AxisDescriptor()
    a.tag, a.name = ax.tag, ax.name
    di = recipe.default_index()
    a.minimum, a.default, a.maximum = positions[0], positions[di], positions[-1]
    ds.addAxis(a)
    for k, stop in enumerate(recipe.stops):
        s = SourceDescriptor()
        s.font = _master(source, order, results, k, fam, stop.name)
        s.location = {ax.name: stop.position}
        s.name = f"master{k}"
        ds.addSource(s)
        inst = InstanceDescriptor()
        inst.familyName, inst.styleName = fam, stop.name
        inst.location = {ax.name: stop.position}
        inst.postScriptFontName = f"{_ps(fam)}-{_ps(stop.name)}"
        ds.addInstance(inst)

    cap = _Capture()
    lg = logging.getLogger("fontTools.varLib")
    lg.addHandler(cap)
    try:
        vf, _, _ = varLib.build(ds)
    finally:
        lg.removeHandler(cap)
    bad = [m for m in cap.msgs if "incompatible" in m]
    if bad:
        raise BuildError(f"{len(bad)} glyph(s) not interpolatable, e.g. {bad[:3]}")

    if copy_layout:
        for tag in LAYOUT_TABLES:
            if tag in source:
                vf[tag] = copy.deepcopy(source[tag])

    # OS/2: start from source (panose, ranges, vendor), widen win metrics to fit blobs
    os2 = copy.deepcopy(source["OS/2"])
    ys = [c[:, 1] for r in results.values() for m in r.masters for c in m if len(c)]
    if ys:
        os2.usWinAscent = max(os2.usWinAscent, int(max(y.max() for y in ys)))
        os2.usWinDescent = max(os2.usWinDescent, int(-min(y.min() for y in ys)))
    vf["OS/2"] = os2
    vf["OS/2"].recalcAvgCharWidth(vf)
    if "post" in source:
        vf["post"].italicAngle = source["post"].italicAngle

    # names + STAT
    name = vf["name"]
    name.removeNames(platformID=1)  # Mac records from FontBuilder would contradict the Windows ones
    ps = _ps(fam)
    default_style = recipe.stops[di].name
    for nid, val in {1: fam, 2: "Regular", 3: f"FontManip;{ps};{recipe.seed}", 4: f"{fam} {default_style}",
                     6: f"{ps}-{_ps(default_style)}", 16: fam, 17: default_style, 25: ps}.items():
        name.setName(val, nid, 3, 1, 0x409)
    for inst, stop in zip(vf["fvar"].instances, recipe.stops):
        if inst.subfamilyNameID in (2, 17):
            inst.subfamilyNameID = name.addMultilingualName({"en": stop.name}, vf, mac=False, minNameID=256)
    src_name = source["name"]
    for rec in src_name.names:  # copyright, trademark, manufacturer, designer, licence, URLs
        if rec.nameID in (0, 7, 8, 9, 10, 11, 12, 13, 14):
            name.setName(rec.toUnicode(), rec.nameID, rec.platformID, rec.platEncID, rec.langID)
    name.setName("Version 1.000; generated with font-manipulator from: " +
                 (src_name.getDebugName(4) or "unknown source"), 5, 3, 1, 0x409)
    buildStatTable(vf, [{"tag": ax.tag, "name": ax.name, "values": [
        {"value": s.position, "name": s.name, **({"flags": 0x2} if i == di else {})}
        for i, s in enumerate(recipe.stops)]}], elidedFallbackName=default_style)
    return vf


def to_bytes(font: TTFont, flavor: str | None = None) -> bytes:
    f = font
    if flavor:
        f = copy.deepcopy(font)
        f.flavor = flavor
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


def varying_glyph_report(vf: TTFont, results: dict[str, GlyphResult]) -> dict:
    gvar = vf["gvar"].variations if "gvar" in vf else {}
    changed = [n for n, r in results.items()
               if r.masters and any(len(m) for m in r.masters)
               and any(not all(np.array_equal(a, b) for a, b in zip(r.masters[0], m)) for m in r.masters[1:])]
    missing = [n for n in changed if not gvar.get(n)]
    return {"glyphs_changed": len(changed), "glyphs_in_gvar": len(gvar), "missing": missing}
