"""Machine-independent audit outputs: no CNC commands generated."""
import csv,io,json,zipfile
from svg_preview import svg_sheet
from dxf_export import export_layout_dxf
from remnants import remnants_json,remnants_dxf
from simulation import simulation_html
from shapely.geometry import mapping


def tables(layout,plan):
    placements=[]
    for item in layout['placements']:
        placements.append({'Teil':item['name'],'Blech':item.get('sheet_index',0)+1,
            'X_mm':round(item['x'],3),'Y_mm':round(item['y'],3),
            'Drehung_grad':item['angle'],'Nettoflaeche_mm2':round(item['shape'].area,3),
            'Kontur_mm':round(item['shape'].length,3),
            'Lochzahl':len(item['shape'].interiors),
            'Verschachtelt_in':item.get('nested_in',''),
            'Tasche_Typ':item.get('nest_type','')})
    paths=[]
    for t in plan['paths']:
        paths.append({'Nr':t['order'],'Blech':t['sheet_index']+1,'Teil':t['part'],
             'Kontur':t['type'] if t['type']=='outer' else f'Loch {t["hole_index"]}',
             'Eintritt_ok':t['entry_ok'],'X_Start_mm':t['cut_start_mm'][0],
             'Y_Start_mm':t['cut_start_mm'][1],
             'X_Anfahrt_mm':t['lead_start_mm'][0],
             'Y_Anfahrt_mm':t['lead_start_mm'][1],
             'Schnittweg_mm':round(t['cut_length_mm'],3),
             'Anfahrt_mm':round(t['lead_length_mm'],3),
             'Eilweg_mm':round(t['rapid_from_previous_mm'],3)})
    return placements,paths


def _csv(rows):
    if not rows:return b''
    f=io.StringIO();w=csv.DictWriter(f,fieldnames=list(rows[0].keys()),delimiter=';')
    w.writeheader();w.writerows(rows)
    return f.getvalue().encode('utf-8-sig')


def build_export(layout,plan,quote,metadata,remnants=None,audit=None):
    positions,routes=tables(layout,plan)
    clean_layout={k:v for k,v in layout.items() if k not in ('placements','stock_outline')}
    if layout.get('stock_outline') is not None:
        clean_layout['stock_outline_geojson']=mapping(layout['stock_outline'])
    clean_plan={k:v for k,v in plan.items() if k!='paths'}
    clean_routes=[{k:v for k,v in path.items()} for path in plan['paths']]
    scrap_quality=[]
    from remnant_portfolio import remnant_metrics
    for row in remnant_metrics(layout,grid=18):
        scrap_quality.append({'sheet_index':row['sheet_index'],
          'largest_rectangle_mm2':round(row['largest_rectangle_mm2'],2),
          'top_three_rectangles_mm2':round(row['combined_rectangles_mm2'],2),
          'largest_connected_free_mm2':round(row['largest_connected_mm2'],2),
          'free_region_count':row['fragment_count'],
          'candidate_rectangles_mm':[[round(v,3) for v in r.bounds] for r in row['rectangles']]})
    summary={'project':metadata,'layout':clean_layout,'offcut_quality':scrap_quality,'path_summary':clean_plan,
             'cost':quote,'positions':positions,'paths':clean_routes,'xy_audit':audit,
             'important':'Only an illustrative estimate / toolpath proposal. NOT G-code or checked CAM.'}
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('Ergebnis_2D.json',json.dumps(summary,ensure_ascii=False,indent=2))
        if remnants is not None:
            z.writestr('Remnant_Inventory.json',remnants_json(remnants,material=metadata.get('material',''),thickness_mm=metadata.get('thickness_mm')))
            z.writestr('Remnant_Outlines.dxf',remnants_dxf(remnants))
        z.writestr('Common_Line_Candidates.json',json.dumps(plan.get('common_lines',[]),ensure_ascii=False,indent=2))
        z.writestr('Optimization_Offcut_Metrics.json',json.dumps(scrap_quality,ensure_ascii=False,indent=2))
        z.writestr('Positionen_2D.csv',_csv(positions))
        z.writestr('Werkzeugwege_2D.csv',_csv(routes))
        z.writestr('ANWEISUNG.txt',
            'PLANUNGSVORSCHLAG – KEINE MASCHINENDATEI.\n'
            'Bitte Maße, Einheiten, Material, Blech, Schnittbreite, Einfahrstrategie,\n'
            'Spannmittel, Restgitter, Mikrostege, Bohrungen, Kollisionsfreiheit,\n'
            'Werkzeug, Z-Zustellung, Technikdaten und Vorschub im CAM prüfen.\n')
        for sheet in layout['sheets']:
            ix=sheet['index']
            z.writestr(f'Nesting_Blech_{ix+1}.svg',svg_sheet(layout,None,ix))
            z.writestr(f'Nesting_Sheet_{ix+1}.dxf',export_layout_dxf(layout,ix))
            z.writestr(f'Wegplanung_Blech_{ix+1}.svg',svg_sheet(layout,plan,ix))
            z.writestr(f'XY_Animation_Sheet_{ix+1}.html',simulation_html(layout,plan,ix,lang='en',
                common_lines=plan.get('common_lines')))
        z.writestr('Nesting_All_Sheets.dxf',export_layout_dxf(layout))
        z.writestr('DXF_README.txt',
            'Editable 2D nesting (millimeters, AutoCAD R2010).\n'
            'Nesting_All_Sheets.dxf: all sheets placed side-by-side, not overlapping.\n'
            'Nesting_Sheet_N.dxf: each sheet in local XY coordinates.\n'
            'Layers: SHEET_BOUNDARY, PART_Snnn_nnnn (part contours + holes),\n'
            'PART_LABELS, SHEET_LABELS.\n'
            'CUT_* entities are CLOSED polylines representing PART OUTLINES, NOT machine paths.\n'
            'Remove/freeze labels and frame for CAM. Curves approximated by line segments\n'
            'during DXF import. Check dimensions, kerf, gap, clamping and toolpaths.\n')
    return memory.getvalue()
