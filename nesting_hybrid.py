"""Bounded NFP + beam search + large-neighborhood re-packing for small DXF jobs.

A deterministic heuristic. The initial v1.3 layout is retained as a guaranteed
fallback; candidates must pass exact Shapely polygon collision/clearance tests.
Sheet auto-cropping recalculates part anchors and material dimensions.
"""
from __future__ import annotations
from itertools import combinations
from math import ceil
from shapely import affinity
from shapely.geometry import box
from sheet_nesting import _orient
from nesting_nfp import nfp_contact_candidates
from remnant_polygonal import largest_polygonal_free_rectangle
from nesting_refinement import _candidate_xy,_diverse_candidates,_valid


def _crop(rows,margin):
    lo_x=min(p['shape'].bounds[0] for p in rows)
    lo_y=min(p['shape'].bounds[1] for p in rows)
    out=[]
    for p in rows:
        sh=affinity.translate(p['shape'],xoff=margin-lo_x,yoff=margin-lo_y)
        out.append(dict(p,shape=sh,x=sh.bounds[0],y=sh.bounds[1]))
    return out,(ceil(max(p['shape'].bounds[2] for p in out)+margin),
                ceil(max(p['shape'].bounds[3] for p in out)+margin))


def _key(rows):
    return tuple((round(p['shape'].bounds[0],1),round(p['shape'].bounds[1],1),
                  round(float(p.get('angle',0)),1)) for p in rows)


def _quick(rows,W,H,margin,preserve):
    a=min(p['shape'].bounds[0] for p in rows)
    b=min(p['shape'].bounds[1] for p in rows)
    c=max(p['shape'].bounds[2] for p in rows)
    d=max(p['shape'].bounds[3] for p in rows)
    footprint=(c-a)*(d-b)
    strip=max((W-margin-c)*(H-2*margin),(H-margin-d)*(W-2*margin),0.)
    return (-strip,footprint) if preserve else (footprint, c+d)


def _objective(rows,W,H,margin,gap,preserve,auto,max_area):
    if auto:
        rows,(w,h)=_crop(rows,margin)
    else:w,h=W,H
    area=w*h
    if auto and preserve and area>max_area+1e-6:return None
    if preserve:
        _,r,free,fragments=largest_polygonal_free_rectangle(rows,w,h,margin=margin,clearance=gap/2,
                                                        grid=20)
        # A large empty rectangle is a strong but not exclusive remnant metric.
        return (-round(r,3),-round(free,3),fragments,area),rows,(w,h),r,free
    bounds=[p['shape'].bounds for p in rows]
    footprint=(max(v[2] for v in bounds)-min(v[0] for v in bounds))*\
              (max(v[3] for v in bounds)-min(v[1] for v in bounds))
    return ((area,footprint) if auto else (footprint,area)),rows,(w,h),0.,0.


def _insert_candidates(part,placed,W,H,margin,gap,angles,quality,preserve,stock,limit=3):
    """Find a few diverse legal positions for one removed part."""
    results=[]
    normal_angle=float(part.get('angle',0))
    angles=(normal_angle,)+tuple(a for a in angles if a!=normal_angle)
    # NFP triangulation is the most expensive stage. Per-pair limit keeps
    # the routine interactive on a 6-8-part DXF.
    for ai,angle in enumerate(angles):
        ref=_orient(part['shape'],angle-normal_angle)
        width,height=ref.bounds[2],ref.bounds[3]
        if width>W-2*margin or height>H-2*margin:continue
        max_x=W-margin-width;max_y=H-margin-height
        pts=_candidate_xy(placed,ref,W,H,margin,gap,level=1)
        # Generate a real polygonal NFP for the first few selected orientations;
        # any resulting anchor is checked against the ORIGINAL unsimplified parts.
        if placed and ai==0 and len(placed)<=3 and quality!='fast':
            try:
                nfp_pts=nfp_contact_candidates(placed,ref,gap,max_old=1,max_per_pair=12)
                pts=nfp_pts+pts
            except Exception:
                # Robust fallback to validated contact candidates for malformed
                # DXF geometry, without silently authorizing an invalid result.
                pass
        old_pos=(round(part['shape'].bounds[0],4),round(part['shape'].bounds[1],4))
        pts=[old_pos,(margin,margin),(max_x,margin),(margin,max_y),(max_x,max_y)]+pts
        pts=[pt for pt in dict.fromkeys((round(x,4),round(y,4)) for x,y in pts)
             if margin-1e-6<=pt[0]<=max_x+1e-6 and margin-1e-6<=pt[1]<=max_y+1e-6]
        pts=_diverse_candidates(pts,55 if quality=='precision' else 34,W,H,keep=old_pos)
        for x,y in pts:
            shape=affinity.translate(ref,xoff=x,yoff=y)
            if not _valid(shape,placed,gap,stock):continue
            new=dict(part,shape=shape,x=x,y=y,angle=angle%360)
            provisional=placed+[new]
            rank=_quick(provisional,W,H,margin,preserve)
            results.append((rank,new))
    results.sort(key=lambda r:r[0]);picked=[];seen=set()
    for _,obj in results:
        sig=(round(obj['x'],1),round(obj['y'],1),round(obj['angle'],1))
        if sig in seen:continue
        seen.add(sig);picked.append(obj)
        if len(picked)>=limit:break
    return picked


def _neighborhoods(rows,step):
    n=len(rows)
    if n<2:return []
    # Geometry-near pairs, large-part pairs and a changing distant pair.
    centers=[p['shape'].centroid for p in rows]
    close=[]
    for a,b in combinations(range(n),2):
        d=centers[a].distance(centers[b]);close.append((d,a,b))
    close.sort()
    pairlist=[(a,b) for _,a,b in close[:2]]
    size=sorted(range(n),key=lambda i:rows[i]['shape'].area,reverse=True)
    pairlist.append(tuple(sorted((size[step%len(size)],size[-1]))))
    pairlist.append(tuple(sorted(((step*3)%n,(step*3+max(1,n//2))%n))))
    if n>=5 and step%2:
        a,b=close[0][1:]
        c=next(i for i in size if i not in (a,b))
        pairlist.append(tuple(sorted((a,b,c))))
    return list(dict.fromkeys(tuple(sorted(set(p))) for p in pairlist if len(set(p))>1))[:4]


def _repack(rows,indices,W,H,margin,gap,angles,quality,preserve,branch=2):
    group=[rows[i] for i in indices]
    fixed=[p for i,p in enumerate(rows) if i not in indices]
    stock=box(margin-1e-7,margin-1e-7,W-margin+1e-7,H-margin+1e-7)
    orderings=[sorted(group,key=lambda p:p['shape'].area,reverse=True),
               sorted(group,key=lambda p:p['shape'].area)]
    outputs=[]
    for order in orderings:
        partial=[(fixed,[])]
        for part in order:
            nxt=[]
            for existing,placed_group in partial:
                choices=_insert_candidates(part,existing,W,H,margin,gap,angles,quality,preserve,stock,limit=branch+1)
                for candidate in choices:
                    nxt.append((existing+[candidate],placed_group+[candidate]))
            if not nxt:break
            nxt.sort(key=lambda v:_quick(v[0],W,H,margin,preserve))
            partial=nxt[:branch]
        else:
            for existing,_ in partial:
                # Restore input identity/order so manual editor and nesting IDs
                # are stable. Names are not guaranteed unique, so use geometry
                # reference indices from the removed group.
                rem=existing[len(fixed):]
                reordered=[None]*len(rows)
                for j,p in enumerate(rows):
                    if j not in indices:reordered[j]=p
                used=set()
                for j in indices:
                    old=rows[j]
                    match=next((k for k,q in enumerate(rem)
                        if k not in used and q['name']==old['name']
                        and abs(q['shape'].area-old['shape'].area)<1e-5),None)
                    if match is None:break
                    used.add(match);reordered[j]=rem[match]
                if all(p is not None for p in reordered):outputs.append(reordered)
    return outputs


def enhance_layout(layout,quality='balanced',preserve=True,auto=True):
    """Improve (or preserve) a complete single-sheet layout using NFP/LNS/beam.

    Heavy search intentionally restricted to small polygon jobs. Existing v1.3
    nesting is still used as the robust fallback for large jobs/multi-sheet.
    """
    data=dict(layout)
    base=[dict(p) for p in layout['placements']]
    n=len(base)
    if not 2<=n<=8 or layout['sheet_count']!=1 or quality=='fast' or layout.get('stock_outline') is not None:
        data.update(hybrid_engine='skipped: large/fast/irregular/multi-sheet',hybrid_evaluated=0)
        return data
    margin=float(layout['margin_mm']);gap=float(layout['gap_mm'])
    baseW=float(layout['sheets'][0]['width_mm']);baseH=float(layout['sheets'][0]['height_mm'])
    W,H=(baseW*1.18,baseH*1.18) if auto else (baseW,baseH)
    max_area=baseW*baseH*1.035 if auto and preserve else float('inf')
    settings={'balanced':(2,2,(0,90,180,270)),
              'thorough':(3,3,tuple(range(0,360,45))),
              'precision':(4,3,tuple(range(0,360,30)))}
    rounds,beam_width,angles=settings[quality]
    # Never drop the starting layout.
    original=_objective(base,W,H,margin,gap,preserve,auto,max_area)
    if original is None:return data
    best=original;winner=base
    beam=[base];visited={_key(base)};evaluated=1
    for step in range(rounds):
        expansions=[]
        for rows in beam:
            for subset in _neighborhoods(rows,step):
                for trial in _repack(rows,subset,W,H,margin,gap,angles,quality,preserve,branch=2):
                    key=_key(trial)
                    if key in visited:continue
                    visited.add(key)
                    outcome=_objective(trial,W,H,margin,gap,preserve,auto,max_area)
                    if outcome is None:continue
                    evaluated+=1
                    expansions.append((outcome[0],trial))
                    if outcome[0]<best[0]:best=outcome;winner=trial
        if not expansions:break
        expansions.sort(key=lambda v:v[0]);beam=[v[1] for v in expansions[:beam_width]]
    if auto:
        rows,dimensions=_crop(winner,margin)
        from nesting_advanced import _summarize
        data=_summarize(rows,[dimensions],margin,gap,layout['algorithm'])
        # Carry previously computed metadata forward without overwriting the
        # optimized geometry and derived totals.
        for key in ('search_objective','evaluated_alternatives'):
            if key in layout:data[key]=layout[key]
    else:
        from nesting_advanced import _summarize
        data=_summarize(winner,[(baseW,baseH)],margin,gap,layout['algorithm'])
        for key in ('search_objective','evaluated_alternatives'):
            if key in layout:data[key]=layout[key]
    data['algorithm']+='; NFP contacts + LNS (2-3 parts) + beam search + polygonal free-space evaluation'
    data['hybrid_engine']='NFP / LNS / Beam / PolygonalRemnants'
    data['hybrid_evaluated']=evaluated
    data['hybrid_search_rounds']=rounds
    data['hybrid_beam_width']=beam_width
    data['polygonal_largest_rectangle_mm2']=round(best[3],2) if preserve else None
    data['polygonal_largest_connected_mm2']=round(best[4],2) if preserve else None
    return data
