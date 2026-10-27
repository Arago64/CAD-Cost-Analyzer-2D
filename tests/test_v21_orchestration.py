"""Cooperative stage scheduling and diversity-preservation checks."""
from shapely.geometry import box
from shapely import affinity
from nesting_advanced import _summarize
from nesting_engine2 import _diverse_expansions
from nesting_orchestrator import cooperative_search, SearchReport
import nesting_orchestrator


def fixture():
    rows=[dict(name=n,shape=box(x,y,x+30,y+15),x=x,y=y,angle=0,sheet_index=0)
          for n,x,y in [('A',5,5),('B',85,5)]]
    return _summarize(rows,[(150,75)],5.,3.,'fixture')


def test_each_phase_has_own_budget_and_receives_previous_candidate(monkeypatch):
    original=fixture();calls=[]
    def stub(seeds,auto,quality,*,time_budget_s,seed,allow_nesting,return_all,strategy):
        calls.append((seed,time_budget_s,len(seeds),return_all,strategy))
        source=seeds[0]
        new=[]
        for idx,p in enumerate(source['placements']):
            s=affinity.translate(p['shape'],xoff=(-4 if idx==1 else 0))
            new.append(dict(p,shape=s,x=s.bounds[0],y=s.bounds[1]))
        return [_summarize(new,[(150,75)],5.,3.,'generated')]
    monkeypatch.setattr(nesting_orchestrator,'search_engine2',stub)
    report=SearchReport()
    results=cooperative_search([original],False,total_budget_s=6.,report=report)
    assert len(calls)==3
    assert calls[0][0]!=calls[1][0]!=calls[2][0]
    assert all(c[1]>0 and c[3] for c in calls)
    assert [c[4] for c in calls]==['constructive','contacts','reshuffle']
    assert report.phases==3
    assert len(results)>=2
    assert any(r['placements'][1]['shape'].bounds[0]<85 for r in results)


def test_expansion_diversity_does_not_discard_all_rotations():
    parts=[]
    for angle in (0,90,180,270):
        for k in range(4):
            parts.append((k+angle/1000,dict(x=float(k),y=0.,angle=angle)))
    selected=_diverse_expansions(parts,8)
    assert {p['angle'] for _,p in selected}=={0,90,180,270}


def test_unknown_mode_fails_clear():
    import pytest
    with pytest.raises(ValueError):
        cooperative_search([fixture()],False,mode='truly_exhaustive')
