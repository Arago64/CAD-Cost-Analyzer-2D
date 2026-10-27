"""Validated manual placement edits plus an optional drag/rotate HTML editor.

The browser exports a JSON edit recipe; ONLY this Python validator can apply edits.
"""
from __future__ import annotations
import copy, json,hashlib
from html import escape
from shapely import affinity
from shapely.geometry import box, Polygon
from shapely.ops import unary_union
from nesting_advanced import _summarize


def layout_fingerprint(layout):
    """Reject edits from unrelated DXF, quantities or stock shape."""
    obj={'parts':[(p['name'],p['shape'].wkb_hex,p.get('sheet_index',0)) for p in layout['placements']],
         'sheets':[(p['width_mm'],p['height_mm']) for p in layout['sheets']],
         'stock':layout['stock_outline'].wkb_hex if layout.get('stock_outline') is not None else None}
    return hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest()


def update_layout(layout, rows, *, stock_outline=None):
    """Apply absolute bottom-left coordinates, sheet and absolute angle; reject every invalid edit.

    Edits must preserve the set of parts (including duplicates). A string UID is
    created from array index rather than ambiguous part labels.
    """
    if isinstance(rows,(bytes,str)):
        rows=json.loads(rows)
    if isinstance(rows,dict):
        if rows.get('fingerprint') != layout_fingerprint(layout):
            raise ValueError('Editor: layout has changed; generate a new edits JSON')
        rows=rows.get('placements',[])
    if not isinstance(rows,list) or len(rows)!=len(layout['placements']):
        raise ValueError('Editor: incorrect number of placement records')
    records={}
    for row in rows:
        idx=int(row['index'])
        if idx in records or idx<0 or idx>=len(layout['placements']):
            raise ValueError('Editor: duplicate or invalid part index')
        records[idx]=row
    if len(records)!=len(rows):raise ValueError('Editor: incomplete part set')
    placement=[]
    for idx,old in enumerate(layout['placements']):
        row=records[idx]
        sheet=int(row.get('sheet_index',old.get('sheet_index',0)))
        if sheet<0 or sheet>=len(layout['sheets']):raise ValueError('Editor: invalid sheet')
        x,y,angle=float(row['x']),float(row['y']),float(row['angle'])
        if not all(abs(v)<1e9 for v in (x,y,angle)):
            raise ValueError('Editor: coordinates too large')
        rotated=affinity.rotate(old['shape'],angle-float(old['angle']),origin='center',use_radians=False)
        bounds=rotated.bounds
        rotated=affinity.translate(rotated,xoff=x-bounds[0],yoff=y-bounds[1])
        changed=dict(old,shape=rotated,x=rotated.bounds[0],y=rotated.bounds[1],angle=angle%360,sheet_index=sheet)
        placement.append(changed)
    margin,gap=float(layout['margin_mm']),float(layout['gap_mm'])
    for i,p in enumerate(placement):
        sh=layout['sheets'][p['sheet_index']]
        stock=(stock_outline if stock_outline is not None and p['sheet_index']==0 else
               box(0,0,sh['width_mm'],sh['height_mm']))
        # A round-buffer margin approximates real stock-edge tool clearance.
        if not stock.buffer(-margin).covers(p['shape']):
            raise ValueError(f"{p['name']}: outside usable sheet / remnant")
        for q in placement[:i]:
            if p['sheet_index']!=q['sheet_index']:continue
            if p['shape'].intersection(q['shape']).area>1e-6 or p['shape'].distance(q['shape'])<gap-1e-6:
                raise ValueError(f"{p['name']} overlaps or violates gap with {q['name']}")
    sheets=[(sh['width_mm'],sh['height_mm']) for sh in layout['sheets']]
    rebuilt=_summarize(placement,sheets,margin,gap,'Manual validated edits; '+layout.get('algorithm',''))
    if 'stock_outline' in layout:
        rebuilt['stock_outline']=layout['stock_outline']
        W=float(layout['sheets'][0]['width_mm']);H=float(layout['sheets'][0]['height_mm'])
        area=layout['stock_outline'].area+max(0,len(layout['sheets'])-1)*W*H
        rebuilt['sheet_mm2']=area
        rebuilt['waste_mm2']=max(0,area-rebuilt['net_mm2'])
        rebuilt['utilization']=100*rebuilt['net_mm2']/area if area else 0
    return rebuilt


def editor_html(layout, sheet_index=0, lang='ru', reference_fingerprint=None):
    """Drag parts with pointer, rotate selected part R, export validated JSON for reimport.

    Dynamic SVG is an offline standalone editor; Streamlit does not implicitly
    trust browser coordinates. The downloaded edits are verified by update_layout.
    """
    sheet=layout['sheets'][sheet_index];w=float(sheet['width_mm']);h=float(sheet['height_mm'])
    data=[]
    for ix,p in enumerate(layout['placements']):
        if p.get('sheet_index',0)!=sheet_index:continue
        a,b,_,_=p['shape'].bounds
        data.append({'index':ix,'name':p['name'],'x':round(a,5),'y':round(b,5),'angle':p['angle'],
                     'sheet_index':sheet_index,'rings':[[[round(x-a,5),round(y-b,5)] for x,y in ring.coords]
                                        for ring in [p['shape'].exterior]+list(p['shape'].interiors)]})
    all_rows=[{'index':ix,'x':p['x'],'y':p['y'],'angle':p['angle'],'sheet_index':p.get('sheet_index',0)}
              for ix,p in enumerate(layout['placements'])]
    labels={'ru':['Перетаскивай детали мышью · R: повернуть +15° · стрелки: сдвиг 1 мм','Скачать правки JSON','Изменения нужно загрузить в приложение; там выполняется точная проверка пересечений.'],
            'de':['Teile ziehen · R: +15° drehen · Pfeile: 1 mm verschieben','Änderungen JSON speichern','JSON im Hauptfenster hochladen; die Geometrie wird dort exakt geprüft.'],
            'en':['Drag parts · R: rotate +15° · Arrows: move 1 mm','Download edits JSON','Upload the JSON in the main app for exact geometric validation.']}[lang]
    cfg=json.dumps(data,ensure_ascii=False);original=json.dumps(all_rows,ensure_ascii=False);fingerprint=reference_fingerprint or layout_fingerprint(layout)
    # HTML is isolated in Streamlit iframe; drawing is local; no execution of downloaded edits.
    template='''<!doctype html><meta charset="utf-8"><style>
      body{font:14px system-ui,sans-serif;margin:0;color:#263238;background:white}
      .bar{padding:8px;background:#edf2f7;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
      svg{width:100%;height:620px;border:1px solid #abb6c2;background:#fafafa;touch-action:none}
      path{cursor:grab;stroke:#34495e;stroke-width:0.9;fill-opacity:.65;fill-rule:evenodd}
      path.selected{stroke:#db3e21;stroke-width:2.5;cursor:grabbing}
      button{padding:8px 14px;background:#276db0;border:0;color:white;border-radius:6px;cursor:pointer}
      small{padding:6px;display:block;color:#5c6572}</style>
      <div class="bar"><span>HELP_TEXT</span>
      <button type="button" id="rotate-left" title="-15°">↶ −15°</button>
      <button type="button" id="rotate-right" title="+15°">↷ +15°</button>
      <button type="button" id="rotate-90" title="+90°">↷ +90°</button>
      <label>° <input id="angle-input" type="number" min="0" max="359" step="0.1" style="width:65px" disabled></label>
      <label>XY step <select id="position-step"><option value="1">1 mm</option><option value="0.1" selected>0.1 mm</option><option value="0.01">0.01 mm</option></select></label>
      <button id="save">SAVE_TEXT</button><b id="state"></b></div>
      <svg id="board" tabindex="0" viewBox="0 0 WIDTH HEIGHT" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="0" width="WIDTH" height="HEIGHT" fill="white" stroke="black" stroke-width="2"/></svg>
      <small>INFO_TEXT</small>
      <script>
      const pieces=DATA, rows=ROWS, H=HEIGHT;
      const svg=document.getElementById('board');
      const angleInput=document.getElementById('angle-input');
      const stepInput=document.getElementById('position-step');
      const snap=v=>Number((Math.round(v/Number(stepInput.value))*Number(stepInput.value)).toFixed(4));
      let selected=null,drag=null;
      function rotate(deg){
        if(selected===null)return;
        const r=getRow(selected);
        r.angle=((Number(r.angle)+Number(deg))%360+360)%360;
        render();svg.focus();
      }
      document.getElementById('rotate-left').onclick=()=>rotate(-15);
      document.getElementById('rotate-right').onclick=()=>rotate(15);
      document.getElementById('rotate-90').onclick=()=>rotate(90);
      angleInput.onchange=()=>{
        if(selected===null || !Number.isFinite(Number(angleInput.value)))return;
        getRow(selected).angle=((Number(angleInput.value)%360)+360)%360;
        render();
      };
      function getRow(index){return rows.find(r=>r.index===index)}
      function transformed(p){
        const row=getRow(p.index), baseAngle=Number(p.angle), da=(Number(row.angle)-baseAngle)*Math.PI/180;
        const all=p.rings.flat(), xs=all.map(a=>a[0]), ys=all.map(a=>a[1]);
        const cx=(Math.min(...xs)+Math.max(...xs))/2,cy=(Math.min(...ys)+Math.max(...ys))/2;
        const rings=p.rings.map(r=>r.map(([x,y])=>[
          cx+(x-cx)*Math.cos(da)-(y-cy)*Math.sin(da),
          cy+(x-cx)*Math.sin(da)+(y-cy)*Math.cos(da)]));
        const pts=rings.flat(), xmin=Math.min(...pts.map(q=>q[0])),ymin=Math.min(...pts.map(q=>q[1]));
        return rings.map(r=>r.map(([x,y])=>[x-xmin+Number(row.x),y-ymin+Number(row.y)]));
      }
      function render(){svg.querySelectorAll('g.part').forEach(e=>e.remove());
        for(const [i,p] of pieces.entries()){
          const rings=transformed(p);
          const d=rings.map(r=>r.map(([x,y],j)=>(j?'L':'M')+x.toFixed(3)+' '+(H-y).toFixed(3)).join(' ')+' Z').join(' ');
          const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.classList.add('part');
          const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',d);
          path.setAttribute('fill',['#66b3d3','#f3bc73','#95c6aa','#a9a2d5','#edaaa8'][i%5]);
          if(selected===p.index)path.classList.add('selected');
          const label=document.createElementNS('http://www.w3.org/2000/svg','title');label.textContent=p.name;path.append(label);
          group.append(path);svg.append(group);
          path.addEventListener('pointerdown',e=>{e.preventDefault();selected=p.index;svg.focus();const pt=pos(e);const row=getRow(selected);
            drag={pointer:e.pointerId,start:pt,x:Number(row.x),y:Number(row.y)};svg.setPointerCapture(e.pointerId);render();});
        }
        angleInput.disabled=selected===null;
        angleInput.value=selected===null?'':String(Number(getRow(selected).angle).toFixed(1));
        document.getElementById('state').textContent=selected===null?'':pieces.find(p=>p.index===selected)?.name+' · x='+Number(getRow(selected).x).toFixed(1)+' y='+Number(getRow(selected).y).toFixed(1)+' · '+getRow(selected).angle+'°';
      }
      function pos(event){let p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;
        const t=svg.getScreenCTM();if(!t)return{x:0,y:0};p=p.matrixTransform(t.inverse());return{x:p.x,y:H-p.y};}
      svg.addEventListener('pointermove',e=>{if(!drag)return;const p=pos(e),r=getRow(selected);
        r.x=Math.max(0,snap(drag.x+p.x-drag.start.x));
        r.y=Math.max(0,snap(drag.y+p.y-drag.start.y));render()});
      svg.addEventListener('pointerup',()=>{drag=null});
      svg.addEventListener('pointercancel',()=>{drag=null});
      document.addEventListener('keydown',e=>{if(selected===null || e.target.tagName==='INPUT')return;const r=getRow(selected);
        if(e.key.toLowerCase()==='r')r.angle=(Number(r.angle)+15)%360;
        else if(e.key==='ArrowLeft')r.x=snap(Number(r.x)-Number(stepInput.value));
        else if(e.key==='ArrowRight')r.x=snap(Number(r.x)+Number(stepInput.value));
        else if(e.key==='ArrowDown')r.y=snap(Number(r.y)-Number(stepInput.value));
        else if(e.key==='ArrowUp')r.y=snap(Number(r.y)+Number(stepInput.value));else return;
        e.preventDefault();render();});
      document.getElementById('save').onclick=()=>{const blob=new Blob([JSON.stringify({fingerprint:'FINGERPRINT',placements:rows},null,2)],{type:'application/json'});
        const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='layout_edits.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
      render();</script>'''
    for marker,subst in [('HELP_TEXT',escape(labels[0])),('SAVE_TEXT',escape(labels[1])),('INFO_TEXT',escape(labels[2])),
                         ('WIDTH',str(w)),('HEIGHT',str(h)),('FINGERPRINT',fingerprint),('DATA',cfg.replace('</','<\\/')),('ROWS',original.replace('</','<\\/'))]:
        template=template.replace(marker,subst)
    return template
