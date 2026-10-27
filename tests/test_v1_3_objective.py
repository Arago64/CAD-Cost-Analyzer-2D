"""Integration regression: offcut checkbox must change a real search, not labels."""
from pathlib import Path
from shapely.geometry import box
from sheet_nesting import parse_sheet_dxf
from nesting_advanced import advanced_nest
from remnant_optimization import remnant_quality
from svg_preview import svg_sheet
from dxf_export import export_layout_dxf

EX=Path(__file__).resolve().parents[1]/'examples'

def test_objective_uses_real_alternatives_and_distinct_placements():
    parts=parse_sheet_dxf((EX/'DEMO_7_PARTS_COMPACT_V12.dxf').read_bytes()).pieces
    base=dict(auto=True,quality='balanced',margin=5.,gap=3.)
    small=advanced_nest(parts,preserve_remnants=False,**base)
    scrap=advanced_nest(parts,preserve_remnants=True,**base)
    assert small['search_objective']=='minimum_sheet_area'
    assert scrap['search_objective']=='maximize_reusable_offcut'
    assert small['evaluated_alternatives']>=3 and scrap['evaluated_alternatives']>=3
    assert small['sheet_mm2']<=scrap['sheet_mm2']
    assert scrap['sheet_mm2']<=1.035*small['sheet_mm2']+1e-6
    a=remnant_quality(small)[0]['area_mm2']
    b=remnant_quality(scrap)[0]['area_mm2']
    assert b>a*1.5, (small['sheet_mm2'],scrap['sheet_mm2'],a,b)
    for layout in (small,scrap):
        bounds=box(5.,5.,layout['sheet_width_mm']-5,layout['sheet_height_mm']-5)
        for i,p in enumerate(layout['placements']):
            assert bounds.covers(p['shape'])
            for q in layout['placements'][:i]:
                assert p['shape'].intersection(q['shape']).area<1e-5
                assert p['shape'].distance(q['shape'])>=3.-1e-5
        svg=svg_sheet(layout,show_paths=False,highlight_offcut=True)
        assert svg.count('<svg')==1 and 'stroke-dasharray=' in svg
        restored=parse_sheet_dxf(export_layout_dxf(layout),units='auto')
        assert len(restored.pieces)==len(parts)


def test_fixed_sheet_still_respects_stock_dimensions():
    parts=parse_sheet_dxf((EX/'DEMO_7_PARTS_COMPACT_V12.dxf').read_bytes()).pieces
    result=advanced_nest(parts,auto=False,stock_width=1000,stock_height=2000,
                         quality='balanced',preserve_remnants=True)
    assert result['sheet_count']==1
    assert result['search_objective']=='maximize_reusable_offcut'
    assert result['sheet_mm2']==2_000_000
