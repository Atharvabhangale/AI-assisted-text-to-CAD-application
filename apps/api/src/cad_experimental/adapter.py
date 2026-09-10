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
``position``     the same ``position`` object -- **omitted when absent**, so
                 the contract's own default applies rather than an invented
                 ``{0,0,0}``
``axis``         the same ``axis`` string, omitted when absent
===============  ===========================================================
"""

from __future__ import annotations

from typing import Any, Dict, List

from .plan import BOX, CYLINDER, UNITS, OperationPlan, PlanStatus

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
