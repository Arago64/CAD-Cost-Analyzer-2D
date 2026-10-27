from shapely.geometry import box
from nesting_advanced import _summarize
from optimizer_v15 import _validate
from material_priority import polish_layout, reusable_offcuts


def test_polish_reduces_auto_sheet_without_overlaps():
    rows=[{'name':'a','shape':box(5,5,25,25),'x':5,'y':5,'angle':0,'sheet_index':0},
          {'name':'b','shape':box(55,5,75,25),'x':55,'y':5,'angle':0,'sheet_index':0}]
    old=_summarize(rows,[(80,30)],5,2,'old')
    new=polish_layout(dict(old,auto=True),rounds=2)
    assert _validate(new)
    assert new['sheet_mm2']<old['sheet_mm2']
    assert new['items']==old['items']


def test_offcuts_top_k_threshold_and_valid_geometries():
    rows=[{'name':'a','shape':box(40,0,60,100),'x':40,'y':0,'angle':0,'sheet_index':0}]
    lay=_summarize(rows,[(100,100)],0,0,'offcut')
    saved=reusable_offcuts(lay,threshold_pct=10,count=2,kerf_mm=0,min_neck_mm=0)
    assert len(saved)==2
    assert [r['area_pct'] for r in saved]==[40,40]
    assert saved[0]['polygon'].intersection(saved[1]['polygon']).area==0
    assert reusable_offcuts(lay,threshold_pct=41,count=3,kerf_mm=0,min_neck_mm=0)==[]
