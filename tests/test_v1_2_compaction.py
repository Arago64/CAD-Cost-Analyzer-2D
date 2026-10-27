"""v1.2: realistic 7-part geometry, alternative objectives and DXF roundtrip."""
from pathlib import Path
import pytest
from shapely.geometry import Point,box
from sheet_nesting import parse_sheet_dxf, CutPiece
from nesting_advanced import advanced_nest
from remnant_optimization import remnant_quality
from nesting_refinement import _free_connected
from toolpath2d import CuttingParameters,plan_toolpaths
from dxf_export import export_layout_dxf

EX=Path(__file__).resolve().parents[1]/'examples'


def verify_layout(out,gap):
    for i,p in enumerate(out['placements']):
        sh=out['sheets'][p['sheet_index']]
        a,b,c,d=p['shape'].bounds
        assert a>=out['margin_mm']-1e-5 and b>=out['margin_mm']-1e-5
        assert c<=sh['width_mm']-out['margin_mm']+1e-5
        assert d<=sh['height_mm']-out['margin_mm']+1e-5
        for other in out['placements'][:i]:
            if other['sheet_index'] != p['sheet_index']:continue
            assert p['shape'].intersection(other['shape']).area<1e-5
            assert p['shape'].distance(other['shape'])>=gap-1e-5


def test_sample_seven_parts_compact_auto_and_export_roundtrip():
    parts=parse_sheet_dxf((EX/'DEMO_7_PARTS_COMPACT_V12.dxf').read_bytes()).pieces
    assert len(parts)==7
    out=advanced_nest(parts,auto=True,quality='balanced',margin=5,gap=3,
                      preserve_remnants=True)
    verify_layout(out,3)
    assert out['sheet_count']==1
    assert out['sheet_mm2']<=151980  # v1.1 baseline on same synthetic demo
    assert remnant_quality(out)[0]['area_mm2']>=8000
    # Reimporting must recover all parts and any true internal holes.
    scan=parse_sheet_dxf(export_layout_dxf(out),filename='roundtrip.dxf',units='auto')
    assert len(scan.pieces)==len(parts)
    plan=plan_toolpaths(out,CuttingParameters(kerf_mm=.3))
    assert len(plan['paths'])>=len(parts)


def test_offcut_toggle_has_measurable_effect_with_fixed_stock():
    parts=[CutPiece('plate',box(0,0,55,35),'L'),
           CutPiece('circle',Point(0,0).buffer(6,quad_segs=20),'L')]
    common=dict(stock_width=120,stock_height=100,auto=False,quality='balanced',gap=3,margin=3)
    a=advanced_nest(parts,preserve_remnants=False,**common)
    b=advanced_nest(parts,preserve_remnants=True,**common)
    verify_layout(a,3);verify_layout(b,3)
    assert a['sheet_count']==b['sheet_count']==1
    assert remnant_quality(b)[0]['area_mm2']>=remnant_quality(a)[0]['area_mm2']
    # A meaningful toggle should change the optimizer's goal, not just its label.
    assert a['algorithm']!=b['algorithm']
    assert _free_connected(b['placements'],120,100,3,3)[0]>0


def test_large_batch_fast_still_single_sheet():
    parts=parse_sheet_dxf((EX/'PLATTE_A.dxf').read_bytes()).pieces
    out=advanced_nest(parts,{parts[0].name:40},stock_width=1250,stock_height=2500,
                      auto=False,quality='fast',preserve_remnants=True)
    assert out['items']==40 and out['sheet_count']==1
    verify_layout(out,3)


def test_precise_five_parts_preserves_15_degree_search():
    parts=[CutPiece(f'rect_{i}',box(0,0,20+2*i,9+2*i),'0') for i in range(5)]
    out=advanced_nest(parts,auto=False,stock_width=220,stock_height=120,
                      quality='precision',preserve_remnants=False)
    assert '24 angles' in out['algorithm']
    verify_layout(out,3)
