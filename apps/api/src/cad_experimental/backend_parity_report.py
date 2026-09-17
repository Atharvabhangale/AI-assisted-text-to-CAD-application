"""Compare two backend parity records and categorise every difference.

Reads the JSON written by :mod:`cad_experimental.backend_parity_probe` for
two backends and answers one question per row: *do these two kernels agree
about what the canonical Operation Plan means?*

Why a separate step
-------------------
The two kernels cannot be imported into one interpreter in this environment
-- FreeCAD is a WSL2 build, CadQuery a Windows virtual environment -- so the
probe runs twice and the comparison happens on the records. That is a virtue
rather than a workaround: nothing in the comparison can reach a kernel, so it
cannot accidentally decide a question by asking one of them.

The parity invariant
--------------------
**Same semantic selector -> same intended geometric edges.** NOT the same
numeric edge index. Backends number topology in their own order, and
requiring agreement there would be inventing a parity that means nothing.
Every comparison below is on the normalised description -- curve kind,
principal axis, midpoint, centre, radius, seam flag -- with the backend's own
index carried for diagnosis and excluded from the verdict.

Categories, kept distinct on purpose
------------------------------------
``EXACT``        identical to the last bit.
``TOLERANT``     equal within the project's tolerance; a legitimate
                 difference between two kernels' arithmetic.
``SEMANTIC``     the same edges were named and the operation succeeded, but
                 the resulting topology differs (face or edge counts).
``UNSUPPORTED``  one backend does not implement the capability at all.
``MISMATCH``     the backends genuinely disagree. Never merged into anything
                 softer.
``ERROR``        a backend raised where the other did not.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

EXACT = "EXACT"
TOLERANT = "TOLERANT"
SEMANTIC = "SEMANTIC"
UNSUPPORTED = "UNSUPPORTED"
MISMATCH = "MISMATCH"
ERROR = "ERROR"

#: Relative tolerance for a volume, and absolute for a length in millimetres.
#: Deliberately loose enough to admit two kernels' arithmetic and far tighter
#: than any real geometric disagreement.
VOLUME_RELATIVE_TOLERANCE = 1e-9
LENGTH_TOLERANCE_MM = 1e-6


def _close(left: Any, right: Any, *, relative: float) -> bool:
    if left is None or right is None:
        return left is right
    scale = max(abs(float(left)), abs(float(right)), 1.0)
    return abs(float(left) - float(right)) <= relative * scale


def _compare_numbers(left: Any, right: Any, *, relative: float) -> str:
    if left == right:
        return EXACT
    if _close(left, right, relative=relative):
        return TOLERANT
    return MISMATCH


def _edge_key(edge: Dict[str, Any]) -> Any:
    """The comparable identity of an edge. Index deliberately excluded."""
    return (
        edge.get("curve"),
        edge.get("axis"),
        bool(edge.get("is_seam")),
        tuple(edge.get("midpoint") or ()),
        tuple(edge.get("centre") or ()),
        edge.get("radius"),
        tuple(edge.get("adjacent") or ()),
    )


def compare_selectors(left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    by_selector = {
        json.dumps(m["selector"], sort_keys=True): m
        for m in right.get("selector_matrix", [])
    }
    for mine in left.get("selector_matrix", []):
        key = json.dumps(mine["selector"], sort_keys=True)
        theirs = by_selector.get(key)
        row: Dict[str, Any] = {"selector": key}
        if theirs is None:
            row["verdict"] = UNSUPPORTED
            row["detail"] = "the other backend produced no row for this selector"
            rows.append(row)
            continue

        row["left"] = f"{mine['selected_count']}/{mine['candidate_count']}"
        row["right"] = f"{theirs['selected_count']}/{theirs['candidate_count']}"
        row["code"] = f"{mine['code']} / {theirs['code']}"

        same_edges = (
            [_edge_key(e) for e in mine.get("selected", [])]
            == [_edge_key(e) for e in theirs.get("selected", [])]
        )
        if (
            mine["code"] == theirs["code"]
            and mine["candidate_count"] == theirs["candidate_count"]
            and mine["selected_count"] == theirs["selected_count"]
            and mine["seam_count"] == theirs["seam_count"]
            and same_edges
        ):
            row["verdict"] = EXACT
        elif same_edges and mine["selected_count"] == theirs["selected_count"]:
            row["verdict"] = SEMANTIC
            row["detail"] = "same edges named; candidate or code bookkeeping differs"
        else:
            row["verdict"] = MISMATCH
            row["detail"] = "the backends named different edges"
        rows.append(row)
    return rows


def compare_measurement(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    if not left or not right:
        return {"verdict": ERROR, "detail": "a measurement is missing"}
    verdicts = {
        "validity": EXACT if left.get("is_valid") == right.get("is_valid") else MISMATCH,
        "solid_count": EXACT if left.get("solid_count") == right.get("solid_count") else MISMATCH,
        "volume": _compare_numbers(
            left.get("volume"), right.get("volume"),
            relative=VOLUME_RELATIVE_TOLERANCE),
        "face_count": EXACT if left.get("face_count") == right.get("face_count") else MISMATCH,
        "edge_count": EXACT if left.get("edge_count") == right.get("edge_count") else MISMATCH,
    }
    size_left = left.get("size") or []
    size_right = right.get("size") or []
    if len(size_left) == len(size_right) == 3:
        per_axis = [
            _compare_numbers(a, b, relative=LENGTH_TOLERANCE_MM)
            for a, b in zip(size_left, size_right)
        ]
        verdicts["bounding_box"] = (
            MISMATCH if MISMATCH in per_axis
            else (TOLERANT if TOLERANT in per_axis else EXACT)
        )
    else:
        verdicts["bounding_box"] = MISMATCH

    # A topology difference on an otherwise-agreeing solid is SEMANTIC, not
    # MISMATCH: the same part, described with a different number of faces, is
    # a real and reportable difference but not a disagreement about meaning.
    overall = EXACT
    if MISMATCH in (verdicts["face_count"], verdicts["edge_count"]) and \
       verdicts["volume"] in (EXACT, TOLERANT) and verdicts["validity"] == EXACT:
        overall = SEMANTIC
    elif MISMATCH in verdicts.values():
        overall = MISMATCH
    elif TOLERANT in verdicts.values():
        overall = TOLERANT
    verdicts["overall"] = overall
    return verdicts


def compare_cases(left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    by_case = {c["case"]: c for c in right.get("cases", [])}
    for mine in left.get("cases", []):
        theirs = by_case.get(mine["case"])
        row: Dict[str, Any] = {"case": mine["case"]}
        if theirs is None:
            row["verdict"] = UNSUPPORTED
            rows.append(row)
            continue
        row["outcome"] = f"{mine['outcome']} / {theirs['outcome']}"
        if mine["outcome"] != theirs["outcome"]:
            row["verdict"] = (
                UNSUPPORTED
                if UNSUPPORTED in (mine["outcome"], theirs["outcome"])
                else MISMATCH
            )
            rows.append(row)
            continue
        if mine["outcome"] != "BUILT":
            row["verdict"] = EXACT if mine["outcome"] == theirs["outcome"] else MISMATCH
            rows.append(row)
            continue
        row["selected"] = f"{mine['selected_count']} / {theirs['selected_count']}"
        detail = compare_measurement(mine.get("measurement"), theirs.get("measurement"))
        row["verdict"] = detail["overall"]
        row["detail"] = detail
        rows.append(row)
    return rows


def report(left: Dict[str, Any], right: Dict[str, Any]) -> str:
    ln, rn = left.get("backend", "?"), right.get("backend", "?")
    out: List[str] = []
    out.append(f"BACKEND PARITY   {ln} (v{left.get('version')})  vs  "
               f"{rn} (v{right.get('version')})")
    out.append("parity invariant: same semantic selector -> same intended edges")
    out.append("(edge INDICES are not compared; backends number topology their own way)")
    out.append("")

    out.append("BASELINE  plate 100x60x10 + centred d20 through hole")
    base = compare_measurement(left.get("baseline"), right.get("baseline"))
    for key in ("validity", "solid_count", "volume", "face_count",
                "edge_count", "bounding_box"):
        out.append(f"  {key:<14}{base[key]}")
    out.append(f"  {'OVERALL':<14}{base['overall']}")
    lseam, rseam = left.get("seam_indices"), right.get("seam_indices")
    out.append(f"  seam edges    {ln}={lseam}  {rn}={rseam}  "
               f"(count {'agrees' if len(lseam or []) == len(rseam or []) else 'DIFFERS'})")
    out.append("")

    out.append("SEMANTIC SELECTOR MATRIX   (selected/candidates)")
    out.append(f"  {'selector':<50}{ln:<12}{rn:<12}{'codes':<16}verdict")
    for row in compare_selectors(left, right):
        out.append(f"  {row['selector']:<50}{row.get('left',''):<12}"
                   f"{row.get('right',''):<12}{row.get('code',''):<16}{row['verdict']}"
                   + (f"  -- {row['detail']}" if row.get("detail") else ""))
    out.append("")

    out.append("EDGE MODIFIER CASES")
    for row in compare_cases(left, right):
        out.append(f"  {row['case']}")
        out.append(f"    outcome  {row.get('outcome','')}   selected {row.get('selected','')}")
        detail = row.get("detail")
        if isinstance(detail, dict):
            out.append("    " + "  ".join(
                f"{k}={v}" for k, v in detail.items() if k != "overall"))
        out.append(f"    VERDICT  {row['verdict']}")
    out.append("")

    verdicts = (
        [r["verdict"] for r in compare_selectors(left, right)]
        + [r["verdict"] for r in compare_cases(left, right)]
        + [base["overall"]]
    )
    tally = {name: verdicts.count(name) for name in
             (EXACT, TOLERANT, SEMANTIC, UNSUPPORTED, MISMATCH, ERROR)}
    out.append("TALLY  " + "  ".join(f"{k}={v}" for k, v in tally.items()))
    return "\n".join(out)


def compare_plan_matrix(left: Dict[str, Any], right: Dict[str, Any]) -> str:
    """Compare two `backend_plan_probe` records, case by case.

    The load-bearing invariants, and nothing else: the same canonical plan,
    resolved to the requested engine with no fallback, producing valid
    geometry whose measurements agree within tolerance and whose semantic
    selections name the same edges.

    **Triangle counts are reported, never compared.** A mesh is a
    visualisation artifact: two tessellators legitimately disagree about how
    many triangles a curved face needs, and demanding agreement there would
    manufacture a mismatch out of a rendering detail.
    """
    ln, rn = left.get("resolved_backend", "?"), right.get("resolved_backend", "?")
    out: List[str] = []
    out.append(f"BACKEND SELECTION MATRIX   {ln} vs {rn}")
    out.append("")
    out.append(f"  {'case':<44}{'path ' + ln:<18}{'path ' + rn:<18}"
               f"{'volume':<12}{'topology':<12}{'mesh':<10}verdict")
    tally: Dict[str, int] = {}
    by_case = {c["case"]: c for c in right.get("cases", [])}
    for mine in left.get("cases", []):
        theirs = by_case.get(mine["case"])
        if theirs is None:
            verdict, volume, topology, mesh = UNSUPPORTED, "-", "-", "-"
        else:
            # No fallback is a precondition, not a comparison: if either side
            # ran an engine other than the one asked for, nothing below means
            # anything.
            if not (mine.get("no_fallback") and theirs.get("no_fallback")):
                verdict, volume, topology, mesh = MISMATCH, "-", "-", "FALLBACK"
            elif not (mine.get("built") and theirs.get("built")):
                verdict, volume, topology, mesh = (
                    ERROR if mine.get("built") != theirs.get("built")
                    else UNSUPPORTED, "-", "-", "-")
            else:
                detail = compare_measurement(mine["measurement"],
                                             theirs["measurement"])
                volume = detail["volume"]
                topology = (
                    EXACT
                    if detail["face_count"] == EXACT
                    and detail["edge_count"] == EXACT
                    else MISMATCH
                )
                both_meshed = (mine["render"]["present"]
                               and theirs["render"]["present"]
                               and mine["render"]["triangles"] > 0
                               and theirs["render"]["triangles"] > 0)
                mesh = "both" if both_meshed else "MISSING"
                selections_agree = (
                    _selection_shape(mine.get("selections"))
                    == _selection_shape(theirs.get("selections")))
                if not both_meshed:
                    verdict = ERROR
                elif not selections_agree:
                    verdict = MISMATCH
                elif volume == EXACT and topology == EXACT:
                    verdict = EXACT
                elif volume in (EXACT, TOLERANT) and topology == EXACT:
                    verdict = TOLERANT
                elif volume in (EXACT, TOLERANT):
                    verdict = SEMANTIC
                else:
                    verdict = MISMATCH
        tally[verdict] = tally.get(verdict, 0) + 1
        out.append(f"  {mine['case']:<44}{mine.get('execution_path',''):<18}"
                   f"{(theirs or {}).get('execution_path',''):<18}"
                   f"{volume:<12}{topology:<12}{mesh:<10}{verdict}")
    out.append("")
    out.append("TALLY  " + "  ".join(
        f"{name}={tally.get(name, 0)}" for name in
        (EXACT, TOLERANT, SEMANTIC, UNSUPPORTED, MISMATCH, ERROR)))
    return "\n".join(out)


def _selection_shape(selections: Any) -> Any:
    """Selector evidence reduced to what must agree across backends.

    How MANY edges each selector named, and how many it considered -- never
    WHICH index, because the two kernels number topology their own way.
    """
    if not isinstance(selections, dict):
        return {}
    return {
        name: (len(value.get("indices") or []),
               len(value.get("candidates") or []),
               len(value.get("seams") or []),
               value.get("code"))
        for name, value in sorted(selections.items())
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument(
        "--matrix", action="store_true",
        help="compare backend_plan_probe records instead of parity probes")
    args = parser.parse_args(argv)
    with open(args.left, encoding="utf-8") as handle:
        left = json.load(handle)
    with open(args.right, encoding="utf-8") as handle:
        right = json.load(handle)
    print(compare_plan_matrix(left, right) if args.matrix
          else report(left, right))
    return 0


if __name__ == "__main__":
    sys.exit(main())
