"""Reproducible 2D DXF cost / nesting / toolpath analysis without web browser."""
import argparse
from pathlib import Path
from sheet_nesting import parse_sheet_dxf
from nesting_advanced import advanced_nest
from toolpath2d import plan_toolpaths
from process_cost import MATERIALS,TECHNIQUES,default_params,cost_quote,suitability
from reporting import build_export


def main(argv=None):
    p=argparse.ArgumentParser(description='2D DXF nesting + advisory toolpath + EUR estimate')
    p.add_argument('dxf',nargs='+',type=Path)
    p.add_argument('--material',choices=list(MATERIALS),default='S235JR')
    p.add_argument('--method',choices=list(TECHNIQUES),default='Laser')
    p.add_argument('--thickness',type=float,default=3)
    p.add_argument('--quantity',type=int,default=1)
    p.add_argument('--unit',choices=['auto','mm','cm','m','in'],default='auto')
    p.add_argument('--join-tolerance',type=float,default=0.)
    p.add_argument('--width',type=float)
    p.add_argument('--height',type=float)
    p.add_argument('--gap',type=float,default=3.)
    p.add_argument('--margin',type=float,default=5.)
    p.add_argument('--quality',choices=['fast','balanced','thorough','precision'],default='balanced')
    p.add_argument('--hole-nesting',dest='hole_nesting',action='store_true',default=True,
                   help='Allow nesting inside holes and open concave cutouts (default)')
    p.add_argument('--no-hole-nesting',dest='hole_nesting',action='store_false',
                   help='Disable nesting inside holes and open concave cutouts')
    p.add_argument('--no-preserve-remnants',dest='preserve_remnants',action='store_false',default=True,
                   help='Disable largest rectangular offcut priority on fixed stock')
    p.add_argument('--feed',type=float)
    p.add_argument('--kerf',type=float)
    p.add_argument('--lead',type=float)
    p.add_argument('--price-kg',type=float)
    p.add_argument('--eur-hour',type=float)
    p.add_argument('--out',type=Path,default=Path('2D_Ergebnis.zip'))
    args=p.parse_args(argv)
    if (args.width is None)!=(args.height is None):p.error('--width and --height must be given together')
    pieces=[];warnings=[]
    for f in args.dxf:
        scan=parse_sheet_dxf(f.read_bytes(),f.name,units=args.unit,join_tolerance_mm=args.join_tolerance)
        for part in scan.pieces:
            part.name=f'{f.stem}/{part.name}'
            pieces.append(part)
        warnings.extend(f'{f.name}: {w}' for w in scan.issues)
    if not pieces:raise SystemExit('No closed 2D contours found')
    quantities={item.name:args.quantity for item in pieces}
    layout=advanced_nest(pieces,quantities,stock_width=args.width,stock_height=args.height,
         auto=args.width is None,quality=args.quality,hole_nesting=args.hole_nesting,
         gap=args.gap,margin=args.margin,preserve_remnants=args.preserve_remnants)
    params=default_params(args.method)
    if args.feed is not None:params.feed_mm_min=args.feed
    if args.kerf is not None:params.kerf_mm=args.kerf
    if args.lead is not None:params.lead_mm=args.lead
    path=plan_toolpaths(layout,params)
    tech=TECHNIQUES[args.method]
    quote=cost_quote(layout,path,params,thickness_mm=args.thickness,
       material=args.material,eur_kg=(args.price_kg if args.price_kg is not None else MATERIALS[args.material]['eur_kg']),
       hourly_eur=(args.eur_hour if args.eur_hour is not None else tech['eur_h']),
       consumables_eur_h=tech['consumables_eur_h'],setup_min=tech['setup'])
    suitable,note=suitability(args.method,args.material,args.thickness)
    if not suitable:warnings.append('Material-method-thickness compatibility: '+note)
    warnings+=path['warnings']
    project={'files':[str(p) for p in args.dxf], 'material':args.material,
             'method':args.method,'thickness_mm':args.thickness,
             'warnings':warnings,'units':args.unit,'verified_material_method':suitable,
             'preserve_remnants':args.preserve_remnants}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_bytes(build_export(layout,path,quote,project))
    print(f"{layout['items']} parts | {layout['sheet_count']} sheets | utilization {layout['utilization']:.1f}%")
    print(f"Cut {path['cut_length_mm']/1000:.2f} m, {path['pierces']} starts, machine time {quote['total_min']:.2f} min")
    print(f"Preliminary net offer {quote['offer_eur']:.2f} EUR | Saved to: {args.out}")
    print(f"Hybrid: {layout.get('hybrid_engine', 'baseline')} | evaluated {layout.get('hybrid_evaluated', 0)} alternatives")
    if args.width is not None:
        from remnant_optimization import remnant_quality
        quality=remnant_quality(layout)
        if quality:print(f"Conservative largest rectangular offcut: {max(q['area_mm2'] for q in quality):.0f} mm2")
    for warning in warnings:print('CHECK:',warning)

if __name__=='__main__':main()
