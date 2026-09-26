"""Stage 77 arena: the broader multi-body corpus, N live calls per case.

**The prompt is not touched and no variant is run.** The only independent
variable is which case is sent. Every expectation comes from
`ground_truth77`, fetched by case NAME and turn INDEX, and every verdict
comes from `evaluate77`. This module holds no expectation of its own.

Three kinds of case, driven differently on purpose:

* a **creation** case is one request, sent COLD. Nothing precedes it, and
  its post-conditions -- the measurement probes and the STEP export -- are
  run on the part it produced, which costs no further live call.
* an **edit** case starts from a DETERMINISTIC fixture and sends its
  requests in order, committing the model's own plan between turns so the
  next turn sees what the last one did.
* a **refusal** case starts from the same kind of fixture and sends one
  request that must be declined.

If the setup came from a model, a setup failure would be recorded as an
edit or refusal failure and the run would measure two things at once.

`--live` is required. A credential's presence never starts a run.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "stage76-observation"))

from cad_experimental import prompt as prompt_module            # noqa: E402
from cad_experimental.cad_backend import resolve_backend        # noqa: E402
from cad_experimental.config import (                           # noqa: E402
    DEFAULT_MODEL, PROVIDER_NAME, bridge_credential,
    config_from_environment, credential_variable,
)
from cad_experimental.executor import execute_plan              # noqa: E402
from cad_experimental.generation import (                       # noqa: E402
    PLAN_SCHEMA_FINGERPRINT, PLAN_SCHEMA_INLINED, PLAN_SCHEMA_NAME,
    OperationPlanService,
)
from cad_experimental.parser import parse_plan                  # noqa: E402
from cad_experimental.session import (                          # noqa: E402
    CadSession, Revision, revision_context,
)
from cad_experimental.validation import validate_plan           # noqa: E402

import evaluate77 as EV                                          # noqa: E402
import fixtures77 as F                                           # noqa: E402
import ground_truth77 as G                                       # noqa: E402
import observe76 as O76                                          # noqa: E402

#: Every earlier baseline directory. This stage writes into its own only.
PROTECTED = tuple(
    p.name for p in HERE.parent.iterdir()
    if p.is_dir() and p.name != HERE.name
)


# ------------------------------------------------------- the identity guard


def identity() -> Dict[str, Any]:
    """The five things a recorded number is only meaningful against."""
    return {
        "model": DEFAULT_MODEL,
        "provider": PROVIDER_NAME,
        "prompt_version": prompt_module.PROMPT_VERSION,
        "prompt_fingerprint": prompt_module.prompt_fingerprint(),
        "prompt_characters": len(prompt_module.system_prompt()),
        "schema_name": PLAN_SCHEMA_NAME,
        "schema_inlined": PLAN_SCHEMA_INLINED,
        "schema_fingerprint": PLAN_SCHEMA_FINGERPRINT,
    }


def check_identity() -> List[str]:
    """Every way the live route differs from what this corpus was built for.

    Read from `ground_truth77`, which records them as constants, and
    compared against the live route. A number measured under a different
    prompt or a different grammar is a different number, so a run that
    drifts is refused rather than recorded beside ones that did not.
    """
    now = identity()
    want = {
        "model": G.MODEL,
        "prompt_version": G.PROMPT_VERSION,
        "prompt_fingerprint": G.PROMPT_FINGERPRINT,
        "prompt_characters": G.PROMPT_CHARACTERS,
        "schema_name": G.SCHEMA_NAME,
        "schema_inlined": G.SCHEMA_INLINED,
        "schema_fingerprint": G.SCHEMA_FINGERPRINT,
    }
    return [f"{key}: corpus {value!r}, live {now.get(key)!r}"
            for key, value in want.items() if now.get(key) != value]


# ---------------------------------------------------------- the fixtures


def build_fixture(name: str, backend) -> Dict[str, Any]:
    """Build one deterministic fixture and ASSERT it against its truth.

    Raises rather than degrading. An edit or refusal case measured against
    a part that did not build, or built wrong, measures nothing -- and
    Stage 75 Phase A recorded exactly that as a model failure.
    """
    plan = F.plan_for(name)
    parsed = parse_plan(plan)
    verdict = validate_plan(parsed)
    if not verdict.valid:
        raise SystemExit(
            f"fixture {name} does not validate: "
            + ", ".join(sorted({p.code for p in verdict.problems})))
    execution = execute_plan(parsed, part_name="stage77", backend=backend)
    if not execution.succeeded:
        raise SystemExit(
            f"fixture {name} does not build: "
            + (execution.failure.message if execution.failure else "?"))
    want = G.FIXTURE_BODIES[name]
    got = {b.id: b.measurement.volume for b in execution.bodies}
    if set(got) != set(want):
        raise SystemExit(f"fixture {name} built {sorted(got)}, "
                         f"not {sorted(want)}")
    for body, volume in want.items():
        if abs(got[body] - volume) / volume > G.VOLUME_TOLERANCE:
            raise SystemExit(
                f"fixture {name} body {body} measured {got[body]!r}, "
                f"expected {volume!r}")
    return {"plan": plan, "bodies": got, "backend": backend.name}


def _session(name: str, fixture: Dict[str, Any], attempt: int) -> CadSession:
    """A FRESH session on the fixture, per attempt.

    Fresh because a session accumulates a transcript and a committed plan,
    and an attempt that inherited the last one's would be a different
    experiment with the same name.
    """
    session = CadSession(session_id=f"stage77-{name}-{attempt}")
    session.commit(Revision(
        plan=F.plan_for(name),
        summary=F.SUMMARIES[name],
        request="(deterministic fixture -- not model generated)",
        bodies={body: {"volume": volume}
                for body, volume in fixture["bodies"].items()},
        backend=fixture["backend"],
    ))
    return session


# ------------------------------------------------------------- one turn


def one_turn(planner, backend, case_name: str, index: int,
             session: Optional[CadSession]) -> Dict[str, Any]:
    """One live call, observed and graded. Never raises."""
    case = G.expected(case_name)
    request = G.requests_to_send(case_name)[index]
    context = (revision_context(session, request)
               if session is not None else None)

    started = time.time()
    result = planner.generate(request, context=context)
    seconds = round(time.time() - started, 2)

    execution = None
    if result.plan is not None and getattr(result.plan_validation, "valid",
                                           False):
        try:
            execution = execute_plan(result.plan, part_name="stage77",
                                     backend=backend)
        except Exception as exc:                        # noqa: BLE001
            # An executor that threw would make the arena's error handling
            # depend on which layer failed. Recorded, never raised.
            execution = None
            seconds = seconds  # keep the timing; the exception is below

    observation = EV.observe(
        case_name=case_name, turn_index=index, group=case["group"],
        # The one label a live call may carry, and only because a live call
        # is what produced it.
        source=G.MODEL_GENERATED,
        generation=result, execution=execution, raw_text=result.raw_text)

    verdict = EV.grade_turn(observation)
    meta = (result.metadata.to_dict()
            if hasattr(result.metadata, "to_dict") else {})

    row: Dict[str, Any] = {
        "case": case_name, "turn": index, "request": request,
        "seconds": seconds,
        "stop_reason": meta.get("stop_reason"),
        "usage": meta.get("usage"),
        "observation": observation,
        "verdict": verdict,
        "label": EV.outcome_label(observation),
    }

    # --- the post-conditions, which cost NO live call ------------------
    measurement = aggregate = export = None
    if case["measure"]:
        rows = None
        if observation["execution_succeeded"] and observation["bodies"]:
            rows = O76.measure_probes(
                EV.probes_for(observation),
                result.plan.to_dict() if hasattr(result.plan, "to_dict")
                else {}, execution, backend_name=backend.name)
        measurement = EV.grade_measurement(observation, rows)
        aggregate = EV.grade_aggregate(observation, rows)
        row["measurement_rows"] = rows
    if case["export"]:
        export_row = None
        if observation["execution_succeeded"] and observation["bodies"]:
            with tempfile.TemporaryDirectory() as folder:
                export_row = O76.export(
                    case_name, execution, backend, pathlib.Path(folder),
                    filename=f"{case_name}-{index}.step")
        export = EV.grade_export(observation, export_row)
        row["export_row"] = export_row
    row["measurement"] = measurement
    row["aggregate"] = aggregate
    row["export"] = export
    row["codes"] = list(EV.classify(observation, verdict, measurement,
                                    aggregate, export))
    return row


def one_attempt(planner, backend, case_name: str, attempt: int,
                fixtures: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """One attempt at one case: its whole turn chain."""
    case = G.expected(case_name)
    session = None
    if case["fixture"]:
        session = _session(case["fixture"], fixtures[case["fixture"]],
                           attempt)

    turns: List[Dict[str, Any]] = []
    for index in range(case["calls"]):
        row = one_turn(planner, backend, case_name, index, session)
        turns.append(row)
        if session is not None:
            observation = row["observation"]
            if case["group"] == G.EDIT:
                if not observation["execution_succeeded"]:
                    # The chain cannot continue honestly: the next turn's
                    # context would describe a part that does not exist.
                    # The remaining turns are NOT RUN, and the attempt
                    # fails -- never silently shortened.
                    for remaining in range(index + 1, case["calls"]):
                        turns.append({
                            "case": case_name, "turn": remaining,
                            "not_run": "the previous turn did not build, so "
                                       "there is no part to revise",
                            "verdict": {"case": case_name, "turn": remaining,
                                        "group": case["group"],
                                        "family": case["family"],
                                        "dimensions": case["dimensions"],
                                        "checks": {}, "metrics": {},
                                        "strict_success": False},
                            "observation": {"is_live_model_result": False,
                                            "structured_output": False,
                                            "fenced": False},
                            "codes": [G.Q_OTHER],
                        })
                    break
                session.said("user", row["request"])
                session.commit(Revision(
                    plan=row["observation"].get("raw_plan")
                    or _plan_dict(row),
                    summary=observation.get("summary") or "revised",
                    request=row["request"],
                    bodies={b["id"]: {"volume": b["volume"]}
                            for b in observation["bodies"]},
                    backend=backend.name,
                ))
    return {
        "case": case_name, "attempt": attempt, "group": case["group"],
        "turns": turns,
        "strict_success": all(t["verdict"]["strict_success"] for t in turns),
    }


def _plan_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """The plan the model actually wrote, as plain data, for the next turn.

    Read from the RAW answer rather than reconstructed: the next turn's
    context must describe the part the model made, and rebuilding it here
    would be this module inventing a plan.
    """
    raw = row["observation"].get("raw_text")
    try:
        parsed = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("operations"), list):
        return parsed
    return {"status": "generated", "summary": "revised", "operations": []}


# ------------------------------------------------------------------ run


def run(cases: List[str], calls: int) -> Dict[str, Any]:
    drift = check_identity()
    if drift:
        raise SystemExit(
            "the live route is not what this corpus was built against, so a "
            "number recorded now is not comparable:\n  " + "\n  ".join(drift))

    from cad_ai.anthropic_provider import AnthropicTextToCadModel
    from cad_ai.config import AiConfig

    bridge_credential()
    settings = config_from_environment()
    model = AnthropicTextToCadModel.from_environment(AiConfig(
        model=settings.model, provider=settings.provider,
        timeout_seconds=settings.timeout_seconds))
    planner = OperationPlanService(model, settings)
    backend = resolve_backend()

    needed = {G.expected(c)["fixture"] for c in cases}
    fixtures = {name: build_fixture(name, backend)
                for name in sorted(n for n in needed if n)}

    record: Dict[str, Any] = {
        "stage": 77,
        "is_live_model_result": True,
        "credential_from": credential_variable(),
        "backend": backend.name,
        "backend_version": str(backend.version()),
        "calls_per_case_attempt": calls,
        "cases": list(cases),
        "fixtures": fixtures,
        "corpus": {
            name: {
                k: (list(v) if isinstance(v, tuple) else v)
                for k, v in G.expected(name).items()
            }
            for name in cases
        },
        "attempts": [],
    }
    record.update(identity())

    total = sum(G.expected(c)["calls"] for c in cases) * calls
    spent = 0
    for case_name in cases:
        for attempt in range(calls):
            row = one_attempt(planner, backend, case_name, attempt, fixtures)
            record["attempts"].append(row)
            spent += len([t for t in row["turns"] if "not_run" not in t])
            mark = "ok " if row["strict_success"] else "FAIL"
            print(f"  {mark} {case_name} #{attempt}  ({spent}/{total} calls)",
                  flush=True)
    record["summary"] = EV.summarise(record["attempts"])
    record["live_calls_spent"] = spent
    return record


def _write(record: Dict[str, Any], out: str) -> None:
    target = pathlib.Path(out)
    resolved = target.resolve()
    for name in PROTECTED:
        if name in resolved.parts:
            raise SystemExit(
                f"refusing to write inside {name}: an earlier baseline is "
                "immutable, and a stage that can overwrite one can rewrite "
                "what an old number meant")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, sort_keys=True))
    print(f"wrote {target}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="REQUIRED. Spends real provider calls.")
    parser.add_argument("--calls", type=int, default=8,
                        help="attempts per case (default 8)")
    parser.add_argument("--cases", help="comma-separated case names")
    parser.add_argument("--group", choices=list(G.GROUPS))
    parser.add_argument("--out")
    parser.add_argument("--check", action="store_true",
                        help="identity and corpus only; calls nothing")
    args = parser.parse_args(argv)

    cases = list(G.ACTIVE)
    if args.group:
        cases = [c for c in cases if G.expected(c)["group"] == args.group]
    if args.cases:
        wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in G.ACTIVE]
        if unknown:
            raise SystemExit(f"unknown case(s): {', '.join(unknown)}")
        cases = wanted

    if args.check:
        drift = check_identity()
        print("identity:", "MATCHES" if not drift else "DRIFTED")
        for line in drift:
            print("   ", line)
        print(f"cases: {len(cases)}")
        print(f"live calls for --calls {args.calls}: "
              f"{sum(G.expected(c)['calls'] for c in cases) * args.calls}")
        return 1 if drift else 0

    if not args.live:
        raise SystemExit(
            "refusing to run without --live. A credential's presence has "
            "started a paid run in this project once already.")
    record = run(cases, args.calls)
    summary = record["summary"]
    print(json.dumps({k: summary[k] for k in
                      ("creation", "edit_turns", "edit_chains", "refusal",
                       "measurement", "aggregate", "export",
                       "live_model_results")}, indent=1))
    if args.out:
        _write(record, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
