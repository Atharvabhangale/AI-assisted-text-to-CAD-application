"""Stage 67: the golden request's SPATIAL success criterion, judged by the kernel.

Stage 66's arena scored a build "semantically correct" on envelope + targeting
alone. That let an attempt through which drilled ONE hole instead of three: a
valid, one-solid enclosure of exactly the right envelope that is not the part
asked for. This module replaces that with a criterion the kernel decides.

THE CANONICAL PART, for
"a hollow rectangular box with 40*20*5 (4) plates and 20*20 (2) plates with
 8mm diameter holes in center of each plate":

  * six plates of thickness t standing on the six faces of a 40 x 20 x 20 box
    -- the four 40x20 faces and the two 20x20 ends;
  * THREE through_holes, one per axis. A through_hole passes all the way
    through, so one bore opens the two walls on its centreline: 3 bores are
    the 6 physical openings the request asks for;
  * therefore exactly 12 planar faces (6 outer + 6 inner) and 6 cylindrical
    faces (2 per bore) = 18 faces, 42 edges, one solid.

Every number below is asked of the kernel or derived from the model's own
plan. Nothing is inferred from an attempt merely building.
"""
from __future__ import annotations

import math
from collections import Counter

#: The enclosure the request describes, as an unordered extent triple.
CANONICAL_ENVELOPE = (40.0, 20.0, 20.0)
#: 6 outer + 6 inner planar faces, plus 2 cylindrical faces per axial bore.
CANONICAL_FACES = 18
CANONICAL_EDGES = 42
CANONICAL_SOLIDS = 1
#: One bore per axis; each opens a facing pair.
CANONICAL_BORES = 3
CANONICAL_OPENINGS = 6

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

# Failure taxonomy, Phase 2 of the Stage 67 brief.
A_HOLE_PER_WALL = "A:one_hole_per_wall"
B_WRONG_DIRECTION = "B:wrong_direction"
C_WRONG_POSITION = "C:wrong_position"
D_PLATE_PLACEMENT = "D:wrong_plate_placement"
E_MISSING_PLATE = "E:missing_opposing_plate"
F_DUPLICATE_COAXIAL = "F:duplicate_coaxial_cut"
G_OTHER = "G:other"


def _axis_letter(axis: str | None) -> str:
    return (axis or "+Z")[-1].upper()


def describe_plan(operations) -> dict:
    """Read the model's own plan: plates, bores, and how the bores are grouped."""
    boxes, holes, unions = [], [], []
    for op in operations:
        kind = getattr(op, "TYPE", None)
        if kind == "box":
            pos = op.position
            boxes.append({
                "id": op.id,
                "size": (float(op.x), float(op.y), float(op.z)),
                "position": ((float(pos.x), float(pos.y), float(pos.z))
                             if pos is not None else (0.0, 0.0, 0.0)),
            })
        elif kind == "through_hole":
            pos = op.position
            holes.append({
                "id": op.id,
                "target": op.target,
                "diameter": float(op.diameter),
                "axis": _axis_letter(op.axis),
                "position": (float(pos.x), float(pos.y), float(pos.z)),
            })
        elif kind == "union":
            unions.append({"id": op.id, "target": op.target,
                           "tools": list(op.tools or ())})
    # Two bores are COAXIAL when they share an axis and both perpendicular
    # components: the along-axis component has no effect on a through cut.
    def centreline(hole):
        i = AXIS_INDEX[hole["axis"]]
        return (hole["axis"], *[round(v, 6) for j, v in enumerate(hole["position"]) if j != i])
    lines = Counter(centreline(h) for h in holes)
    return {
        "boxes": boxes, "holes": holes, "unions": unions,
        "plate_count": len(boxes), "bore_count": len(holes),
        "distinct_centrelines": len(lines),
        "coaxial_duplicates": sum(n - 1 for n in lines.values() if n > 1),
        "axes_used": sorted({h["axis"] for h in holes}),
        "thickness": min((min(b["size"]) for b in boxes), default=None),
    }


def closed_form_volume(envelope, thickness, diameter, bores) -> float:
    """Shell volume less one cylindrical bore per axis through a facing pair.

    The three centrelines meet only inside the cavity, so no removed volume is
    counted twice.
    """
    x, y, z = envelope
    t, r = thickness, diameter / 2.0
    shell = x * y * z - (x - 2 * t) * (y - 2 * t) * (z - 2 * t)
    return shell - bores * (2 * math.pi * r * r * t)


def verdict(plan_facts: dict, measurement, tolerance: float = 1e-6) -> dict:
    """The eight-point criterion, decided on kernel numbers where possible."""
    out: dict = {"checks": {}, "failures": []}
    if measurement is None:
        out["checks"] = {k: False for k in
                         ("one_solid", "envelope", "faces", "edges", "volume")}
        out["spatially_correct"] = False
        return out

    envelope = tuple(round(hi - lo, 6) for lo, hi in
                     zip(measurement.minimum, measurement.maximum))
    out["envelope"] = envelope
    out["checks"]["one_solid"] = measurement.solid_count == CANONICAL_SOLIDS
    out["checks"]["envelope"] = sorted(envelope) == sorted(CANONICAL_ENVELOPE)
    out["checks"]["faces"] = measurement.face_count == CANONICAL_FACES
    out["checks"]["edges"] = measurement.edge_count == CANONICAL_EDGES

    t = plan_facts.get("thickness")
    d = plan_facts["holes"][0]["diameter"] if plan_facts["holes"] else None
    expected = None
    if t and d and out["checks"]["envelope"]:
        expected = closed_form_volume(CANONICAL_ENVELOPE, t, d, CANONICAL_BORES)
        out["closed_form_volume"] = expected
        out["volume_delta"] = abs(measurement.volume - expected)
        out["checks"]["volume"] = out["volume_delta"] <= max(
            tolerance, abs(expected) * 1e-9)
    else:
        out["checks"]["volume"] = False

    # The six openings are not counted directly; they are implied by the
    # topology: 2 cylindrical faces per bore on top of 12 planar faces.
    out["cylindrical_faces_implied"] = (
        measurement.face_count - 12 if out["checks"]["envelope"] else None)
    out["checks"]["six_openings"] = (
        out["cylindrical_faces_implied"] == CANONICAL_OPENINGS)

    out["spatially_correct"] = all(out["checks"].values())
    return out


def classify(plan_facts: dict, built: bool, failure_message: str | None,
             v: dict | None) -> list[str]:
    """Which of the brief's A-G failure modes actually happened."""
    codes: list[str] = []
    bores = plan_facts["bore_count"]
    plates = plan_facts["plate_count"]

    if plan_facts["coaxial_duplicates"] > 0:
        codes.append(F_DUPLICATE_COAXIAL)
    if bores > CANONICAL_BORES and plan_facts["distinct_centrelines"] > CANONICAL_BORES:
        codes.append(A_HOLE_PER_WALL)
    if bores and len(plan_facts["axes_used"]) < 3:
        codes.append(B_WRONG_DIRECTION)
    if plates < 6:
        codes.append(E_MISSING_PLATE)
    if v and not v["checks"].get("envelope", False) and plates >= 6:
        codes.append(D_PLATE_PLACEMENT)
    if not built and failure_message and "centreline" in failure_message:
        codes.append(C_WRONG_POSITION)
    if not codes and not (v or {}).get("spatially_correct", False):
        codes.append(G_OTHER)
    return codes
