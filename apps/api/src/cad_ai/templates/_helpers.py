"""Small constructors shared by the templates, so every template writes the
same complete feature shape (every parameter present, as the response schema
requires)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

Vec = Tuple[float, float, float]


def p(x: float, y: float, z: float) -> Dict[str, float]:
    return {"x": x, "y": y, "z": z}


def box(id: str, size: Vec, position: Vec) -> Dict[str, Any]:
    return {"id": id, "type": "box", "size": p(*size), "position": p(*position)}


def cyl(id: str, diameter: float, height: float, position: Vec, axis: str = "+Z") -> Dict[str, Any]:
    return {"id": id, "type": "cylinder", "diameter": diameter, "height": height, "position": p(*position), "axis": axis}


def hole(id: str, target: str, diameter: float, position: Vec, axis: str = "+Z") -> Dict[str, Any]:
    return {"id": id, "type": "through_hole", "target": target, "diameter": diameter, "position": p(*position), "axis": axis}


def union(id: str, target: str, tools: Sequence[str]) -> Dict[str, Any]:
    return {"id": id, "type": "union", "target": target, "tools": list(tools)}


def subtract(id: str, target: str, tools: Sequence[str]) -> Dict[str, Any]:
    return {"id": id, "type": "subtract", "target": target, "tools": list(tools)}


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
