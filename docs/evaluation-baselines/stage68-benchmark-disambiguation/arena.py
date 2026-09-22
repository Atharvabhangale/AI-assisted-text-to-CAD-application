"""Stage 68 arena: one request, N live calls, graded against immutable truth.

The PROMPT IS NOT TOUCHED. Stage 68 changes no prompt and runs no variant;
the only independent variable is which of the two golden requests is sent.
Every expectation comes from `ground_truth`'s constants.
"""
from __future__ import annotations

import argparse, json, pathlib, sys, time
from collections import Counter

from cad_experimental import plan as plan_module
from cad_experimental import prompt as prompt_module
from cad_experimental import schema_ladder as ladder
from cad_experimental.config import (
    DEFAULT_MODEL, PROVIDER_NAME, bridge_credential, credential_variable,
)
from cad_experimental.generation import OperationPlanService
from cad_experimental.parser import PlanParseError, parse_plan_text
from cad_experimental.validation import validate_plan
from cad_experimental.cad_backend import resolve_backend
from cad_experimental.executor import execute_plan

import evaluate as EV
import ground_truth as G


def run(request_key: str, calls: int) -> dict:
    request = G.REQUESTS[request_key]
    schema = plan_module.strict_selector_union_provider_schema()
    metrics = ladder.grammar_metrics(schema)

    record = {
        "request_key": request_key,
        "request": request,
        "ground_truth": {k: (list(v) if isinstance(v, tuple) else v)
                         for k, v in G.expected(request_key).items()},
        "model": DEFAULT_MODEL,
        "provider": PROVIDER_NAME,
        "credential_from": credential_variable(),
        "prompt_version": prompt_module.PROMPT_VERSION,
        "prompt_fingerprint": prompt_module.prompt_fingerprint(),
        "prompt_characters": len(prompt_module.system_prompt()),
        "schema": "strict_selector_union",
        "schema_inlined": metrics["inlined_characters"],
        "schema_fingerprint": metrics["fingerprint"],
        "calls": calls,
        "is_live_model_result": True,
        "attempts": [],
    }

    from cad_ai.anthropic_provider import AnthropicTextToCadModel
    from cad_ai.config import AiConfig
    from cad_experimental.config import config_from_environment
    settings = config_from_environment()
    model = AnthropicTextToCadModel.from_environment(AiConfig(
        model=settings.model, provider=settings.provider,
        timeout_seconds=settings.timeout_seconds))
    planner = OperationPlanService(model, settings)
    backend = resolve_backend()
    record["backend"] = backend.name
    record["backend_version"] = str(backend.version())

    for i in range(1, calls + 1):
        row = {"attempt": i}
        started = time.time()
        result = planner.generate(request)
        row["seconds"] = round(time.time() - started, 2)
        meta = result.metadata.to_dict() if hasattr(result.metadata, "to_dict") else {}
        row["outcome"] = result.outcome.value
        row["structured_output"] = meta.get("structured_output")
        row["stop_reason"] = meta.get("stop_reason")
        row["usage"] = meta.get("usage")
        row["raw_text"] = result.raw_text
        row["provider_error"] = result.error
        row["fenced"] = bool(result.raw_text
                             and result.raw_text.lstrip().startswith("```"))

        stage, codes = "no_output", []
        if result.raw_text:
            try:
                parsed = parse_plan_text(result.raw_text)
                ops = list(parsed.operations)
                seen = EV.observe(ops)
                row["observed"] = seen
                row["operations"] = [
                    {"id": o.id, "type": getattr(o, "TYPE", "?"),
                     "target": getattr(o, "target", None),
                     "tools": list(getattr(o, "tools", []) or []) or None}
                    for o in ops]
                verdict_result = validate_plan(parsed)
                codes = sorted({p.code for p in verdict_result.problems})
                row["problems"] = [{"code": p.code, "message": p.message}
                                   for p in verdict_result.problems]
                row["plan_valid"] = verdict_result.valid

                measurement, failure_message = None, None
                if verdict_result.valid:
                    execution = execute_plan(parsed, backend=backend)
                    row["built"] = execution.succeeded
                    if execution.failure:
                        failure_message = execution.failure.message
                        row["build_failure"] = {"code": execution.failure.code,
                                                "message": failure_message}
                    if execution.succeeded:
                        m = execution.bodies[0].measurement
                        measurement = m
                        row["measurement"] = {
                            "volume": m.volume, "solids": m.solid_count,
                            "faces": m.face_count, "edges": m.edge_count,
                            "minimum": list(m.minimum), "maximum": list(m.maximum)}
                else:
                    row["built"] = False

                verdict = EV.grade(seen, measurement, bool(row.get("built")),
                                   failure_message, verdict_result.valid)
                row["verdict"] = verdict
                row["STRICT_SUCCESS"] = verdict["STRICT_SUCCESS"]
                row["failure_codes"] = EV.classify(seen, verdict, failure_message)
                stage = ("STRICT_SUCCESS" if verdict["STRICT_SUCCESS"]
                         else ("built_but_wrong" if row.get("built")
                               else ("build_failed" if verdict_result.valid
                                     else "invalid:" + ",".join(codes))))
            except PlanParseError as exc:
                stage = "parse_failed"
                row["parse_error"] = str(exc)
                row["STRICT_SUCCESS"] = False
                row["failure_codes"] = [G.I_OTHER]
        row["stage"] = stage
        row["codes"] = codes
        record["attempts"].append(row)
        obs = row.get("observed") or {}
        print(f"  {request_key} #{i}: {row['outcome']:20s} {stage:22s} "
              f"t={obs.get('observed_thickness')} plates={obs.get('plate_count')} "
              f"bores={obs.get('bore_count')} codes={row.get('failure_codes')} "
              f"{row['seconds']}s", flush=True)

    a = record["attempts"]
    def n(key): return sum(1 for r in a if (r.get("verdict") or {}).get("checks", {}).get(key))
    record["summary"] = {
        "calls": len(a),
        "structured_output": sum(1 for r in a if r.get("structured_output")),
        "fenced": sum(1 for r in a if r.get("fenced")),
        "validated": sum(1 for r in a if r.get("plan_valid")),
        "built": sum(1 for r in a if r.get("built")),
        "thickness_correct": n("thickness"),
        "envelope_correct": n("envelope"),
        "plate_count_correct": n("plate_count"),
        "bore_count_correct": n("bore_count"),
        "bore_axes_correct": n("bore_axes"),
        "topology_correct": sum(1 for r in a
                                if (r.get("verdict") or {}).get("checks", {}).get("faces")
                                and (r.get("verdict") or {}).get("checks", {}).get("edges")),
        "volume_correct": n("volume"),
        "STRICT_SUCCESS": sum(1 for r in a if r.get("STRICT_SUCCESS")),
        "failure_codes": dict(Counter(c for r in a for c in (r.get("failure_codes") or []))),
        "stages": dict(Counter(r["stage"] for r in a)),
        "p_codes": dict(Counter(c for r in a for c in r["codes"])),
    }
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True, choices=sorted(G.REQUESTS))
    ap.add_argument("--calls", type=int, default=8)
    ap.add_argument("--out", default=None)
    ap.add_argument("--live", action="store_true",
                    help="required; a credential's presence never starts a run")
    args = ap.parse_args(argv)
    if not args.live:
        print("refusing to run without --live"); return 2
    came_from = bridge_credential()
    if came_from is None:
        print("no credential in this process; nothing attempted"); return 2
    print(f"credential from {came_from} (name only)")
    print(f"prompt {prompt_module.PROMPT_VERSION} "
          f"{prompt_module.prompt_fingerprint()[:16]} (UNCHANGED)")
    record = run(args.request, args.calls)
    s = record["summary"]
    print(f"\nREQUEST '{args.request}': structured {s['structured_output']}/{s['calls']}  "
          f"valid {s['validated']}/{s['calls']}  built {s['built']}/{s['calls']}")
    print(f"    thickness {s['thickness_correct']}/{s['calls']}  "
          f"envelope {s['envelope_correct']}/{s['calls']}  "
          f"topology {s['topology_correct']}/{s['calls']}  "
          f"volume {s['volume_correct']}/{s['calls']}")
    print(f"    ==> STRICT SUCCESS {s['STRICT_SUCCESS']}/{s['calls']}")
    print(f"    failure codes: {s['failure_codes']}")
    out = args.out or f"/tmp/claude-0/stage68-{args.request}.json"
    pathlib.Path(out).write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
