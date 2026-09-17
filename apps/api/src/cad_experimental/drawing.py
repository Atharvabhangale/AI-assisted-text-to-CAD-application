"""A basic engineering drawing of the part that is currently built.

An MVP, and honest about it: four orthographic views, a title block, and
overall dimensions. It is **not** a standards-compliant drawing -- no hidden
lines, no section views, no tolerances, no GD&T, no dimension chains.

Where the numbers come from
---------------------------
Every dimension on the sheet is the executor's own measurement of the build
that succeeded, passed in as evidence. Nothing here measures geometry and
nothing here asks a model what a part is. If a quantity was not measured, the
sheet does not show it rather than estimating one.

Where the geometry comes from
-----------------------------
`CadBackend.project_edges`, which returns plain 2D polylines. This module
never touches a kernel, never imports FreeCAD or CadQuery, and would draw a
third engine's projections unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: The four views, and the direction each looks ALONG. Standard third-angle
#: naming: the "front" view looks along -Y at the XZ face.
VIEWS: Tuple[Tuple[str, Tuple[float, float, float]], ...] = (
    ("front", (0.0, -1.0, 0.0)),
    ("top", (0.0, 0.0, 1.0)),
    ("right", (1.0, 0.0, 0.0)),
    ("isometric", (1.0, -1.0, 1.0)),
)

#: Sheet geometry, in millimetres of paper.
SHEET_WIDTH = 420.0
SHEET_HEIGHT = 297.0
MARGIN = 12.0
TITLE_HEIGHT = 34.0
GUTTER = 14.0

#: Scales a drawing may choose from, largest first. A drawing states its
#: scale; it never silently stretches a view to fit.
SCALES: Tuple[float, ...] = (2.0, 1.0, 0.5, 0.2, 0.1, 0.05)


@dataclass(frozen=True)
class View:
    """One projected view, already placed on the sheet."""

    name: str
    polylines: Tuple[Tuple[Tuple[float, float], ...], ...]
    origin: Tuple[float, float]
    extent: Tuple[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "edges": len(self.polylines),
            "extent": list(self.extent),
        }


@dataclass(frozen=True)
class Drawing:
    """A sheet: its views, its dimensions, and what it says about itself."""

    part_name: str
    scale: float
    units: str
    views: Tuple[View, ...]
    dimensions: Tuple[Tuple[str, str], ...]
    backend: str
    svg: str
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "part_name": self.part_name,
            "scale": self.scale,
            "units": self.units,
            "backend": self.backend,
            "views": [view.to_dict() for view in self.views],
            "dimensions": [list(pair) for pair in self.dimensions],
            "notes": list(self.notes),
            "svg": self.svg,
        }


def _bounds(
    polylines: Sequence[Sequence[Tuple[float, float]]]
) -> Tuple[float, float, float, float]:
    xs = [x for line in polylines for x, _ in line]
    ys = [y for line in polylines for _, y in line]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def dimensions_from(measurement: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """The dimension table, read from measured evidence only.

    A quantity that was not measured is omitted. Nothing here derives a
    dimension from a projection, because a projection is a picture and the
    build is the authority.
    """
    rows: List[Tuple[str, str]] = []
    size = measurement.get("size") or []
    if len(size) == 3 and all(isinstance(v, (int, float)) for v in size):
        rows.append(("Overall length", f"{size[0]:g} mm"))
        rows.append(("Overall width", f"{size[1]:g} mm"))
        rows.append(("Overall height", f"{size[2]:g} mm"))
    volume = measurement.get("volume")
    if isinstance(volume, (int, float)):
        rows.append(("Volume", f"{volume:.2f} mm3"))
    for key, label in (("face_count", "Faces"), ("edge_count", "Edges"),
                       ("solid_count", "Solids")):
        value = measurement.get(key)
        if isinstance(value, int):
            rows.append((label, str(value)))
    return tuple(rows)


def choose_scale(extents: Sequence[Tuple[float, float]],
                 cell: Tuple[float, float]) -> float:
    """The largest listed scale at which every view still fits its cell."""
    width = max((w for w, _ in extents), default=1.0) or 1.0
    height = max((h for _, h in extents), default=1.0) or 1.0
    for scale in SCALES:
        if width * scale <= cell[0] and height * scale <= cell[1]:
            return scale
    return SCALES[-1]


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def build_drawing(
    backend: Any,
    shape: Any,
    *,
    part_name: str,
    measurement: Mapping[str, Any],
    units: str = "mm",
) -> Drawing:
    """Project the part four ways and lay the views out on one sheet.

    Raises whatever the backend raises when it cannot project -- a drawing
    that could not be produced is reported as such, never as an empty sheet.
    """
    projected: List[Tuple[str, Tuple[Tuple[Tuple[float, float], ...], ...]]] = []
    notes: List[str] = []
    for name, direction in VIEWS:
        polylines = backend.project_edges(shape, direction)
        if polylines:
            projected.append((name, polylines))
        else:
            notes.append(f"the {name} view projected no visible edges")

    if not projected:
        raise ValueError("the part projected no visible edges in any view")

    # Normalise each view to its own origin, then size the grid from the
    # largest so every view shares one scale -- a sheet whose views were
    # each scaled to fit would be unreadable as a drawing.
    normalised = []
    extents = []
    for name, polylines in projected:
        x0, y0, x1, y1 = _bounds(polylines)
        moved = tuple(
            tuple((x - x0, y - y0) for x, y in line) for line in polylines
        )
        normalised.append((name, moved))
        extents.append((x1 - x0, y1 - y0))

    columns = 2
    rows = (len(normalised) + columns - 1) // columns
    usable_w = SHEET_WIDTH - 2 * MARGIN
    usable_h = SHEET_HEIGHT - 2 * MARGIN - TITLE_HEIGHT
    cell = ((usable_w - GUTTER * (columns - 1)) / columns,
            (usable_h - GUTTER * (rows - 1)) / rows)
    scale = choose_scale(extents, cell)

    views: List[View] = []
    for index, ((name, polylines), extent) in enumerate(zip(normalised, extents)):
        column, row = index % columns, index // columns
        # Centre each view in its cell so the sheet reads evenly.
        ox = MARGIN + column * (cell[0] + GUTTER) + (cell[0] - extent[0] * scale) / 2
        oy = MARGIN + row * (cell[1] + GUTTER) + (cell[1] - extent[1] * scale) / 2
        views.append(View(name=name, polylines=polylines,
                          origin=(ox, oy), extent=extent))

    dimensions = dimensions_from(measurement)
    if not dimensions:
        notes.append("no measurements were available, so no dimensions are shown")

    svg = _render_svg(views, scale=scale, part_name=part_name, units=units,
                      dimensions=dimensions,
                      backend=getattr(backend, "name", "unknown"))
    return Drawing(part_name=part_name, scale=scale, units=units,
                   views=tuple(views), dimensions=dimensions,
                   backend=getattr(backend, "name", "unknown"),
                   svg=svg, notes=tuple(notes))


def _render_svg(
    views: Sequence[View], *, scale: float, part_name: str, units: str,
    dimensions: Sequence[Tuple[str, str]], backend: str,
) -> str:
    """The sheet, as SVG. Plain text out; nothing is executed anywhere."""
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_WIDTH}mm" '
        f'height="{SHEET_HEIGHT}mm" viewBox="0 0 {SHEET_WIDTH} {SHEET_HEIGHT}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<rect x="{MARGIN/2}" y="{MARGIN/2}" '
        f'width="{SHEET_WIDTH-MARGIN}" height="{SHEET_HEIGHT-MARGIN}" '
        'fill="none" stroke="#111" stroke-width="0.6"/>',
    ]

    for view in views:
        ox, oy = view.origin
        # SVG y grows downward; the model's y grows up. Flipped here so the
        # drawing reads the way the part sits.
        out.append(f'<g transform="translate({ox:.3f},{oy + view.extent[1]*scale:.3f}) '
                   f'scale({scale:.5f},{-scale:.5f})">')
        for line in view.polylines:
            points = " ".join(f"{x:.4f},{y:.4f}" for x, y in line)
            out.append(f'<polyline points="{points}" fill="none" '
                       f'stroke="#111" stroke-width="{0.35/scale:.4f}"/>')
        out.append("</g>")
        out.append(f'<text x="{ox:.2f}" y="{oy - 2:.2f}" font-family="monospace" '
                   f'font-size="4" fill="#333">{_escape(view.name.upper())}</text>')

    # --- title block ---------------------------------------------------
    ty = SHEET_HEIGHT - MARGIN / 2 - TITLE_HEIGHT
    tx = SHEET_WIDTH - MARGIN / 2 - 150.0
    out.append(f'<rect x="{tx}" y="{ty}" width="150" height="{TITLE_HEIGHT}" '
               'fill="none" stroke="#111" stroke-width="0.6"/>')
    lines = [
        ("PART", part_name),
        ("SCALE", f"{scale:g}:1" if scale >= 1 else f"1:{1/scale:g}"),
        ("UNITS", units),
        ("ENGINE", backend),
    ]
    for index, (key, value) in enumerate(lines):
        y = ty + 7 + index * 7
        out.append(f'<text x="{tx+3}" y="{y}" font-family="monospace" '
                   f'font-size="4" fill="#666">{_escape(key)}</text>')
        out.append(f'<text x="{tx+34}" y="{y}" font-family="monospace" '
                   f'font-size="4.5" fill="#111">{_escape(str(value))}</text>')

    # --- dimensions, from measured evidence only ------------------------
    dx, dy = MARGIN / 2 + 3, ty + 7
    out.append(f'<text x="{dx}" y="{dy - 2}" font-family="monospace" '
               'font-size="4" fill="#666">DIMENSIONS (measured)</text>')
    for index, (label, value) in enumerate(dimensions[:6]):
        y = dy + 5 + index * 5
        out.append(f'<text x="{dx}" y="{y}" font-family="monospace" '
                   f'font-size="4" fill="#111">'
                   f'{_escape(label)}: {_escape(value)}</text>')
    out.append("</svg>")
    return "\n".join(out)


__all__ = ["Drawing", "View", "VIEWS", "build_drawing", "dimensions_from",
           "choose_scale"]
