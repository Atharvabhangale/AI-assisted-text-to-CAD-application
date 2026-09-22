"""Grade one attempt against the immutable ground truth.

The whole point of this module is what it does NOT do. Every expectation
comes from `ground_truth`'s constants. The model's plan is read only to
OBSERVE what it chose, never to decide what it should have chosen, and the
kernel measurement is compared against a constant volume rather than against
a volume computed from the model's own thickness.

Stage 67's criterion failed exactly there. See `ground_truth`'s docstring.
"""
from __future__ import annotations

from collections import Counter

import ground_truth as G

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _axis_letter(axis: str | None) -> str:
    return (axis or "+Z")[-1].upper()


def observe(operations) -> dict:
    """What the model actually wrote. OBSERVATION ONLY -- never an expectation."""
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
                "id": op.id, "target": op.target,
                "diameter": float(op.diameter),
                "axis": _axis_letter(op.axis),
                "position": (float(pos.x), float(pos.y), float(pos.z)),
            })
        elif kind == "union":
            unions.append({"id": op.id, "target": op.target,
                           "tools": list(op.tools or ())})

    def centreline(hole):
        i = AXIS_INDEX[hole["axis"]]
        return (hole["axis"],
                *[round(v, 6) for j, v in enumerate(hole["position"]) if j != i])

    lines = Counter(centreline(h) for h in holes)
    # The model's chosen thickness, RECORDED so it can be graded against the
    # constant -- never so it can become the reference.
    observed_thickness = min((min(b["size"]) for b in boxes), default=None)
    # The outer height the plates actually span along Z.
    span = None
    if boxes:
        span = tuple(round(max(b["position"][i] + b["size"][i] for b in boxes)
                           - min(b["position"][i] for b in boxes), 6)
                     for i in range(3))
    return {
        "boxes": boxes, "holes": holes, "unions": unions,
        "plate_count": len(boxes), "bore_count": len(holes),
        "distinct_centrelines": len(lines),
        "coaxial_duplicates": sum(n - 1 for n in lines.values() if n > 1),
        "axes_used": sorted({h["axis"] for h in holes}),
        "observed_thickness": observed_thickness,
        "observed_span": span,
    }


def grade(seen: dict, measurement, built: bool, failure_message: str | None,
          plan_valid: bool) -> dict:
    """Every check against `ground_truth`'s constants. No self-reference."""
    want = G.expected()
    checks: dict[str, bool] = {}

    # --- what the PLAN says, graded against the constants -----------------
    checks["thickness"] = seen["observed_thickness"] == want["thickness"]
    checks["plate_count"] = seen["plate_count"] == want["plate_count"]
    checks["bore_count"] = seen["bore_count"] == want["bore_count"]
    checks["bore_axes"] = tuple(seen["axes_used"]) == want["bore_axes"]
    checks["no_duplicate_cut"] = seen["coaxial_duplicates"] == 0
    checks["diameter"] = all(h["diameter"] == want["diameter"]
                             for h in seen["holes"]) if seen["holes"] else False
    # "Through the centre of each pair of opposite walls" fixes the two
    # components ACROSS a bore's own axis at the enclosure's centre. The
    # component ALONG the axis has no effect on a through cut and is free.
    # Measured defect this catches: a model that reuses the +Z hole's triple
    # for all three bores leaves z=0 on the +Y and +X bores, dropping their
    # centrelines into the bottom face plane, where they graze.
    checks["bore_centred"] = bool(seen["holes"]) and all(
        all(abs(h["position"][j] - want["centre"][j]) <= 1e-9
            for j in range(3) if j != AXIS_INDEX[h["axis"]])
        for h in seen["holes"])
    checks["plan_valid"] = bool(plan_valid)

    # --- what the KERNEL says, graded against the constants ---------------
    if measurement is None:
        for key in ("built", "solid_count", "envelope", "faces", "edges",
                    "volume", "openings"):
            checks[key] = False
        envelope = None
    else:
        envelope = tuple(round(hi - lo, 6)
                         for lo, hi in zip(measurement.minimum, measurement.maximum))
        checks["built"] = True
        checks["solid_count"] = measurement.solid_count == want["solid_count"]
        checks["envelope"] = sorted(envelope) == sorted(want["envelope"])
        checks["faces"] = measurement.face_count == want["face_count"]
        checks["edges"] = measurement.edge_count == want["edge_count"]
        checks["volume"] = abs(measurement.volume - want["volume"]) <= G.VOLUME_TOLERANCE
        checks["openings"] = (
            measurement.face_count - G.PLANAR_FACES == want["opening_count"])

    return {
        "checks": checks,
        "envelope_observed": envelope,
        "thickness_observed": seen["observed_thickness"],
        "volume_expected": want["volume"],
        "volume_observed": None if measurement is None else measurement.volume,
        "volume_delta": (None if measurement is None
                         else abs(measurement.volume - want["volume"])),
        "STRICT_SUCCESS": all(checks.values()),
    }


def classify(seen: dict, verdict: dict, failure_message: str | None) -> list[str]:
    """The Stage 68 taxonomy: A-I, each decided against the constants."""
    want = G.expected()
    checks = verdict["checks"]
    codes: list[str] = []

    if not checks["thickness"]:
        codes.append(G.A_THICKNESS)
    if verdict["envelope_observed"] is not None and not checks["envelope"]:
        codes.append(G.B_ENVELOPE_HEIGHT)
    elif seen["observed_span"] and sorted(seen["observed_span"]) != sorted(want["envelope"]):
        codes.append(G.B_ENVELOPE_HEIGHT)
    if not checks["plate_count"]:
        codes.append(G.C_PLATE_PLACEMENT)
    if not checks["no_duplicate_cut"]:
        codes.append(G.G_DEGENERATE_CUT)
    if not checks["bore_axes"] and seen["holes"]:
        codes.append(G.F_BORE_DIRECTION)
    if not checks["bore_centred"] or (
            failure_message and "centreline" in failure_message):
        codes.append(G.E_BORE_POSITION)
    if failure_message and "split the body" in failure_message:
        codes.append(G.D_OPPOSING_PLATE)
    if not checks["plan_valid"]:
        codes.append(G.H_WRONG_TARGET)
    if not codes and not verdict["STRICT_SUCCESS"]:
        codes.append(G.I_OTHER)
    return codes
