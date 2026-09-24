"""Stage 75 arena: the eight multi-body cases, N live calls each.

**The prompt is not touched and no variant is run.** The only independent
variable is which case is sent. Every expectation comes from
`ground_truth75`, fetched by case NAME, and every verdict comes from
`evaluate75`. This module holds no expectation of its own -- if it did, it
would be a second source of truth and the instrument would be worthless.

Two kinds of case, driven differently on purpose:

* a **creation** case is one request, sent cold. Nothing precedes it.
* a **refusal** case is a request against a part that already exists, so it
  is sent as a REVISION of :data:`ground_truth75.REFUSAL_FIXTURE` -- a
  deterministic two-body plan, built here, never model-generated. If the
  setup came from a model, a setup failure would be recorded as a refusal
  failure and the run would be measuring two things at once.

Like every arena before it, `--live` is required. A credential's presence
never starts a run.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cad_experimental import plan as plan_module              # noqa: E402
from cad_experimental import prompt as prompt_module          # noqa: E402
from cad_experimental import schema_ladder as ladder          # noqa: E402
from cad_experimental.cad_backend import resolve_backend      # noqa: E402
from cad_experimental.config import (                         # noqa: E402
    DEFAULT_MODEL, PROVIDER_NAME, bridge_credential, credential_variable,
)
from cad_experimental.executor import execute_plan            # noqa: E402
from cad_experimental.generation import (                     # noqa: E402
    PLAN_SCHEMA_FINGERPRINT, PLAN_SCHEMA_INLINED, PLAN_SCHEMA_NAME,
    OperationPlanService,
)
from cad_experimental.parser import PlanParseError, parse_plan_text  # noqa: E402
from cad_experimental.session import (                        # noqa: E402
    CadSession, Revision, revision_context,
)
from cad_experimental.validation import validate_plan         # noqa: E402

import evaluate75 as EV                                        # noqa: E402
import ground_truth75 as G                                     # noqa: E402

#: Every earlier baseline. This stage writes into its own directory only.
PROTECTED = tuple(
    p.name for p in HERE.parent.iterdir()
    if p.is_dir() and p.name != HERE.name
)


# ------------------------------------------------------- the identity guard


def identity() -> dict:
    """The three things a recorded number is only meaningful against."""
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


#: What the instrument was built and committed against. A run whose identity
#: differs is not comparable to the baseline and the arena refuses it, rather
#: than recording a number that looks like the others.
COMMITTED = {
    "model": "claude-haiku-4-5-20251001",
    "prompt_version": "2026-09-24.2",
    "prompt_fingerprint":
        "90ebab2c38d615fb04b31e6494e4826b817d308fb70a66a39f727df966483948",
    "schema_name": "strict_selector_union_part",
    "schema_inlined": 3874,
    "schema_fingerprint":
        "ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f",
}


def check_identity() -> list:
    """Every way the live route differs from what the corpus was built for."""
    now = identity()
    return [f"{key}: committed {want!r}, now {now.get(key)!r}"
            for key, want in COMMITTED.items() if now.get(key) != want]


# ------------------------------------------------------ the refusal fixture


def build_fixture(backend):
    """Build the deterministic two-body part the refusal cases modify.

    Deterministic by construction: the plan is a literal in the ground truth
    and no model is involved. Raises rather than degrading -- a refusal case
    measured against a part that did not build would measure nothing.
    """
    parsed = parse_plan_text(json.dumps(G.REFUSAL_FIXTURE))
    verdict = validate_plan(parsed)
    if not verdict.valid:
        raise SystemExit(
            "the refusal fixture does not validate: "
            + ", ".join(sorted({p.code for p in verdict.problems}))
        )
    execution = execute_plan(parsed, backend=backend)
    if not execution.succeeded:
        raise SystemExit(
            "the refusal fixture does not build: "
            + (execution.failure.message if execution.failure else "?")
        )
    standing = tuple(body.id for body in execution.bodies)
    if standing != G.FIXTURE_BODIES:
        raise SystemExit(
            f"the fixture built {standing}, not {G.FIXTURE_BODIES}"
        )
    session = CadSession(session_id="stage75-refusal-fixture")
    session.commit(Revision(
        plan=G.REFUSAL_FIXTURE,
        summary="two independent bodies",
        request="(deterministic fixture -- not model generated)",
        bodies={
            body.id: {
                "volume": body.measurement.volume,
                "faces": body.measurement.face_count,
            }
            for body in execution.bodies
        },
        backend=backend.name,
    ))
    return session, execution


# ------------------------------------------------------------------ the run


def one_call(planner, backend, case, session):
    """One live call for one case, observed and graded. Never raises."""
    row = {"case": case.name, "group": case.group}
    context = (
        revision_context(session, case.text) if case.group == G.REFUSAL
        else None
    )
    started = time.time()
    result = planner.generate(case.text, context=context)
    row["seconds"] = round(time.time() - started, 2)

    meta = result.metadata.to_dict() if hasattr(result.metadata, "to_dict") else {}
    row["structured_output"] = meta.get("structured_output")
    row["stop_reason"] = meta.get("stop_reason")
    row["usage"] = meta.get("usage")
    row["fenced"] = bool(
        result.raw_text and result.raw_text.lstrip().startswith("```"))

    execution = None
    if result.plan is not None and getattr(result.plan_validation, "valid", False):
        try:
            execution = execute_plan(result.plan, backend=backend)
        except Exception as exc:                       # noqa: BLE001
            # An executor that threw would make the arena's error handling
            # depend on which layer failed. Recorded, never raised.
            row["executor_exception"] = repr(exc)
            execution = None

    observation = EV.observe(
        case_name=case.name,
        generation=result,
        execution=execution,
        schema_fingerprint=PLAN_SCHEMA_FINGERPRINT,
        schema_name=PLAN_SCHEMA_NAME,
        prompt_version=prompt_module.PROMPT_VERSION,
        prompt_fingerprint=prompt_module.prompt_fingerprint(),
        raw_text=result.raw_text,
    )
    graded = EV.grade(observation)
    row["observation"] = observation
    row["checks"] = graded["checks"]
    row["strict_success"] = graded["strict_success"]
    # Reported beside the verdict, never folded into it. Present only for a
    # refusal case, which is why `summarise` keys on its presence.
    if "metrics" in graded:
        row["metrics"] = graded["metrics"]
    row["codes"] = list(EV.classify(observation, graded))
    row["label"] = EV.outcome_label(observation)
    return row


def run(cases, calls: int) -> dict:
    drift = check_identity()
    if drift:
        raise SystemExit(
            "the live route is not what this corpus was built against, so a "
            "number recorded now is not comparable:\n  " + "\n  ".join(drift)
        )

    from cad_ai.anthropic_provider import AnthropicTextToCadModel
    from cad_ai.config import AiConfig
    from cad_experimental.config import config_from_environment

    settings = config_from_environment()
    model = AnthropicTextToCadModel.from_environment(AiConfig(
        model=settings.model, provider=settings.provider,
        timeout_seconds=settings.timeout_seconds))
    planner = OperationPlanService(model, settings)
    backend = resolve_backend()

    record = {
        "stage": 75,
        "is_live_model_result": True,
        "credential_from": credential_variable(),
        "backend": backend.name,
        "backend_version": str(backend.version()),
        "calls_per_case": calls,
        "cases": [c.name for c in cases],
        "corpus": {
            c.name: {
                "group": c.group, "text": c.text,
                "expected": {
                    k: (list(v) if isinstance(v, tuple) else v)
                    for k, v in G.expected(c.name).items()
                },
            }
            for c in cases
        },
        "attempts": [],
    }
    record.update(identity())

    session = None
    if any(c.group == G.REFUSAL for c in cases):
        session, fixture = build_fixture(backend)
        record["refusal_fixture"] = {
            "plan": G.REFUSAL_FIXTURE,
            "bodies": [
                {"id": b.id, "volume": b.measurement.volume,
                 "faces": b.measurement.face_count}
                for b in fixture.bodies
            ],
            "is_deterministic": True,
            "is_live_model_result": False,
        }

    for case in cases:
        for i in range(1, calls + 1):
            try:
                row = one_call(planner, backend, case, session)
            except PlanParseError as exc:
                row = {"case": case.name, "group": case.group,
                       "parse_error": str(exc), "strict_success": False,
                       "codes": [G.J_INVALID_PLAN], "label": G.PROVIDER_ERROR}
            row["attempt"] = i
            record["attempts"].append(row)
            observation = row.get("observation") or {}
            print(
                f"  {case.name} #{i}: {str(observation.get('outcome_declared')):20s} "
                f"bodies={observation.get('body_count')} "
                f"declared={observation.get('declared_bodies')} "
                f"{'STRICT' if row['strict_success'] else 'fail'} "
                f"{row['codes']} {row.get('seconds')}s",
                flush=True,
            )

    record["summary"] = EV.summarise(record["attempts"])
    record["summary"]["labels"] = dict(
        Counter(r.get("label") for r in record["attempts"]))
    record["summary"]["structured_output"] = sum(
        1 for r in record["attempts"] if r.get("structured_output"))
    record["summary"]["fenced"] = sum(
        1 for r in record["attempts"] if r.get("fenced"))
    return record


# ------------------------------------------------------------------- the CLI


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calls", type=int, default=8,
                    help="live calls per case")
    ap.add_argument("--case", action="append", default=None,
                    choices=sorted(G.CASES_BY_NAME),
                    help="one case; repeatable. Default: every ACTIVE case")
    ap.add_argument("--group", default=None, choices=[G.CREATION, G.REFUSAL])
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true",
                    help="offline: print the identity and the corpus, call "
                         "nothing")
    ap.add_argument("--live", action="store_true",
                    help="required; a credential's presence never starts a run")
    args = ap.parse_args(argv)

    # ACTIVE only. A retired case is never run again: `expected()` refuses
    # it, and quoting a number the corpus has disowned is the thing the
    # retirement exists to prevent. Naming one explicitly is an error rather
    # than a silent skip.
    if args.case:
        retired = [n for n in args.case if n in G.RETIRED]
        if retired:
            print("refusing to run retired cases: " + ", ".join(retired))
            for name in retired:
                print(f"  {name}: {G.CASES_BY_NAME[name].retired}")
            return 2
        chosen = [G.CASES_BY_NAME[n] for n in args.case]
    else:
        chosen = [G.CASES_BY_NAME[n] for n in G.ACTIVE]
    if args.group:
        chosen = [c for c in chosen if c.group == args.group]

    if args.check:
        now = identity()
        for key in sorted(now):
            print(f"  {key:22s} {now[key]}")
        drift = check_identity()
        print("\nidentity: " + ("MATCHES the committed instrument" if not drift
                                else "DRIFTED\n  " + "\n  ".join(drift)))
        print(f"\ncases ({len(chosen)}):")
        for case in chosen:
            print(f"  {case.name}  {case.group:8s}  {case.text}")
        print("\nnothing was called.")
        return 0 if not drift else 1

    if not args.live:
        print("refusing to run without --live")
        return 2
    came_from = bridge_credential()
    if came_from is None:
        print("no credential in this process; nothing attempted")
        return 2
    print(f"credential from {came_from} (name only)")
    print(f"prompt {prompt_module.PROMPT_VERSION} "
          f"{prompt_module.prompt_fingerprint()[:16]} (UNCHANGED)")
    print(f"schema {PLAN_SCHEMA_NAME} {PLAN_SCHEMA_INLINED} "
          f"{PLAN_SCHEMA_FINGERPRINT[:16]}")

    record = run(chosen, args.calls)
    summary = record["summary"]
    print("\nper case:")
    for name in sorted(summary["per_case"]):
        entry = summary["per_case"][name]
        print(f"  {name}  {entry['strict']}/{entry['calls']}  "
              f"({entry['rate']:.2f})  {entry['codes']}")
    print("\nper group (NEVER pooled):")
    for group in sorted(summary["per_group"]):
        bucket = summary["per_group"][group]
        print(f"  {group:9s} {bucket['strict']}/{bucket['calls']} "
              f"({bucket['rate']:.2f})")
    print(f"\nlabels: {summary['labels']}")
    print(f"structured_output {summary['structured_output']}/"
          f"{len(record['attempts'])}  fenced {summary['fenced']}")

    out = pathlib.Path(args.out or (HERE / "baseline.json"))
    if any(part in PROTECTED for part in out.parts):
        raise SystemExit(f"refusing to write into an earlier baseline: {out}")
    out.write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
