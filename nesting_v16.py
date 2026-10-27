"""Shape-aware directional contact compaction for existing nestings.

Each solution is revalidated against source polygons and sheet margins.
Neighbour repositioning and a limited portfolio of layout goals avoid
single-metric convergence; source layout is always preserved.
"""
from __future__ import annotations
from math import ceil, hypot
from shapely import affinity
from shapely.geometry import box
from nesting_refinement import _valid
from optimizer_v15 import _rebuild, _validate, _signature
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

DIRECTIONS=((1,0),(-1,0),(0,1),(0,-1),
            (1,1),(-1,1),(1,-1),(-1,-1))


def _contact_slide(shape,others,stock,gap,dx,dy,steps=20):
    """Slide to FIRST geometric contact; bisection refines its position.

    Uses actual polygons, not a rectangular envelope. The movement is relative
    to the *current* position and never accepts a collision.
    """
    x0,y0,x1,y1=shape.bounds
    sx,sy,ex,ey=stock.bounds
    d=hypot(dx,dy);dx/=d;dy/=d
    bounds=[]
    if dx>0:bounds.append((ex-x1)/dx)
    if dx<0:bounds.append((sx-x0)/dx)
    if dy>0:bounds.append((ey-y1)/dy)
    if dy<0:bounds.append((sy-y0)/dy)
    max_t=min(bounds) if bounds else 0
    if max_t<=1e-7:return shape
    # Broadphase bbox avoids calling Shapely intersection on distant polygons.
    # Sample to find the earliest invalid point, then narrow the bracket.
    good=0.;bad=None
    for i in range(1,steps+1):
        t=max_t*i/steps
        moved=affinity.translate(shape,xoff=dx*t,yoff=dy*t)
        if _valid(moved,others,gap,stock):good=t
        else:
            bad=t;break
    if bad is not None:
        for _ in range(17):
            mid=(good+bad)/2
            moved=affinity.translate(shape,xoff=dx*mid,yoff=dy*mid)
            if _valid(moved,others,gap,stock):good=mid
            else:bad=mid
    return affinity.translate(shape,xoff=dx*good,yoff=dy*good)


def _score(layout,goal,auto,base_area):
    area=layout['sheet_mm2']
    if goal=='compact':
        # For fixed stock every arrangement has the same sheet area. Use
        # occupied group envelope to drive actual directional compaction.
        bounds=[p['shape'].bounds for p in layout['placements']]
        footprint=(max(q[2] for q in bounds)-min(q[0] for q in bounds))*\
                  (max(q[3] for q in bounds)-min(q[1] for q in bounds))
        return (-area,-footprint) if auto else (-footprint,)
    if auto and area>base_area*1.06:return (-float('inf'),)
    # Cheap *conservative* cell raster derived from true polygon free space.
    # This avoids expensive repeated polygon differences for every candidate.
    # Final alternatives are rescored with true polygonal remnants.
    remnants=0.; circle=0.; connected=0.
    for sheet in layout['sheets']:
        sid=sheet['index'];w=sheet['width_mm'];h=sheet['height_mm']
        safe=box(layout['margin_mm'],layout['margin_mm'],w-layout['margin_mm'],h-layout['margin_mm'])
        obstacles=[p['shape'].buffer(layout['gap_mm']/2,quad_segs=4)
                   for p in layout['placements'] if p.get('sheet_index',0)==sid]
        free=safe.difference(unary_union(obstacles)) if obstacles else safe
        if free.is_empty:continue
        components=[free] if free.geom_type=='Polygon' else [p for p in getattr(free,'geoms',()) if p.geom_type=='Polygon']
        connected=max(connected,max((p.area for p in components),default=0.))
        a,b,c,d=safe.bounds
        nx=ny=9;dx=(c-a)/nx;dy=(d-b)/ny
        covered=prep(free)
        cells=[]
        for iy in range(ny):
            row=[]
            for ix in range(nx):
                x=a+ix*dx;y=b+iy*dy
                row.append(covered.covers(box(x+1e-6,y+1e-6,x+dx-1e-6,y+dy-1e-6)))
            cells.append(row)
        # Largest 3 independent raster-aligned rectangles (no overlap).
        def take_best():
            best=(0,0,0,0,0)
            for x0 in range(nx):
                for x1 in range(x0+1,nx+1):
                    streak=0
                    for y in range(ny):
                        if all(cells[y][x] for x in range(x0,x1)):
                            streak+=1
                            area=streak*(x1-x0)
                            if area>best[0]:best=(area,x0,x1,y+1-streak,y+1)
                        else:streak=0
            _,x0,x1,y0,y1=best
            for yy in range(y0,y1):
                for xx in range(x0,x1):cells[yy][xx]=False
            return best[0]*dx*dy
        rectangles=take_best()+take_best()+take_best()
        remnants+=rectangles
        for ix in range(1,nx):
            for iy in range(1,ny):
                p=Point(a+ix*dx,b+iy*dy)
                if covered.covers(p):
                    r=free.boundary.distance(p)
                    circle=max(circle,3.141592653589793*r*r)
    if goal=='rectangles':return (remnants,-area)
    if goal=='mixed':return (remnants+circle*0.2,-area)
    return (remnants,connected,-area)

def directional_refine(seed,*,auto,goal='mixed',rounds=2,angle_step=90,
                       candidate_cap=7):
    """Coordinate descent with shape-contact pushes and rotated reseeding.

    Scores alternatives by selected goal rather than always by bounding box.
    A rejected attempt leaves the original valid layout untouched.
    """
    if seed['sheet_count']!=1 or len(seed['placements'])>12 or not seed['placements']:
        return seed
    margin=seed['margin_mm'];gap=seed['gap_mm']
    W=seed['sheets'][0]['width_mm'];H=seed['sheets'][0]['height_mm']
    # During automatic search, allow a small temporary expansion, then crop.
    if auto:
        W*=1.06;H*=1.06
    stock=box(margin,margin,W-margin,H-margin)
    base_area=seed['sheet_mm2']
    best=seed;best_score=_score(seed,goal,auto,base_area)
    placed=list(seed['placements'])
    for sweep in range(rounds):
        improved=False
        order=sorted(range(len(placed)),key=lambda i:placed[i]['shape'].area,
                     reverse=(sweep%2==1))
        for index in order:
            part=placed[index]
            others=[p for j,p in enumerate(placed) if j!=index]
            try:
                trial_shapes=[]
                # First test the user's requested horizontal/vertical push.
                for vx,vy in DIRECTIONS:
                    s=_contact_slide(part['shape'],others,stock,gap,vx,vy)
                    trial_shapes.append((s,part['angle']))
                if angle_step:
                    # Rotations are followed by a *contact push* toward corners.
                    for da in (angle_step,-angle_step):
                        sh=affinity.rotate(part['shape'],da,origin='center')
                        x0,y0,_,_=sh.bounds
                        sh=affinity.translate(sh,xoff=part['x']-x0,yoff=part['y']-y0)
                        if _valid(sh,others,gap,stock):
                            trial_shapes.append((sh,(part['angle']+da)%360))
                            for vx,vy in DIRECTIONS[:4]:
                                trial_shapes.append((_contact_slide(sh,others,stock,gap,vx,vy),
                                                     (part['angle']+da)%360))
                for sh,ang in trial_shapes[:candidate_cap]:
                    if sh.equals_exact(part['shape'],1e-4) or not _valid(sh,others,gap,stock):continue
                    row=dict(part,shape=sh,x=sh.bounds[0],y=sh.bounds[1],angle=ang)
                    candidate_rows=placed[:];candidate_rows[index]=row
                    fresh=_rebuild(candidate_rows,seed,auto,'v1.6 polygonal contact refinement')
                    if not _validate(fresh):continue
                    sc=_score(fresh,goal,auto,base_area)
                    if sc>best_score:
                        best_score=sc;best=fresh;improved=True
                        placed=list(fresh['placements'])
                        if auto:W,H=best['sheets'][0]['width_mm']*1.06,best['sheets'][0]['height_mm']*1.06
                        stock=box(margin,margin,W-margin,H-margin)
            except Exception:
                # A malformed candidate must not invalidate the selected seed.
                continue
        if not improved:break
    return best


def expanded_candidates(seed,auto,quality='balanced'):
    """Include original plus differently motivated refinements.

    Fixed-size sheets get larger search budgets than auto-sized sheets because
    compactness cannot improve the material dimensions of a fixed sheet.
    """
    out=[seed];seen={_signature(seed)}
    if seed['sheet_count']!=1 or len(seed['placements'])>10 or quality=='fast':return out
    goals=('compact','rectangles','mixed')
    rounds=1 if quality=='balanced' else 2
    for goal in goals:
        candidate=directional_refine(seed,auto=auto,goal=goal,rounds=rounds)
        sig=_signature(candidate)
        if sig not in seen:
            seen.add(sig);out.append(candidate)
    return out
