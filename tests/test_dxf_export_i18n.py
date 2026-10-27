"""Regression tests for the v0.9 editable DXF export and language selector."""
import io
import zipfile
import tempfile
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon

from sheet_nesting import CutPiece,parse_sheet_dxf
from nesting_advanced import advanced_nest
from dxf_export import export_layout_dxf
from reporting import build_export
from i18n import ROWS, LANGUAGES, t, method_label


def _open(blob):
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'nesting.dxf'
        path.write_bytes(blob)
        return ezdxf.readfile(path)


def _sq(name,size):
    return CutPiece(name, Polygon([(0,0),(size,0),(size,size),(0,size)]),layer='0')


def test_dxf_has_editable_closed_contours_and_mm_units():
    layout=advanced_nest([_sq('a',20)],auto=False,stock_width=100,stock_height=100)
    doc=_open(export_layout_dxf(layout))
    assert doc.dxfversion == 'AC1024'  # AutoCAD 2010 / commonly supported
    assert doc.header['$INSUNITS']==4
    layers={e.dxf.layer for e in doc.modelspace()}
    assert 'SHEET_BOUNDARY' in layers
    assert 'PART_S001_0001' in layers
    assert 'PART_LABELS' in layers
    polylines=list(doc.modelspace().query('LWPOLYLINE'))
    assert all(e.closed for e in polylines)
    assert len(polylines)==2  # sheet and part
    part=[e for e in polylines if e.dxf.layer.startswith('PART_')][0]
    assert len(part.get_points('xy'))==4


def test_dxf_roundtrip_preserves_nesting_holes_and_areas():
    ring=CutPiece('ring',Polygon([(0,0),(120,0),(120,120),(0,120)],
         holes=[[(20,20),(100,20),(100,100),(20,100)]]),'A',1)
    small=_sq('inside',35)
    layout=advanced_nest([ring,small],auto=False,stock_width=130,stock_height=130,
                         gap=3,margin=5,hole_nesting=True)
    assert layout['nested_items']==1
    data=export_layout_dxf(layout)
    rescanned=parse_sheet_dxf(data,'nesting.dxf')
    assert len(rescanned.pieces)==2
    assert sorted(p.holes for p in rescanned.pieces)==[0,1]
    assert sorted(p.area_mm2 for p in rescanned.pieces)==pytest.approx(
        sorted(p['shape'].area for p in layout['placements']))
    # The exported sheet frame is not imported as a fictitious third part.
    assert all(not p.layer.startswith('SHEET') for p in rescanned.pieces)


def test_multisheet_export_combined_and_individual():
    layout=advanced_nest([_sq('large',50)],{'large':3},auto=False,
                         stock_width=70,stock_height=70,margin=5,gap=3)
    assert layout['sheet_count']==3
    merged=_open(export_layout_dxf(layout,include_labels=False))
    parts=list(merged.modelspace().query('LWPOLYLINE'))
    assert len(parts)==6  # 3 outlines and 3 borders
    frames=[e for e in parts if e.dxf.layer=='SHEET_BOUNDARY']
    assert len(frames)==3
    starts=[min(p[0] for p in entity.get_points('xy')) for entity in frames]
    assert starts == sorted(starts)
    assert starts[1]-starts[0]==170.0  # width 70 + horizontal spacing 100
    for i in range(3):
        individual=export_layout_dxf(layout,i,include_labels=False)
        readback=parse_sheet_dxf(individual,f'sheet{i}.dxf')
        assert len(readback.pieces)==1
        doc=_open(individual)
        assert len(doc.modelspace().query('LWPOLYLINE'))==2
    with pytest.raises(ValueError):
        export_layout_dxf(layout,3)


def test_result_zip_contains_dxf_for_every_sheet_and_combined():
    from toolpath2d import plan_toolpaths
    from process_cost import default_params,cost_quote
    layout=advanced_nest([_sq('x',20)],auto=False,stock_width=50,stock_height=50)
    params=default_params('Laser')
    plan=plan_toolpaths(layout,params)
    quote=cost_quote(layout,plan,params,thickness_mm=3,material='S235JR',eur_kg=2,
                    hourly_eur=50,consumables_eur_h=7)
    report=build_export(layout,plan,quote,{'name':'example'})
    with zipfile.ZipFile(io.BytesIO(report)) as z:
        for path in ('Nesting_All_Sheets.dxf','Nesting_Sheet_1.dxf','DXF_README.txt'):
            assert path in z.namelist()
        doc=_open(z.read('Nesting_All_Sheets.dxf'))
        assert doc.header['$INSUNITS']==4


def test_all_language_keys_complete_and_method_labels_translated():
    assert set(LANGUAGES.values())=={'ru','de','en'}
    assert all(len(v)==3 and all(v) for v in ROWS.values())
    assert t('tab_export','ru')=='📥 Экспорт DXF'
    assert t('tab_export','de')=='📥 DXF-Export'
    assert t('tab_export','en')=='📥 Export DXF'
    assert method_label('Wasserstrahl','en')=='Waterjet cutting'
    assert 'DXF' in t('export_archive','de')
