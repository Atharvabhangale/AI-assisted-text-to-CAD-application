"""Operation plan -> canonical V1 CAD document. The only bridge.

This module is a **translation**, not an implementation. It contains no
geometry, calls no kernel, and knows nothing about CadQuery or OpenCascade.
It turns a typed plan into the same canonical document the stable path
produces, and hands it to the existing machinery:

    OperationPlan
        -> canonical V1 document (here)
        -> cad_core.validator            (authoritative, unchanged)
        -> cad_core application service  (cache, isolation, engine)
        -> B-rep, RenderModel, STEP/IGES/STL

Because the output is an ordinary V1 document, every downstream guarantee
still holds and nothing downstream needed changing. In particular the
single-solid rule S9 is enforced by the real validator on the real document:
a plan with two constructive operations translates fine and is then correctly
**rejected** as CAD, which is the right place for that judgement.

The mapping, in full:

===============  ===========================================================
plan             V1 document
===============  ===========================================================
``box``          ``{"type": "box", "size": {x, y, z}}``
``cylinder``     ``{"type": "cylinder", "diameter", "height"}``
``through_hole`` ``{"type": "through_hole", "target", "diameter",
                 "position"}`` -- the plan's operation-level ``target``
                 becomes the feature's ``target``, which is where the V1
                 document already keeps it
``subtract``     ``{"type": "subtract", "target", "tools"}`` -- both
                 references pass through unchanged, and **``tools`` keeps its
                 order**, because Section C.4 removes the tools in list order
                 and reordering them would change the geometry
``fillet``       ``{"type": "fillet", "target", "radius", "edges"}`` -- the
                 selector object passes through **as it is**. There is no
                 selector logic here: ``cad_core.edge_selection`` owns that,
                 and reinterpreting a selector on the way past is exactly how
                 a layer like this would start lying about geometry
``position``     the same ``position`` object -- **omitted when absent**, so
                 the contract's own default applies rather than an invented
                 ``{0,0,0}``. A ``through_hole`` always carries one, because
                 Section C.3 requires it
``axis``         the same ``axis`` string, omitted when absent
===============  ===========================================================

The translation is almost an identity, and deliberately so: the plan's whole
difference from the V1 document is a flatter *shape*, not different
semantics. Nothing here reinterprets a reference, reorders operations,
renames an id or computes geometry -- so the V1 validator judges exactly
what the plan said, and rule S6/S7 still decide whether a reference is
sound.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .plan import (
    BOX,
    CYLINDER,
    FILLET,
    SUBTRACT,
    THROUGH_HOLE,
    UNITS,
    OperationPlan,
    PlanStatus,
)

#: The V1 schema version this adapter writes. It tracks the specification,
#: and is imported from nowhere else because ``cad_core`` exposes it as the
#: document field it is.
SCHEMA_VERSION = "1.0.0"

#: The part name used when a plan does not imply one. A label, never
#: geometry -- the comparison layer treats names as non-geometric for exactly
#: this reason.
DEFAULT_PART_NAME = "experimental-part"


class AdapterError(Exception):
    """A plan that cannot be expressed as a V1 document at all."""


def plan_to_document(
    plan: OperationPlan, *, name: str = DEFAULT_PART_NAME
) -> Dict[str, Any]:
    """Translate a generated plan into a canonical V1 CAD document.

    Only a :attr:`~cad_experimental.plan.PlanStatus.GENERATED` plan has a
    document; asking for one from a refusal is a programming error, not a
    validation result, so it raises.

    The document is returned as a plain mapping. It is deliberately **not**
    validated here -- the caller passes it to the existing validator, which
    is the only thing entitled to judge it.
    """
    if plan.status is not PlanStatus.GENERATED:
        raise AdapterError(
            f"a `{plan.status.value}` plan has no geometry to build"
        )
    if not plan.operations:
        raise AdapterError("a generated plan with no operations has no geometry")

    features: List[Dict[str, Any]] = [
        _feature(operation) for operation in plan.operations
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "units": UNITS,
        "name": name,
        "features": features,
    }


def _feature(operation: Any) -> Dict[str, Any]:
    kind = getattr(operation, "TYPE", None)
    if kind == BOX:
        feature: Dict[str, Any] = {
            "id": operation.id,
            "type": BOX,
            "size": {"x": operation.x, "y": operation.y, "z": operation.z},
        }
    elif kind == CYLINDER:
        feature = {
            "id": operation.id,
            "type": CYLINDER,
            "diameter": operation.diameter,
            "height": operation.height,
        }
        if operation.axis is not None:
            feature["axis"] = operation.axis
    elif kind == THROUGH_HOLE:
        # `position` is written unconditionally: Section C.3 requires it, and
        # the parser has already guaranteed it. It is returned early because
        # the shared position handling below treats it as optional.
        feature = {
            "id": operation.id,
            "type": THROUGH_HOLE,
            "target": operation.target,
            "diameter": operation.diameter,
            "position": operation.position.to_dict(),
        }
        if operation.axis is not None:
            feature["axis"] = operation.axis
        return feature
    elif kind == FILLET:
        # `edges` is handed over unchanged. In particular nothing here
        # excludes a parameterisation seam or trims a selection to what the
        # kernel is likely to accept: `docs/edge-selection.md` records that a
        # selector can legitimately match an edge a fillet cannot take, and
        # the honest answer is the engine's E5 failure, not a quietly
        # narrowed selection.
        return {
            "id": operation.id,
            "type": FILLET,
            "target": operation.target,
            "radius": operation.radius,
            "edges": operation.edges.to_dict(),
        }
    elif kind == SUBTRACT:
        # Both references straight through. `tools` is a list, in the plan's
        # order: Section C.4 subtracts in list order, so the order is
        # geometry, not presentation.
        return {
            "id": operation.id,
            "type": SUBTRACT,
            "target": operation.target,
            "tools": list(operation.tools),
        }
    else:
        raise AdapterError(f"no V1 feature for operation type {kind!r}")

    if operation.position is not None:
        feature["position"] = operation.position.to_dict()
    return feature


__all__ = [
    "DEFAULT_PART_NAME",
    "SCHEMA_VERSION",
    "AdapterError",
    "plan_to_document",
]
