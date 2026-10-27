"""Portable set of user-selectable layouts for the *same* source DXF job.

Only placement transforms are stored. Restore reconstructs from original source
polygons and runs the existing exact update_layout validation; arbitrary saved
shapes are never trusted as geometry input.
"""
from __future__ import annotations
import io,zipfile,json,csv
from nesting_advanced import _summarize
from layout_editor import update_layout,layout_fingerprint
from optimizer_v15 import _summary as metrics_summary
from dxf_export import export_layout_dxf
from svg_preview import svg_sheet


def _rows(layout):
    return [{'index':i,'x':p['shape'].bounds[0],'y':p['shape'].bounds[1],
             'angle':p['angle'],'sheet_index':p.get('sheet_index',0),
             'part_name':p['name'],'part_area_mm2':round(p['shape'].area,6)}
            for i,p in enumerate(layout['placements'])]


def archive_variants(variants,source_key):
    doc={'format':'CAD2D-v1.5-variants','source_key':source_key,'variants':[]}
    blob=io.BytesIO()
    with zipfile.ZipFile(blob,'w',zipfile.ZIP_DEFLATED) as z:
        rows=[]
        for k,v in enumerate(variants,1):
            name=f'Variant_{k:02}'
            l=v['layout']
            doc['variants'].append({'id':name,'name':v['name'],
                'dimensions':[(s['width_mm'],s['height_mm']) for s in l['sheets']],
                'positions':_rows(l)})
            z.writestr(name+'.dxf',export_layout_dxf(l))
            for s in l['sheets']:
                z.writestr(name+f'_Sheet_{s["index"]+1}.svg',svg_sheet(l,None,s['index']))
            rows.append({'variant':name,'name':v['name'],**v['metrics']})
        f=io.StringIO();writer=csv.DictWriter(f,rows[0].keys() if rows else ['variant']);writer.writeheader();writer.writerows(rows)
        z.writestr('Comparison.csv',f.getvalue().encode('utf-8-sig'))
        z.writestr('Variants.json',json.dumps(doc,ensure_ascii=False,indent=2))
        z.writestr('README.txt','Open Variants.json through CAD Cost Analyzer v1.5 with the SAME source DXF, quantities and stock configuration.\n'
                    'DXF are editable outlines, not certified CNC toolpaths.\n')
    return blob.getvalue()


def load_variants(data,base_layout,source_key):
    if isinstance(data,bytes):
        if data.startswith(b'PK'):
            with zipfile.ZipFile(io.BytesIO(data),'r') as z:
                if 'Variants.json' not in z.namelist():raise ValueError('No Variants.json in ZIP')
                data=z.read('Variants.json')
        obj=json.loads(data)
    else:obj=json.loads(data) if isinstance(data,str) else data
    if obj.get('format')!='CAD2D-v1.5-variants':raise ValueError('Incorrect variant project format')
    allowed=set(source_key) if isinstance(source_key,(list,tuple,set)) else {source_key}
    if obj.get('source_key') not in allowed:raise ValueError('Source DXF, quantities or nesting setup does not match this project')
    out=[]
    for v in obj.get('variants',[])[:20]:
        dims=[(float(w),float(h)) for w,h in v['dimensions']]
        # Uses the original part shapes and their orientation from the unchanged
        # current source job. Only user-supplied coordinates/angles are applied.
        # Match stored positions against ORIGINAL identities, not item index:
        # different nesting objectives may change the order of parts.
        used=set();source=[]
        for j,row in enumerate(v['positions']):
            part_name=row.get('part_name')
            part_area=row.get('part_area_mm2')
            if part_name is None or part_area is None:
                raise ValueError('Missing part identity in saved project; cannot safely restore')
            found=next((i for i,p in enumerate(base_layout['placements'])
                    if i not in used and p['name']==part_name
                    and abs(p['shape'].area-float(part_area))<=0.005),None)
            if found is None:raise ValueError(f'Missing or altered part: {part_name}')
            used.add(found);source.append(dict(base_layout['placements'][found]))
        if len(source)!=len(base_layout['placements']):
            raise ValueError('Saved alternative has an incomplete part set')
        temp=_summarize(source,dims,
                base_layout['margin_mm'],base_layout['gap_mm'],'v1.5 stored alternative')
        if base_layout.get('stock_outline') is not None:temp['stock_outline']=base_layout['stock_outline']
        rows={'fingerprint':layout_fingerprint(temp),'placements':v['positions']}
        lay=update_layout(temp,rows,stock_outline=base_layout.get('stock_outline'))
        out.append({'id':'restored_'+str(len(out)+1),'name':str(v['name'])[:120],
            'layout':lay,'metrics':metrics_summary(lay,grid=18),'origin':'restored'})
    return out
