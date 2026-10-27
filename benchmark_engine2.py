"""Repeatable Engine 2.0 benchmark on four scattered plates and seven DXF parts.

Run from application directory: python benchmark_engine2.py --out my_results
"""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from shapely import affinity
from shapely.geometry import box
from sheet_nesting import parse_sheet_dxf, _orient
from nesting_advanced import _summarize
from nesting_engine2 import search_engine2, _compact_score, _validate
from dxf_export import export_layout_dxf
from svg_preview import svg_sheet


def scattered_fixture():
    parts=[('A',5,5,70,40),('B',170,15,65,35),('C',40,140,25,70),('D',140,130,60,55)]
    rows=[dict(name=n,shape=box(x,y,x+w,y+h),x=x,y=y,angle=0,sheet_index=0)
          for n,x,y,w,h in parts]
    return _summarize(rows,[(260,230)],5.,3.,'scattered reference')


def seven_fixture():
    root=Path(__file__).parent
    pieces=parse_sheet_dxf((root/'examples/DEMO_7_PARTS_COMPACT_V12.dxf').read_bytes()).pieces
    by_name={p.name:p.polygon for p in pieces}
    entries=json.loads((root/'examples/ENGINE2_SEVEN_PART_REFERENCE.json').read_text(encoding='utf-8'))['variants']
    layouts=[]
    for variant in entries[:2]:
        rows=[]
        for p in variant['positions']:
            name=p['part_name'].split(' #')[0]
            ref=_orient(by_name[name],p['angle'])
            shape=affinity.translate(ref,xoff=p['x'],yoff=p['y'])
            rows.append(dict(name=p['part_name'],shape=shape,x=p['x'],y=p['y'],
                             angle=p['angle'],sheet_index=0))
        lay=_summarize(rows,[tuple(variant['dimensions'][0])],5.,3.,'v1.6 reference')
        assert _validate(rows,lay['sheet_width_mm'],lay['sheet_height_mm'],5.,3.)
        layouts.append(lay)
    return layouts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='Benchmark_Engine2')
    parser.add_argument('--seconds',type=float,default=8.)
    args=parser.parse_args()
    dest=Path(args.out);dest.mkdir(exist_ok=True,parents=True)
    reports=[]
    for label,seeds,auto in [('four_scattered',[scattered_fixture()],False),
                             ('seven_irregular',seven_fixture(),True)]:
        improved=search_engine2(seeds,auto,'balanced',time_budget_s=args.seconds)
        candidates=seeds+improved
        if auto:
            best=min(candidates,key=lambda x:x['sheet_mm2'])
            key='sheet_mm2';before=min(s['sheet_mm2'] for s in seeds);after=best['sheet_mm2']
        else:
            best=min(candidates,key=lambda x:_compact_score(x['placements'],5.))
            key='occupied_envelope_mm2';before=_compact_score(seeds[0]['placements'],5.)
            after=_compact_score(best['placements'],5.)
        reports.append(dict(example=label,metric=key,before_mm2=round(before,1),
                            after_mm2=round(after,1),improvement_percent=round((before-after)/before*100,2),
                            discovered_variants=len(improved)))
        for variant_name,layout in [('starting_layout',seeds[0]),('chosen_layout',best)]:
            assert _validate(layout['placements'],layout['sheet_width_mm'],
                             layout['sheet_height_mm'],layout['margin_mm'],layout['gap_mm'])
            folder=dest/label;folder.mkdir(exist_ok=True)
            (folder/(variant_name+'.svg')).write_text(svg_sheet(layout),encoding='utf8')
            (folder/(variant_name+'.dxf')).write_bytes(export_layout_dxf(layout))
            imported=parse_sheet_dxf((folder/(variant_name+'.dxf')).read_bytes())
            assert len(imported.pieces)==layout['items']
    with (dest/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=reports[0].keys());writer.writeheader();writer.writerows(reports)
    (dest/'report.json').write_text(json.dumps(reports,indent=2),encoding='utf8')
    print(json.dumps(reports,indent=2))

if __name__=='__main__':main()
