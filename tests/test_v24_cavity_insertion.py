"""Regression: small parts must not lose possible cavities to beam pruning."""
from pathlib import Path
from shapely import affinity
from shapely.geometry import Point,box
from sheet_nesting import parse_sheet_dxf,_orient
from nesting_advanced import advanced_nest,_summarize
from pocket_repair import cavity_repair
from optimizer_v15 import _validate,variant_pool
from dxf_export import export_layout_dxf


def _reference_with_removed_rect():
    filepath=Path(__file__).resolve().parent.parent/'examples'/'DEMO_7_PARTS_COMPACT_V12.dxf'
    pieces=parse_sheet_dxf(filepath.read_bytes()).pieces
    baseline=advanced_nest(pieces,margin=5.,gap=3.,quality='fast',auto=True,
                           hole_nesting=True,preserve_remnants=False)
    rows=[dict(p) for p in baseline['placements']]
    idx=next(i for i,p in enumerate(rows) if p['name'].startswith('SMALL_RECT'))
    ref=_orient(rows[idx]['shape'],0)
    shifted=affinity.translate(ref,xoff=105.,yoff=344.)
    rows[idx]=dict(rows[idx],shape=shifted,x=105.,y=344.,angle=0.)
    return _summarize(rows,[(340.,444.)],5.,3.,'intentional omission of cavity nesting')


def test_real_c_shape_refits_small_rectangle_beside_nested_circle():
    src=_reference_with_removed_rect()
    assert _validate(src)
    assert src['nested_items']==1
    result=cavity_repair(src,auto=False)
    assert result is not None and _validate(result)
    assert result['nested_items']==2
    assert len(result['placements'])==len(src['placements'])
    for a,b in zip(src['placements'],result['placements']):
        assert abs(a['shape'].area-b['shape'].area)<1e-6
    assert len(parse_sheet_dxf(export_layout_dxf(result)).pieces)==7


def test_ring_hole_insertion_without_colliding_with_host():
    host=Point(60,60).buffer(38).difference(Point(60,60).buffer(25))
    small=box(120,10,132,20)
    rows=[{'name':name,'shape':poly,'x':poly.bounds[0], 'y':poly.bounds[1],
           'angle':0, 'sheet_index':0}
          for name,poly in [('host',host),('child',small)]]
    src=_summarize(rows,[(150,125)],2.,2.,'circle with genuine hole')
    result=cavity_repair(src,auto=False)
    assert result is not None and result['nested_items']==1
    assert _validate(result)
    assert result['placements'][1]['nested_in']=='host'


def test_variant_portfolio_preserves_nested_as_user_selectable_alternative():
    src=_reference_with_removed_rect()
    entries=variant_pool([('manual',src)],auto=False,quality='fast',search=False,hole_nesting=True)
    assert any(v['layout']['nested_items']==2 for v in entries)
    assert any('вложений' in v['name'].lower() for v in entries)
    assert any(v['layout']['nested_items']==1 for v in entries)


def test_disallow_repair_if_no_fit_with_clearance():
    host=Point(60,60).buffer(30).difference(Point(60,60).buffer(12))
    too_big=box(110,20,140,60)
    src=_summarize([{'name':n,'shape':p,'x':p.bounds[0], 'y':p.bounds[1],
                     'angle':0,'sheet_index':0}
            for n,p in [('host',host),('big',too_big)]],
           [(150.,100.)],2.,3.,'no room')
    assert cavity_repair(src,auto=False) is None


def test_coordinated_repacking_moves_existing_nested_part_first():
    # Both inner blocks fit side by side, but the initial one occupies the
    # middle of the hole. Moving only the second part cannot solve this.
    ring=box(0,0,90,70).difference(box(15,15,78,55))
    occupied=box(30,20,60,50)
    outside=box(95,15,120,45)
    rows=[dict(name=n,shape=poly,x=poly.bounds[0],y=poly.bounds[1],
               angle=0,sheet_index=0) for n,poly in
          [('host',ring),('center',occupied),('extra',outside)]]
    src=_summarize(rows,[(130,75)],0.,2.,'existing nested part blocks another')
    assert src['nested_items']==1 and _validate(src)
    result=cavity_repair(src,auto=False,angles=(0,),max_moves=1)
    assert result is not None and _validate(result)
    assert result['nested_items']==2
    assert result['placements'][1]['shape'].bounds!=src['placements'][1]['shape'].bounds
