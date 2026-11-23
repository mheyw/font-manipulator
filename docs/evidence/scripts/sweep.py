import sys
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
gs=TTFont(sys.argv[1]).getGlyphSet()
pts=[]
for g in 'abcdeghkmnopqrsuvwxyz':
    p=RecordingPen(); gs[g].draw(p); w=gs[g].width
    pts+= [(a[-1][0],w) for op,a in p.value if a]
res=[]
for N in range(3,31):
    ok=sum(1 for x,w in pts if min(((x/(w/N))-0.5)%1, 1-((x/(w/N))-0.5)%1)<0.06)
    res.append((N,round(ok/len(pts),2)))
print(sys.argv[1].split('/')[-1], res)
