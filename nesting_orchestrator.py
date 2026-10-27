"""Cooperative anytime 2D nesting search.

Independent, deterministic, sequential search windows. Each window gets its own
budget and all candidate layouts enter a common archive; promising alternatives
become seeds for later windows. Search is heuristic, NOT exhaustive in the
continuous configuration space and NOT an optimality certificate.
"""
from __future__ import annotations
from dataclasses import dataclass
from time import monotonic
from nesting_engine2 import search_engine2, _compact_score, _validate
from optimizer_v15 import _signature


@dataclass
class SearchReport:
    phases: int = 0
    candidates: int = 0
    elapsed_seconds: float = 0.0
    best_sheet_mm2: float = 0.0
    mode: str = 'cooperative'
    budgets_exhausted: bool = False


def _candidate_pool(layouts, cap=10):
    """Use cheap *different* objectives so scrap-friendly seeds survive."""
    if len(layouts) <= cap: return list(layouts)
    families = [
        sorted(layouts,key=lambda x:x['sheet_mm2']),
        sorted(layouts,key=lambda x:x['sheet_width_mm']),
        sorted(layouts,key=lambda x:x['sheet_height_mm']),
        sorted(layouts,key=lambda x:_compact_score(x['placements'],x['margin_mm'])),
        list(reversed(layouts)),
    ]
    selected=[]; seen=set()
    while len(selected)<cap:
        found=False
        for family in families:
            while family:
                item=family.pop(0)
                sig=_signature(item)
                if sig in seen:continue
                seen.add(sig);selected.append(item);found=True
                break
            if len(selected)>=cap:break
        if not found:break
    return selected


def cooperative_search(sources,auto,quality='balanced',*,mode='cooperative',total_budget_s=18.,
                       allow_nesting=True,random_seed=1701,report=None,progress=None):
    """Return valid diverse candidates from sequential independently budgeted windows.

    Modes: 'standard' (one window), 'cooperative' (3), 'intensive' (5).
    Budgets are *requested* wallclock limits. Complex GEOS calls can exceed an
    individual window slightly because no OS-level process is killed.
    """
    valid_sources=[l for l in sources if l and l['sheet_count']==1 and
                   l.get('stock_outline') is None and 2<=len(l['placements'])<=8]
    if not valid_sources:return []
    if mode not in ('standard','cooperative','intensive'):
        raise ValueError(f'Unknown search mode: {mode}')
    periods={'standard':[1.], 'cooperative':[.34,.33,.33],
             'intensive':[.18,.19,.20,.21,.22]}[mode]
    strategies={'standard':['mixed'],
                'cooperative':['constructive','contacts','reshuffle'],
                'intensive':['constructive','contacts','reshuffle','contacts','mixed']}[mode]
    cap=36 if mode!='intensive' else 64
    budget=max(0.3,float(total_budget_s))
    deadline=monotonic()+budget
    start=monotonic()
    archive={_signature(l):l for l in valid_sources}
    new_candidates=0;executed=0
    for phase,fraction in enumerate(periods):
        remaining=deadline-monotonic()
        if remaining <= .03: break
        slot=min(remaining,budget*fraction)
        # Pass untouched originals every time; the rest is a diverse shared
        # frontier, not just the one minimum-area winner.
        if phase==0:
            seeds=valid_sources
        else:
            seeds=_candidate_pool(list(archive.values()),cap=min(10,len(archive)))
            for original in valid_sources:
                if _signature(original) not in {_signature(x) for x in seeds}:
                    seeds=[original]+seeds[:9]
        phase_quality=quality
        if mode=='intensive' and phase>=2 and quality=='balanced':
            phase_quality='thorough'
        candidates=search_engine2(seeds,auto,phase_quality,
                    time_budget_s=max(.1,slot),seed=random_seed+10007*phase,
                    allow_nesting=allow_nesting,return_all=True,strategy=strategies[phase])
        executed+=1
        for candidate in candidates:
            sig=_signature(candidate)
            if sig not in archive:
                # Verify all proposed layouts against original precise shapes.
                sheet=candidate['sheets'][0]
                if _validate(candidate['placements'],sheet['width_mm'],sheet['height_mm'],
                         candidate['margin_mm'],candidate['gap_mm']):
                    archive[sig]=candidate;new_candidates+=1
        if len(archive)>cap:
            kept=_candidate_pool(list(archive.values()),cap=cap)
            archive={_signature(x):x for x in kept}
        if progress is not None:
            # Capture the LAST COMPLETE phase. This callback must not change
            # candidate ordering or decide validity; it only checkpoints.
            progress(list(archive.values()), phase+1)
    pool=_candidate_pool(list(archive.values()),cap=min(cap,len(archive)))
    for layout in pool:
        layout['orchestration_phases']=executed
        layout['orchestration_mode']=mode
        layout['orchestration_seconds']=round(monotonic()-start,2)
        layout['orchestration_new_candidates']=new_candidates
    if report is not None:
        report.phases=executed
        report.candidates=new_candidates
        report.elapsed_seconds=round(monotonic()-start,3)
        report.best_sheet_mm2=min(l['sheet_mm2'] for l in pool)
        report.mode=mode
        report.budgets_exhausted=(monotonic()>=deadline)
    return pool
