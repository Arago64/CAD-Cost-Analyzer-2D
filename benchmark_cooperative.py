"""Compare source seeds with cooperative three-phase search, save checked DXF/SVG."""
from pathlib import Path
import argparse,csv,json
from benchmark_engine2 import scattered_fixture,seven_fixture
from nesting_engine2 import _compact_score, _validate
from nesting_orchestrator import cooperative_search, SearchReport
from dxf_export import export_layout_dxf
from sheet_nesting import parse_sheet_dxf
from svg_preview import svg_sheet


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=6.)
    parser.add_argument('--out',default='Benchmark_Cooperative')
    args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for case,seeds,auto in [('four_scattered',[scattered_fixture()],False),
                            ('seven_irregular',seven_fixture(),True)]:
        report=SearchReport()
        candidates=cooperative_search(seeds,auto,total_budget_s=args.seconds,report=report)
        metric=(lambda layout:layout['sheet_mm2']) if auto else (
            lambda layout:_compact_score(layout['placements'],layout['margin_mm']))
        initial=min(seeds,key=metric);best=min(candidates,key=metric)
        assert all(_validate(x['placements'],x['sheet_width_mm'],x['sheet_height_mm'],
                      x['margin_mm'],x['gap_mm']) for x in candidates)
        folder=out/case;folder.mkdir(exist_ok=True)
        for name,layout in [('original',initial),('selected',best)]:
            (folder/f'{name}.svg').write_text(svg_sheet(layout),encoding='utf-8')
            payload=export_layout_dxf(layout)
            (folder/f'{name}.dxf').write_bytes(payload)
            assert len(parse_sheet_dxf(payload).pieces)==len(layout['placements'])
        rows.append(dict(case=case,metric='sheet_area_mm2' if auto else 'occupied_envelope_mm2',
            before=round(metric(initial),2),after=round(metric(best),2),
            new_valid_candidates=report.candidates,phases=report.phases,
            actual_search_seconds=report.elapsed_seconds))
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    (out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
