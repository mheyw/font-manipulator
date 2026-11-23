import sys, collections
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
path=sys.argv[1]; N=int(sys.argv[2])
f=TTFont(path); gs=f.getGlyphSet()
ok=bad=0; badex=[]; ys=collections.Counter()
for g in list('abcdefghijklmnopqrstuvwxyzHOAVWXKRS')+['zero','one','eight']:
    if g not in gs: continue
    p=RecordingPen(); gs[g].draw(p); w=gs[g].width; step=w/N
    for op,a in p.value:
        if not a: continue
        x,y=a[-1]; ys[y]+=1
        r=(x/step-0.5)%1
        if min(r,1-r)<0.06: ok+=1
        else: bad+=1; badex.append((g,x,round(x/step,2)))
print(path,'N',N,'on-center',ok,'off',bad,badex[:12])
print(' y values',sorted(ys.items()))
