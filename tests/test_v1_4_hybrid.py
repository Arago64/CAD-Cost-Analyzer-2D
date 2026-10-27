"""v1.4: configuration-space contacts, multi-part neighborhoods and true offcut geometry."""
from shapely.geometry import box
from shapely import affinity
from nesting_nfp import no_fit_polygon,nfp_contact_candidates
from nesting_hybrid import enhance_layout
from nesting_advanced import _summarize
from remnant_polygonal import polygonal_remnants,largest_polygonal_free_rectangle
from remnant_optimization import largest_free_rectangle


def test_nfp_has_hole_for_larger_ring():
    host=box(0,0,100,100).difference(box(25,25,75,75))
    mover=box(0,0,10,10)
    forbidden=no_fit_polygon(host,mover)
    assert forbidden.is_valid
    # Mover origin (35,35) leaves the entire part in the 50 x 50 opening.
    from shapely.geometry import Point
    assert not forbidden.covers(Point(35,35))
    assert forbidden.covers(Point(5,5))
    candidates=nfp_contact_candidates([{'shape':host}],mover,3)
    assert candidates and len(candidates)>4


def test_polygonal_offcut_improves_bbox_only_estimate():
    ring=box(10,10,100,100).difference(box(20,20,90,90))
    placed=[{'shape':ring}]
    rect,area,connected,components=largest_polygonal_free_rectangle(placed,120,120,margin=5,clearance=0,grid=24)
    _,bbox_only=largest_free_rectangle(placed,120,120,margin=5,clearance=0)
    assert area > bbox_only
    assert area>1000 and rect is not None
    free,_,_=polygonal_remnants(placed,120,120,margin=5)
    assert free.buffer(1e-6).covers(rect)


def test_lns_beam_improves_scattered_two_parts_without_collisions():
    rows=[{'shape':affinity.translate(box(0,0,25,20),xoff=5,yoff=5),
           'name':'A','angle':0.,'x':5.,'y':5.,'sheet_index':0},
          {'shape':affinity.translate(box(0,0,25,20),xoff=150,yoff=70),
           'name':'B','angle':0.,'x':150.,'y':70.,'sheet_index':0}]
    baseline=_summarize(rows,[(210.,120.)],5,3,'fixture')
    improved=enhance_layout(baseline,quality='balanced',preserve=False,auto=False)
    x0=min(p['shape'].bounds[0] for p in rows)
    y0=min(p['shape'].bounds[1] for p in rows)
    x1=max(p['shape'].bounds[2] for p in rows)
    y1=max(p['shape'].bounds[3] for p in rows)
    old=(x1-x0)*(y1-y0)
    p=improved['placements']
    X0=min(z['shape'].bounds[0] for z in p);X1=max(z['shape'].bounds[2] for z in p)
    Y0=min(z['shape'].bounds[1] for z in p);Y1=max(z['shape'].bounds[3] for z in p)
    assert (X1-X0)*(Y1-Y0) < old
    assert p[0]['shape'].distance(p[1]['shape'])>=3-1e-6
    assert improved['hybrid_evaluated']>=1
