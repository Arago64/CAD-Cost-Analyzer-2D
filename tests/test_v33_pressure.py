"""v3.3 exact contact search: geometry, monotonic material and DXF roundtrip."""
from time import monotonic
from shapely.geometry import box, Polygon
from nesting_advanced import _summarize
from optimizer_v15 import _validate
from nesting_v33 import improve_seed,footprint
from dxf_export import export_layout_dxf
from sheet_nesting import parse_sheet_dxf


def make(rows, margin=5, gap=3):
    placements=[]
    for i,shape in enumerate(rows):
        placements.append(dict(name='Repeated',shape=shape,x=shape.bounds[0],
                         y=shape.bounds[1],angle=0.,sheet_index=0))
    return _summarize(placements,[(500.,300.)],margin,gap,'seed')


def test_directional_compactor_finds_continuous_contact_and_valid_dxf():
    seed=make([box(5,5,45,35),box(90,5,130,35),box(190,5,230,35)])
    assert _validate(seed)
    result=improve_seed(seed,deadline=monotonic()+2.,quality='precision')
    assert _validate(result)
    assert result['sheet_mm2']<seed['sheet_mm2']*.6
    parsed=parse_sheet_dxf(export_layout_dxf(result),units='mm')
    assert len(parsed.pieces)==3


def test_convex_concave_hole_and_distinct_duplicate_instances():
    outer=Polygon([(5,5),(70,5),(70,70),(5,70)],holes=[[(22,22),(53,22),(53,53),(22,53)]])
    other=box(130,5,142,17)
    seed=make([outer,other,box(250,5,262,17)])
    assert _validate(seed)
    found=improve_seed(seed,deadline=monotonic()+2.,quality='thorough')
    assert _validate(found)
    assert len(found['placements'])==3
    assert len(found['placements'][0]['shape'].interiors)==1
    assert sorted(round(p['shape'].area,5) for p in found['placements']) == sorted(round(p['shape'].area,5) for p in seed['placements'])


def test_fifty_polygons_bounded_and_valid():
    polygons=[box(5+(i%10)*40,5+(i//10)*33,25+(i%10)*40,21+(i//10)*33)
              for i in range(50)]
    seed=make(polygons)
    assert _validate(seed)
    improved=improve_seed(seed,deadline=monotonic()+.6,quality='balanced')
    assert _validate(improved)
    assert len(improved['placements'])==50
    assert improved['sheet_mm2']<=seed['sheet_mm2']


def test_tiny_deadline_returns_untouched_valid_incumbent():
    seed=make([box(5,5,20,20),box(80,5,95,20)])
    result=improve_seed(seed,deadline=monotonic()-1.)
    assert result is seed
