"""Regression tests for independent global-search core. No Streamlit needed."""
from shapely.geometry import box,Point,Polygon
from nesting_advanced import _summarize
from nesting_engine2 import search_engine2,_validate,_compact_score,_polish
from optimizer_v15 import variant_pool
from sheet_nesting import parse_sheet_dxf
from dxf_export import export_layout_dxf


def scatter_fixture():
    rows=[dict(name=n,shape=box(x,y,x+w,y+h),x=x,y=y,angle=0,sheet_index=0)
          for n,x,y,w,h in [('A',5,5,70,40),('B',170,15,65,35),
                           ('C',40,140,25,70),('D',140,130,60,55)]]
    return _summarize(rows,[(260,230)],5.,3.,'known scattered layout')


def test_all_part_rebuild_improves_a_scattered_layout():
    src=scatter_fixture()
    before=_compact_score(src['placements'],5.)
    results=search_engine2([src],False,'balanced',time_budget_s=6.)
    assert results
    assert all(_validate(r['placements'],260,230,5.,3.) for r in results)
    assert min(_compact_score(r['placements'],5.) for r in results)<before*.60
    assert all(abs(p['shape'].area - q['shape'].area)<1e-5 for r in results
               for p,q in zip(src['placements'],r['placements']))


def test_original_layout_is_retained_in_portfolio_and_dxf_reimports():
    src=scatter_fixture()
    pool=variant_pool([('original',src)],auto=False,quality='balanced',search=True)
    assert pool
    assert any(all(abs(a['shape'].bounds[0]-b['shape'].bounds[0])<1e-5
                for a,b in zip(v['layout']['placements'],src['placements'])) for v in pool)
    for entry in pool:
        lay=entry['layout']
        assert _validate(lay['placements'],260,230,5.,3.)
        imported=parse_sheet_dxf(export_layout_dxf(lay))
        assert len(imported.pieces)==4


def test_polish_moves_nonbounding_red_part_left_without_overlap():
    # Red item is far to the right while the taller anchor sets the bounding
    # envelope.  Pure bbox objective considers left-shifting red a tie.
    parts=[('anchor',box(5,5,35,125)),('right',box(155,5,175,125)),
           ('red',box(145,50,154,65))]
    rows=[dict(name=n,shape=p,x=p.bounds[0],y=p.bounds[1],angle=0,sheet_index=0)
          for n,p in parts]
    improved=_polish(rows,180,140,5.,3.,rounds=2)
    assert _validate(improved,180,140,5.,3.)
    assert improved[2]['shape'].bounds[0]<145


def test_hole_nesting_can_be_disabled():
    host=Point(70,70).buffer(57).difference(Point(70,70).buffer(30))
    inner=Point(70,70).buffer(9)
    far=box(140,7,154,22)
    rows=[dict(name=n,shape=sh,x=sh.bounds[0],y=sh.bounds[1],angle=0,sheet_index=0)
          for n,sh in [('ring',host),('inner',inner),('far',far)]]
    src=_summarize(rows,[(200,180)],5.,3.,'nested seed')
    results=search_engine2([src],False,'balanced',time_budget_s=3.,allow_nesting=False)
    assert all(not any('nested_in' in p for p in r['placements']) for r in results)
