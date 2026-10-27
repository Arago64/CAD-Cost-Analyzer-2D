"""Regressions for keyboard-independent rotation, nested ordering, offcut scoring."""
import json
import pytest
from shapely import affinity
from shapely.geometry import Polygon, Point, box
from nesting_advanced import advanced_nest, _summarize
from remnant_optimization import largest_free_rectangle,remnant_quality
from layout_editor import editor_html,update_layout
from toolpath2d import CuttingParameters, plan_toolpaths
from sheet_nesting import CutPiece
from simulation import audit_plan


def test_editor_rotation_has_buttons_and_backend_uses_same_bounding_box_center():
    triangle=Polygon([(5,5),(35,5),(5,15)])
    row={'name':'triangle','shape':triangle,'x':5,'y':5,'angle':0,'sheet_index':0}
    lay=_summarize([row],[(80,80)],2,2,'test')
    html=editor_html(lay,lang='ru')
    for key in ('rotate-right','rotate-left','rotate-90','angle-input','svg.focus()'):
        assert key in html
    amended=update_layout(lay,[{'index':0,'x':10,'y':10,'angle':90,'sheet_index':0}])
    target=affinity.rotate(triangle,90,origin='center')
    target=affinity.translate(target,10-target.bounds[0],10-target.bounds[1])
    assert amended['placements'][0]['shape'].equals_exact(target,1e-6)


def test_deep_nesting_all_children_before_host_hole_and_outer_even_with_nearest_routing():
    host=Polygon([(0,0),(80,0),(80,80),(0,80)],
                 holes=[[(10,10),(70,10),(70,70),(10,70)]])
    placements=[{'name':'host','shape':affinity.translate(host,5,5),'angle':0,'sheet_index':0},
                {'name':'insert','shape':box(20,20,35,35),'angle':0,'sheet_index':0},
                {'name':'insert','shape':box(43,43,56,56),'angle':0,'sheet_index':0}]
    lay=_summarize(placements,[(95,95)],2,2,'test')
    assert [p.get('nested_in_index') for p in lay['placements']]==[None,0,0]
    for optimize in (False,True):
        plan=plan_toolpaths(lay,CuttingParameters(kerf_mm=.3),route_optimization=optimize)
        steps=[(x['part_index'],x['type']) for x in plan['paths']]
        assert [idx for idx,typ in steps[:2]]==[1,2] or [idx for idx,typ in steps[:2]]==[2,1]
        assert steps[-2:]==[(0,'hole'),(0,'outer')]
        audit=audit_plan(lay,plan,.3)
        assert not any(r['kind']=='NESTED_RELEASE_ORDER' for r in audit['problems'])


def test_remnant_metric_guarantees_clear_rectangle_and_prefers_larger_offcut():
    p=[{'shape':box(3,3,15,15)}, {'shape':box(18,3,73,38)}]
    rect,area=largest_free_rectangle(p,120,100,margin=3,clearance=1.5)
    assert rect is not None and area>6000
    for part in p:
        assert rect.distance(part['shape'])>=1.5-1e-5
    objects=[CutPiece(name='plate',polygon=box(0,0,55,35),layer='L'),
             CutPiece(name='circle',polygon=Point(0,0).buffer(6),layer='L')]
    base=advanced_nest(objects,stock_width=120,stock_height=100,
                       auto=False,margin=3,gap=3,quality='balanced',preserve_remnants=False)
    improved=advanced_nest(objects,stock_width=120,stock_height=100,
                       auto=False,margin=3,gap=3,quality='balanced',preserve_remnants=True)
    assert improved['sheet_count']==base['sheet_count']==1
    assert remnant_quality(improved)[0]['area_mm2']>remnant_quality(base)[0]['area_mm2']
    assert remnant_quality(improved)[0]['area_mm2']==pytest.approx(7285,abs=1)


def test_remnant_rectangle_not_offered_for_irregular_stock():
    l=_summarize([{'name':'tiny','shape':box(5,5,10,10),'angle':0,'sheet_index':0}],
                 [(50,50)],2,2,'test')
    l['stock_outline']=box(0,0,50,50).difference(box(20,0,30,50))
    assert remnant_quality(l)==[]


def test_duplicate_labels_reference_correct_host_by_index():
    ring=Polygon([(0,0),(35,0),(35,35),(0,35)],holes=[[(8,8),(27,8),(27,27),(8,27)]])
    lay=_summarize([
        {'name':'frame','shape':affinity.translate(ring,3,3),'sheet_index':0},
        {'name':'frame','shape':affinity.translate(ring,50,3),'sheet_index':0},
        {'name':'inner','shape':box(63,16,70,23),'sheet_index':0},
    ],[(95,45)],2,2,'test')
    assert lay['placements'][2]['nested_in_index']==1
    paths=plan_toolpaths(lay,CuttingParameters(kerf_mm=.2),route_optimization=False)['paths']
    childlast=max(t['order'] for t in paths if t['part_index']==2)
    secondhostfirst=min(t['order'] for t in paths if t['part_index']==1)
    assert childlast<secondhostfirst
