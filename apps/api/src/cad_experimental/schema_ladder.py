"""Stage 49: find the provider's compiled-grammar ceiling, offline first.

Stage 48 could not run because the provider refused both plan schemas, and
said why in its own words::

    The compiled grammar is too large, which would cause performance issues.
    Simplify your tool schemas or reduce the number of strict tools.

That is a real constraint and it is not a byte count. This module builds a
**ladder** of schema variants between the one grammar known to compile and
the ones known not to, so the ceiling can be located with the fewest possible
live calls -- one per variant, chosen deliberately, never a sweep.

What this module is not
-----------------------

**It does not change the operation plan.** The canonical IR is
:mod:`cad_experimental.plan` and stays exactly as it is; every variant here
is built by the existing :func:`cad_experimental.plan._plan_document` through
its existing knobs. Nothing new is invented, no operation is redefined, and
no variant is written back into ``plan.py``.

**A provider schema is an encoding of the IR, not the IR.** It says what the
model may *say*; the parser and the validator decide what a plan *means* and
stay authoritative over every variant. A schema that omits a field does not
make that field legal-by-omission or illegal-by-omission -- it makes it
unsayable by a grammar-constrained decoder, and the parser's rules are
unchanged either way. This distinction is the whole reason a narrow encoding
is safe: it can lose expressiveness without losing rigour.

Why serialized size is only a proxy
-----------------------------------

The limit is on the **compiled** grammar. Stage 41 measured that a ``$ref``
does not shrink it and that unused ``$defs`` still cost budget, so the
honest size proxy is the schema with every ``$ref`` **inlined** -- a shared
definition referenced five times is compiled five times. Every measurement
here is therefore taken on the expanded form, and the raw serialized size is
reported alongside only to show how badly it misleads: ``compact`` is 19%
smaller than ``provider`` in bytes and was refused just the same.

What is measured, and what is known
-----------------------------------

======================  ========  =========  ========  =========
variant                 inlined     nodes    branches  live
======================  ========  =========  ========  =========
``executable``              3622        82          6  **accepted**
``compact``                 6190       147          8  **refused**
``provider``                7351       180          8  **refused**
======================  ========  =========  ========  =========

So the ceiling lies somewhere in ``(3622, 6190]`` inlined characters, and the
ladder's job is to say where. The marginal cost of each capability, measured
against the six-type base, says which rungs are worth spending a call on:

======================  ==========  =========
capability added        inlined     nodes
======================  ==========  =========
``sketch``                  +2653        +73
``pattern``                 +1014        +24
``extrude``                  +459         +9
``revolve``                  +455         +9
semantic selectors           +150         +2
----------------------  ----------  ---------
(of which sketch's
``constraints`` alone)      +1161        +33
======================  ==========  =========

**One capability dominates.** ``sketch`` is 73% of the growth from the
accepted grammar to the full vocabulary, and its ``constraints`` are 44% of
``sketch``. Everything else is cheap: ``extrude`` and ``revolve`` together
cost less than a third of ``sketch``, and the semantic edge selectors that
Stage 47 added cost almost nothing at all.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .plan import (
    MERGED_SCHEMA_GROUPS,
    OPERATION_TYPES,
    SELECT_MODES,
    V1_FEATURE_TYPES,
    V1_SELECT_MODES,
    _plan_document,
)

#: Where the ceiling is known to lie, from the Stage 48 diagnostic probe.
#: Both bounds are measured live, not assumed.
KNOWN_ACCEPTED_INLINED = 3622
KNOWN_REFUSED_INLINED = 6190

#: The provider's own words when it refuses for grammar size.
GRAMMAR_TOO_LARGE_MARKER = "compiled grammar is too large"


# --- the size proxy ---------------------------------------------------------


def expand_refs(schema: Any, defs: Optional[Mapping[str, Any]] = None,
                depth: int = 0) -> Any:
    """Inline every ``$ref``, because the compiler does.

    Stage 41 measured that ``$ref`` does not shrink the compiled grammar: a
    definition referenced five times is compiled five times. Measuring the
    referenced form therefore flatters a schema in exactly the way that led
    to sending two grammars the provider could not compile.

    ``depth`` guards a recursive definition; the plan schema has none today,
    and a guard is cheaper than discovering that it grew one.
    """
    if defs is None:
        defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    if depth > 40:
        return {"type": "string"}
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = str(schema["$ref"]).split("/")[-1]
            return expand_refs(
                copy.deepcopy(dict(defs.get(name, {}))), defs, depth + 1
            )
        return {
            key: expand_refs(value, defs, depth + 1)
            for key, value in schema.items() if key != "$defs"
        }
    if isinstance(schema, list):
        return [expand_refs(item, defs, depth + 1) for item in schema]
    return schema


def grammar_metrics(schema: Mapping[str, Any]) -> Dict[str, Any]:
    """Every proxy for compiled grammar size, measured on the inlined form.

    ``inlined_characters`` is the headline number and the one to compare
    against :data:`KNOWN_ACCEPTED_INLINED` and
    :data:`KNOWN_REFUSED_INLINED`. The rest are reported because the limit is
    not published and a single proxy that happened to correlate once is not
    a model of it -- if the ceiling turns out to track node or alternative
    count better than characters, these are what will show it.
    """
    expanded = expand_refs(schema)
    nodes = enum_alternatives = any_of_alternatives = properties = 0
    worst_object = 0

    def walk(node: Any) -> None:
        nonlocal nodes, enum_alternatives, any_of_alternatives
        nonlocal properties, worst_object
        if isinstance(node, dict):
            nodes += 1
            if isinstance(node.get("enum"), list):
                enum_alternatives += len(node["enum"])
            if isinstance(node.get("anyOf"), list):
                any_of_alternatives += len(node["anyOf"])
            own = node.get("properties")
            if isinstance(own, dict):
                properties += len(own)
                optional = len(set(own) - set(node.get("required") or ()))
                worst_object = max(worst_object, optional)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(expanded)
    raw = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return {
        "inlined_characters": len(
            json.dumps(expanded, sort_keys=True, separators=(",", ":"))
        ),
        "serialized_characters": len(raw),
        "nodes": nodes,
        "enum_alternatives": enum_alternatives,
        "any_of_alternatives": any_of_alternatives,
        "properties": properties,
        "worst_object_optional_properties": worst_object,
        "branches": len(
            schema["properties"]["operations"]["items"]["anyOf"]
        ),
        "definitions": len(schema.get("$defs", {}) or {}),
        "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


# --- the ladder -------------------------------------------------------------


class Variant:
    """One rung: a named schema, built from the canonical plan's own builder.

    ``capabilities`` is what a grammar-constrained model could *say* under
    this variant. It is the field Phase 4 cares about: a variant that cannot
    express ``sketch`` cannot be used to ask whether a model would choose a
    sketch, which is precisely the mistake Stage 43 made without noticing.
    """

    def __init__(self, name: str, *, kinds: Tuple[str, ...],
                 merged: Tuple[Tuple[str, ...], ...] = (),
                 omit_parameters: Tuple[str, ...] = (),
                 selector_modes: Tuple[str, ...] = V1_SELECT_MODES,
                 note: str = "") -> None:
        self.name = name
        self.kinds = kinds
        self.merged = merged
        self.omit_parameters = omit_parameters
        self.selector_modes = selector_modes
        self.note = note

    def schema(self) -> Dict[str, Any]:
        return _plan_document(
            self.kinds,
            merged=self.merged,
            omit_parameters=self.omit_parameters,
            selector_modes=self.selector_modes,
        )

    @property
    def capabilities(self) -> Tuple[str, ...]:
        """Operation types a model could emit under this variant."""
        return tuple(self.kinds)

    @property
    def semantic_selectors(self) -> bool:
        """Whether Stage 47's richer selector modes are reachable."""
        return set(self.selector_modes) > set(V1_SELECT_MODES)

    def describe(self) -> Dict[str, Any]:
        facts = grammar_metrics(self.schema())
        return {
            "variant": self.name,
            "note": self.note,
            "capabilities": list(self.capabilities),
            "semantic_selectors": self.semantic_selectors,
            "omitted_parameters": list(self.omit_parameters),
            "merged_groups": [list(g) for g in self.merged],
            **facts,
            "predicted": _prediction(facts["inlined_characters"]),
        }


def _prediction(inlined: int) -> str:
    """Where this size sits relative to what the provider has actually done.

    A prediction, stated as one: the bounds are two measured points, not a
    model of the limit, and the ladder exists because the space between them
    is unknown.
    """
    if inlined <= KNOWN_ACCEPTED_INLINED:
        return "at_or_below_known_accepted"
    if inlined >= KNOWN_REFUSED_INLINED:
        return "at_or_above_known_refused"
    return "unknown_between_the_bounds"


#: The capability ladder. Each rung adds exactly one capability to the one
#: below it, so a refusal names the capability that crossed the ceiling
#: rather than leaving a set of changes to pick apart.
_SIX = V1_FEATURE_TYPES


def _ladder() -> Tuple[Variant, ...]:
    return (
        Variant(
            "L0-executable", kinds=_SIX,
            note="Stage 43's grammar. The one point known to compile.",
        ),
        Variant(
            "L1-sketch", kinds=_SIX + ("sketch",),
            note="The single most expensive capability, added alone.",
        ),
        Variant(
            "L2-extrude", kinds=_SIX + ("sketch", "extrude"),
            note="A sketch is only useful if something consumes it.",
        ),
        Variant(
            "L3-revolve", kinds=_SIX + ("sketch", "extrude", "revolve"),
            note="Both profile consumers.",
        ),
        Variant(
            "L4-pattern", kinds=_SIX + ("sketch", "extrude", "revolve",
                                        "pattern"),
            note="Stage 46's native operation.",
        ),
        Variant(
            "L5-selectors",
            kinds=_SIX + ("sketch", "extrude", "revolve", "pattern"),
            selector_modes=SELECT_MODES,
            note="Stage 47's semantic selectors. The full vocabulary.",
        ),
    )


#: Alternative encodings of the **same** capabilities, cheaper to compile.
#: Each is a different answer to "what may the model say", never a different
#: answer to "what does a plan mean": the parser and validator are untouched
#: by every one of them.
def _compressions() -> Tuple[Variant, ...]:
    full = _SIX + ("sketch", "extrude", "revolve", "pattern")
    return (
        Variant(
            "C1-merged", kinds=full, merged=MERGED_SCHEMA_GROUPS,
            selector_modes=SELECT_MODES,
            note="Full vocabulary, fillet/chamfer and extrude/revolve "
                 "each one branch. Equals provider_schema plus pattern.",
        ),
        Variant(
            "C2-no-constraints", kinds=full, merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",), selector_modes=SELECT_MODES,
            note="C1 without a sketch's constraints -- the documented "
                 "fallback, and measured REFUSED as compact_provider_schema.",
        ),
        Variant(
            "C3-lean-sketch", kinds=full, merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="C2 with V1 selectors. Semantic selectors cost only ~150 "
                 "inlined characters, so this isolates how little they "
                 "matter next to a sketch.",
        ),
        Variant(
            "C4-profiles-no-sketch",
            kinds=_SIX + ("extrude", "revolve", "pattern"),
            merged=MERGED_SCHEMA_GROUPS, selector_modes=SELECT_MODES,
            note="Everything except sketch. Not a proposal -- the "
                 "measurement of what sketch alone costs, at full size.",
        ),
        Variant(
            "C5-minimal-profiles", kinds=_SIX + ("sketch", "extrude"),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="The smallest grammar that can still express a profile "
                 "and consume it. The fallback if the ceiling is low.",
        ),
        Variant(
            "C6-sketch-floor", kinds=_SIX + ("sketch",),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="The leanest grammar that can express a sketch at all. "
                 "Not useful on its own -- nothing consumes the profile -- "
                 "but it is the floor: if this is refused, no sketch-"
                 "carrying grammar fits and the question changes.",
        ),
        # --- profile-focused encodings, from the Stage 50 measurements ---
        #
        # C4 and C6 were both refused, at 4698 and 4551, and C4 carries no
        # sketch at all. So the cost is not sketch's structure specifically:
        # it is the whole grammar, and the base of six solid types is most
        # of it. These encodings spend the budget the other way round --
        # keep the profile pipeline, drop the solid operations a profile
        # request does not need -- which is what brings a sketch-carrying
        # grammar under the accepted bound for the first time.
        #
        # They are narrower encodings, not a narrower language: every type
        # omitted here is still in the IR, still parsed and still validated.
        # A caller picks one deliberately and the choice is recorded.
        Variant(
            "P1-profile-core",
            kinds=("box", "cylinder", "sketch", "extrude", "revolve"),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="The smallest grammar that expresses a whole profile "
                 "pipeline -- sketch, both consumers, and two solids to "
                 "combine them with. Drops through_hole, subtract, fillet "
                 "and chamfer.",
        ),
        Variant(
            "P2-profile-and-hole",
            kinds=("box", "cylinder", "through_hole", "sketch", "extrude"),
            omit_parameters=("constraints",),
            note="P1 traded for a through_hole: the most-used modifier in "
                 "the corpus. Sits inside the unknown band, so it measures "
                 "the ceiling and adds capability in the same call.",
        ),
        Variant(
            "P3-profile-union",
            kinds=("box", "cylinder", "through_hole", "sketch", "extrude",
                   "revolve"),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="The union of P1 and P2: sketch, both consumers, and a "
                 "through_hole. If this compiles, one encoding covers every "
                 "profile capability and the two-instrument split is "
                 "unnecessary.",
        ),
        Variant(
            "S1-selector-solids",
            kinds=("box", "cylinder", "through_hole", "subtract", "fillet",
                   "chamfer"),
            merged=MERGED_SCHEMA_GROUPS,
            selector_modes=SELECT_MODES,
            note="Stage 53: the six solid types with the full selector "
                 "vocabulary. A profile encoding cannot carry a selector at "
                 "all -- nothing in it selects an edge -- so a "
                 "selector-capable grammar must contain fillet or chamfer.",
        ),
        Variant(
            "P4-profile-union-subtract",
            kinds=("box", "cylinder", "through_hole", "subtract", "sketch",
                   "extrude", "revolve"),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            note="P3 plus subtract, 70 inlined characters below the proven "
                 "refusal. The upper edge of the band, and the last "
                 "capability that could plausibly still fit.",
        ),
    )


def variants() -> Tuple[Variant, ...]:
    """Every variant, ladder first then compressions. Deterministic order."""
    return _ladder() + _compressions()


def variants_by_name() -> Dict[str, Variant]:
    return {variant.name: variant for variant in variants()}


def variant(name: str) -> Variant:
    found = variants_by_name().get(name)
    if found is None:
        raise KeyError(
            f"unknown variant {name!r}; known: "
            f"{', '.join(sorted(variants_by_name()))}"
        )
    return found


def ladder_report() -> Dict[str, Any]:
    """Every variant measured, offline. Calls no model."""
    rows = [item.describe() for item in variants()]
    return {
        "stage": 49,
        "kind": "schema-ladder-offline-measurement",
        "known_accepted_inlined": KNOWN_ACCEPTED_INLINED,
        "known_refused_inlined": KNOWN_REFUSED_INLINED,
        "note": (
            "Sizes are of the ref-inlined schema, because the provider "
            "compiles the inlined form. Predictions are bounds from two "
            "measured points, not a model of the limit."
        ),
        "variants": rows,
    }


def format_ladder(data: Mapping[str, Any]) -> str:
    """The ladder, as a table."""
    lines = [
        "=" * 100,
        "SCHEMA LADDER -- offline measurement, no model call",
        "=" * 100,
        f"known ACCEPTED at {data['known_accepted_inlined']} inlined chars; "
        f"known REFUSED at {data['known_refused_inlined']}",
        "",
        f"{'variant':<22}{'inlined':>9}{'raw':>8}{'nodes':>7}{'br':>4}"
        f"{'defs':>6}{'worst':>7}  {'prediction':<28} capabilities",
        "-" * 100,
    ]
    for row in data["variants"]:
        extra = len(row["capabilities"])
        selectors = "+sel" if row["semantic_selectors"] else ""
        lines.append(
            f"{row['variant']:<22}{row['inlined_characters']:>9}"
            f"{row['serialized_characters']:>8}{row['nodes']:>7}"
            f"{row['branches']:>4}{row['definitions']:>6}"
            f"{row['worst_object_optional_properties']:>7}  "
            f"{row['predicted']:<28} {extra} types {selectors}"
        )
    return "\n".join(lines)


# --- one variant, one call --------------------------------------------------

#: The probe description. Deliberately trivial: the question is only whether
#: the provider COMPILES the grammar, never whether the answer is any good.
#: A probe that judged an answer would be an unfrozen benchmark.
PROBE_DESCRIPTION = "Create a 10 mm by 10 mm by 10 mm box."


def probe_variant(name: str) -> Dict[str, Any]:
    """Send **one** variant in **one** call. Never a sweep.

    Nothing here loops over the ladder. Each call costs money and, more to
    the point, each one answers a question that changes which variant is
    worth asking about next -- so the choice stays with a person, and this
    function probes exactly what it was named.
    """
    import time

    from cad_ai.provider import ModelRequest, ProviderError

    from .stage48_capability_evaluation import (
        MODEL,
        OPERATOR_KEY_VARIABLE,
        SHARED_MAX_OUTPUT_TOKENS,
        CredentialUnavailable,
        _error_class,
        credential_present,
        real_model,
        redact,
    )
    from .prompt import system_prompt

    chosen = variant(name)
    if not credential_present():
        raise CredentialUnavailable(
            f"{OPERATOR_KEY_VARIABLE} is not set; the ladder probe cannot "
            "run, and no other credential or provider may be substituted"
        )
    schema = chosen.schema()
    facts = grammar_metrics(schema)
    model = real_model()
    started = time.monotonic()
    record: Dict[str, Any] = {
        "stage": 49,
        "kind": "schema-ladder-variant-probe",
        "model": MODEL,
        "variant": chosen.name,
        "note": chosen.note,
        "capabilities": list(chosen.capabilities),
        "semantic_selectors": chosen.semantic_selectors,
        "calls": 1,
        **{
            key: facts[key] for key in (
                "fingerprint", "inlined_characters", "serialized_characters",
                "nodes", "branches", "definitions", "properties",
                "worst_object_optional_properties",
            )
        },
    }
    try:
        response = model.generate(
            ModelRequest(
                system=system_prompt(),
                user_text=PROBE_DESCRIPTION,
                output_schema=schema,
                max_output_tokens=SHARED_MAX_OUTPUT_TOKENS,
            )
        )
    except ProviderError as error:
        detail = redact(str(getattr(error, "detail", "") or ""))
        record.update({
            "accepted": False,
            "error_kind": getattr(
                getattr(error, "kind", None), "value", "unknown"
            ),
            "error_class": _error_class(error),
            "detail": detail,
            "grammar_too_large": GRAMMAR_TOO_LARGE_MARKER in detail.lower(),
            "latency_seconds": round(time.monotonic() - started, 3),
        })
    else:
        record.update({
            "accepted": True,
            "structured_output_reported": response.structured_output,
            "usage": dict(response.usage or {}),
            "latency_seconds": round(time.monotonic() - started, 3),
        })
    return record


def format_variant_probe(record: Mapping[str, Any]) -> str:
    """One probe result, and what it moves."""
    lines = [
        "=" * 72,
        f"SCHEMA LADDER PROBE -- {record['variant']} (1 real call)",
        "=" * 72,
        f"model        {record['model']}",
        f"fingerprint  {str(record['fingerprint'])[:16]}",
        f"inlined      {record['inlined_characters']} "
        f"(accepted<={KNOWN_ACCEPTED_INLINED}, "
        f"refused>={KNOWN_REFUSED_INLINED})",
        f"nodes        {record['nodes']}   branches {record['branches']}   "
        f"defs {record['definitions']}",
        f"capabilities {', '.join(record['capabilities'])}",
        "",
    ]
    if record.get("accepted"):
        lines.append("ACCEPTED     YES")
        lines.append(
            f"new lower bound: the ceiling is at least "
            f"{record['inlined_characters']} inlined characters"
        )
    else:
        lines.append("ACCEPTED     NO")
        lines.append(f"error class  {record.get('error_class')}")
        lines.append(f"grammar size {record.get('grammar_too_large')}")
        lines.append(f"provider said {record.get('detail')}")
        if record.get("grammar_too_large"):
            lines.append(
                f"new upper bound: the ceiling is below "
                f"{record['inlined_characters']} inlined characters"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The CLI. ``--probe`` names one variant and makes one call."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Locate the provider's compiled-grammar ceiling."
    )
    parser.add_argument("--list", action="store_true",
                        help="measure every variant offline; calls nothing")
    parser.add_argument("--probe", default=None,
                        help="probe ONE named variant with ONE real call")
    parser.add_argument("--out", default=None,
                        help="write the result as JSON to this path")
    arguments = parser.parse_args(argv)

    from .stage48_capability_evaluation import (
        PROTECTED_BASELINE_DIRECTORIES, writes_into_a_baseline,
    )

    if arguments.out and writes_into_a_baseline(arguments.out):
        print(
            f"refusing to write to {arguments.out}: it is inside a "
            "protected baseline directory "
            f"({', '.join(PROTECTED_BASELINE_DIRECTORIES)})."
        )
        return 2

    if arguments.probe:
        from .stage48_capability_evaluation import CredentialUnavailable

        try:
            record = probe_variant(arguments.probe)
        except KeyError as error:
            print(error)
            return 2
        except CredentialUnavailable as error:
            print(f"LADDER PROBE: NOT RUN -- {error}")
            return 1
        print(format_variant_probe(record))
        if arguments.out:
            with open(arguments.out, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print(f"\nwritten to {arguments.out}")
        return 0 if record.get("accepted") else 1

    data = ladder_report()
    print(format_ladder(data))
    if not arguments.list:
        print(
            "\nno model was called. --probe <variant> makes exactly one "
            "call; there is deliberately no flag that probes them all."
        )
    if arguments.out:
        with open(arguments.out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
    return 0


__all__ = [
    "GRAMMAR_TOO_LARGE_MARKER",
    "KNOWN_ACCEPTED_INLINED",
    "KNOWN_REFUSED_INLINED",
    "PROBE_DESCRIPTION",
    "Variant",
    "expand_refs",
    "format_ladder",
    "format_variant_probe",
    "grammar_metrics",
    "ladder_report",
    "main",
    "probe_variant",
    "variant",
    "variants",
    "variants_by_name",
]


if __name__ == "__main__":
    raise SystemExit(main())
