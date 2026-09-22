"""Stage 68: the golden benchmark's IMMUTABLE ground truth.

Stage 67 measured the golden request over 80 live calls and could not tell
model error from benchmark ambiguity, because its criterion derived the
expected plate thickness from the model's own plan:

    t = plan_facts.get("thickness")          # stage67 spatial.py:124

Sixteen parts built from 4 mm plate therefore scored correct against a
request that says 5. **A criterion that grades a part against its own answer
cannot fail it.** This module exists so that cannot happen again.

EVERY number here is a CONSTANT, fixed before any model was called, and
derived only from the request text and closed-form arithmetic. Nothing in
this module reads a plan, a model answer or a kernel measurement. The only
input `expected_*` takes is the identity of a request.

THE TWO REQUESTS describe the SAME part. `ORIGINAL` is kept exactly as it has
always been, ambiguous, and is never edited. `EXPLICIT` states the thickness
and the outer height that `ORIGINAL` leaves to inference, and changes nothing
else -- same plates, same counts, same holes, same geometry.

THE PART, for both:

  * outer envelope 40 x 20 x 20, walls 5 mm thick;
  * six plates -- four 40x20x5 (floor, roof, front, back) and two 20x20x5
    (the two ends), each rotated so its 5 mm runs along its own face normal;
  * THREE through_holes, one per axis. A through_hole passes all the way
    through, so one bore opens the two walls on its centreline: three bores
    are the six openings the request asks for;
  * one solid, 12 planar faces (6 outer + 6 inner) and 2 cylindrical faces
    per bore = 18 faces, 42 edges;
  * volume = (40*20*20 - 30*10*10) - 3 * 2*pi*4^2*5 = 11492.035526276899.
"""
from __future__ import annotations

import math
from typing import Final

# --------------------------------------------------------------- the requests

#: The original benchmark request. AMBIGUOUS, and deliberately never edited:
#: "40*20*5" can read as a 40x20 plate 5 thick or as a 40x20x5 box, and
#: "(4)plates" is a count that has been read as a thickness.
ORIGINAL: Final[str] = (
    "Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 "
    "(2) plates with 8mm diameter holes in center of each plate"
)

#: The companion request. Same part, same plates, same holes; the thickness
#: and the outer height are stated rather than inferred.
EXPLICIT: Final[str] = (
    "Make a hollow rectangular enclosure with an outer size of 40 mm long, "
    "20 mm wide and 20 mm high. Build it from six plates, every one of them "
    "5 mm thick: four plates with a 40 x 20 face form the floor, the roof, "
    "the front wall and the back wall, and two plates with a 20 x 20 face "
    "form the two end walls. Each plate's 5 mm thickness runs along the "
    "normal of the face it covers, and the six plates meet to enclose a "
    "hollow interior. Then put an 8 mm diameter hole through the centre of "
    "each pair of opposite walls, so the finished part has one hole along "
    "each of the three axes."
)

REQUESTS: Final[dict[str, str]] = {"original": ORIGINAL, "explicit": EXPLICIT}

# ----------------------------------------------------------- the ground truth
# Constants. Not one of these is computed from a model answer.

#: Outer envelope, as an unordered extent triple in mm.
ENVELOPE: Final[tuple[float, float, float]] = (40.0, 20.0, 20.0)
#: Plate thickness in mm. The request says 5; nothing may override this.
THICKNESS: Final[float] = 5.0
#: Hole diameter in mm.
DIAMETER: Final[float] = 8.0
#: Six plates: four with a 40x20 face, two with a 20x20 face.
PLATE_COUNT: Final[int] = 6
PLATE_FACES: Final[tuple[tuple[float, float], ...]] = (
    (40.0, 20.0), (40.0, 20.0), (40.0, 20.0), (40.0, 20.0),
    (20.0, 20.0), (20.0, 20.0),
)
#: One bore per axis; each opens a facing pair.
BORE_COUNT: Final[int] = 3
BORE_AXES: Final[tuple[str, ...]] = ("X", "Y", "Z")
#: Each bore pierces two opposing walls, so six physical openings.
OPENING_COUNT: Final[int] = 6
OPPOSING_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("left", "right"), ("front", "back"), ("floor", "roof"),
)
#: Exactly one live body.
SOLID_COUNT: Final[int] = 1
#: 6 outer + 6 inner planar faces, plus two cylindrical faces per bore.
PLANAR_FACES: Final[int] = 12
CYLINDRICAL_FACES: Final[int] = 2 * BORE_COUNT
FACE_COUNT: Final[int] = PLANAR_FACES + CYLINDRICAL_FACES      # 18
EDGE_COUNT: Final[int] = 42

#: Volume tolerance. Kernel agreement on this part is measured at ~1e-11.
VOLUME_TOLERANCE: Final[float] = 1e-6

#: Where a bore's centreline must sit. "Through the centre of each pair of
#: opposite walls" fixes the TWO components ACROSS a bore's own axis at the
#: middle of the enclosure; the third, along the axis, has no effect on a
#: through cut and is free.
CENTRE: Final[tuple[float, float, float]] = tuple(e / 2.0 for e in ENVELOPE)


def _closed_form() -> float:
    """The one volume, from the constants above and nothing else."""
    x, y, z = ENVELOPE
    t, r = THICKNESS, DIAMETER / 2.0
    shell = x * y * z - (x - 2 * t) * (y - 2 * t) * (z - 2 * t)
    return shell - BORE_COUNT * (2 * math.pi * r * r * t)


#: 11492.035526276899 -- the same number the deterministic reader builds and
#: a live model-generated plan has reproduced.
VOLUME: Final[float] = _closed_form()


def expected(_request_key: str | None = None) -> dict:
    """The ground truth. Takes a request NAME, never a plan and never a shape.

    The parameter is accepted so a caller can ask per request, and ignored
    because both requests describe the same part. It is deliberately
    impossible to pass this function anything a model produced.
    """
    return {
        "envelope": ENVELOPE,
        "thickness": THICKNESS,
        "diameter": DIAMETER,
        "plate_count": PLATE_COUNT,
        "bore_count": BORE_COUNT,
        "bore_axes": BORE_AXES,
        "opening_count": OPENING_COUNT,
        "opposing_pairs": OPPOSING_PAIRS,
        "solid_count": SOLID_COUNT,
        "face_count": FACE_COUNT,
        "edge_count": EDGE_COUNT,
        "volume": VOLUME,
        "centre": CENTRE,
    }


# ------------------------------------------------- the failure taxonomy (P3)

A_THICKNESS = "A:thickness"
B_ENVELOPE_HEIGHT = "B:envelope_height"
C_PLATE_PLACEMENT = "C:plate_placement"
D_OPPOSING_PLATE = "D:opposing_plate"
E_BORE_POSITION = "E:bore_position"
F_BORE_DIRECTION = "F:bore_direction"
G_DEGENERATE_CUT = "G:degenerate_cut"
H_WRONG_TARGET = "H:wrong_target"
I_OTHER = "I:other"

TAXONOMY: Final[tuple[str, ...]] = (
    A_THICKNESS, B_ENVELOPE_HEIGHT, C_PLATE_PLACEMENT, D_OPPOSING_PLATE,
    E_BORE_POSITION, F_BORE_DIRECTION, G_DEGENERATE_CUT, H_WRONG_TARGET,
    I_OTHER,
)
