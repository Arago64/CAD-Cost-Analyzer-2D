"""Candidate shared cutting lines from parallel, facing nominal contours.

Conservative planning aid; common edge substitution is NOT performed in the
ordinary offset toolpath and savings are NOT subtracted from the quote.
"""
from __future__ import annotations
from math import hypot
from shapely.geometry import LineString


def _edges(poly,min_length_mm=5.0):
    coords=list(poly.exterior.coords)
    for a,b in zip(coords,coords[1:]):
        if hypot(b[0]-a[0],b[1]-a[1])>=min_length_mm:yield a,b


def common_line_candidates(layout,kerf_mm,tolerance_mm=0.08,min_length_mm=5.0):
    """Find straight parallel segments at approximately one kerf apart.

    Intersections and line ends require a CAM-specific cut-sharing strategy.
    No merge is executed without verified cutter compensation and sequencing.
    """
    result=[]
    parts=layout['placements']
    for i,p in enumerate(parts):
        for q in parts[i+1:]:
            if p.get('sheet_index',0)!=q.get('sheet_index',0):continue
            if abs(p['shape'].distance(q['shape'])-kerf_mm)>tolerance_mm:continue
            for a,b in _edges(p['shape'],min_length_mm):
                dx,dy=b[0]-a[0],b[1]-a[1];length=hypot(dx,dy)
                ux,uy=dx/length,dy/length
                for c,d in _edges(q['shape'],min_length_mm):
                    vx,vy=d[0]-c[0],d[1]-c[1];vlen=hypot(d[0]-c[0],d[1]-c[1]);vx/=vlen;vy/=vlen
                    if abs(ux*vx+uy*vy)<0.9999:continue
                    # Signed perpendicular distance to p line and projections overlap.
                    dist=abs((c[0]-a[0])*(-uy)+(c[1]-a[1])*ux)
                    if abs(dist-kerf_mm)>tolerance_mm:continue
                    coords=[(c[0]-a[0])*ux+(c[1]-a[1])*uy,
                            (d[0]-a[0])*ux+(d[1]-a[1])*uy]
                    lo=max(0,min(coords));hi=min(length,max(coords))
                    if hi-lo<min_length_mm:continue
                    # Midline between the two facing boundaries.
                    sign=((c[0]-a[0])*(-uy)+(c[1]-a[1])*ux)
                    mx=-uy*sign/2;my=ux*sign/2
                    p1=(a[0]+ux*lo+mx,a[1]+uy*lo+my)
                    p2=(a[0]+ux*hi+mx,a[1]+uy*hi+my)
                    seg=LineString([p1,p2]);blocked=False
                    for r in parts:
                        if r is p or r is q or r.get('sheet_index',0)!=p.get('sheet_index',0):continue
                        if r['shape'].distance(seg)<kerf_mm/2-tolerance_mm:blocked=True;break
                    if blocked:continue
                    result.append({'sheet_index':p.get('sheet_index',0),'a':p['name'],'b':q['name'],
                                   'xy':[p1,p2],'length_mm':round(hi-lo,3),
                                   'status':'candidate_only_unverified'})
    # Duplicate geometries from tessellated source edges may still exist.
    return result
