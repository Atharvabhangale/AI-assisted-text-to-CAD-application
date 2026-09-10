"""A local development provider. **NOT a model, and not a Claude result.**

The real Anthropic credential is unavailable in this environment, so the one
thing that cannot be exercised is the model call itself. Everything *after*
it can be, and this module exists to do exactly that: it hands a
pre-supplied operation plan to the same :class:`~cad_ai.provider.TextToCadModel`
boundary a real provider satisfies, so the plan travels the whole real path --

    fixture (or a caller's plan)
      -> cad_ai.provider.TextToCadModel boundary
      -> cad_experimental.generation.OperationPlanService
      -> parser            (the same allow-list parser)
      -> plan validation   (the same P1-P7 rules)
      -> V1 adapter        (the same translation)
      -> cad_core.validator (the existing, authoritative validator)
      -> cad_core CAD engine (the existing CadQuery/OpenCascade engine)
      -> RenderModel        (the existing tessellation)

-- with not one step stubbed, mocked or skipped except the network call.

**What this is not.** It is not a language model, it does not interpret
natural language, and its output is not a Claude result of any kind. It
returns text a developer supplied. Every result it produces is stamped
:data:`SOURCE_LABEL` (``"LOCAL_DEVELOPMENT_PLAN"``) and reports
``is_live_model_result: false``, and a test asserts that a caller cannot
mistake one for a model answer.

**What it deliberately cannot do.** It reads no credential and no
environment variable, opens no socket, imports no SDK, touches no file, and
executes nothing. It ignores the prompt it is given, because it does not
reason -- a fixture is chosen by name or supplied whole.

The real live harness (:mod:`cad_experimental.harness`) is untouched by this
module and cannot reach it: ``--live`` still requires ``ANTHROPIC_API_KEY``
and still builds the real Anthropic provider. When a credential exists, the
genuine comparison runs there, unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cad_ai.provider import ModelRequest, ModelResponse, ProviderError

#: Stamped on every result this module produces. The one string a caller
#: should look for before believing a number came from a model: it did not.
SOURCE_LABEL = "LOCAL_DEVELOPMENT_PLAN"

#: The provider name that appears in generation metadata. Deliberately not a
#: vendor name and deliberately not a model id.
PROVIDER_NAME = "local-development"

#: Stands where a model id would. Says what it is.
MODEL_NAME = "local-plan-fixture (not a model)"


def _box(identifier: str, x: float, y: float, z: float, **extra: Any) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"x": x, "y": y, "z": z}
    parameters.update(extra)
    return {"id": identifier, "type": "box", "parameters": parameters}


def _cylinder(
    identifier: str, diameter: float, height: float, **extra: Any
) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"diameter": diameter, "height": height}
    parameters.update(extra)
    return {"id": identifier, "type": "cylinder", "parameters": parameters}


def _subtract(identifier: str, target: str, tools: List[str]) -> Dict[str, Any]:
    """A subtract. No `parameters`: its whole input is two references."""
    return {
        "id": identifier,
        "type": "subtract",
        "target": target,
        "tools": list(tools),
    }


def _fillet(
    identifier: str, target: str, radius: float, edges: Dict[str, Any]
) -> Dict[str, Any]:
    """A fillet. The selector is an object, and passes through untouched."""
    return {
        "id": identifier,
        "type": "fillet",
        "target": target,
        "parameters": {"radius": radius, "edges": dict(edges)},
    }


def _chamfer(
    identifier: str, target: str, distance: float, edges: Dict[str, Any]
) -> Dict[str, Any]:
    """A chamfer: the same shape as a fillet, with `distance`."""
    return {
        "id": identifier,
        "type": "chamfer",
        "target": target,
        "parameters": {"distance": distance, "edges": dict(edges)},
    }


def _through_hole(
    identifier: str,
    target: str,
    diameter: float,
    x: float,
    y: float,
    z: float = 0.0,
    axis: str = "+Z",
) -> Dict[str, Any]:
    """A hole in ``target``. Every hole in a body targets the body itself.

    Per Section B.4 a modifier's result keeps the target's id, so holes never
    chain: four holes in a plate all name the plate.
    """
    return {
        "id": identifier,
        "type": "through_hole",
        "target": target,
        "parameters": {
            "diameter": diameter,
            "position": {"x": x, "y": y, "z": z},
            "axis": axis,
        },
    }


#: Expected volumes, computed from geometry rather than transcribed. Written
#: out so a wrong expectation cannot quietly hide a wrong build.
_PLATE = 100.0 * 60.0 * 10.0
_HOLE_D8_THROUGH_10 = math.pi * 4.0**2 * 10.0
_PLATE_ONE_HOLE = _PLATE - _HOLE_D8_THROUGH_10
_PLATE_FOUR_HOLES = _PLATE - 4.0 * _HOLE_D8_THROUGH_10
_BORED_ROD = math.pi * (10.0**2 - 4.0**2) * 50.0

#: The four hole centres of the plate fixture. All inside the plate with
#: clearance: at radius 4 the outermost spans 86..94 in x and 46..54 in y.
_FOUR_HOLES = ((10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0))

#: Stage 34 expectations, computed from geometry.
_CUBE_50 = 50.0**3
_CUBE_BORED = _CUBE_50 - math.pi * 10.0**2 * 50.0
_PLATE_SUBTRACT_BORE = _PLATE - math.pi * 10.0**2 * 10.0
_PLATE_TWO_TOOLS = _PLATE - 2.0 * math.pi * 8.0**2 * 10.0

#: What a fixture is for. Most build; some exist to be *rejected*, because a
#: rule that is never exercised is a rule nobody has tested.
BUILDS = "builds"
PLAN_REJECTED = "plan_rejected"
BUILD_REJECTED = "build_rejected"

#: For a geometry with no clean closed form -- a box with all twelve edges
#: filleted, where the corner blends interact. Recording a *measured* number
#: as the "expected" one would be circular, so these are checked against a
#: document built directly with cad-core instead, in the tests.
CROSS_CHECKED = "cross_checked"

#: Stage 35 expectations. A vertical corner blended at radius r removes
#: r^2 - pi*r^2/4 = r^2(1 - pi/4) in cross-section, over the edge's length.
_CORNER = 2.0**2 * (1.0 - math.pi / 4.0)
_PLATE_FILLET_Z = _PLATE - 4.0 * 10.0 * _CORNER
_PLATE_FILLET_X = _PLATE - 4.0 * 100.0 * _CORNER
#: The drilled plate, then its four X or Y corners blended.
_DRILLED = _PLATE - math.pi * 10.0**2 * 10.0
_DRILLED_FILLET_X = _DRILLED - 4.0 * 100.0 * _CORNER
_DRILLED_FILLET_Y = _DRILLED - 4.0 * 60.0 * _CORNER

#: Stage 36 expectations. A chamfer at distance d removes a right triangle
#: of area d^2/2 per unit of edge length -- an exact decimal, unlike a
#: fillet's quarter-circle.
_BEVEL = 2.0**2 / 2.0
_PLATE_CHAMFER_Z = _PLATE - 4.0 * 10.0 * _BEVEL
_PLATE_CHAMFER_X = _PLATE - 4.0 * 100.0 * _BEVEL
_DRILLED_CHAMFER_X = _DRILLED - 4.0 * 100.0 * _BEVEL

#: The two selectors, spelled once.
_ALL = {"select": "all"}


def _axis_parallel(axis: str) -> Dict[str, Any]:
    """An unsigned selector axis -- ``"Z"``, never ``"+Z"`` (Section C.7)."""
    return {"select": "axis_parallel", "axis": axis}


#: The development fixtures. Hand-written plans, with the geometry each is
#: expected to produce recorded beside it so a build can be checked rather
#: than merely observed.
FIXTURES: Mapping[str, Dict[str, Any]] = {
    "box-100x60x10": {
        "description": "a rectangular plate, 100 x 60 x 10 mm",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE,
        "expected_face_count": 6,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a rectangular plate 100 x 60 x 10 mm",
            "operations": [_box("body", 100, 60, 10)],
        },
    },
    "plate-one-hole": {
        "description": (
            "the plate with one 8 mm through hole at x=10, y=10, along +Z"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_ONE_HOLE,
        # Six planar faces, plus one cylindrical face per hole.
        "expected_face_count": 7,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a 100 x 60 x 10 mm plate with one 8 mm through hole",
            "operations": [
                _box("plate", 100, 60, 10),
                _through_hole("hole1", "plate", 8, 10, 10),
            ],
        },
    },
    "plate-four-holes": {
        "description": (
            "the plate with four 8 mm through holes at the corners, along +Z"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_FOUR_HOLES,
        "expected_face_count": 10,
        "expected_hole_count": 4,
        "plan": {
            "status": "generated",
            "summary": "a 100 x 60 x 10 mm plate with four 8 mm through holes",
            "operations": [
                _box("plate", 100, 60, 10),
                *(
                    # Every hole targets `plate`, never the hole before it.
                    _through_hole(f"hole{index + 1}", "plate", 8, x, y)
                    for index, (x, y) in enumerate(_FOUR_HOLES)
                ),
            ],
        },
    },
    "cylinder-d20-h50-z": {
        "description": "a cylinder, diameter 20 mm, height 50 mm, along +Z",
        "expected_bounding_box": {"x": 20.0, "y": 20.0, "z": 50.0},
        "expected_face_count": 3,
        "expected_hole_count": 0,
        "expected_volume_mm3": None,  # computed from the diameter below
        "plan": {
            "status": "generated",
            "summary": "a cylinder 20 mm across and 50 mm tall along +Z",
            "operations": [
                _cylinder(
                    "body",
                    20,
                    50,
                    position={"x": 0, "y": 0, "z": 0},
                    axis="+Z",
                )
            ],
        },
    },
    "cylinder-bored-d20-h50": {
        "description": (
            "the d20 x 50 cylinder bored through coaxially with an 8 mm hole "
            "-- a tube"
        ),
        "expected_bounding_box": {"x": 20.0, "y": 20.0, "z": 50.0},
        "expected_volume_mm3": _BORED_ROD,
        # Outer wall, inner wall, and the two annular ends.
        "expected_face_count": 4,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a 20 mm cylinder, 50 mm tall, bored 8 mm through",
            "operations": [
                _cylinder("rod", 20, 50, axis="+Z"),
                _through_hole("bore", "rod", 8, 0, 0, axis="+Z"),
            ],
        },
    },
    "cylinder-d16-h30-x": {
        "description": "a cylinder, diameter 16 mm, height 30 mm, along +X",
        "expected_bounding_box": {"x": 30.0, "y": 16.0, "z": 16.0},
        "expected_face_count": 3,
        "expected_hole_count": 0,
        "expected_volume_mm3": None,
        "plan": {
            "status": "generated",
            "summary": "a cylinder 16 mm across and 30 mm long along +X",
            "operations": [_cylinder("body", 16, 30, axis="+X")],
        },
    },
    # --- Stage 34: subtract -------------------------------------------
    "subtract-cube-bore": {
        "description": (
            "a 50 mm cube with a d20 cylindrical tool subtracted through it"
        ),
        "expected_bounding_box": {"x": 50.0, "y": 50.0, "z": 50.0},
        "expected_volume_mm3": _CUBE_BORED,
        "expected_face_count": 7,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a 50 mm cube bored through by a 20 mm cylinder",
            "operations": [
                _box("body", 50, 50, 50),
                # The tool spans the cube's full depth: position is the base
                # centre, so z=0 with height 50 covers 0..50 exactly.
                _cylinder(
                    "tool", 20, 50,
                    position={"x": 25, "y": 25, "z": 0}, axis="+Z",
                ),
                _subtract("cut", "body", ["tool"]),
            ],
        },
    },
    "subtract-plate-bore": {
        "description": (
            "the 100 x 60 x 10 plate cut by a d20 cylindrical tool "
            "(subtract, not through_hole)"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_SUBTRACT_BORE,
        "expected_face_count": 7,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a plate cut through by a 20 mm cylindrical tool",
            "operations": [
                _box("plate", 100, 60, 10),
                # Overshoots both faces, so the cut is unambiguously through.
                _cylinder(
                    "tool", 20, 20,
                    position={"x": 50, "y": 30, "z": -5}, axis="+Z",
                ),
                _subtract("bore", "plate", ["tool"]),
            ],
        },
    },
    "subtract-two-tools": {
        "description": "one plate, two cylindrical tools, one subtract",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_TWO_TOOLS,
        "expected_face_count": 8,
        "expected_hole_count": 2,
        "plan": {
            "status": "generated",
            "summary": "a plate with two cylindrical cut-outs",
            "operations": [
                _box("plate", 100, 60, 10),
                _cylinder(
                    "tool_a", 16, 20,
                    position={"x": 25, "y": 30, "z": -5}, axis="+Z",
                ),
                _cylinder(
                    "tool_b", 16, 20,
                    position={"x": 75, "y": 30, "z": -5}, axis="+Z",
                ),
                _subtract("cut", "plate", ["tool_a", "tool_b"]),
            ],
        },
    },
    # --- Stage 34: fixtures that must be REJECTED ---------------------
    "reject-consumed-tool": {
        "description": "reusing a tool after a subtract has consumed it",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P12",),
        "plan": {
            "status": "generated",
            "summary": "a tool used twice",
            "operations": [
                _box("plate", 100, 60, 10),
                _cylinder(
                    "tool", 16, 20,
                    position={"x": 25, "y": 30, "z": -5}, axis="+Z",
                ),
                _subtract("first", "plate", ["tool"]),
                _subtract("second", "plate", ["tool"]),
            ],
        },
    },
    "reject-empty-tools": {
        "description": "a subtract with an empty tool list (rule S14)",
        "expects": PLAN_REJECTED,
        # Refused by the parser: an empty list is a malformed subtract
        # rather than a bad reference, so it never becomes a typed plan.
        "expected_codes": ("parse",),
        "plan": {
            "status": "generated",
            "summary": "a subtract that removes nothing",
            "operations": [
                _box("plate", 100, 60, 10),
                _subtract("cut", "plate", []),
            ],
        },
    },
    "reject-target-in-tools": {
        "description": "a subtract listing its own target as a tool (S15)",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P14",),
        "plan": {
            "status": "generated",
            "summary": "a solid subtracted from itself",
            "operations": [
                _box("plate", 100, 60, 10),
                _subtract("cut", "plate", ["plate"]),
            ],
        },
    },
    "reject-future-tool": {
        "description": "a tool that appears later in the plan",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P10",),
        "plan": {
            "status": "generated",
            "summary": "a forward reference",
            "operations": [
                _box("plate", 100, 60, 10),
                _subtract("cut", "plate", ["tool"]),
                _cylinder(
                    "tool", 16, 20,
                    position={"x": 25, "y": 30, "z": -5}, axis="+Z",
                ),
            ],
        },
    },
    "reject-modifier-as-tool": {
        "description": "a through_hole's id used as a subtract tool",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P11",),
        "plan": {
            "status": "generated",
            "summary": "a modifier id used as a solid",
            "operations": [
                _box("plate", 100, 60, 10),
                _through_hole("hole1", "plate", 8, 10, 10),
                _subtract("cut", "plate", ["hole1"]),
            ],
        },
    },
    "reject-two-unconsumed-solids": {
        "description": (
            "two solids and no subtract -- a valid plan, and an invalid part: "
            "the existing V1 validator rejects it with S9"
        ),
        "expects": BUILD_REJECTED,
        "expected_codes": ("S9",),
        "plan": {
            "status": "generated",
            "summary": "two unconnected solids",
            "operations": [
                _box("plate", 100, 60, 10),
                _cylinder(
                    "spare", 16, 20,
                    position={"x": 200, "y": 200, "z": 0}, axis="+Z",
                ),
            ],
        },
    },
    # --- Stage 35: fillet ---------------------------------------------
    "fillet-box-z": {
        "description": (
            "the plate with its four vertical corners rounded, r2 "
            "(axis_parallel Z)"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_FILLET_Z,
        "expected_face_count": 10,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with rounded vertical corners",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 2, _axis_parallel("Z")),
            ],
        },
    },
    "fillet-box-x": {
        "description": "the plate's four X-parallel edges rounded, r2",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_FILLET_X,
        "expected_face_count": 10,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with its long edges rounded",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 2, _axis_parallel("X")),
            ],
        },
    },
    "fillet-box-all": {
        "description": (
            "the plate with every edge rounded, r2 -- permitted on a plain "
            "box, and cross-checked against cad-core rather than a number"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": CROSS_CHECKED,
        "expected_face_count": 26,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with every edge rounded",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 2, _ALL),
            ],
        },
    },
    "fillet-after-through-hole": {
        "description": (
            "a drilled plate, then its X-parallel corners rounded. Z would "
            "select the cavity seam and fail E5 -- see the docs"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _DRILLED_FILLET_X,
        "expected_face_count": 11,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a drilled plate with rounded long edges",
            "operations": [
                _box("plate", 100, 60, 10),
                _through_hole("hole1", "plate", 20, 50, 30),
                _fillet("round", "plate", 2, _axis_parallel("X")),
            ],
        },
    },
    "fillet-after-subtract": {
        "description": (
            "a plate cut by a cylindrical tool, then its Y-parallel corners "
            "rounded -- a fillet on a solid with history"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _DRILLED_FILLET_Y,
        "expected_face_count": 11,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a cut plate with rounded short edges",
            "operations": [
                _box("plate", 100, 60, 10),
                _cylinder(
                    "tool", 20, 20,
                    position={"x": 50, "y": 30, "z": -5}, axis="+Z",
                ),
                _subtract("bore", "plate", ["tool"]),
                _fillet("round", "plate", 2, _axis_parallel("Y")),
            ],
        },
    },
    # --- Stage 35: fixtures that must be REJECTED ---------------------
    "reject-fillet-no-edges": {
        "description": (
            "a selector that matches nothing -- axis_parallel X on a +Z "
            "cylinder. Rule E4, and only the engine can know it"
        ),
        "expects": BUILD_REJECTED,
        "expected_codes": ("E4",),
        "plan": {
            "status": "generated",
            "summary": "a fillet that would affect nothing",
            "operations": [
                _cylinder("rod", 20, 50, axis="+Z"),
                _fillet("round", "rod", 2, _axis_parallel("X")),
            ],
        },
    },
    "reject-fillet-seam": {
        "description": (
            "a selector that matches only a parameterisation seam -- "
            "axis_parallel Z on a bare cylinder. Rule E5: the edge is not "
            "skipped, the whole fillet fails"
        ),
        "expects": BUILD_REJECTED,
        "expected_codes": ("E5",),
        "plan": {
            "status": "generated",
            "summary": "a fillet on a cylinder's seam",
            "operations": [
                _cylinder("rod", 20, 50, axis="+Z"),
                _fillet("round", "rod", 2, _axis_parallel("Z")),
            ],
        },
    },
    "reject-fillet-bad-selector": {
        "description": "a selector written as a bare string, not an object",
        "expects": PLAN_REJECTED,
        "expected_codes": ("parse",),
        "plan": {
            "status": "generated",
            "summary": "a malformed selector",
            "operations": [
                _box("plate", 100, 60, 10),
                {
                    "id": "round", "type": "fillet", "target": "plate",
                    "parameters": {"radius": 2, "edges": "all"},
                },
            ],
        },
    },
    "reject-fillet-signed-axis": {
        "description": (
            "a signed selector axis. Section C.7 axes are UNSIGNED, and "
            "deliberately a different vocabulary from a cylinder's"
        ),
        "expects": PLAN_REJECTED,
        "expected_codes": ("parse",),
        "plan": {
            "status": "generated",
            "summary": "a signed selector axis",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 2,
                        {"select": "axis_parallel", "axis": "+Z"}),
            ],
        },
    },
    "reject-fillet-zero-radius": {
        "description": "a fillet of radius zero (rule S16)",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P4",),
        "plan": {
            "status": "generated",
            "summary": "a fillet with no radius",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 0, _axis_parallel("Z")),
            ],
        },
    },
    "reject-fillet-negative-radius": {
        "description": "a fillet of negative radius (rule S16)",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P4",),
        "plan": {
            "status": "generated",
            "summary": "a fillet with a negative radius",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", -2, _axis_parallel("Z")),
            ],
        },
    },
    "reject-fillet-future-target": {
        "description": "a fillet targeting an operation that comes later",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P10",),
        "plan": {
            "status": "generated",
            "summary": "a forward reference",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "later", 2, _axis_parallel("Z")),
                _box("later", 10, 10, 10),
            ],
        },
    },
    "reject-fillet-modifier-target": {
        "description": "a fillet targeting a through_hole's id",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P11",),
        "plan": {
            "status": "generated",
            "summary": "a modifier id used as a solid",
            "operations": [
                _box("plate", 100, 60, 10),
                _through_hole("hole1", "plate", 8, 10, 10),
                _fillet("round", "hole1", 2, _axis_parallel("Z")),
            ],
        },
    },
    "reject-fillet-consumed-target": {
        "description": "a fillet targeting a solid a subtract has consumed",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P12",),
        "plan": {
            "status": "generated",
            "summary": "a consumed solid used again",
            "operations": [
                _box("plate", 100, 60, 10),
                _cylinder(
                    "tool", 20, 20,
                    position={"x": 50, "y": 30, "z": -5}, axis="+Z",
                ),
                _subtract("bore", "plate", ["tool"]),
                _fillet("round", "tool", 2, _axis_parallel("Z")),
            ],
        },
    },
    # --- Stage 36: chamfer --------------------------------------------
    "chamfer-box-z": {
        "description": "the plate's four vertical corners bevelled, d2",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_CHAMFER_Z,
        "expected_face_count": 10,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with bevelled vertical corners",
            "operations": [
                _box("plate", 100, 60, 10),
                _chamfer("bevel", "plate", 2, _axis_parallel("Z")),
            ],
        },
    },
    "chamfer-box-x": {
        "description": "the plate's four X-parallel edges bevelled, d2",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _PLATE_CHAMFER_X,
        "expected_face_count": 10,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with bevelled long edges",
            "operations": [
                _box("plate", 100, 60, 10),
                _chamfer("bevel", "plate", 2, _axis_parallel("X")),
            ],
        },
    },
    "chamfer-box-all": {
        "description": (
            "every edge of the plate bevelled -- cross-checked against "
            "cad-core rather than a number"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": CROSS_CHECKED,
        "expected_face_count": 26,
        "expected_hole_count": 0,
        "plan": {
            "status": "generated",
            "summary": "a plate with every edge bevelled",
            "operations": [
                _box("plate", 100, 60, 10),
                _chamfer("bevel", "plate", 2, _ALL),
            ],
        },
    },
    "chamfer-after-through-hole": {
        "description": (
            "a drilled plate, then its X-parallel corners bevelled. Z would "
            "select the cavity seam and fail E5"
        ),
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": _DRILLED_CHAMFER_X,
        "expected_face_count": 11,
        "expected_hole_count": 1,
        "plan": {
            "status": "generated",
            "summary": "a drilled plate with bevelled long edges",
            "operations": [
                _box("plate", 100, 60, 10),
                _through_hole("hole1", "plate", 20, 50, 30),
                _chamfer("bevel", "plate", 2, _axis_parallel("X")),
            ],
        },
    },
    "reject-chamfer-no-edges": {
        "description": "a chamfer selector that matches nothing -- rule E4",
        "expects": BUILD_REJECTED,
        "expected_codes": ("E4",),
        "plan": {
            "status": "generated",
            "summary": "a chamfer that would affect nothing",
            "operations": [
                _cylinder("rod", 20, 50, axis="+Z"),
                _chamfer("bevel", "rod", 2, _axis_parallel("X")),
            ],
        },
    },
    "reject-chamfer-seam": {
        "description": (
            "a chamfer on a cylinder's parameterisation seam -- rule E5, and "
            "the whole feature fails rather than bevelling a subset"
        ),
        "expects": BUILD_REJECTED,
        "expected_codes": ("E5",),
        "plan": {
            "status": "generated",
            "summary": "a chamfer on a seam",
            "operations": [
                _cylinder("rod", 20, 50, axis="+Z"),
                _chamfer("bevel", "rod", 2, _axis_parallel("Z")),
            ],
        },
    },
    "reject-chamfer-zero-distance": {
        "description": "a chamfer of distance zero (rule S17)",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P4",),
        "plan": {
            "status": "generated",
            "summary": "a chamfer with no setback",
            "operations": [
                _box("plate", 100, 60, 10),
                _chamfer("bevel", "plate", 0, _axis_parallel("Z")),
            ],
        },
    },
    "reject-chamfer-signed-axis": {
        "description": "a signed selector axis on a chamfer",
        "expects": PLAN_REJECTED,
        "expected_codes": ("parse",),
        "plan": {
            "status": "generated",
            "summary": "a signed selector axis",
            "operations": [
                _box("plate", 100, 60, 10),
                _chamfer("bevel", "plate", 2,
                         {"select": "axis_parallel", "axis": "+Z"}),
            ],
        },
    },
    "reject-chamfer-modifier-target": {
        "description": "a chamfer targeting a fillet's id",
        "expects": PLAN_REJECTED,
        "expected_codes": ("P11",),
        "plan": {
            "status": "generated",
            "summary": "a modifier id used as a solid",
            "operations": [
                _box("plate", 100, 60, 10),
                _fillet("round", "plate", 2, _axis_parallel("Z")),
                _chamfer("bevel", "round", 2, _axis_parallel("X")),
            ],
        },
    },
    "cylinder-d80-h100-z": {
        "description": "the plan from the Stage 32 brief, verbatim",
        "expected_bounding_box": {"x": 80.0, "y": 80.0, "z": 100.0},
        "expected_face_count": 3,
        "expected_hole_count": 0,
        "expected_volume_mm3": None,
        "plan": {
            "status": "generated",
            "summary": "a cylinder 80 mm across and 100 mm tall along +Z",
            "operations": [
                _cylinder(
                    "body",
                    80,
                    100,
                    position={"x": 0, "y": 0, "z": 0},
                    axis="+Z",
                )
            ],
        },
    },
}

#: Fixture names, in a stable order.
FIXTURE_NAMES: Tuple[str, ...] = tuple(FIXTURES)


def _expected_volume(name: str) -> Any:
    """The volume a fixture should build to, computed from its own numbers.

    A recorded value wins. The fallback derives a lone cylinder's volume
    from its own parameters, and applies only to single-operation cylinder
    fixtures -- anything with a modifier records its expectation explicitly,
    because deriving it here would re-implement the geometry.
    """
    entry = FIXTURES[name]
    recorded = entry.get("expected_volume_mm3")
    if recorded == CROSS_CHECKED:
        return CROSS_CHECKED
    if recorded is not None:
        return float(recorded)
    parameters = entry["plan"]["operations"][0]["parameters"]
    radius = float(parameters["diameter"]) / 2.0
    return math.pi * radius**2 * float(parameters["height"])


def fixture(name: str) -> Dict[str, Any]:
    """One fixture, by name. Raises :class:`KeyError` for an unknown name."""
    if name not in FIXTURES:
        raise KeyError(
            f"unknown fixture {name!r}; available: {', '.join(FIXTURE_NAMES)}"
        )
    entry = dict(FIXTURES[name])
    entry.setdefault("expects", BUILDS)
    entry.setdefault("expected_codes", ())
    if entry["expects"] == BUILDS:
        entry["expected_volume_mm3"] = _expected_volume(name)
    return entry


def building_fixtures() -> Tuple[str, ...]:
    """The fixtures that are expected to produce geometry."""
    return tuple(
        name
        for name in FIXTURE_NAMES
        if FIXTURES[name].get("expects", BUILDS) == BUILDS
    )


def rejecting_fixtures() -> Tuple[str, ...]:
    """The fixtures that exist to be refused, and where."""
    return tuple(
        name
        for name in FIXTURE_NAMES
        if FIXTURES[name].get("expects", BUILDS) != BUILDS
    )


def fixture_plan(name: str) -> Dict[str, Any]:
    """The plan payload of one fixture."""
    return json.loads(json.dumps(fixture(name)["plan"]))


def describe_fixtures() -> List[Dict[str, Any]]:
    """Every fixture, for a development endpoint or a CLI listing."""
    return [
        {
            "name": name,
            "description": FIXTURES[name]["description"],
            "expects": FIXTURES[name].get("expects", BUILDS),
            "expected_codes": list(FIXTURES[name].get("expected_codes", ())),
            "expected_bounding_box": FIXTURES[name].get(
                "expected_bounding_box"
            ),
            "expected_volume_mm3": (
                _expected_volume(name)
                if FIXTURES[name].get("expects", BUILDS) == BUILDS
                else None
            ),
            "expected_face_count": FIXTURES[name].get("expected_face_count"),
            "expected_hole_count": FIXTURES[name].get("expected_hole_count"),
            "operation_count": len(FIXTURES[name]["plan"]["operations"]),
            "source": SOURCE_LABEL,
        }
        for name in FIXTURE_NAMES
    ]


class LocalPlanProvider:
    """Returns a developer-supplied plan at the provider boundary.

    Satisfies :class:`~cad_ai.provider.TextToCadModel` structurally, so
    :class:`~cad_experimental.generation.OperationPlanService` treats it
    exactly as it treats a real provider -- which is the point: the parser,
    the validator and the outcome mapping all run for real.

    It does **not** read :attr:`ModelRequest.user_text`. There is no
    interpretation here, and pretending otherwise by matching on the text
    would make this look like a model.
    """

    #: The name that reaches generation metadata.
    name = PROVIDER_NAME

    #: True on this class and absent from every real provider, so a caller
    #: can tell a development result from a model result by type, not by
    #: reading a string.
    is_local_development = True

    def __init__(
        self,
        *,
        plan: Optional[Mapping[str, Any]] = None,
        fixture_name: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        """Give exactly one of ``plan``, ``fixture_name`` or ``raw_text``.

        ``raw_text`` exists so a developer can feed deliberately malformed
        text through the real parser and watch it be rejected -- the failure
        paths deserve exercising too.
        """
        supplied = [
            value is not None for value in (plan, fixture_name, raw_text)
        ]
        if sum(supplied) != 1:
            raise ValueError(
                "give exactly one of plan, fixture_name or raw_text"
            )
        if fixture_name is not None:
            self._text = json.dumps(fixture_plan(fixture_name))
            self._fixture = fixture_name
        elif plan is not None:
            self._text = json.dumps(plan)
            self._fixture = None
        else:
            self._text = raw_text or ""
            self._fixture = None
        self.requests: List[ModelRequest] = []

    @property
    def fixture_name(self) -> Optional[str]:
        return self._fixture

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Hand back the supplied text. No network, no reasoning, no retry."""
        self.requests.append(request)
        return ModelResponse(
            text=self._text,
            provider=PROVIDER_NAME,
            model=MODEL_NAME,
            # No schema was enforced by anything: nothing constrained this
            # text, so claiming structured output would be a lie.
            structured_output=False,
            stop_reason="local_fixture",
            usage={},
        )


class UnavailableProvider:
    """Raises, so "no credential" can be exercised as the outcome it is.

    Not part of the development path; used by tests to show a provider
    failure stays a provider failure and never becomes a wrong answer.
    """

    name = PROVIDER_NAME
    is_local_development = True

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError(
            "no interpretation model is configured",
            detail="local development provider: nothing to call",
        )


def stamp(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a payload as local development output. Called on every result.

    Two fields rather than one: a human reads ``source``, and a program
    checks ``is_live_model_result``. Neither can be omitted by accident,
    because everything goes through here.
    """
    payload["source"] = SOURCE_LABEL
    payload["is_live_model_result"] = False
    payload["note"] = (
        "Local development plan. NOT a Claude/Anthropic result: no model "
        "was called and no credential was used. The real comparison runs "
        "through cad_experimental.harness --live."
    )
    return payload


# --- the development CLI ---------------------------------------------------


def run_fixture(name: str, *, build: bool = True) -> Dict[str, Any]:
    """Push one fixture through the entire real path and report every step."""
    # Imported here so the module's own import stays free of the CAD kernel.
    from cad_core.application_service import CadApplicationService

    from .build import build_plan
    from .config import ExperimentalConfig
    from .generation import OperationPlanService

    entry = fixture(name)
    provider = LocalPlanProvider(fixture_name=name)
    service = OperationPlanService(
        provider, ExperimentalConfig(model=MODEL_NAME, provider=PROVIDER_NAME)
    )

    generation = service.generate(entry["description"])
    expects = entry["expects"]
    expected_codes = tuple(entry["expected_codes"])
    validation = generation.plan_validation
    problems = (
        [problem.to_dict() for problem in validation.problems]
        if validation is not None
        else []
    )
    result: Dict[str, Any] = {
        "fixture": name,
        "description": entry["description"],
        "expects": expects,
        "expected_codes": list(expected_codes),
        "generation_outcome": generation.outcome.value,
        "parsed": generation.plan is not None,
        "plan_valid": bool(validation is not None and validation.valid),
        "problems": problems,
        "plan": (
            generation.plan.to_dict() if generation.plan is not None else None
        ),
        "provider": generation.metadata.provider,
        "model": generation.metadata.model,
    }

    if expects == PLAN_REJECTED:
        # A rejection fixture passes by being refused, and by being refused
        # for the stated reason -- "rejected somehow" would pass even if the
        # rule under test had quietly stopped working.
        observed = ["parse"] if generation.plan is None else [
            problem["code"] for problem in problems
        ]
        result["observed_codes"] = observed
        result["rejected"] = generation.plan is None or not result["plan_valid"]
        result["rejected_for_the_right_reason"] = result["rejected"] and all(
            code in observed for code in expected_codes
        )
        result["error"] = generation.error
        return stamp(result)

    if generation.plan is None:
        result["error"] = generation.error
        return stamp(result)

    from .adapter import plan_to_document

    document = plan_to_document(generation.plan, name=name)
    result["v1_document"] = document
    result["v1_conversion"] = "ok"

    if not build:
        return stamp(result)

    import tempfile

    cad = CadApplicationService.local(tempfile.mkdtemp())

    # The existing validator's verdict, recorded on its own so it is never
    # confused with the plan validator's.
    validation = cad.validate_document(document)
    result["existing_validator_valid"] = validation.valid
    result["document_hash"] = validation.document_hash

    built = build_plan(cad, generation.plan, name=name)
    outcome = built.outcome
    result["built"] = built.built
    if outcome is None or not built.built:
        message = (
            built.error
            if outcome is None
            else (outcome.error.message if outcome.error else "build failed")
        )
        result["build_error"] = message
        if expects == BUILD_REJECTED:
            result["rejected"] = True
            result["observed_codes"] = [
                code for code in expected_codes if code in (message or "")
            ]
            result["rejected_for_the_right_reason"] = all(
                code in (message or "") for code in expected_codes
            )
        return stamp(result)

    if expects == BUILD_REJECTED:
        # It built when it should not have. Recorded plainly rather than
        # dressed up as a pass.
        result["rejected"] = False
        result["rejected_for_the_right_reason"] = False

    geometry = outcome.artifact("geometry")
    details = geometry.details if geometry is not None else {}
    box = (details.get("bounding_box") or {}).get("size")
    volume = details.get("volume_mm3")
    expected_volume = entry["expected_volume_mm3"]

    result.update(
        {
            "build_key": outcome.build_key,
            "solid_count": details.get("solid_count"),
            "is_solid": details.get("is_solid"),
            "face_count": details.get("face_count"),
            "edge_count": details.get("edge_count"),
            "volume_mm3": volume,
            "expected_volume_mm3": expected_volume,
            "bounding_box": box,
            "expected_bounding_box": entry["expected_bounding_box"],
            "expected_face_count": entry["expected_face_count"],
            "expected_hole_count": entry["expected_hole_count"],
        }
    )
    # A hole is a real cylindrical face in the B-rep, so the face count is a
    # topological check on the holes rather than a visual impression.
    result["face_count_matches"] = (
        details.get("face_count") == entry["expected_face_count"]
    )

    import math

    if expected_volume == CROSS_CHECKED:
        # No closed form. `None` rather than True: this fixture's volume is
        # checked against cad-core in the tests, and claiming a match here
        # would be claiming a check that did not happen.
        result["volume_matches"] = None
    else:
        result["volume_matches"] = (
            volume is not None
            and math.isclose(volume, expected_volume, rel_tol=1e-6)
        )
    result["bounding_box_matches"] = box is not None and all(
        math.isclose(
            float(box[axis]),
            float(entry["expected_bounding_box"][axis]),
            rel_tol=1e-6,
        )
        for axis in ("x", "y", "z")
    )

    render = outcome.render_model
    if render is None:
        result["render_model"] = None
    else:
        result["render_model"] = {
            "triangles": render.triangle_count(),
            "vertices": len(render.vertices),
            "units": render.units,
            "coordinate_system": render.coordinate_system,
            "bounds": {
                "minimum": list(render.bounds.minimum),
                "maximum": list(render.bounds.maximum),
            },
        }
    return stamp(result)


def format_report(results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("=" * 74)
    lines.append("LOCAL DEVELOPMENT PLAN -- architecture check")
    lines.append("=" * 74)
    lines.append("NOT a Claude/Anthropic result. No model was called.")
    lines.append("Plans are developer-supplied fixtures; everything after the")
    lines.append("provider boundary is the real implementation.")
    lines.append("")
    for entry in results:
        lines.append("-" * 74)
        lines.append(f"{entry['fixture']}  --  {entry['description']}")
        lines.append(
            f"  generation {entry['generation_outcome']}   parsed "
            f"{entry['parsed']}   plan_valid {entry['plan_valid']}"
        )
        if entry.get("expects") in (PLAN_REJECTED, BUILD_REJECTED):
            verdict = (
                "REJECTED as expected"
                if entry.get("rejected_for_the_right_reason")
                else "*** NOT REJECTED AS EXPECTED ***"
            )
            lines.append(
                f"  expects {entry['expects']} {entry['expected_codes']}   "
                f"observed {entry.get('observed_codes')}   {verdict}"
            )
            for problem in entry.get("problems", []):
                lines.append(
                    f"  problem    {problem['code']} {problem['where']} "
                    f"{problem['message'][:60]}"
                )
            if entry.get("build_error"):
                lines.append(f"  build error {entry['build_error'][:64]}")
            if entry.get("error"):
                lines.append(f"  error      {entry['error'][:64]}")
            continue
        if "v1_conversion" in entry:
            lines.append(
                f"  v1 conversion {entry['v1_conversion']}   existing "
                f"validator {entry.get('existing_validator_valid')}"
            )
        if "built" in entry:
            lines.append(
                f"  built {entry['built']}   solids "
                f"{entry.get('solid_count')}   faces "
                f"{entry.get('face_count')} (expected "
                f"{entry.get('expected_face_count')}, match "
                f"{entry.get('face_count_matches')})   holes "
                f"{entry.get('expected_hole_count')}"
            )
            lines.append(
                f"  volume {entry.get('volume_mm3')}  expected "
                f"{entry.get('expected_volume_mm3')}  match "
                f"{entry.get('volume_matches')}"
            )
            lines.append(
                f"  bbox   {entry.get('bounding_box')}  expected "
                f"{entry.get('expected_bounding_box')}  match "
                f"{entry.get('bounding_box_matches')}"
            )
            render = entry.get("render_model")
            lines.append(
                f"  render {render['triangles']} triangles, "
                f"{render['vertices']} vertices, {render['units']}"
                if render
                else "  render NONE"
            )
        if entry.get("build_error"):
            lines.append(f"  build error {entry['build_error']}")
        if entry.get("error"):
            lines.append(f"  error {entry['error']}")
    lines.append("=" * 74)
    building = [
        e for e in results if e.get("expects", BUILDS) == BUILDS
    ]
    rejecting = [e for e in results if e.get("expects", BUILDS) != BUILDS]
    built = sum(1 for e in building if e.get("built"))
    ok = sum(
        1
        for e in building
        if e.get("volume_matches") and e.get("bounding_box_matches")
    )
    rejected = sum(
        1 for e in rejecting if e.get("rejected_for_the_right_reason")
    )
    lines.append(f"fixtures             {len(results)}")
    lines.append(f"expected to build    {len(building)}")
    lines.append(f"built                {built}")
    lines.append(f"geometry as expected {ok}")
    lines.append(f"expected to reject   {len(rejecting)}")
    lines.append(f"rejected correctly   {rejected}")
    lines.append("=" * 74)
    lines.append(
        "REAL HAIKU COMPARISON: STILL PENDING -- needs ANTHROPIC_API_KEY and "
        "cad_experimental.harness --live."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cad_experimental.local_plan_provider",
        description=(
            "Push a developer-supplied operation plan through the real "
            "parser, validator, adapter, CAD engine and RenderModel. "
            "NOT a model call and NOT a Claude result."
        ),
    )
    parser.add_argument(
        "--list", action="store_true", help="list the fixtures and exit"
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        help=f"fixture to run (default: all). One of: {', '.join(FIXTURE_NAMES)}",
    )
    parser.add_argument(
        "--plan-file",
        default=None,
        help="a JSON file holding a plan to run instead of a fixture",
    )
    parser.add_argument(
        "--no-build", action="store_true", help="stop before the CAD build"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    arguments = parser.parse_args(argv)

    if arguments.list:
        for entry in describe_fixtures():
            print(f"{entry['name']:22s} {entry['description']}")
        return 0

    if arguments.plan_file is not None:
        with open(arguments.plan_file, encoding="utf-8") as handle:
            supplied = json.load(handle)
        results = [_run_supplied(supplied, build=not arguments.no_build)]
    else:
        names = arguments.fixture or list(FIXTURE_NAMES)
        for name in names:
            if name not in FIXTURES:
                print(f"unknown fixture {name!r}", file=sys.stderr)
                print(f"available: {', '.join(FIXTURE_NAMES)}", file=sys.stderr)
                return 2
        results = [
            run_fixture(name, build=not arguments.no_build) for name in names
        ]

    if arguments.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print(format_report(results))
    return 0


def _run_supplied(plan: Mapping[str, Any], *, build: bool) -> Dict[str, Any]:
    """Run a caller's own plan through the same path as a fixture."""
    from cad_core.application_service import CadApplicationService

    from .adapter import AdapterError, plan_to_document
    from .build import build_plan
    from .config import ExperimentalConfig
    from .generation import OperationPlanService

    provider = LocalPlanProvider(plan=plan)
    service = OperationPlanService(
        provider, ExperimentalConfig(model=MODEL_NAME, provider=PROVIDER_NAME)
    )
    generation = service.generate("a plan supplied by a developer")
    result: Dict[str, Any] = {
        "fixture": "(supplied)",
        "description": "a plan supplied on the command line",
        "generation_outcome": generation.outcome.value,
        "parsed": generation.plan is not None,
        "plan_valid": bool(
            generation.plan_validation is not None
            and generation.plan_validation.valid
        ),
        "plan": (
            generation.plan.to_dict() if generation.plan is not None else None
        ),
        "provider": generation.metadata.provider,
        "model": generation.metadata.model,
    }
    if generation.plan is None:
        result["error"] = generation.error
        return stamp(result)
    try:
        result["v1_document"] = plan_to_document(generation.plan)
        result["v1_conversion"] = "ok"
    except AdapterError as exc:
        result["v1_conversion"] = f"refused: {exc}"
        return stamp(result)
    if not build:
        return stamp(result)

    import tempfile

    cad = CadApplicationService.local(tempfile.mkdtemp())
    validation = cad.validate_document(result["v1_document"])
    result["existing_validator_valid"] = validation.valid
    built = build_plan(cad, generation.plan)
    result["built"] = built.built
    outcome = built.outcome
    if outcome is not None and built.built:
        geometry = outcome.artifact("geometry")
        details = geometry.details if geometry is not None else {}
        result.update(
            {
                "solid_count": details.get("solid_count"),
                "face_count": details.get("face_count"),
                "volume_mm3": details.get("volume_mm3"),
                "bounding_box": (details.get("bounding_box") or {}).get("size"),
            }
        )
        render = outcome.render_model
        result["render_model"] = (
            None
            if render is None
            else {
                "triangles": render.triangle_count(),
                "vertices": len(render.vertices),
                "units": render.units,
            }
        )
    elif outcome is not None:
        result["build_error"] = (
            outcome.error.message if outcome.error else "build failed"
        )
    else:
        result["build_error"] = built.error
    return stamp(result)


__all__ = [
    "BUILDS",
    "BUILD_REJECTED",
    "CROSS_CHECKED",
    "FIXTURES",
    "FIXTURE_NAMES",
    "PLAN_REJECTED",
    "building_fixtures",
    "rejecting_fixtures",
    "MODEL_NAME",
    "PROVIDER_NAME",
    "SOURCE_LABEL",
    "LocalPlanProvider",
    "UnavailableProvider",
    "describe_fixtures",
    "fixture",
    "fixture_plan",
    "format_report",
    "main",
    "run_fixture",
    "stamp",
]


if __name__ == "__main__":
    sys.exit(main())
