import sys
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen, DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen

src, out = sys.argv[1], sys.argv[2]
f = TTFont(src); gs = f.getGlyphSet(); cmap = f.getBestCmap()
os2 = f['OS/2']; xh = os2.sxHeight; capH = os2.sCapHeight
text = sys.argv[3] if len(sys.argv)>3 else "Ferro"

def bez(pts, t):
    while len(pts) > 1:
        pts = [((1-t)*a[0]+t*b[0], (1-t)*a[1]+t*b[1]) for a,b in zip(pts, pts[1:])]
    return pts[0]

def contours(glyph):
    rp = DecomposingRecordingPen(gs); glyph.draw(rp)
    cs=[]; cur=None; start=None
    for op,args in rp.value:
        if op=='moveTo': cur=[]; start=args[0]; last=start
        elif op in('lineTo','curveTo','qCurveTo'):
            ctrl=[last]+list(args); cur.append(ctrl); last=args[-1]
        elif op in('closePath','endPath'):
            if last!=start: cur.append([last,start])
            cs.append(cur)
    return cs

def magnet(glyph, nx, rows_per_xh, samples=80):
    adv = glyph.width; cw = adv/nx; rh = xh/rows_per_xh
    def node(p):
        i = int(p[0]//cw); x = (i+.5)*cw            # columns: cell centres of advance/N
        y = round(p[1]/rh)*rh                       # rows: lines anchored on baseline/x-height
        return (round(x), round(y))
    res=[]
    for c in contours(glyph):
        pts=[]; seen=set()
        for seg in c:
            for k in range(samples):
                q=node(bez(seg,k/(samples-1)))
                if q not in seen: seen.add(q); pts.append(q)
        if len(pts)>=3: res.append(pts)
    return res

stages=[None,(15,6),(10,4),(5,2)]
H=f['head'].unitsPerEm*1.15; rows=[]; W=0
for si,st in enumerate(stages):
    x=0; parts=[]
    for ch in text:
        if ch==' ': x+=gs[cmap[32]].width; continue
        g=gs[cmap[ord(ch)]]
        if st is None:
            sp=SVGPathPen(gs); g.draw(sp); d=sp.getCommands()
        else:
            d=" ".join("M"+" L".join(f"{a} {b}" for a,b in poly)+"Z" for poly in magnet(g,*st))
        parts.append(f'<path transform="translate({x},0)" d="{d}"/>'); x+=g.width
    W=max(W,x); rows.append((si,st,parts))
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W+200} {H*len(rows)}" width="1400" height="{1400*H*len(rows)/(W+200):.0f}">','<rect width="100%" height="100%" fill="#fff"/>']
for si,st,parts in rows:
    svg.append(f'<g transform="translate(100,{H*si+H*0.78}) scale(1,-1)" fill="#111" fill-rule="nonzero">'+"".join(parts)+'</g>')
    lab="source" if st is None else f"stage {si+1}: cols=advance/{st[0]}, rows=x-height/{st[1]}"
    svg.append(f'<text x="100" y="{H*si+H*0.97}" font-size="70" font-family="monospace" fill="#c00">{lab}</text>')
svg.append('</svg>'); open(out,'w').write("\n".join(svg)); print("ok")
