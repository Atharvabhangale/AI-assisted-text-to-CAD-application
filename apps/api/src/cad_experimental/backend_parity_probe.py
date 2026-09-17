"""Run one backend through the shared selector engine and print plain facts.

A measuring instrument, not a feature. It exists because the two kernels
cannot be imported into one interpreter here -- FreeCAD lives in a WSL2
build, CadQuery in the Windows virtual environment -- so parity has to be
established by running the SAME code against each and comparing the JSON.

It builds the one parity fixture, resolves every semantic selector through
``cad_experimental.edge_semantics`` (the backend-neutral engine, used
unchanged), executes the two edge-modifier cases through the backend's
``fillet_edges``/``chamfer_edges``, and writes a machine-comparable record.

Nothing here decides what a selector means. That is the whole point: if this
file contained selector logic, a parity result would only prove this file
agrees with itself.

    python -m cad_experimental.backend_parity_probe --backend freecad
    python -m cad_experimental.backend_parity_probe --backend cadquery
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from .cad_backend import (
    BackendOperationError,
    BackendUnavailable,
    Selector,
    UnsupportedSelector,
    resolve_backend,
)
from .edge_semantics import SemanticSelector, resolve

#: The one fixture every case is built on: a 100 x 60 x 10 mm plate with a
#: centred 20 mm through hole. Small enough to reason about by hand, and it
#: carries a cylindrical face, so it has a seam -- which is the only thing
#: that makes `straight` and `axis_parallel` different questions.
PLATE = (100.0, 60.0, 10.0)
HOLE_DIAMETER = 20.0
HOLE_CENTRE = (50.0, 30.0, 0.0)

#: The selector matrix. Every one is a plan-level selector, exactly as the
#: canonical Operation Plan carries it.
SELECTORS: Sequence[SemanticSelector] = (
    SemanticSelector(select="circular", axis="X"),
    SemanticSelector(select="axis_parallel", axis="Z"),
    SemanticSelector(select="straight", axis="Z"),
    SemanticSelector(select="circular", axis="Z"),
    SemanticSelector(select="circular", axis="Z", position="top"),
    SemanticSelector(select="circular", axis="Z", position="bottom"),
)


def _fixture(backend: Any) -> Any:
    """The plate with its centred through hole, built by this backend."""
    plate = backend.create_box(PLATE)
    return backend.through_hole(
        plate, diameter=HOLE_DIAMETER, position=HOLE_CENTRE, axis="+Z"
    )


def _normalise(fact: Any) -> Dict[str, Any]:
    """An edge described so two backends can be compared without indices.

    Coordinates are rounded to 6 decimal places, which is far tighter than
    any kernel difference this project tolerates and far looser than float
    noise. The backend's own index is carried alongside but is **not** part
    of the comparable description -- FreeCAD and CadQuery number edges
    differently and requiring them to agree would be inventing parity.
    """
    def point(value: Optional[Sequence[float]]) -> Optional[List[float]]:
        if value is None:
            return None
        return [round(float(component), 6) for component in value]

    def axis_of(vector: Optional[Sequence[float]]) -> Optional[str]:
        """Which principal axis a direction lies along, sign ignored."""
        if vector is None:
            return None
        for letter, position in (("X", 0), ("Y", 1), ("Z", 2)):
            if abs(abs(float(vector[position])) - 1.0) < 1e-9:
                return letter
        return None

    return {
        "curve": fact.curve,
        "is_seam": bool(fact.is_seam),
        "axis": axis_of(fact.direction or fact.normal),
        "midpoint": point(fact.midpoint),
        "centre": point(fact.centre),
        "radius": None if fact.radius is None else round(float(fact.radius), 6),
        "adjacent": list(fact.adjacent),
        "backend_index": fact.index,
    }


def _measure(backend: Any, shape: Any) -> Dict[str, Any]:
    measurement = backend.measure(shape)
    payload = measurement.to_dict() if hasattr(measurement, "to_dict") else {}
    return {key: payload.get(key) for key in (
        "is_valid", "solid_count", "volume", "face_count", "edge_count",
        "minimum", "maximum", "size",
    )}


#: Every capability the interface promises, in the order a build needs them.
#: Audited by actually calling each one -- a capability that exists as a
#: method but raises is not implemented, and only exercising it shows that.
CAPABILITIES = (
    "create_box", "create_cylinder", "through_hole", "subtract",
    "select_edges", "fillet", "chamfer",
    "describe_edges", "edges_at", "fillet_edges", "chamfer_edges",
    "measure", "export_step", "read_step", "render_model",
)

CAP_PASS = "PASS"
CAP_UNSUPPORTED = "UNSUPPORTED"
CAP_ERROR = "ERROR"
CAP_NO_SOURCE = "NO_SOURCE"


def audit_capabilities(backend: Any, shape: Any, facts: Sequence[Any],
                       tmpdir: str) -> List[Dict[str, Any]]:
    """Exercise every interface capability and say plainly what happened.

    Deliberately NOT wrapped in one broad ``except``: each capability is
    called on its own and its outcome classified, so a failure names the
    capability that failed and the kind of failure it was. A harness that
    swallowed these into one 'FreeCAD not ready' would hide exactly the
    information the milestone needs.
    """
    import os

    from .edge_semantics import SemanticSelector, resolve

    straight = resolve(SemanticSelector(select="straight", axis="Z"), facts)
    rim = resolve(
        SemanticSelector(select="circular", axis="Z", position="top"), facts)

    def attempt(name: str, call: Any) -> Dict[str, Any]:
        row: Dict[str, Any] = {"capability": name}
        try:
            value = call()
        except NotImplementedError:
            row["status"] = CAP_UNSUPPORTED
            row["detail"] = "the backend does not implement this capability"
        except FileNotFoundError as exc:
            row["status"] = CAP_NO_SOURCE
            row["detail"] = str(exc)
        except BackendOperationError as exc:
            # A refusal the operation itself is entitled to make. It is an
            # honest answer from an implemented capability, so it is reported
            # as an error of the operation, never as a missing capability.
            row["status"] = CAP_ERROR
            row["detail"] = f"BackendOperationError: {exc}"
        except Exception as exc:  # noqa: BLE001 - classified, not swallowed
            row["status"] = CAP_ERROR
            row["detail"] = f"{type(exc).__name__}: {exc}"
        else:
            row["status"] = CAP_PASS
            row["detail"] = type(value).__name__
        return row

    step_path = os.path.join(tmpdir, "parity.step")

    def read_back() -> Any:
        """Round-trip the STEP this backend just wrote.

        NO_SOURCE is reserved for its literal meaning -- there is no artifact
        to read, because the export step did not produce one. Reading a file
        that was never meant to exist would report NO_SOURCE every run and
        say nothing about the backend.
        """
        if not os.path.exists(step_path):
            raise FileNotFoundError(
                "export_step produced no file, so there is nothing to read")
        return backend.read_step(step_path)

    plan = [
        ("create_box", lambda: backend.create_box(PLATE)),
        ("create_cylinder", lambda: backend.create_cylinder(
            diameter=20.0, height=30.0, position=(0.0, 0.0, 0.0), axis="+Z")),
        ("through_hole", lambda: _fixture(backend)),
        ("subtract", lambda: backend.subtract(
            backend.create_box(PLATE),
            [backend.create_cylinder(diameter=20.0, height=40.0,
                                     position=(50.0, 30.0, -10.0), axis="+Z")])),
        ("select_edges", lambda: backend.select_edges(
            shape, Selector(select="axis_parallel", axis="Z"))),
        ("fillet", lambda: backend.fillet(
            shape, 1.0, Selector(select="axis_parallel", axis="X"))),
        ("chamfer", lambda: backend.chamfer(
            shape, 1.0, Selector(select="axis_parallel", axis="X"))),
        ("describe_edges", lambda: backend.describe_edges(shape)),
        ("edges_at", lambda: backend.edges_at(shape, straight.indices)),
        ("fillet_edges", lambda: backend.fillet_edges(
            shape, 2.0, backend.edges_at(shape, straight.indices))),
        ("chamfer_edges", lambda: backend.chamfer_edges(
            shape, 1.0, backend.edges_at(shape, rim.indices))),
        ("measure", lambda: backend.measure(shape)),
        ("export_step", lambda: backend.export_step(shape, step_path)),
        ("read_step", read_back),
        ("render_model", lambda: backend.render_model(
            shape, part_name="parity", feature_id="plate")),
    ]
    return [attempt(name, call) for name, call in plan]


def run(name: str) -> Dict[str, Any]:
    backend = resolve_backend(name)
    record: Dict[str, Any] = {
        "backend": backend.name,
        "version": backend.version(),
        "fixture": {"plate": list(PLATE), "hole_diameter": HOLE_DIAMETER},
    }

    shape = _fixture(backend)
    record["baseline"] = _measure(backend, shape)

    facts = backend.describe_edges(shape)
    record["edges"] = [_normalise(fact) for fact in facts]
    record["seam_indices"] = [f.index for f in facts if f.is_seam]

    # --- the selector matrix, through the shared engine -------------------
    matrix = []
    for selector in SELECTORS:
        resolution = resolve(selector, facts)
        by_index = {fact.index: fact for fact in facts}
        matrix.append({
            "selector": selector.to_dict(),
            "candidate_count": len(resolution.candidates),
            "selected_count": len(resolution.indices),
            "code": resolution.code,
            "message": resolution.message,
            "seam_count": len(resolution.seams),
            # The comparable part: WHICH edges, described semantically.
            "selected": sorted(
                (_normalise(by_index[i]) for i in resolution.indices),
                key=lambda d: (d["curve"], d["midpoint"]),
            ),
        })
    record["selector_matrix"] = matrix

    # --- the two edge-modifier cases --------------------------------------
    cases = []
    # The operation is a bound method chosen here, never a name looked up
    # with `getattr` at run time. The difference matters even though this
    # list is a literal: a boundary test forbids computed attribute lookup
    # across this package, so that no path exists down which a parsed string
    # could ever select what gets called.
    for label, selector, blend, amount in (
        ("CASE A fillet straight/Z r=2",
         SemanticSelector(select="straight", axis="Z"),
         backend.fillet_edges, 2.0),
        ("CASE B chamfer circular/Z/top d=1",
         SemanticSelector(select="circular", axis="Z", position="top"),
         backend.chamfer_edges, 1.0),
    ):
        entry: Dict[str, Any] = {"case": label, "selector": selector.to_dict()}
        resolution = resolve(selector, facts)
        entry["selected_count"] = len(resolution.indices)
        entry["resolution_code"] = resolution.code
        if resolution.code is not None:
            entry["outcome"] = "SELECTOR_REFUSED"
            cases.append(entry)
            continue
        try:
            edges = backend.edges_at(shape, resolution.indices)
            built = blend(shape, amount, edges)
            entry["outcome"] = "BUILT"
            entry["measurement"] = _measure(backend, built)
        except UnsupportedSelector as exc:
            entry["outcome"] = "UNSUPPORTED"
            entry["error"] = str(exc)
        except BackendOperationError as exc:
            entry["outcome"] = "OPERATION_FAILED"
            entry["error"] = str(exc)
        cases.append(entry)
    record["cases"] = cases

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        record["capabilities"] = audit_capabilities(
            backend, shape, facts, tmpdir)
    tally: Dict[str, int] = {}
    for row in record["capabilities"]:
        tally[row["status"]] = tally.get(row["status"], 0) + 1
    record["capability_tally"] = tally
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True,
                        help="which backend to measure; never falls back")
    parser.add_argument("--out", default=None, help="write JSON here")
    args = parser.parse_args(argv)

    try:
        record = run(args.backend)
    except BackendUnavailable as exc:
        # Reported as itself, never as a bad plan and never by substituting
        # another engine.
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
