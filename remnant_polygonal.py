"""Shape-aware unused sheet analysis. Units millimetres.

Connected free areas are computed by actual polygonal boolean difference;
this is exact for the polygonal DXF approximation. The largest inscribed
axis-aligned free rectangle is a CONSERVATIVE sampled-grid estimate (not a
mathematically certified continuous optimum), validated against free space.
"""
from __future__ import annotations
from functools import lru_cache
from shapely.geometry import box,Polygon
from shapely.ops import unary_union
from shapely.prepared import prep


def polygonal_remnants(placements,W,H,margin=0.,clearance=0.):
    stock=box(margin,margin,W-margin,H-margin)
    parts=[p['shape'].buffer(clearance,quad_segs=5) for p in placements]
    space=stock.difference(unary_union(parts)) if parts else stock
    polys=[space] if space.geom_type=='Polygon' else [g for g in getattr(space,'geoms',()) if g.geom_type=='Polygon']
    areas=sorted((float(p.area) for p in polys if p.area>1e-6),reverse=True)
    return space,(areas[0] if areas else 0.),len(areas)


def largest_polygonal_free_rectangle(placements,W,H,margin=0.,clearance=0.,grid=24):
    """Maximal safe rectangle on an adaptive x/y cell grid.

    Grid lines combine stock limits, geometrical obstacle vertices and uniform
    sampling. Occupied cells that touch the cut geometry are rejected, so the
    returned rectangle is inside actual free polygon space, never just a bbox.
    """
    space,connected,fragments=polygonal_remnants(placements,W,H,margin,clearance)
    if space.is_empty:return None,0.,connected,fragments
    x0,y0,x1,y1=margin,margin,W-margin,H-margin
    if x1<=x0 or y1<=y0:return None,0.,connected,fragments
    # Use polygon-boundary vertex coordinates as well as uniform samples.
    xset={x0,x1};yset={y0,y1}
    for p in placements:
        poly=p['shape'].buffer(clearance,quad_segs=4)
        boundary=[poly] if poly.geom_type=='Polygon' else list(getattr(poly,'geoms',[]))
        for part in boundary:
            if part.geom_type!='Polygon':continue
            for ring in [part.exterior]+list(part.interiors):
                coords=list(ring.coords)
                step=max(1,len(coords)//22)
                for x,y in coords[::step]:
                    if x0<x<x1:xset.add(float(x))
                    if y0<y<y1:yset.add(float(y))
    for i in range(1,grid):
        xset.add(x0+(x1-x0)*i/grid)
        yset.add(y0+(y1-y0)*i/grid)
    # Uniformly downselect for bounded complexity without discarding edges.
    def thin(seq,cap):
        seq=sorted(seq)
        if len(seq)<=cap:return seq
        return [seq[round(i*(len(seq)-1)/(cap-1))] for i in range(cap)]
    xs=thin(xset,min(55,grid+25));ys=thin(yset,min(55,grid+25))
    nx=len(xs)-1
    heights=[0.]*nx
    best_area=0.;best=None
    # Prepare the actual free polygon for repeated rectangular cell tests.
    prepared=prep(space)
    for j in range(len(ys)-1):
        for i in range(nx):
            cell=box(xs[i]+1e-7,ys[j]+1e-7,xs[i+1]-1e-7,ys[j+1]-1e-7)
            safe=prepared.covers(cell)
            heights[i]=heights[i]+ys[j+1]-ys[j] if safe else 0.
        stack=[]
        for i in range(nx+1):
            h=heights[i] if i<nx else 0.;start=i
            while stack and stack[-1][1]>h+1e-9:
                ix,old_h=stack.pop();start=ix
                a=old_h*(xs[i]-xs[ix])
                if a>best_area+1e-8:
                    rect=box(xs[ix],ys[j+1]-old_h,xs[i],ys[j+1])
                    if space.buffer(1e-6).covers(rect):best_area=a;best=rect
            if not stack or stack[-1][1]<h-1e-9:stack.append((start,h))
    return best,best_area,connected,fragments
