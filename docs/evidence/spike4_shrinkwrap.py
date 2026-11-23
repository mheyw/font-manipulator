"""Go/no-go: shrink-wrap (SDF projection) + collapse rule; measure masters & in-betweens."""
import sys, time, hashlib, numpy as np
from scipy import ndimage
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPointPen
from fontTools.fontBuilder import FontBuilder
from fontTools.designspaceLib import DesignSpaceDocument, AxisDescriptor, SourceDescriptor
from fontTools import varLib

SRC, OUT = sys.argv[1], sys.argv[2]
TEXT = "eagsB8i%@Ferro"
STEP = 12; S = 0.3; PAD = 120  # px per unit; padding in units
src = TTFont(SRC); gs = src.getGlyphSet(); cmap = src.getBestCmap()
upm = src['head'].unitsPerEm; os2 = src['OS/2']; xh = os2.sxHeight
Y0, Y1 = -0.4*upm, 1.15*upm
names = ['.notdef'] + list(dict.fromkeys(cmap[ord(c)] for c in TEXT))

# ---------- skeleton ----------
def bez(pts, t):
    pts = np.asarray(pts, float)
    while len(pts) > 1: pts = (1-t)*pts[:-1] + t*pts[1:]
    return pts[0]
def skeleton(glyph):
    rp = DecomposingRecordingPen(gs); glyph.draw(rp); out=[]; segs=[]; last=start=None
    for op,a in rp.value:
        if op=='moveTo': segs=[]; start=last=a[0]
        elif op=='lineTo': segs.append([last,a[0]]); last=a[0]
        elif op=='curveTo': segs.append([last,*a]); last=a[-1]
        elif op=='qCurveTo':
            pts=[last,*a]
            for i in range(1,len(pts)-1):
                p0=pts[0] if i==1 else tuple(np.add(pts[i-1],pts[i])/2)
                p2=pts[-1] if i==len(pts)-2 else tuple(np.add(pts[i],pts[i+1])/2)
                segs.append([p0,pts[i],p2])
            last=a[-1]
        elif op in('closePath','endPath'):
            if last!=start: segs.append([last,start])
            dense=np.array([bez(s,t) for s in segs for t in np.linspace(0,1,40,endpoint=False)])
            closed=np.vstack([dense,dense[:1]])
            d=np.r_[0,np.cumsum(np.hypot(*np.diff(closed,axis=0).T))]
            K=max(16,int(d[-1]/STEP)); ts=np.linspace(0,d[-1],K,endpoint=False)
            out.append(np.c_[np.interp(ts,d,closed[:,0]),np.interp(ts,d,closed[:,1])])
    return out

# ---------- raster frame + exact nonzero winding raster ----------
class Frame:
    def __init__(s, adv):
        s.x0=-PAD; s.W=int((adv+2*PAD)*S); s.H=int((Y1-Y0)*S)
    def to_px(s,p): return (p[:,0]-s.x0)*S-0.5, (p[:,1]-Y0)*S-0.5   # (col,row) continuous
    def winding(s, contours):
        acc=np.zeros((s.H,s.W+1))
        for c in contours:
            cx,cy=s.to_px(c); nx,ny=np.roll(cx,-1),np.roll(cy,-1)
            for x0,y0,x1,y1 in zip(cx,cy,nx,ny):
                if y0==y1: continue
                d=1 if y1>y0 else -1
                lo,hi=(y0,y1) if y0<y1 else (y1,y0)
                rows=np.arange(int(np.ceil(lo)), int(np.ceil(hi)))
                rows=rows[(rows>=0)&(rows<s.H)]
                if not len(rows): continue
                xi=x0+(rows-y0)*(x1-x0)/(y1-y0)
                cols=np.clip(np.ceil(xi),0,s.W).astype(int)
                np.add.at(acc,(rows,np.zeros_like(rows)),d); np.add.at(acc,(rows,cols),-d)
        return np.cumsum(acc,axis=1)[:,:s.W]
    def fill(s, contours): return s.winding(contours)!=0

# ---------- world-space noise texture (fast, coherent across glyphs) ----------
_noise={}
def noise_at(fr, freq, seed):
    key=(freq,seed)
    if key not in _noise:
        rng=np.random.default_rng(seed); g=rng.standard_normal((64,64))
        _noise[key]=g/np.abs(g).max()
    g=_noise[key]
    xs=(np.arange(fr.W)/S+fr.x0)/freq; ys=(np.arange(fr.H)/S+Y0)/freq
    X,Y=np.meshgrid(xs%60,ys%60)
    return ndimage.map_coordinates(g,[Y,X],order=3,mode='wrap')

# ---------- targets (binary masks) ----------
def t_blob(fr, mask, blur, thresh, amp=0.0, freq=80, seed=1):
    f=ndimage.gaussian_filter(mask.astype(float), blur*S)
    if amp: f=f+amp*noise_at(fr,freq,seed)
    return f>thresh
def t_pixels(fr, mask, adv, nx, rows):
    cw=adv/nx; rh=xh/rows; out=np.zeros_like(mask)
    xs=(np.arange(fr.W)+0.5)/S+fr.x0; ys=(np.arange(fr.H)+0.5)/S+Y0
    ci=np.floor(xs/cw).astype(int); ri=np.floor(ys/rh).astype(int)
    # coverage per cell
    R,C=np.meshgrid(ri,ci,indexing='ij'); key=(R-R.min())*10000+(C-C.min())
    cov=ndimage.mean(mask.astype(float),labels=key,index=np.unique(key))
    lut=dict(zip(np.unique(key),cov))
    return np.vectorize(lut.get)(key)>0.5

# ---------- shrink-wrap with collapse rule ----------
def sdf(m):
    return ndimage.distance_transform_edt(~m)-ndimage.distance_transform_edt(m)  # +outside, -inside (px)
def sample(a, fr, p):
    cx,cy=fr.to_px(p); return ndimage.map_coordinates(a,[cy,cx],order=1,mode='nearest')
def shrinkwrap(fr, contours, target, src_contours):
    lab_fill,_=ndimage.label(target); lab_hole,_=ndimage.label(~target)
    out=[]; used_holes=set()
    for i,(c,c0) in enumerate(zip(contours,src_contours)):
        own=fr.winding([c0]); outer=(own>0).sum()>=(own<0).sum()   # orientation via own winding
        region=own!=0
        if outer:
            labs=lab_fill[region & target]; 
        else:
            labs=lab_hole[region & ~target]
        if len(labs)==0 or len(labs) < 0.12*region.sum():
            out.append(np.repeat(c.mean(0,keepdims=True),len(c),0)); continue   # collapse
        L=np.bincount(labs).argmax()
        if not outer:
            if L in used_holes: out.append(np.repeat(c.mean(0,keepdims=True),len(c),0)); continue
            used_holes.add(L)
        comp=(lab_fill==L) if outer else ~(lab_hole==L)
        d=sdf(comp); gy,gx=np.gradient(d)
        p=c.copy()
        for it in range(4):
            for _ in range(6):
                v=sample(d,fr,p); dx=sample(gx,fr,p); dy=sample(gy,fr,p)
                n=np.hypot(dx,dy)+1e-9
                p[:,0]-=v*dx/n/S; p[:,1]-=v*dy/n/S
            if it<3:   # tangential relax to untangle & even spacing
                p=0.5*p+0.25*(np.roll(p,1,0)+np.roll(p,-1,0))
        out.append(p)
    return out

# ---------- masters ----------
STOPS=[0,33,66,100]
masters={k:{} for k in STOPS}; targets={k:{} for k in STOPS}; adv={}; frames={}
T=time.time()
for n in names:
    g=gs[n]; adv[n]=g.width; fr=Frame(g.width); frames[n]=fr
    sk=skeleton(g); m0=fr.fill(sk) if sk else np.zeros((fr.H,fr.W),bool)
    masters[0][n]=sk; targets[0][n]=m0
    plan={33:t_blob(fr,m0,blur=14,thresh=0.32,amp=0.18,freq=40,seed=3),   # ragged bleed
          66:t_pixels(fr,m0,g.width,10,4),                                  # clean pixels
          100:None}
    plan[100]=t_blob(fr,plan[66],blur=40,thresh=0.5,amp=0.25,freq=90,seed=7)  # pixels melted to blobs
    for k in (33,66,100):
        targets[k][n]=plan[k]
        masters[k][n]=shrinkwrap(fr,sk,plan[k],sk) if sk else []
print("masters built in %.1fs (%d glyphs)"%(time.time()-T,len(names)))

# ---------- measure ----------
def iou(a,b): return (a&b).sum()/max(1,(a|b).sum())
print("\nIoU of master outline vs target (1.0 = perfect):")
for k in (33,66,100):
    vals={n:iou(frames[n].fill(masters[k][n]),targets[k][n]) for n in names if masters[k][n]}
    print(f"  DCAY {k:3d}: mean {np.mean(list(vals.values())):.3f}  worst "+", ".join(f"{n}={v:.2f}" for n,v in sorted(vals.items(),key=lambda x:x[1])[:3]))
print("\nIn-between artefacts: % of ink pixels where |winding|>1 or <0 (folds/inversions):")
for a,b in zip(STOPS,STOPS[1:]):
    for t in (0.25,0.5,0.75):
        bad=[];
        for n in names:
            if not masters[a][n]: continue
            inst=[(1-t)*p+t*q for p,q in zip(masters[a][n],masters[b][n])]
            w=frames[n].winding(inst); ink=(w!=0).sum()
            bad.append(((w<0)|(w>1)).sum()/max(1,ink))
        print(f"  {a}->{b} @{t:.2f}: mean {100*np.mean(bad):.1f}%  max {100*np.max(bad):.1f}%")

# ---------- build VF ----------
def build(mm):
    fb=FontBuilder(upm,isTTF=True); fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c):cmap[ord(c)] for c in TEXT})
    glyphs={}
    for n in names:
        pen=TTGlyphPointPen(None)
        for c in mm[n]:
            pen.beginPath()
            for q in np.round(c).astype(int): pen.addPoint((int(q[0]),int(q[1])),'line')
            pen.endPath()
        glyphs[n]=pen.glyph()
    fb.setupGlyf(glyphs); met={}
    for n in names:
        gl=fb.font['glyf'][n]; gl.recalcBounds(fb.font['glyf']); met[n]=(adv[n],getattr(gl,'xMin',0))
    fb.setupHorizontalMetrics(met); fb.setupHorizontalHeader(ascent=src['hhea'].ascent,descent=src['hhea'].descent)
    fb.setupNameTable({"familyName":"DecaySpike4","styleName":"Regular"})
    fb.setupOS2(sTypoAscender=os2.sTypoAscender,sTypoDescender=os2.sTypoDescender,usWinAscent=os2.usWinAscent,usWinDescent=os2.usWinDescent)
    fb.setupPost(); return fb.font
ds=DesignSpaceDocument(); ax=AxisDescriptor(); ax.tag="DCAY"; ax.name="Decay"; ax.minimum=0; ax.default=0; ax.maximum=100; ds.addAxis(ax)
for k in STOPS:
    s=SourceDescriptor(); s.font=build(masters[k]); s.location={"Decay":k}; s.name=f"m{k}"; ds.addSource(s)
vf,_,_=varLib.build(ds); vf.save(OUT)
print("\nVF saved; varying glyphs:",len(vf['gvar'].variations),"of",len(names))
