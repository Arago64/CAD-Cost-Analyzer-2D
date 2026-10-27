"""Freeform offcut candidates from exact 2D sheet polygons (not bbox estimates).

A narrow-neck morphological filter removes unhelpful thin corridors. The
resulting polygons are always intersected with the actual free stock so no
material occupied by parts can be proposed for reuse. These are *candidate*
profiles, not a checked manufacturing separation/cut plan.
"""
from __future__ import annotations
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from remnants import find_remnants, remnants_dxf, remnants_json


def _polygons(g):
    if g.is_empty:
        return []
    if isinstance(g,Polygon):
        return [g]
    return [p for piece in getattr(g,'geoms',()) for p in _polygons(piece)]


def decompose_freeform(polygon, max_sections=5):
    """Exact polygonal decomposition of ONE compound reusable profile.

    Separate simple strips are interpretation aids, not separate offcuts.
    Their union recovers the original polygon; holes, concavities and curves
    are preserved in the one authoritative enclosing boundary.
    """
    if polygon.is_empty or polygon.area <= 0:
        return []
    x0,y0,x1,y1=polygon.bounds
    if x1-x0<=1e-9:
        return [polygon]
    # Prefer cuts at shape features (steps/notches), then distributed cuts.
    # Each original curve remains in its own strip; no surrogate bbox is used.
    vertices=list(polygon.exterior.coords)
    key_x=sorted({round(x,6) for x,y in vertices})
    cuts=[]
    for x in key_x[1:-1]:
        if x0+0.12*(x1-x0)<x<x1-0.12*(x1-x0):
            cuts.append(x)
    if len(cuts)>max_sections-1:
        cuts=[cuts[round(i*(len(cuts)-1)/max_sections)] for i in range(1,max_sections)]
    if not cuts:
        cuts=[x0+(x1-x0)/2]
    bounds=[x0]+sorted(set(cuts))+[x1]
    parts=[]
    for a,b in zip(bounds,bounds[1:]):
        section=polygon.intersection(box(a,y0-1,b,y1+1))
        parts.extend([p for p in _polygons(section) if p.area>1e-7])
    # Floating point roundoff is acceptable only far below cutting precision.
    if not parts or polygon.symmetric_difference(unary_union(parts)).area > max(1e-5,polygon.area*1e-8):
        return [polygon]
    return parts


def irregular_remnants(layout, *, kerf_mm=0.3, min_neck_mm=8.,
                       min_area_mm2=1000., edge_margin_mm=0.):
    """Find connected L/C/stepped/etc. offcuts with optional narrow-gap filter.

    min_neck_mm=0 keeps original connected polygons. Larger widths make an
    inward/outward geometric opening and split thin corridors. Boundaries may
    be slightly rounded at acute corners; output geometry stays within the
    ORIGINAL free sheet to preserve the no-overlap invariant.
    """
    if kerf_mm<0 or min_neck_mm<0 or min_area_mm2<0 or edge_margin_mm<0:
        raise ValueError('Offcut geometric limits must be nonnegative')
    source=find_remnants(layout,kerf_mm=kerf_mm,min_area_mm2=0.,
                         edge_margin_mm=edge_margin_mm)
    result=[]
    for src in source:
        free=src['polygon']
        if min_neck_mm:
            radius=min_neck_mm/2
            core=free.buffer(-radius,join_style=2)
            if core.is_empty:
                continue
            # The opening rejects strips smaller than min_neck_mm.
            output=core.buffer(radius,join_style=2).intersection(free)
        else:
            output=free
        for polygon in _polygons(output):
            if polygon.area<min_area_mm2 or polygon.is_empty:
                continue
            if not free.buffer(1e-7).covers(polygon):
                raise AssertionError('Offcut must be fully contained in free material')
            sections=decompose_freeform(polygon)
            result.append({'sheet_index':src['sheet_index'],
                           'compound_components':sections,
                           'component_count':len(sections),
                           'compound':len(sections)>1 and (polygon.area < polygon.envelope.area*0.98 or len(polygon.interiors)>0),
                           'area_mm2':round(float(polygon.area),3),
                           'bbox_mm':[round(v,3) for v in polygon.bounds],
                           'polygon':polygon,
                           'kind':'freeform',
                           'min_neck_mm':float(min_neck_mm)})
    return sorted(result,key=lambda r:(-r['area_mm2'],r['sheet_index']))


def irregular_remnants_dxf(remnants):
    return remnants_dxf(remnants)


def irregular_remnants_json(remnants, *, material='', thickness_mm=None):
    return remnants_json(remnants,material=material,thickness_mm=thickness_mm)
