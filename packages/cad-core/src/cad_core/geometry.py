"""Boundary for the geometric validation rules E1-E5.

**No geometry is implemented in this package, and none is implemented here.**

Section E.2 of ``docs/cad-specification.md`` defines five rules that cannot be
decided from a specification document alone -- each one requires an evaluated
solid, and therefore a geometry kernel:

* E1 -- a through_hole centreline actually intersects its target solid.
* E2 -- a subtract leaves a non-empty solid.
* E3 -- every modifier leaves a single connected solid.
* E4 -- an edge selector matches at least one edge of its target.
* E5 -- a fillet radius / chamfer distance is admissible for every matched edge.

These belong to the future deterministic CAD engine stage.  They are declared
here so the boundary is visible in code and so the failure modes are named,
but nothing checks them yet.

Consequences that callers must not confuse:

* :func:`cad_core.validator.validate` implements the static rules S1-S20 only.
  It never emits an ``E`` code, and a document passing it is *statically*
  valid, not geometrically buildable.
* A statically valid part may still be impossible to build.  Until the engine
  exists, that verdict is simply unknown -- not "passed".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, NoReturn

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from cad_core.model import Part

#: Geometric rule codes and their requirements (specification Section E.2).
GEOMETRIC_RULES: Mapping[str, str] = {
    "E1": (
        "A through_hole centreline actually intersects its target solid. A hole "
        "that misses the material is an error, not a no-op."
    ),
    "E2": "A subtract leaves a non-empty solid. Removing all material is an error.",
    "E3": (
        "Every modifier leaves a single connected solid. A cut that splits the "
        "body into two pieces is an error in V1, which has no multi-body parts."
    ),
    "E4": (
        "An edge selector matches at least one edge of its target. A fillet or "
        "chamfer that affects nothing is an error, because it indicates a "
        "misread request."
    ),
    "E5": (
        "A fillet.radius / chamfer.distance is admissible for every matched "
        "edge -- it fits within the adjoining faces and does not consume "
        "neighbouring geometry."
    ),
}

GEOMETRIC_RULE_CODES = tuple(GEOMETRIC_RULES)


def check_geometric_rules(part: "Part") -> NoReturn:
    """Placeholder for the geometric rules E1-E5. Always raises.

    This is the seam where the future deterministic CAD engine will evaluate a
    statically valid part and report E1-E5.  It raises
    :class:`NotImplementedError` rather than returning an empty result, so that
    no caller can mistake "not checked" for "checked and passed".

    Raises:
        NotImplementedError: always.
    """
    raise NotImplementedError(
        "Geometric rules E1-E5 require an evaluated solid and belong to the "
        "future deterministic CAD engine stage; no geometry is implemented in "
        "cad-core. Static validation covers rules S1-S20 only."
    )
