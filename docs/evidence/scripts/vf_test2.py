"""VF test with glyf masters: convert CFF->quadratic once, then snap the SAME point list per master."""
import sys, os
from fontTools.ttLib import TTFont, newTable
from fontTools.subset import Subsetter, Options
from fontTools.pens.recordingPen import RecordingPen, RecordingPointPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPointPen, TTGlyphPen
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.designspaceLib import DesignSpaceDocument, AxisDescriptor, SourceDescriptor
from fontTools import varLib

src, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)

def base_quads():
    f = TTFont(src)
    o = Options(); o.notdef_outline = True; o.layout_features = ["*"]
    s = Subsetter(o); s.populate(text="HAMBURGEFONTSIVhamburgefontsiv"); s.subset(f)
    gs = f.getGlyphSet()
    rec = {}
    for n in f.getGlyphOrder():
        r = RecordingPen()
        gs[n].draw(Cu2QuPen(r, max_err=1.0, reverse_direction=True))
        rec[n] = r.value
    return f, rec

def make_master(grid, overlaps):
    f, rec = base_quads()
    sn = (lambda p: p) if grid == 1 else (lambda p: (round(p[0] / grid) * grid, round(p[1] / grid) * grid))
    glyf = newTable("glyf"); glyf.glyphOrder = f.getGlyphOrder(); glyf.glyphs = {}
    for n, ops in rec.items():
        pen = TTGlyphPen(None)
        for op, a in ops:
            if op in ("closePath", "endPath"): getattr(pen, op)()
            else: getattr(pen, op)(*[sn(x) for x in a])
        glyf[n] = pen.glyph()
    del f["CFF "]
    f["glyf"] = glyf; f["loca"] = newTable("loca")
    f.sfntVersion = "\x00\x01\x00\x00"
    for t in ("maxp",):
        f[t].tableVersion = 0x00010000
        for k in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
            setattr(f[t], k, 1 if k == "maxZones" else 0)
    f["post"].formatType = 2.0; f["post"].extraNames = []; f["post"].mapping = {}
    f["head"].glyphDataFormat = 0
    for n in f.getGlyphOrder():
        g = glyf[n]; g.recalcBounds(glyf)
        f["hmtx"][n] = (f["hmtx"][n][0], getattr(g, "xMin", 0))
    if overlaps:
        removeOverlaps(f, ignoreErrors=True)
    return f

def build(name, masters):
    ds = DesignSpaceDocument()
    ax = AxisDescriptor(); ax.tag = "DCAY"; ax.name = "Decay"; ax.minimum = 0; ax.default = 0; ax.maximum = 100
    ds.addAxis(ax)
    for i, (loc, font) in enumerate(masters):
        p = os.path.join(outdir, f"{name}_m{i}.ttf"); font.save(p)
        s = SourceDescriptor(); s.path = p; s.location = {"Decay": loc}; ds.addSource(s)
    try:
        vf, _, _ = varLib.build(ds)
        out = os.path.join(outdir, f"{name}-VF.ttf"); vf.save(out)
        print(name, "OK ->", out, "tables:", " ".join(sorted(vf.keys())))
    except Exception as e:
        print(name, "FAILED:", type(e).__name__, str(e)[:300])

build("glyfSnapOnly", [(0, make_master(1, False)), (100, make_master(50, False))])
build("glyfSnapPlusOverlap", [(0, make_master(1, False)), (100, make_master(50, True))])
