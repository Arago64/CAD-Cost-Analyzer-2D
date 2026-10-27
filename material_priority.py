"""Material-first finishing of valid 2D nests and reusable offcut selection.

Greedy coordinate descent is intentionally conservative: every move is tested
against exact polygons with clearance. It is a heuristic, not an optimum proof.
"""
from __future__ import annotations
from shapely import affinity
from nesting_advanced import _summarize
from optimizer_v15 import _validate
from irregular_remnants import irregular_remnants


def material_key(layout):
    """Strict lexicographic purchase objective: sheets, then material area."""
    return (int(layout['sheet_count']),float(layout['sheet_mm2']))


def polish_layout(layout, *, rounds=3):
    """Compact existing orientations using coordinate-descent translations.

    Does not change stock dimensions in fixed mode, original order, shape or angle.
    Exact validity is mandatory. For auto stock crop to the resulting bounds.
    """
    if layout['sheet_count'] != 1 or layout.get('stock_outline') is not None:
        return layout
    rows=[dict(p) for p in layout['placements']]
    if not rows or len(rows)>30:return layout
    margin=float(layout['margin_mm']);gap=float(layout['gap_mm'])
    is_auto=bool(layout.get('auto',False))
    # Use the existing dimensions to infer auto-ness only if explicitly passed
    # by the caller; fixed-stock jobs must never be cropped.
    if not is_auto:return layout
    from nesting_refinement import _valid
    from shapely.geometry import box
    def bounds_area(items):
        x0=min(p['shape'].bounds[0] for p in items)
        y0=min(p['shape'].bounds[1] for p in items)
        x1=max(p['shape'].bounds[2] for p in items)
        y1=max(p['shape'].bounds[3] for p in items)
        return (x1-x0+2*margin)*(y1-y0+2*margin)
    def normalize(items):
        x=min(p['shape'].bounds[0] for p in items)
        y=min(p['shape'].bounds[1] for p in items)
        return [dict(p,shape=affinity.translate(p['shape'],xoff=margin-x,yoff=margin-y)) for p in items]
    rows=normalize(rows)
    for _ in range(rounds):
        improved=False
        for i in sorted(range(len(rows)),key=lambda j:-rows[j]['shape'].area):
            others=rows[:i]+rows[i+1:]
            sh=rows[i]['shape'];x0,y0,x1,y1=sh.bounds
            xs={margin,x0};ys={margin,y0}
            for other in others:
                a,b,c,d=other['shape'].bounds
                xs.update((a-(x1-x0)-gap,c+gap,a,c-(x1-x0)+gap))
                ys.update((b-(y1-y0)-gap,d+gap,b,d-(y1-y0)+gap))
            current=bounds_area(rows)
            best=None
            # Candidate grid includes part-edge contact and combined x/y moves.
            for x in sorted(xs):
                if x<margin-1e-7:continue
                for y in sorted(ys):
                    if y<margin-1e-7:continue
                    moved=affinity.translate(sh,xoff=x-x0,yoff=y-y0)
                    if not _valid(moved,others,gap,box(-1e6,-1e6,1e6,1e6)):
                        continue
                    trial=others+[dict(rows[i],shape=moved)]
                    score=bounds_area(trial)
                    if score<current-1e-5 and (best is None or score<best[0]):
                        best=(score,moved)
            if best is not None:
                rows[i]=dict(rows[i],shape=best[1]);rows=normalize(rows)
                improved=True
        if not improved:break
    x1=max(p['shape'].bounds[2] for p in rows)
    y1=max(p['shape'].bounds[3] for p in rows)
    for p in rows:
        p['x']=p['shape'].bounds[0];p['y']=p['shape'].bounds[1]
    result=_summarize(rows,[(x1+margin,y1+margin)],margin,gap,'Material-first coordinate polishing')
    if material_key(result)<material_key(layout) and _validate(result):
        return result
    return layout


def reusable_offcuts(layout, *, threshold_pct=7, count=3, kerf_mm=None, min_neck_mm=8):
    """Up to 2/3 true nonoverlapping polygonal offcuts, threshold per sheet.

    Results carry area_pct and are geometric-only, not guaranteed separable.
    """
    if not 0<=threshold_pct<=100:raise ValueError('threshold_pct must be 0..100')
    if count not in (2,3):raise ValueError('count must be 2 or 3')
    source=irregular_remnants(layout,kerf_mm=(float(layout['gap_mm']) if kerf_mm is None else kerf_mm),
                  min_neck_mm=min_neck_mm,min_area_mm2=0.)
    selected=[]
    for entry in source:
        sheet=layout['sheets'][entry['sheet_index']]
        area=float(sheet['width_mm'])*float(sheet['height_mm'])
        fraction=100*entry['area_mm2']/area if area>0 else 0
        if fraction+1e-8>=threshold_pct:
            selected.append(dict(entry,area_pct=round(fraction,2)))
        if len(selected)>=count:break
    return selected
