"""Deterministic, exact-checked insertion of small parts into existing cavities.

This is a *separate search objective*: regular beam pruning must not discard
candidate placements inside a host's hole before collision tests.  A cavity is
an interior ring or a concavity within a host's convex hull.  Every reported
placement is tested against ORIGINAL source polygons and all other parts.
No CAM / part-support guarantee is implied by 2D geometric validity.
"""
from __future__ import annotations
from math import ceil
from shapely import affinity
from shapely.geometry import Polygon, box
from nesting_advanced import _cavity_regions, _tag_nested
from nesting_refinement import _valid
from sheet_nesting import _orient


def _regions(host):
    result=[Polygon(ring) for ring in host.interiors]
    result.extend(_cavity_regions(host))
    # Discard narrow artifacts that could never house a normal part.
    return [p for p in result if p.is_valid and p.area>1.0]


def _grid_points(cavity,ref,gap,steps=9):
    """Sample cavity as a 2D REGION rather than sparse vertex contacts.

    The cheap point grid is a candidate generator, never the acceptance test.
    Candidate positions include edges, internal points and centered placements.
    """
    x0,y0,x1,y1=cavity.bounds
    w=ref.bounds[2]-ref.bounds[0];h=ref.bounds[3]-ref.bounds[1]
    lo_x=x0+gap;hi_x=x1-gap-w
    lo_y=y0+gap;hi_y=y1-gap-h
    if lo_x>hi_x+1e-7 or lo_y>hi_y+1e-7:return []
    def linspace(a,b,n):
        if b-a<1e-8:return [a]
        return [a+(b-a)*i/(n-1) for i in range(n)]
    xs=linspace(lo_x,hi_x,steps)
    ys=linspace(lo_y,hi_y,steps)
    middle=((lo_x+hi_x)/2,(lo_y+hi_y)/2)
    mark=cavity.representative_point()
    special=[middle,(lo_x,lo_y),(hi_x,hi_y),(hi_x,lo_y),(lo_x,hi_y),
             (mark.x-w/2,mark.y-h/2)]
    # Favour cavity-center and peripheral positions equally, not y/x sort.
    out=[];seen=set()
    for x,y in special+[(x,y) for x in xs for y in ys]:
        key=(round(x,5),round(y,5))
        if key not in seen:
            seen.add(key);out.append((x,y))
    return out


def cavity_repair(layout,*,auto=False,angles=(0,90,180,270),max_moves=2,
                  deadline=None):
    """Return an independently validated *alternative* or None.

    Revisit the existing layout and actively try small parts in host cavities.
    Supports cavities which already hold OTHER nested parts. Moves are accepted
    only if the child becomes newly nested without increasing the occupied group
    bbox area. Never changes part geometry, scales or quantities.
    """
    from time import monotonic
    if layout.get('stock_outline') is not None or layout['sheet_count']!=1:return None
    rows=[dict(r) for r in layout['placements']]
    if not (2<=len(rows)<=12):return None
    margin=float(layout['margin_mm']);gap=float(layout['gap_mm'])
    sh=layout['sheets'][0];W=float(sh['width_mm']);H=float(sh['height_mm'])
    stock=box(margin,margin,W-margin,H-margin)
    if not all(_valid(p['shape'],rows[:i],gap,stock) for i,p in enumerate(rows)):
        return None
    changed=False
    for _ in range(max_moves):
        if deadline is not None and monotonic()>=deadline:break
        baseline=[dict(r) for r in rows]
        _tag_nested(baseline)
        old_nested=sum('nested_in_index' in r for r in baseline)
        bb=[r['shape'].bounds for r in rows]
        old_area=(max(b[2] for b in bb)-min(b[0] for b in bb))*(max(b[3] for b in bb)-min(b[1] for b in bb))
        best=None
        for host_i,host in enumerate(rows):
            if deadline is not None and monotonic()>=deadline:break
            for cavity in _regions(host['shape']):
                # Don't waste expensive geometry tests on impossible cavity fits.
                x0,y0,x1,y1=cavity.bounds
                for child_i in sorted(range(len(rows)),key=lambda k:rows[k]['shape'].area):
                    if child_i==host_i:continue
                    if deadline is not None and monotonic()>=deadline:break
                    item=rows[child_i]
                    # Only MOVE NEW parts into the selected host, preserving
                    # already nested pieces (including a small round in a C).
                    if baseline[child_i].get('nested_in_index')==host_i:continue
                    if item['shape'].area>=cavity.area*.96:continue
                    others=[p for k,p in enumerate(rows) if k!=child_i]
                    current_angle=float(item.get('angle',0))
                    for angle in angles:
                        if deadline is not None and monotonic()>=deadline:break
                        ref=_orient(item['shape'],angle-current_angle)
                        w,h=ref.bounds[2],ref.bounds[3]
                        if w+2*gap>x1-x0+1e-7 or h+2*gap>y1-y0+1e-7:continue
                        # Safe polygon erosion makes containment checks cheap.
                        safe_cavity=cavity.buffer(-gap+1e-7,join_style=2)
                        if safe_cavity.is_empty:continue
                        for xy_index,(x,y) in enumerate(_grid_points(cavity,ref,gap)):
                            if deadline is not None and xy_index%12==0 and monotonic()>=deadline:break
                            shape=affinity.translate(ref,xoff=x,yoff=y)
                            if not safe_cavity.covers(shape):continue
                            if not _valid(shape,others,gap,stock):continue
                            trial=[dict(p) for p in rows]
                            trial[child_i]=dict(item,shape=shape,x=shape.bounds[0],
                                                 y=shape.bounds[1],angle=angle)
                            _tag_nested(trial)
                            if trial[child_i].get('nested_in_index')!=host_i:continue
                            new_nested=sum('nested_in_index' in p for p in trial)
                            if new_nested<=old_nested:continue
                            bounds=[p['shape'].bounds for p in trial]
                            new_area=(max(b[2] for b in bounds)-min(b[0] for b in bounds))*(
                                max(b[3] for b in bounds)-min(b[1] for b in bounds))
                            # Nested part count is a useful secondary objective
                            # only when it does not make the outer group larger.
                            if new_area>old_area+1e-6:continue
                            # Prefer true footprint reduction over identical
                            # bounds, and keep a deterministic tie breaker.
                            score=(-new_nested,new_area,shape.centroid.x+shape.centroid.y)
                            if best is None or score<best[0]:best=(score,trial)
        if best is None:
            # Two movable children may require a coordinated change: a circle
            # already in the opening can block a rectangle even when BOTH fit
            # after moving the circle sideways. A single-part local search is
            # unable to discover this legal configuration.
            for host_i,host in enumerate(rows):
                if deadline is not None and monotonic()>=deadline:break
                for cavity in _regions(host['shape']):
                    occupants=[i for i,p in enumerate(baseline) if
                               p.get('nested_in_index')==host_i]
                    if len(occupants)!=1:continue  # intentionally bounded
                    occupant_i=occupants[0]
                    for child_i in sorted(range(len(rows)),key=lambda k:rows[k]['shape'].area):
                        if deadline is not None and monotonic()>=deadline:break
                        if child_i in (host_i,occupant_i) or baseline[child_i].get('nested_in_index') is not None:
                            continue
                        if rows[child_i]['shape'].area>=cavity.area*.96:continue
                        others=[p for k,p in enumerate(rows) if k not in (occupant_i,child_i)]
                        safe_cavity=cavity.buffer(-gap+1e-7,join_style=2)
                        if safe_cavity.is_empty:continue
                        occ=rows[occupant_i]
                        occ_ref=_orient(occ['shape'],0)
                        positions=_grid_points(cavity,occ_ref,gap,steps=5)
                        for index,(ox,oy) in enumerate(positions):
                            if deadline is not None and index%8==0 and monotonic()>=deadline:break
                            placed_occ=affinity.translate(occ_ref,xoff=ox,yoff=oy)
                            if not safe_cavity.covers(placed_occ) or not _valid(placed_occ,others,gap,stock):continue
                            trial_others=others+[dict(occ,shape=placed_occ)]
                            child=rows[child_i]
                            for angle in angles:
                                ref=_orient(child['shape'],angle-float(child.get('angle',0)))
                                for x,y in _grid_points(cavity,ref,gap,steps=7):
                                    shape=affinity.translate(ref,xoff=x,yoff=y)
                                    if not safe_cavity.covers(shape) or not _valid(shape,trial_others,gap,stock):continue
                                    trial=[dict(p) for p in rows]
                                    trial[occupant_i]=dict(occ,shape=placed_occ,x=placed_occ.bounds[0],y=placed_occ.bounds[1])
                                    trial[child_i]=dict(child,shape=shape,x=shape.bounds[0],y=shape.bounds[1],angle=angle)
                                    _tag_nested(trial)
                                    if trial[child_i].get('nested_in_index')!=host_i or trial[occupant_i].get('nested_in_index')!=host_i:
                                        continue
                                    new_nested=sum('nested_in_index' in p for p in trial)
                                    if new_nested<=old_nested:continue
                                    bounds=[p['shape'].bounds for p in trial]
                                    new_area=(max(b[2] for b in bounds)-min(b[0] for b in bounds))*(
                                        max(b[3] for b in bounds)-min(b[1] for b in bounds))
                                    if new_area>old_area+1e-6:continue
                                    score=(-new_nested,new_area,shape.centroid.x+shape.centroid.y)
                                    if best is None or score<best[0]:best=(score,trial)
                                    # One collision-verified pair is sufficient
                                    # for the expensive fallback pass.
                                    break
                                if best is not None:break
                            if best is not None:break
                        if best is not None:break
                    if best is not None:break
                if best is not None:break
        if best is None:break
        rows=best[1];changed=True
    if not changed:return None
    from optimizer_v15 import _rebuild, _validate
    result=_rebuild(rows,layout,auto,'2D cavity-aware exact-checked repair / v2.4')
    if not _validate(result):return None
    result['cavity_repair']=True
    result['cavity_repair_nested_count']=result['nested_items']
    return result
