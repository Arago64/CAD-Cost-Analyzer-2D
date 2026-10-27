"""Collision-tested polygon nesting for 2D sheet cutting (mm).

Supports cut-outs and open concave bays; does not claim globally optimal packing.
No automatic CAM/fixturing or cut sequence certification is implied.
"""
from __future__ import annotations
from math import ceil, sqrt
import random
from shapely import affinity
from shapely.geometry import Polygon, MultiPolygon, Point, box
from sheet_nesting import _input_items, _orient, STANDARD_SHEETS


def _envelope_area(poly):
    b=poly.bounds
    return (b[2]-b[0])*(b[3]-b[1])


def _pocket_area(poly):
    """Free regions *within the convex hull*: both holes and concave recesses."""
    return max(0.,poly.convex_hull.area-poly.area)


def _sort_items(items,mode,seed):
    items=list(items)
    if mode=='container':
        return sorted(items,key=lambda v:(-_pocket_area(v[1]),-_envelope_area(v[1]),-v[1].area,v[0]))
    if mode=='footprint':
        return sorted(items,key=lambda v:(-_envelope_area(v[1]),-v[1].area,v[0]))
    if mode=='area':return sorted(items,key=lambda v:(-v[1].area,v[0]))
    if mode=='length':return sorted(items,key=lambda v:(-max(v[1].bounds[2]-v[1].bounds[0],v[1].bounds[3]-v[1].bounds[1]),-v[1].area))
    if mode=='perimeter':return sorted(items,key=lambda v:(-v[1].length,v[0]))
    rng=random.Random(seed);rng.shuffle(items)
    return items


def _rings(poly):
    return [poly.exterior]+list(poly.interiors)


def _sample_ring(ring,max_points=24):
    """Include corners and uniformly spaced edge points on curved polygons."""
    coords=list(ring.coords)[:-1]
    if len(coords)<=max_points:
        return [(float(x),float(y)) for x,y in coords]
    from shapely.geometry import LineString
    line=LineString(ring.coords)
    return [(p.x,p.y) for p in (line.interpolate(i*line.length/max_points) for i in range(max_points))]


def _cavity_regions(poly):
    """Find fully cut-out holes and concave pockets, not simply the overall bbox."""
    cavity=poly.convex_hull.difference(poly)
    if cavity.is_empty:return []
    if isinstance(cavity,Polygon):return [cavity] if cavity.area>1e-4 else []
    if isinstance(cavity,MultiPolygon):return [p for p in cavity.geoms if p.area>1e-4]
    return [p for p in cavity.geoms if isinstance(p,Polygon) and p.area>1e-4] if hasattr(cavity,'geoms') else []


def _base_candidates(placed,margin,gap,width,height):
    pts={(round(float(margin),4),round(float(margin),4))}
    for item in placed:
        a,b,c,d=item['shape'].bounds
        for x,y in ((a,b),(c+gap,b),(a,d+gap),(c+gap,d+gap),
                    (c+gap,margin),(margin,d+gap),(a,d+gap),(c+gap,b)):
            pts.add((round(x,4),round(y,4)))
    return pts


def _cavity_candidates(placed,ref,gap,pocket_cache):
    """Center and near-align a part in real internal cutouts or open concavities."""
    _,_,w,h=ref.bounds
    pts=set()
    for old in placed:
        for pocket in pocket_cache[id(old['shape'])]:
            a,b,c,d=pocket.bounds
            if c-a+1e-7<w+2*gap or d-b+1e-7<h+2*gap:continue
            # The centroid is not guaranteed to be inside a crescent-shaped bay;
            # the exact shape collision test is performed later in any case.
            cp=pocket.representative_point()
            locs=[((a+c-w)/2,(b+d-h)/2),
                  (a+gap,b+gap), (c-w-gap,b+gap),
                  (a+gap,d-h-gap), (c-w-gap,d-h-gap),
                  (cp.x-w/2,cp.y-h/2)]
            for x,y in locs:pts.add((round(x,4),round(y,4)))
    return pts


def _boundary_candidates(placed,ref,gap,fast_mode):
    """Vertex contacts beyond bbox corners enable irregular interlocking.

    Generate translation candidates from pairs of boundary points, with clearance
    offsets. The subsequent *exact polygon* intersection/distance check decides
    validity; these points never bypass gap or sheet margins.
    """
    pts=set()
    max_old=12 if fast_mode else 22
    max_new=7 if fast_mode else 14
    new_coords=[]
    for ring in _rings(ref):
        new_coords.extend(_sample_ring(ring,max_new))
    # Include reference bbox corners, helpful for triangles and other tapers.
    if len(new_coords)>max_new*3:new_coords=new_coords[:max_new*3]
    shifts=[(0,gap),(gap,0),(-gap,0),(0,-gap)] if not fast_mode else [(gap,0),(0,gap)]
    for p in placed:
        for ring in _rings(p['shape']):
            for ox,oy in _sample_ring(ring,max_old):
                for nx,ny in new_coords:
                    for dx,dy in shifts:
                        pts.add((round(ox-nx+dx,4),round(oy-ny+dy,4)))
    return pts


def _can_fit(shape,x,y,w,h,placed,gap):
    for old in placed:
        a,b,c,d=old['shape'].bounds
        # Cheap rejection where bboxes are separated by the required clearance.
        if x>=c+gap-1e-7 or y>=d+gap-1e-7 or x+w<=a-gap+1e-7 or y+h<=b-gap+1e-7:
            continue
        other=old['shape']
        if shape.intersection(other).area>1e-7 or (gap>0 and shape.distance(other)<gap-1e-6):
            return False
    return True


def _pack(items,width,height,margin,gap,angles,use_holes=False,allow_partial=False,fast_mode=False,stock_outline=None,preserve_remnants=True,anchor='BL'):
    if width<=2*margin or height<=2*margin:raise ValueError('Blech kleiner als Randabstand.')
    placed=[];leftovers=[];cache={};pocket_cache={}
    usable=stock_outline.buffer(-margin) if stock_outline is not None else None
    for label,poly in items:
        key=id(poly)
        if key not in cache:cache[key]=[(angle,_orient(poly,angle)) for angle in angles]
        best=None
        has_pockets=use_holes and any(pocket_cache.get(id(p['shape'])) for p in placed)
        base=_base_candidates(placed,margin,gap,width,height)
        # Include placements anchored to *every* sheet corner, not just (margin,margin).
        # Corners are actual translations, not forbidden mirrored part geometries.
        anchor_x=width-margin
        anchor_y=height-margin

        max_old_x=max((p['shape'].bounds[2] for p in placed),default=margin)
        max_old_y=max((p['shape'].bounds[3] for p in placed),default=margin)
        for angle,ref in cache[key]:
            _,_,w,h=ref.bounds
            if w>width-2*margin+1e-7 or h>height-2*margin+1e-7:continue
            points=set(base)
            if not fast_mode:
                points.update({(margin,margin),(width-margin-w,margin),
                               (margin,height-margin-h),(width-margin-w,height-margin-h)})
            # Large batches keep the proven fast strip-packing candidates.
            for old in (placed if not fast_mode else []):
                a,b,c,d=old['shape'].bounds
                for xx in (a-w-gap,a-w,a,c,c+gap,c-w,c-w-gap):
                    for yy in (margin,height-margin-h,b,d+gap,d-h,d-h-gap):
                        points.add((round(xx,4),round(yy,4)))
                for yy in (b-h-gap,b-h,b,d,d+gap,d-h,d-h-gap):
                    for xx in (margin,width-margin-w,a,c+gap,c-w,c-w-gap):
                        points.add((round(xx,4),round(yy,4)))
            if stock_outline is not None:
                # Irregular remnant: add stock vertices and spaced edge samples.
                for px,py in _sample_ring(stock_outline.exterior,60):
                    points.add((round(px+margin,4),round(py+margin,4)))
                    points.add((round(px-w/2,4),round(py-h/2,4)))
                minx,miny,maxx,maxy=stock_outline.bounds
                for ii in range(12):
                    for jj in range(12):
                        points.add((round(minx+margin+(maxx-minx-2*margin-w)*ii/11,4),
                                    round(miny+margin+(maxy-miny-2*margin-h)*jj/11,4)))
                center=stock_outline.representative_point()
                points.add((round(center.x-w/2,4),round(center.y-h/2,4)))
            if has_pockets:
                points.update(_cavity_candidates(placed,ref,gap,pocket_cache))
                # Edge-to-edge matching is costly; use it on small jobs only
                # and only if an actual cavity is present.
                if not fast_mode and len(items)<=6:
                    hosts=[p for p in placed if pocket_cache.get(id(p['shape']))]
                    points.update(_boundary_candidates(hosts,ref,gap,False))
            points=sorted((x,y) for x,y in points if
                x>=margin-1e-7 and y>=margin-1e-7 and x+w<=width-margin+1e-7 and y+h<=height-margin+1e-7)
            if anchor=='BR':points.sort(key=lambda v:(v[1],-v[0]))
            elif anchor=='TR':points.sort(key=lambda v:(-v[1],-v[0]))
            elif anchor=='TL':points.sort(key=lambda v:(-v[1],v[0]))
            else:points.sort(key=lambda v:(v[1],v[0]))
            if fast_mode:
                # Sampling by row rather than global truncation prevents starving
                # late rows on sheets with dozens of repeated parts.
                per_row={};selected=[]
                for x,y in points:
                    row=round(y,2)
                    if per_row.get(row,0)>=10:continue
                    per_row[row]=per_row.get(row,0)+1;selected.append((x,y))
                    if len(selected)>=110:break
                points=selected
            elif len(points)>(750 if len(items)>5 else 1700):
                # Keep pocket-center candidates and coarse global sampling.
                cap=(750 if len(items)>5 else 1700)
                stride=ceil(len(points)/cap);points=points[::stride]
            for x,y in points:
                bx=max(x+w,max_old_x)+margin
                by=max(y+h,max_old_y)+margin
                # Preserve one large, rectangular offcut: favor tight grouping
                # along the bottom/left edges, leaving a full-width or
                # full-height reusable strip. This is only a fast proxy; the
                # exact conservative rectangle is measured after packing.
                maxx=max(max_old_x,x+w)
                maxy=max(max_old_y,y+h)
                strip=max(max(0.,width-margin-maxx)*max(0.,height-2*margin),
                          max(0.,height-margin-maxy)*max(0.,width-2*margin))
                # Greedy phase clusters toward the chosen corner. Afterwards
                # local refinement uses true free-region geometry to choose.
                anchor_dist={'BL':x+y, 'BR':(width-w-x)+y,
                             'TL':x+(height-h-y),
                             'TR':(width-w-x)+(height-h-y)}[anchor]
                score=(-strip,anchor_dist,bx*by,by,bx,y,x,angle) if preserve_remnants else (
                    (bx*by,by,bx,y,x,angle) if anchor=='BL' else
                    (anchor_dist,bx*by,by,bx,y,x,angle))
                if best is not None and score>=best[0]:continue
                shape=affinity.translate(ref,xoff=x,yoff=y)
                if usable is not None and not usable.covers(shape):continue
                if not _can_fit(shape,x,y,w,h,placed,gap):continue
                best=(score,shape,x,y,angle)
                if fast_mode and not has_pockets:break
            if fast_mode and best is not None and not has_pockets:break
        if best is None:
            leftovers.append((label,poly))
            if not allow_partial:return None,items
        else:
            _,shape,x,y,angle=best
            placed.append({'name':label,'shape':shape,'x':x,'y':y,'angle':angle})
            if use_holes:
                pocket_cache[id(shape)]=_cavity_regions(shape) if _pocket_area(shape)>1e-6 else []
    return placed,leftovers


def _tag_nested(placements):
    """Tag nested parts by *unambiguous placement index*, not their labels.

    True closed holes and open concave pockets count as possible hosts. A
    child is matched to the smallest containing pocket, avoiding ambiguous
    IDs when multiple copies of the same part occur in one sheet.
    """
    pockets=[]
    for j,host in enumerate(placements):
        if host['shape'].is_empty:continue
        for interior in host['shape'].interiors:
            pocket=Polygon(interior)
            if pocket.area>1e-7:pockets.append((pocket.area,j,'hole',pocket))
        for pocket in _cavity_regions(host['shape']):
            if pocket.area>1e-7:pockets.append((pocket.area,j,'recess',pocket))
    for i,p in enumerate(placements):
        p.pop('nested_in',None);p.pop('nest_type',None);p.pop('nested_in_index',None)
        possible=[(area,j,typ) for area,j,typ,pocket in pockets
                  if i!=j and p.get('sheet_index',0)==placements[j].get('sheet_index',0)
                  and pocket.covers(p['shape'])]
        if possible:
            _area,j,typ=min(possible)
            p['nested_in']=placements[j]['name']
            p['nested_in_index']=j
            p['nest_type']=typ


def _summarize(placements,sheets,margin,gap,description):
    area=sum(float(item['shape'].area) for item in placements)
    area_stock=sum(float(w)*float(h) for w,h in sheets)
    for p in placements:p['sheet_index']=int(p.get('sheet_index',0))
    _tag_nested(placements)
    nested=sum('nested_in' in p for p in placements)
    return {'placements':placements,'sheets':[{'index':i,'width_mm':w,'height_mm':h} for i,(w,h) in enumerate(sheets)],
        'sheet_count':len(sheets),'sheet_width_mm':sheets[0][0], 'sheet_height_mm':sheets[0][1],
        'net_mm2':area,'sheet_mm2':area_stock,'waste_mm2':max(0,area_stock-area),
        'utilization':100*area/area_stock if area_stock else 0.,
        'cut_length_mm':sum(float(p['shape'].length) for p in placements),
        'pierces':sum(1+len(p['shape'].interiors) for p in placements),'items':len(placements),
        'nested_items':nested,'algorithm':description,'margin_mm':margin,'gap_mm':gap}


def _tight_auto_result(rows, margin):
    """Crop an auto layout to its true material bbox without changing any part."""
    xmin=min(p['shape'].bounds[0] for p in rows)
    ymin=min(p['shape'].bounds[1] for p in rows)
    dx=margin-xmin;dy=margin-ymin
    moved=[]
    for p in rows:
        shape=affinity.translate(p['shape'],xoff=dx,yoff=dy)
        moved.append(dict(p,shape=shape,x=shape.bounds[0],y=shape.bounds[1]))
    used_w=ceil(max(p['shape'].bounds[2] for p in moved)+margin)
    used_h=ceil(max(p['shape'].bounds[3] for p in moved)+margin)
    return moved,(used_w,used_h)


def advanced_nest(pieces,qtys=None,*,stock_width=None,stock_height=None,margin=5.,gap=3.,
                  auto=True,quality='balanced',hole_nesting=True,multi_sheet=True,stock_outline=None,
                  preserve_remnants=True):
    """Multi-start, shape-contact and local compaction nesting (heuristic).

    Toggle `preserve_remnants` changes the objective: if enabled, use the largest
    usable rectangular offcut plus largest connected free piece. When disabled,
    select smallest occupied bounding box instead. For automatic stock, sheet
    area is the primary metric; offcut preference only breaks near-ties (3.5%).
    """
    from nesting_refinement import refine_sheet, score_layout
    items=_input_items(pieces,qtys)
    if min(margin,gap)<0:raise ValueError('Randabstand / Fugenabstand negativ.')
    if quality not in ('fast','balanced','thorough','precision'):raise ValueError('Ungültige Optimierungsstufe')
    angles={'fast':(0,90),'balanced':(0,90,180,270),
            'thorough':(0,45,90,135,180,225,270,315),
            'precision':tuple(range(0,360,15))}[quality]
    styles={'fast':['area'], 'balanced':['area','footprint','container'],
            'thorough':['area','footprint','container','length','perimeter','random1'],
            'precision':['container','area','footprint','length','perimeter','random1','random2']}[quality]
    if hole_nesting:
        styles=['container','footprint']+[m for m in styles if m not in ('container','footprint')]
    n=len(items)
    fast_mode=n>12
    if n>5 and quality=='precision':
        # Fifteen degrees on >5 complex polygons can lead to millions of
        # geometric operations. Preserve it for smaller jobs; degrade gracefully.
        angles=tuple(range(0,360,45));styles=styles[:3]
    if n>12:styles=styles[:2];angles=angles[:2]
    if n>25:styles=styles[:1]
    # Four corner seeds for small jobs, capped for larger ones.
    anchors={'fast':('BL',), 'balanced':('BL','TR'),
             'thorough':('BL','BR','TL','TR'),
             'precision':('BL','BR','TL','TR')}[quality] if n<=8 else ('BL',)
    if stock_outline is not None and auto:raise ValueError('Remnant requires fixed stock size')

    if not auto:
        W,H=float(stock_width or 0),float(stock_height or 0)
        if W<=0 or H<=0:raise ValueError('Blechabmessungen fehlen.')
        candidates=[]
        for ix,mode in enumerate(styles):
            for anchor in anchors:
                remaining=_sort_items(items,mode,ix+4)
                sheets=[];placements=[]
                for _ in range(n):
                    if not remaining:break
                    current,others=_pack(remaining,W,H,margin,gap,angles,hole_nesting,
                              allow_partial=multi_sheet,fast_mode=fast_mode,
                              stock_outline=stock_outline if len(sheets)==0 else None,
                              preserve_remnants=preserve_remnants,anchor=anchor)
                    if not current:break
                    sid=len(sheets)
                    for row in current:row['sheet_index']=sid
                    placements.extend(current);sheets.append((W,H));remaining=others
                if remaining:continue
                used_area=sum((max(p['shape'].bounds[2] for p in placements if p['sheet_index']==sid)-
                               min(p['shape'].bounds[0] for p in placements if p['sheet_index']==sid))*
                              (max(p['shape'].bounds[3] for p in placements if p['sheet_index']==sid)-
                               min(p['shape'].bounds[1] for p in placements if p['sheet_index']==sid))
                              for sid in range(len(sheets)))
                if preserve_remnants and stock_outline is None:
                    from remnant_optimization import largest_free_rectangle
                    rects=[largest_free_rectangle([p for p in placements if p['sheet_index']==sid],
                           W,H,margin=margin,clearance=gap/2)[1] for sid in range(len(sheets))]
                    approx=(len(sheets),-max(rects,default=0.),-sum(rects),used_area)
                else:
                    approx=(len(sheets),used_area)
                candidates.append((approx,placements,sheets,mode,anchor))
        if not candidates:raise ValueError('Teile passen nicht auf dieses Blech (auch nicht einzeln).')
        candidates.sort(key=lambda row:row[0])
        # Only refine a few strong but diverse candidates, avoiding large-job slowdown.
        short=candidates[:(4 if n<=8 and quality in ('thorough','precision') else 2)]
        scored=[]
        for _,placements,sheets,mode,anchor in short:
            if n<=14 and stock_outline is None:
                revised=[]
                for sid in range(len(sheets)):
                    own=[p for p in placements if p['sheet_index']==sid]
                    revised.extend(refine_sheet(own,W,H,margin,gap,quality,preserve_remnants))
                placements=revised
            # Global quality first prioritizes sheet count. Other criteria are
            # computed on the whole sheet, not only individual greedy insertions.
            sheets_score=[score_layout([p for p in placements if p['sheet_index']==sid],
                          W,H,margin,gap,preserve_remnants) for sid in range(len(sheets))]
            if preserve_remnants and stock_outline is None:
                quality_score=(len(sheets),-max(sc[0] for sc in sheets_score),
                               -sum(sc[0] for sc in sheets_score),
                               -max(sc[1] for sc in sheets_score))
            else:
                quality_score=(len(sheets),-sum(sc[0] for sc in sheets_score))
            scored.append((quality_score,placements,sheets,mode,anchor))
        best=min(scored,key=lambda row:row[0])
        result=_summarize(best[1],best[2],margin,gap,
            f'Multi-start polygon nesting {quality}; {len(angles)} angles; '
            f'{len(styles)} orderings; {len(anchors)} corners; local compaction; '
            f'large offcut: {"on" if preserve_remnants else "off"}; no proven global optimum')
        if stock_outline is not None:
            result['stock_outline']=stock_outline
            stock_area=float(stock_outline.area)+max(0,len(best[2])-1)*W*H
            result['sheet_mm2']=stock_area
            result['waste_mm2']=max(0,stock_area-result['net_mm2'])
            result['utilization']=100*result['net_mm2']/stock_area if stock_area else 0
        result['search_objective']='maximize_reusable_offcut' if preserve_remnants else 'compact_grouping'
        result['evaluated_alternatives']=len(scored)
        from nesting_hybrid import enhance_layout
        return enhance_layout(result,quality,preserve_remnants,auto=False)

    base_area=sum(poly.area for _,poly in items)
    maxsize=max(max(p.bounds[2]-p.bounds[0],p.bounds[3]-p.bounds[1]) for _,p in items)
    minimum=max(maxsize+2*margin,sqrt(base_area)*0.48)
    maximum=max(minimum*2.3,2*margin+sum(max(p.bounds[2]-p.bounds[0],p.bounds[3]-p.bounds[1])+gap for _,p in items))
    import numpy as np
    count={'fast':10,'balanced':18,'thorough':26,'precision':30}[quality]
    if quality=='precision' and n>5:count=min(count,9)
    if n>12:count=min(count,10)
    if n>30:count=min(count,6)
    widths=sorted(set(float(x) for x in np.geomspace(minimum,maximum,num=count))|{minimum})
    early=[]
    for ix,mode in enumerate(styles):
        ordered=_sort_items(items,mode,ix+4)
        for W in widths:
            height=max(maximum,2*margin+sqrt(base_area)*5)
            current,remaining=_pack(ordered,W,height,margin,gap,angles,hole_nesting,
                                    fast_mode=fast_mode,preserve_remnants=False,anchor='BL')
            if remaining or not current:continue
            rows,dimensions=_tight_auto_result(current,margin)
            early.append((dimensions[0]*dimensions[1],rows,dimensions,mode,'BL'))
    if not early:raise ValueError('Kein automatisches Layout gefunden.')
    early.sort(key=lambda row:row[0])
    # Run 4-corner trials on the most promising widths, not every expensive width.
    # This also allows repositioning the entire cluster in another direction.
    if n<=8 and quality!='fast':
        promising_widths=sorted({w for _,_,(w,_),_,_ in early[:3]})
        for ii,W in enumerate(promising_widths):
            for mode in styles[:2]:
                for anchor in anchors[1:]:
                    ordered=_sort_items(items,mode,ii+4)
                    current,remaining=_pack(ordered,W,max(maximum,2*margin+sqrt(base_area)*5),
                          margin,gap,angles,hole_nesting,fast_mode=False,
                          preserve_remnants=False,anchor=anchor)
                    if remaining or not current:continue
                    rows,dim=_tight_auto_result(current,margin)
                    early.append((dim[0]*dim[1],rows,dim,mode,anchor))
    early.sort(key=lambda row:row[0])
    # Refine only a few of the strongest *distinct* starts.
    finalists=[];seen=set()
    for area,rows,(W,H),mode,anchor in early:
        sig=(round(area,1),round(W,1),round(H,1),mode,anchor)
        if sig in seen:continue
        seen.add(sig);finalists.append((area,rows,(W,H),mode,anchor))
        if len(finalists)>=(4 if n<=8 and quality in ('thorough','precision') else (3 if n<=8 else 2)):break
    # Phase 2: revisit promising compact sheets with a bounded set of widths
    # and *real* fixed-canvas objectives. v1.2 always used preserve=False in
    # the constructive stage and the refinement stage of automatic nesting:
    # the checkbox could consequently select only between near-identical
    # already-final layouts. This phase genuinely optimizes both objectives.
    alternatives=[]
    def register(rows, mode, anchor, *, refine=False, canvas=None):
        if not rows:return
        if refine and n<=12:
            bw,bh=canvas if canvas else _tight_auto_result(rows,margin)[1]
            rows=refine_sheet(rows,bw,bh,margin,gap,quality,
                              preserve=preserve_remnants)
        moved,dim=_tight_auto_result(rows,margin)
        area=dim[0]*dim[1]
        # Stock cut: score the actually recoverable blank region, not the
        # huge temporary canvas used during the constructive search.
        sc=score_layout(moved,*dim,margin,gap,preserve_remnants)
        alternatives.append((area,sc,moved,dim,mode,anchor))

    for area,rows,(W,H),mode,anchor in finalists:
        # Always retain the unmodified original. Local improvement is
        # heuristic and can fail to improve the final cropped dimensions.
        register(rows,mode,anchor)
        if n<=12:
            register(rows,mode,anchor,refine=True,canvas=(W,H))

    if n<=8 and quality!='fast' and stock_outline is None:
        seed=min(alternatives,key=lambda v:v[0])
        seed_w,seed_h=seed[3]
        trial_dims=[]
        for a in (0.94,0.985,1.025,1.065):
            trial_dims.append((max(2*margin+1,seed_w*a),seed_h*1.18))
        if quality in ('thorough','precision'):
            trial_dims.extend([(seed_h*a,seed_w*1.16) for a in (0.94,1.025)])
        # Constrained-size re-packing can discover arrangements missed by
        # automatic height=very-large construction. Include cavity-first
        # orders for shape-with-cutout workloads.
        for ti,(bw,bh) in enumerate(trial_dims):
            best_trial=None
            for mode in ('area','container') if hole_nesting else ('area',):
                ordered=_sort_items(items,mode,ti+4)
                for offcut_search in ((False,True) if preserve_remnants else (False,)):
                    trial,left=_pack(ordered,bw,bh,margin,gap,angles,hole_nesting,
                          fast_mode=False,preserve_remnants=offcut_search,anchor='BL')
                    if left or not trial:continue
                    moved,dim=_tight_auto_result(trial,margin)
                    area=dim[0]*dim[1]
                    if best_trial is None or area<best_trial[0]:
                        best_trial=(area,moved,dim,mode,'BL')
                    # Keep a different feasible layout produced by the
                    # offcut-aware objective even if it has slightly more area.
                    if offcut_search:
                        register(trial,mode,'BL-remnant')
            if best_trial is not None:
                _,trial,dim,mode,anchor=best_trial
                register(trial,mode,anchor)

    # Auto-sheet: sheet area is the first-order cost. Compare sizeable offcuts
    # *only among* near-equal material choices (3.5% tolerance). No false claim
    # that a toggle must change every possible input: a Pareto optimum can
    # legitimately be the same for both settings.
    best_area=min(row[0] for row in alternatives)
    if preserve_remnants:
        eligible=[row for row in alternatives if row[0]<=best_area*1.035+1e-6]
        selected=max(eligible,key=lambda row:(row[1],-row[0]))
    else:
        selected=min(alternatives,key=lambda row:(row[0],-row[1][0]))
    area,sc,placements,(W,H),mode,anchor=selected
    layout=_summarize(placements,[(W,H)],margin,gap,
        f'Multi-start polygon nesting {quality}; {len(angles)} angles; '
        f'{len(styles)} orderings; {len(widths)} widths; bounded-width repacking; '
        f'objective-aware compaction; '
        f'large offcut: {"on (3.5% area tolerance)" if preserve_remnants else "off"}; '
        f'no proven global optimum')
    layout['search_objective']='maximize_reusable_offcut' if preserve_remnants else 'minimum_sheet_area'
    layout['evaluated_alternatives']=len(alternatives)
    from nesting_hybrid import enhance_layout
    return enhance_layout(layout,quality,preserve_remnants,auto=True)


def compare_sheet_formats(pieces,qtys=None,margin=5.,gap=3.,options=STANDARD_SHEETS,quality='fast'):
    answers=[]
    for w,h in options:
        try:
            layout=advanced_nest(pieces,qtys,stock_width=w,stock_height=h,margin=margin,gap=gap,
                auto=False,quality=quality,multi_sheet=True)
            answers.append({'Breite_mm':w,'Hoehe_mm':h,'Bleche':layout['sheet_count'],
                            'Material_mm2':round(layout['sheet_mm2']),
                            'Ausnutzung_pct':round(layout['utilization'],2)})
        except ValueError:continue
    return sorted(answers,key=lambda r:(r['Material_mm2'],-r['Ausnutzung_pct']))
