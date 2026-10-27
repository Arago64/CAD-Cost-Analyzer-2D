"""CAD Cost Analyzer 2D v3.3: checked hybrid nesting and historical layout reuse."""
from __future__ import annotations
from pathlib import Path
import hashlib, json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from sheet_nesting import parse_sheet_dxf
from toolpath2d import CuttingParameters, plan_toolpaths
from process_cost import MATERIALS, TECHNIQUES, suitability, default_params, cost_quote
from svg_preview import svg_sheet
from reporting import build_export, tables
from dxf_export import export_layout_dxf
from layout_editor import update_layout, editor_html, layout_fingerprint
from common_line import common_line_candidates
from remnants import find_remnants, remnants_json, remnants_dxf, import_remnant
from remnant_optimization import remnant_quality
from simulation import audit_plan, simulation_html
from i18n import t, LANGUAGES, method_label, material_label, engine_warning, localized_table
from optimizer_v15 import _signature, _summary as all_metrics
from remnant_portfolio import remnant_metrics, metrics_summary
from remnant_shapes import freeform_metrics
from variant_storage import archive_variants, load_variants
from shapely import affinity

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title='CAD Cost Analyzer 2D', page_icon='📐', layout='wide',
                   initial_sidebar_state='expanded')
st.markdown('''<style>
.block-container{max-width:1500px;padding-top:1.4rem}
[data-testid="stMetric"]{background:rgba(128,140,160,.08);border-radius:10px;padding:13px}
</style>''', unsafe_allow_html=True)

# The language chooser is always the first control at the top-left of the UI.
with st.sidebar:
    language_name = st.selectbox('🌐 Language / Sprache / Язык', list(LANGUAGES),
                                  index=0, key='ui_language')
lang = LANGUAGES[language_name]
def L(key, **kwargs):
    return t(key, lang, **kwargs)

st.title('📐 CAD Cost Analyzer · 2D v3.3 · Platz sparen')
st.caption(L('subtitle'))
st.warning(L('warning_cam'))
st.subheader(L('source_heading'))
left, right = st.columns([2,1])
with left:
    uploads = st.file_uploader(L('upload'), type=['dxf'], accept_multiple_files=True)
with right:
    use_demo = st.checkbox(L('demo'), value=not uploads)

with st.sidebar:
    st.header(L('settings'))
    unit = st.selectbox(L('unit'), ['auto','mm','cm','m','in'],
                        format_func=lambda v: L('auto_unit') if v=='auto' else v)
    tolerance = st.number_input(L('join'), min_value=0., max_value=2., value=0., step=.05)
    quality = st.selectbox(L('quality'), ['fast','balanced','thorough','precision'], index=1,
                           format_func=lambda v:L(v))
    st.caption({'ru':'v3.3: до 50 деталей · NFP/LNS + контактное сжатие + группы + точная геометрия.',
                'de':'v3.3: bis 50 Teile · NFP/LNS + Kontaktdruck + Gruppenbewegungen.',
                'en':'v3.3: up to 50 parts · NFP/LNS + contact-pressure compaction + clusters.'}[lang])
    search_budget_s = st.select_slider({'ru':'Время оптимизации, секунд','de':'Optimierungszeit, Sekunden',
                         'en':'Optimization time, seconds'}[lang],
                         options=[6,12,18,30,60,120],value=30)
    hole_nesting = st.checkbox(L('nest_holes'), value=True, help=L('nest_help'))
    preserve_remnants = st.checkbox(L('keep_remnant'),value=False,help=L('keep_remnant_help'))
    common_enabled = st.checkbox(L('common_toggle'), value=False)
    route_enabled = st.checkbox(L('route_toggle'), value=True)
    stored_stock = st.file_uploader(L('remnant_reuse'),type=['json'],key='stock_import')
    remnant_index = st.number_input(L('remnant_index'),min_value=1,value=1,step=1)
    stock_outline=None
    if stored_stock:
        try:
            stock_outline,_inventory=import_remnant(stored_stock.getvalue(),int(remnant_index)-1)
            W,H=stock_outline.bounds[2],stock_outline.bounds[3]
        except Exception as exc:
            st.error(str(exc));st.stop()
    stock_mode = st.radio(L('sheet_mode'), ['auto','fixed'],
                          format_func=lambda v:L('auto_sheet' if v=='auto' else 'fixed_sheet'))
    if stock_mode == 'fixed':
        stock = st.selectbox(L('stock_size'), ['1000×2000','1250×2500','1500×3000','2000×4000','500×500','custom'],
                             format_func=lambda v:L('custom') if v=='custom' else v)
        if stock == 'custom':
            W = st.number_input(L('stock_width'), min_value=10., value=1000., step=50.)
            H = st.number_input(L('stock_height'), min_value=10., value=2000., step=50.)
        else:
            W,H = [float(z) for z in stock.split('×')]
        allow_multi = st.checkbox(L('multi_sheet'), value=True)
        auto = False
    else:
        W=H=None
        allow_multi=False
        auto=True
    if stock_outline is not None:
        W,H=stock_outline.bounds[2],stock_outline.bounds[3]
        allow_multi=False;auto=False
        st.warning(L('remnant_warning'))
    margin = st.number_input(L('margin'), min_value=0., value=5., step=1.)
    gap = st.number_input(L('gap'), min_value=0., value=3., step=.5)
    st.divider()
    st.header(L('process_heading'))
    material = st.selectbox(L('material'), list(MATERIALS),
                            format_func=lambda v:material_label(v,lang))
    thickness = st.number_input(L('thickness'), min_value=.1, max_value=300., value=3., step=.5)
    if stored_stock:
        if _inventory.get('material') and _inventory['material'] != material:
            st.error('Saved remnant material differs from selected material.');st.stop()
        if _inventory.get('thickness_mm') is not None and abs(float(_inventory['thickness_mm'])-thickness)>1e-6:
            st.error('Saved remnant thickness differs from selected thickness.');st.stop()
    method = st.selectbox(L('method'), list(TECHNIQUES),
                          format_func=lambda v:method_label(v,lang))
    suitable,note = suitability(method,material,thickness)
    if suitable:st.caption('✓ '+L('suitable'))
    else:st.error(L('unsuitable')+f' ({note})')
    tech=TECHNIQUES[method]; preset=default_params(method)
    price = st.number_input(L('material_price'), min_value=0., value=float(MATERIALS[material]['eur_kg']), step=.1)
    machine_rate = st.number_input(L('machine_rate'), min_value=0., value=float(tech['eur_h']), step=5.)
    with st.expander(L('params_heading'), expanded=True):
        feed = st.number_input(L('feed'), min_value=1., value=float(preset.feed_mm_min), step=50.)
        kerf = st.number_input(L('kerf'), min_value=.01, value=float(preset.kerf_mm), step=.1)
        lead = st.number_input(L('lead'), min_value=0., value=float(preset.lead_mm), step=.5)
        rapid = st.number_input(L('rapid'), min_value=1., value=float(preset.rapid_mm_min), step=500.)
        pierce = st.number_input(L('pierce'), min_value=0., value=float(preset.pierce_s), step=.5)
        stepdown = st.number_input(L('stepdown'), min_value=.1, value=float(preset.stepdown_mm), step=.5)
        pass_s = st.number_input(L('pass_setup'), min_value=0., value=float(preset.pass_setup_s), step=.5)
    setup = st.number_input(L('setup'), min_value=0., value=float(tech['setup']), step=1.)
    consumables = st.number_input(L('consumables'), min_value=0., value=float(tech['consumables_eur_h']), step=1.)
    electricity = st.number_input(L('electricity'), min_value=0., value=0., step=1.)
    scrap_credit = st.number_input(L('scrap_credit'), min_value=0., value=0., step=.1)
    overhead = st.number_input(L('overhead'), min_value=0., value=15., step=1.)
    markup = st.number_input(L('markup'), min_value=0., value=10., step=1.)

sources=[]
if uploads:
    sources=[(f.name,f.getvalue()) for f in uploads]
elif use_demo:
    demo=ROOT/'examples'/'DEMO_2D_ZWEI_QUADRATE_KREIS.dxf'
    sources=[(demo.name,demo.read_bytes())]
if not sources:
    st.info(L('load_prompt'))
    st.stop()

pieces=[];issues=[]
for filename,data in sources:
    try:
        scan=parse_sheet_dxf(data,filename,unit,join_tolerance_mm=tolerance)
        issues.extend(f'{filename}: {msg}' for msg in scan.issues)
        for p in scan.pieces:
            p.name=f'{Path(filename).stem}/{p.name}'
            pieces.append(p)
    except Exception as exc:
        issues.append(f'{filename}: {exc}')
if not pieces:
    st.error(L('no_contours'))
    for msg in issues:st.warning(msg)
    st.stop()
for msg in issues:st.warning(msg)

st.write(L('recognized',n=len(pieces)))
with st.expander('ℹ️ '+L('search_details_title')):
    st.write(L('search_details_intro'))
    for key in ('search_fast_details','search_balanced_details','search_thorough_details','search_precision_details'):
        st.write('• '+L(key))
    st.caption(L('search_limits'))
    st.write(L('algorithm_v12'))
    st.write(L('algorithm_v14'))
# Stable DataFrame column IDs allow language switching without altering geometries.
source_table=pd.DataFrame([{'name':p.name,'layer':p.layer,'area':round(p.area_mm2,2),
    'perimeter':round(p.cut_mm,2),'holes':p.holes,'quantity':1} for p in pieces])
config={key:st.column_config.NumberColumn(L(label)) if key in ('area','perimeter','holes')
        else st.column_config.TextColumn(L(label)) for key,label in {
        'name':'col_name','layer':'col_layer','area':'col_area','perimeter':'col_perim',
        'holes':'col_holes'}.items()}
config['quantity']=st.column_config.NumberColumn(L('col_qty'),min_value=0,max_value=50,step=1,format='%d')
edited=st.data_editor(source_table,hide_index=True,use_container_width=True,
      key='quantity_editor_v32',disabled=['name','layer','area','perimeter','holes'],column_config=config)
qty={str(r['name']):int(r['quantity']) for _,r in edited.iterrows()}
if sum(qty.values())>50:
    st.error({'ru':'Максимум 50 деталей на одну раскладку. Уменьши количество экземпляров.',
              'de':'Maximal 50 Teile pro Verschachtelung. Stückzahlen reduzieren.',
              'en':'Maximum 50 pieces per nesting. Reduce quantities.'}[lang])
    st.stop()
if quality=='precision' and sum(qty.values())>5:
    st.info(L('precision_adjusted'))

if hole_nesting:st.caption(L('nest_on'))
if gap<kerf:st.warning(L('gap_warn'))
params=CuttingParameters(method=method,kerf_mm=kerf,lead_mm=lead,feed_mm_min=feed,
    rapid_mm_min=rapid,pierce_s=pierce,stepdown_mm=stepdown,pass_setup_s=pass_s)
# Every saved variant is a separate checked layout. Keep project data between
# Streamlit reruns, but invalidate it when geometry or nesting input changes.
source_payload={
    'source':[{'name':name,'hash':hashlib.sha256(payload).hexdigest()} for name,payload in sources],
    'quantity':qty,'unit':unit,'join':tolerance,'quality':quality,'holes':hole_nesting,
    'auto':auto,'W':W,'H':H,'allow_multi':allow_multi,'margin':margin,'gap':gap,
    'v33':True,'search_budget_s':search_budget_s,
    'material_first_version':33,'remnant':(hashlib.sha256(stored_stock.getvalue()).hexdigest() if stored_stock else None),
}
def _source_digest(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
source_key=_source_digest(source_payload)
legacy_payload=dict(source_payload)
legacy_payload.pop('v33',None)
legacy_payload['v32']=True
legacy_payload['material_first_version']=32
legacy_source_key=_source_digest(legacy_payload)
if st.session_state.get('v15_source_key') != source_key:
    # A single independent search replaces the old Engine 2 / v1.5 / v2.x
    # orchestration.  It runs in a killable process and checkpoints valid layouts.
    from v3_bounded import run as run_v3
    with st.spinner({'ru':'Поиск максимально компактной раскладки…',
                     'de':'Suche nach dem materialsparendsten Layout…',
                     'en':'Finding the most material-efficient layout…'}[lang]):
        search_input={
            'pieces':pieces,'qty':qty,'W':W,'H':H,'margin':margin,'gap':gap,
            'auto':auto,'multi_sheet':allow_multi,'stock_outline':stock_outline,
            'hole_nesting':hole_nesting,'quality':quality,
        }
        try:
            variants,time_report=run_v3(search_input,search_budget_s)
        except Exception as exc:
            st.error(f'Optimizer v3: {exc}')
            st.stop()
        if not variants:
            st.error('No legal nesting solution found.');st.stop()
        st.session_state['v15_variants']=variants
        st.session_state['v15_source_key']=source_key
        st.session_state['v15_base_layout']=variants[0]['layout']
        st.session_state['v23_search_time_report']=time_report
        for obsolete in ('v15_selected','v15_applied_edit','v15_pending','v15_imported_file','v15_prev_preserve'):
            st.session_state.pop(obsolete,None)

report23=st.session_state.get('v23_search_time_report')
if report23:
    st.caption({'ru':f"Полный поиск: {report23['elapsed_seconds']:.1f} с при лимите {report23['requested_seconds']:.0f} с. " + ('Лимит достигнут — сохранён последний завершённый вариант.' if report23['timed_out'] or report23['partial'] else 'Поиск завершён.'),
                'de':f"Gesamtsuche: {report23['elapsed_seconds']:.1f} s / Budget {report23['requested_seconds']:.0f} s. " + ('Zeitlimit: letzter fertiger Stand erhalten.' if report23['timed_out'] or report23['partial'] else 'Abgeschlossen.'),
                'en':f"Entire search: {report23['elapsed_seconds']:.1f} s / {report23['requested_seconds']:.0f} s budget. " + ('Time limit: last complete checkpoint kept.' if report23['timed_out'] or report23['partial'] else 'Finished.')}[lang])

catalog=st.session_state['v15_variants']
with st.expander({'ru':'Как работает новый алгоритм','de':'Wie funktioniert der neue Solver',
                  'en':'How the new solver works'}[lang], expanded=False):
    st.write({'ru':'Гибрид v3.3: до 50 деталей, NFP/LNS, перестановка групп, непрерывное контактное сжатие и точная проверка геометрии. Ручная раскладка используется только после явного импорта. Глобальный оптимум не гарантируется.',
              'de':'Der Solver optimiert Teile-Reihenfolge, Rotationen und Kontaktpositionen. Jede Platzierung wird gegen die tatsächlichen DXF-Polygone, Abstände und Blechgrenzen validiert. Das globale Optimum ist nicht garantiert.',
              'en':'The solver varies part order, rotations, and geometric contacts. Every result is checked against original DXF polygons, clearances, and sheet bounds. Global optimality is not guaranteed.'}[lang])
st.subheader('🗂️ '+{'ru':'Сохранённые варианты раскроя','de':'Gespeicherte Nesting-Varianten','en':'Saved nesting alternatives'}[lang])
# The original left-hand toggle changes which saved strategy is active.
# It never destroys the other alternatives.
if st.session_state.get('v15_prev_preserve') != preserve_remnants:
    st.session_state['v15_prev_preserve']=preserve_remnants
    if catalog:
        # Saving material always wins. Offcut preference only breaks near-ties.
        target=min(catalog,key=lambda v:(v['metrics']['sheets'],v['metrics']['sheet_area_mm2'],
                 -v['metrics'].get('freeform_top3_mm2',0) if preserve_remnants else 0))
        st.session_state['v15_pending']=target['id']
if 'v15_pending' in st.session_state:
    st.session_state['v15_selected']=st.session_state.pop('v15_pending')
ids=[v['id'] for v in catalog]
if st.session_state.get('v15_selected') not in ids:
    st.session_state['v15_selected']=ids[0]
choice=st.selectbox({'ru':'Выбрать рабочий вариант','de':'Aktive Variante wählen','en':'Choose active layout'}[lang],
    ids,key='v15_selected',format_func=lambda x:next(v['name'] for v in catalog if v['id']==x))
selected=next(v for v in catalog if v['id']==choice)

# Selected variant always governs editor, costs, previews and export.
active_layout=selected['layout']
quick_cols=st.columns([2,1,1])
with quick_cols[0]:
    st.caption({'ru':'Открой нужный вариант выше, затем редактируй его во вкладке «Редактор».',
                'de':'Oben Variante auswählen und im Tab „Editor“ bearbeiten.',
                'en':'Choose a variant above, then edit it in the Editor tab.'}[lang])
with quick_cols[1]:
    st.download_button({'ru':'⬇ DXF выбранного','de':'⬇ DXF der Variante','en':'⬇ Selected DXF'}[lang],
        export_layout_dxf(active_layout),file_name=f'Nesting_{selected["id"]}.dxf',
        mime='application/dxf',key='v22_quick_dxf',use_container_width=True)
with quick_cols[2]:
    st.download_button({'ru':'⬇ SVG выбранного','de':'⬇ SVG der Variante','en':'⬇ Selected SVG'}[lang],
        svg_sheet(active_layout,None,0),file_name=f'Nesting_{selected["id"]}.svg',
        mime='image/svg+xml',key='v22_quick_svg',use_container_width=True)
st.caption({'ru':f'Активный вариант: {selected["name"]} · {len(catalog)} сохранено · вложено деталей: {active_layout.get("nested_items",0)}',
            'de':f'Aktive Variante: {selected["name"]} · {len(catalog)} gespeichert',
            'en':f'Active alternative: {selected["name"]} · {len(catalog)} saved'}[lang])
show_cols=['name','sheets','sheet_area_mm2','largest_rect_mm2',
           'top_rectangles_total_mm2','largest_connected_mm2','fragment_count']
comparison=[]
for v in catalog:
    m=v['metrics']
    comparison.append({'ID':v['id'],'Вариант / Variant':v['name'],
       'Листов / Sheets':m['sheets'],'Вложено / Nested':v['layout'].get('nested_items',0),'Лист, мм² / Sheet':round(m['sheet_area_mm2']),
       'Лучший прямоугольник, мм² / Single':round(m['largest_rect_mm2']),
       'До 3 прямоугольников, мм² / Combined':round(m['top_rectangles_total_mm2']),
       'Прямоугольники и круги, мм² / Mixed':round(m.get('mixed_shapes_total_mm2',0)),
       'Связная область, мм² / Connected':round(m['largest_connected_mm2']),
       'Свободный полигон, мм² / Freeform':round(m.get('freeform_largest_mm2',0)),
       'Составной остаток, мм² / Compound':round(m.get('compound_largest_mm2',0))})
st.dataframe(pd.DataFrame(comparison),hide_index=True,use_container_width=True)

variant_upload=st.file_uploader({'ru':'Восстановить сохранённые варианты (ZIP/JSON)',
      'de':'Gespeicherte Varianten laden (ZIP/JSON)',
      'en':'Restore saved alternatives (ZIP/JSON)'}[lang],type=['zip','json'],key='v15_import_project')
if variant_upload is not None:
    file_id=hashlib.sha256(variant_upload.getvalue()).hexdigest()
    if st.session_state.get('v15_imported_file')!=file_id:
        try:
            restored=load_variants(variant_upload.getvalue(),st.session_state['v15_base_layout'],(source_key,legacy_source_key))
            existing={_signature(v['layout']) for v in catalog}
            for row in restored:
                if _signature(row['layout']) in existing:continue
                row['id']='variant_'+str(len(catalog)+1)
                catalog.append(row);existing.add(_signature(row['layout']))
            st.session_state['v15_imported_file']=file_id
            st.success('Saved layouts restored / Варианты восстановлены')
            st.rerun()
        except Exception as exc:
            st.error(f'Cannot restore alternatives: {exc}')

edit_file=st.file_uploader(L('edit_import'),type=['json'],key='edit_import_v15')
if edit_file is not None:
    digest=hashlib.sha256(edit_file.getvalue()).hexdigest()
    if st.session_state.get('v15_applied_edit')!=digest:
        try:
            # Find which saved variant generated this editor JSON; it may not
            # be the variant currently selected after Streamlit rerender.
            payload=json.loads(edit_file.getvalue())
            original=next((v for v in catalog
                    if layout_fingerprint(v['layout'])==payload.get('fingerprint')),None)
            if original is None:raise ValueError('Editor JSON does not match any variant in this project')
            updated=update_layout(original['layout'],payload,stock_outline=stock_outline)
            fresh={'id':'variant_'+str(len(catalog)+1),'name':'✏️ Manual / Ручной '+original['id'],
                   'layout':updated,'metrics':all_metrics(updated,grid=18),'origin':'manual'}
            catalog.append(fresh)
            st.session_state['v15_applied_edit']=digest
            st.session_state['v15_pending']=fresh['id']
            st.rerun()
        except Exception as exc:st.error(f'Edit not applied: {exc}')

# Reuse previously validated layouts across solver upgrades. This intentionally
# distinguishes a user-supplied historical/manual seed from an autonomous run.
with st.expander({'ru':'📥 Импорт старой раскладки как начального варианта (v3.1 / v3.2)',
                  'de':'📥 Früheres Layout als Startposition importieren',
                  'en':'📥 Import previous layout as optimization seed'}[lang]):
    st.caption({'ru':'Загрузи ZIP старого результата с Ergebnis_2D.json и Nesting_Sheet_1.dxf. Необязательно добавь JSON ручных правок. Программа сверит исходные детали и зазоры перед использованием.',
                'de':'Vorheriges Ergebnis-ZIP mit Ergebnis_2D.json und Nesting_Sheet_1.dxf laden. Optional manuelle Bearbeitungen (JSON). Alle Geometrien werden gegen den aktuellen Auftrag geprüft.',
                'en':'Upload an older result ZIP containing Ergebnis_2D.json and Nesting_Sheet_1.dxf; optionally add manual-edit JSON. Shapes and clearances are checked before acceptance.'}[lang])
    old_job=st.file_uploader({'ru':'Архив старого результата','de':'Frühere Ergebnis-ZIP','en':'Previous result ZIP'}[lang],
                           type=['zip'],key='v33_legacy_result')
    old_edits=st.file_uploader({'ru':'Ручные правки (необязательно)','de':'Manuelle Änderungen (optional)',
                                'en':'Manual edits (optional)'}[lang],type=['json'],key='v33_legacy_edits')
    if st.button({'ru':'Импортировать и проверить','de':'Importieren und prüfen','en':'Import and validate'}[lang],
                 key='v33_import_legacy',disabled=old_job is None):
        try:
            from reference_import import load_legacy_reference
            restored=load_legacy_reference(old_job.getvalue(),st.session_state['v15_base_layout'],
                          old_edits.getvalue() if old_edits else None)
            signature=_signature(restored)
            matched=next((v for v in catalog if _signature(v['layout'])==signature),None)
            if matched is None:
                new_id='variant_'+str(len(catalog)+1)
                catalog.append({'id':new_id,'name':'📥 Reference / Ручной предыдущий вариант',
                                'layout':restored,'metrics':all_metrics(restored,grid=18),
                                'origin':'historical'})
            else:new_id=matched['id']
            st.session_state['v15_pending']=new_id
            st.rerun()
        except Exception as exc:
            st.error(f'Historical layout rejected: {exc}')

buttons=st.columns([1,1,2])
with buttons[0]:
    optimize_clicked=st.button({'ru':'🔄 Улучшить выбранный','de':'🔄 Variante optimieren',
        'en':'🔄 Refine selected'}[lang],use_container_width=True)
with buttons[1]:
    st.download_button({'ru':'📦 Сохранить ВСЕ варианты','de':'📦 Alle Varianten speichern',
        'en':'📦 Export ALL alternatives'}[lang], archive_variants(catalog,source_key),
        file_name='CAD2D_v21_All_Variants.zip',mime='application/zip',use_container_width=True)
with buttons[2]:
    st.caption({'ru':'Редактируй выбранную версию ниже; загрузи JSON правок — изменённая раскладка сохранится отдельным вариантом. Кнопка улучшения не удаляет исходную раскладку.',
       'de':'Variante im Editor bearbeiten; Änderungen werden als neue Alternative gespeichert.',
       'en':'Edit the selected layout below; importing edits saves a separate alternative.'}[lang])
if optimize_clicked:
    from v3_bounded import run as run_v3
    with st.spinner({'ru':'Повторная оптимизация из выбранного варианта…',
                    'de':'Erneute Optimierung der ausgewählten Variante…',
                    'en':'Reoptimizing from selected layout…'}[lang]):
        next_input={'pieces':pieces,'qty':qty,'W':W,'H':H,'margin':margin,'gap':gap,
              'auto':auto,'multi_sheet':allow_multi,'stock_outline':stock_outline,
              'hole_nesting':hole_nesting,'quality':quality}
        improved,refine_time=run_v3(next_input,search_budget_s,seed_layout=selected['layout'])
        existing={_signature(v['layout']) for v in catalog}
        additions=[]
        for cand in improved:
            if _signature(cand['layout']) in existing:continue
            existing.add(_signature(cand['layout']))
            additions.append(dict(cand,id=f'variant_{len(catalog)+len(additions)+1}',
                                  origin='reoptimized'))
        if additions:
            catalog.extend(additions)
            st.session_state['v15_pending']=additions[0]['id']
            st.rerun()
        else:
            st.info({'ru':'В указанное время улучшение не найдено.',
                     'de':'Keine bessere Variante innerhalb des Zeitlimits gefunden.',
                     'en':'No improved variant within the time budget.'}[lang])
layout=selected['layout']
if False and layout.get('engine_2'):
    st.caption(f"Nesting Engine 2.0 · {layout.get('engine_2_trials',0)} checked candidate layouts · "
               f"{layout.get('engine_2_search_seconds',0)} s · "
               "geometry-checked heuristic, no proven global optimum")
base_fingerprint=layout_fingerprint(layout)

try:
    with st.spinner(L('processing')):
        plan=plan_toolpaths(layout,params,route_optimization=route_enabled)
        shared=common_line_candidates(layout,kerf) if common_enabled else []
        plan['common_lines']=shared
        plan['common_line_enabled']=bool(common_enabled)
        audit=audit_plan(layout,plan,kerf)
        free_report=remnant_metrics(layout,grid=18)
        best_scrap=[{'sheet_index':sh['sheet_index'],'area_mm2':round(r.area,2),
           'bbox_mm':tuple(round(v,2) for v in r.bounds),
           'width_mm':round(r.bounds[2]-r.bounds[0],2),
           'height_mm':round(r.bounds[3]-r.bounds[1],2),
           'rectangle_rank':index+1}
             for sh in free_report for index,r in enumerate(sh['rectangles'])]
        plan['audit']=audit
        quote=cost_quote(layout,plan,params,thickness_mm=thickness,material=material,eur_kg=price,
                    hourly_eur=machine_rate,consumables_eur_h=consumables,setup_min=setup,
                    overhead_pct=overhead,markup_pct=markup,electricity_eur_h=electricity,
                    scrap_credit_eur_kg=scrap_credit)
except Exception as exc:
    st.error(L('layout_error',err=str(exc)))
    st.stop()

st.subheader(L('results')+' — '+selected['name'])
c1,c2,c3,c4,c5=st.columns(5)
c1.metric(L('parts'),layout['items'])
c2.metric(L('sheets'),layout['sheet_count'])
c3.metric(L('utilization'),f"{layout['utilization']:.1f}%")
c4.metric(L('cut_length'),f"{plan['effective_cut_mm']/1000:.2f} m")
c5.metric(L('offer'),f"{quote['offer_eur']:.2f} €")
# Algorithm description and engine warnings originate from the CAD core (German).
st.caption({'ru':'Материал — главный критерий. Каждая раскладка проверяется геометрически.', 'de':'Materialverbrauch hat Priorität. Jede Position wird geometrisch geprüft.', 'en':'Material first. Every placement is validated geometrically.'}[lang])
st.caption(L('remnant_mode_on' if preserve_remnants else 'remnant_mode_off'))
if False and str(layout.get('hybrid_engine','')).startswith('NFP'):
    st.caption(L('hybrid_summary',n=layout.get('hybrid_evaluated',0)))
else:
    pass
if layout.get('polygonal_largest_rectangle_mm2') is not None:
    st.caption(L('polygonal_remnant')+f": {layout['polygonal_largest_rectangle_mm2']:,.0f} mm²")

if layout.get('nested_items',0):st.info(L('nested_count',n=layout['nested_items']))
if best_scrap:
    max_scrap=max((r['area_mm2'] for r in best_scrap),default=0)
    st.caption(L('remnant_rectangle_metric')+f': {max_scrap:,.0f} mm² · '+L('remnant_rectangle_description'))
    if stock_outline is None:
        from nesting_refinement import _free_connected
        zones=[_free_connected([p for p in layout['placements'] if p.get('sheet_index',0)==sh['index']],
                     sh['width_mm'],sh['height_mm'],margin,gap)[0] for sh in layout['sheets']]
        st.caption(L('largest_connected')+f': {max(zones,default=0):,.0f} mm²')
if str(selected.get('origin',''))=='manual':st.success(L('edit_applied'))
if common_enabled:st.info(L('common_info',n=len(shared),mm=sum(x['length_mm'] for x in shared)))
if not plan['complete']:st.error(L('incomplete'))
for msg in plan['warnings']:st.warning(engine_warning(msg,lang))

nest_tab,path_tab,editor_tab,sim_tab,remnant_tab,cost_tab,compare_tab,export_tab=st.tabs([
    L('tab_nest'),L('tab_path'),L('tab_editor'),L('tab_sim'),L('tab_remnant'),
    L('tab_cost'),L('tab_compare'),L('tab_export')])
with nest_tab:
    chosen=st.selectbox(L('sheet_choice'),[s['index'] for s in layout['sheets']],
                        format_func=lambda i:L('sheet_n',n=i+1))
    sh=layout['sheets'][chosen]
    st.caption(L('sheet_dims',n=chosen+1,w=sh['width_mm'],h=sh['height_mm']))
    show_offcut=st.checkbox(L('show_offcut'),value=True)
    show_freeform=st.checkbox({'ru':'Показывать пригодные круги и прямоугольники вместо только прямоугольников',
         'de':'Wiederverwendbare Kreise und Rechtecke hervorheben',
         'en':'Show reusable circles and rectangles (mixed shapes)'}[lang],value=True)
    show_compound=st.checkbox({'ru':'Показывать составные остатки: Г-, Т-, П-образные и другие цельные контуры',
         'de':'Zusammengesetzte Restkonturen (L/T/U und Freiform) anzeigen',
         'en':'Show compound offcuts (L/T/U and other connected profiles)'}[lang],value=True)
    preview_mode=('irregular' if show_offcut and show_compound else
                  'mixed' if show_offcut and show_freeform else show_offcut)
    components.html(svg_sheet(layout,None,chosen,highlight_offcut=preview_mode),
                    height=760,scrolling=True)
    if show_offcut:
        st.caption({'ru':'F1–Fn — цельные составные контуры, R/C — вписанные базовые формы. Зоны геометрически свободны, но отделение на реальном станке ещё нужно проверить.',
            'de':'F1–Fn sind zusammenhängende zusammengesetzte Konturen; R/C sind einfache eingeschriebene Formen. Trennbarkeit ist noch zu prüfen.',
            'en':'F1–Fn are connected compound outlines; R/C are simple inscribed shapes. Real separation still requires CAM validation.'}[lang])
        this_free=remnant_metrics(layout,grid=18)[chosen]
        st.metric('Сумма до трёх прямоугольных остатков / Up to 3 rectangles',
            f'{this_free["combined_rectangles_mm2"]:,.0f} mm²')
        shape_report=freeform_metrics(layout,grid=11)
        st.metric('До трёх кругов/прямоугольников / Mixed offcuts',
                  f'{sum(a["area_mm2"] for a in shape_report["mixed"][chosen]):,.0f} mm²')
        st.caption('Крупнейшая связная свободная зона / Largest connected free polygon: '
            f'{this_free["largest_connected_mm2"]:,.0f} mm²')
    if not auto and layout['sheet_count']==1 and layout['net_mm2']<layout['sheet_mm2']*0.27:
        st.info(L('oversized_sheet'))
    if layout['placements']:
        own=[p['shape'].bounds for p in layout['placements'] if p.get('sheet_index',0)==chosen]
        if own:
            dw=max(r[2] for r in own)-min(r[0] for r in own)
            dh=max(r[3] for r in own)-min(r[1] for r in own)
            st.caption(f"{L('occupied_group')}: {dw:.1f} × {dh:.1f} mm")
    positions,_=tables(layout,plan)
    st.dataframe(pd.DataFrame(localized_table(positions,lang)),hide_index=True,use_container_width=True)
    st.caption(L('sheet_note'))
    if stock_outline is None and st.checkbox(L('compare_offcut'),value=False):
        # Compare ALREADY COMPUTED alternatives; do not silently repeat the
        # potentially very expensive full-quality baseline outside the budget.
        alt=(min(catalog,key=lambda entry:(entry['layout']['sheet_count'],entry['layout']['sheet_mm2']))
             if preserve_remnants else
             max(catalog,key=lambda entry:entry['metrics'].get('freeform_largest_mm2',0)))['layout']
        current_rem=sum(r['combined_rectangles_mm2'] for r in remnant_metrics(layout,grid=18))
        other_rem=sum(r['combined_rectangles_mm2'] for r in remnant_metrics(alt,grid=18))
        label_current=L('remnant_mode_on' if preserve_remnants else 'remnant_mode_off')
        label_other=L('remnant_mode_off' if preserve_remnants else 'remnant_mode_on')
        compare=pd.DataFrame([
           {'mode':label_current,'sheets':layout['sheet_count'],'sheet_area_mm2':round(layout['sheet_mm2']),
            'sum_top_rectangles_mm2':current_rem},
           {'mode':label_other,'sheets':alt['sheet_count'],'sheet_area_mm2':round(alt['sheet_mm2']),
            'sum_top_rectangles_mm2':other_rem}])
        st.dataframe(compare,hide_index=True,use_container_width=True)
        if layout['sheet_mm2']==alt['sheet_mm2'] and abs(current_rem-other_rem)<.01:
            st.info(L('compare_same'))

with path_tab:
    chosen_path=st.selectbox(L('path_sheet'),[s['index'] for s in layout['sheets']],
                             format_func=lambda i:L('sheet_n',n=i+1),key='path_sheet_v09')
    st.caption(L('path_key'))
    components.html(svg_sheet(layout,plan,chosen_path),height=760,scrolling=True)
    _,routes=tables(layout,plan)
    st.dataframe(pd.DataFrame(localized_table(routes,lang)),hide_index=True,use_container_width=True)
    st.info(L('path_note'))
    st.caption(f'Rapid travel: {plan["rapid_length_mm"]:.1f} mm')
    if common_enabled:
        st.info(L('common_advisory'))
        if shared:
            st.dataframe(pd.DataFrame(shared),hide_index=True,use_container_width=True)
        else:st.caption(L('common_none'))
with editor_tab:
    st.markdown('#### '+{'ru':'Редактируемый вариант','de':'Zu bearbeitende Variante','en':'Variant to edit'}[lang])
    editor_open=st.selectbox({'ru':'Открыть один из сохранённых вариантов',
             'de':'Gespeicherte Variante öffnen','en':'Open saved alternative'}[lang],
        ids,index=ids.index(selected['id']),
        format_func=lambda v:next(item['name'] for item in catalog if item['id']==v),
        key='v22_editor_open_'+selected['id'])
    if editor_open != selected['id']:
        st.session_state['v15_pending']=editor_open
        st.rerun()
    st.caption({'ru':'Любая проверенная правка сохраняется новой версией. Исходная остаётся в списке.',
        'de':'Jede geprüfte Änderung ergibt eine neue Variante; die ursprüngliche bleibt erhalten.',
        'en':'Each validated edit creates a new variant; the original remains available.'}[lang])
    st.write(L('edit_explainer'))
    st.caption('v1.5: Use the interactive drag editor (JSON workflow) OR change coordinates directly in the table below and apply them immediately. Original alternative is preserved.')
    sid=st.selectbox(L('edit_sheet'),[sh['index'] for sh in layout['sheets']],key='editor_sheet',
                     format_func=lambda i:L('sheet_n',n=i+1))
    components.html(editor_html(layout,sid,lang,reference_fingerprint=base_fingerprint),height=710,scrolling=True)
    st.caption(L('edit_hint'))
    st.markdown('#### '+{'ru':'Мгновенные правки и экспорт DXF','de':'Direkt bearbeiten und DXF exportieren',
        'en':'Edit coordinates and export DXF immediately'}[lang])
    st.caption({'ru':'Измени X, Y или угол, нажми «Применить». После проверки появится новый вариант, доступный для DXF-экспорта. Все координаты в мм.',
        'de':'X, Y oder Winkel bearbeiten, dann anwenden. Ein neuer validierter DXF-Export wird verfügbar.',
        'en':'Change X, Y or angle and apply. A new validated alternative is immediately available for DXF export.'}[lang])
    with st.form('v15_direct_edit_'+selected['id']):
        original_rows=[{'index':i,'part':p['name'],'sheet_index':p.get('sheet_index',0),
           'x':round(float(p['shape'].bounds[0]),3),'y':round(float(p['shape'].bounds[1]),3),
           'angle':round(float(p['angle']),2)} for i,p in enumerate(layout['placements'])]
        raw_edit=st.data_editor(pd.DataFrame(original_rows),hide_index=True,use_container_width=True,
               disabled=['index','part','sheet_index'],key='direct_table_'+selected['id'])
        direct_apply=st.form_submit_button('✓ '+{'ru':'Применить и сохранить новый вариант',
             'de':'Anwenden und als Variante speichern','en':'Apply and save new alternative'}[lang])
    if direct_apply:
        try:
            rows={'fingerprint':base_fingerprint,'placements':[
                {'index':int(z['index']),'x':float(z['x']),'y':float(z['y']),
                 'angle':float(z['angle']),'sheet_index':int(z['sheet_index'])}
                 for _,z in raw_edit.iterrows()]}
            proposed=update_layout(layout,rows,stock_outline=stock_outline)
            candidate={'id':'variant_'+str(len(catalog)+1),'name':'✏️ Direct edit / Таблица',
                'layout':proposed,'metrics':all_metrics(proposed,grid=18),'origin':'manual'}
            catalog.append(candidate)
            st.session_state['v15_pending']=candidate['id']
            st.rerun()
        except Exception as exc:st.error(f'Invalid placement (changes were not applied): {exc}')
    with st.form('v15_group_edit_'+selected['id']):
        d1,d2,d3=st.columns(3)
        dx=d1.number_input('Move ALL ΔX, mm',value=0.,step=1.,key='all_dx_'+selected['id'])
        dy=d2.number_input('Move ALL ΔY, mm',value=0.,step=1.,key='all_dy_'+selected['id'])
        da=d3.selectbox('Rotate ALL °',[0,90,180,270],key='all_turn_'+selected['id'])
        group_apply=st.form_submit_button('↗ '+{'ru':'Переместить всю группу и сохранить',
            'de':'Alle Teile als Gruppe verschieben','en':'Move whole group and save'}[lang])
    if group_apply:
        try:
            cx=sum(p['shape'].centroid.x for p in layout['placements'])/len(layout['placements'])
            cy=sum(p['shape'].centroid.y for p in layout['placements'])/len(layout['placements'])
            group=[]
            for p in layout['placements']:
                sh=affinity.rotate(p['shape'],da,origin=(cx,cy))
                sh=affinity.translate(sh,xoff=dx,yoff=dy)
                group.append(dict(p,shape=sh,x=sh.bounds[0],y=sh.bounds[1],angle=(p['angle']+da)%360))
            candidate_layout=_rebuild(group,layout,False,'v1.5 validated rigid group edit')
            if not _validate(candidate_layout):raise ValueError('Group outside sheet or clearance violation')
            candidate={'id':'variant_'+str(len(catalog)+1),'name':'↗ Whole group / Группой',
                'layout':candidate_layout,'metrics':all_metrics(candidate_layout,grid=18),'origin':'manual'}
            catalog.append(candidate)
            st.session_state['v15_pending']=candidate['id'];st.rerun()
        except Exception as exc:st.error(f'Invalid group transform: {exc}')
with sim_tab:
    st.info(L('simulation_info'))
    sid=st.selectbox(L('path_sheet'),[sh['index'] for sh in layout['sheets']],key='sim_sheet',
                     format_func=lambda i:L('sheet_n',n=i+1))
    components.html(simulation_html(layout,plan,sid,lang,shared if common_enabled else None),height=700)
    st.warning(L('sim_problems',n=len(audit['problems']))) if audit['problems'] else st.success(L('sim_ok'))
    if audit['problems']:
        st.dataframe(pd.DataFrame(audit['problems']),hide_index=True,use_container_width=True)
with remnant_tab:
    from irregular_remnants import irregular_remnants, irregular_remnants_dxf, irregular_remnants_json
    st.markdown('#### '+{'ru':'Остатки произвольной формы','de':'Freiform-Reststücke',
                      'en':'Freeform reusable offcuts'}[lang])
    min_neck=st.number_input({'ru':'Минимальная ширина перемычки, мм',
        'de':'Minimale Engstelle, mm','en':'Minimum narrow passage width, mm'}[lang],
        min_value=0.,max_value=100.,value=8.,step=1.,key='v22_min_neck')
    min_irreg_area=st.number_input({'ru':'Минимальная площадь остатка, мм²',
        'de':'Mindestfläche Reststück, mm²','en':'Minimum offcut area, mm²'}[lang],
        min_value=0.,value=1000.,step=500.,key='v22_min_irreg')
    arbitrary=irregular_remnants(layout,kerf_mm=kerf,min_neck_mm=min_neck,
                                  min_area_mm2=min_irreg_area)
    st.caption({'ru':'Г-образные, ступенчатые и другие цельные полигональные остатки; отверстия сохраняются. Минимальная ширина отсекает слишком узкие перемычки. Контуры — предварительная геометрическая оценка, не готовая технология отделения.',
        'de':'L-, Stufen- und andere zusammenhängende Restformen. Geometrische Kandidaten, keine geprüfte Trennstrategie.',
        'en':'L-shaped, stepped and other connected polygon remnants. Geometrical candidates; separation is not CAM-verified.'}[lang])
    if arbitrary:
        st.dataframe(pd.DataFrame([{'ID':f'F{i+1}','Sheet':r['sheet_index']+1,
            'Area mm²':round(r['area_mm2'],1), 'Bounds mm':tuple(round(v,1) for v in r['polygon'].bounds),
            'Holes':len(r['polygon'].interiors),
            'Составных участков / Components':r.get('component_count',1)} for i,r in enumerate(arbitrary)]),
            hide_index=True,use_container_width=True)
        st.metric({'ru':'Суммарная площадь пригодных полигонов','de':'Summe Freiformflächen',
                   'en':'Total freeform offcut area'}[lang],f"{sum(r['area_mm2'] for r in arbitrary):,.0f} mm²")
        cols=st.columns(2)
        with cols[0]:
            st.download_button('DXF — Freeform',irregular_remnants_dxf(arbitrary),
              file_name=f'Freeform_{selected["id"]}.dxf',mime='application/dxf')
        with cols[1]:
            st.download_button('JSON — Freeform',irregular_remnants_json(arbitrary,material=material,thickness_mm=thickness),
              file_name=f'Freeform_{selected["id"]}.json',mime='application/json')
        show_shape=st.checkbox({'ru':'Показывать произвольные остатки на чертеже',
            'de':'Freiformreste im Plan anzeigen','en':'Show freeform scraps in layout'}[lang],
            value=True,key='v22_show_freeform')
        if show_shape:
            sid_free=st.selectbox({'ru':'Лист для остатков','de':'Reststücke Blatt',
                 'en':'Freeform sheet'}[lang],[x['index'] for x in layout['sheets']],key='v22_freeform_sheet')
            components.html(svg_sheet(layout,None,sid_free,highlight_offcut='irregular',
                     irregular_candidates=arbitrary),height=650,scrolling=True)
    else:
        st.info({'ru':'После фильтрации по площади и ширине пригодных цельных областей не найдено.',
                 'de':'Keine passenden zusammenhängenden Restbereiche.',
                 'en':'No connected usable offcuts satisfy the filters.'}[lang])
    st.divider()
    from material_priority import reusable_offcuts
    st.markdown('#### ♻️ '+{'ru':'Сохранить 2–3 полезных обрезка','de':'2–3 nutzbare Reststücke aufbewahren',
                     'en':'Save 2–3 reusable offcuts'}[lang])
    offcol1,offcol2=st.columns(2)
    with offcol1:
        keep_count=st.selectbox({'ru':'Максимум обрезков','de':'Maximale Reststückzahl','en':'Maximum offcuts'}[lang],
                                 [2,3],index=1,key='material_keep_count')
    with offcol2:
        keep_threshold=st.selectbox({'ru':'Минимальная площадь, % листа','de':'Mindestfläche, % des Blechs',
                    'en':'Minimum area, % of sheet'}[lang],[5,7,10],index=1,key='material_keep_threshold')
    saved_offcuts=reusable_offcuts(layout,threshold_pct=keep_threshold,count=keep_count,
                                   kerf_mm=kerf,min_neck_mm=min_neck)
    if saved_offcuts:
        st.dataframe(pd.DataFrame([{'ID':f'R{i+1}','Sheet':r['sheet_index']+1,
                'Area mm²':round(r['area_mm2']), 'Sheet %':r['area_pct'],
                'Bounds mm':tuple(round(v,1) for v in r['polygon'].bounds)}
                for i,r in enumerate(saved_offcuts)]),hide_index=True,use_container_width=True)
        sid_keep=st.selectbox({'ru':'Лист с обрезками','de':'Blech für Reststücke',
                 'en':'Sheet with offcuts'}[lang], [sh['index'] for sh in layout['sheets']],key='material_offcuts_sheet')
        components.html(svg_sheet(layout,None,sid_keep,highlight_offcut='irregular',
                        irregular_candidates=saved_offcuts),height=650,scrolling=True)
        ac,bc=st.columns(2)
        with ac:st.download_button('DXF — selected offcuts',irregular_remnants_dxf(saved_offcuts),
                    file_name='Reusable_Offcuts.dxf',mime='application/dxf')
        with bc:st.download_button('JSON — selected offcuts',irregular_remnants_json(saved_offcuts,
                    material=material,thickness_mm=thickness),file_name='Reusable_Offcuts.json',mime='application/json')
        st.caption({'ru':'Процент считается от площади исходного листа. Показаны реальные контуры без перекрытий. Возможность физического отделения проверяется отдельно.',
          'de':'Prozent des Ausgangsblechs. Exakte, nicht überlappende Konturen; Trennbarkeit nicht geprüft.',
          'en':'Percent of original sheet. True nonoverlapping outlines; physical separation is not verified.'}[lang])
    else:
        st.info({'ru':'Обрезков, превышающих порог, не найдено.', 'de':'Keine Reststücke über dem Grenzwert.',
                 'en':'No offcuts exceed the selected threshold.'}[lang])
    st.divider()
    st.info(L('remnant_info'))
    if best_scrap:
        st.metric(L('remnant_rectangle_metric'),
                  f"{max(r['area_mm2'] for r in best_scrap):,.0f} mm²")
        st.caption(L('remnant_rectangle_description'))
        st.dataframe(pd.DataFrame(best_scrap),hide_index=True,use_container_width=True)
    else:
        st.caption(L('remnant_rectangle_na'))
    remnant_min=st.number_input(L('remnant_min'),min_value=0.,value=1000.,step=500.)
    leftover=find_remnants(layout,kerf_mm=kerf,min_area_mm2=remnant_min)
    st.write(L('remnant_total',n=len(leftover)))
    if leftover:
        st.dataframe(pd.DataFrame([{'sheet':r['sheet_index']+1,'area_mm2':r['area_mm2'],
                    'bounds_mm':str(r['bbox_mm'])} for r in leftover]),hide_index=True)
        st.download_button(L('remnant_inventory'),remnants_json(leftover,material=material,thickness_mm=thickness),
            file_name='Remnant_Inventory.json',mime='application/json')
        st.download_button(L('remnant_export'),remnants_dxf(leftover),
            file_name='Remnant_Outlines.dxf',mime='application/dxf')
with cost_tab:
    k1,k2,k3,k4=st.columns(4)
    k1.metric(L('sheet_mass'),f"{quote['sheet_kg']:.3f} kg")
    k2.metric(L('part_mass'),f"{quote['parts_kg']:.3f} kg")
    k3.metric(L('process_time'),f"{quote['process_min']:.2f} min")
    k4.metric(L('time_total'),f"{quote['total_min']:.2f} min")
    breakdown=[('c_material',quote['material_eur']),('c_machine',quote['machine_eur']),
       ('c_consume',quote['consumables_eur']),('c_energy',quote['electricity_eur']),
       ('c_scrap',-quote['scrap_credit_eur']),('c_overhead',quote['overhead_eur']),
       ('c_markup',quote['offer_eur']-quote['manufacturing_eur'])]
    st.dataframe(pd.DataFrame([{L('c_line'):L(n),'EUR':v} for n,v in breakdown]),
                 hide_index=True,use_container_width=True)
    st.markdown(L('c_offer',value=quote['offer_eur'],each=quote['per_part_eur']))
    st.caption(L('c_note'))
with compare_tab:
    st.write(L('compare_intro'))
    comparisons=[]
    for mt,settings in TECHNIQUES.items():
        ok,reason=suitability(mt,material,thickness)
        alt=default_params(mt)
        try:
            p=plan_toolpaths(layout,alt,route_optimization=route_enabled)
            result=cost_quote(layout,p,alt,thickness_mm=thickness,material=material,eur_kg=price,
                hourly_eur=settings['eur_h'],consumables_eur_h=settings['consumables_eur_h'],
                setup_min=settings['setup'],overhead_pct=overhead,markup_pct=markup)
            fits=p['complete'] and all('Teileabstand kleiner' not in x for x in p['warnings'])
            comparisons.append({L('compare_method'):method_label(mt,lang),L('compare_ok'):ok,
                L('compare_fit'):fits,L('compare_time'):round(result['process_min'],2),
                L('compare_price'):round(result['offer_eur'],2),L('compare_note'):(L('suitable') if ok else L('unsuitable'))})
        except Exception as exc:
            comparisons.append({L('compare_method'):method_label(mt,lang),L('compare_note'):str(exc)})
    st.dataframe(pd.DataFrame(comparisons),hide_index=True,use_container_width=True)
    st.caption(L('compare_disclaimer'))
with export_tab:
    st.markdown('### '+L('export_heading'))
    st.write(L('export_intro'))
    include_labels=st.checkbox(L('export_labels'),value=True,key='dxf_labels_v09')
    st.download_button(L('export_combined'),export_layout_dxf(layout,include_labels=include_labels),
        file_name=f'Nesting_{selected["id"]}_All_Sheets.dxf',mime='application/dxf',type='primary',
        key='all_sheets_dxf')
    cols=st.columns(min(3,len(layout['sheets'])))
    for sheet in layout['sheets']:
        index=sheet['index']
        with cols[index%len(cols)]:
            st.download_button(L('export_one',n=index+1),
                export_layout_dxf(layout,index,include_labels=include_labels),
                file_name=f'Nesting_{selected["id"]}_Sheet_{index+1}.dxf',mime='application/dxf',
                key=f'one_sheet_dxf_{index}')
    st.caption(L('export_dxf_note'))
    st.divider()
    meta={'files':[a for a,_ in sources],'material':material,'thickness_mm':thickness,
          'procedure':method,'units':unit,'warnings':issues+plan['warnings'],
          'common_line_enabled':common_enabled,'route_optimization':route_enabled,
          'quality':quality,'preserve_remnants':preserve_remnants,'largest_free_rectangles':best_scrap,'manual_edit_applied':selected.get('origin')=='manual',
          'variant_id':selected['id'],'variant_name':selected['name'],
          'multi_rectangle_metrics':selected['metrics'],
          'assumptions':{'feed_mm_min':feed,'kerf_mm':kerf,'lead_mm':lead,'eur_kg':price,
                         'hourly_eur':machine_rate,'consumables_eur_h':consumables,
                         'setup_min':setup,'overhead_pct':overhead,'markup_pct':markup}}
    archive=build_export(layout,plan,quote,meta,
        remnants=find_remnants(layout,kerf_mm=kerf,min_area_mm2=1000.),audit=audit)
    st.download_button(L('export_archive'),archive,file_name=f'CAD_Cost_2D_Result_v32_{selected["id"]}.zip',
                       mime='application/zip',key='whole_project_zip')
    st.caption(L('export_archive_note'))
with st.expander(L('help_title')):
    st.markdown(L('help_text'))
