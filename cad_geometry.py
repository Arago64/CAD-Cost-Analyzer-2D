"""Simple 2D DXF profile analysis (contour lengths and nested area)."""
from __future__ import annotations
from dataclasses import dataclass,asdict
import io, math
import ezdxf
from dxf_reader import open_dxf
from ezdxf import path as ezpath

UNITS_TO_MM={'mm':1.,'cm':10.,'m':1000.,'in':25.4}
AUTOCAD_UNIT_CODES={1:'in',4:'mm',5:'cm',6:'m'}
SUPPORTED={'LINE','LWPOLYLINE','POLYLINE','CIRCLE','ARC','ELLIPSE','SPLINE'}

@dataclass
class Geometry:
    filename: str
    units: str
    cut_length_mm: float
    net_area_mm2: float
    closed_contours: int
    open_entities: int
    ignored_entities: int
    notes: str


def polygon_area(points):
    return abs(sum(x1*y2-x2*y1 for (x1,y1),(x2,y2) in zip(points,points[1:]+points[:1])))/2.


def in_polygon(point, ring):
    x,y=point; inside=False
    for (x1,y1),(x2,y2) in zip(ring,ring[1:]+ring[:1]):
        if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:
            inside=not inside
    return inside


def flatten_path(ent):
    try:
        pts=list(ezpath.make_path(ent).flattening(distance=0.05,segments=16))
        if len(pts)>20000:
            pts=pts[::math.ceil(len(pts)/20000)]
        return [(p.x,p.y) for p in pts]
    except Exception:
        return []


def entity_length(ent):
    typ=ent.dxftype()
    if typ=='LINE':
        return (ent.dxf.end-ent.dxf.start).magnitude
    if typ=='CIRCLE':
        return 2*math.pi*abs(ent.dxf.radius)
    if typ=='ARC':
        ang=(ent.dxf.end_angle-ent.dxf.start_angle)%360
        return ent.dxf.radius*math.radians(ang)
    if typ in ('LWPOLYLINE','POLYLINE'):
        try:
            return sum(entity_length(sub) for sub in ent.virtual_entities())
        except Exception:
            pass
    pts=flatten_path(ent)
    return sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))


def is_closed(ent):
    t=ent.dxftype()
    if t=='CIRCLE': return True
    if t in ('LWPOLYLINE','POLYLINE'):
        return bool(ent.is_closed)
    if t in ('ELLIPSE','SPLINE'):
        try: return bool(ent.closed)
        except Exception: return False
    return False


def flattened_entities(ent,depth=0):
    if ent.dxftype()=='INSERT':
        if depth>=5:
            return
        try:
            for nested in ent.virtual_entities():
                yield from flattened_entities(nested,depth+1)
        except Exception:
            return
    else:
        yield ent


def analyze_dxf(content: bytes,filename: str,units='auto',include_layers=None) -> Geometry:
    doc=open_dxf(content)
    if units=='auto':
        units=AUTOCAD_UNIT_CODES.get(doc.header.get('$INSUNITS',0),'mm')
        no_units=(doc.header.get('$INSUNITS',0) not in AUTOCAD_UNIT_CODES)
    else:
        no_units=False
    factor=UNITS_TO_MM[units]
    length=0.; closed=[]; n_open=ignored=0
    for top in doc.modelspace():
        if include_layers is not None and top.dxf.layer not in include_layers:
            continue
        sublist=list(flattened_entities(top))
        if not sublist:
            ignored+=1
        for ent in sublist:
            typ=ent.dxftype()
            if typ not in SUPPORTED:
                ignored+=1
                continue
            dist=entity_length(ent)
            if math.isfinite(dist):
                length+=dist
            if is_closed(ent):
                pts=flatten_path(ent)
                if len(pts)>=3:
                    # remove duplicated ending point
                    if math.dist(pts[0],pts[-1])<1e-8: pts=pts[:-1]
                    if len(pts)>=3 and polygon_area(pts)>1e-9:
                        closed.append(pts)
                else: n_open+=1
            else:
                n_open+=1
    areas=[polygon_area(p) for p in closed]
    # Nested closed loops contribute with alternating signs: outer contour (+), hole (-), island (+).
    # Not a general boolean union and unsuitable for overlapping/unclean technical drawings.
    net=0.
    for i,poly in enumerate(closed):
        test=poly[0]
        depth=sum(1 for j,other in enumerate(closed) if j!=i and areas[j]>areas[i] and in_polygon(test,other))
        net+=(-1 if depth%2 else 1)*areas[i]
    notes=[]
    if no_units: notes.append('Keine eindeutige DXF-Einheit; mm angenommen.')
    if n_open: notes.append(f'{n_open} offene Geometrieelemente; Nettofläche nur aus geschlossenen Konturen.')
    if ignored: notes.append(f'{ignored} nicht unterstützte/ignorierte Elemente.')
    if closed: notes.append('Fläche ist eine Näherung (verschachtelte Konturen), keine CAD-Massenanalyse.')
    else: notes.append('Keine geschlossenen Konturen – Fläche/Masse nicht ermittelbar.')
    return Geometry(filename,units,round(length*factor,3),round(max(0.,net)*factor**2,3),
                    len(closed),n_open,ignored,' '.join(notes))


def dxf_layers(content: bytes):
    doc=open_dxf(content)
    return sorted({e.dxf.layer for e in doc.modelspace()})
