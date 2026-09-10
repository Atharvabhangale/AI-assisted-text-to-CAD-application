"""Stage 40: where the failures came from. A diagnostic, not a score.

The frozen measurement is unambiguous and is reported as it stands: with API
structured output unavailable to one side and therefore disabled for both,
**every one of the 130 live calls was rejected at parse, on both arms**. Both
representations score 0%.

That result is real and it is also uninformative about the question the stage
asks, because one cause dominates it: Claude Haiku wrapped its JSON in a
markdown fence, and both frozen parsers refuse a fence by design. Neither
parser repairs model output -- deliberately, on both sides -- so the strict
transport, not the representation, decided every case.

This module answers the stage's fourth question -- *were the failures caused
by the model, the parser, the validator or the CAD backend?* -- by replaying
the **recorded raw output** through each arm's own frozen parser, validator,
adapter and build, after extracting the fenced JSON block. Nothing is
re-requested and nothing is re-prompted; the model output is exactly what was
recorded.

What this is and is not
-----------------------
It **is** a counterfactual: what each representation would have scored had
the transport tolerated a markdown fence, holding everything else frozen.

It is **not** the result. It does not replace the measured 0%, it is not a
change to the scoring rules, and it was written after the scores were seen --
which is why it is a separate module with a separate name, reported
separately, and why it changes no expectation in
:mod:`cad_experimental.comparison_corpus`.

The extraction is deliberately the *most* forgiving step in the pipeline: one
fenced block, or a bare object. Everything after it is the frozen machinery,
unchanged.
"""

from __future__ import annotations

import collections
import json
import re
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
)

from .adapter import ExecutionUnsupported, plan_to_document
from .comparison_corpus import (
    CASES_BY_ID,
    EXPECT_BUILD,
    EXPECT_UNSUPPORTED,
    EXPECT_VALID_UNEXECUTABLE,
    ComparisonCase,
)
from .parser import PlanParseError, parse_plan
from .representation_comparison import PLAN, V1, _geometry_matches
from .validation import validate_plan

#: One fenced JSON object. The whole of the leniency this module adds.
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

#: The verdict that means the representation would have been right.
CORRECT = "CORRECT"


def extract_json(text: Optional[str]) -> Optional[Any]:
    """The JSON a fence-tolerant transport would have found, or ``None``.

    Tries a fenced block first, then a bare object. Never repairs the JSON
    itself: a malformed body stays malformed.
    """
    if not text:
        return None
    match = FENCE.search(text)
    if match is not None:
        try:
            return json.loads(match.group(1))
        except ValueError:
            return None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except ValueError:
            return None
    return None


def shape_of(text: Optional[str]) -> str:
    """How the model framed its answer. The finding behind the 0%."""
    if not text:
        return "empty"
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        trailing = len(parts) >= 3 and bool(parts[2].strip())
        return "fenced json + prose" if trailing else "fenced json"
    if stripped.startswith("{"):
        return "bare json"
    return "prose only"


def _build_geometry_verdict(
    service: CadApplicationService,
    item: ComparisonCase,
    document: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Build and check, with the same tolerances the frozen scorer uses."""
    outcome = service.build_document(
        BuildDocumentRequest.for_outputs(dict(document), "geometry", "render")
    )
    if not outcome.succeeded:
        error = outcome.error
        return False, (
            f"build failed: {error.message}" if error else "build failed"
        )
    if outcome.render_model is None:
        return False, "no render model"
    geometry = outcome.artifact("geometry")
    details = dict(geometry.details) if geometry is not None else {}
    box = (details.get("bounding_box") or {}).get("size") or {}
    return _geometry_matches(
        item.geometry, details.get("volume_mm3"), box,
        details.get("solid_count"),
    )


def diagnose_v1(
    service: CadApplicationService,
    item: ComparisonCase,
    payload: Any,
) -> str:
    """Replay one V1 answer through the frozen production boundary."""
    if not isinstance(payload, dict):
        return "JSON, but not an object"
    status = payload.get("status")
    if status is None:
        # The single most common V1 failure: a correct document, in the wrong
        # envelope. The frozen schema requires `status`, and the parser is
        # right to insist.
        return "JSON, but no `status` envelope"
    if status == "document":
        document = payload.get("document")
        if not isinstance(document, dict):
            return "status=document but no document"
        verdict = service.validate_document(document)
        if not verdict.valid:
            return "V1 validator rejected the document"
        if item.expect_v1 != EXPECT_BUILD:
            return "answered a request that should have been refused"
        ok, why = _build_geometry_verdict(service, item, document)
        return CORRECT if ok else f"wrong geometry: {why}"
    if status == "unsupported":
        return (
            CORRECT if item.expect_v1 == EXPECT_UNSUPPORTED
            else "wrongly refused"
        )
    if status == "needs_clarification":
        return "asked for clarification"
    return f"unknown status {status!r}"


def diagnose_plan(
    service: CadApplicationService,
    item: ComparisonCase,
    payload: Any,
) -> str:
    """Replay one plan answer through the frozen experimental boundary."""
    try:
        plan = parse_plan(payload)
    except PlanParseError as exc:
        return f"plan parser rejected: {exc.message}"
    verdict = validate_plan(plan)
    if not verdict.valid:
        return f"plan rules rejected: {verdict.problems[0].code}"
    if plan.status.value != "generated":
        if plan.status.value == "unsupported":
            return (
                CORRECT if item.expect_plan == EXPECT_UNSUPPORTED
                else "wrongly refused"
            )
        return "asked for clarification"
    if item.expect_plan == EXPECT_UNSUPPORTED:
        return "answered a request that should have been refused"
    try:
        document = plan_to_document(plan)
    except ExecutionUnsupported:
        required = set(item.required_plan_operations)
        produced = {op.TYPE for op in plan.operations}
        if item.expect_plan == EXPECT_VALID_UNEXECUTABLE and (
            not required or required <= produced
        ):
            return CORRECT
        return "unexecutable, and not the operations asked for"
    except Exception as exc:
        return f"would not translate: {type(exc).__name__}"
    if item.expect_plan == EXPECT_VALID_UNEXECUTABLE:
        return "built a solid where a sketch chain was asked for"
    check = service.validate_document(dict(document))
    if not check.valid:
        return "V1 validator rejected the translated document"
    ok, why = _build_geometry_verdict(service, item, document)
    return CORRECT if ok else f"wrong geometry: {why}"


def diagnose(
    records: Sequence[Mapping[str, Any]],
    *,
    cache_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay every recorded answer. Returns per-arm and per-case findings."""
    service = CadApplicationService.local(cache_root or tempfile.mkdtemp())

    verdicts: List[Dict[str, Any]] = []
    for record in records:
        representation = record["representation"]
        item = CASES_BY_ID[record["case_id"]]
        raw = record.get("raw_text")
        payload = extract_json(raw)
        if payload is None:
            verdict = "no JSON block at all"
        elif representation == V1:
            verdict = diagnose_v1(service, item, payload)
        else:
            verdict = diagnose_plan(service, item, payload)
        verdicts.append({
            "case_id": record["case_id"],
            "representation": representation,
            "attempt": record.get("attempt"),
            "shape": shape_of(raw),
            "verdict": verdict,
            "correct": verdict == CORRECT,
        })

    summary: Dict[str, Any] = {}
    for representation in (V1, PLAN):
        mine = [v for v in verdicts if v["representation"] == representation]
        correct = sum(1 for v in mine if v["correct"])
        summary[representation] = {
            "n": len(mine),
            "correct": correct,
            "rate": correct / len(mine) if mine else None,
            "verdicts": dict(
                collections.Counter(v["verdict"] for v in mine).most_common()
            ),
            "shapes": dict(
                collections.Counter(v["shape"] for v in mine).most_common()
            ),
        }

    per_case: Dict[str, Any] = {}
    for case_id in CASES_BY_ID:
        row: Dict[str, Any] = {}
        for representation in (V1, PLAN):
            mine = [
                v for v in verdicts
                if v["case_id"] == case_id
                and v["representation"] == representation
            ]
            row[representation] = {
                "correct": sum(1 for v in mine if v["correct"]),
                "n": len(mine),
                "verdicts": dict(
                    collections.Counter(
                        v["verdict"] for v in mine if not v["correct"]
                    ).most_common()
                ),
            }
        per_case[case_id] = row

    return {
        "note": (
            "DIAGNOSTIC ONLY. The frozen measurement is 0% on both arms. "
            "This replays the recorded output through the same frozen "
            "machinery after extracting a fenced JSON block, to locate where "
            "each failure originated."
        ),
        "summary": summary,
        "per_case": per_case,
        "verdicts": verdicts,
    }


def format_diagnosis(data: Mapping[str, Any]) -> str:
    """The diagnosis as a readable report."""
    lines = [
        "=" * 72,
        "FAILURE DIAGNOSIS  (counterfactual: a fence-tolerant transport)",
        "  NOT the measured result, which is 0% on both arms.",
        "=" * 72,
        "",
        f"{'':<34}{'V1 JSON':>16}{'Operation Plan':>18}",
        "-" * 68,
    ]
    summary = data["summary"]
    for label, key in (
        ("Would-be correct", "correct"), ("Attempts", "n"),
    ):
        cells = [str(summary[r][key]) for r in (V1, PLAN)]
        lines.append(f"{label:<34}{cells[0]:>16}{cells[1]:>18}")
    rates = [
        f"{summary[r]['rate']:.1%}" if summary[r]["rate"] is not None else "n/a"
        for r in (V1, PLAN)
    ]
    lines.append(f"{'Would-be correctness rate':<34}{rates[0]:>16}{rates[1]:>18}")
    lines.append("")

    for representation in (V1, PLAN):
        lines.append(f"{representation}: how the answer was framed")
        for shape, count in summary[representation]["shapes"].items():
            lines.append(f"    {count:3d}  {shape}")
        lines.append(f"{representation}: where it landed")
        for verdict, count in summary[representation]["verdicts"].items():
            mark = "  ok " if verdict == CORRECT else "  -- "
            lines.append(f"  {mark}{count:3d}  {verdict}")
        lines.append("")

    lines.append("per case (would-be correct / attempts):")
    lines.append(f"{'case':<24}{'V1':>10}{'plan':>10}")
    lines.append("-" * 48)
    for case_id, row in data["per_case"].items():
        lines.append(
            f"{case_id:<24}"
            f"{row[V1]['correct']}/{row[V1]['n']:<8}"
            f"{row[PLAN]['correct']}/{row[PLAN]['n']:<8}"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Diagnose a saved comparison result."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Locate where a comparison run's failures originated."
    )
    parser.add_argument("result", help="a JSON file written by --out")
    parser.add_argument("--out", default=None, help="write the diagnosis here")
    arguments = parser.parse_args(argv)

    with open(arguments.result, encoding="utf-8") as handle:
        data = json.load(handle)
    diagnosis = diagnose(data["records"])
    print(format_diagnosis(diagnosis))
    if arguments.out:
        with open(arguments.out, "w", encoding="utf-8") as handle:
            json.dump(diagnosis, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"\nwritten to {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
