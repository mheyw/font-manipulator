"""Render shaped text from a (variable) font to an SVG of plain outlines.

HarfBuzz shapes the text (kerning, ligatures) at the requested axis values and draws each glyph
as it renders at that location, so the SVG matches the browser preview but needs no font.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def render_svg(font_bytes: bytes, lines: list[str], rows: list[tuple[str, dict[str, float]]],
               size_px: float = 120.0, line_height: float = 1.2, fill: str = "#000",
               padding_em: float = 0.25, label: bool = False) -> str:
    """rows = [(label, {axis_tag: value}), ...]; each row renders every line of text."""
    face = hb.Face(hb.Blob(font_bytes))
    font = hb.Font(face)
    upm = face.upem
    ascent = font.get_font_extents("ltr").ascender
    lh = upm * line_height
    pad = upm * padding_em

    groups = []
    minx, miny, maxx, maxy = 0.0, 0.0, 0.0, 0.0  # ink bounds in SVG coordinates (y down)
    y = ascent
    for row_label, loc in rows:
        font.set_variations(loc)
        row_paths = []
        for line in lines:
            buf = hb.Buffer()
            buf.add_str(line)
            buf.guess_segment_properties()
            hb.shape(font, buf, {"kern": True, "liga": True})
            x = 0.0
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
                pen = SVGPathPen(None, ntos=_fmt)
                font.draw_glyph_with_pen(info.codepoint, pen)
                d = pen.getCommands()
                if d:
                    gx, gy = x + pos.x_offset, y - pos.y_offset
                    row_paths.append(f'<path transform="translate({_fmt(gx)} {_fmt(gy)}) scale(1 -1)" d="{d}"/>')
                    ext = font.get_glyph_extents(info.codepoint)  # at the current variation
                    if ext is not None:
                        minx = min(minx, gx + ext.x_bearing)
                        maxx = max(maxx, gx + ext.x_bearing + ext.width)
                        miny = min(miny, gy - ext.y_bearing)
                        maxy = max(maxy, gy - ext.y_bearing - ext.height)
                x += pos.x_advance
            maxx = max(maxx, x)
            y += lh
        if label:
            ly = y - lh + upm * 0.28
            row_paths.append(f'<text x="0" y="{_fmt(ly)}" font-family="monospace" '
                             f'font-size="{_fmt(upm * 0.09)}" fill="#c00">{escape(row_label)}</text>')
            maxy = max(maxy, ly + upm * 0.03)
        groups.append(f'<g data-row="{escape(row_label)}">' + "".join(row_paths) + "</g>")
        y += upm * 0.15  # small gap between rows

    x0, y0 = minx - pad, miny - pad
    w, h = (maxx + pad) - x0, (maxy + pad) - y0
    scale = size_px / upm
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(w * scale)}" height="{_fmt(h * scale)}" '
            f'viewBox="{_fmt(x0)} {_fmt(y0)} {_fmt(w)} {_fmt(h)}"><g fill="{escape(fill)}">'
            + "".join(groups) + "</g></svg>")
