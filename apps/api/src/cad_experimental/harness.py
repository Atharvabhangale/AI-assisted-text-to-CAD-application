"""The five-case measurement harness. Measures; never repairs.

The question this stage exists to answer is comparative and empirical:

    Is an operation plan easier for a model to produce correctly than the
    full V1 CAD document?

So every case records the model's actual output, whether it parsed, whether
it validated, whether it built, whether the geometry is what was asked for,
and whether a RenderModel came out. Nothing is retried, nothing is repaired,
and a provider failure is recorded as a provider failure -- never as a wrong
answer.

Run it deliberately, like the stable benchmark:

    python -m cad_experimental.harness --live      # spends money
    python -m cad_experimental.harness --self-check  # stub; proves plumbing
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from cad_core.application_service import CadApplicationService

from . import EXPERIMENT_NAME, PLAN_SCHEMA_VERSION
from .build import build_plan
from .config import (
    API_KEY_VARIABLE,
    ExperimentalConfig,
    config_from_environment,
    credential_available,
)
from .generation import OperationPlanService, PlanOutcome
from .plan import BOX, CYLINDER, PlanStatus
from .prompt import PROMPT_VERSION, prompt_fingerprint

#: Volumes are compared with a relative tolerance, never for float equality:
#: a kernel result is not an exact decimal.
VOLUME_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Expectation:
    """What a correct answer looks like, written before the model was asked."""

    outcome: PlanOutcome
    operation_type: Optional[str] = None
    #: Expected extents, for a box.
    size: Optional[Tuple[float, float, float]] = None
    #: Expected diameter/height, for a cylinder.
    cylinder: Optional[Tuple[float, float]] = None
    axis: Optional[str] = None
    #: The volume the built solid should have, in mm^3.
    volume_mm3: Optional[float] = None


@dataclass(frozen=True)
class Case:
    """One prompt and its independently authored expectation."""

    id: str
    text: str
    expectation: Expectation
    note: str = ""


#: The five cases this stage was asked to measure, verbatim.
CASES: Tuple[Case, ...] = (
    Case(
        id="1-box-plate",
        text=(
            "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm "
            "thick."
        ),
        expectation=Expectation(
            outcome=PlanOutcome.GENERATED,
            operation_type=BOX,
            size=(100.0, 60.0, 10.0),
            volume_mm3=100.0 * 60.0 * 10.0,
        ),
        note="one box operation",
    ),
    Case(
        id="2-cylinder-axis",
        text=(
            "Create a cylinder 20 mm in diameter and 50 mm tall along the +Z "
            "axis."
        ),
        expectation=Expectation(
            outcome=PlanOutcome.GENERATED,
            operation_type=CYLINDER,
            cylinder=(20.0, 50.0),
            axis="+Z",
            volume_mm3=math.pi * 10.0**2 * 50.0,
        ),
        note="one cylinder operation",
    ),
    Case(
        id="3-box-terse",
        text="Create a 100 mm by 60 mm by 10 mm box.",
        expectation=Expectation(
            outcome=PlanOutcome.GENERATED,
            operation_type=BOX,
            size=(100.0, 60.0, 10.0),
            volume_mm3=100.0 * 60.0 * 10.0,
        ),
        note="one box operation",
    ),
    Case(
        id="4-sphere-unsupported",
        text="Create a sphere with a 20 mm diameter.",
        expectation=Expectation(outcome=PlanOutcome.UNSUPPORTED),
        note="a sphere is not in the vocabulary",
    ),
    Case(
        id="5-join-unsupported",
        text="Create a cylinder and a box joined together.",
        expectation=Expectation(outcome=PlanOutcome.UNSUPPORTED),
        note="union/join is not implemented in this stage",
    ),
)


@dataclass
class CaseResult:
    """Everything measured for one case."""

    case_id: str
    prompt: str
    expected_outcome: str
    actual_outcome: Optional[str] = None
    answered: bool = False
    parsed: bool = False
    plan_valid: bool = False
    outcome_match: bool = False
    semantically_correct: bool = False
    built: Optional[bool] = None
    render_triangles: Optional[int] = None
    volume_mm3: Optional[float] = None
    volume_matches: Optional[bool] = None
    bounding_box: Optional[Dict[str, float]] = None
    operations: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: Optional[str] = None
    provider_error_kind: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    model_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "prompt": self.prompt,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "answered": self.answered,
            "parsed": self.parsed,
            "plan_valid": self.plan_valid,
            "outcome_match": self.outcome_match,
            "semantically_correct": self.semantically_correct,
            "built": self.built,
            "render_triangles": self.render_triangles,
            "volume_mm3": self.volume_mm3,
            "volume_matches": self.volume_matches,
            "bounding_box": self.bounding_box,
            "operations": self.operations,
            "raw_text": self.raw_text,
            "provider_error_kind": self.provider_error_kind,
            "notes": self.notes,
            "model_seconds": self.model_seconds,
        }


def _semantics(case: Case, plan: Any) -> Tuple[bool, List[str]]:
    """Is the plan actually what was asked for? Not merely valid."""
    expectation = case.expectation
    notes: List[str] = []

    if expectation.outcome is not PlanOutcome.GENERATED:
        # A refusal is semantically correct when it refuses and offers no
        # geometry. Refusing for a stated reason is checked by the validator.
        return True, notes

    if len(plan.operations) != 1:
        notes.append(f"expected 1 operation, got {len(plan.operations)}")
        return False, notes

    operation = plan.operations[0]
    kind = getattr(operation, "TYPE", None)
    if kind != expectation.operation_type:
        notes.append(
            f"expected a {expectation.operation_type}, got a {kind}"
        )
        return False, notes

    correct = True
    if expectation.size is not None:
        actual = (operation.x, operation.y, operation.z)
        if actual != expectation.size:
            notes.append(f"expected size {expectation.size}, got {actual}")
            correct = False
    if expectation.cylinder is not None:
        actual_cylinder = (operation.diameter, operation.height)
        if actual_cylinder != expectation.cylinder:
            notes.append(
                f"expected (diameter, height) {expectation.cylinder}, "
                f"got {actual_cylinder}"
            )
            correct = False
    if expectation.axis is not None:
        # An omitted axis is correct when the expected axis is the default.
        actual_axis = operation.axis if operation.axis is not None else "+Z"
        if actual_axis != expectation.axis:
            notes.append(
                f"expected axis {expectation.axis}, got {actual_axis}"
            )
            correct = False
    return correct, notes


def run_case(
    case: Case,
    planner: OperationPlanService,
    service: Optional[CadApplicationService],
) -> CaseResult:
    """One prompt, one model call, no retry."""
    result = CaseResult(
        case_id=case.id,
        prompt=case.text,
        expected_outcome=case.expectation.outcome.value,
    )

    started = time.perf_counter()
    generation = planner.generate(case.text)
    result.model_seconds = round(time.perf_counter() - started, 3)

    result.actual_outcome = generation.outcome.value
    result.answered = generation.answered
    result.raw_text = generation.raw_text
    if generation.error_kind is not None:
        result.provider_error_kind = generation.error_kind.value

    if not generation.answered:
        result.notes.append(
            "no model response; this case measured nothing about the model"
        )
        return result

    result.parsed = generation.plan is not None
    result.plan_valid = bool(
        generation.plan_validation is not None
        and generation.plan_validation.valid
    )
    result.outcome_match = generation.outcome is case.expectation.outcome

    if generation.plan is None:
        result.notes.append(generation.error or "the output was not a plan")
        return result

    result.operations = generation.plan.to_dict()["operations"]
    correct, notes = _semantics(case, generation.plan)
    result.semantically_correct = correct and result.outcome_match
    result.notes.extend(notes)

    if generation.plan.status is not PlanStatus.GENERATED:
        return result
    if service is None:
        result.notes.append("no build service; geometry was not built")
        return result

    build = build_plan(service, generation.plan, name=case.id)
    result.built = build.built
    if build.outcome is None:
        result.notes.append(build.error or "the plan could not be built")
        return result
    if not build.built:
        error = build.outcome.error
        result.notes.append(
            error.message if error is not None else "the build failed"
        )
        return result

    geometry = build.outcome.artifact("geometry")
    if geometry is not None:
        details = geometry.details
        result.volume_mm3 = details.get("volume_mm3")
        result.bounding_box = (details.get("bounding_box") or {}).get("size")
        expected_volume = case.expectation.volume_mm3
        if expected_volume is not None and result.volume_mm3 is not None:
            result.volume_matches = math.isclose(
                result.volume_mm3, expected_volume, rel_tol=VOLUME_TOLERANCE
            )
            if not result.volume_matches:
                result.notes.append(
                    f"expected volume {expected_volume}, "
                    f"got {result.volume_mm3}"
                )
    render = build.outcome.render_model
    if render is not None:
        result.render_triangles = render.triangle_count()
    else:
        result.notes.append("no render model was produced")
    return result


def run(
    planner: OperationPlanService,
    service: Optional[CadApplicationService],
    *,
    cases: Tuple[Case, ...] = CASES,
    live: bool = False,
) -> Dict[str, Any]:
    """Run every case and summarise. Reliability and quality stay separate."""
    results = [run_case(case, planner, service) for case in cases]
    answered = [r for r in results if r.answered]
    return {
        "experiment": EXPERIMENT_NAME,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_fingerprint": prompt_fingerprint(),
        "live": live,
        "provider": planner.config.provider,
        "model": planner.config.model,
        "totals": {
            "cases": len(results),
            # Coverage is a provider fact. It is never merged into quality.
            "answered": len(answered),
            "unanswered": len(results) - len(answered),
            "outcome_match": sum(1 for r in answered if r.outcome_match),
            "semantically_correct": sum(
                1 for r in answered if r.semantically_correct
            ),
            "built": sum(1 for r in answered if r.built),
            "parse_failures": sum(1 for r in answered if not r.parsed),
        },
        "results": [r.to_dict() for r in results],
    }


def format_report(run_data: Dict[str, Any]) -> str:
    """A readable report. Every dimension shown separately."""
    lines: List[str] = []
    lines.append("=" * 74)
    lines.append("EXPERIMENTAL OPERATION-PLAN HARNESS")
    lines.append("=" * 74)
    lines.append(f"experiment    {run_data['experiment']}")
    lines.append(f"plan schema   {run_data['plan_schema_version']}")
    lines.append(
        f"prompt        {run_data['prompt_version']}  "
        f"{run_data['prompt_fingerprint'][:16]}..."
    )
    lines.append(
        f"provider      {run_data['provider']} / {run_data['model']}"
    )
    lines.append(f"live          {run_data['live']}")
    if not run_data["live"]:
        lines.append(
            "NOTE: not a live run. Says nothing about real model quality."
        )
    lines.append("")

    for entry in run_data["results"]:
        lines.append("-" * 74)
        lines.append(f"{entry['case_id']}")
        lines.append(f"  prompt     {entry['prompt']}")
        lines.append(
            f"  expected   {entry['expected_outcome']}"
            f"   actual  {entry['actual_outcome']}"
        )
        if not entry["answered"]:
            lines.append(
                f"  NO DATA    provider error "
                f"({entry['provider_error_kind']})"
            )
        else:
            lines.append(
                f"  parsed {entry['parsed']}  plan_valid "
                f"{entry['plan_valid']}  outcome_match "
                f"{entry['outcome_match']}  semantic "
                f"{entry['semantically_correct']}"
            )
            if entry["operations"]:
                lines.append(
                    f"  plan       {json.dumps(entry['operations'])}"
                )
            if entry["built"] is not None:
                lines.append(
                    f"  built {entry['built']}  volume "
                    f"{entry['volume_mm3']}  volume_ok "
                    f"{entry['volume_matches']}  triangles "
                    f"{entry['render_triangles']}"
                )
                lines.append(f"  bbox       {entry['bounding_box']}")
        for note in entry["notes"]:
            lines.append(f"  note       {note}")

    totals = run_data["totals"]
    lines.append("=" * 74)
    lines.append("TOTALS")
    lines.append(f"  cases                 {totals['cases']}")
    lines.append(f"  answered              {totals['answered']}")
    lines.append(f"  unanswered            {totals['unanswered']}")
    lines.append(f"  outcome match         {totals['outcome_match']}")
    lines.append(f"  semantically correct  {totals['semantically_correct']}")
    lines.append(f"  built                 {totals['built']}")
    lines.append(f"  parse failures        {totals['parse_failures']}")
    lines.append("=" * 74)
    return "\n".join(lines)


class _CorpusStub:
    """Answers every case from its own expectation. Proves the plumbing.

    It is not a model and says nothing about one. It exists so the harness
    itself can be exercised without spending a request.
    """

    name = "corpus-stub"

    def __init__(self) -> None:
        self._by_text = {case.text: case for case in CASES}

    def generate(self, request: Any) -> Any:
        from cad_ai.provider import ModelResponse

        case = self._by_text.get(request.user_text)
        if case is None:
            payload: Dict[str, Any] = {
                "status": "unsupported",
                "summary": "not in the stub corpus",
                "reason": "the stub only answers the five cases",
                "operations": [],
            }
        elif case.expectation.outcome is PlanOutcome.GENERATED:
            expectation = case.expectation
            if expectation.operation_type == BOX:
                assert expectation.size is not None
                parameters: Dict[str, Any] = {
                    "x": expectation.size[0],
                    "y": expectation.size[1],
                    "z": expectation.size[2],
                }
            else:
                assert expectation.cylinder is not None
                parameters = {
                    "diameter": expectation.cylinder[0],
                    "height": expectation.cylinder[1],
                }
                if expectation.axis is not None:
                    parameters["axis"] = expectation.axis
            payload = {
                "status": "generated",
                "summary": case.note,
                "operations": [
                    {
                        "id": "body",
                        "type": expectation.operation_type,
                        "parameters": parameters,
                    }
                ],
            }
        else:
            payload = {
                "status": "unsupported",
                "summary": case.note,
                "reason": case.note,
                "operations": [],
            }
        return ModelResponse(
            text=json.dumps(payload),
            provider="corpus-stub",
            model="corpus-stub",
            structured_output=request.output_schema is not None,
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cad_experimental.harness",
        description=(
            "Measure the operation-plan representation against five cases. "
            "Measurement only: no repair, no retry."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "actually call the provider. REQUIRED for a real run: a "
            "credential being present is deliberately not enough."
        ),
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="run against a stub. Proves the harness, not the model.",
    )
    parser.add_argument(
        "--no-build", action="store_true", help="skip the CAD build"
    )
    parser.add_argument("--out", default=None, help="write the run as JSON")
    arguments = parser.parse_args(argv)

    settings = config_from_environment()
    service = (
        None
        if arguments.no_build
        else CadApplicationService.local(tempfile.mkdtemp())
    )

    if arguments.self_check:
        planner = OperationPlanService(
            _CorpusStub(), ExperimentalConfig(model="corpus-stub")
        )
        run_data = run(planner, service, live=False)
    elif not arguments.live:
        print("LIVE RUN: NOT_RUN -- pass --live to call the provider.")
        print(
            "A benchmark spends money, so a present credential is "
            "deliberately not sufficient."
        )
        return 0
    else:
        if not credential_available():
            print("LIVE RUN: NOT_RUN")
            print(f"{API_KEY_VARIABLE} is not set. Nothing was attempted.")
            print(
                "No other credential is substituted, and no other provider "
                "is used."
            )
            return 1
        from cad_ai.anthropic_provider import AnthropicTextToCadModel
        from cad_ai.config import AiConfig

        model = AnthropicTextToCadModel.from_environment(
            AiConfig(
                model=settings.model,
                provider=settings.provider,
                timeout_seconds=settings.timeout_seconds,
            )
        )
        planner = OperationPlanService(model, settings)
        run_data = run(planner, service, live=True)

    print(format_report(run_data))
    if arguments.out:
        with open(arguments.out, "w", encoding="utf-8") as handle:
            json.dump(run_data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"\nwritten to {arguments.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
