"""Spike 5: free-topology effects (raster->trace) + compatibiliser (match/resample/align/spawn-collapse)."""
import sys, time, numpy as np
from scipy import ndimage
from skimage import measure
sys.path.insert(0, '/tmp/vfspike')
import importlib.util
spec = importlib.util.spec_from_file_location("s4", "/tmp/vfspike/spike4_lib.py"); s4 = importlib.util.module_from_spec(spec)
src_code = open('/tmp/vfspike/spike4.py').read().split('# ---------- shrink-wrap')[0]
open('/tmp/vfspike/spike4_lib.py','w').write(src_code); spec.loader.exec_module(s4)
from fontTools.pens.ttGlyphPen import TTGlyphPointPen
from fontTools.fontBuilder import FontBuilder
from fontTools.designspaceLib import DesignSpaceDocument, AxisDescriptor, SourceDescriptor
from fontTools import varLib

S, STEP = s4.S, 12
def area(c): x,y=c[:,0],c[:,1]; return 0.5*np.sum(x*np.roll(y,-1)-np.roll(x,-1)*y)

def classify_source(contours):
    """Return list of (loop, is_island) normalising orientation: island = clockwise (TT)."""
    if not contours: return []
    big=max(contours,key=lambda c:abs(area(c))); outer_sign=np.sign(area(big))
    out=[]
    for c in contours:
        isl=np.sign(area(c))==outer_sign
        out.append((c,isl))
    return [(c if (area(c)<0)==isl else c[::-1], isl) for c,isl in out]

def trace(fr, mask):
    """Marching squares on target; islands vs holes via positive_orientation."""
    m=np.pad(mask.astype(float),2)
    loops=measure.find_contours(m,0.5,positive_orientation='high')
    out=[]
    for L in loops:
        if len(L)<8: continue
        rows,cols=L[:,0]-2,L[:,1]-2
        p=np.c_[(cols+0.5)/S+fr.x0, (rows+0.5)/S+s4.Y0]
        p=0.5*p+0.25*(np.roll(p,1,0)+np.roll(p,-1,0))      # light smooth of the staircase
        a=area(p)
        if abs(a)<200: continue                              # drop specks (units^2)
        out.append(p)
    # island if traced with 'high' orientation -> sign of area tells; normalise island=CW
    res=[]
    if not out: return res
    # with positive_orientation='high' islands share one sign; determine via raster test of a point just inside
    for p in out:
        a=area(p); isl_guess = a>0
        res.append((p,isl_guess))
    # sanity: largest loop must be an island; flip interpretation if not
    big=max(range(len(res)),key=lambda i:abs(area(res[i][0])))
    if not res[big][1]: res=[(p,not i) for p,i in res]
    return [(p if (area(p)<0)==isl else p[::-1], isl) for p,isl in res]

def resample(c,K):
    closed=np.vstack([c,c[:1]]); d=np.r_[0,np.cumsum(np.hypot(*np.diff(closed,axis=0).T))]
    ts=np.linspace(0,d[-1],K,endpoint=False)
    return np.c_[np.interp(ts,d,closed[:,0]),np.interp(ts,d,closed[:,1])]
def perim(c): return np.hypot(*np.diff(np.vstack([c,c[:1]]),axis=0).T).sum()
def align(ref,c):
    """cyclic shift of c minimising distance to ref (same K)."""
    K=len(ref); best=min(range(K),key=lambda s: ((np.roll(c,-s,0)-ref)**2).sum()); return np.roll(c,-best,0)

def compatibilise(fr, stops_loops):
    """stops_loops: list over stops of [(loop,is_island)]. Returns list over stops of [loop] with identical structure."""
    tracks=[]   # each: dict stop->loop (raw), island flag
    prev=[]     # (track_idx) alive at previous stop
    for k,loops in enumerate(stops_loops):
        fills=[fr.winding([l])!=0 for l,_ in loops]
        alive=[]
        cand=[]
        for ti in prev:
            t=tracks[ti]; ft=fr.winding([t['loops'][k-1]])!=0
            for li,(l,isl) in enumerate(loops):
                if isl!=t['island']: continue
                inter=(ft&fills[li]).sum()
                if inter>0: cand.append((inter/ (ft|fills[li]).sum(), ti, li))
        used_t=set(); used_l=set()
        for score,ti,li in sorted(cand,reverse=True):
            if ti in used_t or li in used_l or score<0.15: continue
            tracks[ti]['loops'][k]=loops[li][0]; used_t.add(ti); used_l.add(li); alive.append(ti)
        for li,(l,isl) in enumerate(loops):
            if li not in used_l:
                tracks.append({'island':isl,'loops':{k:l}}); alive.append(len(tracks)-1)
        prev=alive
    n=len(stops_loops); out=[[] for _ in range(n)]
    # islands first then holes (stable order)
    for t in sorted(tracks,key=lambda t: not t['island']):
        K=max(16,int(max(perim(l) for l in t['loops'].values())/STEP))
        ks=sorted(t['loops'])
        res={k:resample(t['loops'][k],K) for k in ks}
        for a,b in zip(ks,ks[1:]): res[b]=align(res[a],res[b])
        for k in range(n):
            if k in res: out[k].append(res[k]); continue
            # placeholder: collapse to centroid of nearest existing stop's loop
            near=min(ks,key=lambda j:abs(j-k)); c=res[near].mean(0)
            out[k].append(np.repeat(c[None],K,0))
    return out

def _sdf(m): return ndimage.distance_transform_edt(~m)-ndimage.distance_transform_edt(m)
s4.sdf=_sdf
STOPS=[0,33,66,100]
names=s4.names; gs=s4.gs; T=time.time()
masters={k:{} for k in STOPS}; targets={k:{} for k in STOPS}; frames={}; adv={}
for n in names:
    g=gs[n]; adv[n]=g.width; fr=s4.Frame(g.width); frames[n]=fr
    sk=s4.skeleton(g)
    if not sk:
        for k in STOPS: masters[k][n]=[]
        continue
    m0=fr.fill(sk)
    t33=s4.t_blob(fr,m0,blur=14,thresh=0.32,amp=0.18,freq=40,seed=3)
    t66=s4.t_pixels(fr,m0,g.width,10,4)
    t100=s4.t_blob(fr,t66,blur=40,thresh=0.5,amp=0.25,freq=90,seed=7)
    tg=[m0,t33,t66,t100]
    for k,t in zip(STOPS,tg): targets[k][n]=t
    loops=[classify_source(sk)]+[trace(fr,t) for t in tg[1:]]
    comp=compatibilise(fr,loops)
    for k,c in zip(STOPS,comp): masters[k][n]=c
print("masters built in %.1fs"%(time.time()-T))

def iou(a,b): return (a&b).sum()/max(1,(a|b).sum())
print("IoU master vs target:")
for k in STOPS:
    v={n:iou(frames[n].fill(masters[k][n]),targets[k][n]) for n in names if masters[k][n]}
    print(f"  DCAY {k:3d}: mean {np.mean(list(v.values())):.3f}  worst "+", ".join(f"{n}={x:.2f}" for n,x in sorted(v.items(),key=lambda x:x[1])[:3]))
print("In-betweens vs SDF-blend reference (IoU):")
for a,b in zip(STOPS,STOPS[1:]):
    for t in (0.25,0.5,0.75):
        v=[]
        for n in names:
            if not masters[a][n]: continue
            inst=[(1-t)*p+t*q for p,q in zip(masters[a][n],masters[b][n])]
            ref=((1-t)*s4.sdf(targets[a][n])+t*s4.sdf(targets[b][n]))<0
            v.append(iou(frames[n].fill(inst),ref))
        print(f"  {a}->{b} @{t:.2f}: mean {np.mean(v):.3f}  min {np.min(v):.3f}")

def build(mm):
    fb=FontBuilder(s4.upm,isTTF=True); fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c):s4.cmap[ord(c)] for c in s4.TEXT})
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
    fb.setupHorizontalMetrics(met); fb.setupHorizontalHeader(ascent=s4.src['hhea'].ascent,descent=s4.src['hhea'].descent)
    fb.setupNameTable({"familyName":"DecaySpike5","styleName":"Regular"})
    o=s4.os2; fb.setupOS2(sTypoAscender=o.sTypoAscender,sTypoDescender=o.sTypoDescender,usWinAscent=o.usWinAscent,usWinDescent=o.usWinDescent)
    fb.setupPost(); return fb.font
ds=DesignSpaceDocument(); ax=AxisDescriptor(); ax.tag="DCAY"; ax.name="Decay"; ax.minimum=0; ax.default=0; ax.maximum=100; ds.addAxis(ax)
for k in STOPS:
    s=SourceDescriptor(); s.font=build(masters[k]); s.location={"Decay":k}; s.name=f"m{k}"; ds.addSource(s)
vf,_,_=varLib.build(ds); vf.save(sys.argv[2]); print("VF varying glyphs:",len(vf['gvar'].variations),"of",len(names))
