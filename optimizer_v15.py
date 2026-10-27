"""v1.5 candidate portfolio, group transformations and bounded LNS neighborhoods.

Not a mathematical certificate of optimum. All candidates are revalidated in
Shapely against source contours and clearances. Multi-rectangle free-space
ranking is applied at portfolio selection (not only the display layer).
"""
from __future__ import annotations
from itertools import combinations
from math import ceil
from shapely import affinity
from shapely.geometry import box
from nesting_advanced import _summarize
from nesting_hybrid import _repack, _crop, _key
from nesting_refinement import _valid
from remnant_portfolio import metrics_summary


def _signature(layout):
    return (tuple(round(float(s['width_mm']),2) for s in layout['sheets']),
            tuple(round(float(s['height_mm']),2) for s in layout['sheets']),
            tuple((p['name'],p.get('sheet_index',0),round(p['shape'].bounds[0],2),
                   round(p['shape'].bounds[1],2),round(float(p['angle']),2))
                  for p in layout['placements']))


def _validate(layout):
    gap=float(layout['gap_mm']);margin=float(layout['margin_mm'])
    for sid,sh in enumerate(layout['sheets']):
        own=[p for p in layout['placements'] if p.get('sheet_index',0)==sid]
        outline=(layout.get('stock_outline') if sid==0 else None)
        safe=outline.buffer(-margin) if outline is not None else box(margin,margin,sh['width_mm']-margin,sh['height_mm']-margin)
        for i,p in enumerate(own):
            if not _valid(p['shape'],own[:i],gap,safe):return False
    return True


def _rebuild(rows,seed,auto,desc):
    margin=float(seed['margin_mm']);gap=float(seed['gap_mm'])
    if auto:
        fresh,(w,h)=_crop(rows,margin)
        result=_summarize(fresh,[(w,h)],margin,gap,desc)
    else:
        sheets=[(s['width_mm'],s['height_mm']) for s in seed['sheets']]
        result=_summarize(rows,sheets,margin,gap,desc)
    if seed.get('stock_outline') is not None:
        result['stock_outline']=seed['stock_outline']
        result['sheet_mm2']=seed['sheet_mm2']
        result['waste_mm2']=max(0,result['sheet_mm2']-result['net_mm2'])
        result['utilization']=100*result['net_mm2']/result['sheet_mm2']
    return result


def global_moves(seed,auto=True):
    """Move/rotate ALL parts as a rigid GROUP, preserving every internal gap.

    Useful where many individual moves are temporarily forbidden but a whole
    cluster can move/turn into a large connected blank region.
    """
    if seed['sheet_count']!=1 or seed.get('stock_outline') is not None:return []
    rows=seed['placements'];xs=[p['shape'].centroid.x for p in rows];ys=[p['shape'].centroid.y for p in rows]
    center=(sum(xs)/len(xs),sum(ys)/len(ys))
    margin=seed['margin_mm'];W=seed['sheets'][0]['width_mm'];H=seed['sheets'][0]['height_mm']
    alternatives=[]
    for angle in (0,90,180,270):
        turned=[]
        for p in rows:
            sh=affinity.rotate(p['shape'],angle,origin=center)
            turned.append(dict(p,shape=sh,angle=(float(p['angle'])+angle)%360))
        aa=min(p['shape'].bounds[0] for p in turned);bb=min(p['shape'].bounds[1] for p in turned)
        cc=max(p['shape'].bounds[2] for p in turned);dd=max(p['shape'].bounds[3] for p in turned)
        if auto:
            offsets=[(margin-aa,margin-bb)]
        else:
            if cc-aa>W-2*margin+1e-6 or dd-bb>H-2*margin+1e-6:continue
            offsets=[(margin-aa,margin-bb),(W-margin-cc,margin-bb),
                     (margin-aa,H-margin-dd),(W-margin-cc,H-margin-dd)]
        for dx,dy in offsets:
            moved=[]
            for p in turned:
                sh=affinity.translate(p['shape'],xoff=dx,yoff=dy)
                moved.append(dict(p,shape=sh,x=sh.bounds[0],y=sh.bounds[1]))
            result=_rebuild(moved,seed,auto,f'v1.5 rigid all-part group rotation {angle}°')
            if _validate(result):alternatives.append(result)
    return alternatives


def _neighborhoods(n,quality):
    if not 2<=n<=8:return []
    # Every part participates in at least one neighborhood. At up to six
    # objects, the full group is considered; above six the complete rigid-group
    # transformation is always tested separately as a cheap global move.
    sizes={'fast':(2,), 'balanced':(2,3),'thorough':(2,3,4,5),'precision':(3,4,5,6)}[quality]
    opts=[]
    for size in sizes:
        if size>n:continue
        allsets=list(combinations(range(n),size))
        if len(allsets)>7:
            step=max(1,(len(allsets)-1)//5)
            allsets=allsets[::step][:6]+[allsets[-1]]
        opts.extend(allsets)
    if n<=6:opts.append(tuple(range(n)))
    return list(dict.fromkeys(opts))


def reoptimize(seed,auto,quality='balanced',max_trials=None,engine2_enabled=True,hole_nesting=True):
    """Bounded large-neighborhood & beam re-packing from ANY validated seed.

    Returns an archive of DIFFERENT legal placements. Preserves original, does
    not automatically replace it. Deliberately restrict heavy group repacking.
    """
    # New engine first: rebuild ALL parts, rotate independent shapes and keep
    # diverse validated variants.  Legacy search remains for multi-sheet jobs.
    if engine2_enabled and seed['sheet_count']==1 and not seed.get('stock_outline') and quality!='fast' and 2<=len(seed['placements'])<=8:
        from nesting_engine2 import search_engine2
        rebuilt=search_engine2([seed],auto,quality,allow_nesting=hole_nesting,
                    time_budget_s=(max(1.,float(max_trials)) if max_trials is not None else None))
        return [seed]+[p for p in rebuilt if _signature(p)!=_signature(seed)]
    found=[seed];seen={_signature(seed)}
    if seed['sheet_count']!=1 or seed.get('stock_outline') is not None or quality=='fast':
        return found
    margin=float(seed['margin_mm']);gap=float(seed['gap_mm'])
    baseW=float(seed['sheets'][0]['width_mm']);baseH=float(seed['sheets'][0]['height_mm'])
    if auto:
        W,H=baseW*1.14,baseH*1.14
    else:W,H=baseW,baseH
    n=len(seed['placements'])
    if not 2<=n<=8:return found
    for layout in global_moves(seed,auto):
        sig=_signature(layout)
        if sig not in seen:seen.add(sig);found.append(layout)
    budget=max_trials or {'balanced':2,'thorough':5,'precision':8}[quality]
    subsets=_neighborhoods(n,quality)
    # Diverse neighborhoods: largest subgroups first every other step.
    subsets=sorted(subsets,key=lambda x:(-len(x),x))
    if len(subsets)>budget:
        sample=[subsets[0],subsets[-1]]
        stride=max(1,len(subsets)//max(1,budget-2))
        sample+=subsets[1:-1:stride][:max(0,budget-2)]
        subsets=list(dict.fromkeys(sample))[:budget]
    angles={'balanced':(0,90,180,270), 'thorough':tuple(range(0,360,45)),
            'precision':tuple(range(0,360,30))}[quality]
    # This is the bounded beam/LNS search. Each _repack explores several
    # placement orders and keeps top partial beams of up to two branches.
    for group in subsets:
        try:
            trials=_repack(seed['placements'],group,W,H,margin,gap,angles,quality,False,
                           branch=(1 if quality=='balanced' else 2))
        except Exception:
            continue
        for rows in trials[:4]:
            obj=_rebuild(rows,seed,auto,'v1.5 multi-part LNS + NFP / Beam')
            sig=_signature(obj)
            if sig in seen or not _validate(obj):continue
            seen.add(sig);found.append(obj)
    # v1.6: the portfolio also includes directed contact compaction under
    # independent free-form scrap objectives, not just legacy bbox repacking.
    from nesting_v16 import expanded_candidates
    for new in expanded_candidates(seed,auto,quality):
        sig=_signature(new)
        if sig not in seen and _validate(new):
            seen.add(sig);found.append(new)
    return found


def _summary(layout,grid=18):
    result=metrics_summary(layout,grid=grid)
    from remnant_shapes import freeform_metrics
    shapes=freeform_metrics(layout,grid=10)
    result['mixed_shapes_total_mm2']=round(shapes['mixed_total_mm2'],2)
    result['usable_free_mm2']=round(shapes['usable_free_mm2'],2)
    # True connected polygon candidates (including L- and stepped offcuts).
    # Evaluated on the final shortlist only; never in inner search loops.
    from irregular_remnants import irregular_remnants
    freeform=irregular_remnants(layout,kerf_mm=float(layout['gap_mm']),
            min_neck_mm=8.,min_area_mm2=1000.)
    result['freeform_largest_mm2']=round(max((x['area_mm2'] for x in freeform),default=0.),2)
    result['freeform_top3_mm2']=round(sum(x['area_mm2'] for x in freeform[:3]),2)
    result['compound_largest_mm2']=round(max((x['area_mm2'] for x in freeform if x.get('compound')),default=0.),2)
    result['compound_count']=sum(1 for x in freeform if x.get('compound'))
    return result


def variant_pool(seeds,auto,quality='balanced',search=True,engine2_enabled=True,hole_nesting=True, search_mode='cooperative',search_budget_s=18.,progress=None):
    """Preserve alternatives for min sheet, multi-rectangle, connected leftover.

    seeds: iterable of (name, layout). Manual seeds are retained verbatim.
    Returns list of dictionaries {id,name,layout,metrics}. No false alternatives:
    identical layouts are not duplicated just to make the UI show options.
    """
    distinct=[];keys=set();search_info={}
    def include(label,layout):
        sig=_signature(layout)
        if sig not in keys:
            keys.add(sig);distinct.append((label,layout))
    for label,layout in seeds:
        include(label,layout)   # NEVER discard a manual or previous solution
    # Protected cavity repair: do not let generic beam/contact pruning eliminate
    # small-in-large placements. This runs independently of the global search.
    if hole_nesting:
        from pocket_repair import cavity_repair
        from time import monotonic
        cavity_deadline=monotonic()+1.5
        for label,layout in list(distinct)[:5]:
            if monotonic()>=cavity_deadline:break
            alternative=cavity_repair(layout,auto=auto,deadline=cavity_deadline)
            if alternative is not None:
                include('Плотное вложение в полости / Cavity repair: '+label,alternative)
    use_new=(search and engine2_enabled and quality!='fast' and len(seeds)>0 and
             all(l['sheet_count']==1 and not l.get('stock_outline') for _,l in seeds)
             and 2<=len(seeds[0][1]['placements'])<=8)
    if use_new:
        from nesting_orchestrator import cooperative_search, SearchReport
        from dataclasses import asdict
        search_report=SearchReport()
        rebuilt=cooperative_search([layout for _,layout in seeds],auto,quality,
              mode=search_mode,total_budget_s=search_budget_s,allow_nesting=hole_nesting,report=search_report,progress=progress)
        search_info=asdict(search_report)
        for candidate in rebuilt:include('Engine 2 / Full-group search',candidate)
    elif search:
        for label,layout in seeds:
            for candidate in reoptimize(layout,auto,quality,engine2_enabled=False):include(label,candidate)
    if not distinct:return []
    # Exact remnant geometry is expensive: keep the original solutions and a
    # diverse geometry-only shortlist, then score these finalists accurately.
    # Earlier releases calculated all scrap metrics for every trial and could
    # become unresponsive when the shared search archive grew.
    if len(distinct)>12:
        originals={_signature(layout) for _,layout in seeds}
        first=[v for v in distinct if _signature(v[1]) in originals]
        other=[v for v in distinct if _signature(v[1]) not in originals]
        families=[sorted(other,key=lambda e:e[1]['sheet_mm2']),
                  sorted(other,key=lambda e:e[1]['sheet_width_mm']),
                  sorted(other,key=lambda e:e[1]['sheet_height_mm']),
                  list(reversed(other))]
        shortlisted=[];seen_short={_signature(l) for _,l in first}
        while len(shortlisted)+len(first)<12:
            progress=False
            for group in families:
                while group:
                    candidate=group.pop(0);signature=_signature(candidate[1])
                    if signature in seen_short:continue
                    seen_short.add(signature);shortlisted.append(candidate);progress=True
                    break
                if len(shortlisted)+len(first)>=12:break
            if not progress:break
        distinct=first+shortlisted
    # For auto sheet, large-remainder variants cannot silently consume
    # arbitrarily more metal than minimum found area.
    min_area=min(l['sheet_mm2'] for _,l in distinct)
    eligible=[(label,l) for label,l in distinct if l['sheet_count']==min(lay['sheet_count'] for _,lay in distinct)
             and (not auto or l['sheet_mm2']<=min_area*1.05+1e-6)]
    metrics={_signature(l):_summary(l) for _,l in distinct}
    def m(v):return metrics[_signature(v[1])]
    winners=[]
    def add(label,candidate,origin):
        sig=_signature(candidate)
        if any(_signature(x['layout'])==sig for x in winners):return
        winners.append({'id':f'variant_{len(winners)+1}','name':label,'layout':candidate,
                        'metrics':metrics[sig],'origin':origin,'search_info':search_info})
    smallest=min(distinct,key=lambda x:(x[1]['sheet_count'],x[1]['sheet_mm2']))
    add('Минимальный лист / Minimum sheet',smallest[1],smallest[0])
    # A densely nested geometry must remain selectable even when its measured
    # rectangle scrap area is lower: small parts inside material holes are a
    # genuinely different goal from minimum bounding rectangle.
    if hole_nesting:
        nested_best=max(distinct,key=lambda e:(e[1].get('nested_items',0),-e[1]['sheet_mm2']))
        if nested_best[1].get('nested_items',0)>0:
            add('Максимум вложений в отверстия / Most nested parts',nested_best[1],nested_best[0])
    multi=max(eligible,key=lambda x:(m(x)['top_rectangles_total_mm2'],m(x)['largest_connected_mm2'], -x[1]['sheet_mm2']))
    add('Несколько пригодных остатков / Multiple remnants',multi[1],multi[0])
    connected=max(eligible,key=lambda x:(m(x)['largest_connected_mm2'],m(x)['top_rectangles_total_mm2']))
    add('Крупнейшая свободная область / Connected remnant',connected[1],connected[0])
    one=max(eligible,key=lambda x:(m(x)['largest_rect_mm2'],m(x)['top_rectangles_total_mm2']))
    add('Один большой прямоугольник / Largest rectangle',one[1],one[0])
    freeform=max(eligible,key=lambda x:(m(x)['mixed_shapes_total_mm2'],m(x)['usable_free_mm2'],-x[1]['sheet_mm2']))
    add('Прямоугольники + круги / Mixed shape remnants',freeform[1],freeform[0])
    irregular=max(eligible,key=lambda x:(m(x).get('compound_largest_mm2',0),
                m(x)['freeform_largest_mm2'],m(x)['freeform_top3_mm2'],-x[1]['sheet_mm2']))
    add('Цельный остаток сложной формы / Irregular offcut',irregular[1],irregular[0])
    # Keep every input, including manual layouts and previous catalog selections:
    # it must never disappear merely because the heuristic scored it poorly.
    for label,lay in seeds:
        add(('Ручная раскладка / Manual: ' if 'руч' in label.lower() or 'manual' in label.lower()
             else 'Исходная раскладка / Original: ')+label,lay,label)
    # Retain additional diverse layouts, without unbounded session memory.
    for label,lay in sorted(eligible,key=lambda x:(-m(x)['top_rectangles_total_mm2'],x[1]['sheet_mm2'])):
        if len(winners)>=8:break
        add('Альтернативная раскладка / Alternative',lay,label)
    return winners
