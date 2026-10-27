"""Advisory 2D cut planning: kerf offset, holes-first, lead-ins, part order.

NOT G-code / tool-ready CAM. Does not model clamping, tabs, Z, accelerations,
thermal distortion, machine-specific compensation, quality or fixturing.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil, hypot, pi
from shapely.geometry import Polygon, Point, LineString
from shapely.ops import unary_union


@dataclass
class CuttingParameters:
    method: str = 'Laser'
    kerf_mm: float = 0.30  # For milling set equal to tool diameter
    lead_mm: float = 2.0
    stepdown_mm: float = 2.0
    feed_mm_min: float = 2000.0
    rapid_mm_min: float = 10000.0
    pierce_s: float = 2.0
    pass_setup_s: float = 0.5


def _point_tuple(point):
    return (round(float(point.x),4),round(float(point.y),4))


def _centerline(original,ring_type,index,radius):
    if ring_type=='outer':
        region=Polygon(original.exterior)
        offset=region.buffer(radius,join_style=2)
        return offset.exterior if offset.geom_type=='Polygon' and not offset.is_empty else None
    hole_region=Polygon(original.interiors[index])
    offset=hole_region.buffer(-radius,join_style=2)
    return offset.exterior if offset.geom_type=='Polygon' and not offset.is_empty else None


def _sample_entry(ring,original,other_metal,sheet,params,previous):
    """Select candidate start with scrap-side straight lead-in and short travel.

    Lead-in must stay inside the sheet and outside the original metal at the
    approximate tool clearance. No machine collision/fixturing guarantee.
    """
    radius=params.kerf_mm/2
    L=params.lead_mm
    width,height=sheet
    n=max(16,min(72,ceil(ring.length/15)))
    step=ring.length/n
    best=None;fallback=None
    def inside(pt):
        return radius-1e-7<=pt.x<=width-radius+1e-7 and radius-1e-7<=pt.y<=height-radius+1e-7
    for i in range(n):
        d=(i+.5)*step
        p=ring.interpolate(d)
        before=ring.interpolate((d-max(step*.12,0.001))%ring.length)
        after=ring.interpolate((d+max(step*.12,0.001))%ring.length)
        tx=after.x-before.x;ty=after.y-before.y
        length=hypot(tx,ty)
        if length<1e-8:continue
        for direction in (-1,1):
            nx=direction*-ty/length;ny=direction*tx/length
            entry=Point(p.x+nx*L,p.y+ny*L)
            if not inside(entry):continue
            check=[Point(p.x+nx*L*t,p.y+ny*L*t) for t in (.25,.5,.75,1.)]
            clearance=max(radius*.98,1e-5)
            # End of lead-in touches the nominal kerf offset from the material.
            admissible=all(other_metal.distance(q)>=clearance-1e-5 for q in check)
            # Favor high clearance and shorter air moves. Straight-in rather than
            # tangential entry; ramps/arc entry must be programmed in real CAM.
            excess=min(other_metal.distance(entry),max(L+radius,1.0))
            travel=hypot(entry.x-previous[0],entry.y-previous[1])
            score=(travel-max(0.,excess-radius)*.35,travel)
            if fallback is None or score<fallback[0]:fallback=(score,p,entry)
            if admissible and (best is None or score<best[0]):
                best=(score,p,entry)
    selected=best or fallback
    if selected is None:
        p=ring.interpolate(.25*ring.length)
        return _point_tuple(p),_point_tuple(p),False
    _,p,entry=selected
    return _point_tuple(p),_point_tuple(entry),best is not None


def plan_toolpaths(layout, params:CuttingParameters, route_optimization=True):
    """Plan each contour with holes/nested-release precedence and nearest travel.

    For route_optimization=False, use deterministic part-wise hole-first order.
    Every cut remains independent: common-line mode is reported separately.
    """
    if params.kerf_mm<=0 or params.feed_mm_min<=0 or params.rapid_mm_min<=0:
        raise ValueError('Schnittbreite, Schnittvorschub und Eilgang müssen > 0 sein.')
    if params.lead_mm<0 or params.pierce_s<0 or params.pass_setup_s<0 or params.stepdown_mm<=0:
        raise ValueError('Ungültige Eintritts-/Zeitparameter.')
    sheets=layout.get('sheets') or [{'index':0,'width_mm':layout['sheet_width_mm'],
                                     'height_mm':layout['sheet_height_mm']}]
    all_paths=[];warnings=[];order=0;rapid=0.;cut=0.;lead=0.;pierces=0;skipped=0
    radius=params.kerf_mm/2
    for sheet in sheets:
        sid=sheet['index']; W=sheet['width_mm'];H=sheet['height_mm']
        placements=[p for p in layout['placements'] if p.get('sheet_index',0)==sid]
        metal=unary_union([p['shape'] for p in placements])
        pending=[];current=(0.,0.)
        global_idx_by_identity={id(p):j for j,p in enumerate(layout['placements'])}
        # Unambiguous nesting dependency graph. Every descendant is cut
        # before ANY contour of its surrounding host, including the host hole
        # boundary that might release the inner part from scrap.
        ancestors={}
        for item in placements:
            own_idx=global_idx_by_identity[id(item)]
            parents=[];seen={own_idx}
            parent=item.get('nested_in_index')
            if parent is None and item.get('nested_in'):
                matches=[j for j,x in enumerate(layout['placements']) if
                    x.get('sheet_index',0)==sid and x['name']==item['nested_in'] and j!=own_idx]
                if len(matches)==1:parent=matches[0]
            while parent is not None and parent not in seen:
                seen.add(parent);parents.append(parent)
                node=layout['placements'][parent]
                parent=node.get('nested_in_index')
            ancestors[own_idx]=set(parents)
        for item in placements:
            geom=item['shape']
            for typ,idx in [('hole',i) for i in range(len(geom.interiors))]+[('outer',None)]:
                ring=_centerline(geom,typ,idx,radius)
                if ring is None or ring.is_empty or ring.length<1e-5:
                    skipped+=1
                    warnings.append(f"{item['name']}: {typ} {'' if idx is None else idx+1} kleiner als Schnittbreite; Kontur nicht bearbeitbar.")
                else:
                    pending.append({'part':item,'part_index':global_idx_by_identity[id(item)],'type':typ,'idx':idx,'ring':ring})
        while pending:
            def eligible(t):
                my_id=t['part_index']
                if t['type']=='outer' and any(u['part_index']==my_id and u['type']=='hole' for u in pending):
                    return False
                # Any unfinished descendant must be completed before even
                # the host's inner hole perimeter is severed.
                if any(my_id in ancestors.get(u['part_index'],set()) for u in pending):return False
                return True
            options=[t for t in pending if eligible(t)]
            if not options:
                raise ValueError('Nested toolpath precedence cycle; cannot safely schedule cutting')
            def distance(t):
                ring=LineString(t['ring'].coords)
                nearest=ring.interpolate(ring.project(Point(current)))
                return hypot(nearest.x-current[0],nearest.y-current[1])
            task=min(options,key=distance) if route_optimization else options[0]
            pending.remove(task)
            part=task['part'];typ=task['type'];idx=task['idx'];ring=task['ring']
            st,approach,ok=_sample_entry(ring,part['shape'],metal,(W,H),params,current)
            rapid+=hypot(approach[0]-current[0],approach[1]-current[1])
            lead+=hypot(st[0]-approach[0],st[1]-approach[1])
            order+=1;pierces+=1;cut+=ring.length
            rows=list(ring.coords)
            from shapely.ops import substring
            line=LineString(rows);d=line.project(Point(st))
            trail=substring(line,d,line.length);head=substring(line,0,d)
            coords=list(trail.coords)+list(head.coords)[1:]
            if len(coords)<2:coords=rows
            all_paths.append({'order':order,'sheet_index':sid,'part':part['name'],'part_index':task['part_index'],
                'type':typ,'hole_index':idx+1 if idx is not None else None,
                'cut_start_mm':st,'lead_start_mm':approach,'entry_ok':bool(ok),
                'cut_length_mm':float(ring.length),'lead_length_mm':hypot(st[0]-approach[0],st[1]-approach[1]),
                'rapid_from_previous_mm':hypot(approach[0]-current[0],approach[1]-current[1]),
                'xy':[(round(x,4),round(y,4)) for x,y in coords]})
            if not ok:
                warnings.append(f"{part['name']} / {typ}: sicherer geradliniger Eintritt im freien Material nicht bestätigt; CAM-Planung nötig.")
            current=st
        if params.method=='CNC Konturfräsen':
            warnings.append('CNC: Zustellung, Werkzeugbelastung, Haltestege, Aufspannung, Einfahrstrategie und Z-Freigänge müssen in CAM geprüft werden.')
    if any(p.get('nested_in') for p in layout['placements']):
        warnings.append('Verschachtelte Teile: vollständige Innenkonturen vor ALLEN Konturen des umgebenden Teils geplant. '
                        'Haltestege, Ausschussabstützung, Spannlage, thermische Effekte und Durchführbarkeit in CAM prüfen.')
    for i,p in enumerate(layout['placements']):
        for q in layout['placements'][i+1:]:
            if p.get('sheet_index',0)!=q.get('sheet_index',0):continue
            if p['shape'].distance(q['shape'])<params.kerf_mm-1e-6:
                warnings.append('Teileabstand kleiner als Schnittbreite / Werkzeugdurchmesser: Konturwege können sich überschneiden.')
                break
    if skipped:
        warnings.append(f'{skipped} Konturen geometrisch nicht fräs-/schneidbar, Kosten und Bearbeitungszeit unvollständig.')
    return {'paths':all_paths,'warnings':list(dict.fromkeys(warnings)),
            'complete':skipped==0,'skipped_contours':skipped,
            'contours':len(all_paths),'pierces':pierces,
            'cut_length_mm':cut,'lead_length_mm':lead,'rapid_length_mm':rapid,
            'effective_cut_mm':cut+lead,
            'route_optimization':bool(route_optimization),
            'method':params.method,'kerf_mm':params.kerf_mm}


def time_estimate(plan,params:CuttingParameters,thickness_mm):
    if thickness_mm<=0:raise ValueError('Dicke muss > 0 sein.')
    passes=ceil(thickness_mm/params.stepdown_mm) if params.method=='CNC Konturfräsen' else 1
    # Repeated depth passes are an engineering estimate; no tool acceleration model.
    cutting_min=plan['effective_cut_mm']*passes/params.feed_mm_min
    rapid_min=plan['rapid_length_mm']/params.rapid_mm_min
    pierce_min=plan['pierces']*params.pierce_s/60
    pass_min=plan['contours']*passes*params.pass_setup_s/60
    return {'passes':passes,'cut_min':cutting_min,'rapid_min':rapid_min,
            'pierce_min':pierce_min,'pass_setup_min':pass_min,
            'process_min':cutting_min+rapid_min+pierce_min+pass_min}
