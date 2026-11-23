"""Test: grid-snap every CFF glyph, remove overlaps, rename family, save OTF + WOFF2."""
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib.removeOverlaps import removeOverlaps

src, out = sys.argv[1], sys.argv[2]
GRID = 50

font = TTFont(src)
cff = font["CFF "].cff
top = cff.topDictIndex[0]
cs = top.CharStrings
gs = font.getGlyphSet()
hmtx = font["hmtx"]
print('before O:', __import__('fontTools.pens.recordingPen',fromlist=['x']).RecordingPen and None)

def snap(p):
    return (round(p[0] / GRID) * GRID, round(p[1] / GRID) * GRID)

for name in font.getGlyphOrder():
    rec = RecordingPen()
    gs[name].draw(rec)
    width = hmtx[name][0]
    priv = cs[name].private
    cffw = None if width == priv.defaultWidthX else width - priv.nominalWidthX
    pen = T2CharStringPen(cffw, gs)
    for op, args in rec.value:
        if op == "closePath" or op == "endPath":
            getattr(pen, op)()
        elif op == "addComponent":
            pen.addComponent(*args)
        else:
            getattr(pen, op)(*[snap(a) for a in args])
    new = pen.getCharString(private=cs[name].private, globalSubrs=cs[name].globalSubrs)
    cs[name] = new  # replaces charstring in CharStrings
    b = BoundsPen(gs)
    new.draw(b)
    lsb = b.bounds[0] if b.bounds else 0
    hmtx[name] = (width, round(lsb))

# overlap removal also drops hinting and unused subrs
removeOverlaps(font, ignoreErrors=True)

# rename family (nameIDs 1,4,6,16; also CFF FontName/FullName/FamilyName)
newFam = "SnapTest"
name = font["name"]
for rec in name.names:
    if rec.nameID in (1, 16):
        rec.string = newFam
    elif rec.nameID == 4:
        rec.string = newFam + " Regular"
    elif rec.nameID == 6:
        rec.string = newFam + "-Regular"
    elif rec.nameID == 3:
        rec.string = "1.000;NONE;" + newFam + "-Regular"
cff.fontNames = [newFam + "-Regular"]
top.FullName = newFam + " Regular"
top.FamilyName = newFam

font.save(out)  # recalcBBoxes default True -> head bbox, maxp etc.
font.flavor = "woff2"
font.save(out.replace(".otf", ".woff2"))
print("ok")
