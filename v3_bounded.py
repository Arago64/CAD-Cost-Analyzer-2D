"""Isolated v3 subprocess: preserves the last valid Shapely layout on hard timeout.
No old search engine is imported or invoked by the v3 search driver.
"""
from __future__ import annotations
import os, pickle, subprocess, sys, tempfile, time
from pathlib import Path
from optimizer_v15 import _validate, _signature

ROOT=Path(__file__).resolve().parent


def _write(path, obj):
    tmp=path.with_suffix('.tmp')
    with open(tmp,'wb') as fp:pickle.dump(obj,fp,pickle.HIGHEST_PROTOCOL)
    os.replace(tmp,path)


def worker(request_path,result_path):
    with open(request_path,'rb') as f:payload=pickle.load(f)
    from nesting_v3 import optimize
    all_good=[];seen=set()
    def checkpoint(layout):
        if not _validate(layout):return
        key=_signature(layout)
        if key not in seen:
            seen.add(key);all_good.append(layout)
            # Copy existing verified layouts to disk at every improvement.
            _write(Path(result_path),{'layouts':all_good,'complete':False})
    kwargs=payload['input']; seed_layout=payload.get('seed_layout')
    results=optimize(kwargs['pieces'],kwargs['qty'],W=kwargs.get('W'),H=kwargs.get('H'),
          margin=kwargs['margin'],gap=kwargs['gap'],auto=kwargs['auto'],
          allow_multi=kwargs['multi_sheet'],stock_outline=kwargs['stock_outline'],
          hole_nesting=kwargs['hole_nesting'],quality=kwargs['quality'],
          budget=payload['budget'],seed_layout=seed_layout,
          on_checkpoint=checkpoint)
    _write(Path(result_path),{'layouts':results,'complete':True})


def _metrics(layout,full=True):
    if full:
        from optimizer_v15 import _summary
        return _summary(layout,grid=12)
    from remnant_portfolio import metrics_summary
    m=metrics_summary(layout,grid=10)
    m.update(mixed_shapes_total_mm2=0.,usable_free_mm2=0.,freeform_largest_mm2=0.,
             freeform_top3_mm2=0.,compound_largest_mm2=0.,compound_count=0)
    return m


def run(input_data,budget_s=18,seed_layout=None):
    """Killable nesting run. Parent computes reports only for <=3 finalized options."""
    budget=max(2.,float(budget_s));started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='cad_v3_') as td:
        root=Path(td);request=root/'input.pkl';out=root/'output.pkl'
        with request.open('wb') as f:
            pickle.dump({'input':input_data,'budget':budget,'seed_layout':seed_layout},f,pickle.HIGHEST_PROTOCOL)
        cmd=([sys.executable,'--v3-worker',str(request),str(out)] if getattr(sys,'frozen',False) else
             [sys.executable,str(ROOT/'v3_bounded.py'),'--worker',str(request),str(out)])
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        logpath=os.environ.get('CAD2D_LOG_PATH')
        log=open(logpath,'a',encoding='utf-8') if logpath else subprocess.DEVNULL
        timed_out=False
        try:
            try:
                done=subprocess.run(cmd,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                         cwd=ROOT,creationflags=flags,timeout=budget+4.)
            except subprocess.TimeoutExpired:
                done=None;timed_out=True
        finally:
            if logpath:log.close()
        if not out.exists():
            raise RuntimeError('No valid layout saved by v3 engine. Check source geometry or make the stock larger.')
        with out.open('rb') as f:store=pickle.load(f)
    from nesting_v3 import _score
    from optimizer_v15 import _validate
    layouts=[p for p in store['layouts'] if _validate(p)]
    if not layouts:raise RuntimeError('No legal layouts in v3 worker result')
    layouts=sorted(layouts,key=lambda l:_score(l,input_data['auto']))
    # Keep a compact, meaningful shortlist and preserve any manual seed.
    unique=[];seen=set()
    for l in layouts:
        key=_signature(l)
        if key not in seen:
            seen.add(key);unique.append(l)
        if len(unique)>=4:break
    variants=[]
    for index,layout in enumerate(unique):
        try:score=_metrics(layout,full=index<2)
        except Exception:score=_metrics(layout,full=False)
        variants.append({'id':f'variant_{index+1}',
               'name':('🏆 Best found / Лучший найденный' if index==0
                       else f'🔀 Nesting alternative {index+1}'),
               'layout':layout,'metrics':score,'origin':'v3'})
    info={'requested_seconds':budget,'elapsed_seconds':round(time.monotonic()-started,2),
          'timed_out':timed_out,'partial':not store['complete']}
    return variants,info

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise SystemExit(2)
    worker(sys.argv[2],sys.argv[3])
