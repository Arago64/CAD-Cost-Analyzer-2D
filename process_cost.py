"""Parametric rough production quotes; all feed/prices are editable illustrative inputs."""
from __future__ import annotations
from dataclasses import dataclass
from toolpath2d import CuttingParameters,time_estimate

MATERIALS={
    'S235JR': {'density':7850,'eur_kg':1.90,'group':'carbon'},
    'S355': {'density':7850,'eur_kg':2.20,'group':'carbon'},
    'Edelstahl 1.4301': {'density':7900,'eur_kg':5.50,'group':'stainless'},
    'Edelstahl 1.4404': {'density':8000,'eur_kg':6.50,'group':'stainless'},
    'Aluminium EN AW-5754': {'density':2660,'eur_kg':5.00,'group':'aluminum'},
    'Aluminium EN AW-6082': {'density':2700,'eur_kg':6.00,'group':'aluminum'},
    'Kupfer': {'density':8960,'eur_kg':10.00,'group':'copper'},
    'Messing': {'density':8500,'eur_kg':8.00,'group':'copper'},
    'Kunststoff': {'density':1200,'eur_kg':4.00,'group':'plastic'},
}
# Ranges are indicative screening values, NOT equipment capabilities or DIN rules.
TECHNIQUES={
    'Laser': {'groups':{'carbon':(0.5,25),'stainless':(0.5,20),'aluminum':(0.5,15),'copper':(0.5,6)},
              'feed':2000.,'kerf':0.3,'pierce':2.,'setup':10.,'eur_h':95.,'consumables_eur_h':9.,'description':'Schnell, präzise, ggf. wärmebeeinflusste Zone'},
    'Plasma': {'groups':{'carbon':(2,60),'stainless':(2,35),'aluminum':(2,35)},
              'feed':1200.,'kerf':1.8,'pierce':3.,'setup':12.,'eur_h':80.,'consumables_eur_h':17.,'description':'Dickeres Metall, größere Schnittfuge und Wärmeeinfluss'},
    'Wasserstrahl': {'groups':{'carbon':(0.5,100),'stainless':(0.5,100),'aluminum':(0.5,100),'copper':(0.5,80),'plastic':(0.5,80)},
              'feed':300.,'kerf':0.9,'pierce':8.,'setup':15.,'eur_h':115.,'consumables_eur_h':34.,'description':'Kaltverfahren; abrasive/maschinenspezifische Parameter nötig'},
    'Autogen': {'groups':{'carbon':(6,200)},
              'feed':350.,'kerf':2.5,'pierce':10.,'setup':18.,'eur_h':65.,'consumables_eur_h':16.,'description':'Vorwiegend unlegierter Stahl, wärmebeeinflusster Rand'},
    'CNC Konturfräsen': {'groups':{'carbon':(1,80),'stainless':(1,60),'aluminum':(1,100),'copper':(1,60),'plastic':(1,100)},
              'feed':550.,'kerf':6.,'pierce':0.,'setup':25.,'eur_h':90.,'consumables_eur_h':13.,'description':'Mehrere Z-Zustellungen; Aufspannung / Spannmittel entscheidend'},
}


def suitability(method,material,thickness_mm):
    if method not in TECHNIQUES or material not in MATERIALS:
        return False,'Material / Verfahren unbekannt'
    group=MATERIALS[material]['group'];ranges=TECHNIQUES[method]['groups']
    if group not in ranges:return False,'Werkstoffgruppe nicht im Anwendungsbereich dieser Voreinstellung'
    lo,hi=ranges[group]
    if not(lo<=thickness_mm<=hi):return False,f'Dicke außerhalb der illustrativen Spanne ({lo:g}–{hi:g} mm)'
    return True,'vorläufig geeignet; Maschine und Schnittqualität prüfen'


def default_params(method):
    tech=TECHNIQUES[method]
    return CuttingParameters(method=method,kerf_mm=tech['kerf'],
        feed_mm_min=tech['feed'],pierce_s=tech['pierce'],
        lead_mm=tech['kerf']*3 if method!='CNC Konturfräsen' else 8.,
        stepdown_mm=2. if method=='CNC Konturfräsen' else 100.,
        rapid_mm_min=10000.,pass_setup_s=1. if method=='CNC Konturfräsen' else 0.)


def cost_quote(layout,plan,params,*,thickness_mm,material,eur_kg,
              hourly_eur,consumables_eur_h=0.,setup_min=10.,overhead_pct=15.,
              markup_pct=10.,electricity_eur_h=0.,scrap_credit_eur_kg=0.):
    for key,value in locals().copy().items():
        if key in ('thickness_mm','eur_kg','hourly_eur','consumables_eur_h','setup_min',
                   'overhead_pct','markup_pct','electricity_eur_h','scrap_credit_eur_kg') and value<0:
            raise ValueError(f'Kostenparameter {key} darf nicht negativ sein')
    if thickness_mm==0:raise ValueError('Dicke muss positiv sein')
    if material not in MATERIALS:raise ValueError('Werkstoff nicht bekannt')
    timing=time_estimate(plan,params,thickness_mm)
    rho=MATERIALS[material]['density']
    sheet_kg=layout['sheet_mm2']*thickness_mm*rho/1e9
    good_kg=layout['net_mm2']*thickness_mm*rho/1e9
    scrap_kg=max(0.,sheet_kg-good_kg)
    metal=sheet_kg*eur_kg
    scrap_credit=scrap_kg*scrap_credit_eur_kg
    operating_min=timing['process_min']
    machine=(operating_min+setup_min)/60*hourly_eur
    consumables=operating_min/60*consumables_eur_h
    electricity=operating_min/60*electricity_eur_h
    direct=max(0.,metal+machine+consumables+electricity-scrap_credit)
    overhead=direct*overhead_pct/100.
    manufacturing=direct+overhead
    offer=manufacturing*(1+markup_pct/100.)
    return {**timing,'sheet_kg':sheet_kg,'parts_kg':good_kg,'scrap_kg':scrap_kg,
        'material_eur':metal,'scrap_credit_eur':scrap_credit,
        'machine_eur':machine,'consumables_eur':consumables,'electricity_eur':electricity,
        'overhead_eur':overhead,'direct_eur':direct,
        'manufacturing_eur':manufacturing,'offer_eur':offer,
        'per_part_eur':offer/layout['items'],
        'setup_min':setup_min,'total_min':operating_min+setup_min}
