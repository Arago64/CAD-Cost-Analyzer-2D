"""v3 end-to-end smoke and geometry safety regressions."""
from io import BytesIO
from shapely.geometry import box,Polygon
from sheet_nesting import CutPiece, parse_sheet_dxf
from nesting_v3 import optimize, _score
from optimizer_v15 import _validate
from dxf_export import export_layout_dxf
from layout_editor import layout_fingerprint, update_layout, editor_html
from material_priority import reusable_offcuts
from svg_preview import svg_sheet
from i18n import LANGUAGES, t


def two_parts():
    return [CutPiece('A',box(0,0,100,50),'PART'),CutPiece('B',box(0,0,100,50),'PART')]


def test_auto_material_minimum_and_valid_clearance():
    results=optimize(two_parts(),{'A':1,'B':1},auto=True,margin=5,gap=3,budget=2)
    assert results and all(_validate(l) for l in results)
    assert results[0]['sheet_mm2'] <= 12430.001
    assert len(results[0]['placements'])==2


def test_fixed_sheet_and_independent_scoring():
    results=optimize(two_parts(),{'A':1,'B':1},auto=False,W=120,H=120,margin=5,gap=3,budget=2)
    assert results and all(_validate(l) for l in results)
    assert results[0]['sheet_mm2']==120*120
    assert _score(results[0],False)[0]==1


def test_concave_parts_and_holes():
    lshape=Polygon([(0,0),(90,0),(90,20),(20,20),(20,90),(0,90)])
    donut=Polygon(box(0,0,100,100).exterior.coords,[box(30,30,70,70).exterior.coords])
    pieces=[CutPiece('L',lshape,'0'),CutPiece('Square',box(0,0,25,25),'0'),CutPiece('Frame',donut,'0')]
    out=optimize(pieces,{'L':1,'Square':1,'Frame':1},auto=False,W=270,H=230,margin=3,gap=1,budget=2)
    assert out and _validate(out[0])
    assert out[0]['items']==3


def test_dxf_roundtrip_and_editor_noop():
    layout=optimize(two_parts(),{'A':1,'B':1},auto=True,margin=5,gap=3,budget=2)[0]
    data=export_layout_dxf(layout)
    assert b'PART_S001_' in data
    read=parse_sheet_dxf(data,'roundtrip.dxf',units='mm')
    assert len(read.pieces)==2, read.issues
    edits={'fingerprint':layout_fingerprint(layout),'placements':[
        {'index':i,'x':p['x'],'y':p['y'],'angle':p['angle'],'sheet_index':p.get('sheet_index',0)}
        for i,p in enumerate(layout['placements'])]}
    updated=update_layout(layout,edits)
    assert _validate(updated) and abs(updated['net_mm2']-layout['net_mm2'])<.001
    html=editor_html(layout,lang='ru')
    assert 'svg' in html.lower()


def test_leftovers_and_trilingual_interfaces():
    layout=optimize(two_parts(),{'A':1,'B':1},auto=False,W=400,H=400,margin=5,gap=3,budget=2)[0]
    leftovers=reusable_offcuts(layout,threshold_pct=5,count=3,kerf_mm=1,min_neck_mm=2)
    assert 0<=len(leftovers)<=3
    for offcut in leftovers:
        assert offcut['area_pct']>=5
    output=svg_sheet(layout,highlight_offcut='irregular',irregular_candidates=leftovers)
    assert '<svg' in output
    assert len(LANGUAGES)==3
    for language in LANGUAGES.values():
        assert t('tab_editor',language)
