"""Hard wall-clock bound for the WHOLE nesting job (including seed construction
and remnant/portfolio ranking), using a killable worker process.

For a timed-out worker, returns its most recent atomically saved checkpoint.
No completed layout is modified on the caller side. Works with CPython and
PyInstaller's desktop launcher on Windows.
"""
from __future__ import annotations
import os
import pickle
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _write_atomic(destination: Path, record):
    temporary = destination.with_name(destination.name + '.new')
    with temporary.open('wb') as fp:
        pickle.dump(record, fp, protocol=pickle.HIGHEST_PROTOCOL)
        fp.flush()
    os.replace(temporary, destination)


def _baseline(payload, deadline, output):
    from nesting_advanced import advanced_nest
    from optimizer_v15 import variant_pool
    args = dict(pieces=payload['pieces'], qtys=payload['qty'], stock_width=payload['W'],
                stock_height=payload['H'], margin=payload['margin'], gap=payload['gap'],
                auto=payload['auto'], hole_nesting=payload['hole_nesting'],
                multi_sheet=payload['multi_sheet'], stock_outline=payload['stock_outline'])
    # Cheap first legal solution; safeguard against losing all work if a later
    # heuristic or a Shapely operation overruns its requested budget.
    compact = advanced_nest(**args, quality='fast', preserve_remnants=False)
    seeds = [('Compact / Компактно', compact)]
    if payload['hole_nesting'] and time.monotonic()+0.3<deadline:
        from pocket_repair import cavity_repair
        repaired=cavity_repair(compact,auto=payload['auto'],deadline=min(deadline-.2,time.monotonic()+0.9))
        if repaired is not None:seeds.append(('Cavity-aware packing / Вложение в полости',repaired))
    if payload['stock_outline'] is None and time.monotonic() + 2 < deadline:
        offcut = advanced_nest(**args, quality='fast', preserve_remnants=True)
        seeds.append(('Remnants / Остатки', offcut))
        if payload['hole_nesting'] and time.monotonic()+0.2<deadline:
            repaired=cavity_repair(offcut,auto=payload['auto'],deadline=min(deadline-.2,time.monotonic()+0.8))
            if repaired is not None:seeds.append(('Remnant + cavity repair / Остатки и вложения',repaired))
    # Material-first finishing is applied to valid auto-stock layouts, not to
    # fixed stock. Keep originals as a fallback and never enlarge a sheet.
    if payload['auto'] and time.monotonic()+0.8<deadline:
        from material_priority import polish_layout
        for name,seed in list(seeds)[:2]:
            if time.monotonic()+0.5>=deadline:break
            optimized=polish_layout(dict(seed,auto=True),rounds=2)
            if optimized['sheet_mm2']+1e-6<seed['sheet_mm2']:
                seeds.append(('Material first / Platz sparen',optimized))
    # Save basic layouts BEFORE expensive portfolio geometry. The parent may
    # reconstruct a single safe fallback if the process is interrupted now.
    _write_atomic(output, {'kind':'seeds', 'seeds':seeds})
    if time.monotonic() + 1.5 < deadline:
        variants = variant_pool(seeds, payload['auto'], quality='fast', search=False)
        _write_atomic(output, {'kind':'variants','variants':variants,'partial':True})
    return seeds


def worker(payload_file, output_file):
    payload_file, output_file = Path(payload_file), Path(output_file)
    with payload_file.open('rb') as fp:
        payload=pickle.load(fp)
    deadline=time.monotonic()+float(payload['budget'])
    if payload.get('task')=='refine':
        source=payload['layout']
        _write_atomic(output_file, {'kind':'variants','variants':[
            {'id':'source','name':'Original','layout':source,'metrics':{},'origin':'source'}], 'partial':True})
        if source['sheet_count']==1 and len(source['placements'])<=8:
            from nesting_orchestrator import cooperative_search
            candidates=cooperative_search([source],payload['auto'],payload['quality'],
                mode=payload['search_mode'],total_budget_s=max(.1,deadline-time.monotonic()-1.0),
                allow_nesting=payload['hole_nesting'])
            result=[{'id':f'candidate_{i+1}','name':'Refined','layout':l,'metrics':{},'origin':'refined'}
                    for i,l in enumerate(candidates)]
            _write_atomic(output_file,{'kind':'variants','variants':result,'partial':False})
        return
    seeds=_baseline(payload, deadline, output_file)
    remaining=deadline-time.monotonic()
    if remaining<=4 or payload['quality']=='fast':
        return
    if not payload['engine2']:
        # Engine disabled: honour the user's chosen legacy search depth, still
        # inside the SAME killable worker and total time budget.
        from nesting_advanced import advanced_nest
        from optimizer_v15 import variant_pool
        args=dict(pieces=payload['pieces'],qtys=payload['qty'],stock_width=payload['W'],
             stock_height=payload['H'],margin=payload['margin'],gap=payload['gap'],
             auto=payload['auto'],hole_nesting=payload['hole_nesting'],
             multi_sheet=payload['multi_sheet'],stock_outline=payload['stock_outline'])
        refined=advanced_nest(**args,quality=payload['quality'],
                               preserve_remnants=payload['preserve_remnants'])
        extra=seeds+[('Legacy refined / Точный базовый раскрой',refined)]
        newer=variant_pool(extra,payload['auto'],quality='fast',search=False)
        _write_atomic(output_file,{'kind':'variants','variants':newer,'partial':False})
        return
    from optimizer_v15 import variant_pool
    # Reserve a few seconds for a finished portfolio: each engine phase also
    # uses its own cooperative timer. A hard parent kill still protects us.
    candidate_budget=max(.1,remaining-3.)
    checkpoint_ids=set()
    def checkpoint(layouts,phase):
        # Preserve the smallest sheet and other genuinely different layouts
        # after EACH full phase, not only after the entire multi-stage search.
        # We calculate verified metrics only for 1-2 new finalists; doing it
        # for every beam intermediate previously caused long pauses.
        from optimizer_v15 import _signature, _summary
        ordered=sorted(layouts,key=lambda x:(x['sheet_count'],x['sheet_mm2']))
        selected=[]
        for entry in (ordered[:2]+sorted(ordered,key=lambda x:x['sheet_width_mm'])[:1]):
            signature=_signature(entry)
            if signature not in checkpoint_ids:
                checkpoint_ids.add(signature)
                selected.append(entry)
        if not selected:return
        # Keep previous versions AND new phase results. Important when later
        # expensive phases are interrupted by the global hard deadline.
        with output_file.open('rb') as stream:
            previous=pickle.load(stream)
        previous_rows=previous.get('variants',[]) if previous['kind']=='variants' else []
        extra=[]
        for entry in selected[:2]:
            if time.monotonic()>deadline-1.:break
            score=_summary(entry,grid=8)
            extra.append({'id':f'phase_{phase}_{len(extra)+1}',
               'name':f'Поиск, этап {phase} / Stage {phase}', 'layout':entry,
               'metrics':score,'origin':'checkpoint'})
        if extra:
            # Small Pareto pool by different sheet sizes & freeform potential.
            allrows=previous_rows+extra
            unique={_signature(r['layout']):r for r in allrows}
            chosen=sorted(unique.values(),key=lambda r:r['layout']['sheet_mm2'])[:8]
            _write_atomic(output_file,{'kind':'variants','variants':chosen,'partial':True})
    variants=variant_pool(seeds,payload['auto'],quality=payload['quality'], search=True,
        engine2_enabled=True,hole_nesting=payload['hole_nesting'],
        search_mode=payload['search_mode'],search_budget_s=candidate_budget,
        progress=checkpoint)
    _write_atomic(output_file,{'kind':'variants','variants':variants,'partial':False})


def run_bounded_refine(layout,auto,quality,search_mode,budget_s,hole_nesting=True):
    rows,info=run_bounded_search({'task':'refine','layout':layout,'auto':auto,
        'quality':quality,'search_mode':search_mode,'hole_nesting':hole_nesting},budget_s)
    return [r['layout'] for r in rows],info


def worker_cli():
    if len(sys.argv)!=4 or sys.argv[1]!='--cad-search-worker':
        raise SystemExit('Usage: --cad-search-worker input.pkl result.pkl')
    worker(sys.argv[2],sys.argv[3])


def run_bounded_search(payload, budget_s=18.):
    """Return (variants, metadata). Terminates runaway GEOS/search work.

    Tiny overhead beyond the budget is required for process startup and reading
    one checkpoint. With single-sheet normal workloads this is sub-second.
    """
    from optimizer_v15 import variant_pool
    budget=max(2.,float(budget_s))
    with tempfile.TemporaryDirectory(prefix='cad2d_search_') as folder:
        location=Path(folder)
        request=location/'request.pkl'; result=location/'result.pkl'
        payload=dict(payload,budget=budget)
        with request.open('wb') as fp: pickle.dump(payload,fp,pickle.HIGHEST_PROTOCOL)
        if getattr(sys,'frozen',False):
            command=[sys.executable,'--cad-search-worker',str(request),str(result)]
        else:
            command=[sys.executable,str(ROOT/'bounded_search.py'),'--cad-search-worker',str(request),str(result)]
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        start=time.monotonic();timed_out=False
        logname=os.environ.get('CAD2D_LOG_PATH')
        # Preserve child stderr in the same application log when available.
        logfile=open(logname,'a',encoding='utf-8') if logname else subprocess.DEVNULL
        try:
            try:
                completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=logfile,
                    stderr=logfile,timeout=budget+1.5,cwd=ROOT,creationflags=creationflags)
            except subprocess.TimeoutExpired:
                timed_out=True
                completed=None
        finally:
            if logname: logfile.close()
        elapsed=time.monotonic()-start
        if not result.exists():
            status='timed out before baseline' if timed_out else ('worker failed: '+str(completed.returncode if completed else '?'))
            raise RuntimeError('No verified nesting layout available: '+status+'. See application log.')
        with result.open('rb') as fp:
            saved=pickle.load(fp)
        if saved['kind']=='seeds':
            # Rare checkpoint between first baseline and variant scoring.
            seeds=saved['seeds']
            # Limit return-side expense to a single seed on timeout.
            fallback=seeds[0][1]
            variants=[{'id':'variant_1','name':'Базовая раскладка / Baseline',
                'layout':fallback,'metrics':{
                    'sheets':fallback['sheet_count'], 'sheet_area_mm2':fallback['sheet_mm2'],
                    'largest_rect_mm2':0.,'top_rectangles_total_mm2':0.,
                    'largest_connected_mm2':0.,'fragment_count':0},
                'origin':'fallback'}]
        else:
            variants=saved['variants']
        info={'elapsed_seconds':round(elapsed,2),'timed_out':timed_out,
              'partial':bool(saved.get('partial',False)), 'requested_seconds':budget}
        return variants,info


if __name__=='__main__':worker_cli()
