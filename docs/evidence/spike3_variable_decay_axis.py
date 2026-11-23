"""Spike: point-compatible decay masters -> variable font with DCAY axis."""
import sys, math, hashlib
import numpy as np
from scipy import ndimage
from skimage.draw import polygon as skpoly
from opensimplex import OpenSimplex
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPointPen
from fontTools.fontBuilder import FontBuilder
from fontTools.designspaceLib import DesignSpaceDocument, AxisDescriptor, SourceDescriptor
from fontTools import varLib

SRC, OUT = sys.argv[1], sys.argv[2]
CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,!? "
STEP = 14  # resample spacing in font units

src = TTFont(SRC); gs = src.getGlyphSet(); cmap = src.getBestCmap()
upm = src['head'].unitsPerEm; os2 = src['OS/2']; xh = os2.sxHeight
names = [cmap[ord(c)] for c in CHARS if ord(c) in cmap]
names = ['.notdef'] + sorted(set(names) - {'.notdef'}, key=names.index)

def bez(pts, t):
    pts = np.asarray(pts, float)
    while len(pts) > 1: pts = (1-t)*pts[:-1] + t*pts[1:]
    return pts[0]

def resample(glyph):
    """Flatten + resample by arc length -> list of (K,2) arrays. Defines the shared structure."""
    rp = DecomposingRecordingPen(gs); glyph.draw(rp)
    contours=[]; segs=[]; last=start=None
    for op,a in rp.value:
        if op=='moveTo': segs=[]; start=last=a[0]
        elif op=='lineTo': segs.append([last,a[0]]); last=a[0]
        elif op=='curveTo': segs.append([last,*a]); last=a[-1]
        elif op=='qCurveTo':  # expand implied on-curves
            pts=[last,*a]; 
            for i in range(1,len(pts)-1):
                p0 = pts[0] if i==1 else tuple((np.add(pts[i-1],pts[i])/2))
                p2 = pts[-1] if i==len(pts)-2 else tuple((np.add(pts[i],pts[i+1])/2))
                segs.append([p0,pts[i],p2])
            last=a[-1]
        elif op in('closePath','endPath'):
            if last!=start: segs.append([last,start])
            dense=np.array([bez(s,t) for s in segs for t in np.linspace(0,1,40,endpoint=False)])
            d=np.r_[0,np.cumsum(np.hypot(*np.diff(np.vstack([dense,dense[:1]]),axis=0).T))]
            L=d[-1]; K=max(12,int(L/STEP))
            ts=np.linspace(0,L,K,endpoint=False)
            closed=np.vstack([dense,dense[:1]])
            contours.append(np.c_[np.interp(ts,d,closed[:,0]),np.interp(ts,d,closed[:,1])])
    return contours

# ---------- effects: all are point maps (same K points out) ----------
def magnet(contours, adv, nx, rows):
    cw=adv/nx; rh=xh/rows
    return [np.c_[(np.floor(c[:,0]/cw)+.5)*cw, np.round(c[:,1]/rh)*rh] for c in contours]

S = 0.25          # raster px per font unit
PAD = 60
def field(contours, adv, blur, thresh, noise_amp, noise_freq, seed):
    """Ink field: rasterise (even-odd), blur, add simplex noise; level 0 = ink edge."""
    W=int((adv+2*PAD/S)*S)+1; y0=-0.35*upm; H=int((1.4*upm)*S)+1
    m=np.zeros((H,W),bool)
    for c in contours:
        rr,cc=skpoly((c[:,1]-y0)*S,(c[:,0]+PAD/S)*S,(H,W)); m[rr,cc]^=True
    f=ndimage.gaussian_filter(m.astype(float), blur*S)
    if noise_amp:
        n=OpenSimplex(seed); xs=np.arange(W)/(noise_freq*S); ys=np.arange(H)/(noise_freq*S)
        f=f+noise_amp*n.noise2array(xs,ys)
    f=f-thresh
    to_px=lambda p: ((p[:,1]-y0)*S,(p[:,0]+PAD/S)*S)
    return f,to_px

def project(contours, f, to_px, iters=25, maxstep=6.0):
    gy,gx=np.gradient(f)
    out=[]
    for c in contours:
        p=c.copy()
        for _ in range(iters):
            r,cc=to_px(p)
            v=ndimage.map_coordinates(f,[r,cc],order=1,mode='nearest')
            dx=ndimage.map_coordinates(gx,[r,cc],order=1,mode='nearest')
            dy=ndimage.map_coordinates(gy,[r,cc],order=1,mode='nearest')
            g2=dx*dx+dy*dy+1e-9
            stp=np.clip(-v/g2,-maxstep,maxstep)    # pixels
            p[:,0]+=stp*dx/S; p[:,1]+=stp*dy/S
        out.append(p)
    return out

def seed_for(*k): return int(hashlib.sha1("|".join(map(str,k)).encode()).hexdigest()[:8],16)

def ink(contours, adv, name, blur, thresh, amp, freq):
    f,tp=field(contours,adv,blur,thresh,amp,freq,seed_for(name,blur))
    return project(contours,f,tp)

import time
T={'res':0,'ink':0,'mag':0}
masters={0:{},33:{},66:{},100:{}}
adv={}
for n in names:
    g=gs[n]; adv[n]=g.width
    t=time.time(); base=resample(g); T['res']+=time.time()-t; t=time.time()
    masters[0][n]=base
    masters[33][n]=ink(base,g.width,n,blur=18,thresh=0.38,amp=0.10,freq=60)       # bleed
    T['ink']+=time.time()-t
    px=magnet(base,g.width,10,4)
    masters[66][n]=px
    masters[100][n]=ink(px,g.width,n,blur=45,thresh=0.45,amp=0.12,freq=90)       # melt pixels into blobs

def build(cmap_contours):
    fb=FontBuilder(upm,isTTF=True); fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c):cmap[ord(c)] for c in CHARS if ord(c) in cmap})
    glyphs={}; metrics={}
    for n in names:
        pen=TTGlyphPointPen(None)
        for c in cmap_contours[n]:
            c=np.round(c).astype(int)
            pen.beginPath()
            for q in c: pen.addPoint((int(q[0]),int(q[1])),'line')
            pen.endPath()
        glyphs[n]=pen.glyph()
    fb.setupGlyf(glyphs)
    for n in names:
        gl=fb.font['glyf'][n]; gl.recalcBounds(fb.font['glyf'])
        metrics[n]=(adv[n], getattr(gl,'xMin',0))
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=src['hhea'].ascent,descent=src['hhea'].descent)
    fb.setupNameTable({"familyName":"DecaySpike","styleName":"Regular"})
    fb.setupOS2(sTypoAscender=os2.sTypoAscender,sTypoDescender=os2.sTypoDescender,usWinAscent=os2.usWinAscent,usWinDescent=os2.usWinDescent,sxHeight=xh,sCapHeight=os2.sCapHeight)
    fb.setupPost()
    return fb.font

ds=DesignSpaceDocument()
ax=AxisDescriptor(); ax.tag="DCAY"; ax.name="Decay"; ax.minimum=0; ax.default=0; ax.maximum=100
ds.addAxis(ax)
for loc in sorted(masters):
    s=SourceDescriptor(); s.font=build(masters[loc]); s.location={"Decay":loc}; s.name=f"m{loc}"
    ds.addSource(s)
print('timing',{k:round(v,1) for k,v in T.items()})
vf,_,_=varLib.build(ds)
vf.save(OUT); print("saved",OUT, "glyphs",len(names), "gvar glyphs",len(vf['gvar'].variations))
