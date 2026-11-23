import sys, math, collections
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
for path in sys.argv[1:]:
    f=TTFont(path); gs=f.getGlyphSet(); cff=f['CFF '].cff
    print('==',path, 'glyphs',len(f.getGlyphOrder()),'upm',f['head'].unitsPerEm, 'fvar' in f)
    tot=collections.Counter(); angles=collections.Counter(); xs=collections.Counter(); ys=collections.Counter()
    for g in ['o','n','a','e','s','H']:
        p=RecordingPen(); gs[g].draw(p)
        ops=collections.Counter(op for op,_ in p.value)
        pts=[a[-1] for op,a in p.value if a]
        print(g, dict(ops), 'width',gs[g].width)
        if g in('o','a'): print('  pts',[tuple(map(round,pt)) for pt in pts][:60])
    # global stats on lowercase
    for g in 'abcdefghijklmnopqrstuvwxyz':
        p=RecordingPen(); gs[g].draw(p)
        prev=None
        for op,a in p.value:
            if op=='moveTo': prev=a[0]; start=a[0]
            elif op in('lineTo','curveTo','qCurveTo'):
                tot[op]+=1; pt=a[-1]
                if op=='lineTo' and prev:
                    ang=round(math.degrees(math.atan2(pt[1]-prev[1],pt[0]-prev[0])))%180
                    angles[(ang//15)*15]+=1
                xs[round(pt[0])]+=1; ys[round(pt[1])]+=1
                prev=pt
    print(' segs',dict(tot)); print(' lineTo angle hist (15deg bins)',sorted(angles.items()))
    print(' distinct x',len(xs),'distinct y',len(ys),'top y',ys.most_common(8))
