"""Manufacturing-oriented candidate shapes cut from remaining sheet metal.

Geometry is derived from polygonal free sheet space, never part bounding boxes.
Rectangles and discs are *inscribed* and selected without counting overlaps.
These are geometric candidates, NOT a proof they can be physically separated.
"""
from __future__ import annotations
from math import pi
from shapely.geometry import Point
from shapely.prepared import prep
from remnant_portfolio import _rectangles_in_space, remnant_metrics


def _circles_in_space(space, grid=12, max_candidates=6, min_radius=3.):
    if space.is_empty:
        return []
    x0,y0,x1,y1 = space.bounds
    if x1-x0 < 2*min_radius or y1-y0 < 2*min_radius:
        return []
    prepared=prep(space)
    circles=[]
    # A full usable disc must fit inside free space, including internal holes.
    for i in range(1,grid):
        for j in range(1,grid):
            p=Point(x0+(x1-x0)*i/grid,y0+(y1-y0)*j/grid)
            if not prepared.covers(p):continue
            radius=space.boundary.distance(p)
            if radius<min_radius:continue
            disc=p.buffer(max(0.,radius-0.001),quad_segs=12)
            if prepared.covers(disc):circles.append(disc)
    circles.sort(key=lambda p:p.area,reverse=True)
    return circles[:max_candidates]


def select_mixed_remnants(space,max_shapes=3,grid=12,min_dimension=5.):
    """Greedy mixed rectangle/square/disc selection with geometric dedup.

    Also tests circle-first and rectangle-first policies to avoid systemic
    bias against circular offcuts. Polygon components remain in free_geometry.
    """
    choices=[]
    for policy in ('area','rect_first','circle_first'):
        remaining=space
        selected=[]
        for _ in range(max_shapes):
            rectangles=_rectangles_in_space(remaining,grid=grid,max_rectangles=1,
                                            min_dimension=min_dimension,min_area=min_dimension**2)
            circles=_circles_in_space(remaining,grid=grid,
                                      min_radius=min_dimension/2)
            ranked=([(r,'rectangle') for r in rectangles] +
                    [(c,'circle') for c in circles])
            if not ranked:break
            if policy=='rect_first':ranked.sort(key=lambda v:(v[1]=='rectangle',v[0].area),reverse=True)
            elif policy=='circle_first':ranked.sort(key=lambda v:(v[1]=='circle',v[0].area),reverse=True)
            else:ranked.sort(key=lambda v:v[0].area,reverse=True)
            part,kind=ranked[0]
            if not remaining.buffer(1e-7).covers(part):break
            selected.append({'geometry':part,'kind':kind,'area_mm2':part.area})
            remaining=remaining.difference(part)
        choices.append(selected)
    return max(choices,key=lambda a:sum(p['area_mm2'] for p in a),default=[])


def freeform_metrics(layout,grid=12,min_dimension=5.):
    sheets=remnant_metrics(layout,grid=grid,max_rectangles=3,min_dimension=min_dimension)
    mixed=[]; total=0.;best_connected=0.; usable_area=0.
    for sh in sheets:
        parts=select_mixed_remnants(sh['free_geometry'],max_shapes=3,
                                    grid=grid,min_dimension=min_dimension)
        mixed.append(parts)
        total+=sum(p['area_mm2'] for p in parts)
        best_connected=max(best_connected,sh['largest_connected_mm2'])
        # A minimum width opening excludes narrow and impractical scrap fingers.
        free=sh['free_geometry']
        inner=free.buffer(-min_dimension/2)
        if not inner.is_empty:
            usable_area+=inner.buffer(min_dimension/2).intersection(free).area
    return {'mixed':mixed,'mixed_total_mm2':total,
            'rectangles_total_mm2':sum(sh['combined_rectangles_mm2'] for sh in sheets),
            'connected_mm2':best_connected,'usable_free_mm2':usable_area,
            'free_sheets':sheets}
