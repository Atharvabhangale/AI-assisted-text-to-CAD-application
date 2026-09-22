"""Stage 69: the per-bore axis-centre analysis, and nothing else.

Stage 68 left exactly one failure standing on the EXPLICIT request. One
attempt in eight wrote the `+Z` bore's position triple and reused it verbatim
for the `+Y` and `+X` bores, leaving `z = 0` on both. For `+Z` the z
component is the ignored along-axis one; for `+Y` and `+X` it is an
across-axis component and must sit at the enclosure's centre, so those two
centrelines dropped into the bottom face plane and grazed.

This module ADDS a reading; it replaces nothing. Stage 68's `ground_truth.py`
and `evaluate.py` are imported unmodified and every expectation still comes
from `ground_truth`'s constants -- in particular `CENTRE`, which is derived
from `ENVELOPE` and was fixed before any model was called.

Nothing here reads a plan to decide what the plan should have said.
"""
from __future__ import annotations

from collections import Counter
from typing import Final

import ground_truth as G
from evaluate import AXIS_INDEX

#: Component agreement tolerance. The plan carries exact decimals, so this is
#: a float-noise guard, not a grading allowance.
TOLERANCE: Final[float] = 1e-9


def across_axis_components(hole: dict) -> tuple[int, ...]:
    """The two component indices that a through cut along this axis fixes."""
    along = AXIS_INDEX[hole["axis"]]
    return tuple(j for j in range(3) if j != along)


def hole_is_centred(hole: dict) -> bool:
    """True when both ACROSS-axis components sit at the ground-truth centre.

    The ALONG-axis component is free: a through cut is unaffected by where
    along its own centreline the operation is anchored.
    """
    return all(abs(hole["position"][j] - G.CENTRE[j]) <= TOLERANCE
               for j in across_axis_components(hole))


def wrong_components(hole: dict) -> list[dict]:
    """Which across-axis components missed, and by how much."""
    out = []
    for j in across_axis_components(hole):
        got = hole["position"][j]
        if abs(got - G.CENTRE[j]) > TOLERANCE:
            out.append({"component": "XYZ"[j], "expected": G.CENTRE[j],
                        "observed": got, "delta": round(got - G.CENTRE[j], 9)})
    return out


def triple_reuse(holes: list[dict]) -> dict:
    """Did one position triple get copied across bores of different axes?

    This is the Stage 68 residual's exact shape. It is reported as an
    OBSERVATION with its own count; whether it is a defect is decided by the
    rate, not by one sighting.
    """
    triples = Counter(tuple(round(v, 9) for v in h["position"]) for h in holes)
    shared = {t: n for t, n in triples.items() if n > 1}
    axes_sharing = {}
    for t in shared:
        axes_sharing[t] = sorted(h["axis"] for h in holes
                                 if tuple(round(v, 9) for v in h["position"]) == t)
    # The measured defect: a single triple used by bores on two or more
    # DIFFERENT axes. Two bores on the same axis sharing a triple is a
    # duplicate cut (Stage 68's code G), a different failure.
    cross_axis = {t: axes for t, axes in axes_sharing.items()
                  if len(set(axes)) > 1}
    z_sourced = any(("Z" in axes and len(set(axes)) > 1)
                    for axes in cross_axis.values())
    return {
        "any_shared_triple": bool(shared),
        "cross_axis_reuse": bool(cross_axis),
        "all_three_identical": len(holes) == 3 and len(triples) == 1,
        "z_triple_reused": bool(z_sourced),
        "shared": [{"triple": list(t), "axes": axes}
                   for t, axes in sorted(cross_axis.items())],
    }


def analyse(seen: dict) -> dict:
    """The per-attempt reading. `seen` is Stage 68's `evaluate.observe` output."""
    holes = seen.get("holes") or []
    per_axis: dict[str, dict] = {}
    for axis in G.BORE_AXES:
        matching = [h for h in holes if h["axis"] == axis]
        if not matching:
            per_axis[axis] = {"present": False, "centred": None, "wrong": None,
                              "position": None}
            continue
        # More than one bore on an axis is a duplicate cut; grade the set.
        per_axis[axis] = {
            "present": True,
            "count": len(matching),
            "centred": all(hole_is_centred(h) for h in matching),
            "wrong": [c for h in matching for c in wrong_components(h)],
            "position": [list(h["position"]) for h in matching],
        }
    reuse = triple_reuse(holes)
    centred = [h for h in holes if hole_is_centred(h)]
    return {
        "bore_count": len(holes),
        "per_axis": per_axis,
        "holes_centred": len(centred),
        "all_centred": bool(holes) and len(centred) == len(holes),
        "triple_reuse": reuse,
        # Any bore defect that is NOT the centre failure: a missing axis, a
        # duplicate coaxial cut, or a wrong diameter.
        "other_bore_error": bool(
            tuple(seen.get("axes_used") or ()) != G.BORE_AXES
            or seen.get("coaxial_duplicates")
            or len(holes) != G.BORE_COUNT
            or any(h["diameter"] != G.DIAMETER for h in holes)),
    }


def summarise(analyses: list[dict]) -> dict:
    """Phase 2's rates, over a run's attempts."""
    n = len(analyses)
    def rate(pred):
        return sum(1 for a in analyses if pred(a))
    per_axis = {}
    for axis in G.BORE_AXES:
        present = [a for a in analyses if a["per_axis"][axis]["present"]]
        per_axis[axis] = {
            "present": len(present),
            "centred": sum(1 for a in present if a["per_axis"][axis]["centred"]),
        }
    return {
        "calls": n,
        "per_axis_centred": per_axis,
        "all_bores_centred": rate(lambda a: a["all_centred"]),
        "cross_axis_triple_reuse": rate(lambda a: a["triple_reuse"]["cross_axis_reuse"]),
        "z_triple_reused": rate(lambda a: a["triple_reuse"]["z_triple_reused"]),
        "all_three_triples_identical": rate(
            lambda a: a["triple_reuse"]["all_three_identical"]),
        "other_bore_error": rate(lambda a: a["other_bore_error"]),
    }
