"""v1.6 regression: directed contact compaction and mixed-geometry remnants."""
from shapely.geometry import box
from shapely.ops import unary_union
from nesting_advanced import _summarize
from nesting_v16 import _contact_slide,directional_refine
from nesting_refinement import _valid
from remnant_shapes import freeform_metrics,select_mixed_remnants
from optimizer_v15 import _validate,_summary
from svg_preview import svg_sheet
from dxf_export import export_layout_dxf
from sheet_nesting import parse_sheet_dxf


def two_parts():
    rows=[{'name':'big','shape':box(5,5,35,35),'x':5,'y':5,'angle':0,'sheet_index':0},
          {'name':'red','shape':box(120,10,135,25),'x':120,'y':10,'angle':0,'sheet_index':0}]
    return _summarize(rows,[(140,85)],5,3,'test')


def test_red_part_is_pushed_left_to_first_legal_contact():
    lay=two_parts();stock=box(5,5,135,80)
    old=lay['placements']
    moved=_contact_slide(old[1]['shape'],[old[0]],stock,3,-1,0)
    assert 37.99<moved.bounds[0]<38.01
    assert _valid(moved,[old[0]],3,stock)
    improved=directional_refine(lay,auto=False,goal='compact',rounds=1)
    assert _validate(improved)
    assert improved['placements'][1]['shape'].bounds[0]<120


def test_multiple_offcut_types_have_no_overlap_and_fit():
    lay=two_parts();f=freeform_metrics(lay,grid=9)
    assert f['mixed_total_mm2']>0
    sh=f['free_sheets'][0];all_shapes=f['mixed'][0]
    for a in all_shapes:
        assert a['kind'] in ('rectangle','circle')
        assert sh['free_geometry'].buffer(1e-6).covers(a['geometry'])
    for i,a in enumerate(all_shapes):
        for b in all_shapes[:i]:
            assert a['geometry'].intersection(b['geometry']).area<1e-5


def test_freeform_mode_draws_and_exports_clean_dxf():
    lay=two_parts()
    svg=svg_sheet(lay,highlight_offcut='mixed')
    assert '<svg' in svg and ('R1:' in svg or 'C1:' in svg)
    scan=parse_sheet_dxf(export_layout_dxf(lay),units='auto')
    assert len(scan.pieces)==2
    m=_summary(lay,grid=11)
    assert 'mixed_shapes_total_mm2' in m


def test_circle_can_be_recognized_in_open_free_space():
    lay=_summarize([{'name':'block','shape':box(5,5,25,25),'x':5,'y':5,'angle':0,'sheet_index':0}],
                   [(120,100)],5,3,'test')
    shapes=select_mixed_remnants(freeform_metrics(lay,grid=8)['free_sheets'][0]['free_geometry'],grid=8)
    assert shapes
