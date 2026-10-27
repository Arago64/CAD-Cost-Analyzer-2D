"""2D geometric plan audit and offline animated visualization.

No Z-axis, fixturing, process thermal model, collision-free CNC claim, or G-code.
"""
from __future__ import annotations
import json
from html import escape
from math import hypot
from shapely.geometry import LineString,box
from svg_preview import _poly_d,COLORS


def audit_plan(layout,plan,kerf_mm):
    problems=[];checks=0
    for sid,sh in enumerate(layout['sheets']):
        parts=[p for p in layout['placements'] if p.get('sheet_index',0)==sid]
        for i,p in enumerate(parts):
            stock=layout.get('stock_outline') if sid==0 else None
            border=stock if stock is not None else box(0,0,sh['width_mm'],sh['height_mm'])
            if not border.covers(p['shape']):problems.append({'kind':'OUT_OF_STOCK','sheet':sid,'part':p['name']})
            for q in parts[i+1:]:
                checks+=1
                if p['shape'].intersection(q['shape']).area>1e-7:
                    problems.append({'kind':'PART_OVERLAP','sheet':sid,'part':p['name'],'other':q['name']})
                elif p['shape'].distance(q['shape'])<kerf_mm-1e-5:
                    problems.append({'kind':'KERF_CLEARANCE','sheet':sid,'part':p['name'],'other':q['name']})
    paths=plan['paths']
    order_by_part={}
    for route in paths:
        order_by_part.setdefault((route['sheet_index'],route.get('part_index',route['part'])),[]).append(route)
        if not route['entry_ok']:
            problems.append({'kind':'LEAD_NOT_CONFIRMED','sheet':route['sheet_index'],'part':route['part'],'order':route['order']})
        line=LineString(route['xy'])
        lead=LineString([route['lead_start_mm'],route['cut_start_mm']])
        sheet=layout['sheets'][route['sheet_index']]
        border=layout.get('stock_outline') if route['sheet_index']==0 else None
        border=border if border is not None else box(0,0,sheet['width_mm'],sheet['height_mm'])
        if not border.covers(line):
            problems.append({'kind':'CUT_OUTSIDE_STOCK','sheet':route['sheet_index'],'part':route['part'],'order':route['order']})
        if not border.covers(lead):
            problems.append({'kind':'LEAD_OUTSIDE_STOCK','sheet':route['sheet_index'],'part':route['part'],'order':route['order']})
        # Tool center should remain at least the cutter radius from all OTHER material.
        for part in layout['placements']:
            if part.get('sheet_index',0)!=route['sheet_index'] or part['name']==route['part']:continue
            if min(line.distance(part['shape']),lead.distance(part['shape']))<kerf_mm/2-1e-4:
                problems.append({'kind':'CUTTER_TOUCHES_OTHER_PART','sheet':route['sheet_index'],
                                 'part':route['part'],'other':part['name'],'order':route['order']})
                break
    for sid,p in enumerate(layout['placements']):
        myroutes=order_by_part.get((p.get('sheet_index',0),sid),
                 order_by_part.get((p.get('sheet_index',0),p['name']),[]))
        outers=[r['order'] for r in myroutes if r['type']=='outer']
        holes=[r['order'] for r in myroutes if r['type']=='hole']
        if holes and outers and min(outers)<max(holes):
            problems.append({'kind':'HOLE_ORDER','sheet':p.get('sheet_index',0),'part':p['name']})
        if p.get('nested_in'):
            host=p.get('nested_in_index',p['nested_in'])
            host_paths=order_by_part.get((p.get('sheet_index',0),host),[])
            # Holes too: cutting a surrounding inner hole before its child
            # could release the child as unsupported scrap.
            own=[r['order'] for r in myroutes]
            if host_paths and own and min(r['order'] for r in host_paths)<max(own):
                problems.append({'kind':'NESTED_RELEASE_ORDER','sheet':p.get('sheet_index',0),'part':p['name']})
    return {'checks':checks+len(paths),'problems':problems,'warnings':len(problems),
            'notice':'XY geometric audit only; no clamps, Z, height, heat, tabs, stability, machine dynamics or NC output.'}


def simulation_html(layout,plan,sheet_index=0,lang='ru',common_lines=None):
    sh=layout['sheets'][sheet_index]; W,H=sh['width_mm'],sh['height_mm']
    parts=[p for p in layout['placements'] if p.get('sheet_index',0)==sheet_index]
    paths=[p for p in plan['paths'] if p['sheet_index']==sheet_index]
    defs=[]
    for i,p in enumerate(parts):
        defs.append(f'<path d="{_poly_d(p["shape"],H)}" fill="{COLORS[i%len(COLORS)]}" '
                    'fill-opacity=".5" fill-rule="evenodd" stroke="#364b57" stroke-width="0.6" />')
    lines=[]
    for j,p in enumerate(paths):
        path='M '+' L '.join(f'{x:.4f},{H-y:.4f}' for x,y in p['xy'])
        lines.append(f'<path class="cut" data-step="{j}" d="{path}" fill="none" stroke="#0963bd" '
                     'stroke-width="1.4" stroke-linecap="round"/>')
    if common_lines:
        for candidate in common_lines:
            if candidate['sheet_index']!=sheet_index:continue
            (x1,y1),(x2,y2)=candidate['xy']
            lines.append(f'<line x1="{x1:.3f}" y1="{H-y1:.3f}" x2="{x2:.3f}" y2="{H-y2:.3f}" '
                         'stroke="orange" stroke-width="1.3" stroke-dasharray="4,3" />')
    lex={'ru':['▶ Запуск','⏸ Пауза','Шаг','Демонстрация в плоскости XY, не симулятор станка.'],
         'de':['▶ Start','⏸ Pause','Schritt','Nur XY-Vorschau, keine Maschinensimulation.'],
         'en':['▶ Play','⏸ Pause','Step','XY visualization only; not a machine simulator.']}[lang]
    textsteps=json.dumps([{'part':p['part'],'type':p['type'],'order':p['order']} for p in paths],ensure_ascii=False)
    return f'''<!doctype html><meta charset="utf-8"><style>
      body{{font-family:system-ui;background:white;color:#29384c;margin:0}}nav{{padding:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
      button{{background:#2767a5;border:0;color:white;border-radius:6px;padding:7px 15px;cursor:pointer}}
      input{{flex:1}} svg{{width:100%;height:590px;background:#f9fafb;border:1px solid #b3c2d0}}
      small{{padding:7px;display:block;color:#666}}</style>
      <nav><button id="play">{escape(lex[0])}</button><button id="pause">{escape(lex[1])}</button>
      <label>{escape(lex[2])}: <b id="counter">0/{len(paths)}</b></label>
      <input id="progress" type="range" min="0" max="{max(1,len(paths)*100)}" step="1" value="0"><span id="detail"></span></nav>
      <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="0" width="{W}" height="{H}" fill="white" stroke="#344"/>
      {''.join(defs)}{''.join(lines)}</svg>
      <small>{escape(lex[3])}</small>
      <script>
      const meta={textsteps};const paths=Array.from(document.querySelectorAll('path.cut'));
      const progress=document.getElementById('progress');let run=null;
      function draw(){{const v=+progress.value;paths.forEach((p,i)=>{{let n=p.getTotalLength();p.style.strokeDasharray=n+' '+n;
       p.style.strokeDashoffset=n*(1-Math.max(0,Math.min(1,v/100-i)));}});
       const idx=Math.min(meta.length,Math.floor(v/100));
       document.getElementById('counter').textContent=idx+'/'+meta.length;
       document.getElementById('detail').textContent=meta.length?meta[Math.min(idx,meta.length-1)].part:'';}}
      progress.addEventListener('input',draw);document.getElementById('pause').onclick=()=>{{if(run)clearInterval(run);run=null}};
      document.getElementById('play').onclick=()=>{{if(run)clearInterval(run);run=setInterval(()=>{{
        progress.value=Number(progress.value)+2;draw();if(+progress.value>=+progress.max){{clearInterval(run);run=null;}}
       }},55)}};draw();</script>'''
