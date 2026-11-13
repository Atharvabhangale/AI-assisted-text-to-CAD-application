"""The selection every edge selector made before schema 1.1.1.

Since 1.1.1 a selector returns sharp edges only, so a seam can no longer reach
the engine's coverage invariant (rule E5) through a document. That invariant
is defence in depth, and these helpers provoke it directly: they reproduce the
old, unfiltered selection from the shape's own ``Edges()`` and substitute it
for ``select_edges`` inside the engine.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Tuple
from unittest import mock

from cad_core.edge_selection import (
    SEAM,
    edge_kind,
    is_straight_edge,
    line_direction,
)
from cad_core.model import EdgeSelector

#: Direction components, dimensionless.
_DIRECTION_TOLERANCE = 1e-9


def seam_edges(shape: Any) -> Tuple[Any, ...]:
    """Every edge of ``shape`` the kernel reports and the selector excludes."""
    return tuple(edge for edge in shape.Edges() if edge_kind(shape, edge) == SEAM)


def unfiltered_edges(shape: Any, selector: EdgeSelector) -> Tuple[Any, ...]:
    """What ``select_edges`` returned before 1.1.1: seams and smooth edges
    included, in ``shape.Edges()`` order."""
    edges = tuple(shape.Edges())
    if selector.select == "all":
        return edges
    index = "XYZ".index(selector.axis)
    return tuple(
        edge
        for edge in edges
        if is_straight_edge(edge)
        and abs(abs(line_direction(edge)[index]) - 1.0) <= _DIRECTION_TOLERANCE
    )


@contextmanager
def engine_selects_unfiltered() -> Iterator[None]:
    """Make the local engine select as it did before 1.1.1."""
    with mock.patch("cad_core.local_cad.select_edges", unfiltered_edges):
        yield
