"""v2.2 regressions: freeform scraps, export, desktop logging, variant roundtrip."""
from __future__ import annotations
import io
import json
import re
import sys
import types
import zipfile
from shapely.geometry import box
from shapely.ops import unary_union
from nesting_advanced import _summarize
from optimizer_v15 import _validate,_summary
from dxf_export import export_layout_dxf
from irregular_remnants import irregular_remnants,irregular_remnants_dxf,irregular_remnants_json
from svg_preview import svg_sheet
from variant_storage import archive_variants,load_variants
from layout_editor import update_layout,layout_fingerprint
from remnants import import_remnant


def layout():
    items=[{'name':'left','shape':box(0,0,70,45),'x':0,'y':0,
            'angle':0,'sheet_index':0}]
    return _summarize(items,[(110,110)],0,2,'v2.2 freeform')


def test_freeform_l_shape_is_genuinely_polygon_and_not_a_rectangle():
    lay=layout()
    parts=irregular_remnants(lay,kerf_mm=0,min_neck_mm=0,min_area_mm2=20)
    assert len(parts)==1
    geom=parts[0]['polygon']
    assert geom.area>8000
    assert geom.area < geom.envelope.area
    assert geom.is_valid
    assert box(75,10,105,100).within(geom)
    assert not geom.intersects(box(5,5,65,40))
    assert 'F1:' in svg_sheet(lay,None,highlight_offcut='irregular',irregular_candidates=parts)


def test_min_neck_filters_thin_strip_and_all_parts_remain_outside():
    lay=layout()
    small=irregular_remnants(lay,kerf_mm=1,min_neck_mm=0,min_area_mm2=10)
    wide=irregular_remnants(lay,kerf_mm=1,min_neck_mm=9,min_area_mm2=10)
    assert small and wide
    assert sum(r['area_mm2'] for r in wide)<=sum(r['area_mm2'] for r in small)+1e-5
    filled=unary_union([r['polygon'] for r in wide])
    assert filled.intersection(lay['placements'][0]['shape']).area < 1e-6
    assert all(r['polygon'].is_valid for r in wide)
    assert not irregular_remnants(lay,min_neck_mm=200,min_area_mm2=0)


def test_irregular_export_roundtrip_retains_polygon():
    polygons=irregular_remnants(layout(),kerf_mm=0,min_neck_mm=0)
    dxf=irregular_remnants_dxf(polygons)
    import ezdxf
    doc=ezdxf.read(io.StringIO(dxf.decode()))
    assert len(doc.modelspace().query('LWPOLYLINE')) >= len(polygons)
    blob=irregular_remnants_json(polygons,material='S235',thickness_mm=5)
    obj=json.loads(blob)
    assert obj['format']=='CAD2D_REMNANTS_1'
    assert obj['material']=='S235'
    restored,info=import_remnant(blob,0)
    assert abs(restored.area-polygons[0]['polygon'].area)<.01


def test_selected_manual_variant_remains_separate_and_exported():
    base=layout()
    original={'id':'original','name':'A','layout':base,'metrics':_summary(base,grid=5)}
    changed=update_layout(base,{'fingerprint':layout_fingerprint(base),
      'placements':[{'index':0,'x':7,'y':8,'angle':0,'sheet_index':0}]})
    assert _validate(changed)
    second={'id':'manual','name':'B','layout':changed,'metrics':_summary(changed,grid=5)}
    archive=archive_variants([original,second],'job_v22')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipf:
        assert 'Variant_01.dxf' in zipf.namelist()
        assert 'Variant_02.dxf' in zipf.namelist()
        assert zipf.read('Variant_01.dxf') != zipf.read('Variant_02.dxf')
    decoded=load_variants(archive,base,'job_v22')
    assert len(decoded)==2
    assert abs(decoded[0]['layout']['placements'][0]['shape'].bounds[0])<1e-5
    assert abs(decoded[1]['layout']['placements'][0]['shape'].bounds[0]-7)<1e-5


def test_desktop_log_file_name_and_error_logging(tmp_path,monkeypatch):
    import desktop_launcher
    monkeypatch.setattr(desktop_launcher,'executable_directory',lambda:tmp_path)
    filename=desktop_launcher.log_file()
    assert re.fullmatch(r'\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d-\d{6}\.txt',filename.name)
    assert filename.exists()
    # Stub the server so startup and capture behavior are testable without Streamlit.
    mock_bootstrap=types.ModuleType('streamlit.web.bootstrap')
    mock_bootstrap.run=lambda *a,**kw: None
    mock_web=types.ModuleType('streamlit.web')
    mock_web.bootstrap=mock_bootstrap
    mock_st=types.ModuleType('streamlit')
    mock_st.web=mock_web
    monkeypatch.setitem(sys.modules,'streamlit',mock_st)
    monkeypatch.setitem(sys.modules,'streamlit.web',mock_web)
    monkeypatch.setitem(sys.modules,'streamlit.web.bootstrap',mock_bootstrap)
    monkeypatch.setenv('CAD2D_LOG_PATH',str(filename))
    monkeypatch.setattr(desktop_launcher,'browser_when_ready',lambda port: None)
    oldcwd=__import__('os').getcwd()
    try:
        desktop_launcher.main()
    finally:
        __import__('os').chdir(oldcwd)
    assert 'CAD Cost Analyzer 2D desktop' in filename.read_text(encoding='utf-8')
