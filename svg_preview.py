"""Sheet and toolpath previews. SVG drawings are illustrations, not CAM output."""
from html import escape

COLORS=['#3F88C5','#37A07A','#D9943E','#9B7CCC','#E17A7E','#36A1B2','#BA6C9C']


def _poly_d(poly,height):
    rings=[poly.exterior]+list(poly.interiors)
    return ' '.join('M '+' L '.join(f'{x:.3f},{height-y:.3f}' for x,y in r.coords)+' Z' for r in rings)


def svg_sheet(layout,toolplan=None,sheet_index=0,show_paths=True,show_numbers=True,highlight_offcut=False,irregular_candidates=None):
    sheet=layout.get('sheets',[{'index':0,'width_mm':layout['sheet_width_mm'],
                                  'height_mm':layout['sheet_height_mm']}])[sheet_index]
    W=sheet['width_mm'];H=sheet['height_mm']
    border=min(W,H)*.002
    result=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="750" viewBox="{-border:.2f} {-border:.2f} {W+2*border:.2f} {H+2*border:.2f}">',
            f'<rect width="{W}" height="{H}" fill="#fff" stroke="#333" stroke-width="{max(W,H)/800:.4f}"/>']
    if sheet_index==0 and layout.get('stock_outline') is not None:
        result.append(f'<path d="{_poly_d(layout["stock_outline"],H)}" fill="#f5f5f5" fill-rule="evenodd" stroke="#303030" stroke-width="{max(W,H)/300:.3f}"/>')
    pieces=[p for p in layout['placements'] if p.get('sheet_index',0)==sheet_index]
    if highlight_offcut == 'irregular':
        if irregular_candidates is None:
            from irregular_remnants import irregular_remnants
            irregular_candidates=irregular_remnants(layout)
        for k,entry in enumerate(r for r in irregular_candidates if r['sheet_index']==sheet_index):
            polygon=entry['polygon']
            result.append(f'<path d="{_poly_d(polygon,H)}" fill="#158f68" fill-opacity=".20" '
                          f'stroke="#086446" stroke-width="{max(W,H)/250:.3f}" '
                          f'stroke-dasharray="{max(W,H)/80:.3f},{max(W,H)/110:.3f}" '
                          'fill-rule="evenodd" />')
            # Decomposition overlay shows how one irregular reusable outline
            # consists of several connected primitive/curved polygonal pieces.
            for segment in entry.get('compound_components',[]):
                if len(entry.get('compound_components',[]))<=1:break
                result.append(f'<path d="{_poly_d(segment,H)}" fill="none" '
                    f'stroke="#387b68" stroke-opacity=".45" stroke-width="{max(W,H)/950:.3f}" '
                    'stroke-dasharray="2,2" />')
            location=polygon.representative_point()
            result.append(f'<text x="{location.x:.3f}" y="{H-location.y:.3f}" '
                          f'font-family="Arial" font-size="{max(W,H)/70:.3f}" '
                          f'fill="#064f3b" text-anchor="middle">F{k+1}: {entry["area_mm2"]/1e6:.4f} m²</text>')
    elif highlight_offcut == 'mixed':
        from remnant_shapes import freeform_metrics
        parts=freeform_metrics(layout,grid=11)['mixed'][sheet_index]
        for idx,rem in enumerate(parts):
            geometry=rem['geometry']
            # We draw inscribed material candidates with their true geometry.
            result.append(f'<path d="{_poly_d(geometry,H)}" fill="#2ba66c" fill-opacity=".17" stroke="#12834e" stroke-width="{max(W,H)/270:.3f}" stroke-dasharray="{max(W,H)/70:.3f},{max(W,H)/110:.3f}" />')
            center=geometry.representative_point()
            label='C' if rem['kind']=='circle' else 'R'
            result.append(f'<text x="{center.x:.3f}" y="{H-center.y:.3f}" font-family="Arial" font-weight="bold" font-size="{max(W,H)/65:.3f}" text-anchor="middle" fill="#096138">{label}{idx+1}: {rem["area_mm2"]/1e6:.4f} m²</text>')
    elif highlight_offcut:
        from remnant_portfolio import remnant_metrics
        rows=remnant_metrics(layout,grid=18,max_rectangles=3)
        for idx,rect in enumerate(rows[sheet_index]['rectangles']):
            a,b,c,d=rect.bounds
            result.append(f'<rect x="{a:.3f}" y="{H-d:.3f}" width="{c-a:.3f}" height="{d-b:.3f}" fill="#3ebf79" fill-opacity=".13" stroke="#148045" stroke-width="{max(W,H)/270:.3f}" stroke-dasharray="{max(W,H)/70:.3f},{max(W,H)/110:.3f}"/>')
            result.append(f'<text x="{(a+c)/2:.3f}" y="{H-(b+d)/2:.3f}" font-family="Arial" font-weight="bold" font-size="{max(W,H)/55:.3f}" text-anchor="middle" fill="#136d3c">R{idx+1}: {rect.area/1e6:.4f} m²</text>')
    for n,item in enumerate(pieces):
        poly=item['shape']
        result.append(f'<path d="{_poly_d(poly,H)}" fill="{COLORS[n%len(COLORS)]}" fill-opacity="{0.96 if highlight_offcut == 'irregular' else 0.72}" fill-rule="evenodd" stroke="#283744" stroke-width="{max(W,H)/1500:.3f}"><title>{escape(item["name"])}</title></path>')
        loc=poly.representative_point()
        size=max(W,H)/80
        if show_numbers:
            result.append(f'<text x="{loc.x:.3f}" y="{H-loc.y:.3f}" font-family="Arial" font-size="{size:.3f}" text-anchor="middle" fill="#16212b">{escape(item["name"][:24])}</text>')
    if show_paths and toolplan:
        for path in toolplan['paths']:
            if path['sheet_index']!=sheet_index:continue
            points=path['xy']
            if points:
                path_d='M '+' L '.join(f'{x:.3f},{H-y:.3f}' for x,y in points)
                result.append(f'<path d="{path_d}" fill="none" stroke="#103B87" stroke-width="{max(W,H)/600:.3f}" stroke-opacity=".85"/>')
            x,y=path['cut_start_mm'];sx,sy=path['lead_start_mm']
            result.append(f'<path d="M {sx:.3f},{H-sy:.3f} L {x:.3f},{H-y:.3f}" stroke="#bd4b17" stroke-width="{max(W,H)/450:.3f}" fill="none" stroke-dasharray="{max(W,H)/100:.2f},{max(W,H)/150:.2f}"/>')
            result.append(f'<circle cx="{sx:.3f}" cy="{H-sy:.3f}" r="{max(W,H)/230:.3f}" fill="{"#167d4b" if path["entry_ok"] else "#db242b"}" />')
            if show_numbers:
                result.append(f'<text x="{x:.3f}" y="{H-y:.3f}" font-family="Arial" font-size="{max(W,H)/95:.3f}" fill="#0b2867">{path["order"]}</text>')
    result.append('</svg>')
    return '\n'.join(result)
