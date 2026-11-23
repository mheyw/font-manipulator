import sys
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
for path in sys.argv[1:]:
    gs=TTFont(path).getGlyphSet(); out=[]
    for g in ['o','x','b','h','l','p','H','O']:
        p=RecordingPen(); gs[g].draw(p)
        ys=sorted({round(a[-1][1]) for op,a in p.value if a}); xs=sorted({round(a[-1][0]) for op,a in p.value if a})
        out.append(f"{g} w={gs[g].width} y={ys} x={xs}")
    print('==',path.split('/')[-1]); print('\n'.join(out))
