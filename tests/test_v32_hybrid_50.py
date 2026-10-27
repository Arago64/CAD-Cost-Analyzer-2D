"""v3.2 key regressions: 50 items, duplicate identity, groups, degree search, DXF."""
from time import monotonic
from shapely import affinity
from shapely.geometry import Polygon, box
import pytest
from sheet_nesting import CutPiece, _input_items, parse_sheet_dxf
from nesting_advanced import _summarize
from nesting_shelf_v32 import shelf_start
from nesting_v32 import improve_seed, _sources_for_rows, _group_trial
from nesting_v3 import optimize
from optimizer_v15 import _validate
from dxf_export import export_layout_dxf


def test_fifty_copies_full_pipeline_and_dxf_roundtrip():
    parts=[CutPiece('Repeat',box(0,0,35,20),'P',qty=50)]
    found=optimize(parts,{'Repeat':50},auto=True,quality='balanced',budget=6)
    assert found and len(found[0]['placements'])==50
    assert _validate(found[0])
    reread=parse_sheet_dxf(export_layout_dxf(found[0]),units='mm')
    assert len(reread.pieces)==50


def test_fifty_one_is_rejected():
    pieces=[CutPiece('A',box(0,0,20,20),'P',qty=51)]
    with pytest.raises(ValueError, match='50'):
        _input_items(pieces)


def test_different_shapes_with_same_label_are_not_confused():
    base=[CutPiece('Same',box(0,0,35,20),'P'),
          CutPiece('Same',Polygon([(0,0),(50,0),(50,12),(15,12),(15,30),(0,30)]),'P'),
          CutPiece('Different',box(0,0,12,8),'P')]
    items=_input_items(base)
    rows=[]
    for i,(nm,shape) in enumerate(items):
        geom=affinity.translate(shape,xoff=10+i*100,yoff=20)
        rows.append(dict(name=nm,shape=geom,x=geom.bounds[0],y=geom.bounds[1],angle=0,sheet_index=0))
    src=_sources_for_rows(rows,items)
    assert src is not None
    assert [round(p.area,2) for p in src]==[round(r['shape'].area,2) for r in rows]
    seed=_summarize(rows,[(500,80)],5,3,'duplicate labels')
    assert _validate(seed)
    improved=improve_seed(seed,items,auto=True,quality='fast',deadline=monotonic()+1.4)
    assert _validate(improved)
    assert len(improved['placements'])==3
    assert sorted(round(p['shape'].area,3) for p in improved['placements'])==sorted(round(p['shape'].area,3) for p in seed['placements'])


def test_cluster_mover_keeps_identity_and_clearance():
    shapes=[box(80,70,100,90),box(110,70,130,90),box(5,5,25,25)]
    rows=[dict(name='X #1',shape=s,x=s.bounds[0],y=s.bounds[1],angle=0,sheet_index=0) for s in shapes]
    layout=_summarize(rows,[(160,130)],5,3,'test')
    assert _validate(layout)
    result=_group_trial(rows,[0,1],W=160,H=130,margin=5,gap=3,auto=True,
                        deadline=monotonic()+3,quality='thorough',rng=None)
    assert result is not None
    score,changed=result
    candidate=_summarize(changed,[(160,130)],5,3,'group changed')
    assert _validate(candidate)
    assert len(changed)==len(rows)
    assert [row['name'] for row in changed]==[row['name'] for row in rows]
    assert score[0]<((130-5+10)*(90-5+10))


def test_fixed_stock_quick_start_with_50_items():
    parts=[CutPiece('X',box(0,0,12,8),'P',qty=50)]
    start=shelf_start(_input_items(parts),auto=False,W=200,H=140,margin=5,gap=3,multi_sheet=False)
    assert start is not None and start['sheet_count']==1 and _validate(start)
