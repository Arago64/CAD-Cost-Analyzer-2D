from pathlib import Path
import io,zipfile
import pytest
from shapely.geometry import box
from nesting_advanced import _summarize,advanced_nest
from remnant_portfolio import remnant_metrics
from optimizer_v15 import global_moves, variant_pool,_validate,reoptimize
from variant_storage import archive_variants,load_variants
from layout_editor import update_layout,layout_fingerprint
from sheet_nesting import parse_sheet_dxf
from svg_preview import svg_sheet


def _simple():
    rows=[{'name':'A','shape':box(5,5,45,45),'x':5,'y':5,'angle':0,'sheet_index':0},
          {'name':'B','shape':box(48,5,70,25),'x':48,'y':5,'angle':0,'sheet_index':0}]
    return _summarize(rows,[(180,100)],5,3,'test')


def test_two_nonoverlapping_rectangles_are_more_valuable_than_one():
    placed=[{'name':'bar','shape':box(100,0,110,60),'x':100,'y':0,'angle':0,'sheet_index':0}]
    lay=_summarize(placed,[(210,60)],0,0,'test')
    result=remnant_metrics(lay,grid=18,max_rectangles=3)[0]
    assert result['rectangle_count']>=2
    assert result['combined_rectangles_mm2']>=result['largest_rectangle_mm2']*1.9
    rects=result['rectangles']
    for i,r in enumerate(rects):
        assert result['free_geometry'].buffer(1e-6).covers(r)
        for s in rects[:i]:assert r.intersection(s).area<.001


def test_all_part_rigid_rotation_preserves_gaps_and_original_option():
    lay=_simple()
    moves=global_moves(lay,auto=False)
    assert moves
    assert all(_validate(x) for x in moves)
    assert any(x['placements'][0]['angle']==90 for x in moves)
    for x in moves:
        assert x['placements'][0]['shape'].area==lay['placements'][0]['shape'].area
        assert x['placements'][1]['shape'].area==lay['placements'][1]['shape'].area


def test_manual_editor_and_portfolio_roundtrip():
    base=_simple()
    variants=variant_pool([('seed',base)],auto=False,quality='balanced',search=True)
    assert len(variants)>=2
    edits={'fingerprint':layout_fingerprint(base),'placements':[
        {'index':0,'x':7,'y':10,'angle':0,'sheet_index':0},
        {'index':1,'x':74,'y':5,'angle':90,'sheet_index':0}]}
    manual=update_layout(base,edits)
    assert _validate(manual)
    variants.append({'id':'man','name':'Manual test','layout':manual,
                     'metrics':variants[0]['metrics'],'origin':'manual'})
    data=archive_variants(variants,'fingerprint_123')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len([f for f in z.namelist() if f.endswith('.dxf')])==len(variants)
    restored=load_variants(data,base,'fingerprint_123')
    assert len(restored)==len(variants)
    assert all(_validate(x['layout']) for x in restored)
    assert restored[-1]['layout']['placements'][1]['angle']==90
    with pytest.raises(ValueError):load_variants(data,base,'wrong_project')


def test_v15_refinement_keeps_selected_and_can_change_group():
    lay=_simple()
    pool=reoptimize(lay,auto=False,quality='balanced',max_trials=1)
    assert pool and pool[0] is lay
    assert all(_validate(x) for x in pool)


def test_svg_displays_multiple_rectangles():
    placed=[{'name':'bar','shape':box(100,0,110,60),'x':100,'y':0,'angle':0,'sheet_index':0}]
    lay=_summarize(placed,[(210,60)],0,0,'test')
    picture=svg_sheet(lay,None,0,highlight_offcut=True)
    assert 'R1:' in picture and 'R2:' in picture
