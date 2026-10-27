"""Regression for v1.0 geometry, inventory, shared-cut and XY simulation."""
import json
import pytest
from shapely.geometry import box,Polygon
from sheet_nesting import CutPiece
from nesting_advanced import advanced_nest
from toolpath2d import CuttingParameters,plan_toolpaths
from layout_editor import update_layout,editor_html,layout_fingerprint
from remnants import find_remnants,remnants_json,import_remnant,remnants_dxf
from common_line import common_line_candidates
from simulation import audit_plan,simulation_html
from dxf_export import export_layout_dxf
from sheet_nesting import parse_sheet_dxf


def mock_layout():
    parts=[{'name':'A #1','shape':box(3,3,23,23),'x':3,'y':3,'angle':0,'sheet_index':0},
           {'name':'B #1','shape':box(30,3,40,13),'x':30,'y':3,'angle':0,'sheet_index':0}]
    return {'placements':parts,'sheets':[{'index':0,'width_mm':100,'height_mm':50}],
            'margin_mm':2.,'gap_mm':3.,'sheet_width_mm':100,'sheet_height_mm':50}


def test_validated_editor_roundtrip_and_overlap_rejection():
    lay=mock_layout()
    changes=[{'index':0,'x':7,'y':8,'angle':90},
             {'index':1,'x':40,'y':6,'angle':0}]
    result=update_layout(lay,changes)
    assert result['placements'][0]['shape'].bounds[0]==pytest.approx(7)
    assert result['placements'][0]['shape'].bounds[1]==pytest.approx(8)
    assert result['items']==2
    assert b'PART_S' in export_layout_dxf(result)
    with pytest.raises(ValueError,match='overlaps'):
        update_layout(lay,[{'index':0,'x':5,'y':5,'angle':0},
                          {'index':1,'x':15,'y':6,'angle':0}])
    with pytest.raises(ValueError):
        update_layout(lay,[{'index':0,'x':100,'y':5,'angle':0},
                          {'index':1,'x':40,'y':6,'angle':0}])
    assert 'layout_edits.json' in editor_html(lay)


def test_remnants_export_and_reuse():
    lay=mock_layout()
    res=find_remnants(lay,kerf_mm=.3,min_area_mm2=100)
    assert res and res[0]['polygon'].area<5000
    inv=remnants_json(res,material='S235',thickness_mm=3)
    poly,metadata=import_remnant(inv,0)
    assert poly.is_valid and poly.area>0
    assert metadata['material']=='S235'
    assert len(remnants_dxf(res))>100
    p=CutPiece(name='tiny',polygon=box(0,0,2,2),layer='A')
    packed=advanced_nest([p],{'tiny':1},stock_width=poly.bounds[2],stock_height=poly.bounds[3],
                         margin=1,gap=.5,auto=False,quality='fast',stock_outline=poly)
    assert packed['placements'][0]['shape'].buffer(1e-4).within(poly.buffer(.001))


def test_commonline_is_opt_in_candidate_not_fictitious_saving():
    lay=mock_layout()
    lay['placements'][1]['shape']=box(23.3,3,33.3,13)
    lay['placements'][1]['x']=23.3
    candidates=common_line_candidates(lay,kerf_mm=.3,tolerance_mm=.01,min_length_mm=2)
    assert candidates
    assert any(abs(c['length_mm']-10)<.05 for c in candidates)
    assert all(c['status']=='candidate_only_unverified' for c in candidates)
    assert not common_line_candidates(lay,kerf_mm=2)


def test_route_opt_and_xy_audit_animation():
    lay=mock_layout()
    params=CuttingParameters(kerf_mm=.3,lead_mm=1)
    base=plan_toolpaths(lay,params,route_optimization=False)
    opt=plan_toolpaths(lay,params,route_optimization=True)
    assert len(opt['paths'])==len(base['paths'])==2
    assert opt['route_optimization']
    assert opt['paths'][0]['xy']
    audit=audit_plan(lay,opt,.3)
    assert isinstance(audit['problems'],list)
    html=simulation_html(lay,opt,lang='ru')
    assert 'range' in html and 'setInterval' in html


def test_nested_precedence_hole_first():
    outer=Polygon([(0,0),(60,0),(60,60),(0,60)],holes=[[(15,15),(45,15),(45,45),(15,45)]])
    lay={'placements':[{'name':'ring','shape':outer,'x':0,'y':0,'angle':0,'sheet_index':0},
                       {'name':'insert','shape':box(25,25,35,35),'x':25,'y':25,'angle':0,
                        'sheet_index':0,'nested_in':'ring','nest_type':'hole'}],
         'sheets':[{'index':0,'width_mm':80,'height_mm':80}], 'margin_mm':0,'gap_mm':2}
    p=plan_toolpaths(lay,CuttingParameters(kerf_mm=.2),route_optimization=True)
    assert [x['part'] for x in p['paths']][0]=='insert'
    assert [x['type'] for x in p['paths'] if x['part']=='ring']==['hole','outer']
    a=audit_plan(lay,p,.2)
    assert not any(x['kind']=='NESTED_RELEASE_ORDER' for x in a['problems'])


def test_editor_source_fingerprint_rejects_unrelated_document():
    layout=mock_layout()
    good={'fingerprint':layout_fingerprint(layout),'placements':[
          {'index':0,'x':3,'y':3,'angle':0},
          {'index':1,'x':30,'y':3,'angle':0}]}
    assert update_layout(layout,good)['items']==2
    good['fingerprint']='unrelated-job'
    with pytest.raises(ValueError,match='layout has changed'):
        update_layout(layout,good)


def test_remnant_area_used_for_cost_after_editor():
    lay=mock_layout()
    region=box(0,0,100,50).difference(box(40,20,50,35))
    lay['stock_outline']=region
    lay['placements']=[{'name':'A','shape':box(5,5,20,20),
                         'x':5,'y':5,'angle':0,'sheet_index':0}]
    upd=update_layout(lay,[{'index':0,'x':5,'y':5,'angle':0}])
    assert upd['sheet_mm2']==pytest.approx(region.area)
    assert 'SHEET_BOUNDARY' in export_layout_dxf(upd).decode()
