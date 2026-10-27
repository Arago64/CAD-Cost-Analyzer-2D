"""Timeboxed secondary v3.1 decoder. A hard timeout leaves the fast v3 checkpoint."""
from __future__ import annotations
import os, pickle, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent


def worker(request,out):
    with open(request,'rb') as f: kw=pickle.load(f)
    from nesting_advanced import advanced_nest
    result=advanced_nest(kw['pieces'],kw['qty'],stock_width=kw['W'],stock_height=kw['H'],
                        margin=kw['margin'],gap=kw['gap'],auto=kw['auto'],
                        quality='balanced',hole_nesting=kw['hole_nesting'],
                        multi_sheet=kw['allow_multi'],stock_outline=None,
                        preserve_remnants=False)
    from optimizer_v15 import _validate
    if not _validate(result):return
    with open(out+'.tmp','wb') as f:pickle.dump(result,f,pickle.HIGHEST_PROTOCOL)
    os.replace(out+'.tmp',out)


def run(kwargs,seconds):
    """Return None if solver doesn't finish within its allocated sub-budget."""
    if seconds < 1.:return None
    with tempfile.TemporaryDirectory(prefix='cad_secondary_') as d:
        src=str(Path(d)/'in.pkl');dst=str(Path(d)/'out.pkl')
        with open(src,'wb') as f:pickle.dump(kwargs,f,pickle.HIGHEST_PROTOCOL)
        if getattr(sys,'frozen',False):
            cmd=[sys.executable,'--v31-balanced',src,dst]
        else:
            cmd=[sys.executable,str(ROOT/'v31_balanced_worker.py'),'--worker',src,dst]
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        try:
            subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,cwd=str(ROOT),creationflags=flags,
                           timeout=max(1.,float(seconds)))
        except (subprocess.TimeoutExpired, OSError):return None
        if not Path(dst).exists():return None
        try:
            with open(dst,'rb') as f:result=pickle.load(f)
            return result
        except (OSError,pickle.UnpicklingError,EOFError):return None


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise SystemExit(2)
    worker(sys.argv[2],sys.argv[3])
