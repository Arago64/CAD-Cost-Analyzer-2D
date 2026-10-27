import io,json,math,zipfile
from pathlib import Path
import ezdxf
import pytest
from shapely.geometry import Polygon
from sheet_nesting import parse_sheet_dxf,CutPiece
from nesting_advanced import advanced_nest,compare_sheet_formats
from toolpath2d import plan_toolpaths,CuttingParameters,time_estimate
from process_cost import cost_quote,default_params,suitability
from reporting import build_export,tables
from svg_preview import svg_sheet

EX=Path(__file__).resolve().parents[1]/'examples'


def one(name,coords,holes=None):
    return CutPiece(name,Polygon(coords,holes=holes),layer='0',holes=len(holes or []))


def test_demo_dxf_and_angles_no_collisions():
    doc=parse_sheet_dxf((EX/'DEMO_2D_ZWEI_QUADRATE_KREIS.dxf').read_bytes())
    assert len(doc.pieces)==3
    for quality in ('fast','balanced','thorough'):
        layout=advanced_nest(doc.pieces,quality=quality)
        assert layout['sheet_count']==1
        assert 0<layout['utilization']<=100
        for i,placement in enumerate(layout['placements']):
            for other in layout['placements'][i+1:]:
                assert placement['shape'].distance(other['shape'])>=2.999
                assert placement['shape'].intersection(other['shape']).area<1e-6


def test_fixed_multi_sheet_and_qty():
    part=one('square',[(0,0),(50,0),(50,50),(0,50)])
    out=advanced_nest([part],{'square':3},stock_width=70,stock_height=70,auto=False,quality='balanced')
    assert out['sheet_count']==3
    assert out['items']==3
    assert out['sheet_mm2']==3*70*70
    with pytest.raises(ValueError):
        advanced_nest([part],{'square':3},stock_width=70,stock_height=70,auto=False,multi_sheet=False)


def test_holes_before_exterior_and_centerline_offset():
    big=one('ring',[(0,0),(100,0),(100,100),(0,100)],
            holes=[[(30,30),(70,30),(70,70),(30,70)]])
    layout=advanced_nest([big],stock_width=130,stock_height=130,auto=False,margin=10)
    params=CuttingParameters(method='CNC Konturfräsen',kerf_mm=6,lead_mm=3,
                  feed_mm_min=500,rapid_mm_min=5000,stepdown_mm=2)
    route=plan_toolpaths(layout,params)
    assert [x['type'] for x in route['paths']]==['hole','outer']
    assert route['contours']==2
    assert route['pierces']==2
    assert route['paths'][0]['cut_length_mm']<160
    assert route['paths'][1]['cut_length_mm']>400
    summary=time_estimate(route,params,5)
    assert summary['passes']==3
    assert summary['cut_min']==pytest.approx(route['effective_cut_mm']*3/500)


def test_narrow_gap_warned_for_mill():
    sq=one('sq',[(0,0),(20,0),(20,20),(0,20)])
    layout=advanced_nest([sq],{'sq':2},stock_width=60,stock_height=40,auto=False,
                         gap=2,margin=5,preserve_remnants=False)
    p=plan_toolpaths(layout,CuttingParameters(method='CNC Konturfräsen',kerf_mm=6,lead_mm=2))
    assert any('Teileabstand kleiner' in s for s in p['warnings'])


def test_real_autocad_binary_dxf():
    result=parse_sheet_dxf((EX/'REGRESSION_AutoCAD_2018_Binary_Chunks.dxf').read_bytes())
    assert len(result.pieces)==1
    assert result.pieces[0].area_mm2==pytest.approx(math.pi*4,rel=1e-3)


def test_loose_arc_and_lines_can_close_a_loop():
    d=ezdxf.new('R2018'); d.header['$INSUNITS']=4;m=d.modelspace()
    m.add_arc(center=(0,0),radius=10,start_angle=0,end_angle=180)
    m.add_line((-10,0),(10,0))
    b=io.StringIO();d.write(b)
    result=parse_sheet_dxf(b.getvalue().encode(),filename='half_circle.dxf')
    assert len(result.pieces)==1
    assert result.pieces[0].area_mm2==pytest.approx(math.pi*100/2,rel=.005)


def test_quote_and_export():
    sq=one('sq',[(0,0),(20,0),(20,20),(0,20)])
    layout=advanced_nest([sq],stock_width=100,stock_height=100,auto=False)
    params=default_params('Laser')
    route=plan_toolpaths(layout,params)
    q=cost_quote(layout,route,params,thickness_mm=3,material='S235JR',eur_kg=2,
        hourly_eur=50,consumables_eur_h=7,setup_min=10)
    assert q['sheet_kg']>q['parts_kg']>0
    assert q['material_eur']>0
    assert q['offer_eur']>q['manufacturing_eur']>q['material_eur']
    blob=build_export(layout,route,q,{'name':'test'})
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert 'Werkzeugwege_2D.csv' in z.namelist()
        assert 'Wegplanung_Blech_1.svg' in z.namelist()
        report=json.loads(z.read('Ergebnis_2D.json'))
        assert len(report['paths'])==1
    assert '<svg' in svg_sheet(layout,route)


def test_screen_material_limits():
    assert suitability('Autogen','S235JR',10)[0]
    assert not suitability('Autogen','Aluminium EN AW-6082',10)[0]
    assert suitability('Wasserstrahl','Edelstahl 1.4404',5)[0]


def test_large_batch_fits_single_standard_sheet():
    example=parse_sheet_dxf((EX/'PLATTE_A.dxf').read_bytes()).pieces
    layout=advanced_nest(example,{example[0].name:40},auto=False,
                         stock_width=1250,stock_height=2500,quality='fast')
    assert layout['sheet_count']==1
    assert layout['items']==40


def test_unmachinable_small_hole_marks_incomplete():
    part=one('micro',[(0,0),(20,0),(20,20),(0,20)],
             holes=[[(8,8),(12,8),(12,12),(8,12)]])
    layout=advanced_nest([part],stock_width=40,stock_height=40,auto=False)
    plan=plan_toolpaths(layout,CuttingParameters(method='CNC Konturfräsen',kerf_mm=6))
    assert not plan['complete']
    assert plan['skipped_contours']==1
    assert any('unvollständig' in s for s in plan['warnings'])


def _verify_clearance(layout,gap):
    for i,p in enumerate(layout['placements']):
        sid=p.get('sheet_index',0)
        sheet=layout['sheets'][sid]
        a,b,c,d=p['shape'].bounds
        assert a>=layout['margin_mm']-1e-6
        assert b>=layout['margin_mm']-1e-6
        assert c<=sheet['width_mm']-layout['margin_mm']+1e-6
        assert d<=sheet['height_mm']-layout['margin_mm']+1e-6
        for q in layout['placements'][i+1:]:
            if sid!=q.get('sheet_index',0):continue
            assert p['shape'].intersection(q['shape']).area<1e-7
            assert p['shape'].distance(q['shape'])>=gap-1e-6


def test_internal_hole_reuses_cutout_instead_of_new_sheet():
    ring=one('ring',[(0,0),(120,0),(120,120),(0,120)],
             holes=[[(20,20),(100,20),(100,100),(20,100)]])
    square=one('small',[(0,0),(35,0),(35,35),(0,35)])
    ordinary=advanced_nest([ring,square],auto=False,stock_width=130,stock_height=130,
                           gap=3,margin=5,hole_nesting=False)
    nested=advanced_nest([ring,square],auto=False,stock_width=130,stock_height=130,
                         gap=3,margin=5,hole_nesting=True)
    assert ordinary['sheet_count']==2
    assert nested['sheet_count']==1
    assert nested['nested_items']==1
    assert next(p for p in nested['placements'] if p['name']=='small #1')['nest_type']=='hole'
    _verify_clearance(nested,3)
    route=plan_toolpaths(nested,default_params('Laser'))
    indices={p['part']:p['order'] for p in route['paths'] if p['type']=='outer'}
    assert indices['small #1']<indices['ring #1']
    assert 'Verschachtelte Teile' in '\n'.join(route['warnings'])


def test_open_concave_pocket_c_shape_reuses_free_space():
    # C profile: pocket opens to the left, so it is NOT an internal hole.
    c=one('C',[(0,0),(100,0),(100,100),(0,100),(0,78),
               (75,78),(75,22),(0,22)])
    small=one('small',[(0,0),(35,0),(35,35),(0,35)])
    without=advanced_nest([small,c],auto=False,stock_width=110,stock_height=110,
                          margin=5,gap=3,hole_nesting=False)
    with_pockets=advanced_nest([small,c],auto=False,stock_width=110,stock_height=110,
                              margin=5,gap=3,hole_nesting=True)
    assert without['sheet_count']==2
    assert with_pockets['sheet_count']==1
    assert with_pockets['nested_items']==1
    assert next(p for p in with_pockets['placements'] if p['name']=='small #1')['nest_type']=='recess'
    _verify_clearance(with_pockets,3)
    route=plan_toolpaths(with_pockets,default_params('Laser'))
    indices={p['part']:p['order'] for p in route['paths'] if p['type']=='outer'}
    assert indices['small #1']<indices['C #1']


def test_too_small_pocket_rejected_despite_improved_packing():
    ring=one('tight',[(0,0),(120,0),(120,120),(0,120)],
             holes=[[(20,20),(100,20),(100,100),(20,100)]])
    square=one('large',[(0,0),(77,0),(77,77),(0,77)])
    layout=advanced_nest([ring,square],auto=False,stock_width=130,stock_height=130,
                         margin=5,gap=3,hole_nesting=True)
    assert layout['sheet_count']==2
    _verify_clearance(layout,3)


def test_many_parts_hole_nesting_not_silently_switched_off():
    # More than 12 parts used to turn nesting OFF entirely.
    ring=one('ring',[(0,0),(120,0),(120,120),(0,120)],
             holes=[[(20,20),(100,20),(100,100),(20,100)]])
    small=one('small',[(0,0),(16,0),(16,16),(0,16)])
    layout=advanced_nest([ring,small],{'ring':1,'small':13},auto=False,
                         stock_width=160,stock_height=130,margin=5,gap=3,
                         quality='fast',hole_nesting=True)
    assert layout['sheet_count']>=1
    assert layout['nested_items']>=1
    _verify_clearance(layout,3)
