"""v3.2 scalable cooperative nesting local search.

Every candidate is checked with GEOS, with a second whole-layout validator
before being exported. No promise of a globally optimal nesting solution.

Innovations relative to v3.1:
- source matching and updates by distinct *instance position* rather than name;
- rigid two-/three-part cluster (LNS) moves and rotations;
- scheduled 15/5/1 degree angle refinement;
- right/top frontier priority with escape moves and global rebuilds;
- search caps determined by deadline, not an arbitrary 20-part exclusion.
"""
from __future__ import annotations
import math
import random
from time import monotonic
from shapely import affinity
from shapely.ops import unary_union
from sheet_nesting import _orient
from nesting_advanced import _tight_auto_result, _summarize
from nesting_v31 import _parts_score, _try_insert, _geometry_ok
from nesting_v3 import _positions, _score
from optimizer_v15 import _validate


def _sources_for_rows(rows, items):
    """Find each source polygon without collapsing repeated names into a dict.

    Dupes from imported DXFs may even share exactly the same label. Area,
    perimeter and hole count disambiguate geometrically different contours.
    Truly identical copies remain distinct by their placement indices.
    """
    used=set(); result=[]
    for row in rows:
        candidates=[]
        for i,(name,shape) in enumerate(items):
            if i in used or name!=row['name']:continue
            placed=row['shape']
            scale=max(1.,shape.area,placed.area)
            if abs(shape.area-placed.area)>scale*1e-5:continue
            if abs(shape.length-placed.length)>max(1.,shape.length)*1e-4:continue
            if len(shape.interiors)!=len(placed.interiors):continue
            candidates.append(i)
        if not candidates:return None
        i=candidates[0];used.add(i);result.append(items[i][1])
    return result if len(used)==len(items) else None


def _new_result(rows, seed, auto):
    margin=float(seed['margin_mm']);gap=float(seed['gap_mm'])
    if auto:
        moved,dims=_tight_auto_result(rows,margin)
        return _summarize(moved,[dims],margin,gap,'v3.2 hybrid: NFP/contact + cluster LNS + angle precision')
    sheets=[(p['width_mm'],p['height_mm']) for p in seed['sheets']]
    return _summarize(rows,sheets,margin,gap,'v3.2 hybrid: NFP/contact + cluster LNS + angle precision')


def _candidate_indices(rows,rng,iteration):
    n=len(rows)
    bbox=[r['shape'].bounds for r in rows]
    right=max(b[2] for b in bbox); top=max(b[3] for b in bbox)
    # The parts contributing to the stock edges have the greatest marginal
    # potential; their immediate neighbors are often the actual blockers.
    frontier=sorted(range(n),key=lambda i:
        max((bbox[i][2]/max(1.,right))**3,(bbox[i][3]/max(1.,top))**3),reverse=True)
    if iteration%9==0:return rng.sample(range(n),min(3,n))
    if iteration%4==0:return rng.sample(range(n),min(2,n))
    critical=rng.choice(frontier[:min(n,5)])
    if iteration%5==1:return [critical]
    near=sorted((i for i in range(n) if i!=critical),key=lambda i:
                rows[critical]['shape'].distance(rows[i]['shape']))
    if not near:return [critical]
    if iteration%3==0 and len(near)>=2:return [critical,near[0],near[1]]
    return [critical,near[0]]


def _group_trial(rows, moving_ids, *, W,H,margin,gap,auto,deadline,rng,quality):
    """Rigidly slide/rotate mutually adjacent parts as one compound object.

    A convex hull is used ONLY to propose anchor coordinates: each original
    part's exact polygon (including holes) decides feasibility.
    """
    if monotonic()+0.04>=deadline:return None
    stable=[r for i,r in enumerate(rows) if i not in moving_ids]
    moving=[rows[i] for i in moving_ids]
    if len(moving)<2:return None
    old_score=_parts_score(rows,auto=auto,W=W,H=H,margin=margin)
    minx=min(p['shape'].bounds[0] for p in moving)
    miny=min(p['shape'].bounds[1] for p in moving)
    maxx=max(p['shape'].bounds[2] for p in moving)
    maxy=max(p['shape'].bounds[3] for p in moving)
    center=((minx+maxx)/2,(miny+maxy)/2)
    rotations=[0.,90.,-90.,180.]
    if quality in ('balanced','thorough','precision'):rotations.extend([-15.,15.])
    if quality=='precision':rotations.extend([-5.,5.])
    remaining=deadline-monotonic()
    if remaining < .6:rotations=rotations[:2]
    # Many candidate anchors can be inferred from local neighbors, but final
    # clearance must be verified against ALL other parts.
    cx=(minx+maxx)/2; cy=(miny+maxy)/2
    neighbors=sorted(stable,key=lambda p:p['shape'].centroid.distance(moving[0]['shape'].centroid))
    # Include extreme pieces as anchors, plus local neighbors.
    extreme=sorted(stable,key=lambda p:p['shape'].bounds[2],reverse=True)[:2]
    anchors=list(dict((id(p),p) for p in (neighbors[:(6 if len(rows)>25 else 11)]+extreme)).values())
    best=None
    for delta in rotations:
        if monotonic()+.03>=deadline:break
        turned=[affinity.rotate(p['shape'],delta,origin=center) for p in moving]
        hull=unary_union(turned).convex_hull
        bx,by,br,bt=hull.bounds
        Wc=br-bx;Hc=bt-by
        if Wc>W-2*margin+1e-7 or Hc>H-2*margin+1e-7:continue
        anchor_shape=affinity.translate(hull,xoff=-bx,yoff=-by)
        candidates=_positions(anchor_shape,anchors,W,H,margin,gap,False,deadline,nfp=False)
        candidates.extend(((minx,miny),(margin,margin),
                           (margin,max(margin,miny)),(max(margin,minx),margin)))
        # Encourage aligning each component with a stable component; convex
        # hull contact proposals alone miss concave pair complementarity.
        for neighbor in anchors[:8]:
            a,b,c,d=neighbor['shape'].bounds
            candidates.extend(((c+gap,miny),(a-Wc-gap,miny),
                               (minx,d+gap),(minx,b-Hc-gap),
                               (c+gap,b),(a-Wc-gap,d-Hc)))
        unique=list(dict.fromkeys((round(float(x),5),round(float(y),5)) for x,y in candidates))
        unique=[(x,y) for x,y in unique if
                margin-1e-6<=x<=W-margin-Wc+1e-6 and
                margin-1e-6<=y<=H-margin-Hc+1e-6]
        # Lower envelope test discards any candidate with a worse partial
        # bounding rectangle; no collision check is skipped for survivors.
        def heuristic(xy):
            x,y=xy
            bxs=[p['shape'].bounds for p in stable]
            x0=min([x]+[v[0] for v in bxs]);y0=min([y]+[v[1] for v in bxs])
            x1=max([x+Wc]+[v[2] for v in bxs]);y1=max([y+Hc]+[v[3] for v in bxs])
            if auto:return ((x1-x0+2*margin)*(y1-y0+2*margin),x1+y1)
            strip=max(0.,(W-margin-x1)*(H-2*margin),
                      (H-margin-y1)*(W-2*margin),
                      (x0-margin)*(H-2*margin),
                      (y0-margin)*(W-2*margin))
            return (-strip,(x1-x0)*(y1-y0))
        unique.sort(key=heuristic)
        cap=(32 if len(rows)>30 else 60)
        if len(unique)>cap:
            head=unique[:cap*3//4];rest=unique[cap*3//4:]
            stride=max(1,len(rest)//max(1,cap-len(head)))
            unique=head+rest[::stride][:cap-len(head)]
        for x,y in unique:
            if monotonic()>=deadline:break
            dx=x-bx;dy=y-by
            changed=[];valid=True
            for row,sh in zip(moving,turned):
                new_shape=affinity.translate(sh,xoff=dx,yoff=dy)
                if not _geometry_ok(new_shape,stable,gap):valid=False;break
                if new_shape.bounds[0]<margin-1e-5 or new_shape.bounds[1]<margin-1e-5 or new_shape.bounds[2]>W-margin+1e-5 or new_shape.bounds[3]>H-margin+1e-5:
                    valid=False;break
                changed.append(dict(row,shape=new_shape,x=new_shape.bounds[0],
                                    y=new_shape.bounds[1],angle=(float(row['angle'])+delta)%360))
            if not valid:continue
            candidate=list(rows)
            for idx,part in zip(moving_ids,changed):candidate[idx]=part
            score=_parts_score(candidate,auto=auto,W=W,H=H,margin=margin)
            if score<old_score and (best is None or score<best[0]):best=(score,candidate)
    return best


def improve_seed(seed, items, *, auto, deadline, quality='balanced', rng=None,
                 on_improved=None):
    """Improve a fully verified one-sheet layout of up to 50 instances.

    Never reports a worse layout, returns a checkpoint on deadlines, preserves
    each individual duplicate. Deeper global steps are best-effort.
    """
    if seed is None or seed.get('sheet_count')!=1 or seed.get('stock_outline') is not None:
        return seed
    n=len(seed.get('placements',[]))
    if n<2 or n>50 or len(items)!=n or monotonic()>=deadline:return seed
    if not _validate(seed):return seed
    rows=[dict(p) for p in seed['placements']]
    sources=_sources_for_rows(rows,items)
    if sources is None:return seed
    rng=rng or random.Random(443)
    margin=float(seed['margin_mm']);gap=float(seed['gap_mm'])
    width=float(seed['sheets'][0]['width_mm']);height=float(seed['sheets'][0]['height_mm'])
    if auto:
        largest=max(max(p['shape'].bounds[2]-p['shape'].bounds[0],
                        p['shape'].bounds[3]-p['shape'].bounds[1]) for p in rows)
        W=width+largest*.72+gap;H=height+largest*.72+gap
    else:W,H=width,height
    best=seed;current=_parts_score(rows,auto=auto,W=W,H=H,margin=margin)
    stalled=0;iteration=0;maxstall=max(20,n*5)
    # Coarse steps decide the basin, then fine 5°/1° local refinement examines
    # physically nearby angles without requiring a 360-entry brute-force grid.
    coarse={'fast':30,'balanced':15,'thorough':15,'precision':15}.get(quality,15)
    while monotonic()+.06<deadline and stalled<maxstall:
        iteration+=1
        chosen=list(dict.fromkeys(_candidate_indices(rows,rng,iteration)))
        changed=None
        # Periodically try a rigid move of a favorable 2-3 part cluster.
        if len(chosen)>=2 and iteration%4==0:
            changed=_group_trial(rows,chosen,W=W,H=H,margin=margin,gap=gap,
                                 auto=auto,deadline=deadline,rng=rng,quality=quality)
        if changed is None:
            stable=[p for i,p in enumerate(rows) if i not in chosen]
            moving=list(chosen)
            if iteration%2==0:rng.shuffle(moving)
            candidate=list(rows);others=list(stable);good=True
            for idx in moving:
                if monotonic()+.04>=deadline:good=False;break
                item=rows[idx]
                attempt=_try_insert(item['name'],sources[idx],item,others,
                    W=W,H=H,margin=margin,gap=gap,auto=auto,deadline=deadline,
                    angle_step=coarse,angle_limit=(6 if n>30 else 10 if n>15 else 15),
                    sample_limit=(48 if n>30 else 75 if n>15 else 135),rng=rng)
                if attempt is None:good=False;break
                picked=attempt[1]
                # Fine rotations near best coarse angle. The unchanged angle is
                # retained as a candidate even if it is noninteger.
                if quality in ('balanced','thorough','precision') and monotonic()+.3<deadline:
                    ang=float(picked['angle'])
                    offs=(-5.,5.,-1.,1.)
                    if quality=='precision':offs=(-5.,5.,-2.,2.,-1.,1.)
                    extra=[(float(a),_orient(sources[idx],float(a)))
                           for a in dict.fromkeys((ang+d)%360 for d in offs)]
                    refined=_try_insert(item['name'],sources[idx],picked,others,
                         W=W,H=H,margin=margin,gap=gap,auto=auto,deadline=deadline,
                         angle_step=coarse,angle_limit=2,sample_limit=(35 if n>20 else 65),
                         angle_candidates=extra,rng=rng)
                    if refined is not None and refined[0]<attempt[0]:picked=refined[1]
                candidate[idx]=picked;others.append(picked)
            if good:
                score=_parts_score(candidate,auto=auto,W=W,H=H,margin=margin)
                if score<current:changed=(score,candidate)
        if changed is None:
            stalled+=1;continue
        score,candidate=changed
        if score>=current:
            stalled+=1;continue
        built=_new_result(candidate,best,auto)
        if not _validate(built):
            stalled+=1;continue
        rows=candidate;current=score;stalled=0
        if _score(built,auto)<_score(best,auto):
            best=built
            if on_improved:on_improved(best)
    return best
