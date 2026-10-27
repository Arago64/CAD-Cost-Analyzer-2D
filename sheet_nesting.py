"""2D contours -> individual sheet parts -> collision-tested heuristic nesting & cost.

Units millimetres. Estimates NOT CAM toolpaths, exact quoting, or optimal nesting.
Overlays, open paths, duplicate lines are reported instead of silently billed.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from collections import defaultdict
from itertools import product
from math import ceil,hypot,pi,sqrt,isfinite
import numpy as np
from shapely.geometry import Point,LineString,Polygon,MultiPolygon,GeometryCollection
from shapely.ops import polygonize,unary_union
from shapely import affinity
from ezdxf import path as ezpath
from dxf_reader import open_dxf
from cad_geometry import AUTOCAD_UNIT_CODES,UNITS_TO_MM

@dataclass
class CutPiece:
    name:str
    polygon:Polygon
    layer:str
    holes:int=0
    qty:int=1
    @property
    def area_mm2(self):return float(self.polygon.area)
    @property
    def cut_mm(self):return float(self.polygon.length)

@dataclass
class SheetParse:
    filename:str
    pieces:list[CutPiece]=field(default_factory=list)
    issues:list[str]=field(default_factory=list)
    unit:str='mm'
    ignored:int=0
    open_count:int=0
    total_contours:int=0


def _safe_polygon(points):
    if len(points)<3:return None
    p=Polygon(points)
    if not p.is_valid or p.area<1e-5:return None
    return p


def _entity_polygon(entity):
    kind=entity.dxftype()
    if kind=='CIRCLE':
        c=entity.dxf.center; radius=abs(float(entity.dxf.radius))
        if radius<1e-4:return None
        return Point(c.x,c.y).buffer(radius,quad_segs=96)
    if kind in ('LWPOLYLINE','POLYLINE','ELLIPSE','SPLINE'):
        is_closed=bool(getattr(entity,'is_closed',False)) if kind in ('LWPOLYLINE','POLYLINE') else bool(getattr(entity,'closed',False))
        if not is_closed:return None
        try:
            coords=[(float(pt.x),float(pt.y)) for pt in ezpath.make_path(entity).flattening(distance=.02,segments=16)]
            return _safe_polygon(coords)
        except Exception:return None
    return None


def _exploded(entity,depth=0):
    if entity.dxftype()=='INSERT' and depth<6:
        try:
            for child in entity.virtual_entities():
                yield from _exploded(child,depth+1)
        except Exception: pass
    else:
        yield entity


def _combined_contours(raw_polygons,issues,layer):
    # Convert closed contours into materials with alternating nesting depth;
    # a smaller loop fully inside a bigger loop becomes a hole.
    raw_polygons=sorted(raw_polygons,key=lambda p:p.area,reverse=True)
    unique=[]
    for p in raw_polygons:
        if any(p.equals(q) or p.symmetric_difference(q).area<1e-6 for q in unique):
            issues.append(f'Layer {layer}: doppelte geschlossene Kontur ignoriert.')
        else:unique.append(p)
    parents=[]; depths=[]
    for i,p in enumerate(unique):
        container=next((j for j in range(i-1,-1,-1) if unique[j].covers(p)),None)
        parents.append(container)
        depths.append(0 if container is None else depths[container]+1)
        if container is None:
            for q in unique[:i]:
                if p.intersects(q) and not q.covers(p):
                    issues.append(f'Layer {layer}: überlappende Konturen ohne Einbettung; bitte CAD bereinigen.')
                    break
    results=[]
    for i,p in enumerate(unique):
        if depths[i]%2!=0:continue
        holes=[unique[j].exterior.coords[:] for j in range(i+1,len(unique)) if parents[j]==i and depths[j]%2==1]
        part=Polygon(p.exterior.coords,holes=holes)
        if not part.is_valid:
            issues.append(f'Layer {layer}: ungültige Geometrie mit Aussparung, übersprungen.')
            continue
        results.append((part,len(holes)))
    return results


def parse_sheet_dxf(content:bytes,filename='drawing.dxf',units='auto',include_layers=None,join_tolerance_mm=0.):
    doc=open_dxf(content)
    code=doc.header.get('$INSUNITS',0)
    u=AUTOCAD_UNIT_CODES.get(code,'mm') if units=='auto' else units
    if u not in UNITS_TO_MM:raise ValueError('Unbekannte Maßeinheit')
    k=UNITS_TO_MM[u]
    if not 0 <= join_tolerance_mm <= 2:raise ValueError('Endpunkt-Toleranz 0–2 mm erforderlich')
    result=SheetParse(filename=filename,unit=u)
    if units=='auto' and code not in AUTOCAD_UNIT_CODES:
        result.issues.append('Zeichnungseinheiten nicht eindeutig; mm angenommen und manuell prüfen.')
    per_layer=defaultdict(list)
    open_lines=defaultdict(list)
    for ent in doc.modelspace():
        # Ignore nonmanufacturing annotation/stock-frame layers exported by
        # this analyzer. Otherwise an exported sheet border is misread as a
        # giant part when the DXF is imported back into the application.
        if ent.dxf.layer in ('SHEET_BOUNDARY', 'SHEET_LABELS', 'PART_LABELS'):
            continue
        if include_layers is not None and ent.dxf.layer not in include_layers:continue
        for el in _exploded(ent):
            typ=el.dxftype();layer=ent.dxf.layer
            if typ in ('CIRCLE','LWPOLYLINE','POLYLINE','ELLIPSE','SPLINE'):
                p=_entity_polygon(el)
                if p is not None:
                    per_layer[layer].append(p)
                else:
                    try:
                        xy=[(float(pt.x),float(pt.y)) for pt in
                            ezpath.make_path(el).flattening(distance=.02/k,segments=16)]
                        if len(xy)>=2:
                            open_lines[layer].append(LineString(xy))
                        else:result.open_count+=1
                    except Exception:result.open_count+=1
            elif typ=='LINE':
                s=el.dxf.start;e=el.dxf.end
                if abs(s.z-e.z)>1e-5:
                    result.issues.append(f'Layer {layer}: nicht-planare Linie ignoriert.')
                    result.ignored+=1
                elif hypot(s.x-e.x,s.y-e.y)>1e-6:
                    open_lines[layer].append(LineString([(s.x,s.y),(e.x,e.y)]))
            elif typ=='ARC':
                try:
                    xy=[(float(pt.x),float(pt.y)) for pt in
                        ezpath.make_path(el).flattening(distance=.02/k,segments=16)]
                    if len(xy)>=2:open_lines[layer].append(LineString(xy))
                    else:result.open_count+=1
                except Exception:result.open_count+=1
            else:
                result.ignored+=1
    for layer,lines in open_lines.items():
        if lines:
            if len(lines)<=4000:
                from shapely.geometry import MultiPoint
                # Conservative endpoint clustering: correct only near-coincident endpoints.
                tolerance=max(join_tolerance_mm/k,1e-8)
                ends=[]; mappings={}
                for line in lines:
                    for pt in (line.coords[0],line.coords[-1]):
                        nearest=next((q for q in ends if hypot(pt[0]-q[0],pt[1]-q[1])<=tolerance),None)
                        if nearest is None:
                            nearest=pt;ends.append(nearest)
                        mappings[tuple(pt)]=nearest
                lines=[LineString([mappings.get(tuple(line.coords[0]),line.coords[0])]+
                    list(line.coords)[1:-1]+[mappings.get(tuple(line.coords[-1]),line.coords[-1])]) for line in lines]
                if join_tolerance_mm>0:
                    result.issues.append(f'Layer {layer}: Endpunkte innerhalb {join_tolerance_mm:g} mm zusammengeführt; Geometrie prüfen.')
            merged=unary_union(lines)
            contours=list(polygonize(merged))
            per_layer[layer].extend(contours)
            used_length=sum(float(p.length) for p in contours)
            original=sum(float(line.length) for line in lines)
            if original>used_length+1e-4:
                result.open_count+=1
                result.issues.append(f'Layer {layer}: Linien bilden teilweise offene/überlagerte Konturen; bitte prüfen.')
    for layer,loops in per_layer.items():
        result.total_contours+=len(loops)
        for ix,(poly,holes) in enumerate(_combined_contours(loops,result.issues,layer),1):
            scaled=affinity.scale(poly,xfact=k,yfact=k,origin=(0,0))
            result.pieces.append(CutPiece(f'{layer}_{ix}',scaled,layer,holes))
    if result.open_count:
        result.issues.append(f'{result.open_count} offene/unvollständige Elemente: nicht als geschlossene Teile berechnet.')
    if result.ignored:
        result.issues.append(f'{result.ignored} Hilfs-/3D-Objekte ignoriert. Layer prüfen.')
    if not result.pieces:result.issues.append('Keine geschlossenen 2D-Teile gefunden; DXF Modelspace prüfen.')
    return result


def _orient(poly,angle):
    p=affinity.rotate(poly,angle,origin=(0,0))
    x,y,_,_=p.bounds
    return affinity.translate(p,xoff=-x,yoff=-y)


def _pack_fixed(parts,width,height,margin,gap,angles=(0,90)):
    """Greedy true-polygon collision checking, not just rectangle bin pack."""
    placed=[]
    candidates={(round(margin,4),round(margin,4))}
    expanded=[]
    for label,original in parts:
        best=None
        for a in angles:
            p0=_orient(original,a)
            w=p0.bounds[2];h=p0.bounds[3]
            if w>width-2*margin+1e-7 or h>height-2*margin+1e-7:continue
            for x,y in sorted(candidates,key=lambda q:(q[1],q[0])):
                if x+w>width-margin+1e-7 or y+h>height-margin+1e-7:continue
                shape=affinity.translate(p0,xoff=x,yoff=y)
                # check actual shape spacing; 'gap' is required geometry-to-geometry space
                if any(shape.intersection(prev).area>1e-7 or (gap>0 and shape.distance(prev)<gap-1e-6) for prev in expanded):continue
                max_x=max([shape.bounds[2]]+[q['shape'].bounds[2] for q in placed])
                max_y=max([shape.bounds[3]]+[q['shape'].bounds[3] for q in placed])
                score=(max_x*max_y,max_y,max_x,y,x)
                if best is None or score<best[0]:best=(score,shape,x,y,a)
        if best is None:return None
        _,shape,x,y,a=best
        row={'name':label,'shape':shape,'x':x,'y':y,'angle':a}
        placed.append(row)
        expanded.append(shape)
        # include flush-bottom/flush-side contact with bounding boxes at both edges
        for item in placed:
            b=item['shape'].bounds
            for nx,ny in [(b[2]+gap,margin),(margin,b[3]+gap),(b[2]+gap,b[1]),
                          (b[0],b[3]+gap),(b[2]+gap,b[3]+gap)]:
                if nx<width-margin and ny<height-margin:
                    candidates.add((round(nx,4),round(ny,4)))
    return placed


def _input_items(pieces,qtys=None):
    items=[]
    qtys=qtys or {}
    for i,p in enumerate(pieces):
        quantity=int(qtys.get(p.name,p.qty))
        if quantity<0 or quantity>100:raise ValueError('Menge je Kontur muss 0–100 sein')
        items += [(f'{p.name} #{j}',p.polygon) for j in range(1,quantity+1)]
    if not items:raise ValueError('Keine Teile ausgewählt')
    if len(items)>50:raise ValueError('Maximum: 50 individual parts per nesting run')
    return sorted(items,key=lambda t:-t[1].area)


def nest_parts(pieces,qtys=None,stock_width=None,stock_height=None,margin=5,gap=3,auto=True):
    items=_input_items(pieces,qtys)
    if min(margin,gap)<0:raise ValueError('Randabstand/Fugenabstand negativ')
    if not auto:
        if not stock_width or not stock_height:raise ValueError('Blechgröße fehlt')
        result=_pack_fixed(items,float(stock_width),float(stock_height),margin,gap)
        if result is None:raise ValueError('Nicht alle Teile passen auf das gewählte Blech. Abmessung erhöhen oder aufteilen.')
        W=float(stock_width);H=float(stock_height)
    else:
        # Bounded search over candidate widths, selecting smallest used bounding sheet.
        area=sum(float(p.area) for _,p in items)
        maxdim=max(max(poly.bounds[2]-poly.bounds[0],poly.bounds[3]-poly.bounds[1]) for _,poly in items)
        lower=max(sqrt(area)*.65,maxdim+2*margin)
        upper=max(lower*2.5, sum(max(poly.bounds[2]-poly.bounds[0],poly.bounds[3]-poly.bounds[1])+gap for _,poly in items)+2*margin)
        best=None
        for trial in np.geomspace(lower,upper,num=24):
            # Temporary tall sheet; optimize measured tight bbox afterwards.
            W=float(trial);H=max(upper,2*margin+sqrt(area)*7)
            placed=_pack_fixed(items,W,H,margin,gap)
            if placed is None:continue
            used_w=max(q['shape'].bounds[2] for q in placed)+margin
            used_h=max(q['shape'].bounds[3] for q in placed)+margin
            # Bounded rectangle; round UP to full mm (manufacturing stock format).
            w=ceil(used_w);h=ceil(used_h)
            candidate=(w*h,w,h,placed)
            if best is None or candidate[:3]<best[:3]:best=candidate
        if best is None:raise ValueError('Kein automatisches Layout gefunden; größere Platte/andere Orientierung probieren.')
        _,W,H,result=best
    net=sum(float(p.area) for _,p in items)
    sheet=W*H
    return {'placements':result,'sheet_width_mm':W,'sheet_height_mm':H,
            'net_mm2':net,'sheet_mm2':sheet,'waste_mm2':max(sheet-net,0),
            'utilization':100*net/sheet,'cut_length_mm':sum(float(p.length) for _,p in items),
            'pierces':sum(1+len(p.interiors) for _,p in items),'items':len(items),
            'algorithm':'Greedy polygon placement; 0/90° + best of 24 candidate widths, NOT global optimum.'}

STANDARD_SHEETS=((1000,2000),(1250,2500),(1500,3000),(2000,4000),(1000,1000),(500,1000),(500,500))

def compare_standard_sheets(pieces,qtys=None,margin=5,gap=3,options=STANDARD_SHEETS):
    out=[]
    for w,h in options:
        try:
            r=nest_parts(pieces,qtys,w,h,margin,gap,auto=False)
            out.append({'Breite_mm':w,'Hoehe_mm':h,'Ausnutzung_pct':round(r['utilization'],2),'Fläche_mm2':r['sheet_mm2']})
        except ValueError:pass
    return sorted(out,key=lambda x:x['Fläche_mm2'])

DENSITIES={'S235JR':7850.,'S355':7850.,'Edelstahl':7950.,'Aluminium':2700.,'Kunststoff':1200.,'Sonstiges':7850.}

def cost_sheet(layout,*,thickness_mm=3,material='S235JR',eur_kg=1.9,
               operation='Laser',hourly_eur=95,feed_mm_min=2000,
               mrr_mm3_min=2000,cutter_mm=3,stepdown_mm=2,pierce_seconds=2,
               setup_min=10,overhead_pct=15):
    if thickness_mm<=0 or eur_kg<0 or hourly_eur<0 or setup_min<0 or overhead_pct<0:
        raise ValueError('Ungültige Dicke / Kostensätze')
    rho=DENSITIES.get(material,7850.)
    sheet_mass=layout['sheet_mm2']*thickness_mm*rho/1e9
    part_mass=layout['net_mm2']*thickness_mm*rho/1e9
    if feed_mm_min<=0 or mrr_mm3_min<=0 or cutter_mm<=0 or stepdown_mm<=0:
        raise ValueError('Vorschub, Zeitspanvolumen und Werkzeugdaten müssen > 0 sein')
    path=layout['cut_length_mm']
    if operation=='CNC Konturfräsen':
        passes=ceil(thickness_mm/stepdown_mm)
        cut_volume=path*cutter_mm*thickness_mm
        proc_min=max(path/feed_mm_min*passes,cut_volume/mrr_mm3_min)
        estimate_basis='Konturfräsen: max(Vorschub×Zustellungen, Fräsvolumen / Zeitspanvolumen)'
    elif operation=='CNC gesamte Restfläche abtragen':
        passes=ceil(thickness_mm/stepdown_mm)
        cut_volume=layout['waste_mm2']*thickness_mm
        proc_min=max(cut_volume/mrr_mm3_min,0)
        estimate_basis='Theoretische Vollabtragung der Restfläche; NICHT üblich für Blechzuschnitt.'
    else:
        passes=1
        cut_volume=path*cutter_mm*thickness_mm
        proc_min=path/feed_mm_min + layout['pierces']*pierce_seconds/60
        estimate_basis='Vereinfachter Konturschnittweg / Schnittgeschwindigkeit + Einstiche'
    total_min=setup_min+proc_min
    material_eur=sheet_mass*eur_kg
    machining_eur=total_min/60*hourly_eur
    direct=material_eur+machining_eur
    return {'material':material,'material_kg':sheet_mass,'useful_kg':part_mass,
            'scrap_kg':sheet_mass-part_mass,'material_eur':material_eur,
            'cut_mm':path,'passes':passes,'removed_mm3':cut_volume,
            'machining_min':proc_min,'setup_min':setup_min,'total_min':total_min,
            'machine_eur':machining_eur,'overhead_eur':direct*overhead_pct/100,
            'total_eur':direct*(1+overhead_pct/100),
            'unit_eur':direct*(1+overhead_pct/100)/layout['items'],
            'basis':estimate_basis}


def nesting_svg(layout):
    """Visual inspection only, not a CAM cutting file (no kerf/lead-ins/order)."""
    from html import escape
    w=layout['sheet_width_mm'];h=layout['sheet_height_mm']
    colors=('#3F88C5','#59A14F','#D38A37','#9D72B0','#E56B6F','#328B89','#BF6B9A')
    result=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {h:.2f}" width="980" height="640">',
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="white" stroke="black" stroke-width="0.7"/>']
    for i,item in enumerate(layout['placements']):
        polygon=item['shape']
        polygons=[polygon.exterior]+list(polygon.interiors)
        paths=[]
        for ring in polygons:
            xy=[(x,h-y) for x,y in ring.coords]
            paths.append('M '+' L '.join(f'{x:.3f},{y:.3f}' for x,y in xy)+' Z')
        result.append(f'<path d="{escape(" ".join(paths))}" fill="{colors[i%len(colors)]}" fill-rule="evenodd" fill-opacity="0.65" stroke="black" stroke-width="0.3" />')
    result.append('</svg>')
    return '\n'.join(result)
