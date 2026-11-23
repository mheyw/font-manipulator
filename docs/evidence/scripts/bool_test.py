import time, sys
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.pens.recordingPen import RecordingPen
from booleanOperations.booleanGlyph import BooleanGlyph
f = TTFont(sys.argv[1]); gs = f.getGlyphSet()
names = [n for n in f.getGlyphOrder()][:200]
t = time.time(); changed = 0; fail = 0
for n in names:
    bg = BooleanGlyph()
    gs[n].draw(bg.getPen())
    try:
        res = bg.removeOverlap()
    except Exception as e:
        fail += 1; continue
    r = RecordingPen(); res.drawPoints(PointToSegmentPen(r))
    changed += 1
print("booleanOperations removeOverlap on", len(names), "glyphs:", round(time.time() - t, 2), "s; ok", changed, "fail", fail)
