"""v2.3 regression tests for compound offcuts, precise editor and hard time limit."""
from __future__ import annotations
from pathlib import Path
from time import monotonic
from shapely.geometry import box, Point
from shapely.ops import unary_union
from nesting_advanced import _summarize
from irregular_remnants import irregular_remnants, decompose_freeform, irregular_remnants_dxf
from layout_editor import editor_html,update_layout,layout_fingerprint
from bounded_search import run_bounded_search
from sheet_nesting import parse_sheet_dxf
from optimizer_v15 import _validate
from dxf_export import export_layout_dxf


def test_compound_l_remnant_is_single_exportable_shape_with_multiple_components():
    # A single L-shaped remnant composed of TWO rectangles, not two independent
    # saved inventory items. Shape union, no double counting.
    metal=box(0,0,80,25).union(box(0,25,24,80))
    regions=decompose_freeform(metal)
    assert len(regions)>=2
    assembled=unary_union(regions)
    assert metal.symmetric_difference(assembled).area<1e-6
    occupied=box(24,25,80,80)
    layout=_summarize([{'name':'metal part','shape':occupied,'x':24.,'y':25.,'angle':0.,'sheet_index':0}],
                      [(80,80)],0.,0.,'L-remnant')
    remnants=irregular_remnants(layout,kerf_mm=0,min_neck_mm=0,min_area_mm2=100)
    assert len(remnants)==1
    assert remnants[0]['compound'] is True
    assert remnants[0]['component_count']>=2
    assert abs(remnants[0]['area_mm2']-metal.area)<.001
    assert len(parse_sheet_dxf(irregular_remnants_dxf(remnants)).pieces)>=1


def test_circle_rectangle_blended_compound_keeps_curved_edge():
    free=unary_union([box(0,0,30,35),Point(30,17.5).buffer(17.5,quad_segs=18)])
    parts=decompose_freeform(free)
    assert len(parts)>1
    assert free.symmetric_difference(unary_union(parts)).area<1e-5


def test_editor_submillimeter_coordinates_and_rotation_are_precisely_validated():
    rows=[{'name':'A','shape':box(6,5,26,20),'angle':0,'sheet_index':0,'x':6,'y':5}]
    lay=_summarize(rows,[(85,80)],3,2,'test')
    html=editor_html(lay)
    assert '0.01 mm' in html and 'position-step' in html
    changed=update_layout(lay,{'fingerprint':layout_fingerprint(lay),
        'placements':[{'index':0,'x':8.125,'y':7.375,'angle':17.5,'sheet_index':0}]})
    assert _validate(changed)
    assert abs(changed['placements'][0]['shape'].bounds[0]-8.125)<1e-7
    assert abs(changed['placements'][0]['shape'].bounds[1]-7.375)<1e-7
    assert abs(changed['placements'][0]['shape'].area-lay['placements'][0]['shape'].area)<1e-7


def test_entire_search_budget_bounded_with_valid_checkpoint():
    root=Path(__file__).resolve().parent.parent
    dxf=(root/'examples'/'DEMO_2D_ZWEI_QUADRATE_KREIS.dxf').read_bytes()
    pieces=parse_sheet_dxf(dxf).pieces
    payload=dict(pieces=pieces,qty={p.name:1 for p in pieces},W=None,H=None,
                 margin=5.,gap=3.,auto=True,multi_sheet=False,stock_outline=None,
                 hole_nesting=True,quality='balanced',engine2=True,search_mode='cooperative')
    started=monotonic()
    variants,info=run_bounded_search(payload,6)
    wall=monotonic()-started
    assert wall<10, wall
    assert variants
    for item in variants:
        layout=item['layout']
        assert _validate(layout)
        assert len(parse_sheet_dxf(export_layout_dxf(layout)).pieces)==len(pieces)
    assert info['elapsed_seconds']<10
