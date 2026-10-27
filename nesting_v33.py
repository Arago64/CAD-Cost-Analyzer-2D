"""v3.3 contact-pressure refinement on *exact* Shapely polygons.

A fast, continuous translational search complements v3.2's discrete NFP/LNS
search. It explores the last admissible motion on each ray with bisection,
thereby finding contact positions that no finite angular/XY raster enumerates.
It also moves small coherent clusters and tries modest near-angle rotations.

The layout is never made worse: every published update passes the independent
optimizer_v15 layout validator and respects the caller's hard deadline.
"""
from __future__ import annotations
import math
import random
from time import monotonic
from shapely import affinity
from shapely.geometry import box
from nesting_advanced import _tight_auto_result, _summarize
from optimizer_v15 import _validate
from nesting_v31 import _geometry_ok


def footprint(rows, margin):
    bounds=[r['shape'].bounds for r in rows]
    x0=min(b[0] for b in bounds);y0=min(b[1] for b in bounds)
    x1=max(b[2] for b in bounds);y1=max(b[3] for b in bounds)
    w=x1-x0+2*margin;h=y1-y0+2*margin
    return w*h,w,h


def _feasible(shape, obstacles, gap):
    return _geometry_ok(shape,obstacles,gap)


def _slide(rows, moving, direction, gap, limit, deadline):
    """Find a maximal feasible straight-line motion, not just integer grid moves.

    Stop at the FIRST collision. Shifting past a collision might become feasible,
    but such transitions belong in the separate LNS move, not continuous slide.
    """
    if monotonic()+.015>=deadline:return []
    dx,dy=direction
    length=math.hypot(dx,dy)
    if length==0:return []
    dx/=length;dy/=length
    stable=[r for i,r in enumerate(rows) if i not in moving]
    own=[rows[i] for i in moving]
    if not stable:return []
    def feasible(dist):
        translated=[affinity.translate(r['shape'],xoff=dx*dist,yoff=dy*dist) for r in own]
        return translated if all(_feasible(sh,stable,gap) for sh in translated) else None
    # Small legal movements are important near already optimized contacts.
    trial=[.12,.5,1.0,2.0,4.0,8.,16.,32.,64.,128.,256.,512.,1024.]
    feasible_points=[];last=0.;boundary=None
    for d in trial:
        if d>limit:d=limit
        if monotonic()+.01>=deadline:break
        trial_geom=feasible(d)
        if trial_geom is None:
            boundary=d;break
        feasible_points.append((d,trial_geom));last=d
        if d>=limit:break
    if boundary is not None and last>0 and monotonic()+.015<deadline:
        lo=last;hi=boundary;last_shapes=feasible_points[-1][1]
        for _ in range(13):
            if monotonic()+.005>=deadline:break
            mid=(lo+hi)/2
            candidate=feasible(mid)
            if candidate is None:hi=mid
            else:lo=mid;last_shapes=candidate
        feasible_points.append((lo,last_shapes))
    elif boundary is not None and not feasible_points:
        # Near-contact placements may only permit a sub-0.12mm movement.
        hi=boundary;lo=0.;last_shapes=None
        for _ in range(11):
            if monotonic()+.005>=deadline:break
            mid=(lo+hi)/2
            candidate=feasible(mid)
            if candidate is None:hi=mid
            else:lo=mid;last_shapes=candidate
        if last_shapes is not None:feasible_points.append((lo,last_shapes))
    return feasible_points


def _changed_rows(rows, ids, geom):
    newer=list(rows)
    for j,g in zip(ids,geom):
        newer[j]=dict(rows[j],shape=g,x=float(g.bounds[0]),y=float(g.bounds[1]))
    return newer


def _directions(rng, epoch):
    # Priority toward bottom-left, but allow escaping unnecessary pinning to
    # previous anchors. The last two non-axis directions improve wedge fits.
    result=[(-1,0),(0,-1),(-1,-1),(1,-1),(-1,1),(1,0),(0,1),
            (-1,-.22),(-.22,-1),(.22,-1),(-1,.22)]
    if epoch%3:result=result[:7]
    if epoch%5==4:rng.shuffle(result)
    return result


def _best_translation(rows, ids, *, margin, gap, deadline, epoch, rng):
    old=footprint(rows,margin)[0]
    current=None
    for direction in _directions(rng,epoch):
        if monotonic()+.02>=deadline:break
        for _,geom in _slide(rows,ids,direction,gap,1500.,deadline):
            newer=_changed_rows(rows,ids,geom)
            val=footprint(newer,margin)[0]
            if val+1e-4<old and (current is None or val<current[0]):current=(val,newer)
    return current


def _clusters(rows, rng, epoch, gap):
    # Boundary-driven pairs and triplets of nearby parts. This is intentionally
    # cheaper than rebuilding an NFP for a high-vertex circular DXF on each turn.
    n=len(rows)
    frontier=sorted(range(n),key=lambda i:(rows[i]['shape'].bounds[2],
                                                 rows[i]['shape'].bounds[3]),reverse=True)
    neighbors=[]
    for i in frontier[:min(n,8)]:
        near=sorted((j for j in range(n) if j!=i),
                    key=lambda j:rows[i]['shape'].distance(rows[j]['shape']))[:3]
        for j in near:
            neighbors.append(tuple(sorted((i,j))))
            if epoch%2==0:
                other=next((k for k in near if k!=j),None)
                if other is not None:neighbors.append(tuple(sorted((i,j,other))))
    # Rotate through neighbors rather than always starting from the same pair.
    return list(dict.fromkeys(neighbors))


def _angle_refine(rows,i,margin,gap,deadline,quality):
    """Rotate around current item's centroid and try its original XY footprint.

    The absolute orientation stored for each instance remains trustworthy: we
    increment it by the applied geometric delta, not by a source-template angle.
    """
    if monotonic()+.02>=deadline:return None
    base=rows[i]
    stable=[r for j,r in enumerate(rows) if j!=i]
    old=footprint(rows,margin)[0];best=None
    steps=(-5,5,-1,1) if quality!='fast' else (-5,5)
    if quality=='precision':steps=(-15,15,-5,5,-2,2,-1,1)
    x0,y0=base['shape'].bounds[:2]
    for turn in steps:
        if monotonic()+.02>=deadline:break
        rotated=affinity.rotate(base['shape'],turn,origin='centroid')
        bx,by,_,_=rotated.bounds
        for x,y in ((x0,y0),(bx,by),(x0-5,y0),(x0,y0-5),(x0+5,y0),(x0,y0+5)):
            sh=affinity.translate(rotated,xoff=x-bx,yoff=y-by)
            if not _feasible(sh,stable,gap):continue
            newer=list(rows)
            newer[i]=dict(base,shape=sh,x=sh.bounds[0],y=sh.bounds[1],
                          angle=(float(base.get('angle',0))+turn)%360.)
            val=footprint(newer,margin)[0]
            if val+1e-4<old and (best is None or val<best[0]):best=(val,newer)
    return best


def improve_seed(seed,*,deadline,quality='balanced',random_seed=345,
                 on_improved=None,cluster=True):
    """Improve an already *valid*, one-sheet, auto-sized layout.

    A seed can come from our decoder, a saved older program, or manual edits.
    There is no hidden exemplar data: this does not silently learn the user's
    manually edited answer or claim to find it from scratch.
    """
    if seed is None or seed.get('stock_outline') is not None or seed.get('sheet_count')!=1:
        return seed
    if not _validate(seed):return seed
    original=list(seed['placements']); n=len(original)
    if n<2 or n>50 or monotonic()+.03>=deadline:return seed
    rows=[dict(p) for p in original]
    gap=float(seed['gap_mm']);margin=float(seed['margin_mm'])
    best=seed;best_cost=footprint(rows,margin)[0]
    rng=random.Random(random_seed)
    epochs=0;stall=0
    while monotonic()+.04<deadline and stall<4:
        epochs+=1;changes=0
        # Concentrate on bounding-sheet contributors, not random bulk pieces.
        ix=sorted(range(n),key=lambda i:max(rows[i]['shape'].bounds[2],
                                               rows[i]['shape'].bounds[3]),reverse=True)
        if epochs%3==0:rng.shuffle(ix)
        for i in ix:
            if monotonic()+.05>=deadline:break
            trial=_best_translation(rows,(i,),margin=margin,gap=gap,
                                    deadline=deadline,epoch=epochs,rng=rng)
            if trial is None and quality=='precision':
                trial=_angle_refine(rows,i,margin,gap,deadline,quality)
            if trial is not None and trial[0]+1e-5<best_cost:
                cost,newrows=trial
                fitted,dims=_tight_auto_result(newrows,margin)
                built=_summarize(fitted,[dims],margin,gap,'v3.3 exact contact-pressure + cluster LNS')
                if _validate(built) and built['sheet_mm2']<=best['sheet_mm2']+1e-4:
                    rows=newrows;changes+=1;best_cost=cost
                    if built['sheet_mm2'] < best['sheet_mm2']-1e-4:
                        best=built
                        if on_improved:on_improved(built)
        if cluster and monotonic()+.3<deadline and n<=35:
            groups=_clusters(rows,rng,epochs,gap)
            if epochs%2==0:rng.shuffle(groups)
            for ids in groups[:min(len(groups),30 if n<=20 else 14)]:
                if monotonic()+.06>=deadline:break
                trial=_best_translation(rows,ids,margin=margin,gap=gap,
                                        deadline=deadline,epoch=epochs,rng=rng)
                if trial is not None and trial[0]+1e-5<best_cost:
                    cost,newrows=trial
                    fitted,dims=_tight_auto_result(newrows,margin)
                    built=_summarize(fitted,[dims],margin,gap,'v3.3 contact pressure: rigid cluster')
                    if _validate(built) and built['sheet_mm2']<=best['sheet_mm2']+1e-4:
                        rows=newrows;changes+=1;best_cost=cost
                        if built['sheet_mm2']<best['sheet_mm2']-1e-4:
                            best=built
                            if on_improved:on_improved(built)
        stall=0 if changes else stall+1
    return best
