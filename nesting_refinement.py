"""Geometry-validated post-nesting search.

Optimizes an already legal packing without ever scaling, mirroring or changing
part geometry. A strong rectangle-offcut preference is optional. All sizes mm.
Results are heuristic, not proofs of global optimality / machine-ready CAM.
"""
from __future__ import annotations
from shapely import affinity
from functools import lru_cache
from shapely.geometry import box
from shapely.ops import unary_union
from sheet_nesting import _orient
from remnant_optimization import largest_free_rectangle


def _free_connected(placements, width, height, margin, gap):
    """Exact polygonal largest free component; holes count as disconnected scrap.

    Excludes clearance around parts and sheet edges; areas are geometric
    estimates, not guarantees a cut-off is recoverable or physically supported.
    """
    stock=box(margin,margin,width-margin,height-margin)
    parts=[p['shape'].buffer(gap/2,join_style=2) for p in placements]
    free=stock.difference(unary_union(parts)) if parts else stock
    polys=[free] if free.geom_type=='Polygon' else [g for g in getattr(free,'geoms',[]) if g.geom_type=='Polygon']
    areas=sorted([g.area for g in polys if g.area>1e-5],reverse=True)
    return (areas[0] if areas else 0.,len(areas),sum(areas[1:]) if len(areas)>1 else 0.)


def score_layout(placements,width,height,margin,gap,preserve):
    """Lexicographic, on fixed sheet: usable rectangular offcut first if on.

    No offcut priority: minimize occupied bbox and lightly favor compact grouping.
    Priorities deliberately differ, so the UI checkbox changes the computation.
    """
    if not placements:return (0.,)
    aa=min(p['shape'].bounds[0] for p in placements)
    bb=min(p['shape'].bounds[1] for p in placements)
    cc=max(p['shape'].bounds[2] for p in placements)
    dd=max(p['shape'].bounds[3] for p in placements)
    footprint=(cc-aa)*(dd-bb)
    if not preserve:
        # Pack the entire collection, not just into the bottom-left corner.
        return (-round(footprint,5), -round((cc-aa)+(dd-bb),5))
    _,rect_area=largest_free_rectangle(placements,width,height,margin=margin,clearance=gap/2)
    connected,fragment_count,other_area=_free_connected(placements,width,height,margin,gap)
    return (round(rect_area,5),round(connected,5),-fragment_count,-round(other_area,5),-round(footprint,5))


@lru_cache(maxsize=800)
def _cached_cavities(shape):
    from nesting_advanced import _cavity_regions
    return tuple(_cavity_regions(shape))


def _candidate_xy(placed,ref,W,H,margin,gap,*,level=1):
    """Contact-guided translations, including all four edges and cavity boundaries.

    No fixed raster: pair vertex-to-vertex alignments and bbox adjacency enable
    much tighter groupings for curved / asymmetric DXF parts.
    """
    w,h=ref.bounds[2],ref.bounds[3]
    max_x=W-margin-w; max_y=H-margin-h
    xs=[margin,max_x,(margin+max_x)/2]
    ys=[margin,max_y,(margin+max_y)/2]
    # Include shifts to actual corners, sides, and around every placed object.
    coords={(round(x,4),round(y,4)) for x in xs for y in ys}
    recent=placed[:]
    for p in recent:
        a,b,c,d=p['shape'].bounds
        xpos=(a-w-gap,a-w,a,a+gap,c+gap,c-w,c-w-gap,c)
        ypos=(b-h-gap,b-h,b,b+gap,d+gap,d-h,d-h-gap,d)
        for x in xpos:
            for y in ys+[b,d+gap,b-h,d-h-gap,(b+d-h)/2]:coords.add((round(x,4),round(y,4)))
        for y in ypos:
            for x in xs+[a,c+gap,a-w,c-w-gap,(a+c-w)/2]:coords.add((round(x,4),round(y,4)))
        # Try true polygon vertex contacts rather than bounding boxes only.
        # Explicitly examine holes and open concave bays at *every* quality;
        # bbox-contact candidates alone do not discover their interior.
        for cavity in _cached_cavities(p['shape']):
            u,v,r,t=cavity.bounds
            if (r-u)>=w+2*gap-1e-5 and (t-v)>=h+2*gap-1e-5:
                cp=cavity.representative_point()
                for xx,yy in ((u+gap,v+gap),(r-w-gap,v+gap),(u+gap,t-h-gap),
                              (r-w-gap,t-h-gap),((u+r-w)/2,(v+t-h)/2),
                              (cp.x-w/2,cp.y-h/2)):
                    coords.add((round(xx,4),round(yy,4)))
        if level>=2:
            shell=list(p['shape'].exterior.coords)[:-1]
            inner=list(ref.exterior.coords)[:-1]
            skip_old=max(1,len(shell)//12)
            skip_new=max(1,len(inner)//10)
            offsets=((gap,0),(-gap,0),(0,gap),(0,-gap))
            for ox,oy in shell[::skip_old][:13]:
                for nx,ny in inner[::skip_new][:11]:
                    for dx,dy in offsets:
                        coords.add((round(ox-nx+dx,4),round(oy-ny+dy,4)))
            # Holes and concave bays are additionally sampled for placements.
            for ring in p['shape'].interiors:
                pts=list(ring.coords)[:-1]
                for ox,oy in pts[::max(1,len(pts)//12)][:13]:
                    for nx,ny in inner[::skip_new][:11]:
                        coords.add((round(ox-nx+gap,4),round(oy-ny,4)))
    # Avoid >thousands of expensive exact polygon collision tests per move.
    filtered=[(x,y) for x,y in coords if x>=margin-1e-6 and y>=margin-1e-6
              and x<=max_x+1e-6 and y<=max_y+1e-6]
    # Deterministic and spread across the whole space, not only bottom-left.
    return sorted(filtered,key=lambda pos:(pos[0]+pos[1],pos[1],pos[0]))


def _diverse_candidates(coords, limit, width, height, *, keep=None):
    """Spatially stratified candidate budget.

    The previous `coords[::stride]` applied after sorting by x+y: this
    disproportionately skipped the upper/right side of the sheet, i.e. the
    very contact and cavity placements an optimizer needs to compare.
    Keep candidates near all four corners and spread the remainder over
    the sheet. Exact geometry validation still applies later.
    """
    coords=list(dict.fromkeys(coords))
    if len(coords)<=limit:return coords
    if limit<8:return coords[:limit]
    chosen=[];used=set()
    def add(point):
        if point not in used and len(chosen)<limit:
            used.add(point);chosen.append(point)
    if keep is not None:add(keep)
    for key in (lambda p:(p[0]+p[1]),lambda p:-(p[0]+p[1]),
                lambda p:(p[0]-p[1]),lambda p:-(p[0]-p[1]),
                lambda p:p[0],lambda p:-p[0],lambda p:p[1],lambda p:-p[1]):
        add(min(coords,key=key))
    # Spatial buckets before round-robin sampling: every quadrant and strip
    # keeps candidates even when 90% of vertex contacts lie near one corner.
    nx=ny=5
    groups={}
    for point in coords:
        bx=min(nx-1,max(0,int(nx*point[0]/max(width,1e-9))))
        by=min(ny-1,max(0,int(ny*point[1]/max(height,1e-9))))
        groups.setdefault((bx,by),[]).append(point)
    keys=sorted(groups)
    k=0
    while len(chosen)<limit and keys:
        remaining=[]
        for key in keys:
            arr=groups[key]
            while arr and arr[0] in used:arr.pop(0)
            if arr:add(arr.pop(0))
            if arr:remaining.append(key)
            if len(chosen)>=limit:break
        keys=remaining;k+=1
        if k>limit:break
    return chosen


def _valid(shape,others,gap,usable):
    if not usable.covers(shape):return False
    sx,sy,ex,ey=shape.bounds
    for old in others:
        other=old['shape'];a,b,c,d=other.bounds
        if sx>=c+gap-1e-7 or ex<=a-gap+1e-7 or sy>=d+gap-1e-7 or ey<=b-gap+1e-7:continue
        if shape.intersection(other).area>1e-6 or (gap>0 and shape.distance(other)<gap-1e-6):return False
    return True


def refine_sheet(placements,W,H,margin,gap,quality='balanced',preserve=True,stock_outline=None):
    """Iterative rotation + translation search with full collision validation.

    Each accepted step improves the chosen quality objective; only the small-job
    case is exhaustively explored. Large batches intentionally use a budget.
    """
    placements=[dict(p) for p in placements]
    n=len(placements)
    if n<2 or n>14 or stock_outline is not None:return placements
    # Fast mode limits work, while 'precision' invests in rotations & candidates.
    angles={'fast':(0,90,180,270),'balanced':(0,90,180,270),
            'thorough':tuple(range(0,360,45)),
            'precision':tuple(range(0,360,15))}[quality]
    if n>8:
        angles=tuple(range(0,360,90))
    sweeps={'fast':1,'balanced':2,'thorough':2,'precision':3}[quality]
    cap={'fast':40,'balanced':95,'thorough':140,'precision':200}[quality]
    if n>8:cap=50
    stock=box(margin,margin,W-margin,H-margin)
    score=score_layout(placements,W,H,margin,gap,preserve)
    for turn in range(sweeps):
        moved=False
        # Largest parts, cavity hosts and small mobile parts get chances to move.
        order=sorted(range(n),key=lambda i:placements[i]['shape'].area,
                     reverse=(turn%2==1))
        for i in order:
            original=placements[i];others=[p for j,p in enumerate(placements) if j!=i]
            alternatives=[]
            # Current orientation has priority so small nudges aren't defeated by rotations.
            ordered_angles=(original['angle'],)+tuple(a for a in angles if a!=original['angle'])
            for angle in ordered_angles:
                ref=_orient(original['shape'],angle-original['angle'])
                pts=_candidate_xy(others,ref,W,H,margin,gap,level=2 if n<=8 and quality in ('thorough','precision') else 1)
                # Current anchor and a small set of global shifts matter for local improvements.
                pts=[(round(original['shape'].bounds[0],4),round(original['shape'].bounds[1],4))]+pts
                if len(pts)>cap:
                    pts=_diverse_candidates(pts,cap,W,H,keep=pts[0])
                for x,y in pts:
                    shape=affinity.translate(ref,xoff=x,yoff=y)
                    if not _valid(shape,others,gap,stock):continue
                    # Search for a candidate with strong conservative bbox score first.
                    bnd=[p['shape'].bounds for p in others]+[shape.bounds]
                    minx=min(b[0] for b in bnd);maxx=max(b[2] for b in bnd)
                    miny=min(b[1] for b in bnd);maxy=max(b[3] for b in bnd)
                    footprint=(maxx-minx)*(maxy-miny)
                    if not preserve and -footprint<score[0]-1e-5:continue
                    row=dict(original,shape=shape,x=x,y=y,angle=(angle%360))
                    alternatives.append((footprint,row))
            # Full remnant geometry evaluation is expensive. Evaluate diverse
            # footprints + best few by bbox first (not just first encounter).
            alternatives.sort(key=lambda pair:pair[0])
            shortlisted=alternatives[:max(10,cap//4)]
            if preserve and len(alternatives)>len(shortlisted):
                # Include spread-out candidates; a bigger rectangular scrap can
                # be created by moving a part toward another sheet corner.
                # Add placements from the entire spatial distribution, not
                # just low-footprint candidates (which share a common bias).
                diverse=_diverse_candidates([(c['x'],c['y']) for _,c in alternatives],
                    min(24,max(12,cap//3)),W,H)
                picked=set(diverse)
                shortlisted += [(a,c) for a,c in alternatives if (c['x'],c['y']) in picked][:24]
            best_row=None;best_score=score
            seen=set()
            for _,cand in shortlisted:
                k=(round(cand['x'],4),round(cand['y'],4),cand['angle'])
                if k in seen:continue
                seen.add(k)
                trial=placements[:];trial[i]=cand
                sc=score_layout(trial,W,H,margin,gap,preserve)
                if sc>best_score:
                    best_score=sc;best_row=cand
            if best_row is not None:
                placements[i]=best_row;score=best_score;moved=True
        if not moved:break
    return placements
