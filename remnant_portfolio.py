"""Reusable free-space analysis for multiple NONOVERLAPPING rectangular offcuts.

Computes true polygonal free metal. Rectangle selection uses a finite candidate
coordinate grid and is conservative (all rectangles verified against free shape).
Useful remnants are geometrical candidates, not certified removable blanks.
"""
from __future__ import annotations
from shapely.geometry import box, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep
from remnant_polygonal import polygonal_remnants


def _rectangles_in_space(space, grid=22, max_rectangles=3, min_dimension=5., min_area=50.):
    if space.is_empty:return []
    # Vertices of the actual remaining space + adaptive regularly spaced grid.
    bounds=space.bounds
    if len(bounds)!=4:return []
    x0,y0,x1,y1=bounds
    xs={x0,x1};ys={y0,y1}
    polygons=[space] if space.geom_type=='Polygon' else [p for p in getattr(space,'geoms',()) if p.geom_type=='Polygon']
    for polygon in polygons:
        for ring in [polygon.exterior,*polygon.interiors]:
            coords=list(ring.coords)
            step=max(1,len(coords)//24)
            for x,y in coords[::step]:
                xs.add(x);ys.add(y)
    for i in range(1,grid):
        xs.add(x0+(x1-x0)*i/grid)
        ys.add(y0+(y1-y0)*i/grid)
    def thin(vals,cap=44):
        vals=sorted(vals)
        if len(vals)<=cap:return vals
        return sorted({vals[round(i*(len(vals)-1)/(cap-1))] for i in range(cap)})
    xs=thin(xs);ys=thin(ys)
    remaining=space
    rectangles=[]
    # Iterative greedy extraction finds non-overlapping multi-rectangle candidates;
    # NEVER sum overlapping rectangles or count a pocket twice.
    for _ in range(max_rectangles):
        solid=prep(remaining)
        heights=[0.]*(len(xs)-1)
        best=None;best_area=0.
        for j in range(len(ys)-1):
            dy=ys[j+1]-ys[j]
            for i in range(len(xs)-1):
                if xs[i+1]-xs[i]<1e-6 or dy<1e-6:
                    heights[i]=0.;continue
                eps=min(1e-7,(xs[i+1]-xs[i])/20,dy/20)
                cell=box(xs[i]+eps,ys[j]+eps,xs[i+1]-eps,ys[j+1]-eps)
                heights[i]=heights[i]+dy if solid.covers(cell) else 0.
            stack=[]
            for i in range(len(heights)+1):
                height=heights[i] if i<len(heights) else 0.
                start=i
                while stack and stack[-1][1]>height+1e-8:
                    index,prior=stack.pop();start=index
                    width=xs[i]-xs[index]
                    area=width*prior
                    if area>best_area and prior>=min_dimension and width>=min_dimension:
                        rect=box(xs[index],ys[j+1]-prior,xs[i],ys[j+1])
                        # Tolerate only tiny floating-point artefacts (no collision).
                        if remaining.buffer(1e-7).covers(rect):
                            best=rect;best_area=area
                if not stack or stack[-1][1]<height-1e-8:
                    stack.append((start,height))
        if best is None or best_area<min_area:break
        rectangles.append(best)
        remaining=remaining.difference(best)
    return rectangles


def remnant_metrics(layout,grid=22,max_rectangles=3,min_dimension=5.):
    """Sheet-level metrics, rectangles and polygonal free components.

    Counts removable **candidate** regions geometrically; islands within a
    closed part opening can be components but may require extra cutting.
    """
    results=[]
    for sheet in layout['sheets']:
        sid=sheet['index'];w=float(sheet['width_mm']);h=float(sheet['height_mm'])
        parts=[p for p in layout['placements'] if p.get('sheet_index',0)==sid]
        if layout.get('stock_outline') is not None and sid==0:
            usable=layout['stock_outline'].buffer(-float(layout['margin_mm']))
            from shapely.ops import unary_union
            geom=usable.difference(unary_union([p['shape'].buffer(float(layout['gap_mm'])/2) for p in parts]))
            components=[geom] if geom.geom_type=='Polygon' else [g for g in getattr(geom,'geoms',()) if g.geom_type=='Polygon']
            biggest=max((c.area for c in components),default=0.)
        else:
            geom,biggest,count=polygonal_remnants(parts,w,h,float(layout['margin_mm']),float(layout['gap_mm'])/2)
            components=[geom] if geom.geom_type=='Polygon' else [g for g in getattr(geom,'geoms',()) if g.geom_type=='Polygon']
        components=sorted([g for g in components if g.area>1e-6],key=lambda g:g.area,reverse=True)
        rectangles=_rectangles_in_space(geom,grid=grid,max_rectangles=max_rectangles,min_dimension=min_dimension)
        areas=[float(r.area) for r in rectangles]
        # primary area is total of independent nonoverlapping rectangles.
        results.append({'sheet_index':sid,'largest_rectangle_mm2':areas[0] if areas else 0.,
           'combined_rectangles_mm2':sum(areas), 'rectangle_count':len(rectangles),
           'largest_connected_mm2':max((p.area for p in components),default=0.),
           'fragment_count':len(components),'free_area_mm2':float(geom.area),
           'rectangles':rectangles,'free_geometry':geom})
    return results


def metrics_summary(layout,grid=18):
    sheets=remnant_metrics(layout,grid=grid)
    total=lambda k:sum(z[k] for z in sheets)
    return {'sheets':layout['sheet_count'],'sheet_area_mm2':round(layout['sheet_mm2'],2),
      'utilization_pct':round(layout['utilization'],2),
      'largest_rect_mm2':round(max((z['largest_rectangle_mm2'] for z in sheets),default=0.),2),
      'top_rectangles_total_mm2':round(total('combined_rectangles_mm2'),2),
      'largest_connected_mm2':round(max((z['largest_connected_mm2'] for z in sheets),default=0.),2),
      'fragment_count':total('fragment_count')}
