import sys, math
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.basePen import BasePen

src, out = sys.argv[1], sys.argv[2]
f = TTFont(src); gs = f.getGlyphSet(); cmap = f.getBestCmap()
text = sys.argv[3] if len(sys.argv)>3 else "Ferro"
upm = f['head'].unitsPerEm

def snap_pt(p, g, ang, strength):
    # rotate into grid space, snap, rotate back; blend by strength ("magnet")
    c, s = math.cos(ang), math.sin(ang)
    x, y = p; u, v = x*c + y*s, -x*s + y*c
    su, sv = round(u/g)*g, round(v/g)*g
    sx, sy = su*c - sv*s, su*s + sv*c
    return (x + (sx-x)*strength, y + (sy-y)*strength)

class SnapPen(BasePen):
    def __init__(self, other, g, ang, strength):
        super().__init__(None); self.o=other; self.g=g; self.a=ang; self.s=strength
    def S(self,p): return snap_pt(p,self.g,self.a,self.s)
    def _moveTo(self,p): self.o.moveTo(self.S(p))
    def _lineTo(self,p): self.o.lineTo(self.S(p))
    def _curveToOne(self,a,b,c): self.o.curveTo(self.S(a),self.S(b),self.S(c))
    def _closePath(self): self.o.closePath()

stages = [(16,0.0,1.0),(48,0.12,1.0),(110,0.3,1.0),(220,0.5,1.0)]
rows=[]; W=0
for i,(g,ang,st) in enumerate(stages):
    x=0; paths=[]
    for ch in text:
        gn = cmap[ord(ch)]; glyph = gs[gn]
        sp = SVGPathPen(gs); glyph.draw(SnapPen(sp,g,ang,st))
        paths.append(f'<path transform="translate({x},0)" d="{sp.getCommands()}"/>')
        x += glyph.width
    W=max(W,x); rows.append((i,g,ang,paths))
H=upm*1.1
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W+200} {H*len(rows)}" width="1400" height="{1400*H*len(rows)/(W+200):.0f}">','<rect width="100%" height="100%" fill="#fff"/>']
for i,g,ang,paths in rows:
    svg.append(f'<g transform="translate(100,{H*i+H*0.8}) scale(1,-1)" fill="#111">'+"".join(paths)+'</g>')
    svg.append(f'<text x="100" y="{H*i+H*0.98}" font-size="60" font-family="monospace" fill="#c00">stage {i+1}: grid={g}u angle={ang}rad</text>')
svg.append('</svg>'); open(out,'w').write("\n".join(svg)); print("ok",out)
