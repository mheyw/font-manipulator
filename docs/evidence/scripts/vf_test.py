"""Build a VF with custom axis DCAY from algorithmic masters; test compatibility."""
import sys, os
from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.designspaceLib import DesignSpaceDocument, AxisDescriptor, SourceDescriptor
from fontTools import varLib

src, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)

def base():
    f = TTFont(src)
    o = Options(); o.layout_features = ["*"]; o.notdef_outline = True
    s = Subsetter(o); s.populate(text="HAMBURGEFONTSIVhamburgefontsiv"); s.subset(f)
    return f

def snapped(grid, overlaps):
    f = base()
    cs = f["CFF "].cff.topDictIndex[0].CharStrings
    gs = f.getGlyphSet()
    sn = (lambda p: p) if grid == 1 else (lambda p: (round(p[0] / grid) * grid, round(p[1] / grid) * grid))
    for n in f.getGlyphOrder():
        r = RecordingPen(); gs[n].draw(r)
        pen = T2CharStringPen(f["hmtx"][n][0], gs)
        for op, a in r.value:
            if op in ("closePath", "endPath"): getattr(pen, op)()
            else: getattr(pen, op)(*[sn(x) for x in a])
        cs[n] = pen.getCharString(private=cs[n].private, globalSubrs=cs[n].globalSubrs, optimize=False)
    if overlaps:
        removeOverlaps(f, ignoreErrors=True)
    return f

def build(name, masters):
    ds = DesignSpaceDocument()
    ax = AxisDescriptor(); ax.tag = "DCAY"; ax.name = "Decay"; ax.minimum = 0; ax.default = 0; ax.maximum = 100
    ds.addAxis(ax)
    for i, (loc, font) in enumerate(masters):
        p = os.path.join(outdir, f"{name}_m{i}.otf"); font.save(p)
        s = SourceDescriptor(); s.path = p; s.location = {"Decay": loc}; ds.addSource(s)
    try:
        vf, _, _ = varLib.build(ds)
        out = os.path.join(outdir, f"{name}-VF.otf"); vf.save(out)
        print(name, "OK ->", out, "tables:", sorted(vf.keys()))
    except Exception as e:
        print(name, "FAILED:", type(e).__name__, str(e)[:400])

build("snapOnly", [(0, snapped(1, False)), (100, snapped(50, False))])
build("snapPlusOverlap", [(0, snapped(1, False)), (100, snapped(50, True))])
