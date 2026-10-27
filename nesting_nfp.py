"""Polygonal no-fit region (NFP) candidate generator for 2D nesting.

For polygonal A and B at origin, a translation t of B collides with A iff
 t lies in Minkowski(A, -B).  Build this NFP by unions of convex sums of
 polygon triangulations.  Curved DXF contours are already polygonized.

To bound work, complex boundaries can be simplified for *candidate generation*.
Final placements are ALWAYS checked against the original polygons; a reduced
NFP cannot authorize a collision.  This is not a certified complete NFP solver.
"""
from __future__ import annotations
from functools import lru_cache
from math import ceil
from shapely import affinity
from shapely.geometry import Polygon, MultiPolygon, MultiPoint, LineString
from shapely.ops import triangulate, unary_union


def _poly_parts(geom):
    if geom.geom_type == 'Polygon':return [geom]
    return [p for p in getattr(geom,'geoms',()) if p.geom_type=='Polygon' and p.area>1e-9]


def _decompose(poly, maximum=19):
    if poly.area <= 1e-8:return []
    if not poly.interiors and abs(poly.area-poly.convex_hull.area)<1e-7:
        return [poly]
    # limit complexity only in candidate generation; validation uses exact shape
    geom=poly
    for tol in (0.25,0.65,1.3,2.5,5.):
        if len(geom.exterior.coords)+sum(len(r.coords) for r in geom.interiors)<=maximum+2:break
        geom=poly.simplify(tol,preserve_topology=True)
    tris=[]
    for tri in triangulate(geom):
        if geom.covers(tri):tris.append(tri)
        else:
            intersection=tri.intersection(geom)
            for piece in _poly_parts(intersection):
                if piece.area>1e-8:
                    if not piece.interiors and abs(piece.area-piece.convex_hull.area)<1e-7:
                        tris.append(piece)
                    else:
                        tris.extend(t for t in triangulate(piece) if piece.covers(t))
    if len(tris)>maximum:
        # The overly detailed geometry would yield O(N^2) Minkowski components.
        # Conservative convex-hull fallback affects candidate discovery only.
        return [geom.convex_hull]
    return tris


@lru_cache(maxsize=160)
def no_fit_polygon(obstacle:Polygon,mover:Polygon):
    """Return Minkowski(obstacle, -mover) with geometric holes if possible.

    Geometries should be translated so their bounding box starts at (0,0).
    The result is approximate for large curved shapes because of simplification.
    """
    neg=affinity.scale(mover,xfact=-1,yfact=-1,origin=(0,0))
    a=_decompose(obstacle)
    b=_decompose(neg)
    sums=[]
    for ta in a:
        av=list(ta.exterior.coords)[:-1]
        for tb in b:
            bv=list(tb.exterior.coords)[:-1]
            h=MultiPoint([(x+u,y+v) for x,y in av for u,v in bv]).convex_hull
            if h.area>1e-10:sums.append(h)
    return unary_union(sums) if sums else Polygon()


def _normalize(poly):
    x,y,_,_=poly.bounds
    return affinity.translate(poly,xoff=-x,yoff=-y),(x,y)


def _ring_samples(ring,n=18):
    line=LineString(ring.coords)
    raw=list(ring.coords)[:-1]
    # Corners of NFP plus equally distributed boundary points are important.
    if len(raw)>24:raw=raw[::max(1,ceil(len(raw)/24))]
    pts=raw+[(line.interpolate(i*line.length/n).x,
              line.interpolate(i*line.length/n).y) for i in range(n)]
    return pts


def nfp_contact_candidates(placed,reference,gap,*,max_old=3,max_per_pair=26):
    """Candidate anchor locations along polygonal forbidden-region boundaries.

    Inclusive of no-fit holes, which permit nesting B inside cut-outs in A.
    All candidate transforms still require an independent exact validation.
    """
    result=[]
    mover,_=_normalize(reference)
    obstacles=sorted(placed,key=lambda p:p['shape'].distance(reference))[:max_old]
    for obj in obstacles:
        obstacle,(ox,oy)=_normalize(obj['shape'])
        forbidden=no_fit_polygon(obstacle,mover)
        if forbidden.is_empty:continue
        # Gap buffer is the configuration-space clearance offset. A tolerance
        # outside the boundary avoids floating-point collision false positives.
        forbidden=forbidden.buffer(max(0.,gap)+0.008,quad_segs=3,join_style=2)
        candidates=[]
        for poly in _poly_parts(forbidden):
            for ring in [poly.exterior]+list(poly.interiors):
                candidates.extend(_ring_samples(ring,n=14))
        if len(candidates)>max_per_pair:
            ordered=sorted(set((round(x,4),round(y,4)) for x,y in candidates))
            stride=max(1,ceil(len(ordered)/max_per_pair))
            candidates=ordered[::stride][:max_per_pair]
        result.extend((round(x+ox,4),round(y+oy,4)) for x,y in candidates)
    return list(dict.fromkeys(result))
