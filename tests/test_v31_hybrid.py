"""v3.1 regression tests: manual seed, contact repair, proper stock and DXF."""
from time import monotonic
from shapely import affinity
from shapely.geometry import Polygon,box
from sheet_nesting import CutPiece, _input_items, parse_sheet_dxf
from nesting_advanced import _summarize
from nesting_v31 import improve_seed
from optimizer_v15 import _validate
from dxf_export import export_layout_dxf


def poorly_spaced(auto):
    pieces=[CutPiece('A',box(0,0,100,50),'PART'),
            CutPiece('B',box(0,0,80,65),'PART'),
            CutPiece('C',box(0,0,40,40),'PART')]
    rows=[]
    for (name,p),(x,y) in zip(_input_items(pieces),[(5,5),(170,5),(5,190)]):
        shape=affinity.translate(p,xoff=x,yoff=y)
        rows.append(dict(name=name,shape=shape,x=x,y=y,angle=0.,sheet_index=0))
    return pieces,_summarize(rows,[(300,290) if auto else (450,400)],5,3,'manual separated')


def test_repair_manual_start_reduces_actual_stock_and_roundtrips_dxf():
    pieces,before=poorly_spaced(auto=True)
    after=improve_seed(before,_input_items(pieces),auto=True,
        quality='balanced',deadline=monotonic()+4)
    assert _validate(before) and _validate(after)
    assert after['sheet_mm2'] < before['sheet_mm2']*.75
    rescan=parse_sheet_dxf(export_layout_dxf(after),units='mm')
    assert len(rescan.pieces)==3


def test_fixed_stock_cannot_reduce_paid_area_and_preserves_clearance():
    pieces,before=poorly_spaced(auto=False)
    after=improve_seed(before,_input_items(pieces),auto=False,
        quality='balanced',deadline=monotonic()+3)
    assert _validate(after)
    assert before['sheet_mm2']==after['sheet_mm2']==450*400
    assert len(after['placements'])==3


def test_rotated_manual_incumbent_is_not_lost():
    # Store a non-standard precise editor angle; stage must not require 0/15/90.
    shape=Polygon([(0,0),(38,0),(38,12),(12,12),(12,40),(0,40)])
    pieces=[CutPiece('L',shape,'PART'),CutPiece('M',box(0,0,9,8),'PART')]
    turned=affinity.rotate(shape,23.75,origin=(0,0))
    turned=affinity.translate(turned,xoff=5-turned.bounds[0],yoff=5-turned.bounds[1])
    rows=[dict(name='L #1',shape=turned,x=5,y=5,angle=23.75,sheet_index=0),
          dict(name='M #1',shape=box(75,75,84,83),x=75,y=75,angle=0.,sheet_index=0)]
    before=_summarize(rows,[(100,100)],3,1,'manual 23.75 degrees')
    assert _validate(before)
    after=improve_seed(before,_input_items(pieces),auto=True,
                       quality='thorough',deadline=monotonic()+3)
    assert _validate(after)
    assert after['sheet_mm2']<=before['sheet_mm2']
