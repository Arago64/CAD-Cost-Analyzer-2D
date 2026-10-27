"""CAD Cost Analyzer v3: independent material-first nesting core.

Combines GEOS/Shapely computational geometry, an existing fast decoder, a
bounded secondary multi-start decoder and an independent v3.1 contact-based
ruin/recreate stage seeded from machine or manually edited layouts. All
components are local: no remote APIs and no separate installed CAD engine.

A placement is accepted ONLY after exact Shapely boundary/clearance checks.
This is a time-bounded heuristic, not a certified global optimum or CAM planner.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil, sqrt
from time import monotonic
import random
from shapely import affinity
from shapely.geometry import Polygon, box
from sheet_nesting import _input_items, _orient
from nesting_advanced import _summarize, _tight_auto_result, advanced_nest
from optimizer_v15 import _validate

@dataclass
class Configuration:
    time_seconds: float = 18.0
    rotation_step: int = 90
    seed: int = 47
    allow_holes: bool = True
    spare_percent: int = 7


def _dims(rows, margin):
    if not rows: return 0., 0.
    return (max(p['shape'].bounds[2] for p in rows) - min(p['shape'].bounds[0] for p in rows)+2*margin,
            max(p['shape'].bounds[3] for p in rows) - min(p['shape'].bounds[1] for p in rows)+2*margin)


def _score(layout, auto, prefer_scrap=False):
    # Geometric stock cost first.  When fixed, remaining full-width strip / full-
    # height strip wins over a scattered layout with identical sheet-area number.
    rows=layout['placements']; n=layout['sheet_count']
    area=layout['sheet_mm2']
    used=[]; free_strips=[]
    for sh in layout['sheets']:
        p=[r for r in rows if r.get('sheet_index',0)==sh['index']]
        if not p:continue
        w,h=_dims(p,0)
        used.append(w*h)
        right=max(r['shape'].bounds[2] for r in p)
        top=max(r['shape'].bounds[3] for r in p)
        left=min(r['shape'].bounds[0] for r in p)
        bottom=min(r['shape'].bounds[1] for r in p)
        m=layout['margin_mm']; sw=sh['width_mm'];sy=sh['height_mm']
        free_strips.append(max(max(0,sw-m-right)*max(0,sy-2*m),
                               max(0,left-m)*max(0,sy-2*m),
                               max(0,sy-m-top)*max(0,sw-2*m),
                               max(0,bottom-m)*max(0,sw-2*m)))
    if auto:
        return (n, round(area,4), round(sum(used),4))
    return (n, round(area,4), -round(sum(free_strips),4), round(sum(used),4))


def _outline(poly, count=15):
    """Exact vertices for simple contours; length-proportional points for arcs."""
    pts=[]
    for ring in (poly.exterior, *poly.interiors):
        coords=list(ring.coords)[:-1]
        if len(coords)<=count:pts += coords
        else:
            line=ring
            pts += [(line.interpolate(i*line.length/count).x,
                     line.interpolate(i*line.length/count).y) for i in range(count)]
    return pts


def _positions(ref, placed, W, H, margin, gap, allow_holes, deadline, nfp=False):
    """Candidate contacts: stock corners, aligned bbox edges, opposing vertices,
    optional no-fit-polygon contour contacts. Final acceptance is exact.
    """
    width,height=ref.bounds[2],ref.bounds[3]
    xmax=W-margin-width; ymax=H-margin-height
    if xmax<margin-1e-7 or ymax<margin-1e-7:return []
    contacts={(margin,margin),(xmax,margin),(margin,ymax),(xmax,ymax)}
    rpts=_outline(ref,10 if len(placed)>6 else 18)
    for prev in placed:
        old=prev['shape'];x0,y0,x1,y1=old.bounds
        # Exact aligned edges, including nesting within concave recesses.
        xs=(x0-width-gap,x0-width,x0-gap,x0,x1-width,x1,x1+gap,x1-width+gap)
        ys=(y0-height-gap,y0-height,y0-gap,y0,y1-height,y1,y1+gap,y1-height+gap)
        for x in xs:
            for y in (margin,y0,y1,y1+gap,y0-height,y1-height):contacts.add((x,y))
        for y in ys:
            for x in (margin,x0,x1,x1+gap,x0-width,x1-width):contacts.add((x,y))
        # Critical for nonrectangular interlocking where bbox contacts fail.
        opoints=_outline(old,8 if len(placed)>8 else 16)
        rp=rpts[::max(1,len(rpts)//14)]
        for ox,oy in opoints[::max(1,len(opoints)//16)]:
            for px,py in rp:
                for dx,dy in ((gap,0),(-gap,0),(0,gap),(0,-gap)):
                    contacts.add((ox-px+dx,oy-py+dy))
        # Holes and concave bays are real stock, and can't be ruled out using
        # bounding boxes.  Polygon boundaries are final collision authority.
        if allow_holes and old.interiors:
            for interior in old.interiors:
                a,b,c,d=Polygon(interior).bounds
                contacts.update(((a+gap,b+gap),(c-width-gap,b+gap),
                                 (a+gap,d-height-gap),(c-width-gap,d-height-gap),
                                 ((a+c-width)/2,(b+d-height)/2)))
        if monotonic()>=deadline:break
    if nfp and placed and monotonic()+.03<deadline:
        # Pairwise cached Minkowski sum from the established NFP approach.
        from nesting_nfp import no_fit_polygon
        from shapely.geometry import LineString
        for prev in placed[-2:]:
            if monotonic()+.03>=deadline:break
            old=prev['shape']
            complexity=len(ref.exterior.coords)+len(old.exterior.coords)
            if complexity>35:continue
            try:
                obstacle=affinity.translate(old,xoff=-old.bounds[0],yoff=-old.bounds[1])
                forb=no_fit_polygon(obstacle,ref).buffer(gap+0.012,join_style=2)
                parts=[forb] if forb.geom_type=='Polygon' else getattr(forb,'geoms',[])
                for poly in parts:
                    if poly.geom_type!='Polygon':continue
                    for ring in (poly.exterior,*poly.interiors):
                        line=LineString(ring.coords)
                        for j in range(20):
                            pt=line.interpolate(j*line.length/20)
                            # Slight outward nudges; exact gap validation follows.
                            contacts.add((pt.x+old.bounds[0],pt.y+old.bounds[1]))
            except Exception:
                pass  # NFP candidates only: never bypass geometric verification.
    valid=[(round(x,5),round(y,5)) for x,y in contacts
           if margin-1e-6<=x<=xmax+1e-6 and margin-1e-6<=y<=ymax+1e-6]
    return list(dict.fromkeys(valid))


def _build(items, order, rotations, W,H,margin,gap,deadline, *,
           stock_outline=None, allow_holes=True, mode='horizontal', multi_sheet=False, nfp=False):
    if stock_outline is not None:
        usable=stock_outline.buffer(-margin)
    else:
        usable=box(margin-1e-7,margin-1e-7,W-margin+1e-7,H-margin+1e-7)
    if usable.is_empty:return None
    placed=[];unplaced=[];sid=0; sheets=[]
    indices=list(order)
    while indices:
        work=[];failed=[]
        for index in indices:
            if monotonic()>deadline: return None
            name,source=items[index]
            orientations=rotations[index]
            best=None
            for angle,ref in orientations:
                if monotonic()>deadline:break
                w,h=ref.bounds[2],ref.bounds[3]
                if w>W-2*margin+1e-7 or h>H-2*margin+1e-7:continue
                candidates=_positions(ref,work,W,H,margin,gap,allow_holes,deadline,nfp=nfp)
                # Do not truncate before seeing both axis extremes: interlocking
                # solutions can be far from the preferred bottom-left corner.
                ranked=sorted(candidates,key=lambda p:(p[0],p[1]) if mode=='horizontal'
                              else (p[1],p[0]) if mode=='vertical' else (p[0]+p[1],p[0]))
                # Don't burn the entire run checking hundreds of contacts
                # for each angle: keep strong anchor positions and stratified
                # unusual locations, then let the other decoders diversify.
                cap=145 if len(items)<=9 else 90
                if len(ranked)>cap:
                    first=ranked[:cap*3//4]
                    rest=ranked[cap*3//4:]
                    stride=max(1,len(rest)//max(1,cap-len(first)))
                    ranked=first+rest[::stride][:cap-len(first)]
                for k,(x,y) in enumerate(ranked):
                    if k%25==0 and monotonic()>deadline:break
                    right=max([x+w]+[p['shape'].bounds[2] for p in work])
                    top=max([y+h]+[p['shape'].bounds[3] for p in work])
                    if mode=='horizontal': score=(right,top,right*top,x+y)
                    elif mode=='vertical':score=(top,right,right*top,x+y)
                    else:score=(right*top,max(right,top),right,top,x+y)
                    if best is not None and score>=best[0]:continue
                    sh=affinity.translate(ref,xoff=x,yoff=y)
                    if not usable.covers(sh):continue
                    good=True
                    for prior in work:
                        a,b,c,d=prior['shape'].bounds
                        if x>=c+gap-1e-7 or x+w<=a-gap+1e-7 or y>=d+gap-1e-7 or y+h<=b-gap+1e-7:
                            continue
                        o=prior['shape']
                        if sh.intersection(o).area>1e-7 or (gap>0 and sh.distance(o)<gap-1e-6):
                            good=False;break
                    if good:best=(score,sh,x,y,angle)
            if best is None:
                failed.append(index)
            else:
                _,shape,x,y,angle=best
                work.append({'name':name,'shape':shape,'x':x,'y':y,'angle':angle,'sheet_index':sid})
        if not work:return None
        placed.extend(work);sheets.append((W,H))
        if failed and (not multi_sheet or stock_outline is not None):return None
        if failed and len(failed)==len(indices):return None
        indices=failed
        sid+=1
        if sid>len(items):return None
    result=_summarize(placed,sheets,margin,gap,'v3.2 hybrid contact + cluster LNS / material-first')
    if stock_outline is not None:
        result['stock_outline']=stock_outline
        result['sheet_mm2']=float(stock_outline.area)
        result['waste_mm2']=max(0.,result['sheet_mm2']-result['net_mm2'])
        result['utilization']=100*result['net_mm2']/result['sheet_mm2']
    if not _validate(result):return None
    return result


def _angles(items, step):
    res=[]
    for name,poly in items:
        vals=[];seen=set()
        for a in range(0,360,step):
            oriented=_orient(poly,a)
            key=oriented.wkb
            if key not in seen:
                seen.add(key);vals.append((a,oriented))
        res.append(vals)
    return res


def _orders(items,rng,seed_layout=None):
    n=len(items);ids=list(range(n))
    sizes=[(i,p.area,p.bounds[2]-p.bounds[0],p.bounds[3]-p.bounds[1],p.convex_hull.area-p.area)
           for i,(_,p) in enumerate(items)]
    orders=[tuple(i for i,*_ in sorted(sizes,key=lambda v:-v[1])),
            tuple(i for i,*_ in sorted(sizes,key=lambda v:-max(v[2],v[3]))),
            tuple(i for i,*_ in sorted(sizes,key=lambda v:-v[4])),
            tuple(i for i,*_ in sorted(sizes,key=lambda v:-v[2]*v[3])),
            tuple(i for i,*_ in sorted(sizes,key=lambda v:v[1]))]
    if seed_layout:
        names=[p['name'] for p in seed_layout['placements']]
        lookup={}
        for i,(nm,_) in enumerate(items):lookup.setdefault(nm,[]).append(i)
        prev=[]
        for nm in names:
            if nm in lookup and lookup[nm]:prev.append(lookup[nm].pop(0))
        if len(prev)==n:orders.insert(0,tuple(prev))
    return orders


def _mutate(order,rng):
    ids=list(order);n=len(ids)
    if n<2:return tuple(ids)
    a,b=rng.sample(range(n),2)
    typ=rng.randrange(4)
    if typ==0:ids[a],ids[b]=ids[b],ids[a]
    elif typ==1:
        x=ids.pop(a);ids.insert(b,x)
    elif typ==2:
        a,b=sorted((a,b));ids[a:b+1]=reversed(ids[a:b+1])
    else:
        # keep adjacent shapes together most of the time, but perturb globally
        a,b=sorted((a,b));ids[a:b+1]=rng.sample(ids[a:b+1],b-a+1)
    return tuple(ids)


def optimize(pieces,qty, *, W=None,H=None,margin=5.,gap=3.,auto=True,
             allow_multi=False, stock_outline=None,quality='balanced',
             budget=18.,hole_nesting=True,seed_layout=None,seed=47,on_checkpoint=None):
    """Return verified alternatives, best material-first first.

    A fast legal placement is calculated first; future trials never delete it.
    For irregular remnant stock the legacy packing function supplies a safe
    geometric fallback until a specialized polygon-stock decoder is available.
    """
    if margin<0 or gap<0:raise ValueError('Negative margin/gap')
    items=_input_items(pieces,qty)
    rng=random.Random(seed)
    start=monotonic();deadline=start+max(2.,float(budget))
    seen=set();solutions=[]
    def add(layout):
        if layout is None or not _validate(layout):return
        s=(layout['sheet_count'],round(layout['sheet_mm2'],3),tuple(
           (p['name'],p['sheet_index'],round(p['x'],2),round(p['y'],2),round(p['angle'],2)) for p in layout['placements']))
        if s not in seen:
            seen.add(s);solutions.append(layout)
            if on_checkpoint is not None: on_checkpoint(layout)
    if seed_layout is not None:add(seed_layout)
    # A bounded, conservative start is essential for 35-50 pieces: the more
    # expensive legacy fast stage may itself exceed the entire time budget.
    if len(items)>=16 and stock_outline is None:
        from nesting_shelf_v32 import shelf_start
        add(shelf_start(items,auto=auto,W=W,H=H,margin=margin,gap=gap,multi_sheet=allow_multi))
    try:
        # Decoder A: fast mature Shapely geometry placement. This gives a
        # checkpoint even if later, slower search stages hit the hard timeout.
        baseline=(advanced_nest(pieces,qty,stock_width=W,stock_height=H,margin=margin,gap=gap,
                       auto=auto,quality='fast',hole_nesting=hole_nesting,
                       multi_sheet=allow_multi,stock_outline=stock_outline,
                       preserve_remnants=False) if len(items)<=25 and monotonic()+2.0<deadline else None)
        add(baseline)
    except (ValueError,RuntimeError):
        pass
    # Decoder B: an independent, more exhaustive multi-start strategy already
    # in the project. Blend its best exact layout into the SAME material-first
    # portfolio. Limit invocation to small jobs with sufficient time remaining.
    # Worker-level hard timeout retains decoder A's verified checkpoint.
    if (seed_layout is None and quality in ('balanced','thorough','precision') and len(items)<=9
            and stock_outline is None and monotonic()+10.0 < deadline):
        from v31_balanced_worker import run as run_second_decoder
        remaining=deadline-monotonic()
        # The secondary engine is run in its OWN killable process: even very
        # costly DXF geometries cannot monopolize the time available to the
        # new local improvement search or lose the baseline checkpoint.
        second=run_second_decoder(dict(pieces=pieces,qty=qty,W=W,H=H,margin=margin,
                    gap=gap,auto=auto,hole_nesting=hole_nesting,allow_multi=allow_multi),
                    seconds=min(14.,max(1.,remaining*.83)))
        add(second)
    if not solutions and monotonic()>deadline:raise ValueError('No initial valid layout within time limit')
    if stock_outline is not None:
        return sorted(solutions,key=lambda x:_score(x,auto))[:6]
    n=len(items)
    if not n:return []
    rotations=_angles(items,90 if n>25 or quality in ('fast','balanced') else 45 if n>12 or quality=='thorough' else 30)
    baseorders=_orders(items,rng,solutions[0] if solutions else None)
    if auto:
        if solutions:
            base=min(solutions,key=lambda x:x['sheet_mm2'])
            W0,H0=base['sheet_width_mm'],base['sheet_height_mm']
        else:
            area=sum(p.area for _,p in items)
            dim=max(max(p.bounds[2]-p.bounds[0],p.bounds[3]-p.bounds[1]) for _,p in items)
            W0=H0=max(dim+2*margin,ceil(sqrt(area)*1.9)+2*margin)
        scenarios=[]
        for ratio in (1., .84,1.18, .70,1.40, .58,1.72):
            w=max(2*margin+max(min(ref.bounds[2] for _,ref in orientations) for orientations in rotations),W0*ratio)
            h=max(2*margin+max(min(ref.bounds[3] for _,ref in orientations) for orientations in rotations),H0/ratio)
            scenarios.append((max(w,1.),max(h,1.)))
        # Extra slack: possible improvement may require temporarily larger
        # search canvas; cropped final geometry is still charged exactly.
        scenarios.append((max(W0,H0)*1.2,max(W0,H0)*1.2))
    else:
        if W is None or H is None:raise ValueError('Fixed stock dimensions required')
        scenarios=[(float(W),float(H))]
    best=min(solutions,key=lambda l:_score(l,auto)) if solutions else None
    # v3.1: Improve the exact geometry of the incoming *manual* layout, not
    # just its piece order. This is a different search neighborhood from the
    # initial NFP/permutation construction above. Give it a bounded slice of
    # remaining runtime; never let it displace the saved valid incumbent.
    if best is not None and best['sheet_count']==1 and stock_outline is None and n<=50:
        from nesting_v32 import improve_seed
        seeds=[]
        if seed_layout is not None and _validate(seed_layout):
            seeds.append(seed_layout)
        if best is not seed_layout:seeds.append(best)
        local_limit=min(deadline-0.1,monotonic()+max(0.3,float(budget)*(.17 if seed_layout is not None else .40)))
        for j,starting in enumerate(seeds):
            if monotonic()+.15 >= local_limit:break
            portion=local_limit if j==len(seeds)-1 else min(local_limit,monotonic()+max(.25,(local_limit-monotonic())*.60))
            improved=improve_seed(starting,items,auto=auto,deadline=portion,
                       quality=quality,rng=rng,on_improved=add)
            add(improved)
            if _score(improved,auto)<_score(best,auto):best=improved
    # v3.3: exact contact-pressure refinement. This intentionally works on
    # verified full layouts and can continue a manual or imported seed, but
    # never pretends that manual-seeded wins were discovered from scratch.
    if best is not None and best['sheet_count']==1 and stock_outline is None and auto and n<=50:
        from nesting_v33 import improve_seed as pressure_improve
        remaining=deadline-monotonic()
        if remaining>.15:
            starting=[]
            if seed_layout is not None and _validate(seed_layout):
                starting.append(seed_layout)
            if best is not seed_layout:starting.append(best)
            # Manual sources get the larger budget because the critical task
            # is to surpass an already good human arrangement. For a fresh
            # run, save enough time for independent constructive decoders.
            amount=min(remaining-.07,max(.10,float(budget)*(.64 if seed_layout is not None else .24)))
            end=min(deadline-.05,monotonic()+amount)
            for j,source_layout in enumerate(starting):
                if monotonic()+.06>=end:break
                subend=end if j==len(starting)-1 else min(end,monotonic()+max(.12,(end-monotonic())*.83))
                improved=pressure_improve(source_layout,deadline=subend,quality=quality,
                       random_seed=seed+j+3101,on_improved=add)
                add(improved)
                if _score(improved,auto)<_score(best,auto):best=improved
    champion=baseorders[0]
    trials=0
    while n<=35 and monotonic()<deadline-.03:
        if trials<len(baseorders):order=baseorders[trials]
        elif trials%9==0:order=tuple(rng.sample(range(n),n))
        else:order=_mutate(champion if rng.random()<.80 else rng.choice(baseorders),rng)
        if auto:
            if best is not None and trials%3==0:
                bw,bh=best['sheet_width_mm'],best['sheet_height_mm']
                ratio=rng.choice((0.80,0.92,1.,1.08,1.24))
                w,h=bw*ratio*1.025,bh/ratio*1.025
            else:w,h=scenarios[trials%len(scenarios)]
        else:w,h=scenarios[0]
        mode=('horizontal','vertical','compact')[trials%3]
        layout=_build(items,order,rotations,w,h,margin,gap,deadline,
                      allow_holes=hole_nesting,mode=mode,multi_sheet=allow_multi,
                      nfp=(n<=16 and trials%3==1))
        trials+=1
        if layout is None:continue
        if auto:
            tightened,dimensions=_tight_auto_result(layout['placements'],margin)
            layout=_summarize(tightened,[dimensions],margin,gap,'v3 NFP contacts + multi-start / Platz sparen')
            if not _validate(layout):continue
        add(layout)
        if best is None or _score(layout,auto)<_score(best,auto):
            best=layout;champion=order
    # Top by actual material use, then distinct shape layouts. A handful of
    # alternatives remain available for manual adjustments and comparison.
    return sorted(solutions,key=lambda l:_score(l,auto))[:8]
