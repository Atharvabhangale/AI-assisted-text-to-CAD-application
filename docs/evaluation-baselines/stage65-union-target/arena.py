"""Stage 65 experiment arena: one prompt variant, N live calls, full record.

Isolated by construction. An arm names a prompt variant; the arena patches
`prompt.system_prompt` for the life of that arm ONLY, records the variant's
own fingerprint, and restores the original afterwards. Nothing about the
parser, validator, executor or backend is touched by any arm, and no answer
is repaired, retried or mutated.

Every call records: prompt id and fingerprint, schema name/size/fingerprint,
the raw model output verbatim, the parse outcome, every validation P-code, and
the real build result through the configured backend.
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
from cad_experimental.generation import OperationPlanService, PlanOutcome
from cad_experimental.parser import PlanParseError, parse_plan_text
from cad_experimental.validation import validate_plan
from cad_experimental.cad_backend import resolve_backend
from cad_experimental.executor import execute_plan

import variants   # the prompt variants under test


def run_arm(arm: str, calls: int, backend_name: str) -> dict:
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

    # Patch ONLY for this arm, and only the prompt.
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

            stage, codes, union_target, bad_targets = "no_output", [], None, []
            if result.raw_text:
                try:
                    parsed = parse_plan_text(result.raw_text)
                    stage = "parsed"
                    ops = list(parsed.operations)
                    row["operations"] = [
                        {"id": o.id, "type": getattr(o, "TYPE", "?"),
                         "target": getattr(o, "target", None),
                         "tools": list(getattr(o, "tools", []) or []) or None}
                        for o in ops
                    ]
                    unions = [o for o in ops if getattr(o, "TYPE", None) == "union"]
                    row["used_union"] = bool(unions)
                    if unions:
                        union_target = unions[0].target
                        row["union_id"] = unions[0].id
                        row["union_target"] = union_target
                        # THE measured quantity: does any later modifier
                        # target the union's own id instead of its target?
                        union_ids = {u.id for u in unions}
                        bad_targets = [
                            o.id for o in ops
                            if getattr(o, "target", None) in union_ids
                        ]
                    row["targets_the_union_id"] = bad_targets
                    # THE SUCCESS CRITERION, and it is not "it built".
                    #
                    # One attempt built a plan that put every hole BEFORE the
                    # union, so there was no post-union feature at all -- and
                    # because no plate carried a position, the six coincident
                    # plates fused into ONE 40x20x5 plate. Valid, buildable,
                    # and not the part that was asked for: this project's own
                    # standing lesson (valid CAD != correct CAD).
                    #
                    # So a pass requires BOTH: a modifier that actually comes
                    # after the union and targets the surviving body, AND an
                    # envelope that is a hollow enclosure rather than a slab.
                    union_index = min(
                        (i for i, o in enumerate(ops)
                         if getattr(o, "TYPE", None) == "union"),
                        default=None)
                    after = ([] if union_index is None
                             else ops[union_index + 1:])
                    post = [o for o in after
                            if getattr(o, "target", None) is not None]
                    row["operations_after_union"] = len(after)
                    row["post_union_modifiers"] = [o.id for o in post]
                    row["post_union_targets_surviving_body"] = bool(post) and all(
                        getattr(o, "target", None) == union_target
                        for o in post)
                    verdict = validate_plan(parsed)
                    codes = sorted({p.code for p in verdict.problems})
                    row["problems"] = [{"code": p.code, "message": p.message}
                                       for p in verdict.problems]
                    row["plan_valid"] = verdict.valid
                    if verdict.valid:
                        stage = "validated"
                        execution = execute_plan(parsed, backend=backend)
                        row["built"] = execution.succeeded
                        if execution.failure:
                            row["build_failure"] = {
                                "code": execution.failure.code,
                                "message": execution.failure.message}
                        if execution.succeeded:
                            m = execution.bodies[0].measurement
                            row["measurement"] = {
                                "volume": m.volume, "solids": m.solid_count,
                                "faces": m.face_count, "edges": m.edge_count,
                                "minimum": list(m.minimum),
                                "maximum": list(m.maximum)}
                            envelope = [round(hi - lo, 3) for lo, hi in
                                        zip(m.minimum, m.maximum)]
                            row["envelope"] = envelope
                            # A hollow enclosure from these plates is
                            # 40 x 20 x 20. A slab is 40 x 20 x 5.
                            expected = variant.get("expected_envelope",
                                                   [40.0, 20.0, 20.0])
                            row["expected_envelope"] = expected
                            row["envelope_is_an_enclosure"] = (
                                sorted(envelope) == sorted(expected))
                            row["semantically_correct"] = bool(
                                row.get("post_union_targets_surviving_body")
                                and row["envelope_is_an_enclosure"])
                            stage = ("built"
                                     if row["semantically_correct"]
                                     else "built_but_wrong_part")
                        else:
                            stage = "build_failed"
                    else:
                        stage = "invalid:" + ",".join(codes)
                except PlanParseError as exc:
                    stage = "parse_failed"
                    row["parse_error"] = str(exc)
            row["stage"] = stage
            row["codes"] = codes
            record["attempts"].append(row)
            print(f"  {arm} #{i}: {row['outcome']:20s} {stage:26s} "
                  f"union={row.get('used_union')} "
                  f"bad_targets={len(bad_targets)} "
                  f"so={row['structured_output']} {row['seconds']}s", flush=True)
    finally:
        prompt_module.system_prompt = original
        gen.system_prompt = original
        gen.strict_selector_union_provider_schema = original_schema

    attempts = record["attempts"]
    record["summary"] = {
        "calls": len(attempts),
        "compiled": sum(1 for a in attempts if a.get("structured_output")),
        "fenced": sum(1 for a in attempts if a.get("fenced")),
        "used_union": sum(1 for a in attempts if a.get("used_union")),
        "parsed": sum(1 for a in attempts if a["stage"] != "parse_failed"
                      and a["stage"] != "no_output"),
        "validated": sum(1 for a in attempts if a.get("plan_valid")),
        "built": sum(1 for a in attempts if a.get("built")),
        "has_post_union_modifier": sum(
            1 for a in attempts if a.get("post_union_modifiers")),
        "post_union_targets_surviving_body": sum(
            1 for a in attempts
            if a.get("post_union_targets_surviving_body")),
        "envelope_is_an_enclosure": sum(
            1 for a in attempts if a.get("envelope_is_an_enclosure")),
        "SEMANTICALLY_CORRECT": sum(
            1 for a in attempts if a.get("semantically_correct")),
        "targeted_the_union_id": sum(
            1 for a in attempts if a.get("targets_the_union_id")),
        "codes": dict(Counter(c for a in attempts for c in a["codes"])),
        "stages": dict(Counter(a["stage"] for a in attempts)),
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
    record = run_arm(args.arm, args.calls, "")
    s = record["summary"]
    print(f"\nARM {args.arm}: compiled {s['compiled']}/{s['calls']}  "
          f"union {s['used_union']}/{s['calls']}  "
          f"validated {s['validated']}/{s['calls']}  "
          f"built {s['built']}/{s['calls']}  "
          f"targeted-union-id {s['targeted_the_union_id']}/{s['calls']}")
    print(f"    post-union modifier {s['has_post_union_modifier']}/{s['calls']}"
          f"  targets surviving body "
          f"{s['post_union_targets_surviving_body']}/{s['calls']}"
          f"  enclosure envelope {s['envelope_is_an_enclosure']}/{s['calls']}")
    print(f"    ==> SEMANTICALLY CORRECT "
          f"{s['SEMANTICALLY_CORRECT']}/{s['calls']}")
    print(f"codes: {s['codes']}")
    out = args.out or f"/tmp/claude-0/arm-{args.arm}.json"
    pathlib.Path(out).write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
