"""Conservative re-usable rectangular scrap estimate (all dimensions in mm).

Largest axis-aligned free rectangle, computed against *inflated bounding boxes*
of cut parts. This underestimates, rather than overstates, available rectangular
stock in round/concave spaces. It is NOT a fixturing or salvage guarantee.
"""
from __future__ import annotations
from shapely.geometry import box


def largest_free_rectangle(placements, width, height, *, margin=0., clearance=0.):
    """Return (rectangle polygon, area) inside a rectangular stock.

    Uses weighted maximal histogram rectangle on the grid induced by each
    inflated part's bbox, O((2*N+2)^2). The best rectangle is guaranteed not
    to intersect those bboxes (to numerical tolerance). Holes are treated
    conservatively as occupied because they lie within the host bbox.
    """
    x0=y0=float(margin)
    x1=float(width)-margin; y1=float(height)-margin
    if x1<=x0 or y1<=y0:return None,0.
    blockers=[]
    for part in placements:
        a,b,c,d=part['shape'].bounds
        a=max(x0,a-clearance);b=max(y0,b-clearance)
        c=min(x1,c+clearance);d=min(y1,d+clearance)
        if a<c and b<d:blockers.append((a,b,c,d))
    xs=sorted(set([x0,x1]+[z for a,b,c,d in blockers for z in (a,c)]))
    ys=sorted(set([y0,y1]+[z for a,b,c,d in blockers for z in (b,d)]))
    n=len(xs)-1
    heights=[0.]*n
    best_rect=None;best=0.
    for j in range(len(ys)-1):
        low,high=ys[j],ys[j+1]
        for i in range(n):
            left,right=xs[i],xs[i+1]
            occupied=any(a<right-1e-9 and c>left+1e-9 and b<high-1e-9 and d>low+1e-9
                         for a,b,c,d in blockers)
            heights[i]=0. if occupied else heights[i]+high-low
        stack=[]
        for i in range(n+1):
            next_height=heights[i] if i<n else 0.
            start=i
            while stack and stack[-1][1]>next_height+1e-10:
                first,h=stack.pop();start=first
                area=h*(xs[i]-xs[first])
                if area>best+1e-8:
                    best=area
                    best_rect=box(xs[first],high-h,xs[i],high)
            if not stack or stack[-1][1]<next_height-1e-10:
                stack.append((start,next_height))
    return best_rect,best


def remnant_quality(layout, *, clearance=None):
    """Report one conservative, contiguous rectangular remnant per sheet."""
    if layout.get('stock_outline') is not None:
        return []  # a regular bounding rectangle could lie outside this stock
    clear=layout.get('gap_mm',0.)/2 if clearance is None else clearance
    rows=[]
    for sh in layout['sheets']:
        p=[part for part in layout['placements'] if part.get('sheet_index',0)==sh['index']]
        rect,area=largest_free_rectangle(p,sh['width_mm'],sh['height_mm'],
                     margin=layout.get('margin_mm',0.),clearance=max(0.,clear))
        rows.append({'sheet_index':sh['index'],'area_mm2':round(area,2),
                     'bbox_mm':tuple(round(v,3) for v in rect.bounds) if rect is not None else None})
    return rows
