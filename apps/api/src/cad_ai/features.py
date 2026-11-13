"""Constructors for complete feature objects, in the shape the model writes.

Shared by the prompt's worked examples and by every template, so a reference
answer always carries every parameter the response schema requires -- the
schema makes every parameter required, defaults included. Pure data: nothing
here validates or builds; the validator and the engine do that, and tests run
both on every example and template.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

Vec = Tuple[float, float, float]


def p(x: float, y: float, z: float) -> Dict[str, float]:
    return {"x": x, "y": y, "z": z}


def box(id: str, size: Vec, position: Vec) -> Dict[str, Any]:
    return {"id": id, "type": "box", "size": p(*size), "position": p(*position)}


def cyl(
    id: str,
    diameter: float,
    height: float,
    position: Vec,
    axis: str = "+Z",
    *,
    top: Optional[float] = None,
    sides: int = 0,
) -> Dict[str, Any]:
    """A cylinder; ``top`` makes a taper or cone, ``sides`` a polygon prism."""
    return {
        "id": id,
        "type": "cylinder",
        "diameter": diameter,
        "height": height,
        "position": p(*position),
        "axis": axis,
        "top_diameter": diameter if top is None else top,
        "sides": sides,
    }


def _pattern(count: int, axis: str, angle: float, step: float) -> Dict[str, Any]:
    return {"pattern_count": count, "pattern_axis": axis, "pattern_angle": angle, "pattern_step": step}


def union(
    id: str, target: str, tools: Sequence[str], *, count: int = 1, axis: str = "Z", angle: float = 0, step: float = 0
) -> Dict[str, Any]:
    """Fuse ``tools`` into ``target``; with ``count``/``angle``/``step`` each tool is patterned."""
    return {"id": id, "type": "union", "target": target, "tools": list(tools), **_pattern(count, axis, angle, step)}


def subtract(
    id: str, target: str, tools: Sequence[str], *, count: int = 1, axis: str = "Z", angle: float = 0, step: float = 0
) -> Dict[str, Any]:
    """Cut ``tools`` out of ``target``; with ``count``/``angle``/``step`` each tool is patterned."""
    return {"id": id, "type": "subtract", "target": target, "tools": list(tools), **_pattern(count, axis, angle, step)}


def drill(
    id: str, target: str, diameter: float, start: Vec, length: float, axis: str = "+Z", **pattern: Any
) -> List[Dict[str, Any]]:
    """A through hole as the model writes one: a cylinder tool from ``start``
    along ``axis`` for ``length`` (reaching 2 mm past both faces), subtracted.
    ``pattern`` (count/axis/angle/step) repeats it, e.g. a bolt circle."""
    tool = f"{id}_tool"
    return [cyl(tool, diameter, length, start, axis), subtract(id, target, [tool], **pattern)]


def _edges(axis: Optional[str]) -> Dict[str, Any]:
    return {"select": "axis_parallel", "axis": axis} if axis else {"select": "all"}


def fillet(id: str, target: str, radius: float, axis: Optional[str] = None) -> Dict[str, Any]:
    return {"id": id, "type": "fillet", "target": target, "radius": radius, "edges": _edges(axis)}


def chamfer(id: str, target: str, distance: float, axis: Optional[str] = None) -> Dict[str, Any]:
    return {"id": id, "type": "chamfer", "target": target, "distance": distance, "edges": _edges(axis)}


def answer(*, plan: str, assumptions: Sequence[str], name: str, description: str, features: Sequence[Dict[str, Any]], summary: str) -> Dict[str, Any]:
    """A complete answer in the response schema's field order."""
    from cad_core.model import SCHEMA_VERSION

    return {
        "status": "document",
        "plan": plan,
        "assumptions": list(assumptions),
        "document": {
            "schema_version": SCHEMA_VERSION,
            "units": "mm",
            "name": name,
            "description": description,
            "features": list(features),
        },
        "summary": summary,
        "questions": [],
        "issues": [],
    }
