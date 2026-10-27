"""Nesting Engine 2.0: bounded, multi-objective *global rebuild* for irregular DXF polygons.

Design goals: unlike local-only optimization, destroy and reconstruct the entire
layout; keep original and manual seeds unmodified; separate cheap search ranking
from expensive verified remnant metrics; maintain a diverse, Pareto portfolio.

All proposed placements are independently checked against ORIGINAL Shapely
contours and the sheet boundary. Not a globally optimal solver or certified CAM.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil
from itertools import combinations
from time import monotonic
import random
from shapely import affinity
from shapely.geometry import box, Point
from shapely.ops import unary_union
from nesting_advanced import _summarize
from nesting_refinement import _valid
from sheet_nesting import _orient
from nesting_nfp import nfp_contact_candidates


@dataclass(frozen=True)
class SearchOptions:
    quality: str = 'balanced'
    time_limit_s: float = 10.
    beam_width: int = 5
    max_per_step: int = 7
    max_candidates: int = 76
    seed: int = 1701


def _clone(rows):
    return [dict(p) for p in rows]


def _signature(rows):
    return tuple((p['name'], round(p['shape'].bounds[0],1),
                  round(p['shape'].bounds[1],1), round(p.get('angle',0)%360,1)) for p in rows)


def _dimensions(rows, margin):
    xs=[p['shape'].bounds for p in rows]
    return (ceil(max(b[2] for b in xs)-min(b[0] for b in xs)+2*margin),
            ceil(max(b[3] for b in xs)-min(b[1] for b in xs)+2*margin))


def _crop(rows,margin):
    dx=margin-min(p['shape'].bounds[0] for p in rows)
    dy=margin-min(p['shape'].bounds[1] for p in rows)
    out=[]
    for p in rows:
        sh=affinity.translate(p['shape'],xoff=dx,yoff=dy)
        out.append(dict(p,shape=sh,x=sh.bounds[0],y=sh.bounds[1]))
    return out,_dimensions(out,margin)


def _validate(rows,W,H,margin,gap):
    if not rows:return False
    available=box(margin-1e-7,margin-1e-7,W-margin+1e-7,H-margin+1e-7)
    checked=[]
    for p in rows:
        s=p['shape']
        if not s.is_valid or s.is_empty or not _valid(s,checked,gap,available):return False
        checked.append(p)
    return True


def _result(seed,rows,auto,algorithm='Nesting Engine 2.0 / global destroy-repair + beam'):
    if auto:rows,dimensions=_crop(rows,float(seed['margin_mm']))
    else:dimensions=(seed['sheets'][0]['width_mm'],seed['sheets'][0]['height_mm'])
    output=_summarize(_clone(rows),[dimensions],float(seed['margin_mm']),float(seed['gap_mm']),algorithm)
    output['search_objective']=seed.get('search_objective','multi_objective')
    output['engine_2']=True
    return output


def _rects_gap_free(parts,W,H,margin,gap,grid=14):
    """Fast and bounded polygonal occupancy-based *approximate* useful scrap.

    True polygons (not bboxes) determine cell occupancy; final candidate
    metrics are recomputed by remnant_portfolio with geometric containment.
    """
    inner=box(margin,margin,W-margin,H-margin)
    if inner.is_empty:return 0.,0.,0.
    obstacles=[p['shape'].buffer(gap/2,quad_segs=3) for p in parts]
    free=inner.difference(unary_union(obstacles)) if obstacles else inner
    if free.is_empty:return 0.,0.,0.
    pieces=[free] if free.geom_type=='Polygon' else [g for g in getattr(free,'geoms',()) if g.geom_type=='Polygon']
    linked=max((p.area for p in pieces),default=0.)
    x0,y0,x1,y1=inner.bounds
    cw=(x1-x0)/grid;ch=(y1-y0)/grid
    used=[[free.covers(box(x0+x*cw+1e-5,y0+y*ch+1e-5,
                          x0+(x+1)*cw-1e-5,y0+(y+1)*ch-1e-5)) for x in range(grid)] for y in range(grid)]
    rects=[]
    for _ in range(3):
        max_rect=(0,0,0,0,0)
        heights=[0]*grid
        for y in range(grid):
            for x in range(grid):heights[x]=heights[x]+1 if used[y][x] else 0
            stack=[]
            for x in range(grid+1):
                height=heights[x] if x<grid else 0;start=x
                while stack and stack[-1][1]>height:
                    sx,hh=stack.pop();start=sx
                    if (x-sx)*hh>max_rect[0]:max_rect=((x-sx)*hh,sx,x,y-hh+1,y+1)
                if not stack or stack[-1][1]<height:stack.append((start,height))
        a,x0i,x1i,y0i,y1i=max_rect
        if a==0:break
        for y in range(y0i,y1i):
            for x in range(x0i,x1i):used[y][x]=False
        rects.append(a*cw*ch)
    return (max(rects,default=0.),sum(rects),linked)


def _compact_score(rows,margin):
    # This is evaluated on partial layouts: scale-free geometry, not anchored
    # in one corner, so a promising future position is not discarded merely
    # because it is currently away from bottom-left.
    a=min(p['shape'].bounds[0] for p in rows);b=min(p['shape'].bounds[1] for p in rows)
    c=max(p['shape'].bounds[2] for p in rows);d=max(p['shape'].bounds[3] for p in rows)
    return (c-a+2*margin)*(d-b+2*margin)


def _part_order(rows,mode,seed):
    rng=random.Random(seed)
    keys=list(range(len(rows)))
    if mode==0:keys.sort(key=lambda i:-rows[i]['shape'].area)
    elif mode==1:keys.sort(key=lambda i:-(rows[i]['shape'].convex_hull.area-rows[i]['shape'].area))
    elif mode==2:keys.sort(key=lambda i:-_span(rows[i]['shape']))
    elif mode==3:keys.sort(key=lambda i:rows[i]['shape'].area)
    else:rng.shuffle(keys)
    return keys


def _span(poly):
    a,b,c,d=poly.bounds
    return max(c-a,d-b)


def _angles(part,quality,phase):
    start=float(part.get('angle',0))%360
    base=[start,(start+180)%360,(start+90)%360,(start+270)%360]
    if quality in ('thorough','precision'):
        base.extend((start+a)%360 for a in (45,135,225,315))
    if quality=='precision':base.extend((start+a)%360 for a in (15,30,60,75,105,120))
    # For each step sample all quadrants fairly, including rotations; do not
    # truncate an angles-by-positions list before seeing later orientations.
    return list(dict.fromkeys(base if phase%2==0 else list(reversed(base))))


def _normalize(part,angle,cache):
    key=(id(part['shape']),round(angle,2),round(float(part.get('angle',0)),2))
    if key not in cache:
        cache[key]=_orient(part['shape'],angle-float(part.get('angle',0)))
    return cache[key]


def _candidate_positions(ref,placed,W,H,margin,gap,limit,enable_nfp=False):
    w,h=ref.bounds[2],ref.bounds[3]
    highx=W-margin-w;highy=H-margin-h
    if highx<margin-1e-6 or highy<margin-1e-6:return []
    coords={(margin,margin),(highx,margin),(margin,highy),(highx,highy),
            ((margin+highx)/2,(margin+highy)/2)}
    cavity_priority=[]
    # Align bounding-box edges of both polygons in different orientations.
    for prior in placed:
        a,b,c,d=prior['shape'].bounds
        # A dedicated cavity quota avoids losing valid nested candidates when
        # _diverse_candidates truncates a large set of bbox/vertex contacts.
        from pocket_repair import _regions,_grid_points
        for cavity in _regions(prior['shape']):
            if cavity.area<=ref.area:continue
            for candidate in _grid_points(cavity,ref,gap,steps=5):
                px,py=candidate
                if margin-1e-7<=px<=highx+1e-7 and margin-1e-7<=py<=highy+1e-7:
                    cavity_priority.append((round(px,6),round(py,6)))
        xs=[a-w-gap,a-w,c+gap,c-w,c,c-w-gap,margin,highx]
        ys=[b-h-gap,b-h,d+gap,d-h,d,d-h-gap,margin,highy]
        for x in xs[:6]:
            for y in (margin,highy,b,d+gap,d-h):coords.add((x,y))
        for y in ys[:6]:
            for x in (margin,highx,a,c+gap,c-w):coords.add((x,y))
        # Exact contour vertex contacts expose nesting within concavities that
        # bounding-box-only placement misses.  Gap is checked later on originals.
        outlines=[list(prior['shape'].exterior.coords)[:-1]]
        outlines.extend(list(r.coords)[:-1] for r in prior['shape'].interiors)
        newrings=[list(ref.exterior.coords)[:-1]]
        for r in ref.interiors:newrings.append(list(r.coords)[:-1])
        for aa in outlines:
            pa=aa[::max(1,len(aa)//8)][:9]
            for bb in newrings:
                pb=bb[::max(1,len(bb)//7)][:8]
                for ox,oy in pa:
                    for nx,ny in pb:
                        for dx,dy in ((gap,0),(-gap,0),(0,gap),(0,-gap)):
                            coords.add((ox-nx+dx,oy-ny+dy))
    def complexity(poly):
        return len(poly.exterior.coords) + sum(len(r.coords) for r in poly.interiors)
    # Minkowski decomposition of long circular/polyline boundaries may be
    # catastrophically expensive. Use sampled contours for these, never NFP.
    if enable_nfp and placed and complexity(ref)<=42 and all(
            complexity(p['shape'])<=42 for p in placed[-2:]):
        try:
            # Use authentic no-fit geometry for candidate discovery; acceptance
            # still depends on precise, unsimplified source geometry.
            for x,y in nfp_contact_candidates(placed[-2:],ref,gap,max_old=2,max_per_pair=22):
                coords.add((x,y))
        except Exception:pass
    candidates=[(round(x,6),round(y,6)) for x,y in coords
          if margin-1e-7<=x<=highx+1e-7 and margin-1e-7<=y<=highy+1e-7]
    candidates=list(dict.fromkeys(candidates))
    if len(candidates)<=limit:return candidates
    # Reserve part of the budget for cavity candidates; never rely solely on
    # bbox scoring and a generic spatial-stratified truncation.
    guaranteed=[]
    for candidate in cavity_priority:
        if candidate in candidates and candidate not in guaranteed:
            guaranteed.append(candidate)
        if len(guaranteed)>=max(3,limit//4):break
    # Stratified sampling: keep extremities of each axis, holes near the center,
    # and contact points throughout the sheet; NEVER just the first sorted K.
    from nesting_refinement import _diverse_candidates
    remaining=_diverse_candidates([p for p in candidates if p not in guaranteed],
                                 max(1,limit-len(guaranteed)),W,H)
    return guaranteed+remaining


def _expand_one(part,placed,W,H,margin,gap,angles,limit,step,cache,nfp=False,deadline=None):
    stock=box(margin-1e-7,margin-1e-7,W-margin+1e-7,H-margin+1e-7)
    found=[];keys=set()
    for angle in angles:
        if deadline is not None and monotonic()>deadline:break
        ref=_normalize(part,angle,cache)
        points=_candidate_positions(ref,placed,W,H,margin,gap,limit,enable_nfp=nfp)
        choices=[]
        for jj,(x,y) in enumerate(points):
            if jj%12==0 and deadline is not None and monotonic()>deadline:break
            sh=affinity.translate(ref,xoff=x,yoff=y)
            if not _valid(sh,placed,gap,stock):continue
            obj=dict(part,shape=sh,x=x,y=y,angle=angle)
            # Prefer candidate with low envelope, but retain diverse corners
            # across rotations. This is partial-score not final objective.
            envelope=_compact_score(placed+[obj],margin)
            choices.append((envelope,obj))
        choices.sort(key=lambda v:v[0])
        for score,obj in choices[:max(2,step)]:
            key=(round(obj['x'],1),round(obj['y'],1),round(obj['angle'],1))
            if key not in keys:keys.add(key);found.append((score,obj))
    found.sort(key=lambda v:v[0])
    return found


def _reassemble(rows,indices,placed):
    result=[dict(p) for p in rows]
    for i,entry in zip(indices,placed):result[i]=entry
    return result


def _diverse_expansions(found, cap):
    """Preserve rotations and spatially distinct candidates BEFORE beam pruning.

    Previous version sliced the first K items sorted by the smallest current
    envelope; that suppressed alternative orientations early in the search.
    """
    if len(found) <= cap:
        return found
    output=[]; used=set()
    groups={}
    for score,obj in found:
        angle=int(round(float(obj.get('angle',0)) / 15))
        groups.setdefault(angle,[]).append((score,obj))
    def include(item):
        score,obj=item
        key=(round(obj['x'],2),round(obj['y'],2),round(obj.get('angle',0),2))
        if key not in used and len(output)<cap:
            used.add(key);output.append(item)
    # Every orientation has an opportunity, not just the first few sorted by area.
    for key in sorted(groups):
        include(min(groups[key],key=lambda item:item[0]))
    # Preserve extreme positions in both axes and several rotation classes.
    for order in (lambda item:item[1]['x'], lambda item:-item[1]['x'],
                  lambda item:item[1]['y'], lambda item:-item[1]['y'],
                  lambda item:item[0]):
        for item in sorted(found,key=order)[:2]: include(item)
    for item in found: include(item)
    return output


def _solve_order(rows,order,W,H,margin,gap,options,deadline,cache,keep_fixed=(),nfp=False):
    fixed=[dict(rows[i]) for i in range(len(rows)) if i in keep_fixed]
    if not _validate(fixed,W,H,margin,gap) and fixed:return []
    beam=[(fixed,[])]
    width=options.beam_width
    for stage,index in enumerate(order):
        if monotonic()>deadline:return []
        all_next=[]
        part=rows[index]
        angles=_angles(part,options.quality,stage)
        for current,created in beam:
            options_found=_expand_one(part,current,W,H,margin,gap,angles,
                            options.max_candidates,options.max_per_step,cache,
                            nfp=(nfp and stage>0 and stage%2==0),deadline=deadline)
            for score,obj in _diverse_expansions(options_found,min(22,max(12,options.max_per_step*3))):
                newer=current+[obj]
                all_next.append(((_compact_score(newer,margin),score),newer,created+[(index,obj)]))
        if not all_next:return []
        # Keep solutions of different geometry signatures; progressive beam
        # pruning means this is not complete search, but it avoids identical
        # branches consuming the entire beam.
        # Beam diversity is essential: pure bbox pruning repeatedly converges
        # to the same placement and discards configurations allowing later
        # complex interlocking. Reserve beam slots for different aspect ratios,
        # orientations and spatial clusters, not just the lowest partial score.
        def bbox(a):
            pts=[p['shape'].bounds for p in a[1]]
            x0=min(p[0] for p in pts);y0=min(p[1] for p in pts)
            return max(p[2] for p in pts)-x0,max(p[3] for p in pts)-y0
        ranks=[lambda a:a[0],lambda a:(bbox(a)[0],a[0]),
               lambda a:(bbox(a)[1],a[0]),
               lambda a:(abs(bbox(a)[0]-bbox(a)[1]),a[0]),
               lambda a:(-bbox(a)[0],a[0]),
               lambda a:(-bbox(a)[1],a[0])]
        groups=[sorted(all_next,key=ranking) for ranking in ranks]
        beam=[];sigs=set();positions=[0]*len(groups)
        while len(beam)<width:
            chosen=False
            for group_index,group in enumerate(groups):
                while positions[group_index]<len(group):
                    score,accum,created=group[positions[group_index]]
                    positions[group_index]+=1
                    sig=tuple((int(i),round(o['x']/2),round(o['y']/2),round(o['angle'])) for i,o in created)
                    if sig in sigs:continue
                    sigs.add(sig);beam.append((accum,created));chosen=True
                    break
                if len(beam)>=width:break
            if not chosen:break
    results=[]
    for _placed,created in beam:
        built=_reassemble(rows,[i for i,_ in created],[p for _,p in created])
        if _validate(built,W,H,margin,gap):results.append(built)
    return results


def _move_cluster(rows,W,H,margin,gap):
    # Rigidly translate and rotate ALL parts, including manual seeds, preserving
    # relative polygon positions, dimensions and gaps.
    center=unary_union([p['shape'] for p in rows]).centroid
    for angle in (90,180,270):
        turn=[dict(p,shape=affinity.rotate(p['shape'],angle,origin=(center.x,center.y)),
                   angle=(float(p.get('angle',0))+angle)%360) for p in rows]
        for dx,dy in ((margin-min(p['shape'].bounds[0] for p in turn),
                       margin-min(p['shape'].bounds[1] for p in turn)),):
            out=[]
            for p in turn:
                s=affinity.translate(p['shape'],xoff=dx,yoff=dy)
                out.append(dict(p,shape=s,x=s.bounds[0],y=s.bounds[1]))
            if _validate(out,W,H,margin,gap):yield out


def _polish(rows,W,H,margin,gap,rounds=2):
    """Safe deterministic post-packing compaction (keeps all original contours).

    Repeatedly shift different parts toward an actual contact, always retaining
    the original result as fallback. Acceptance uses the full-group envelope.
    """
    from nesting_v16 import _contact_slide
    result=_clone(rows)
    usable=box(margin,margin,W-margin,H-margin)
    def value(placed):
        # Even if the global bounding rectangle is unchanged, moving a small
        # part away from a valuable outer strip improves the anchor compactness.
        # Exact scrap metrics are evaluated *later* across whole legal layouts.
        return (_compact_score(placed,margin),
                sum(p['shape'].bounds[0]+p['shape'].bounds[1] for p in placed))
    for round_no in range(rounds):
        moved=False
        order=sorted(range(len(result)),key=lambda i:result[i]['shape'].area,
                     reverse=bool(round_no%2))
        for idx in order:
            row=result[idx]
            others=[v for i,v in enumerate(result) if i!=idx]
            best=row;best_score=value(result)
            for vx,vy in ((-1,0),(0,-1),(1,0),(0,1),(-1,-1)):
                sh=_contact_slide(row['shape'],others,usable,gap,vx,vy,steps=7)
                trial=_clone(result)
                trial[idx]=dict(row,shape=sh,x=sh.bounds[0],y=sh.bounds[1])
                score=value(trial)
                if score[0]<best_score[0]-.01 or (abs(score[0]-best_score[0])<.01 and score[1]<best_score[1]-.01):
                    best=trial[idx];best_score=score
            if best is not row:result[idx]=best;moved=True
        if not moved:break
    return result


def _area_score(layout):
    return (layout['sheet_count'],layout['sheet_mm2'])


def _pareto(layouts,margin,gap,auto,limit=14):
    """Pareto diverse archive using accurate final (not cheap intermediate) metrics."""
    from remnant_portfolio import metrics_summary
    # Computing several largest polygons and multiple verified rectangles is
    # O(grid² * contours).  Rank CHEAPLY and evaluate exact remnants only on a
    # small, diverse finalist pool, rather than hundreds of beam intermediates.
    distinct=[];seen=set()
    for lay in layouts:
        key=_signature(lay['placements'])
        if key not in seen:seen.add(key);distinct.append(lay)
    if len(distinct)>8:
        by_area=sorted(distinct,key=lambda x:x['sheet_mm2'])
        by_compact=sorted(distinct,key=lambda x:_compact_score(x['placements'],margin))
        by_width=sorted(distinct,key=lambda x:x['sheet_width_mm'])
        by_height=sorted(distinct,key=lambda x:x['sheet_height_mm'])
        shortlist=[];short_seen=set()
        for family in (by_area[:3],by_compact[:3],by_width[:2],by_height[:2],distinct[:2]):
            for item in family:
                ident=_signature(item['placements'])
                if ident not in short_seen:
                    short_seen.add(ident);shortlist.append(item)
                if len(shortlist)>=8:break
            if len(shortlist)>=8:break
        distinct=shortlist
    enriched=[]
    for lay in distinct:
        try:
            m=metrics_summary(lay,grid=9)
        except Exception:continue
        enriched.append((lay,m))
    if not enriched:return []
    best=min(enriched,key=lambda e:_area_score(e[0]))
    # Prevent an unusually huge sheet from being marketed as a useful remnant
    # victory. Preserve manual seeds separately through the app's catalog.
    bestarea=best[0]['sheet_mm2']
    feasible=[e for e in enriched if e[0]['sheet_count']==best[0]['sheet_count'] and
               (not auto or e[0]['sheet_mm2']<=bestarea*1.07+1e-8)]
    winners=[]
    def add(e):
        if all(_signature(e[0]['placements'])!=_signature(old[0]['placements']) for old in winners):winners.append(e)
    add(best)
    for key in (lambda e:e[1]['top_rectangles_total_mm2'],
                lambda e:e[1]['largest_connected_mm2'],
                lambda e:e[1]['largest_rect_mm2']):
        add(max(feasible,key=lambda e:(key(e),-e[0]['sheet_mm2'])))
    for e in sorted(feasible,key=lambda e:(-e[1]['top_rectangles_total_mm2'],e[0]['sheet_mm2'])):
        if len(winners)>=limit:break
        add(e)
    return winners


def search_engine2(seed_layouts,auto,quality='balanced',*,time_budget_s=None,seed=1701,allow_nesting=True,return_all=False,strategy='mixed'):
    """Produce legal alternative layouts from existing or manually-edited seeds.

    n<=8, single rectangular sheet. Source and manually supplied solutions stay
    unchanged in the surrounding archive. Results are never assumed certified.
    """
    sources=[l for l in seed_layouts if l and l['sheet_count']==1 and l.get('stock_outline') is None]
    if not sources:return []
    n=len(sources[0]['placements'])
    if n<2 or n>8:return []
    if quality=='fast':return []
    if strategy not in ('mixed','constructive','contacts','reshuffle'):
        raise ValueError('Unknown Engine 2 strategy')
    # Do not reuse cached geometric transforms for distinct seed geometries.
    budget={'balanced':7.,'thorough':13.,'precision':20.}.get(quality,7.)
    if time_budget_s is not None:budget=max(.1,float(time_budget_s))
    started=monotonic()
    deadline=started+budget
    beam={'balanced':5,'thorough':7,'precision':9}.get(quality,5)
    opts=SearchOptions(quality=quality,time_limit_s=budget,beam_width=beam,
          max_per_step=4 if quality=='balanced' else 6,
          max_candidates=45 if quality=='balanced' else 80,seed=seed)
    # Use layouts as warm starts; retain both compact and scrap-optimized baselines.
    base=min(sources,key=lambda x:x['sheet_mm2'])
    margin=float(base['margin_mm']);gap=float(base['gap_mm'])
    W=float(base['sheets'][0]['width_mm']);H=float(base['sheets'][0]['height_mm'])
    if auto:W*=1.22;H*=1.22
    cache={};collected=[];seen=set()
    def record(rows,origin):
        if not _validate(rows,W,H,margin,gap):return
        candidate=_result(base,rows,auto,origin)
        if not allow_nesting and any('nested_in' in p for p in candidate['placements']):return
        k=_signature(candidate['placements'])
        if k not in seen:
            seen.add(k);collected.append(candidate)
    # Seeds and rigid global transformations should never be thrown away.
    for src in sources:
        if not _validate(src['placements'],W,H,margin,gap):continue
        record(src['placements'],'warm start / unchanged')
        if monotonic()<deadline:
            record(_polish(src['placements'],W,H,margin,gap,rounds=2), 'Engine 2 warm-start contact compaction')
        for rows in _move_cluster(src['placements'],W,H,margin,gap):record(rows,'rigid global rotation')
    rng=random.Random(seed)
    # Destroy all 7 objects and rebuild in varied orders; this can discover the
    # purple/red rearrangement that no single-part greedy step can achieve.
    orders=[0,1,2,3,4,4,4,4]
    methods=('constructive', 'full_group', 'partial_group')
    for phase in range(16 if quality!='balanced' else 10):
        if monotonic()>deadline:break
        src=sources[phase%len(sources)] if phase<4 else (collected[phase%len(collected)] if collected else base)
        rows=_clone(src['placements'])
        mode=(orders[phase%len(orders)] if strategy=='mixed' else
              (0 if strategy=='constructive' else 1 if strategy=='contacts' else 4))
        full_order=_part_order(rows,mode,seed+phase*17)
        if (strategy=='reshuffle' and phase%2==0 and phase>0) or (phase>=2 and phase%3==2):
            # Adaptive ruin-and-recreate: preserve a small anchor subset;
            # reconstruct the remaining 4-7 parts, including rotations.
            anchors=full_order[:max(0,n-(4 if n<=6 else 5))]
            indices=[i for i in full_order if i not in anchors]
            trials=_solve_order(rows,indices,W,H,margin,gap,opts,deadline,cache,
                               keep_fixed=anchors,nfp=(strategy=='contacts' or phase%4==2))
        else:
            trials=_solve_order(rows,full_order,W,H,margin,gap,opts,deadline,cache,
                               nfp=(strategy=='contacts' or phase%4==1))
        for result in trials:
            record(result,'Engine 2 global rebuild / diverse beam')
            if monotonic()<deadline:
                record(_polish(result,W,H,margin,gap,rounds=2),'Engine 2 global rebuild + contact compaction')
        # Occasionally use a different sheet aspect ratio from the last best
        # solution in AUTO mode. This helps escape fixed-dimension bias.
        if auto and phase in (3,7) and monotonic()<deadline:
            candidates=sorted(collected,key=lambda x:x['sheet_mm2'])[:2]
            for lay in candidates:
                oldw,oldh=_dimensions(lay['placements'],margin)
                for x,y in ((oldw*1.035,oldh*1.1),(oldw*1.1,oldh*1.035)):
                    if x>W or y>H:continue
                    alt=_solve_order(lay['placements'],_part_order(lay['placements'],4,seed+phase),
                                    x,y,margin,gap,opts,deadline,cache)
                    for r in alt:record(r,'Engine 2 adaptive stock aspect')
    if return_all:
        # Keep a diverse, inexpensive-to-evaluate frontier for the next phase.
        # This is deliberately not the full exact remnant-analysis pass.
        if not collected: return []
        families=[sorted(collected,key=lambda a:a['sheet_mm2']),
                  sorted(collected,key=lambda a:a['sheet_width_mm']),
                  sorted(collected,key=lambda a:a['sheet_height_mm']),
                  sorted(collected,key=lambda a:_compact_score(a['placements'],margin)),
                  list(reversed(collected))]
        frontier=[];frontier_keys=set()
        for family in families:
            for item in family[:5]:
                sig=_signature(item['placements'])
                if sig not in frontier_keys:
                    frontier_keys.add(sig);frontier.append(item)
        return frontier[:25]
    best=_pareto(collected,margin,gap,auto,limit=8)
    for lay,metrics in best:
        lay['engine_2_trials']=len(collected)
        lay['engine_2_search_seconds']=round(monotonic()-started,2)
    return [lay for lay,metrics in best]
