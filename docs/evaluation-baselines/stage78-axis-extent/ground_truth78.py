"""Stage 78 immutable truth: an AXIS is a centreline, an EXTENT is a boundary.

Stage 77 measured 240 live calls and found that every one of its fifteen
creation and edit failures is the same mistake: the model writes a
**boundary** coordinate where a **centreline** coordinate is required, and it
fails *toward the extent*. This module pins that distinction so it can be
measured directly instead of inferred from a volume.

Why a new module rather than an edit to `ground_truth77`
--------------------------------------------------------
`ground_truth77` is an immutable instrument: Stage 77's 240 recorded calls
were graded against it, and changing it would silently change what those
numbers mean. Nothing here edits it. This module ADDS a second, narrower
truth over the same two cases, and `evaluate78` applies it ON TOP of the
Stage 77 verdict -- a Stage 78 strict success is a Stage 77 strict success
that ALSO satisfies the axis criterion. A case can therefore never score
better here than it did there.

The structural guarantee, inherited from every corpus in this project
----------------------------------------------------------------------
:func:`axis_truth` takes a case **name** and nothing else. It cannot be
handed a plan, a shape, a measurement or a response, so the truth cannot
drift toward the output. The module imports ``math`` and ``typing`` and
nothing else -- no vendor SDK, no evaluator, no arena.

Absolute where the request fixes it, RELATIONAL where it does not
------------------------------------------------------------------
This is the lesson Stage 75 paid for twice (M4 and M8 were both retired for
pinning something their request never stated), and it applies differently to
the two cases:

* **ED-02 is ABSOLUTE.** Its fixture is deterministic and is built and
  verified before a single model call: the pin is a d20 cylinder whose
  position is ``(100, 20, 0)``. The request says "along its axis", so the
  bore's centreline is fully determined -- ``x = 100``, ``y = 20``. There is
  exactly one right answer and the corpus may name it.

* **CR-06 is RELATIONAL.** It is a creation case: the request gives three
  bodies' dimensions and the word "beside", and says nothing about where any
  of them sits in space. Pinning an absolute axis coordinate would be
  inventing a number the request never states. What IS determined is that a
  cylinder's axis must stand clear of its neighbour's extent **by at least
  its own radius**, and that is what is pinned.

Pinning an absolute coordinate for CR-06 would score the model wrong for a
placement the request permits. Refusing to pin anything would score the
overlap right. Neither is acceptable, so the criterion is the one the
request actually entails.
"""

from __future__ import annotations

import math
from typing import Dict, Final, Mapping, Optional, Sequence, Tuple

# --------------------------------------------------------------- the model

#: The one model these expectations are defined against. A number measured on
#: any other model is a different number and must not be compared to these.
MODEL: Final[str] = "claude-haiku-4-5-20251001"

#: The prompt and encoding Stage 77's baseline was taken under. An arm
#: changes the prompt ON PURPOSE; the ENCODING and the MODEL must not move,
#: and `arena78` refuses to start if they do.
STAGE77_PROMPT_VERSION: Final[str] = "2026-09-25.1"
STAGE77_PROMPT_FINGERPRINT: Final[str] = (
    "f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675")
STAGE77_PROMPT_CHARACTERS: Final[int] = 34036
ENCODING_NAME: Final[str] = "strict_selector_union_part"
ENCODING_FINGERPRINT: Final[str] = (
    "ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f")

# ----------------------------------------------------- the recorded history
#
# Stage 77's per-case strict rates, re-read from `results.json` at commit
# cad3325 rather than recalled. These are HISTORICAL: they were measured
# under the Stage 77 prompt and they are never re-scored, never merged with a
# Stage 78 number, and never edited.

STAGE77_BASELINE: Final[Mapping[str, Tuple[int, int]]] = {
    "ED-02": (21, 32),
    "CR-06": (28, 32),
}

#: What the fifteen Stage 77 failures actually were, computed from the raw
#: recorded plans in `results.json` -- not from the summary, and not recalled.
#: Every one is the same mechanism.
STAGE77_MECHANISM: Final[Mapping[str, str]] = {
    "ED-02": ("11/11 failures bored at x = 110.0, the pin's MAX EXTENT, "
              "where its axis is at x = 100.0. The eleven raw texts are NOT "
              "byte-identical -- there are six distinct SHA-256 prefixes -- "
              "but the defect is: every one of the eleven chose 110.0."),
    "CR-06": ("4/4 failures put the first cylinder's AXIS at x = 60.0, the "
              "plate's MAX EXTENT, so the d16 cylinder spanned 52.0..68.0 "
              "and overlapped the plate over 52.0..60.0."),
}

# ------------------------------------------------------------- closed forms


def _cyl(diameter: float, height: float) -> float:
    return math.pi * (diameter / 2.0) ** 2 * height


#: ED-02's fixture, `cube_pin`. Deterministic: `fixtures77.plan_for` builds
#: it and `arena77` asserts the built part matches before any model call, so
#: these are not assumptions about what the model will do -- they are facts
#: about the part it is handed.
CUBE_EDGE: Final[float] = 40.0
CUBE_VOLUME: Final[float] = CUBE_EDGE ** 3                     # 64000.0

PIN_DIAMETER: Final[float] = 20.0
PIN_HEIGHT: Final[float] = 30.0
PIN_RADIUS: Final[float] = PIN_DIAMETER / 2.0                  # 10.0
#: The cylinder's `position` is its BASE CENTRE -- so this IS the axis, and
#: that is exactly the thing the model confuses with a face.
PIN_AXIS_X: Final[float] = 100.0
PIN_AXIS_Y: Final[float] = 20.0
PIN_BASE_Z: Final[float] = 0.0

PIN_MIN_X: Final[float] = PIN_AXIS_X - PIN_RADIUS              # 90.0
PIN_MAX_X: Final[float] = PIN_AXIS_X + PIN_RADIUS              # 110.0
PIN_MIN_Y: Final[float] = PIN_AXIS_Y - PIN_RADIUS              # 10.0
PIN_MAX_Y: Final[float] = PIN_AXIS_Y + PIN_RADIUS              # 30.0
PIN_MIN_Z: Final[float] = PIN_BASE_Z                           # 0.0
PIN_MAX_Z: Final[float] = PIN_BASE_Z + PIN_HEIGHT              # 30.0
#: The solid's geometric centre. Note it shares x and y with the AXIS and
#: differs only in z -- so a check that only looked at "the centre" would not
#: separate the two. The axis is a LINE; the centre is a POINT on it.
PIN_CENTRE: Final[Tuple[float, float, float]] = (
    PIN_AXIS_X, PIN_AXIS_Y, PIN_BASE_Z + PIN_HEIGHT / 2.0)     # (100,20,15)

PIN_VOLUME: Final[float] = _cyl(PIN_DIAMETER, PIN_HEIGHT)      # 9424.777960769
BORE_DIAMETER: Final[float] = 6.0
#: 8576.547944300135 -- independently re-derived here, and equal to the value
#: `ground_truth77` records Stage 72 as having measured deterministically.
PIN_BORED_VOLUME: Final[float] = PIN_VOLUME - _cyl(BORE_DIAMETER, PIN_HEIGHT)

#: CR-06's three bodies. Dimensions only: the request states no placement.
PLATE_X: Final[float] = 60.0
PLATE_Y: Final[float] = 40.0
PLATE_Z: Final[float] = 8.0
PLATE_VOLUME: Final[float] = PLATE_X * PLATE_Y * PLATE_Z       # 19200.0
BOSS_DIAMETER: Final[float] = 16.0
BOSS_HEIGHT: Final[float] = 20.0
BOSS_RADIUS: Final[float] = BOSS_DIAMETER / 2.0                # 8.0
BOSS_VOLUME: Final[float] = _cyl(BOSS_DIAMETER, BOSS_HEIGHT)
ROD_DIAMETER: Final[float] = 10.0
ROD_HEIGHT: Final[float] = 40.0
ROD_RADIUS: Final[float] = ROD_DIAMETER / 2.0                  # 5.0
ROD_VOLUME: Final[float] = _cyl(ROD_DIAMETER, ROD_HEIGHT)

#: Relative tolerance on a kernel volume against a closed form. The project
#: measures cross-kernel agreement at ~1e-11 on parts of this size; 1e-6 is
#: five orders of headroom. Floating-point equality is never used.
VOLUME_TOLERANCE: Final[float] = 1e-6
#: Absolute tolerance, in millimetres, on a coordinate. A bore that misses
#: its axis by the width of this tolerance is not a rounding artefact of the
#: kernel -- it is the model having written a different number.
COORDINATE_TOLERANCE: Final[float] = 1e-6

# ------------------------------------------------- how a coordinate is named
#
# The vocabulary the whole stage turns on. Given a body and one observed
# coordinate, exactly one of these names it.

AT_AXIS: Final[str] = "axis"
AT_MIN_EXTENT: Final[str] = "min_extent"
AT_MAX_EXTENT: Final[str] = "max_extent"
AT_CENTRE: Final[str] = "centre"
AT_ORIGIN: Final[str] = "origin"
AT_OTHER: Final[str] = "other"

#: Order matters: `axis` is tested first so that a body whose axis happens to
#: coincide with its centre is reported as being ON THE AXIS, which is the
#: thing under test.
COORDINATE_NAMES: Final[Tuple[str, ...]] = (
    AT_AXIS, AT_MIN_EXTENT, AT_MAX_EXTENT, AT_CENTRE, AT_ORIGIN, AT_OTHER)


def name_coordinate(
    observed: Optional[float],
    *,
    axis: float,
    minimum: float,
    maximum: float,
    centre: Optional[float] = None,
    tolerance: float = COORDINATE_TOLERANCE,
) -> str:
    """Which landmark of a body does ``observed`` sit on?

    This is the measurement instrument of Stage 78. It takes the landmarks as
    ARGUMENTS -- it never reads them from a plan -- so a caller cannot make
    the answer come out right by handing it the model's own numbers as the
    truth. `evaluate78` always supplies them from this module.

    `None` (the model wrote no coordinate at all) is `other`, not `axis`: an
    absent number is not a correct one.
    """
    if observed is None:
        return AT_OTHER

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= tolerance

    if close(observed, axis):
        return AT_AXIS
    if close(observed, minimum):
        return AT_MIN_EXTENT
    if close(observed, maximum):
        return AT_MAX_EXTENT
    if centre is not None and close(observed, centre):
        return AT_CENTRE
    if close(observed, 0.0):
        return AT_ORIGIN
    return AT_OTHER


# ----------------------------------------------------------- the truth, by name

#: ED-02: absolute. The fixture fixes the pin, the request says "along its
#: axis", so exactly one centreline is correct and the corpus names it.
_ED02: Final[Dict[str, object]] = {
    "name": "ED-02",
    "kind": "absolute",
    "group": "edit",
    "request": ("Put a 6 mm diameter hole all the way through the cylinder "
                "along its axis. Leave the cube unchanged."),
    "fixture": "cube_pin",
    "feature_body": "pin",
    "feature_axis_direction": "+Z",
    #: THE criterion. The bore's centreline, in the two axes across the bore.
    "feature_centreline": {"x": PIN_AXIS_X, "y": PIN_AXIS_Y},
    "body_landmarks": {
        "x": {"axis": PIN_AXIS_X, "minimum": PIN_MIN_X,
              "maximum": PIN_MAX_X, "centre": PIN_AXIS_X},
        "y": {"axis": PIN_AXIS_Y, "minimum": PIN_MIN_Y,
              "maximum": PIN_MAX_Y, "centre": PIN_AXIS_Y},
    },
    "body_dimensions": {"diameter": PIN_DIAMETER, "height": PIN_HEIGHT},
    "body_placement": {"x": PIN_AXIS_X, "y": PIN_AXIS_Y, "z": PIN_BASE_Z},
    "body_min_extent": (PIN_MIN_X, PIN_MIN_Y, PIN_MIN_Z),
    "body_max_extent": (PIN_MAX_X, PIN_MAX_Y, PIN_MAX_Z),
    "body_geometric_centre": PIN_CENTRE,
    "expected_final_volumes": {"cube": CUBE_VOLUME, "pin": PIN_BORED_VOLUME},
    "untouched_body": "cube",
    "untouched_volume": CUBE_VOLUME,
    "volume_tolerance": VOLUME_TOLERANCE,
    "coordinate_tolerance": COORDINATE_TOLERANCE,
    #: What Stage 77 saw 11/11. Recorded so the classifier can be tested
    #: against a defect that really happened, never to define success.
    "recorded_defect": {"x": PIN_MAX_X, "named": AT_MAX_EXTENT},
}

#: CR-06: relational. The request states three bodies' dimensions and the
#: word "beside"; it states no placement, so none is pinned.
_CR06: Final[Dict[str, object]] = {
    "name": "CR-06",
    "kind": "relational",
    "group": "creation",
    "request": ("Create three separate bodies: a 60 x 40 x 8 mm plate, a "
                "16 mm diameter cylinder 20 mm long beside it, and a 10 mm "
                "diameter cylinder 40 mm long beside that."),
    "fixture": None,
    "bodies": 3,
    "expected_volumes": (PLATE_VOLUME, BOSS_VOLUME, ROD_VOLUME),
    "cylinder_radii": (BOSS_RADIUS, ROD_RADIUS),
    #: THE criterion, and it is a relation, not a coordinate: every pair of
    #: bodies must be strictly disjoint, which for a cylinder means its AXIS
    #: stands at least its own radius clear of the neighbour it is "beside".
    #: A cylinder whose axis sits ON a neighbour's face is half inside it.
    "requires_pairwise_disjoint": True,
    "axis_clearance_rule": (
        "a cylinder's axis must be at least its own radius clear of any "
        "other body's extent; an axis placed ON a boundary puts half the "
        "cylinder inside the neighbour"),
    "volume_tolerance": VOLUME_TOLERANCE,
    "coordinate_tolerance": COORDINATE_TOLERANCE,
    "recorded_defect": {
        "axis_on_neighbour_max_extent": True, "named": AT_MAX_EXTENT},
}

_TRUTH: Final[Mapping[str, Mapping[str, object]]] = {
    "ED-02": _ED02,
    "CR-06": _CR06,
}

CASES: Final[Tuple[str, ...]] = ("ED-02", "CR-06")


def axis_truth(case_name: str) -> Dict[str, object]:
    """The axis-versus-extent truth for one case, BY NAME.

    Takes a case name, never a plan, never a shape, never a measurement and
    never a response. The signature is the guarantee: it is not possible to
    hand this function anything a model produced, so it is not possible for
    the truth to drift toward the output.
    """
    entry = _TRUTH.get(case_name)
    if entry is None:
        raise KeyError(
            f"unknown case {case_name!r}; known: {', '.join(sorted(_TRUTH))}")
    return dict(entry)


def stage77_baseline(case_name: str) -> Tuple[int, int]:
    """The HISTORICAL Stage 77 rate. Never re-scored, never merged."""
    if case_name not in STAGE77_BASELINE:
        raise KeyError(f"no Stage 77 baseline recorded for {case_name!r}")
    return STAGE77_BASELINE[case_name]


__all__ = [
    "AT_AXIS", "AT_CENTRE", "AT_MAX_EXTENT", "AT_MIN_EXTENT", "AT_ORIGIN",
    "AT_OTHER", "BORE_DIAMETER", "BOSS_DIAMETER", "BOSS_HEIGHT",
    "BOSS_RADIUS", "BOSS_VOLUME", "CASES", "COORDINATE_NAMES",
    "COORDINATE_TOLERANCE", "CUBE_EDGE", "CUBE_VOLUME", "ENCODING_FINGERPRINT",
    "ENCODING_NAME", "MODEL", "PIN_AXIS_X", "PIN_AXIS_Y", "PIN_BASE_Z",
    "PIN_BORED_VOLUME", "PIN_CENTRE", "PIN_DIAMETER", "PIN_HEIGHT",
    "PIN_MAX_X", "PIN_MAX_Y", "PIN_MAX_Z", "PIN_MIN_X", "PIN_MIN_Y",
    "PIN_MIN_Z", "PIN_RADIUS", "PIN_VOLUME", "PLATE_VOLUME", "PLATE_X",
    "PLATE_Y", "PLATE_Z", "ROD_DIAMETER", "ROD_HEIGHT", "ROD_RADIUS",
    "ROD_VOLUME", "STAGE77_BASELINE", "STAGE77_MECHANISM",
    "STAGE77_PROMPT_CHARACTERS", "STAGE77_PROMPT_FINGERPRINT",
    "STAGE77_PROMPT_VERSION", "VOLUME_TOLERANCE", "axis_truth",
    "name_coordinate", "stage77_baseline",
]
