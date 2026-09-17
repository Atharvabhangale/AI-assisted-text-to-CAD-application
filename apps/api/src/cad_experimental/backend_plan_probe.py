"""Execute the SAME canonical Operation Plans on a named backend.

Goes through ``build_plan`` -- the real routing the application uses -- so
what is measured is backend *selection*, not a hand-written call sequence
that happens to agree. Each record says which engine ran and by which route,
read back from the result rather than assumed from the request.

Backend selection is explicit and never falls back: it is whatever
``resolve_backend`` is given, and if that engine is unavailable the run says
so and stops.

    python -m cad_experimental.backend_plan_probe --backend freecad
    python -m cad_experimental.backend_plan_probe --backend cadquery
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from .build import build_plan
from .cad_backend import BackendUnavailable, resolve_backend
from .parser import parse_plan
from .validation import validate_plan

#: The four milestone cases, as canonical Operation Plans. Byte-identical
#: between backends -- that is the entire point of running this twice.
#: Cases 1 and 2 are V1-expressible; 3 and 4 carry a semantic selector.
PLANS: Dict[str, Dict[str, Any]] = {
    "CASE 1  40 mm cube": {
        "status": "generated", "summary": "a cube",
        "operations": [
            {"id": "cube", "type": "box",
             "parameters": {"x": 40.0, "y": 40.0, "z": 40.0}},
        ],
    },
    "CASE 2  plate + centred d20 through hole": {
        "status": "generated", "summary": "a bored plate",
        "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20.0,
                            "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
        ],
    },
    "CASE 3  + four vertical corner fillets r=2": {
        "status": "generated", "summary": "a bored plate, corners rounded",
        "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20.0,
                            "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            {"id": "corners", "type": "fillet", "target": "plate",
             "parameters": {"radius": 2.0,
                            "edges": {"select": "straight", "axis": "Z"}}},
        ],
    },
    "CASE 4  + top circular rim chamfer d=1": {
        "status": "generated", "summary": "a bored plate, top rim broken",
        "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20.0,
                            "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            {"id": "rim", "type": "chamfer", "target": "plate",
             "parameters": {"distance": 1.0,
                            "edges": {"select": "circular", "axis": "Z",
                                      "position": "top"}}},
        ],
    },
}


def _service() -> Any:
    """The V1 document service, when this machine can build one.

    Only the CadQuery route uses it. On a FreeCAD-only machine it cannot be
    imported at all, and that is not an error here: the route that needs it
    is never taken.
    """
    try:
        from cad_core.application_service import CadApplicationService
    except Exception:
        return None
    return CadApplicationService.local(tempfile.mkdtemp())


def _measurement(build: Any) -> Dict[str, Any]:
    """The measurement, from whichever path ran, in one shape."""
    if build.executed and build.execution.bodies:
        measured = build.execution.bodies[0].measurement
        payload = measured.to_dict() if hasattr(measured, "to_dict") else {}
    elif build.outcome is not None:
        payload = {}
        for artifact in (build.outcome.to_dict().get("manifest") or {}).get(
                "artifacts", []):
            if artifact.get("kind") == "geometry":
                details = artifact.get("details") or {}
                box = details.get("bounding_box") or {}
                payload = {
                    "is_valid": True,
                    "solid_count": details.get("solid_count"),
                    "volume": details.get("volume_mm3"),
                    "face_count": details.get("face_count"),
                    "edge_count": details.get("edge_count"),
                    "size": [(box.get("size") or {}).get(axis)
                             for axis in ("x", "y", "z")],
                }
    else:
        payload = {}
    return {key: payload.get(key) for key in (
        "is_valid", "solid_count", "volume", "face_count", "edge_count",
        "size")}


def _render_facts(build: Any) -> Dict[str, Any]:
    """Whether a mesh exists, and whether it is non-empty."""
    model = build.render
    if model is None and build.outcome is not None:
        model = getattr(build.outcome, "render_model", None)
    if model is None:
        return {"present": False, "triangles": 0, "vertices": 0}
    payload = model.to_dict()
    # `triangles` is a list of index triples and `vertices` a list of points,
    # so each length IS the count -- not a flat float array to divide by 3.
    return {
        "present": True,
        "triangles": len(payload.get("triangles") or []),
        "vertices": len(payload.get("vertices") or []),
    }


def run(name: str) -> Dict[str, Any]:
    backend = resolve_backend(name)
    service = _service()
    record: Dict[str, Any] = {
        "requested_backend": name,
        "resolved_backend": backend.name,
        "version": backend.version(),
        "CAD_BACKEND_env": os.environ.get("CAD_BACKEND", "(unset)"),
        "service_available": service is not None,
        "cases": [],
    }
    for label, payload in PLANS.items():
        entry: Dict[str, Any] = {"case": label}
        plan = parse_plan(payload)
        validation = validate_plan(plan)
        entry["plan_valid"] = validation.valid
        if not validation.valid:
            entry["outcome"] = "PLAN_INVALID"
            entry["problems"] = [p.to_dict() for p in validation.problems]
            record["cases"].append(entry)
            continue

        build = build_plan(service, plan, name="parity-part", backend=backend)
        entry["backend_reported"] = build.backend
        entry["execution_path"] = build.execution_path
        entry["built"] = bool(build.built)
        entry["outcome"] = "BUILT" if build.built else "FAILED"
        entry["error"] = build.error
        entry["measurement"] = _measurement(build)
        entry["render"] = _render_facts(build)
        if build.executed:
            entry["selections"] = build.execution.to_dict()["selections"]
        else:
            entry["selections"] = {}
        # The invariant this whole probe exists for.
        entry["no_fallback"] = build.backend == name
        record["cases"].append(entry)
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    try:
        record = run(args.backend)
    except BackendUnavailable as exc:
        print(json.dumps({"backend": args.backend,
                          "outcome": "BACKEND_UNAVAILABLE",
                          "error": str(exc)}, indent=1))
        return 2
    text = json.dumps(record, indent=1, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
