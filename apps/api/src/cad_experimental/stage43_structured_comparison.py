"""Stage 43: the same comparison, with Anthropic structured output on.

This module exists because Stage 40 could not ask its own question. It ran
both arms with a strict parser and **no** grammar constraint, because the
operation-plan schema could not be compiled at all -- 31 optional properties
against an API limit of 24. Claude Haiku then wrapped almost every answer in
a markdown fence, both frozen parsers refuse a fence by design, and the run
scored 0% on both sides. The transport decided every case; the
representations were never compared.

Stage 41 changed the one fact that forced that configuration: it restructured
the operation-plan schema to **9** optional properties, and V1's is **8**.
Both are now inside every measured provider limit, so for the first time the
same comparison can be run the way it was designed to be run -- with the API
itself constraining the output format, which is exactly what a markdown fence
cannot survive.

What this module changes, and what it does not
----------------------------------------------

**The only intended difference from Stage 40 is structured output.** The
model, the cases, the attempts, the prompts, the parsers, the validators, the
adapter, the build and every scoring rule are imported from
:mod:`cad_experimental.representation_comparison` and used unchanged. This
module owns no expectation, no threshold and no judgement of its own: it
calls that module's :func:`run`, and reports what comes back.

In particular this module does **not**:

* strip markdown fences, or repair, retry or re-prompt anything -- if
  structured output does not deliver a bare JSON object, that is the result;
* alter either prompt, either representation, or the corpus;
* touch the frozen Stage 40 instrument, which stays exactly as it was
  measured so its result remains reproducible and comparable.

How the schema reaches the provider
-----------------------------------

Stage 40's ``_TimedModel`` equalises both arms and, while doing so, drops
``output_schema`` because ``STRUCTURED_OUTPUT_ENABLED`` is ``False`` there.
Rather than modify that frozen wrapper, this module inserts
:class:`StructuredModel` *beneath* it: the schema Stage 40 removes is put
back, by this module, immediately before the provider call.

The arm is identified by **exact** match against the two system prompts. They
are distinct, versioned and fingerprinted, so the match is deterministic --
and an unrecognised prompt raises rather than guesses, because attaching the
wrong representation's grammar would silently corrupt a measurement.
"""

from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_ai.provider import ModelRequest, ModelResponse, ProviderError

from .plan import provider_schema
from .representation_comparison import (
    CASES,
    MODEL,
    OPERATOR_KEY_VARIABLE,
    OPTIONAL_PROPERTY_LIMIT,
    PLAN,
    SHARED_MAX_OUTPUT_TOKENS,
    SHARED_TIMEOUT_SECONDS,
    UNSUPPORTED_SCHEMA_KEYWORDS,
    V1,
    CredentialUnavailable,
    bridge_credential,
    credential_present,
    format_report,
    frozen_state,
    optional_properties,
    run,
    sanitise_schema,
)

class SchemaNotCompilable(Exception):
    """A schema fails a measured provider limit, so no run may begin.

    Distinct from :class:`CredentialUnavailable`: that says the run cannot be
    paid for, this says the run would not measure what it claims to. Both
    stop a live run, and conflating them would misreport why.
    """


#: This stage's own identity. Recorded in every result file so a Stage 43 run
#: can never be mistaken for, or merged with, the Stage 40 baseline.
STAGE = 43
RESULT_KIND = "stage43-structured-output-comparison"

#: The one intended experimental difference from Stage 40.
STRUCTURED_OUTPUT_ENABLED = True

#: Measured separately from the document-wide limit: a single object carrying
#: more optional properties than this is refused as "too complex" even when
#: the document total is comfortably inside :data:`OPTIONAL_PROPERTY_LIMIT`.
PER_OBJECT_OPTIONAL_LIMIT = 14

#: The shortest description that should produce a document on both arms. Used
#: only by the live acceptance probe, which asks whether the **provider
#: compiles the schema** -- never whether the answer is any good.
PROBE_DESCRIPTION = "Create a 10 mm by 10 mm by 10 mm box."


# --- the schemas, exactly as each arm's own service asks for them -----------


def v1_schema() -> Dict[str, Any]:
    """The V1 schema, narrowed to the subset the API's compiler accepts.

    ``sanitise_schema`` is Stage 40's own function, and it removes only
    numeric and length bounds -- never a type, an enum, a ``required`` list
    or ``additionalProperties``. The shape the model is constrained to is
    therefore the same shape; the bounds it drops are enforced by the parser
    and the validator regardless, which is where they were always enforced.
    """
    from cad_ai.specification import response_schema

    return sanitise_schema(response_schema())


def plan_schema_for_provider() -> Dict[str, Any]:
    """The operation-plan schema Stage 41 built to be provider-compatible.

    Sent as it is. It is passed through :func:`sanitise_schema` only so that
    a regression which reintroduced a rejected keyword could not reach the
    API disguised as an unrelated failure -- on a compliant schema the call
    is an identity, and :func:`preflight` asserts that it is.
    """
    return sanitise_schema(provider_schema())


ARMS: Tuple[str, ...] = (V1, PLAN)


def schema_for(representation: str) -> Dict[str, Any]:
    """The schema one arm is constrained by."""
    if representation == V1:
        return v1_schema()
    if representation == PLAN:
        return plan_schema_for_provider()
    raise ValueError(f"unknown representation {representation!r}")


def _system_prompts() -> Dict[str, str]:
    """The exact system prompt each arm sends, keyed by arm."""
    from cad_ai.prompt import system_prompt as v1_prompt

    from .prompt import system_prompt as plan_prompt

    return {V1: v1_prompt(), PLAN: plan_prompt()}


# --- the offline preflight --------------------------------------------------


def schema_facts(schema: Mapping[str, Any]) -> Dict[str, Any]:
    """Everything the provider's documented limits are decided on.

    Computed from the schema object itself rather than recalled from a
    docstring, so a schema that regressed would be caught here rather than
    by a 400 in the middle of a paid run.
    """
    import hashlib

    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))

    worst_object = 0
    worst_path = ""
    closed = True
    used_keywords: set = set()
    min_items: List[int] = []

    def walk(node: Any, path: str) -> None:
        nonlocal worst_object, worst_path, closed
        if isinstance(node, dict):
            used_keywords.update(node.keys())
            if "minItems" in node and isinstance(node["minItems"], int):
                min_items.append(node["minItems"])
            properties = node.get("properties")
            if isinstance(properties, dict):
                required = set(node.get("required") or ())
                optional = len(set(properties) - required)
                if optional > worst_object:
                    worst_object = optional
                    worst_path = path or "<root>"
                if node.get("additionalProperties") is not False:
                    closed = False
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(schema, "")
    rejected = sorted(used_keywords & set(UNSUPPORTED_SCHEMA_KEYWORDS))
    return {
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "serialized_characters": len(canonical),
        "optional_properties": len(optional_properties(schema)),
        "optional_properties_limit": OPTIONAL_PROPERTY_LIMIT,
        "worst_object_optional_properties": worst_object,
        "worst_object_path": worst_path,
        "worst_object_limit": PER_OBJECT_OPTIONAL_LIMIT,
        "rejected_keywords_present": rejected,
        "every_object_closes_additional_properties": closed,
        "uses_one_of": "oneOf" in used_keywords,
        "min_items_values": sorted(set(min_items)),
        "definitions": len(schema.get("$defs", {}) or {}),
    }


def preflight() -> Dict[str, Any]:
    """Check both schemas against every measured provider limit. Free.

    Makes no network call. Returns the facts and a per-arm verdict, so a
    caller can refuse to start a paid run on a schema that cannot compile.
    """
    arms: Dict[str, Any] = {}
    for representation in ARMS:
        facts = schema_facts(schema_for(representation))
        violations: List[str] = []
        if facts["optional_properties"] > OPTIONAL_PROPERTY_LIMIT:
            violations.append(
                f"{facts['optional_properties']} optional properties against "
                f"a limit of {OPTIONAL_PROPERTY_LIMIT}"
            )
        if facts["worst_object_optional_properties"] > PER_OBJECT_OPTIONAL_LIMIT:
            violations.append(
                f"the object at {facts['worst_object_path']} has "
                f"{facts['worst_object_optional_properties']} optional "
                f"properties against a per-object limit of "
                f"{PER_OBJECT_OPTIONAL_LIMIT}"
            )
        if facts["rejected_keywords_present"]:
            violations.append(
                "rejected keywords present: "
                + ", ".join(facts["rejected_keywords_present"])
            )
        if not facts["every_object_closes_additional_properties"]:
            violations.append(
                "an object does not set additionalProperties: false"
            )
        if facts["uses_one_of"]:
            violations.append("oneOf is used; the API accepts only anyOf")
        if any(value not in (0, 1) for value in facts["min_items_values"]):
            violations.append(
                f"minItems values {facts['min_items_values']} -- only 0 or 1 "
                "is accepted"
            )
        arms[representation] = {
            "facts": facts,
            "violations": tuple(violations),
            "compilable_offline": not violations,
        }
    return {
        "stage": STAGE,
        "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
        "model": MODEL,
        "arms": arms,
        "both_arms_compilable_offline": all(
            arm["compilable_offline"] for arm in arms.values()
        ),
    }


def format_preflight(data: Mapping[str, Any]) -> str:
    """The preflight, as a table."""
    lines = [
        "=" * 72,
        f"STAGE {data['stage']} SCHEMA PREFLIGHT (offline; no model call)",
        "=" * 72,
        f"model                {data['model']}",
        f"structured output    {data['structured_output_enabled']}",
        "",
    ]
    for representation, arm in data["arms"].items():
        facts = arm["facts"]
        lines.extend([
            f"--- {representation}",
            f"  fingerprint               {facts['fingerprint'][:16]}",
            f"  serialized characters     {facts['serialized_characters']}",
            f"  optional properties       {facts['optional_properties']}"
            f"  (limit {facts['optional_properties_limit']})",
            f"  worst single object       "
            f"{facts['worst_object_optional_properties']}"
            f"  (limit {facts['worst_object_limit']})"
            f"  at {facts['worst_object_path']}",
            f"  rejected keywords         "
            f"{facts['rejected_keywords_present'] or 'none'}",
            f"  additionalProperties      "
            f"{'closed everywhere' if facts['every_object_closes_additional_properties'] else 'NOT CLOSED'}",
            f"  oneOf                     {facts['uses_one_of']}",
            f"  minItems values           {facts['min_items_values'] or 'none'}",
            f"  $defs                     {facts['definitions']}",
            f"  verdict                   "
            f"{'COMPILABLE (offline checks)' if arm['compilable_offline'] else 'WOULD BE REJECTED'}",
        ])
        for violation in arm["violations"]:
            lines.append(f"     -- {violation}")
        lines.append("")
    lines.append(
        "both arms compilable offline: "
        f"{data['both_arms_compilable_offline']}"
    )
    return "\n".join(lines)


# --- putting the schema back, beneath Stage 40's wrapper --------------------


class StructuredModel:
    """Re-attach the output schema Stage 40's wrapper removes.

    Stage 40 wraps whatever model it is given in its own ``_TimedModel``,
    which equalises ``max_output_tokens`` and drops ``output_schema``. That
    wrapper is frozen and is not modified here, so this object sits *inside*
    it and restores the schema on the way out to the provider.

    The arm is decided by an exact comparison against the two system prompts.
    An unrecognised prompt is an error: sending one representation's grammar
    with the other's instructions would produce a number that looked like a
    measurement and was not.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "anthropic")
        self._prompts = _system_prompts()
        self._schemas = {arm: schema_for(arm) for arm in ARMS}
        #: How many requests each arm was constrained on. Read by the CLI as
        #: a check that both arms really were given a grammar.
        self.attached: Dict[str, int] = {arm: 0 for arm in ARMS}

    @property
    def config(self) -> Any:
        return self._inner.config

    def _arm_of(self, system: str) -> str:
        for arm, prompt in self._prompts.items():
            if system == prompt:
                return arm
        raise ValueError(
            "the system prompt matches neither representation, so no schema "
            "can be attached without guessing which arm this request belongs "
            "to; refusing rather than risk mislabelling a measurement"
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        arm = self._arm_of(request.system)
        self.attached[arm] += 1
        return self._inner.generate(
            ModelRequest(
                system=request.system,
                user_text=request.user_text,
                output_schema=self._schemas[arm],
                max_output_tokens=request.max_output_tokens,
            )
        )


def real_model() -> Any:
    """The live Anthropic provider, pinned to this comparison's model."""
    bridge_credential()
    from cad_ai.anthropic_provider import AnthropicTextToCadModel
    from cad_ai.config import AiConfig

    return AnthropicTextToCadModel.from_environment(
        AiConfig(
            provider="anthropic",
            model=MODEL,
            timeout_seconds=SHARED_TIMEOUT_SECONDS,
        )
    )


# --- the live schema-acceptance probe (two calls) --------------------------


def probe_live() -> Dict[str, Any]:
    """Ask the provider to compile each schema. **Two real calls.**

    This is the only question the offline preflight cannot answer: the
    documented limits were themselves reverse-engineered, so acceptance is
    established by an actual call rather than by a rule. It sends one
    minimal description per arm and reports whether the request was accepted,
    never whether the answer was correct -- correctness is the comparison's
    job, and this probe must not become a tiny, unfrozen benchmark.
    """
    if not credential_present():
        raise CredentialUnavailable(
            f"{OPERATOR_KEY_VARIABLE} is not set; the schema-acceptance probe "
            "cannot run, and no other credential or provider may be "
            "substituted"
        )
    model = real_model()
    prompts = _system_prompts()
    results: Dict[str, Any] = {}
    for representation in ARMS:
        schema = schema_for(representation)
        started = time.monotonic()
        record: Dict[str, Any] = {
            "schema_fingerprint": schema_facts(schema)["fingerprint"],
        }
        try:
            response = model.generate(
                ModelRequest(
                    system=prompts[representation],
                    user_text=PROBE_DESCRIPTION,
                    output_schema=schema,
                    max_output_tokens=SHARED_MAX_OUTPUT_TOKENS,
                )
            )
        except ProviderError as error:
            record.update({
                "accepted": False,
                "error_kind": getattr(
                    getattr(error, "kind", None), "value", str(
                        getattr(error, "kind", "unknown")
                    )
                ),
                "error_message": str(error),
                "latency_seconds": round(time.monotonic() - started, 3),
            })
        else:
            text = response.text or ""
            stripped = text.strip()
            record.update({
                "accepted": True,
                "structured_output_reported": response.structured_output,
                "latency_seconds": round(time.monotonic() - started, 3),
                "usage": dict(response.usage or {}),
                "response_characters": len(text),
                # The point of the whole stage: did the answer arrive as a
                # bare JSON object, or inside a markdown fence?
                "starts_with_fence": stripped.startswith("```"),
                "contains_fence": "```" in text,
                "parses_as_json": _parses(stripped),
            })
        results[representation] = record
    return {
        "stage": STAGE,
        "kind": "schema-acceptance-probe",
        "model": MODEL,
        "calls": len(ARMS),
        "description": PROBE_DESCRIPTION,
        "arms": results,
        "both_accepted": all(arm.get("accepted") for arm in results.values()),
    }


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def format_probe(data: Mapping[str, Any]) -> str:
    """The probe result, as a table."""
    lines = [
        "=" * 72,
        f"STAGE {data['stage']} LIVE SCHEMA ACCEPTANCE PROBE "
        f"({data['calls']} real calls)",
        "=" * 72,
        f"model   {data['model']}",
        f"prompt  {data['description']!r}",
        "",
    ]
    for representation, arm in data["arms"].items():
        lines.append(f"--- {representation}")
        lines.append(f"  schema fingerprint   {arm['schema_fingerprint'][:16]}")
        if not arm.get("accepted"):
            lines.append("  ACCEPTED             NO")
            lines.append(f"  error kind           {arm.get('error_kind')}")
            lines.append(f"  provider said        {arm.get('error_message')}")
        else:
            lines.append("  ACCEPTED             YES")
            lines.append(
                f"  structured output    "
                f"{arm.get('structured_output_reported')}"
            )
            lines.append(f"  latency (s)          {arm.get('latency_seconds')}")
            lines.append(f"  usage                {arm.get('usage')}")
            lines.append(f"  markdown fence       {arm.get('contains_fence')}")
            lines.append(f"  parses as JSON       {arm.get('parses_as_json')}")
        lines.append("")
    lines.append(f"both schemas accepted: {data['both_accepted']}")
    return "\n".join(lines)


# --- the comparison itself --------------------------------------------------


def stage43_state() -> Dict[str, Any]:
    """What this run pinned, on top of Stage 40's frozen state.

    Stage 40's own record is embedded unchanged, for provenance, and the one
    field this stage deliberately differs on is stated separately rather than
    edited inside it.
    """
    inherited = frozen_state()
    return {
        "stage": STAGE,
        "kind": RESULT_KIND,
        "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
        "intended_difference_from_stage_40": (
            "structured output is enabled for both arms; the model, prompts, "
            "cases, attempts, parsers, validators and scoring rules are "
            "Stage 40's, used unchanged"
        ),
        "schemas": {
            arm: schema_facts(schema_for(arm)) for arm in ARMS
        },
        "stage_40_frozen_state": inherited,
    }


def run_structured(
    *,
    live: bool,
    attempts: Optional[int] = None,
    cases: Optional[Sequence[Any]] = None,
    cache_root: Optional[str] = None,
    progress: Any = None,
) -> Dict[str, Any]:
    """Stage 40's comparison, with both arms constrained by their schema.

    Every scoring decision in the returned data is Stage 40's. This function
    supplies a model that attaches the schema, and labels the result.
    """
    check = preflight()
    if not check["both_arms_compilable_offline"]:
        offenders = {
            arm: value["violations"]
            for arm, value in check["arms"].items()
            if value["violations"]
        }
        raise SchemaNotCompilable(
            "refusing to start a live run: a schema fails a measured "
            f"provider limit and would not compile -- {offenders}"
        )

    holder: Dict[str, Any] = {}

    def factory() -> Any:
        model = StructuredModel(real_model())
        holder["model"] = model
        return model

    from .representation_comparison import DEFAULT_ATTEMPTS

    data = run(
        live=live,
        attempts=DEFAULT_ATTEMPTS if attempts is None else attempts,
        cases=cases,
        cache_root=cache_root,
        model_factory=factory,
        progress=progress,
    )
    model = holder.get("model")
    data["stage43_state"] = stage43_state()
    data["schema_attached_per_arm"] = (
        dict(model.attached) if model is not None else {}
    )
    return data


def format_structured_report(data: Mapping[str, Any]) -> str:
    """Stage 40's table, with this stage's header stated truthfully.

    ``format_report`` prints ``structured_output_enabled`` out of the state it
    is handed. Stage 40's record says ``False`` and must keep saying so, so a
    copy carrying this run's real value is what gets formatted -- the number
    printed is then the number that was actually used.
    """
    shown = copy.deepcopy(dict(data))
    shown["frozen_state"] = dict(shown["frozen_state"])
    shown["frozen_state"]["structured_output_enabled"] = (
        STRUCTURED_OUTPUT_ENABLED
    )
    header = [
        "=" * 72,
        f"STAGE {STAGE}: V1 JSON vs OPERATION PLAN, WITH STRUCTURED OUTPUT",
        "=" * 72,
        "This is NOT the Stage 40 baseline. The one intended difference is",
        "that both arms are constrained by their own JSON schema at the API.",
        f"schema attached per arm: {data.get('schema_attached_per_arm', {})}",
        "",
    ]
    return "\n".join(header) + format_report(shown)


# --- CLI --------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The CLI. ``--live`` is required to spend anything."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Stage 43: compare V1 JSON against the CAD operation plan with "
            "Anthropic structured output enabled for both arms."
        )
    )
    parser.add_argument("--check", action="store_true",
                        help="offline schema preflight; calls no model")
    parser.add_argument("--probe-live", action="store_true",
                        help="ask the provider to compile each schema "
                             "(TWO real calls; not a benchmark)")
    parser.add_argument("--live", action="store_true",
                        help="run the full comparison (spends quota)")
    parser.add_argument("--attempts", type=int, default=None,
                        help="attempts per case per arm; applied to BOTH arms")
    parser.add_argument("--case", action="append", default=None,
                        help="run only this case id (repeatable)")
    parser.add_argument("--out", default=None,
                        help="write the full result as JSON to this path")
    arguments = parser.parse_args(argv)

    if arguments.check:
        data = preflight()
        print(format_preflight(data))
        print(f"\ncredential present: {credential_present()}")
        if arguments.out:
            _write(arguments.out, data)
        return 0 if data["both_arms_compilable_offline"] else 1

    if arguments.probe_live:
        offline = preflight()
        if not offline["both_arms_compilable_offline"]:
            print(format_preflight(offline))
            print("\nrefusing to probe: a schema fails the offline checks.")
            return 1
        try:
            data = probe_live()
        except CredentialUnavailable as error:
            print(f"STAGE {STAGE} PROBE: NOT RUN -- {error}")
            return 1
        print(format_probe(data))
        if arguments.out:
            _write(arguments.out, data)
        return 0 if data["both_accepted"] else 1

    if not arguments.live:
        print("refusing to run: pass --live to make real model calls.")
        print("  --check       offline schema preflight, free")
        print("  --probe-live  two calls, schema acceptance only")
        print(f"credential present: {credential_present()}")
        return 2

    if not credential_present():
        print(
            f"STAGE {STAGE} COMPARISON: NOT RUN -- {OPERATOR_KEY_VARIABLE} is "
            "not set. No other credential or provider may be substituted."
        )
        return 1

    selected = None
    if arguments.case:
        from .comparison_corpus import case as one_case

        selected = [one_case(name) for name in arguments.case]

    def show(record: Any) -> None:
        from .representation_comparison import SUCCESS_CATEGORIES

        mark = "ok" if record.category in SUCCESS_CATEGORIES else "XX"
        print(
            f"  [{mark}] a{record.attempt} {record.case_id:<24}"
            f"{record.representation:<16}{record.category}",
            flush=True,
        )

    data = run_structured(
        live=True, attempts=arguments.attempts, cases=selected, progress=show,
    )
    print()
    print(format_structured_report(data))
    if arguments.out:
        _write(arguments.out, data)
        print(f"\nwritten to {arguments.out}")
    return 0


def _write(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


__all__ = [
    "ARMS",
    "PER_OBJECT_OPTIONAL_LIMIT",
    "PROBE_DESCRIPTION",
    "RESULT_KIND",
    "STAGE",
    "STRUCTURED_OUTPUT_ENABLED",
    "SchemaNotCompilable",
    "StructuredModel",
    "format_preflight",
    "format_probe",
    "format_structured_report",
    "main",
    "plan_schema_for_provider",
    "preflight",
    "probe_live",
    "real_model",
    "run_structured",
    "schema_facts",
    "schema_for",
    "stage43_state",
    "v1_schema",
]


if __name__ == "__main__":
    raise SystemExit(main())
