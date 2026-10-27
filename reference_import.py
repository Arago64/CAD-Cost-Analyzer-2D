"""Import a previously exported full nesting result and optional editor edits.

For trusted geometry we always rebuild shapes from the CURRENT source job,
using the old coordinates/angles only after contour consistency checks. The
legacy result ZIP is *not* treated as an unconstrained new CAD source.
"""
from __future__ import annotations
import io,json,zipfile,math
import ezdxf
from shapely import affinity
from shapely.geometry import Polygon
from nesting_advanced import _summarize,_tight_auto_result
from optimizer_v15 import _validate


def _polygon_groups(dxf_bytes):
    try:
        d=ezdxf.read(io.StringIO(dxf_bytes.decode('utf-8-sig')))
    except UnicodeDecodeError:
        d=ezdxf.read(io.BytesIO(dxf_bytes))
    parts={}
    for e in d.modelspace().query('LWPOLYLINE'):
        name=e.dxf.layer
        if not name.startswith('PART_S'):continue
        points=[(float(p[0]),float(p[1])) for p in e.get_points('xy')]
        if len(points)>=3:parts.setdefault(name,[]).append(Polygon(points))
    polygons=[]
    for name,rings in sorted(parts.items()):
        outer=max(rings,key=lambda p:p.area)
        holes=[list(p.exterior.coords) for p in rings if p is not outer and outer.covers(p)]
        polygons.append(Polygon(outer.exterior.coords,holes=holes))
    return polygons


def load_legacy_reference(zip_data,base_layout,edits_data=None):
    """Restore an old manual seed with source-part equality safeguards.

    This helper currently supports single-sheet source-independent exported
    2D results with Ergebnis_2D.json and Nesting_Sheet_1.dxf. It reports any
    error instead of silently matching unrelated parts by label alone.
    """
    if not _validate(base_layout):raise ValueError('Current reference layout invalid')
    if base_layout['sheet_count']!=1 or base_layout.get('stock_outline') is not None:
        raise ValueError('Historical import only for one auto-sized rectangular sheet')
    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
        if 'Ergebnis_2D.json' not in z.namelist() or 'Nesting_Sheet_1.dxf' not in z.namelist():
            raise ValueError('Previous result ZIP must include Ergebnis_2D.json and Nesting_Sheet_1.dxf')
        meta=json.loads(z.read('Ergebnis_2D.json'))
        original_polys=_polygon_groups(z.read('Nesting_Sheet_1.dxf'))
    original_rows=meta.get('positions')
    if len(original_polys)!=len(base_layout['placements']) or len(original_rows)!=len(original_polys):
        raise ValueError('Saved result does not contain the same number of pieces')
    historical_margin=float(meta['layout']['margin_mm'])
    historical_gap=float(meta['layout']['gap_mm'])
    margin=float(base_layout['margin_mm']);gap=float(base_layout['gap_mm'])
    if abs(historical_margin-margin)>1e-6 or abs(historical_gap-gap)>1e-6:
        raise ValueError('Gap and edge margin differ from previous result')
    edits=None
    if edits_data is not None:
        obj=json.loads(edits_data) if isinstance(edits_data,(bytes,str)) else edits_data
        edits=obj.get('placements',obj) if isinstance(obj,dict) else obj
        if not isinstance(edits,list) or sorted(int(p['index']) for p in edits)!=list(range(len(original_rows))):
            raise ValueError('Edited positions missing/duplicated; cannot import')
        edits=sorted(edits,key=lambda x:int(x['index']))
    used=set();placements=[]
    for i,(poly,old) in enumerate(zip(original_polys,original_rows)):
        name=old['Teil'];old_angle=float(old['Drehung_grad'])
        left=poly.bounds[0];bottom=poly.bounds[1]
        if abs(left-float(old['X_mm']))>0.2 or abs(bottom-float(old['Y_mm']))>0.2:
            raise ValueError('Positions do not match saved DXF layer order')
        if edits is None:x,y,angle=left,bottom,old_angle
        else:x,y,angle=float(edits[i]['x']),float(edits[i]['y']),float(edits[i]['angle'])
        matching=[]
        for j,p in enumerate(base_layout['placements']):
            if j in used or p['name']!=name:continue
            ref=p['shape']
            if abs(ref.area-poly.area)>max(.15,ref.area*.000015):continue
            if abs(ref.length-poly.length)>max(.15,ref.length*.000015):continue
            # First align the CURRENT part at the historical absolute angle.
            candidate=affinity.rotate(ref,old_angle-float(p['angle']),origin='center')
            bx,by,_,_=candidate.bounds
            candidate=affinity.translate(candidate,xoff=left-bx,yoff=bottom-by)
            geom_delta=candidate.hausdorff_distance(poly)
            if geom_delta<=max(.15,math.sqrt(ref.area)*.0005):
                matching.append((geom_delta,j))
        if not matching:raise ValueError(f'Historical geometry differs from current source: {name}')
        j=min(matching)[1];used.add(j)
        base=base_layout['placements'][j]
        geom=affinity.rotate(base['shape'],angle-float(base['angle']),origin='center')
        bx,by,_,_=geom.bounds
        geom=affinity.translate(geom,xoff=x-bx,yoff=y-by)
        placements.append(dict(base,shape=geom,x=float(geom.bounds[0]),y=float(geom.bounds[1]),
                               angle=angle%360,sheet_index=0))
    moved,dims=_tight_auto_result(placements,margin)
    layout=_summarize(moved,[dims],margin,gap,'v3.3 restored previously validated layout')
    if not _validate(layout):
        raise ValueError('Restored layout violates exact gap or stock bounds; check previous edits')
    return layout
