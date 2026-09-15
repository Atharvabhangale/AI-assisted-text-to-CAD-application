"""Stage 48: what the model can do with the operation plan Stages 44-47 made.

Stage 43 answered a question that no longer covers the system. It sent
:func:`~cad_experimental.plan.executable_schema` -- six operation types, no
sketch branch, no pattern, two edge selectors -- so a grammar-constrained
model **could not** emit a profile chain, a pattern or a semantic selector,
whatever the prompt said. Stage 44 established that the profile refusals it
recorded were forced rather than chosen. Stages 45-47 then added the feature
graph, `pattern` and semantic edge selection on top.

Re-running Stage 43 today would send the old grammar with the new prompt and
record the mismatch as a model result. So Stage 43 stays exactly as it is --
the frozen comparison for the earlier capability envelope -- and this is a
separate instrument with its own corpus, its own schema choice, its own
version and its own output directory.

    Stage 43   frozen. executable_schema, prompt 2026-09-10.7, 13 cases.
    Stage 48   this.   provider_schema, the current prompt, 30 cases.

Two result groups, never one number
-----------------------------------
:data:`~cad_experimental.stage48_corpus.LEGACY` is the thirteen Stage 40
requests, answered by **both** representations. It is the fair V1-vs-plan
comparison *within this run*.

:data:`~cad_experimental.stage48_corpus.CAPABILITY` is seventeen requests
answered by the operation plan **only**, because V1 has no sketch, no pattern
and no semantic selector: asking it would re-measure the vocabulary gap and
report it as a model score.

The two are summarised separately and there is no combined headline rate.
A single number over both groups would be a V1 score diluted by cases V1 was
never asked, which is exactly the conflation this stage exists to avoid.

**The legacy group is not a delta against Stage 43.** Three things moved:
the prompt, the plan schema, and -- this one matters most -- the way a
semantic selector is scored. Stage 40's plan runner calls ``plan_to_document``
directly, so a plan using `straight` or `circular` raises
:class:`~cad_experimental.adapter.SelectorNotExpressible` and lands in its
generic ``except Exception`` arm as ``SEMANTICALLY_INCORRECT``. That was
right when no such selector existed. Today it would score a **correct,
buildable** answer as wrong, and legacy cases 07 and 08 ("chamfer/fillet the
vertical edges") are exactly where the current prompt steers the model
towards one. This module therefore builds through
:func:`cad_experimental.build.build_plan`, which chooses the document path or
the graph-driven executor explicitly. Same metric *definitions*; a runner
that no longer mis-scores a capability that did not exist.

What is preserved, exactly
--------------------------
Imported from Stage 40 and used unchanged: the model, the token ceiling, the
timeout, the requested outputs, the attempt count, the failure taxonomy, the
rule that a provider error stays out of every denominator, the geometry
tolerances, the credential handling and the refusal to start without
``--live``. Imported from Stage 43: the schema-facts computation and the
provider limits it checks against.

What this module does **not** do: strip a markdown fence, repair output,
retry, re-prompt, normalise anything for the benchmark's benefit, or write
anywhere near the Stage 40 and Stage 43 baselines. :func:`preflight` asserts
the last one rather than trusting it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.application_service import CadApplicationService

from cad_ai.provider import ModelRequest, ModelResponse, ProviderError

# Re-exported rather than called: `build_plan` catches both and reports
# them as fields, but they are part of the outcome vocabulary a reader of
# a Stage 48 result needs, and a test asserts the V1 document path still
# raises the second one for a semantic selector.
from .adapter import ExecutionUnsupported, SelectorNotExpressible
from .build import build_plan
from .plan import (
    compact_provider_schema,
    executable_schema,
    provider_schema,
)
from .representation_comparison import (
    BUILD_FAILED,
    CORRECT_UNSUPPORTED,
    CORRECT_VALID_UNEXECUTABLE,
    EXECUTION_UNSUPPORTED,
    OK,
    OPERATOR_KEY_VARIABLE,
    OPTIONAL_PROPERTY_LIMIT,
    PARSER_REJECTED,
    PLAN,
    PLAN_VALIDATION_REJECTED,
    PROVIDER_ERROR,
    RENDERMODEL_FAILED,
    SEMANTICALLY_INCORRECT,
    SHARED_MAX_OUTPUT_TOKENS,
    SHARED_TIMEOUT_SECONDS,
    SUCCESS_CATEGORIES,
    V1,
    V1_VALIDATION_REJECTED,
    WRONGLY_ANSWERED,
    WRONGLY_REFUSED,
    CredentialUnavailable,
    MODEL,
    bridge_credential,
    credential_present,
    sanitise_schema,
)
from .stage43_structured_comparison import (
    PER_OBJECT_OPTIONAL_LIMIT,
    SchemaNotCompilable,
    schema_facts,
)
from .stage48_corpus import (
    ACCEPTED_REFUSALS,
    BOUNDING_BOX_ATOL,
    CAPABILITY,
    CASES,
    CATEGORIES,
    EXPECT_BUILD,
    EXPECT_CLARIFICATION,
    EXPECT_NO_PART,
    EXPECT_UNSUPPORTED,
    EXPECT_VALID_UNEXECUTABLE,
    GROUPS,
    INVALID_PLANS,
    LEGACY,
    PARSER as REFUSED_BY_PARSER,
    STAGE48_CORPUS_VERSION,
    VALIDATOR as REFUSED_BY_VALIDATOR,
    VOLUME_RTOL,
    CapabilityCase,
    case as corpus_case,
    cases_in,
    corpus_fingerprint,
)

class BaselineMissing(Exception):
    """A protected baseline is absent or has changed. Nothing may run.

    Deliberately not a :class:`SchemaNotCompilable` and not a
    :class:`~cad_experimental.representation_comparison.CredentialUnavailable`:
    those say the run would not measure what it claims, and that it cannot
    be paid for. This says the run might destroy evidence that cannot be
    regenerated, which is the one failure worth stopping hardest on.
    """


class GroupsNotComparable(Exception):
    """The legacy group drifted from the frozen corpus it is defined by."""


class ReferenceGeometryWrong(Exception):
    """An expectation the kernel disagrees with, or a rule that stopped.

    Raised before any paid call: an expectation no plan can satisfy would
    score every attempt on that case incorrect and look like a model
    failure.
    """


#: This stage's identity. Recorded in every result so a Stage 48 run can
#: never be mistaken for, or merged with, Stage 40's or Stage 43's.
STAGE = 48
RESULT_KIND = "stage48-capability-evaluation"

#: The evaluation instrument's own version, separate from the corpus's.
#: Bumped when a scoring rule, a metric or a recorded field changes.
STAGE48_VERSION = "1.0.0"

#: Structured output is on, as it was for Stage 43. Not the variable under
#: test here; it is how the answer arrives as JSON at all.
STRUCTURED_OUTPUT_ENABLED = True

#: The plan schema this stage sends, **named rather than computed**. Nothing
#: selects a variant automatically -- the same rule
#: :func:`cad_experimental.cad_backend.resolve_backend` keeps, for the same
#: reason: a run whose schema was chosen for it cannot be read.
#:
#: ``provider_schema`` covers the whole ten-type vocabulary in eight
#: branches. If the provider refuses it on compiled-grammar size (unverified
#: -- see :func:`probe_live`), ``--plan-schema compact`` is the documented
#: fallback and the choice is recorded in the result either way.
DEFAULT_PLAN_SCHEMA = "provider"

PLAN_SCHEMAS: Mapping[str, Any] = {
    "provider": provider_schema,
    "compact": compact_provider_schema,
    # Present so a caller can reproduce Stage 43's grammar deliberately and
    # see the difference. Never the default, and never selected for anyone.
    "executable": executable_schema,
}

#: The probe description. Short on purpose: the question is whether the
#: provider COMPILES the schema, never whether the answer is any good.
PROBE_DESCRIPTION = "Create a 10 mm by 10 mm by 10 mm box."

#: A second probe that a six-type grammar could not answer, so an acceptance
#: result says something about the widened schema rather than about a subset
#: Stage 43 already proved. Still not a benchmark: one call, and only
#: "did the request compile" is read from it.
PROBE_PROFILE_DESCRIPTION = (
    "Create a rectangular 40 mm by 20 mm profile on the XY plane and "
    "extrude it 5 mm."
)

#: Files no Stage 48 run may write to or through. Checked by
#: :func:`preflight` and by :func:`run`, because "we would never" is not a
#: guarantee and a clobbered baseline cannot be regenerated.
PROTECTED_BASELINE_DIRECTORIES: Tuple[str, ...] = (
    "stage40-v1-vs-operation-plan",
    "stage43-structured-output",
)

#: Where a Stage 48 result belongs. A new directory, never an existing one.
RESULT_DIRECTORY = "docs/evaluation-baselines/stage48-widened-schema"

#: Every protected baseline file, by repository-relative path, with the
#: SHA-256 of its committed content. Measured from the files themselves at
#: the commit that added this module, and checked by :func:`preflight`.
#:
#: These runs cost real money, cannot be regenerated -- Stage 43's model and
#: schema combination is pinned and Stage 40's was recorded before a
#: provider limit changed -- and are the only real-model evidence this
#: branch has. A digest is cheaper than trusting that nothing overwrote one.
BASELINE_DIGESTS: Mapping[str, str] = {
    "docs/evaluation-baselines/stage40-v1-vs-operation-plan/"
    "comparison-run.json":
        "bf72e79873e249bbf268ba165d648a1f4b42ce8364b9d37231d38c0d214929de",
    "docs/evaluation-baselines/stage40-v1-vs-operation-plan/"
    "failure-diagnosis.json":
        "b7a0f44674e72ad5e99343bb297f3a0267bc752432cf5d791c2310b801f6a8d7",
    "docs/evaluation-baselines/stage40-v1-vs-operation-plan/"
    "comparison-report.txt":
        "0ea838b1232d224b7cf049d250af230b3f5391be8a9b926b4bd3cafed6f22cb9",
    "docs/evaluation-baselines/stage40-v1-vs-operation-plan/"
    "failure-diagnosis.txt":
        "fefa891c1d2f67f44a0fb8632c1b512295e1b0f5dca1a0a4d1a336f56ef19211",
    "docs/evaluation-baselines/stage40-v1-vs-operation-plan/README.md":
        "72c882ef93952ab2c43c3d9f02946ec60d294e03ff2a078f408751df94be5635",
    "docs/evaluation-baselines/stage43-structured-output/"
    "stage43-structured-run.json":
        "9ee1d25283c54c561243ade81cf6486f91b1cfdd2f0b8fd92cb7dbc870d8ccfc",
    "docs/evaluation-baselines/stage43-structured-output/README.md":
        "18c4305422b9c13408cc5e852ce380797670e05d061ee44e221768652354dfa9",
}

# --- the new categories this stage adds, all of them explicit --------------

#: The geometry is right and the required operations are present, but the
#: edge selector is not the one the request named. Reachable only on the
#: cases whose expectation carries a
#: :class:`~cad_experimental.stage48_corpus.SelectorExpectation`, and it
#: exists because a top rim and a bottom rim chamfer to the same volume.
WRONG_SELECTOR = "WRONG_SELECTOR"

#: The model produced a plan where the correct answer was to ask. Kept apart
#: from ``WRONGLY_ANSWERED`` (which is answering where a refusal was due)
#: because inventing a missing dimension is a different failure from
#: answering an impossible request.
INVENTED_MISSING_VALUE = "INVENTED_MISSING_VALUE"

#: The model asked, and asking was the correct answer. A **success**, and
#: deliberately not ``OK``: that means "built the part that was asked for",
#: and folding a correct question into it would make the category counts
#: report a part where none was produced.
CORRECT_CLARIFICATION = "CORRECT_CLARIFICATION"

#: Stage 48's own categories, on top of Stage 40's imported taxonomy. Both
#: are failures; neither can be reached by a Stage 40 or Stage 43 record, so
#: no existing category changed meaning to make room for them.
STAGE48_CATEGORIES: Tuple[str, ...] = (
    WRONG_SELECTOR, INVENTED_MISSING_VALUE, CORRECT_CLARIFICATION,
)

#: Stage 40's successes plus this stage's one. The imported tuple is left
#: exactly as it is -- a Stage 40 or Stage 43 record can never carry
#: ``CORRECT_CLARIFICATION``, so widening theirs would change what their
#: recorded categories mean.
STAGE48_SUCCESS_CATEGORIES: Tuple[str, ...] = (
    SUCCESS_CATEGORIES + (CORRECT_CLARIFICATION,)
)

#: Which build path ran is a **field**, not a category: a plan the executor
#: built correctly is ``OK``, exactly like one built through a V1 document.
#: It is recorded because Stage 43's runner could not build such a plan at
#: all, so the count is the evidence that this run measured something Stage
#: 43 could not.


# --- scoring, stated declaratively so it can be fingerprinted --------------

#: Every metric this stage reports, and what it means. This table **is** the
#: scoring definition: :func:`scoring_fingerprint` hashes it, so a changed
#: meaning changes the fingerprint and a result recorded under the old one
#: can never be silently compared with a result recorded under the new.
#:
#: The first eight are Stage 40's, word for word where the wording was
#: already exact. The last three are new and are marked as new.
SCORING_RULES: Mapping[str, str] = {
    "model_output_valid": (
        "the provider returned an answer at all; false only for a provider "
        "error, which is unmeasured rather than incorrect"
    ),
    "structure_valid": (
        "the answer parsed into the representation's own structure"
    ),
    "cad_valid": (
        "the plan's rules accepted it and, where a V1 document was produced, "
        "the authoritative validator accepted that too"
    ),
    "build_success": (
        "the CAD kernel produced a solid, by either build path"
    ),
    "render_success": (
        "a RenderModel came out of the build. Taken over DOCUMENT-PATH "
        "attempts only: a graph-executed plan has no V1 document and so no "
        "RenderModel by design, and counting those as failures would report "
        "a capability as a defect"
    ),
    "semantically_correct": (
        "the answer is the part that was asked for: closed-form volume "
        "within a relative tolerance, bounding box within an absolute one, "
        "the expected solid count, the required operation types present, "
        "and the required edge selector where the request names one"
    ),
    "correct_unsupported": (
        "over the cases whose correct answer is a refusal only: the model "
        "refused with the word the expectation accepts. A case whose "
        "correct answer is a QUESTION is not in this denominator -- see "
        "correct_clarification"
    ),
    "correct_clarification": (
        "NEW IN STAGE 48: over the cases where a required value is absent "
        "from the request and has no default, the model asked rather than "
        "inventing one"
    ),
    "provider_errors": (
        "attempts the provider did not answer; excluded from every rate's "
        "denominator, never counted as a wrong answer"
    ),
    "correct_valid_unexecutable": (
        "NEW IN STAGE 48 (Stage 40 had the category, not the rate): over "
        "the cases whose correct answer is a valid plan this engine cannot "
        "build, the model produced one carrying the required operations"
    ),
    "selector_correct": (
        "NEW IN STAGE 48: over the cases that name a kind of edge, the "
        "answer's selector mode and position are ones the request admits. "
        "Reported separately because a top and a bottom rim chamfer to the "
        "same volume, so geometry alone cannot score it"
    ),
    "executed_by_graph": (
        "NEW IN STAGE 48: attempts built by the graph-driven executor "
        "rather than through a V1 document, because their selector is "
        "richer than a V1 document can carry. A count, not a rate, and not "
        "a quality signal"
    ),
}

#: The tolerances, pinned into the fingerprint alongside the rules so that
#: loosening one is as visible as rewriting a definition.
SCORING_TOLERANCES: Mapping[str, float] = {
    "volume_rtol": VOLUME_RTOL,
    "bounding_box_atol": BOUNDING_BOX_ATOL,
}


def scoring_fingerprint() -> str:
    """A hash of the metric definitions, the tolerances and the taxonomy."""
    payload = json.dumps(
        {
            "version": STAGE48_VERSION,
            "rules": dict(SCORING_RULES),
            "tolerances": dict(SCORING_TOLERANCES),
            "success_categories": list(STAGE48_SUCCESS_CATEGORIES),
            "stage48_categories": list(STAGE48_CATEGORIES),
            "accepted_refusals": {
                key: list(value) for key, value in ACCEPTED_REFUSALS.items()
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- schemas ----------------------------------------------------------------


def plan_schema_for(choice: str = DEFAULT_PLAN_SCHEMA) -> Dict[str, Any]:
    """The plan schema this run sends, by name.

    Passed through :func:`sanitise_schema` for the same reason Stage 43 does
    it: on a compliant schema the call is an identity, and :func:`preflight`
    asserts that it is, so a regression that reintroduced a rejected keyword
    cannot reach the API disguised as an unrelated failure.
    """
    if choice not in PLAN_SCHEMAS:
        raise KeyError(
            f"unknown plan schema {choice!r}; available: "
            f"{', '.join(sorted(PLAN_SCHEMAS))}"
        )
    return sanitise_schema(PLAN_SCHEMAS[choice]())


def v1_schema() -> Dict[str, Any]:
    """The V1 schema, narrowed to the subset the API's compiler accepts.

    Stage 43's, unchanged: the legacy group's V1 arm must be the V1 arm.
    """
    from cad_ai.specification import response_schema

    return sanitise_schema(response_schema())


def schema_for(
    representation: str, plan_choice: str = DEFAULT_PLAN_SCHEMA,
) -> Dict[str, Any]:
    """The schema one arm is constrained by."""
    if representation == V1:
        return v1_schema()
    if representation == PLAN:
        return plan_schema_for(plan_choice)
    raise ValueError(f"unknown representation {representation!r}")


def _system_prompts() -> Dict[str, str]:
    """The exact system prompt each arm sends, keyed by arm."""
    from cad_ai.prompt import system_prompt as v1_prompt

    from .prompt import system_prompt as plan_prompt

    return {V1: v1_prompt(), PLAN: plan_prompt()}


# --- the offline preflight --------------------------------------------------


def _schema_verdict(schema: Mapping[str, Any]) -> Dict[str, Any]:
    """One schema's facts and every measured limit it is judged against."""
    facts = schema_facts(schema)
    violations: List[str] = []
    if facts["optional_properties"] > OPTIONAL_PROPERTY_LIMIT:
        violations.append(
            f"{facts['optional_properties']} optional properties against a "
            f"limit of {OPTIONAL_PROPERTY_LIMIT}"
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
        violations.append("an object does not set additionalProperties: false")
    if facts["uses_one_of"]:
        violations.append("oneOf is used; the API accepts only anyOf")
    if any(value not in (0, 1) for value in facts["min_items_values"]):
        violations.append(
            f"minItems values {facts['min_items_values']} -- only 0 or 1 is "
            "accepted"
        )
    branches = len(
        (schema.get("properties", {}).get("operations", {})
         .get("items", {}).get("anyOf", []))
    )
    return {
        "facts": facts,
        "branches": branches,
        "violations": tuple(violations),
        "compilable_offline": not violations,
    }


def _sanitising_is_identity(choice: str) -> bool:
    """Whether the plan schema needs sanitising at all.

    It should not: the plan schema has been clean since Stage 41. If this is
    ever false, a rejected keyword has come back and the preflight says so
    rather than letting the strip hide it.
    """
    raw = PLAN_SCHEMAS[choice]()
    return sanitise_schema(raw) == raw


def _group_check() -> Dict[str, Any]:
    """That the two result groups are what the design says they are.

    Three properties, all decidable offline:

    * every legacy case's text is **identical** to the frozen Stage 40 case
      of the same id -- the definition of "legitimately comparable";
    * every legacy case names a V1 expectation and every capability case
      does not, so no capability case can leak into a V1 rate;
    * the capability group actually reaches the operations Stage 43 could
      not, rather than merely being longer.
    """
    from .comparison_corpus import CASES_BY_ID as FROZEN

    drifted = [
        item.identifier for item in cases_in(LEGACY)
        if item.text != FROZEN[item.identifier].text
        or item.expect_plan != FROZEN[item.identifier].expect_plan
        or item.expect_v1 != FROZEN[item.identifier].expect_v1
    ]
    legacy_without_v1 = [
        item.identifier for item in cases_in(LEGACY)
        if item.expect_v1 is None
    ]
    capability_with_v1 = [
        item.identifier for item in cases_in(CAPABILITY)
        if item.expect_v1 is not None
    ]
    reached: set = set()
    for item in cases_in(CAPABILITY):
        reached.update(item.required_plan_operations)
        if item.selector is not None:
            reached.update(item.selector.select)
    beyond_stage43 = sorted(
        reached & {"sketch", "extrude", "revolve", "pattern",
                   "straight", "circular"}
    )
    return {
        "legacy_cases": len(cases_in(LEGACY)),
        "capability_cases": len(cases_in(CAPABILITY)),
        "legacy_text_drifted_from_stage_40": drifted,
        "legacy_missing_v1_expectation": legacy_without_v1,
        "capability_carrying_v1_expectation": capability_with_v1,
        "capabilities_beyond_stage_43": beyond_stage43,
        "correct": not (
            drifted or legacy_without_v1 or capability_with_v1
        ) and len(beyond_stage43) >= 4,
    }


def _baseline_check(repository_root: Optional[str] = None) -> Dict[str, Any]:
    """That the Stage 40 and Stage 43 baselines are present and untouched.

    Their content hashes are recorded at :data:`BASELINE_DIGESTS`, measured
    from the committed files. A run that would write into either directory,
    or that finds either changed, is refused: a baseline that cost real
    money and cannot be regenerated is not something to discover the loss of
    afterwards.
    """
    root = repository_root or _repository_root()
    results: Dict[str, Any] = {}
    intact = True
    for relative, expected in sorted(BASELINE_DIGESTS.items()):
        path = os.path.join(root, relative)
        if not os.path.exists(path):
            results[relative] = {"present": False, "matches": False}
            intact = False
            continue
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        matches = digest == expected
        results[relative] = {
            "present": True, "matches": matches, "digest": digest,
        }
        intact = intact and matches
    return {
        "root": root,
        "files": results,
        "protected_directories": list(PROTECTED_BASELINE_DIRECTORIES),
        "intact": intact,
    }


def _repository_root() -> str:
    """The repository root, from this file's own location."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def writes_into_a_baseline(path: str) -> bool:
    """Whether ``path`` would land inside a protected baseline directory."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    return any(name in parts for name in PROTECTED_BASELINE_DIRECTORIES)


def check_reference_geometry(
    cache_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Build every buildable case's reference plan and check the closed form.

    **Free, deterministic and model-less.** It proves three things a paid
    run would otherwise discover expensively: that each expectation is
    reachable at all, that the closed form agrees with the kernel, and that
    a case whose correct answer is an unexecutable plan really is refused by
    the adapter rather than quietly built.

    A reference plan is never sent to a model. See the corpus module.
    """
    import tempfile

    from .parser import parse_plan
    from .validation import validate_plan

    service = CadApplicationService.local(cache_root or tempfile.mkdtemp())
    rows: List[Dict[str, Any]] = []
    for item in CASES:
        if item.reference_plan is None:
            continue
        row: Dict[str, Any] = {"case": item.identifier, "ok": False}
        plan = parse_plan(dict(item.reference_plan))
        verdict = validate_plan(plan)
        row["plan_valid"] = bool(verdict.valid)
        if not verdict.valid:
            row["detail"] = "; ".join(
                f"{p.code} {p.message}" for p in verdict.problems[:3]
            )
            rows.append(row)
            continue
        outcome = build_plan(service, plan)
        row["executed_by_graph"] = bool(outcome.executed)
        if not outcome.built:
            row["detail"] = outcome.error or "the build failed"
            rows.append(row)
            continue
        volume, box, solids, _ = _measure(outcome)
        row["volume_mm3"] = volume
        matched, why = _geometry_matches(item.geometry, volume, box, solids)
        row["ok"] = matched
        row["expected_volume_mm3"] = (
            item.geometry.volume_mm3 if item.geometry else None
        )
        if not matched:
            row["detail"] = why
        rows.append(row)
    return {
        "checked": len(rows),
        "failed": [row["case"] for row in rows if not row["ok"]],
        "executed_by_graph": [
            row["case"] for row in rows if row.get("executed_by_graph")
        ],
        "rows": rows,
        "correct": all(row["ok"] for row in rows),
    }


def check_invalid_plans() -> Dict[str, Any]:
    """That every deliberately broken plan is refused, by the right layer.

    The brief's "bad references, incompatible references, ambiguous
    selector, unsupported geometry semantics" -- asked of the rules rather
    than of a model, because no wording makes a model emit a dangling
    reference on demand. Model-less, and contributes to no score.
    """
    from .parser import PlanParseError, parse_plan
    from .validation import validate_plan

    rows: List[Dict[str, Any]] = []
    for broken in INVALID_PLANS:
        row: Dict[str, Any] = {
            "id": broken.identifier, "kind": broken.kind,
            "expected": broken.refused_by, "ok": False,
        }
        try:
            plan = parse_plan(dict(broken.payload))
        except PlanParseError as error:
            row["actual"] = REFUSED_BY_PARSER
            row["ok"] = broken.refused_by == REFUSED_BY_PARSER
            row["detail"] = str(error)
            rows.append(row)
            continue
        verdict = validate_plan(plan)
        row["actual"] = (
            REFUSED_BY_VALIDATOR if not verdict.valid else "accepted"
        )
        codes = tuple(problem.code for problem in verdict.problems)
        row["codes"] = list(codes)
        row["expected_codes"] = list(broken.codes)
        row["ok"] = (
            broken.refused_by == REFUSED_BY_VALIDATOR
            and set(broken.codes) == set(codes)
        )
        rows.append(row)
    return {
        "checked": len(rows),
        "failed": [row["id"] for row in rows if not row["ok"]],
        "rows": rows,
        "correct": all(row["ok"] for row in rows),
    }


def preflight(
    plan_choice: str = DEFAULT_PLAN_SCHEMA,
    *,
    geometry: bool = True,
    repository_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything that can be checked without a model call. Free.

    Answers, in order: do both schemas satisfy every measured provider
    limit; are the two result groups what the design claims; is every
    buildable expectation reachable and its closed form right; is every
    deliberately broken plan refused by the right layer; and are the Stage
    40 and Stage 43 baselines present and byte-identical.

    ``geometry=False`` skips the kernel builds, for a caller that only wants
    the schema and metadata answers quickly.
    """
    arms = {
        V1: _schema_verdict(v1_schema()),
        PLAN: _schema_verdict(plan_schema_for(plan_choice)),
    }
    groups = _group_check()
    baselines = _baseline_check(repository_root)
    reference = (
        check_reference_geometry() if geometry
        else {"checked": 0, "failed": [], "rows": [], "correct": None,
              "executed_by_graph": []}
    )
    invalid = check_invalid_plans()
    ready = (
        all(arm["compilable_offline"] for arm in arms.values())
        and groups["correct"]
        and invalid["correct"]
        and baselines["intact"]
        and (reference["correct"] is not False)
    )
    return {
        "stage": STAGE,
        "kind": "stage48-preflight",
        "version": STAGE48_VERSION,
        "model": MODEL,
        "plan_schema_choice": plan_choice,
        "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
        "sanitising_is_identity_for_the_plan": _sanitising_is_identity(
            plan_choice
        ),
        "arms": arms,
        "groups": groups,
        "reference_geometry": reference,
        "invalid_plans": invalid,
        "baselines": baselines,
        "fingerprints": fingerprints(plan_choice),
        "result_directory": RESULT_DIRECTORY,
        "ready": ready,
    }


def fingerprints(plan_choice: str = DEFAULT_PLAN_SCHEMA) -> Dict[str, Any]:
    """Every identity a Stage 48 result is reproducible from."""
    from cad_ai.prompt import PROMPT_VERSION as V1_PROMPT_VERSION
    from cad_ai.prompt import prompt_fingerprint as v1_prompt_fingerprint

    from .prompt import PROMPT_VERSION as PLAN_PROMPT_VERSION
    from .prompt import prompt_fingerprint as plan_prompt_fingerprint
    from .prompt import system_prompt as plan_prompt

    from cad_ai.prompt import system_prompt as v1_prompt

    return {
        "evaluation_version": STAGE48_VERSION,
        "corpus_version": STAGE48_CORPUS_VERSION,
        "corpus": corpus_fingerprint(),
        "scoring": scoring_fingerprint(),
        "model": {
            "model": MODEL,
            "max_output_tokens": SHARED_MAX_OUTPUT_TOKENS,
            "timeout_seconds": SHARED_TIMEOUT_SECONDS,
            "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
            "plan_schema_choice": plan_choice,
        },
        "prompt": {
            V1: {
                "version": V1_PROMPT_VERSION,
                "fingerprint": v1_prompt_fingerprint(),
                "characters": len(v1_prompt()),
            },
            PLAN: {
                "version": PLAN_PROMPT_VERSION,
                "fingerprint": plan_prompt_fingerprint(),
                "characters": len(plan_prompt()),
            },
        },
        "schema": {
            V1: schema_facts(v1_schema())["fingerprint"],
            PLAN: schema_facts(plan_schema_for(plan_choice))["fingerprint"],
        },
    }


# --- one attempt's record ---------------------------------------------------


@dataclass
class Stage48Record:
    """One model call, all the way down. The unit of measurement.

    Stage 40's :class:`~cad_experimental.representation_comparison.AttemptRecord`
    with three fields added and none removed or repurposed:
    :attr:`executed_by_graph`, :attr:`selector` and
    :attr:`selector_correct`. Every inherited field means what it meant.
    """

    case_id: str
    group: str
    category: str
    representation: str
    attempt: int

    raw_text: Optional[str] = None
    latency_seconds: Optional[float] = None
    usage: Mapping[str, int] = field(default_factory=dict)
    declared_outcome: Optional[str] = None

    model_output_valid: bool = False
    structure_valid: bool = False
    cad_valid: bool = False
    build_success: bool = False
    render_success: bool = False
    semantically_correct: bool = False

    #: Built by the graph-driven executor rather than through a V1 document.
    executed_by_graph: bool = False

    #: The edge selector the answer used, as plain data, where it used one.
    selector: Optional[Mapping[str, Any]] = None

    #: ``None`` where the case names no selector requirement.
    selector_correct: Optional[bool] = None

    category_code: str = PROVIDER_ERROR
    detail: Optional[str] = None
    operations: Tuple[str, ...] = ()
    volume_mm3: Optional[float] = None
    bounding_box: Optional[Mapping[str, float]] = None
    solid_count: Optional[int] = None
    triangle_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "category": self.category,
            "representation": self.representation,
            "attempt": self.attempt,
            "raw_text": self.raw_text,
            "latency_seconds": self.latency_seconds,
            "usage": dict(self.usage),
            "declared_outcome": self.declared_outcome,
            "model_output_valid": self.model_output_valid,
            "structure_valid": self.structure_valid,
            "cad_valid": self.cad_valid,
            "build_success": self.build_success,
            "render_success": self.render_success,
            "semantically_correct": self.semantically_correct,
            "executed_by_graph": self.executed_by_graph,
            "selector": dict(self.selector) if self.selector else None,
            "selector_correct": self.selector_correct,
            "category_code": self.category_code,
            "detail": self.detail,
            "operations": list(self.operations),
            "volume_mm3": self.volume_mm3,
            "bounding_box": (
                dict(self.bounding_box)
                if self.bounding_box is not None else None
            ),
            "solid_count": self.solid_count,
            "triangle_count": self.triangle_count,
        }


# --- geometry, identical for both arms and both build paths ----------------


def _geometry_matches(
    expected: Any, volume: Optional[float],
    box: Optional[Mapping[str, float]], solids: Optional[int],
) -> Tuple[bool, str]:
    """Whether built geometry is the part that was asked for.

    Stage 40's rule and Stage 40's tolerances: relative on the volume,
    absolute on the bounding box, no exact float comparison anywhere.
    """
    if expected is None:
        return False, "no geometry was expected for this case"
    if volume is None or box is None:
        return False, "the build reported no geometry"
    if solids is not None and solids != expected.solid_count:
        return False, f"{solids} solids, expected {expected.solid_count}"
    if not math.isclose(volume, expected.volume_mm3, rel_tol=VOLUME_RTOL):
        return (
            False,
            f"volume {volume!r}, expected {expected.volume_mm3!r} "
            f"(rel_tol {VOLUME_RTOL})",
        )
    for axis, want in (
        ("x", expected.size_x), ("y", expected.size_y), ("z", expected.size_z)
    ):
        got = box.get(axis)
        if got is None or not math.isclose(
            float(got), want, rel_tol=VOLUME_RTOL, abs_tol=BOUNDING_BOX_ATOL
        ):
            return False, f"bounding box {axis}={got!r}, expected {want!r}"
    return True, ""


def _measure(
    outcome: Any,
) -> Tuple[
    Optional[float], Optional[Dict[str, float]], Optional[int], Optional[int]
]:
    """Volume, bounding box, solid count and triangles, from either path.

    The two build paths report the same facts under different names -- the
    service through its geometry artifact, the executor through the
    backend's :class:`~cad_experimental.cad_backend.Measurement`. Reading
    both here keeps every downstream comparison identical, which is the only
    way a graph-executed part and a document-built part can be scored by one
    rule.
    """
    if outcome.execution is not None:
        bodies = outcome.execution.bodies
        if len(bodies) != 1:
            return None, None, len(bodies) or None, None
        measurement = bodies[0].measurement
        if measurement is None:
            return None, None, None, None
        size = measurement.size
        return (
            float(measurement.volume),
            {"x": float(size[0]), "y": float(size[1]), "z": float(size[2])},
            int(measurement.solid_count),
            None,
        )
    if outcome.outcome is None:
        return None, None, None, None
    geometry = outcome.outcome.artifact("geometry")
    details = dict(geometry.details) if geometry is not None else {}
    box = (details.get("bounding_box") or {}).get("size")
    render = outcome.outcome.render_model
    counter = getattr(render, "triangle_count", None) if render else None
    return (
        details.get("volume_mm3"),
        dict(box) if isinstance(box, Mapping) else None,
        details.get("solid_count"),
        int(counter()) if callable(counter) else None,
    )


# --- selector scoring -------------------------------------------------------


def _selector_of(plan: Any, operation_type: str) -> Optional[Dict[str, Any]]:
    """The edge selector the answer used on ``operation_type``, if any."""
    for operation in getattr(plan, "operations", ()) or ():
        if getattr(operation, "TYPE", None) != operation_type:
            continue
        edges = getattr(operation, "edges", None)
        if edges is None:
            continue
        return {
            "select": getattr(edges, "select", None),
            "axis": getattr(edges, "axis", None),
            "position": getattr(edges, "position", None),
        }
    return None


def _score_selector(
    item: CapabilityCase, plan: Any,
) -> Tuple[Optional[bool], Optional[Dict[str, Any]], str]:
    """Whether the answer named the kind of edge the request named.

    ``(None, ...)`` where the case pins no selector -- most of them do not,
    and requiring one everywhere would score a legitimate choice as wrong.
    """
    expectation = item.selector
    if expectation is None:
        return None, None, ""
    found = _selector_of(plan, expectation.operation_type)
    if found is None:
        return False, None, (
            f"the request names a kind of edge but the answer's "
            f"{expectation.operation_type} carries no selector"
        )
    if found["select"] not in expectation.select:
        return False, found, (
            f"selector {found['select']!r}, expected one of "
            f"{list(expectation.select)}"
        )
    if expectation.position is not None and (
        found["position"] != expectation.position
    ):
        return False, found, (
            f"selector position {found['position']!r}, expected "
            f"{expectation.position!r}"
        )
    return True, found, ""


# --- arm A: canonical V1 JSON, legacy group only ---------------------------


def run_v1_attempt(
    model: Any,
    service: CadApplicationService,
    item: CapabilityCase,
    attempt: int,
) -> Stage48Record:
    """One V1-document attempt, through the production AI layer.

    Only ever called for a legacy case: a capability case carries no V1
    expectation and :func:`run` refuses to invent one.
    """
    from cad_core.application_service import BuildDocumentRequest
    from cad_ai.generation import GenerationOutcome, TextToCadService

    if item.expect_v1 is None:
        raise ValueError(
            f"{item.identifier} has no V1 expectation; the V1 arm answers "
            "legacy cases only, and inventing one would put a capability "
            "case into a V1 rate"
        )

    record = Stage48Record(
        case_id=item.identifier, group=item.group, category=item.category,
        representation=V1, attempt=attempt,
    )
    planner = TextToCadService(
        model, service, max_output_tokens=SHARED_MAX_OUTPUT_TOKENS
    )
    result = planner.generate_cad_from_text(item.text)

    record.latency_seconds = getattr(model, "last_latency_seconds", None)
    record.raw_text = getattr(model, "last_text", None)
    record.usage = dict(result.metadata.usage)
    record.declared_outcome = result.outcome.value

    if result.outcome is GenerationOutcome.MODEL_ERROR:
        record.category_code = PROVIDER_ERROR
        record.detail = result.message
        return record

    record.model_output_valid = True

    if result.outcome is GenerationOutcome.INVALID_MODEL_OUTPUT:
        if result.rule_codes:
            record.structure_valid = True
            record.category_code = V1_VALIDATION_REJECTED
        else:
            record.category_code = PARSER_REJECTED
        record.detail = "; ".join(result.issues[:4]) or result.message
        return record

    if result.outcome in (
        GenerationOutcome.UNSUPPORTED, GenerationOutcome.NEEDS_CLARIFICATION
    ):
        record.structure_valid = True
        _score_refusal(item, record, item.expect_v1, result.outcome.value)
        if record.category_code == WRONGLY_REFUSED and not record.detail:
            record.detail = "; ".join(
                (result.questions or result.issues)[:3]
            ) or result.message
        return record

    document = result.candidate_document
    record.structure_valid = True
    record.cad_valid = True
    record.operations = tuple(
        str(f.get("type"))
        for f in ((document or {}).get("features") or [])
        if isinstance(f, Mapping)
    )

    if item.expect_v1 != EXPECT_BUILD:
        _score_wrongly_answered(item, record, item.expect_v1)
        return record

    outcome = service.build_document(
        BuildDocumentRequest.for_outputs(dict(document or {}), "geometry",
                                         "render")
    )
    record.build_success = bool(outcome.succeeded)
    if not outcome.succeeded:
        error = outcome.error
        record.category_code = BUILD_FAILED
        record.detail = (
            error.message if error is not None else "the build failed"
        )
        return record
    geometry = outcome.artifact("geometry")
    details = dict(geometry.details) if geometry is not None else {}
    record.volume_mm3 = details.get("volume_mm3")
    record.solid_count = details.get("solid_count")
    box = (details.get("bounding_box") or {}).get("size")
    record.bounding_box = dict(box) if isinstance(box, Mapping) else None
    render = outcome.render_model
    if render is None:
        record.category_code = RENDERMODEL_FAILED
        record.detail = "the build produced no render model"
        return record
    record.render_success = True
    counter = getattr(render, "triangle_count", None)
    record.triangle_count = int(counter()) if callable(counter) else None

    _score_geometry(item, record, item.required_v1_features)
    return record


# --- arm B: the operation plan ---------------------------------------------


def run_plan_attempt(
    model: Any,
    service: CadApplicationService,
    item: CapabilityCase,
    attempt: int,
) -> Stage48Record:
    """One operation-plan attempt, through the experimental layer.

    The one runner for both groups. It builds through
    :func:`cad_experimental.build.build_plan`, which asks explicitly whether
    the plan's selectors fit a V1 document and sends it to the executor when
    they do not -- the difference from Stage 40's runner, and the reason a
    semantic selector is scored on the part it builds rather than on the
    document format it cannot fit into.
    """
    from .config import ExperimentalConfig
    from .generation import OperationPlanService, PlanOutcome

    record = Stage48Record(
        case_id=item.identifier, group=item.group, category=item.category,
        representation=PLAN, attempt=attempt,
    )
    planner = OperationPlanService(
        model, ExperimentalConfig(model=MODEL, provider="anthropic")
    )
    result = planner.generate(item.text)

    record.latency_seconds = getattr(model, "last_latency_seconds", None)
    record.raw_text = result.raw_text or getattr(model, "last_text", None)
    record.usage = dict(result.metadata.usage)
    record.declared_outcome = result.outcome.value

    if result.outcome is PlanOutcome.MODEL_ERROR:
        record.category_code = PROVIDER_ERROR
        record.detail = result.error
        return record

    record.model_output_valid = True

    if result.outcome is PlanOutcome.INVALID_MODEL_OUTPUT:
        if result.plan_validation is not None:
            record.structure_valid = True
            record.category_code = PLAN_VALIDATION_REJECTED
            record.detail = "; ".join(
                f"{p.code} {p.message}"
                for p in result.plan_validation.problems[:4]
            )
        else:
            record.category_code = PARSER_REJECTED
            record.detail = result.error
        return record

    if result.outcome in (
        PlanOutcome.UNSUPPORTED, PlanOutcome.NEEDS_CLARIFICATION
    ):
        record.structure_valid = True
        _score_refusal(item, record, item.expect_plan, result.outcome.value)
        if record.category_code == WRONGLY_REFUSED and not record.detail:
            record.detail = "; ".join(
                (result.plan.questions if result.plan else ())[:3]
            ) or result.error
        return record

    plan = result.plan
    record.structure_valid = True
    record.cad_valid = True
    record.operations = tuple(
        op.TYPE for op in (plan.operations if plan is not None else ())
    )

    if item.expect_plan in ACCEPTED_REFUSALS:
        _score_wrongly_answered(item, record, item.expect_plan)
        return record

    outcome = build_plan(service, plan)
    record.executed_by_graph = bool(outcome.executed)

    if outcome.execution_unsupported:
        record.detail = (
            f"cannot execute {', '.join(outcome.unsupported_types)}"
        )
        if item.expect_plan == EXPECT_VALID_UNEXECUTABLE:
            missing = [
                kind for kind in item.required_plan_operations
                if kind not in set(record.operations)
            ]
            if missing:
                record.category_code = SEMANTICALLY_INCORRECT
                record.detail = (
                    f"the request named {', '.join(missing)}; got "
                    f"{', '.join(record.operations) or 'nothing'}"
                )
                return record
            record.semantically_correct = True
            record.category_code = CORRECT_VALID_UNEXECUTABLE
            return record
        record.category_code = EXECUTION_UNSUPPORTED
        return record

    if item.expect_plan == EXPECT_VALID_UNEXECUTABLE:
        record.category_code = SEMANTICALLY_INCORRECT
        record.detail = (
            f"produced buildable {', '.join(record.operations)} where the "
            "request asked for a sketch-based feature chain"
        )
        return record

    record.build_success = bool(outcome.built)
    if not outcome.built:
        record.category_code = BUILD_FAILED
        record.detail = outcome.error or "the build failed"
        return record

    volume, box, solids, triangles = _measure(outcome)
    record.volume_mm3 = volume
    record.bounding_box = box
    record.solid_count = solids
    record.triangle_count = triangles
    # A graph-executed plan has no V1 document and therefore no RenderModel:
    # that is a fact about the path, not a failure, and the rate's own
    # definition says so.
    record.render_success = (not record.executed_by_graph) and (
        triangles is not None
    )
    if not record.executed_by_graph and not record.render_success:
        record.category_code = RENDERMODEL_FAILED
        record.detail = "the build produced no render model"
        return record

    correct, found, why = _score_selector(item, plan)
    record.selector = found
    record.selector_correct = correct
    _score_geometry(item, record, item.required_plan_operations)
    if record.category_code == OK and correct is False:
        record.semantically_correct = False
        record.category_code = WRONG_SELECTOR
        record.detail = why
    return record


# --- shared scoring ---------------------------------------------------------


def _score_refusal(
    item: CapabilityCase,
    record: Stage48Record,
    expectation: Optional[str],
    declared: str,
) -> None:
    """Score an answer that declined, against what this case expects.

    The corpus owns which refusal words a case accepts
    (:data:`~cad_experimental.stage48_corpus.ACCEPTED_REFUSALS`), so a
    scoring change cannot be smuggled in here.
    """
    accepted = ACCEPTED_REFUSALS.get(expectation or "", ())
    if not accepted:
        record.category_code = WRONGLY_REFUSED
        record.detail = (
            f"declined with {declared!r} where {expectation} was required"
        )
        return
    if declared in accepted:
        record.semantically_correct = True
        record.category_code = (
            CORRECT_CLARIFICATION if expectation == EXPECT_CLARIFICATION
            else CORRECT_UNSUPPORTED
        )
        return
    record.category_code = WRONGLY_REFUSED
    record.detail = (
        f"declined with {declared!r}; this case accepts "
        f"{list(accepted)}"
    )


def _score_wrongly_answered(
    item: CapabilityCase, record: Stage48Record, expectation: Optional[str],
) -> None:
    """Score an answer that produced a part where none was correct."""
    produced = ", ".join(record.operations) or "a document"
    if expectation == EXPECT_CLARIFICATION:
        record.category_code = INVENTED_MISSING_VALUE
        record.detail = (
            f"produced {produced} although a required value is absent from "
            "the request and has no default"
        )
        return
    record.category_code = WRONGLY_ANSWERED
    record.detail = f"produced {produced} where {expectation} was required"


def _score_geometry(
    item: CapabilityCase, record: Stage48Record, required: Sequence[str],
) -> None:
    """Decide semantic correctness. One implementation, both arms.

    Geometry first, because the question is about the part rather than
    about the JSON; then the operation types, but only where the request
    pins them.
    """
    matched, why = _geometry_matches(
        item.geometry, record.volume_mm3, record.bounding_box,
        record.solid_count,
    )
    if not matched:
        record.category_code = SEMANTICALLY_INCORRECT
        record.detail = why
        return
    missing = [kind for kind in required if kind not in set(record.operations)]
    if missing:
        record.category_code = SEMANTICALLY_INCORRECT
        record.detail = (
            f"geometry is right but the request named {', '.join(missing)}; "
            f"got {', '.join(record.operations) or 'nothing'}"
        )
        return
    record.semantically_correct = True
    record.category_code = OK


# --- summarising ------------------------------------------------------------

#: The flags a rate is taken over, each read by an explicit accessor. Spelled
#: out rather than reached by a computed attribute name, so a test can assert
#: the absence of the pattern outright -- Stage 40's rule, kept.
_STAGE_FLAGS: Mapping[str, Any] = {
    "model_output_valid": lambda r: r.model_output_valid,
    "structure_valid": lambda r: r.structure_valid,
    "cad_valid": lambda r: r.cad_valid,
    "build_success": lambda r: r.build_success,
    "render_success": lambda r: r.render_success,
    "semantically_correct": lambda r: r.semantically_correct,
}


def _rate(records: Sequence[Stage48Record], flag: str) -> Optional[float]:
    """A success rate over attempts the provider actually answered.

    A provider error is *unmeasured*, not incorrect, and stays out of every
    denominator. The project's standing rule, and Stage 40's implementation.
    """
    read = _STAGE_FLAGS[flag]
    answered = [r for r in records if r.category_code != PROVIDER_ERROR]
    if not answered:
        return None
    return sum(1 for r in answered if read(r)) / len(answered)


def _counts(records: Sequence[Stage48Record]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        counts[record.category_code] = counts.get(record.category_code, 0) + 1
    return dict(sorted(counts.items()))


def _arm_summary(
    records: Sequence[Stage48Record],
    representation: str,
    cases: Sequence[CapabilityCase],
) -> Dict[str, Any]:
    """Every headline rate for one arm of one group."""
    mine = [r for r in records if r.representation == representation]
    answered = [r for r in mine if r.category_code != PROVIDER_ERROR]
    latencies = [
        r.latency_seconds for r in mine if r.latency_seconds is not None
    ]

    def expectation_of(item: CapabilityCase) -> Optional[str]:
        return item.expect_v1 if representation == V1 else item.expect_plan

    refusal_ids = {
        item.identifier for item in cases
        if expectation_of(item) in (EXPECT_UNSUPPORTED, EXPECT_NO_PART)
    }
    unexecutable_ids = {
        item.identifier for item in cases
        if expectation_of(item) == EXPECT_VALID_UNEXECUTABLE
    }
    clarification_ids = {
        item.identifier for item in cases
        if expectation_of(item) == EXPECT_CLARIFICATION
    }
    refusals = [r for r in answered if r.case_id in refusal_ids]
    unexecutable = [r for r in answered if r.case_id in unexecutable_ids]
    clarifications = [
        r for r in answered if r.case_id in clarification_ids
    ]
    selector_scored = [
        r for r in answered if r.selector_correct is not None
    ]
    return {
        "attempts": len(mine),
        "answered": len(answered),
        "provider_errors": len(mine) - len(answered),
        "model_output_valid": _rate(mine, "model_output_valid"),
        "structure_valid": _rate(mine, "structure_valid"),
        "cad_valid": _rate(mine, "cad_valid"),
        "build_success": _rate(mine, "build_success"),
        # Over document-path attempts only -- see this metric's own rule.
        # A graph-executed plan has no document and therefore no
        # RenderModel; scoring that as a failure would turn Stage 47's
        # capability into a defect in the summary.
        "render_success": _rate(
            [r for r in mine if not r.executed_by_graph], "render_success"
        ),
        "render_scored_attempts": len(
            [r for r in mine
             if not r.executed_by_graph
             and r.category_code != PROVIDER_ERROR]
        ),
        "semantically_correct": _rate(mine, "semantically_correct"),
        "correct_unsupported": (
            sum(1 for r in refusals if r.semantically_correct)
            / len(refusals)
        ) if refusals else None,
        "correct_clarification": (
            sum(1 for r in clarifications if r.semantically_correct)
            / len(clarifications)
        ) if clarifications else None,
        "correct_valid_unexecutable": (
            sum(1 for r in unexecutable if r.semantically_correct)
            / len(unexecutable)
        ) if unexecutable else None,
        "selector_correct": (
            sum(1 for r in selector_scored if r.selector_correct)
            / len(selector_scored)
        ) if selector_scored else None,
        "selector_scored_attempts": len(selector_scored),
        "executed_by_graph": sum(1 for r in mine if r.executed_by_graph),
        "mean_latency_seconds": (
            sum(latencies) / len(latencies) if latencies else None
        ),
        "input_tokens": sum(r.usage.get("input_tokens", 0) for r in mine),
        "output_tokens": sum(r.usage.get("output_tokens", 0) for r in mine),
        "categories": _counts(mine),
    }


def summarise(
    records: Sequence[Stage48Record],
    cases: Sequence[CapabilityCase],
) -> Dict[str, Any]:
    """Per group, per arm. **Never one number over both groups.**

    A combined rate would average a V1 score over cases V1 was never asked,
    which is the conflation this stage exists to avoid, so it is not
    computed here and cannot be read out of the result by accident.
    """
    summary: Dict[str, Any] = {}
    for group in GROUPS:
        group_cases = [item for item in cases if item.group == group]
        if not group_cases:
            continue
        ids = {item.identifier for item in group_cases}
        mine = [r for r in records if r.case_id in ids]
        arms = sorted({r.representation for r in mine})
        summary[group] = {
            "cases": len(group_cases),
            "arms": arms,
            **{
                representation: _arm_summary(
                    mine, representation, group_cases
                )
                for representation in arms
            },
        }
    return summary


def per_case(
    records: Sequence[Stage48Record],
    cases: Sequence[CapabilityCase],
) -> Dict[str, Any]:
    """Semantic correctness per case per arm."""
    table: Dict[str, Any] = {}
    for item in cases:
        row: Dict[str, Any] = {
            "text": item.text,
            "group": item.group,
            "category": item.category,
            "expect_plan": item.expect_plan,
            "expect_v1": item.expect_v1,
        }
        mine = [r for r in records if r.case_id == item.identifier]
        for representation in sorted({r.representation for r in mine}):
            arm = [r for r in mine if r.representation == representation]
            answered = [
                r for r in arm if r.category_code != PROVIDER_ERROR
            ]
            row[representation] = {
                "attempts": len(arm),
                "answered": len(answered),
                "correct": sum(
                    1 for r in answered if r.semantically_correct
                ),
                "executed_by_graph": sum(
                    1 for r in arm if r.executed_by_graph
                ),
                "categories": _counts(arm),
                "details": sorted({r.detail for r in arm if r.detail})[:3],
            }
        table[item.identifier] = row
    return table


def per_category(records: Sequence[Stage48Record]) -> Dict[str, Any]:
    """Plan-arm semantic correctness per corpus category.

    The plan arm only: the categories are the brief's capability axes, and
    V1 answers three of nine, so a per-category V1 rate would be mostly
    empty cells read as failures.
    """
    table: Dict[str, Any] = {}
    for category in CATEGORIES:
        mine = [
            r for r in records
            if r.category == category and r.representation == PLAN
        ]
        answered = [r for r in mine if r.category_code != PROVIDER_ERROR]
        if not mine:
            continue
        table[category] = {
            "attempts": len(mine),
            "answered": len(answered),
            "semantically_correct": (
                sum(1 for r in answered if r.semantically_correct)
                / len(answered)
            ) if answered else None,
            "categories": _counts(mine),
        }
    return table


# --- the model wrapper ------------------------------------------------------


class Stage48Model:
    """Times each call, equalises the token ceiling, attaches the schema.

    One object rather than Stage 43's two nested ones, because Stage 48 does
    not have a frozen wrapper to work around. It changes exactly two fields
    of a request -- ``max_output_tokens`` and ``output_schema`` -- and
    nothing else: not the system prompt, not the user text.

    The arm is decided by an **exact** comparison against the two system
    prompts, which are distinct, versioned and fingerprinted. An
    unrecognised prompt raises rather than guesses: attaching one
    representation's grammar to the other's instructions would produce a
    number that looked like a measurement and was not.
    """

    def __init__(
        self, inner: Any, plan_choice: str = DEFAULT_PLAN_SCHEMA,
    ) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "anthropic")
        self._prompts = _system_prompts()
        self._schemas = {
            V1: schema_for(V1, plan_choice),
            PLAN: schema_for(PLAN, plan_choice),
        }
        self.plan_choice = plan_choice
        self.last_latency_seconds: Optional[float] = None
        self.last_text: Optional[str] = None
        #: How many requests each arm was constrained on. Read by the CLI as
        #: a check that both arms really were given a grammar.
        self.attached: Dict[str, int] = {V1: 0, PLAN: 0}

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
        equalised = ModelRequest(
            system=request.system,
            user_text=request.user_text,
            output_schema=(
                self._schemas[arm] if STRUCTURED_OUTPUT_ENABLED else None
            ),
            max_output_tokens=SHARED_MAX_OUTPUT_TOKENS,
        )
        self.last_latency_seconds = None
        self.last_text = None
        started = time.monotonic()
        try:
            response = self._inner.generate(equalised)
        except ProviderError:
            self.last_latency_seconds = time.monotonic() - started
            raise
        self.last_latency_seconds = time.monotonic() - started
        self.last_text = response.text
        return response


class ReferencePlanStub:
    """Serves each case's own reference plan. **NOT a model.**

    The self-check's provider, and nothing else: it exists so the whole run
    loop -- parser, validator, adapter, both build paths, every scoring rule
    and the summary -- can be exercised for free before a paid run, and so a
    change that broke the scoring would be caught by a green self-check
    turning red rather than by a live run producing nonsense.

    It looks each request up by its **exact** case text and hands back the
    developer-written reference plan, or a refusal for a case whose correct
    answer is one. There is no interpretation here and no model of any kind.

    Two guards keep its output from being mistaken for a measurement:
    :attr:`is_local_development` is true on this class and absent from every
    real provider, and :func:`self_check` stamps its result
    ``is_live_model_result: false``. A self-check score is a statement about
    this harness, never about Claude. It is unreachable from ``--live``,
    which builds :func:`real_model` and nothing else.
    """

    name = "stage48-reference-stub (not a model)"

    #: True here and on no real provider, so a caller can tell a plumbing
    #: result from a model result by type rather than by reading a string.
    is_local_development = True

    def __init__(self) -> None:
        self._plans: Dict[str, str] = {}
        self._refusals: Dict[str, str] = {}
        for item in CASES:
            if item.reference_plan is not None:
                self._plans[item.text] = json.dumps(item.reference_plan)
            elif item.expect_plan in ACCEPTED_REFUSALS:
                word = ACCEPTED_REFUSALS[item.expect_plan][0]
                self._refusals[item.text] = json.dumps({
                    "status": word,
                    "summary": "reference refusal",
                    "operations": [],
                    "reason": "the self-check's fixed answer for this case",
                    "questions": ["what size?"],
                })
            else:
                # A valid-unexecutable case: no reference plan is written
                # for one, because building it is the thing that must fail.
                self._refusals[item.text] = json.dumps({
                    "status": "unsupported",
                    "summary": "no reference plan for this case",
                    "operations": [],
                    "reason": "the self-check has no fixture here",
                })
        self.requests: List[ModelRequest] = []
        self.last_latency_seconds: Optional[float] = None
        self.last_text: Optional[str] = None

    @property
    def config(self) -> Any:
        return None

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = self._plans.get(
            request.user_text, self._refusals.get(request.user_text)
        )
        if text is None:
            raise ProviderError(
                f"the self-check stub has no fixture for {request.user_text!r}"
            )
        self.last_latency_seconds = 0.0
        self.last_text = text
        return ModelResponse(
            text=text,
            provider=self.name,
            model="reference-plan-fixture (not a model)",
            structured_output=False,
            stop_reason="self_check_fixture",
            usage={},
        )


def self_check(
    *,
    attempts: int = 1,
    plan_choice: str = DEFAULT_PLAN_SCHEMA,
    cases: Optional[Sequence[CapabilityCase]] = None,
) -> Dict[str, Any]:
    """Run the whole loop against :class:`ReferencePlanStub`. Free.

    Proves the plumbing, never the model. The result is stamped so that no
    reader and no later script can mistake it for one, and the plan arm is
    the only arm exercised: the V1 service would have to be handed V1
    documents, which this stub does not hold and must not be taught to
    invent.
    """
    stub = ReferencePlanStub()
    selected = [
        item for item in (cases if cases is not None else CASES)
        if item.reference_plan is not None
    ]
    data = run(
        live=False,
        attempts=attempts,
        plan_choice=plan_choice,
        cases=selected,
        arms=(PLAN,),
        model_factory=lambda: stub,
    )
    data["kind"] = "stage48-self-check"
    data["is_live_model_result"] = False
    data["source"] = (
        "ReferencePlanStub -- developer-written plans, not a model. This "
        "result says the harness scores a known-correct answer correctly "
        "and says nothing whatever about Claude."
    )
    return data


def real_model() -> Any:
    """The live Anthropic provider, pinned to this evaluation's model."""
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


# --- the live schema-acceptance probe --------------------------------------


def probe_live(plan_choice: str = DEFAULT_PLAN_SCHEMA) -> Dict[str, Any]:
    """Ask the provider to compile each schema. **Three real calls.**

    The question Stage 44 left open and no existing CLI can ask: Stage 43's
    ``--probe-live`` probes ``executable_schema``, which is already known to
    compile. This sends the **widened** plan schema, and sends it twice --
    once on a box, once on a profile the six-type grammar could not express
    -- plus one V1 call so the legacy arm's grammar is verified too.

    It reports whether the request was **accepted**, never whether the
    answer is any good. Correctness is the evaluation's job, and this probe
    must not become a tiny, unfrozen benchmark.
    """
    if not credential_present():
        raise CredentialUnavailable(
            f"{OPERATOR_KEY_VARIABLE} is not set; the schema-acceptance "
            "probe cannot run, and no other credential or provider may be "
            "substituted"
        )
    model = real_model()
    prompts = _system_prompts()
    probes = (
        (V1, PROBE_DESCRIPTION, "box"),
        (PLAN, PROBE_DESCRIPTION, "box"),
        (PLAN, PROBE_PROFILE_DESCRIPTION, "profile"),
    )
    results: List[Dict[str, Any]] = []
    for representation, description, label in probes:
        schema = schema_for(representation, plan_choice)
        started = time.monotonic()
        record: Dict[str, Any] = {
            "representation": representation,
            "probe": label,
            "description": description,
            "schema_fingerprint": schema_facts(schema)["fingerprint"],
        }
        try:
            response = model.generate(
                ModelRequest(
                    system=prompts[representation],
                    user_text=description,
                    output_schema=schema,
                    max_output_tokens=SHARED_MAX_OUTPUT_TOKENS,
                )
            )
        except ProviderError as error:
            record.update({
                "accepted": False,
                "error_kind": getattr(
                    getattr(error, "kind", None), "value",
                    str(getattr(error, "kind", "unknown")),
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
                "starts_with_fence": stripped.startswith("```"),
                "contains_fence": "```" in text,
                "parses_as_json": _parses(stripped),
            })
        results.append(record)
    return {
        "stage": STAGE,
        "kind": "stage48-schema-acceptance-probe",
        "model": MODEL,
        "plan_schema_choice": plan_choice,
        "calls": len(probes),
        "probes": results,
        "all_accepted": all(item.get("accepted") for item in results),
    }


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


# --- the run ----------------------------------------------------------------

#: Attempts per case per arm. Stage 40's, so a per-case rate means the same
#: thing it meant there.
DEFAULT_ATTEMPTS = 5


def run(
    *,
    live: bool,
    attempts: int = DEFAULT_ATTEMPTS,
    plan_choice: str = DEFAULT_PLAN_SCHEMA,
    cases: Optional[Sequence[CapabilityCase]] = None,
    arms: Optional[Sequence[str]] = None,
    cache_root: Optional[str] = None,
    model_factory: Any = None,
    progress: Any = None,
    repository_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the evaluation. ``live`` is required for a real model.

    Refuses before spending anything if the preflight is not ready: a
    schema that fails a measured limit, a group that is not what the design
    says, a reference expectation the kernel disagrees with, or a baseline
    that is missing or changed. Each is a reason the numbers would not mean
    what they claim, which is a different failure from not being able to pay
    for them and is reported as one.
    """
    if not live and model_factory is None:
        raise CredentialUnavailable(
            "a live run must be requested explicitly with --live; a present "
            "credential is never sufficient to begin a paid run"
        )

    check = preflight(plan_choice, repository_root=repository_root)
    if not check["baselines"]["intact"]:
        raise BaselineMissing(
            "refusing to run: a protected baseline is missing or has "
            f"changed -- {check['baselines']['files']}"
        )
    offenders = {
        arm: value["violations"]
        for arm, value in check["arms"].items() if value["violations"]
    }
    if offenders:
        raise SchemaNotCompilable(
            "refusing to start a live run: a schema fails a measured "
            f"provider limit and would not compile -- {offenders}"
        )
    if not check["groups"]["correct"]:
        raise GroupsNotComparable(
            "refusing to run: the result groups are not what the design "
            f"says -- {check['groups']}"
        )
    if check["reference_geometry"]["correct"] is False:
        raise ReferenceGeometryWrong(
            "refusing to run: an expectation does not agree with the "
            "kernel, so every attempt on it would be scored against a "
            f"number no plan can produce -- {check['reference_geometry']['failed']}"
        )
    if not check["invalid_plans"]["correct"]:
        raise ReferenceGeometryWrong(
            "refusing to run: a deliberately invalid plan was not refused "
            f"as its fixture requires -- {check['invalid_plans']['failed']}"
        )

    selected = tuple(cases) if cases is not None else CASES
    import tempfile

    service = CadApplicationService.local(cache_root or tempfile.mkdtemp())

    if model_factory is not None:
        model = model_factory()
    else:
        model = Stage48Model(real_model(), plan_choice)

    wanted = tuple(arms) if arms is not None else (V1, PLAN)
    unknown = set(wanted) - {V1, PLAN}
    if unknown:
        raise ValueError(f"unknown arm(s) {sorted(unknown)}")

    records: List[Stage48Record] = []
    for attempt in range(1, attempts + 1):
        for item in selected:
            runners: List[Tuple[str, Any]] = []
            if V1 in wanted and item.expect_v1 is not None:
                runners.append((V1, run_v1_attempt))
            if PLAN in wanted:
                runners.append((PLAN, run_plan_attempt))
            for representation, runner in runners:
                record = runner(model, service, item, attempt)
                records.append(record)
                if progress is not None:
                    progress(record)

    return {
        "stage": STAGE,
        "kind": RESULT_KIND,
        "evaluation_version": STAGE48_VERSION,
        "fingerprints": fingerprints(plan_choice),
        "preflight": {
            key: value for key, value in check.items()
            if key not in ("reference_geometry",)
        },
        "reference_geometry_ok": check["reference_geometry"]["correct"],
        "attempts_per_case": attempts,
        "arms": list(wanted),
        "plan_schema_choice": plan_choice,
        "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
        "schema_attached_per_arm": dict(getattr(model, "attached", {})),
        "cases": [item.identifier for item in selected],
        "summary": summarise(records, selected),
        "per_case": per_case(records, selected),
        "per_category": per_category(records),
        "records": [record.to_dict() for record in records],
        "relationship_to_stage_43": RELATIONSHIP_TO_STAGE_43,
    }


#: Stated in every result, so a reader who finds only the JSON knows what it
#: may and may not be compared with.
RELATIONSHIP_TO_STAGE_43 = (
    "Stage 48 is NOT a re-run of Stage 43 and its numbers are not a delta "
    "against it. Stage 43 sent executable_schema (six types, no sketch "
    "branch, two selectors) with plan prompt 2026-09-10.7 and scored a "
    "semantic selector as incorrect because none existed. Stage 48 sends "
    "the widened plan schema with the current prompt and builds a semantic "
    "selector through the graph executor. The legacy group re-uses Stage "
    "40's thirteen requests so that V1 and the operation plan are "
    "comparable WITHIN this run; Stage 43 remains the frozen comparison for "
    "the earlier capability envelope."
)


# --- reports ----------------------------------------------------------------


def _percent(value: Optional[float]) -> str:
    return "  n/a " if value is None else f"{value * 100:5.1f}%"


def format_preflight(data: Mapping[str, Any]) -> str:
    """The preflight, as a table."""
    lines = [
        "=" * 72,
        f"STAGE {data['stage']} PREFLIGHT (offline; no model call)",
        "=" * 72,
        f"evaluation version   {data['version']}",
        f"model                {data['model']}",
        f"plan schema          {data['plan_schema_choice']}",
        f"structured output    {data['structured_output_enabled']}",
        "",
    ]
    for representation, arm in data["arms"].items():
        facts = arm["facts"]
        lines.extend([
            f"--- {representation}",
            f"  fingerprint               {facts['fingerprint'][:16]}",
            f"  serialized characters     {facts['serialized_characters']}",
            f"  operation branches        {arm['branches']}",
            f"  optional properties       {facts['optional_properties']}"
            f"  (limit {facts['optional_properties_limit']})",
            f"  worst single object       "
            f"{facts['worst_object_optional_properties']}"
            f"  (limit {facts['worst_object_limit']})"
            f"  at {facts['worst_object_path']}",
            f"  rejected keywords         "
            f"{facts['rejected_keywords_present'] or 'none'}",
            "  additionalProperties      " + (
                "closed everywhere"
                if facts["every_object_closes_additional_properties"]
                else "NOT CLOSED"
            ),
            f"  oneOf                     {facts['uses_one_of']}",
            f"  minItems values           "
            f"{facts['min_items_values'] or 'none'}",
            f"  $defs                     {facts['definitions']}",
            "  verdict                   " + (
                "COMPILABLE (offline checks)"
                if arm["compilable_offline"] else "WOULD BE REJECTED"
            ),
        ])
        for violation in arm["violations"]:
            lines.append(f"     -- {violation}")
        lines.append("")

    groups = data["groups"]
    lines.extend([
        "--- result groups",
        f"  legacy cases              {groups['legacy_cases']} "
        "(both arms; text identical to the frozen Stage 40 corpus)",
        f"  capability cases          {groups['capability_cases']} "
        "(operation plan only)",
        f"  drifted from Stage 40     "
        f"{groups['legacy_text_drifted_from_stage_40'] or 'none'}",
        f"  beyond Stage 43's reach   "
        f"{', '.join(groups['capabilities_beyond_stage_43']) or 'none'}",
        "  verdict                   " + (
            "COMPARABLE" if groups["correct"] else "NOT COMPARABLE"
        ),
        "",
    ])

    reference = data["reference_geometry"]
    lines.extend([
        "--- reference geometry (built offline; no model)",
        f"  reference plans built     {reference['checked']}",
        f"  by the graph executor     "
        f"{len(reference.get('executed_by_graph', ()))}",
        f"  disagreeing with closed form  {reference['failed'] or 'none'}",
        "",
    ])

    invalid = data["invalid_plans"]
    lines.extend([
        "--- invalid plans (offline; no model)",
        f"  fixtures checked          {invalid['checked']}",
        f"  not refused as required   {invalid['failed'] or 'none'}",
        "",
    ])

    baselines = data["baselines"]
    lines.extend([
        "--- protected baselines",
        f"  files checked             {len(baselines['files'])}",
        "  all present and unchanged " + (
            "YES" if baselines["intact"] else "NO -- REFUSING TO RUN"
        ),
        "",
    ])

    prints = data["fingerprints"]
    lines.extend([
        "--- fingerprints",
        f"  corpus        {prints['corpus_version']}  "
        f"{prints['corpus'][:16]}",
        f"  scoring       {prints['scoring'][:16]}",
        f"  plan prompt   {prints['prompt'][PLAN]['version']}  "
        f"{prints['prompt'][PLAN]['fingerprint'][:16]}",
        f"  v1 prompt     {prints['prompt'][V1]['version']}  "
        f"{prints['prompt'][V1]['fingerprint'][:16]}",
        f"  plan schema   {prints['schema'][PLAN][:16]}",
        f"  v1 schema     {prints['schema'][V1][:16]}",
        "",
        f"result directory: {data['result_directory']}",
        f"READY: {data['ready']}",
    ])
    return "\n".join(lines)


def format_report(data: Mapping[str, Any]) -> str:
    """The Stage 48 result, as two tables that are never added together."""
    prints = data["fingerprints"]
    lines = [
        "=" * 72,
        f"STAGE {data['stage']}: OPERATION-PLAN CAPABILITY EVALUATION",
        "=" * 72,
        "This is NOT a re-run of Stage 43 and not a delta against it.",
        f"model                {prints['model']['model']}",
        f"plan schema          {data['plan_schema_choice']}  "
        f"({prints['schema'][PLAN][:16]})",
        f"plan prompt          {prints['prompt'][PLAN]['version']}  "
        f"({prints['prompt'][PLAN]['fingerprint'][:16]})",
        f"corpus               {prints['corpus_version']}  "
        f"({prints['corpus'][:16]})",
        f"scoring              {prints['scoring'][:16]}",
        f"attempts per case    {data['attempts_per_case']}",
        f"cases                {len(data['cases'])}",
        f"schema attached      {data.get('schema_attached_per_arm', {})}",
        "",
    ]
    for group, summary in data["summary"].items():
        lines.append("-" * 72)
        lines.append(
            f"{group.upper()} GROUP -- {summary['cases']} cases, arms: "
            f"{', '.join(summary['arms'])}"
        )
        lines.append("-" * 72)
        arms = summary["arms"]
        header = f"{'metric':<30}" + "".join(f"{arm:>20}" for arm in arms)
        lines.append(header)
        for metric in (
            "answered", "provider_errors", "model_output_valid",
            "structure_valid", "cad_valid", "build_success",
            "semantically_correct", "correct_unsupported",
            "correct_clarification", "correct_valid_unexecutable",
            "selector_correct", "executed_by_graph",
        ):
            row = f"{metric:<30}"
            for arm in arms:
                value = summary[arm].get(metric)
                if metric in ("answered", "provider_errors",
                              "executed_by_graph"):
                    row += f"{value if value is not None else '-':>20}"
                else:
                    row += f"{_percent(value):>20}"
            lines.append(row)
        lines.append("")
        for arm in arms:
            lines.append(f"  {arm} categories: {summary[arm]['categories']}")
        lines.append("")

    if data.get("per_category"):
        lines.append("-" * 72)
        lines.append("OPERATION PLAN BY CORPUS CATEGORY")
        lines.append("-" * 72)
        for category, row in data["per_category"].items():
            lines.append(
                f"{category:<18} {row['answered']:>3} answered  "
                f"{_percent(row['semantically_correct'])}  "
                f"{row['categories']}"
            )
        lines.append("")

    lines.append("-" * 72)
    lines.append("PER CASE (semantically correct / answered)")
    lines.append("-" * 72)
    for identifier, row in data["per_case"].items():
        parts = []
        for arm in (V1, PLAN):
            if arm in row:
                parts.append(
                    f"{arm} {row[arm]['correct']}/{row[arm]['answered']}"
                )
        lines.append(
            f"{identifier:<34} {row['group']:<11} {'  '.join(parts)}"
        )
        for detail in row.get(PLAN, {}).get("details", ())[:1]:
            lines.append(f"      plan: {detail}")
    lines.append("")
    lines.append(data.get("relationship_to_stage_43", ""))
    return "\n".join(lines)


def format_probe(data: Mapping[str, Any]) -> str:
    """The acceptance probe, as a table."""
    lines = [
        "=" * 72,
        f"STAGE {data['stage']} LIVE SCHEMA ACCEPTANCE PROBE "
        f"({data['calls']} real calls)",
        "=" * 72,
        f"model        {data['model']}",
        f"plan schema  {data['plan_schema_choice']}",
        "",
    ]
    for item in data["probes"]:
        lines.append(f"--- {item['representation']} / {item['probe']}")
        lines.append(f"  description          {item['description']!r}")
        lines.append(
            f"  schema fingerprint   {item['schema_fingerprint'][:16]}"
        )
        if not item.get("accepted"):
            lines.append("  ACCEPTED             NO")
            lines.append(f"  error kind           {item.get('error_kind')}")
            lines.append(
                f"  provider said        {item.get('error_message')}"
            )
        else:
            lines.append("  ACCEPTED             YES")
            lines.append(
                f"  structured output    "
                f"{item.get('structured_output_reported')}"
            )
            lines.append(
                f"  latency (s)          {item.get('latency_seconds')}"
            )
            lines.append(f"  usage                {item.get('usage')}")
            lines.append(
                f"  markdown fence       {item.get('contains_fence')}"
            )
            lines.append(
                f"  parses as JSON       {item.get('parses_as_json')}"
            )
        lines.append("")
    lines.append(f"all schemas accepted: {data['all_accepted']}")
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The CLI. ``--live`` is required to spend anything."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Stage 48: measure what the model does with the operation plan "
            "as Stages 44-47 left it. Not a re-run of Stage 43."
        )
    )
    parser.add_argument("--check", action="store_true",
                        help="offline preflight; calls no model")
    parser.add_argument("--list", action="store_true",
                        help="print the corpus; calls no model")
    parser.add_argument("--self-check", action="store_true",
                        help="run the whole loop against developer-written "
                             "reference plans; calls no model, and its "
                             "score says nothing about any model")
    parser.add_argument("--probe-live", action="store_true",
                        help="ask the provider to compile each schema "
                             "(THREE real calls; not a benchmark)")
    parser.add_argument("--live", action="store_true",
                        help="run the full evaluation (spends quota)")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="attempts per case per arm; applied to BOTH")
    parser.add_argument("--plan-schema", default=DEFAULT_PLAN_SCHEMA,
                        choices=sorted(PLAN_SCHEMAS),
                        help="which plan schema to send; recorded in the "
                             "result. Never chosen automatically")
    parser.add_argument("--case", action="append", default=None,
                        help="run only this case id (repeatable)")
    parser.add_argument("--group", default=None, choices=list(GROUPS),
                        help="run only one result group")
    parser.add_argument("--no-geometry-check", action="store_true",
                        help="skip the offline reference builds in --check")
    parser.add_argument("--out", default=None,
                        help="write the full result as JSON to this path")
    arguments = parser.parse_args(argv)

    if arguments.out and writes_into_a_baseline(arguments.out):
        print(
            f"refusing to write to {arguments.out}: it is inside a "
            "protected baseline directory "
            f"({', '.join(PROTECTED_BASELINE_DIRECTORIES)}). A Stage 48 "
            f"result belongs in {RESULT_DIRECTORY}."
        )
        return 1

    if arguments.list:
        from .stage48_corpus import describe

        print(describe())
        return 0

    if arguments.self_check:
        data = self_check(attempts=1, plan_choice=arguments.plan_schema)
        print(format_report(data))
        print()
        print("SELF-CHECK -- " + data["source"])
        if arguments.out:
            _write(arguments.out, data)
        plan = data["summary"].get(CAPABILITY, {}).get(PLAN, {})
        legacy = data["summary"].get(LEGACY, {}).get(PLAN, {})
        perfect = all(
            arm.get("semantically_correct") == 1.0
            for arm in (plan, legacy) if arm
        )
        return 0 if perfect else 1

    if arguments.check:
        data = preflight(
            arguments.plan_schema,
            geometry=not arguments.no_geometry_check,
        )
        print(format_preflight(data))
        print(f"\ncredential present: {credential_present()}")
        if arguments.out:
            _write(arguments.out, data)
        return 0 if data["ready"] else 1

    if arguments.probe_live:
        offline = preflight(arguments.plan_schema, geometry=False)
        if not all(
            arm["compilable_offline"] for arm in offline["arms"].values()
        ):
            print(format_preflight(offline))
            print("\nrefusing to probe: a schema fails the offline checks.")
            return 1
        try:
            data = probe_live(arguments.plan_schema)
        except CredentialUnavailable as error:
            print(f"STAGE {STAGE} PROBE: NOT RUN -- {error}")
            return 1
        print(format_probe(data))
        if arguments.out:
            _write(arguments.out, data)
        return 0 if data["all_accepted"] else 1

    if not arguments.live:
        print("refusing to run: pass --live to make real model calls.")
        print("  --check       offline preflight, free")
        print("  --list        print the corpus, free")
        print("  --probe-live  three calls, schema acceptance only")
        print(f"credential present: {credential_present()}")
        return 2

    if not credential_present():
        print(
            f"STAGE {STAGE} EVALUATION: NOT RUN -- {OPERATOR_KEY_VARIABLE} "
            "is not set. No other credential or provider may be substituted."
        )
        return 1

    selected: Optional[List[CapabilityCase]] = None
    if arguments.group:
        selected = list(cases_in(arguments.group))
    if arguments.case:
        chosen = [corpus_case(name) for name in arguments.case]
        selected = chosen if selected is None else [
            item for item in selected if item in chosen
        ]

    def show(record: Stage48Record) -> None:
        mark = (
            "ok" if record.category_code in STAGE48_SUCCESS_CATEGORIES
            else "XX"
        )
        path = " [graph]" if record.executed_by_graph else ""
        print(
            f"  [{mark}] a{record.attempt} {record.case_id:<34}"
            f"{record.representation:<16}{record.category_code}{path}",
            flush=True,
        )

    try:
        data = run(
            live=True,
            attempts=arguments.attempts,
            plan_choice=arguments.plan_schema,
            cases=selected,
            progress=show,
        )
    except (
        BaselineMissing, GroupsNotComparable, ReferenceGeometryWrong,
        SchemaNotCompilable,
    ) as error:
        print(f"STAGE {STAGE} EVALUATION: NOT RUN -- {error}")
        return 1
    print()
    print(format_report(data))
    if arguments.out:
        _write(arguments.out, data)
        print(f"\nwritten to {arguments.out}")
    return 0


def _write(path: str, data: Any) -> None:
    if writes_into_a_baseline(path):
        raise BaselineMissing(
            f"refusing to write to {path}: it is inside a protected "
            "baseline directory"
        )
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


__all__ = [
    "BASELINE_DIGESTS",
    "ExecutionUnsupported",
    "SelectorNotExpressible",
    "ReferencePlanStub",
    "DEFAULT_ATTEMPTS",
    "DEFAULT_PLAN_SCHEMA",
    "INVENTED_MISSING_VALUE",
    "PLAN_SCHEMAS",
    "PROBE_DESCRIPTION",
    "PROBE_PROFILE_DESCRIPTION",
    "PROTECTED_BASELINE_DIRECTORIES",
    "RELATIONSHIP_TO_STAGE_43",
    "RESULT_DIRECTORY",
    "RESULT_KIND",
    "SCORING_RULES",
    "SCORING_TOLERANCES",
    "STAGE",
    "CORRECT_CLARIFICATION",
    "STAGE48_CATEGORIES",
    "STAGE48_SUCCESS_CATEGORIES",
    "STAGE48_VERSION",
    "STRUCTURED_OUTPUT_ENABLED",
    "WRONG_SELECTOR",
    "BaselineMissing",
    "GroupsNotComparable",
    "ReferenceGeometryWrong",
    "Stage48Model",
    "Stage48Record",
    "check_invalid_plans",
    "check_reference_geometry",
    "fingerprints",
    "format_preflight",
    "format_probe",
    "format_report",
    "main",
    "per_case",
    "per_category",
    "plan_schema_for",
    "preflight",
    "probe_live",
    "real_model",
    "run",
    "run_plan_attempt",
    "run_v1_attempt",
    "schema_for",
    "self_check",
    "scoring_fingerprint",
    "summarise",
    "v1_schema",
    "writes_into_a_baseline",
]


if __name__ == "__main__":
    import sys

    sys.exit(main())
