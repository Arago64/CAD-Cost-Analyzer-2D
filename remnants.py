"""Usable geometric remnants (unclamped advisory, no stock management database)."""
from __future__ import annotations
import json,io
from shapely.geometry import box,Polygon,MultiPolygon,shape,mapping
from shapely.ops import unary_union
import ezdxf


def find_remnants(layout, *, kerf_mm=0.3, min_area_mm2=1000., edge_margin_mm=0.):
    """Uncut stock regions after allowing half-kerf around EACH part material.

    Parts in holes leave hole scrap as separate islands (may be reusable), but
    no claim is made that an island can be mechanically recovered intact.
    """
    results=[]
    for sh in layout['sheets']:
        sid=sh['index'];outline=layout.get('stock_outline') if sid==0 else None
        boundary=outline if outline is not None else box(0,0,sh['width_mm'],sh['height_mm'])
        stock=boundary.buffer(-edge_margin_mm) if edge_margin_mm>0 else boundary
        occupied=[p['shape'].buffer(max(0,kerf_mm/2),join_style=2) for p in layout['placements'] if p.get('sheet_index',0)==sid]
        diff=stock.difference(unary_union(occupied)) if occupied else stock
        polys=([diff] if isinstance(diff,Polygon) else list(diff.geoms) if hasattr(diff,'geoms') else [])
        for part in polys:
            if not isinstance(part,Polygon) or part.area<min_area_mm2:continue
            results.append({'sheet_index':sid,'area_mm2':round(part.area,2),
                            'bbox_mm':[round(v,3) for v in part.bounds], 'polygon':part})
    return sorted(results,key=lambda row:-row['area_mm2'])


def remnants_json(remnants, *, material='', thickness_mm=None):
    return json.dumps({'format':'CAD2D_REMNANTS_1','units':'mm','material':material,
                       'thickness_mm':thickness_mm,
                       'remnants':[{'sheet_index':r['sheet_index'],'area_mm2':r['area_mm2'],
                                    'geometry':mapping(r['polygon'])} for r in remnants]},
                       ensure_ascii=False,indent=2).encode('utf-8')


def import_remnant(data, index=0):
    obj=json.loads(data)
    if obj.get('format')!='CAD2D_REMNANTS_1' or obj.get('units')!='mm':
        raise ValueError('Unknown remnant inventory / units')
    r=obj['remnants'][index]
    poly=shape(r['geometry'])
    if not isinstance(poly,Polygon) or not poly.is_valid or poly.area<=0:
        raise ValueError('Invalid remnant polygon')
    # Normalize to (0,0), preserving holes and actual boundary.
    from shapely import affinity
    poly=affinity.translate(poly,xoff=-poly.bounds[0],yoff=-poly.bounds[1])
    return poly,obj


def remnants_dxf(remnants):
    doc=ezdxf.new('R2010');doc.header['$INSUNITS']=4;msp=doc.modelspace()
    for i,r in enumerate(remnants):
        lyr=f'REMNANT_{i+1:03d}';doc.layers.new(lyr)
        geom=r['polygon']
        for ring in [geom.exterior]+list(geom.interiors):
            msp.add_lwpolyline(list(ring.coords)[:-1],close=True,dxfattribs={'layer':lyr})
    s=io.StringIO();doc.write(s);return s.getvalue().encode()
