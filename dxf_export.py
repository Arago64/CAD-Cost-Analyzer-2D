"""Export finalized polygon-nesting positions to an editable AutoCAD-compatible DXF.

The exported geometry represents the MATERIAL BOUNDARIES, not cutter-offset
paths or executable CAM operations. All coordinates and sheet sizes are mm.
The input parser may have tessellated arcs and circles; DXF contours are
therefore closed LWPOLYLINEs representing that polygonal approximation.
One PART_Snnn_nnnn layer is created per placed part, with its outer and inner
contours on the same layer, allowing DXF re-import with holes preserved.
"""
from __future__ import annotations

from io import StringIO
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
import ezdxf

LAYER_COLORS = {
    'SHEET_BOUNDARY': 8,
    'PART_LABELS': 3,
    'SHEET_LABELS': 6,
}


def _new_drawing():
    doc = ezdxf.new('R2010', setup=False)
    doc.header['$INSUNITS'] = 4   # millimeters
    doc.header['$MEASUREMENT'] = 1  # metric drawing
    doc.header['$LUNITS'] = 2       # decimal units
    for name, color in LAYER_COLORS.items():
        doc.layers.new(name, dxfattribs={'color': color})
    return doc


def _add_ring(msp, ring, layer, shift_x=0.0, shift_y=0.0):
    xy = [(float(x) + shift_x, float(y) + shift_y) for x, y in list(ring.coords)[:-1]]
    # Closed flag is important: do not replace the physical boundary by an open line.
    if len(xy) < 3:
        raise ValueError('DXF export: ring requires at least three points')
    msp.add_lwpolyline(xy, format='xy', close=True, dxfattribs={'layer': layer})


def _add_sheet(msp, sheet, placements, shift_x=0.0, shift_y=0.0, *, include_labels=True, stock_outline=None):
    w, h = float(sheet['width_mm']), float(sheet['height_mm'])
    if w <= 0 or h <= 0:
        raise ValueError('Invalid sheet dimensions')
    # Frame / irregular remnant outline. Non-manufacturing layer only.
    if stock_outline is None:
        msp.add_lwpolyline([(shift_x, shift_y), (shift_x+w, shift_y),
                            (shift_x+w, shift_y+h), (shift_x, shift_y+h)],
                           close=True, dxfattribs={'layer': 'SHEET_BOUNDARY'})
    else:
        _add_ring(msp,stock_outline.exterior,'SHEET_BOUNDARY',shift_x,shift_y)
        for opening in stock_outline.interiors:
            _add_ring(msp,opening,'SHEET_BOUNDARY',shift_x,shift_y)
    if include_labels:
        msp.add_text('SHEET %d (%g x %g mm)' % (sheet['index']+1, w, h),
                     dxfattribs={'layer': 'SHEET_LABELS', 'height': max(2., min(w, h)/70.),
                                 'insert': (shift_x, shift_y+h+max(3., h*.015))})
    for counter, part in enumerate(placements, 1):
        polygon = part['shape']
        if not isinstance(polygon, Polygon) or polygon.is_empty or not polygon.is_valid:
            raise ValueError('Unsupported or invalid polygon in DXF export: '+part['name'])
        polygon = orient(polygon, sign=1.0)
        # A unique layer per part is intentional: the importer groups outer and
        # inner loops by layer, so its holes survive a DXF round-trip. It also
        # prevents a part nested in another part's hole being merged into it.
        layer = f'PART_S{sheet["index"]+1:03d}_{counter:04d}'
        if layer not in msp.doc.layers:
            msp.doc.layers.new(layer, dxfattribs={'color': 7})
        _add_ring(msp, polygon.exterior, layer, shift_x, shift_y)
        for hole in polygon.interiors:
            _add_ring(msp, hole, layer, shift_x, shift_y)
        if include_labels:
            point = polygon.representative_point()  # point inside the metal, not inside a hole
            label = str(part['name'])[:100]
            text_size = max(1., min(5., min(w, h)/180.))
            msp.add_text(label, dxfattribs={'layer': 'PART_LABELS',
                                            'height': text_size,
                                            'insert': (point.x + shift_x, point.y + shift_y)})


def export_layout_dxf(layout: dict, sheet_index: int | None = None, *,
                      include_labels: bool = True, sheet_spacing_mm: float = 100.) -> bytes:
    """Return a standalone DXF: one sheet or all sheets side-by-side.

    With ``sheet_index=None``, sheets are translated in X by each preceding
    sheet width and the sheet spacing, to prevent geometry overlap in the
    combined DXF. Individual sheet export preserves original nesting XY.
    """
    sheets = layout['sheets']
    if sheet_index is not None and not (0 <= sheet_index < len(sheets)):
        raise ValueError('DXF export sheet index out of range')
    if sheet_spacing_mm < 0:
        raise ValueError('Negative sheet spacing')
    doc = _new_drawing()
    msp = doc.modelspace()
    x_offset = 0.0
    selected = sheets if sheet_index is None else [sheets[sheet_index]]
    for sheet in selected:
        ix = sheet['index']
        own = [p for p in layout['placements'] if p.get('sheet_index', 0) == ix]
        if not own:
            raise ValueError('Sheet has no parts: %s' % ix)
        _add_sheet(msp, sheet, own, shift_x=x_offset, include_labels=include_labels,
                   stock_outline=layout.get('stock_outline') if ix==0 else None)
        if sheet_index is None:
            x_offset += float(sheet['width_mm']) + sheet_spacing_mm
    s = StringIO()
    doc.write(s)
    return s.getvalue().encode('utf-8')
