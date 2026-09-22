"""Stage 67 arena: one prompt variant, N live calls, full spatial record.

Isolated by construction. An arm names a prompt variant; the arena patches
`prompt.system_prompt` for the life of that arm ONLY, records the variant's own
fingerprint, and restores the original afterwards. Nothing about the parser,
validator, executor or backend is touched by any arm, and no answer is
repaired, retried or mutated.

Success is `spatial.verdict`, decided on kernel numbers, NOT on "it built".
"""
from __future__ import annotations

import argparse, json, os, pathlib, sys, time
from collections import Counter

REQUEST = ("Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 "
           "(2) plates with 8mm diameter holes in center of each plate")

from cad_experimental import prompt as prompt_module
from cad_experimental import plan as plan_module
from cad_experimental import schema_ladder as ladder
from cad_experimental.config import (
    DEFAULT_MODEL, PROVIDER_NAME, bridge_credential, credential_variable,
)
from cad_experimental.generation import OperationPlanService
from cad_experimental.parser import PlanParseError, parse_plan_text
from cad_experimental.validation import validate_plan
from cad_experimental.cad_backend import resolve_backend
from cad_experimental.executor import execute_plan

import spatial
import variants


def run_arm(arm: str, calls: int) -> dict:
    variant = variants.VARIANTS[arm]
    request = variant.get("request", REQUEST)
    original = prompt_module.system_prompt
    text = variant["text"]()
    fingerprint = variants.fingerprint(text)

    build_schema = variant.get("schema") or (
        lambda: plan_module.strict_selector_union_provider_schema())
    schema = build_schema()
    metrics = ladder.grammar_metrics(schema)

    record = {
        "arm": arm,
        "hypothesis": variant["hypothesis"],
        "changed": variant["changed"],
        "request": request,
        "model": DEFAULT_MODEL,
        "provider": PROVIDER_NAME,
        "credential_from": credential_variable(),
        "prompt_fingerprint": fingerprint,
        "prompt_characters": len(text),
        "baseline_prompt_fingerprint": variants.fingerprint(original()),
        "schema": variant.get("schema_name", "strict_selector_union"),
        "schema_inlined": metrics["inlined_characters"],
        "schema_fingerprint": metrics["fingerprint"],
        "calls": calls,
        "is_live_model_result": True,
        "attempts": [],
    }

    prompt_module.system_prompt = lambda: text
    import cad_experimental.generation as gen
    gen.system_prompt = lambda: text
    original_schema = gen.strict_selector_union_provider_schema
    gen.strict_selector_union_provider_schema = build_schema
    try:
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
                    stage = "parsed"
                    ops = list(parsed.operations)
                    facts = spatial.describe_plan(ops)
                    row["plan"] = facts
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
                        stage = "validated"
                        execution = execute_plan(parsed, backend=backend)
                        row["built"] = execution.succeeded
                        if execution.failure:
                            failure_message = execution.failure.message
                            row["build_failure"] = {
                                "code": execution.failure.code,
                                "message": failure_message}
                        if execution.succeeded:
                            m = execution.bodies[0].measurement
                            measurement = m
                            row["measurement"] = {
                                "volume": m.volume, "solids": m.solid_count,
                                "faces": m.face_count, "edges": m.edge_count,
                                "minimum": list(m.minimum),
                                "maximum": list(m.maximum)}
                        v = spatial.verdict(facts, measurement)
                        row["spatial"] = v
                        row["SPATIALLY_CORRECT"] = v["spatially_correct"]
                        row["failure_modes"] = spatial.classify(
                            facts, bool(execution.succeeded), failure_message, v)
                        stage = ("SPATIALLY_CORRECT" if v["spatially_correct"]
                                 else ("built_but_wrong" if execution.succeeded
                                       else "build_failed"))
                    else:
                        stage = "invalid:" + ",".join(codes)
                        row["SPATIALLY_CORRECT"] = False
                        row["failure_modes"] = spatial.classify(
                            facts, False, None, None)
                except PlanParseError as exc:
                    stage = "parse_failed"
                    row["parse_error"] = str(exc)
                    row["SPATIALLY_CORRECT"] = False
            row["stage"] = stage
            row["codes"] = codes
            record["attempts"].append(row)
            pf = row.get("plan") or {}
            print(f"  {arm} #{i}: {row['outcome']:20s} {stage:22s} "
                  f"plates={pf.get('plate_count')} bores={pf.get('bore_count')} "
                  f"lines={pf.get('distinct_centrelines')} "
                  f"modes={row.get('failure_modes')} {row['seconds']}s", flush=True)
    finally:
        prompt_module.system_prompt = original
        gen.system_prompt = original
        gen.strict_selector_union_provider_schema = original_schema

    a = record["attempts"]
    record["summary"] = {
        "calls": len(a),
        "compiled": sum(1 for r in a if r.get("structured_output")),
        "fenced": sum(1 for r in a if r.get("fenced")),
        "parsed": sum(1 for r in a if r["stage"] not in ("parse_failed", "no_output")),
        "validated": sum(1 for r in a if r.get("plan_valid")),
        "built": sum(1 for r in a if r.get("built")),
        "SPATIALLY_CORRECT": sum(1 for r in a if r.get("SPATIALLY_CORRECT")),
        "three_bores": sum(1 for r in a if (r.get("plan") or {}).get("bore_count") == 3),
        "six_plates": sum(1 for r in a if (r.get("plan") or {}).get("plate_count") == 6),
        "codes": dict(Counter(c for r in a for c in r["codes"])),
        "failure_modes": dict(Counter(m for r in a for m in (r.get("failure_modes") or []))),
        "stages": dict(Counter(r["stage"] for r in a)),
    }
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(variants.VARIANTS))
    ap.add_argument("--calls", type=int, default=5)
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
    record = run_arm(args.arm, args.calls)
    s = record["summary"]
    print(f"\nARM {args.arm}: compiled {s['compiled']}/{s['calls']}  "
          f"validated {s['validated']}/{s['calls']}  built {s['built']}/{s['calls']}")
    print(f"    six plates {s['six_plates']}/{s['calls']}  "
          f"three bores {s['three_bores']}/{s['calls']}")
    print(f"    ==> SPATIALLY CORRECT {s['SPATIALLY_CORRECT']}/{s['calls']}")
    print(f"    failure modes: {s['failure_modes']}")
    print(f"    codes: {s['codes']}")
    out = args.out or f"/tmp/claude-0/arm-{args.arm}.json"
    pathlib.Path(out).write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
