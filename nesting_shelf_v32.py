"""Deterministic verified quick-start decoder for 2..50 irregular parts.

Uses geometry-conservative rectangular shelves solely as a baseline. Better
NFP/contact/cluster searches may supersede it, but workers always have a
checkpoint before expensive optimization starts.
"""
from __future__ import annotations
from math import sqrt
from shapely import affinity
from sheet_nesting import _orient
from nesting_advanced import _summarize, _tight_auto_result
from optimizer_v15 import _validate


def shelf_start(items,*, auto, W=None,H=None,margin=5.,gap=3., multi_sheet=False):
    if not items:return None
    oriented=[]
    for name,p in items:
        pair=[]
        for angle in (0,90):
            q=_orient(p,angle);pair.append((q.bounds[2],q.bounds[3],angle,q))
        oriented.append((name,pair))
    if auto:
        area=sum(min(a*b for a,b,*_ in pair) for _,pair in oriented)
        widest=max(min(a for a,*_ in pair) for _,pair in oriented)
        target=max(widest+2*margin, sqrt(area)*1.35+2*margin)
        trials=(target,target*1.25,target*.85)
    else:
        if W is None or H is None or W<=2*margin or H<=2*margin:return None
        trials=(float(W),)
    best=None
    for trial_W in trials:
        rows=[];sheets=[]
        sheet=0;x=margin;y=margin;shelf_h=0.;failed=False
        for name,pair in oriented:
            fits=[(hh,ww,ang,poly) for ww,hh,ang,poly in pair
                  if x+ww<=trial_W-margin+1e-7 and
                  (auto or y+hh<=H-margin+1e-7)]
            if fits:
                hh,ww,ang,poly=min(fits)
            else:
                x=margin;y+=shelf_h+gap;shelf_h=0.
                fits=[(hh,ww,ang,poly) for ww,hh,ang,poly in pair
                      if x+ww<=trial_W-margin+1e-7 and
                      (auto or y+hh<=H-margin+1e-7)]
                if not fits and not auto and multi_sheet:
                    sheet+=1;x=margin;y=margin;shelf_h=0.
                    fits=[(hh,ww,ang,poly) for ww,hh,ang,poly in pair
                          if x+ww<=trial_W-margin+1e-7 and
                          y+hh<=H-margin+1e-7]
                if not fits:
                    failed=True;break
                hh,ww,ang,poly=min(fits)
            geom=affinity.translate(poly,xoff=x,yoff=y)
            rows.append(dict(name=name,shape=geom,x=x,y=y,angle=ang,sheet_index=sheet))
            shelf_h=max(shelf_h,hh);x+=ww+gap
        if failed:continue
        if auto:
            moved,dims=_tight_auto_result(rows,margin)
            result=_summarize(moved,[dims],margin,gap,'v3.2 quick shelf checkpoint')
        else:
            result=_summarize(rows,[(W,H)]*(sheet+1),margin,gap,'v3.2 quick shelf checkpoint')
        if _validate(result):
            if best is None or (result['sheet_count'],result['sheet_mm2'])<(best['sheet_count'],best['sheet_mm2']):
                best=result
    return best
