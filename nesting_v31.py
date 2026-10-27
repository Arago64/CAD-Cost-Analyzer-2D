"""Optional v3.1 hybrid improvement stage: manual-seeded contact basin hopping.

Combines the existing Shapely/GEOS geometry checks, v3 contact/NFP candidate
sampler and a new ruin/recreate search.  Does NOT assume rectangles or ignore
holes.  Every returned layout must pass the full independent validator.

No external binary, license, or server required; suitable for Windows Python.
"""
from __future__ import annotations
import random
from math import ceil
from time import monotonic
from shapely import affinity
from nesting_advanced import _summarize, _tight_auto_result
from sheet_nesting import _orient
from optimizer_v15 import _validate


def _parts_score(rows, *, auto, W, H, margin):
    """Cheap sub-mm search score; actual stock is separately verified/charged."""
    if not rows:
        return (0., 0., 0.)
    lo_x=min(r['shape'].bounds[0] for r in rows)
    lo_y=min(r['shape'].bounds[1] for r in rows)
    hi_x=max(r['shape'].bounds[2] for r in rows)
    hi_y=max(r['shape'].bounds[3] for r in rows)
    width=hi_x-lo_x; height=hi_y-lo_y
    if auto:
        # Work with real (unrounded) bounds, so sub-mm improvements can
        # accumulate before the manufactured blank drops by a whole millimetre.
        return (round((width+2*margin)*(height+2*margin),5),
                round(width+height,4),round(hi_x+hi_y,4))
    # For a fixed stock the paid sheet area does NOT change; put all effort into
    # recovering one uninterrupted right/top strip of reusable material.
    usable_width=W-2*margin;usable_height=H-2*margin
    largest=max(0., (W-margin-hi_x)*usable_height,
                (H-margin-hi_y)*usable_width,
                (lo_x-margin)*usable_height,
                (lo_y-margin)*usable_width)
    return (-round(largest,5),round(width*height,5),round(width+height,4))


def _geometry_ok(candidate, others, gap):
    a,b,c,d=candidate.bounds
    for p in others:
        other=p['shape'];x,y,z,t=other.bounds
        # If the gap-wide bounding rectangles are disjoint, skip GEOS entirely.
        if a>=z+gap-1e-7 or c<=x-gap+1e-7 or b>=t+gap-1e-7 or d<=y-gap+1e-7:
            continue
        if candidate.intersection(other).area>1e-7:
            return False
        if gap>0 and candidate.distance(other)<gap-1e-6:
            return False
    return True


def _rotate_options(name, original, current, step):
    """Keep exact current (possibly manually entered) angle, then new turns."""
    result=[(float(current['angle']),
             affinity.translate(current['shape'],
                xoff=-current['shape'].bounds[0],yoff=-current['shape'].bounds[1]))]
    seen={round(float(current['angle'])%360,4)}
    # Put near-angle rotations first: these typically allow fine interlocking
    # without destabilizing an almost-good hand placement.
    angles=list(range(0,360,step))
    angles.sort(key=lambda a:min((a-current['angle'])%360,(current['angle']-a)%360))
    for angle in angles:
        if round(angle%360,4) in seen:continue
        seen.add(round(angle%360,4))
        result.append((angle,_orient(original,angle)))
    return result


def _try_insert(name, source, current, others, *, W,H,margin,gap,auto,deadline,
                angle_step=30, angle_limit=10, sample_limit=125, rng=None, angle_candidates=None):
    """Find contact-positioned insertion; ALL checks use the full original shape."""
    from nesting_v3 import _positions
    from shapely.geometry import box
    if monotonic()>=deadline:return None
    shape_map=_rotate_options(name,source,current,angle_step)
    # Attempt the exact original angle before the rotations.
    angles=(shape_map[:1]+list(angle_candidates)) if angle_candidates is not None else (shape_map[:1]+shape_map[1:angle_limit])
    xmin=min((r['shape'].bounds[0] for r in others),default=margin)
    ymin=min((r['shape'].bounds[1] for r in others),default=margin)
    xmax=max((r['shape'].bounds[2] for r in others),default=margin)
    ymax=max((r['shape'].bounds[3] for r in others),default=margin)
    best=None
    for angle,ref in angles:
        if monotonic()>=deadline:break
        w,h=ref.bounds[2],ref.bounds[3]
        if w>W-2*margin+1e-7 or h>H-2*margin+1e-7:continue
        candidates=_positions(ref,others,W,H,margin,gap,True,deadline,nfp=False)
        # Critical: a manually selected floating position is not necessarily a
        # contact point. Always keep it as an admissible incumbent candidate.
        candidates.append((current['shape'].bounds[0],current['shape'].bounds[1]))
        # Include gap-aware vertex contacts and sheet corners but rank using the
        # eventual complete layout, not just this part's bottom-left position.
        def lower_bound(xy):
            x,y=xy
            if auto:
                bw=max(xmax,x+w)-min(xmin,x)
                bh=max(ymax,y+h)-min(ymin,y)
                return ((bw+2*margin)*(bh+2*margin), bw+bh, x+y)
            sx=max(xmax,x+w);sy=max(ymax,y+h)
            lx=min(xmin,x);ly=min(ymin,y)
            strip=max(0.,(W-margin-sx)*(H-2*margin),
                      (H-margin-sy)*(W-2*margin),
                      (lx-margin)*(H-2*margin),
                      (ly-margin)*(W-2*margin))
            return (-strip,(sx-lx)*(sy-ly),x+y)
        candidates=list(dict.fromkeys((round(x,5),round(y,5)) for x,y in candidates))
        candidates=[(x,y) for x,y in candidates if
                    margin-1e-6<=x<=W-margin-w+1e-6 and
                    margin-1e-6<=y<=H-margin-h+1e-6]
        candidates.sort(key=lower_bound)
        # Stratified sampling to preserve unusual interlocking contacts near
        # the ends of the ranking, rather than slicing only its first N points.
        short=candidates[:max(45,sample_limit*3//4)]
        if len(candidates)>len(short):
            stride=max(1,(len(candidates)-len(short))//max(1,sample_limit-len(short)))
            short+=candidates[len(short)::stride][:sample_limit-len(short)]
        for k,(x,y) in enumerate(short):
            if k%20==0 and monotonic()>=deadline:break
            # Compare cheap geometry first; only feasible and competitive
            # contacts go into GEOS intersection/distance calculations.
            candidate_score=lower_bound((x,y))
            if best is not None and candidate_score[0]>best[0][0]+1e-6:continue
            shape=affinity.translate(ref,xoff=x,yoff=y)
            if not _geometry_ok(shape,others,gap):continue
            row=dict(current,name=name,shape=shape,x=x,y=y,angle=float(angle)%360)
            true_score=_parts_score([*others,row],auto=auto,W=W,H=H,margin=margin)
            if best is None or true_score<best[0]:best=(true_score,row)
    return best


def _build_result(rows, seed, auto):
    margin=float(seed['margin_mm']);gap=float(seed['gap_mm'])
    if auto:
        normalized,dimensions=_tight_auto_result(rows,margin)
        return _summarize(normalized,[dimensions],margin,gap,
                          'v3.1 hybrid: NFP/contact search + manual-seeded ruin-recreate')
    sheets=[(s['width_mm'],s['height_mm']) for s in seed['sheets']]
    return _summarize(rows,sheets,margin,gap,
                      'v3.1 hybrid: NFP/contact search + manual-seeded ruin-recreate')


def improve_seed(seed, items, *, auto, deadline, quality='balanced', rng=None,
                 on_improved=None):
    """Back-compatible API routed to the v3.2 instance-safe implementation."""
    from nesting_v32 import improve_seed as improve_v32
    return improve_v32(seed, items, auto=auto, deadline=deadline,
                       quality=quality, rng=rng, on_improved=on_improved)
