"""Stage 40: V1 JSON versus the CAD operation plan, against the same model.

One question, one instrument: **does Claude Haiku produce the correct
manufacturable CAD representation more often from the canonical V1 document
or from the operation plan?**

Both arms are driven through the same stage ladder, in the same process, with
the same model, the same case text, the same attempt count, the same timeout
and the same output-token ceiling:

    A. text -> Haiku -> V1 document   -> V1 validator -> build -> RenderModel
    B. text -> Haiku -> operation plan -> plan parser -> plan rules
                                       -> adapter -> V1 validator -> build
                                       -> RenderModel

The V1 arm's own service stops at validation, so this module adds the build
and RenderModel steps for it -- using the *same* application service, the
*same* requested outputs and the *same* geometry check as arm B. That is the
"small addition to compare the two representations" the stage allows; it
changes no scoring rule and no existing module.

Fairness, and the one thing that had to be equalised
-----------------------------------------------------
The two services disagreed about ``max_output_tokens``: the V1 path asks for
4096, the plan path for 1024. Left alone, a long answer on the plan side
would be truncated and scored as a model failure, which would be a
measurement artefact rather than a result. :class:`_TimedModel` normalises
both arms to :data:`SHARED_MAX_OUTPUT_TOKENS` -- the larger of the two, so
neither side is cut off. Done here, in one visible place, rather than by
editing either frozen configuration, and decided before any live call.

Nothing else differs. In particular there is **no retry on either side**: a
provider error is recorded as a provider error and never as a wrong answer,
and it is never retried for one arm only. The installed SDK exposes no
temperature, top_p, top_k or seed on this path (see
``cad_ai.anthropic_provider.decoding_capabilities``), so decoding is
identically unconfigured for both.

Credentials
-----------
The credential is read once, from the environment, and handed to the SDK. It
is never printed, logged, returned, or written to a result file. Nothing in
the recorded output contains it, and :func:`run` refuses to start without
``--live``: a present credential must never by itself begin a paid run.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
)

from cad_ai.provider import ModelRequest, ModelResponse, ProviderError

from .adapter import ExecutionUnsupported, plan_to_document
from .comparison_corpus import (
    BOUNDING_BOX_ATOL,
    CASES,
    CORPUS_VERSION,
    EXPECT_BUILD,
    EXPECT_UNSUPPORTED,
    EXPECT_VALID_UNEXECUTABLE,
    VOLUME_RTOL,
    ComparisonCase,
    corpus_fingerprint,
)

#: The one model both arms are measured on. Pinned, not defaulted.
MODEL = "claude-haiku-4-5-20251001"

#: The credential the operator supplied. Bridged onto the SDK's own
#: conventional name for the life of the process, because the platform
#: reserves that name. Only presence is ever tested; the value is never read
#: by anything here but the SDK.
OPERATOR_KEY_VARIABLE = "CAD_ANTHROPIC_API_KEY"
SDK_KEY_VARIABLE = "ANTHROPIC_API_KEY"

#: Equalised across both arms -- see the module docstring.
SHARED_MAX_OUTPUT_TOKENS = 4096

#: Equalised timeout, in seconds.
SHARED_TIMEOUT_SECONDS = 120.0

#: What one build asks for. Identical on both arms.
OUTPUTS: Tuple[str, ...] = ("geometry", "render")

#: JSON-Schema keywords the Anthropic structured-output validator rejects,
#: measured against the live API rather than recalled. Kept because the two
#: frozen schemas both use some of them, and because a future run may want
#: to send a sanitised schema.
UNSUPPORTED_SCHEMA_KEYWORDS: Tuple[str, ...] = (
    "exclusiveMinimum", "exclusiveMaximum", "maximum", "minimum",
    "maxItems", "maxLength", "minLength", "multipleOf",
)

#: The API's ceiling on optional properties in a structured-output schema,
#: read from its own error message. Measured 2026-09-10.
OPTIONAL_PROPERTY_LIMIT = 24

#: Whether API-level structured output is used. It is **off for both arms**,
#: and not by preference.
#:
#: Measured against the live API: the V1 response schema is accepted once
#: `exclusiveMinimum` is stripped (8 optional properties). The operation-plan
#: schema is refused outright -- it declares **31** optional properties
#: against a limit of 24, because its flat `parameters` object must hold all
#: fifteen parameter names of all nine operation types, every one of them
#: optional. No sanitising fixes that; it is the shape of the representation.
#:
#: So there is no configuration in which BOTH frozen representations can use
#: structured output. Running V1 with it and the plan without would hand V1 a
#: grammar-constrained decoder the plan cannot have, which is precisely the
#: asymmetry the fairness rule forbids. Both therefore run without it, on the
#: prompt's own instruction to return JSON -- and the fact that only one of
#: them *could* have used it is reported as a finding rather than hidden in a
#: configuration.
#:
#: This was established before any model output was observed: all 130 calls
#: of the first attempt were rejected by request validation, so no result was
#: seen and nothing here is a reaction to a score.
STRUCTURED_OUTPUT_ENABLED = False

#: Attempts per case per representation. The same on both sides, always.
DEFAULT_ATTEMPTS = 5

# --- the two representation labels -----------------------------------------

V1 = "v1_json"
PLAN = "operation_plan"
REPRESENTATIONS: Tuple[str, ...] = (V1, PLAN)

# --- failure taxonomy, shared by both arms ---------------------------------

OK = "OK"
CORRECT_UNSUPPORTED = "CORRECT_UNSUPPORTED"
CORRECT_VALID_UNEXECUTABLE = "CORRECT_VALID_UNEXECUTABLE"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
PARSER_REJECTED = "PARSER_REJECTED"
PLAN_VALIDATION_REJECTED = "PLAN_VALIDATION_REJECTED"
V1_VALIDATION_REJECTED = "V1_VALIDATION_REJECTED"
BUILD_FAILED = "BUILD_FAILED"
RENDERMODEL_FAILED = "RENDERMODEL_FAILED"
SEMANTICALLY_INCORRECT = "SEMANTICALLY_INCORRECT"
WRONGLY_REFUSED = "WRONGLY_REFUSED"
WRONGLY_ANSWERED = "WRONGLY_ANSWERED"
EXECUTION_UNSUPPORTED = "EXECUTION_UNSUPPORTED"
PROVIDER_ERROR = "PROVIDER_ERROR"

#: Outcomes that count as a correct answer. Everything else is a failure.
SUCCESS_CATEGORIES: Tuple[str, ...] = (
    OK, CORRECT_UNSUPPORTED, CORRECT_VALID_UNEXECUTABLE,
)


class CredentialUnavailable(Exception):
    """No operator credential is present. Never a reason to substitute one."""


def credential_present() -> bool:
    """Whether an operator credential exists. Its value is never returned."""
    return bool(os.environ.get(OPERATOR_KEY_VARIABLE, "").strip())


def bridge_credential() -> None:
    """Put the operator's key where the SDK looks for it, in this process.

    The platform reserves ``ANTHROPIC_API_KEY``, so the operator supplies the
    key under its own name. This copies it across for the life of the
    process and returns nothing: no caller ever receives the value.
    """
    key = os.environ.get(OPERATOR_KEY_VARIABLE, "").strip()
    if not key:
        raise CredentialUnavailable(
            f"{OPERATOR_KEY_VARIABLE} is not set; the live comparison cannot "
            "run, and no other credential or provider may be substituted"
        )
    os.environ[SDK_KEY_VARIABLE] = key


def sanitise_schema(schema: Any) -> Any:
    """Strip the JSON-Schema keywords the structured-output API rejects.

    Kept for the record and for the tests: it is what *would* be needed to
    send either schema as a grammar, and it is enough for V1 and not enough
    for the plan. It removes only bounds -- never a type, an enum, a
    ``required`` list or ``additionalProperties`` -- so a sanitised schema
    still describes the same shape, just without its numeric and length
    limits. Those limits are enforced by the parser regardless, which is
    where they belong.
    """
    if isinstance(schema, dict):
        out: Dict[str, Any] = {}
        for key, value in schema.items():
            if key in UNSUPPORTED_SCHEMA_KEYWORDS:
                continue
            if key == "minItems" and isinstance(value, int):
                # The API accepts only 0 or 1 here.
                out[key] = 1 if value >= 1 else 0
                continue
            out[key] = sanitise_schema(value)
        return out
    if isinstance(schema, list):
        return [sanitise_schema(item) for item in schema]
    return schema


def optional_properties(schema: Any) -> Tuple[str, ...]:
    """Every optional property in a schema, by path.

    The count is what the structured-output API caps at
    :data:`OPTIONAL_PROPERTY_LIMIT`, so this is how the two representations'
    compilability is measured rather than guessed.
    """
    found: List[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                required = set(node.get("required") or ())
                for name in properties:
                    if name not in required:
                        found.append(f"{path}.{name}")
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(schema, "")
    return tuple(found)


# --- the shared timing / equalising wrapper --------------------------------


class _TimedModel:
    """Wraps a provider to time each call and equalise the token ceiling.

    Satisfies :class:`~cad_ai.provider.TextToCadModel` structurally, so both
    services treat it exactly as they treat the real provider. It changes one
    field of the request -- ``max_output_tokens`` -- and nothing else: not the
    system prompt, not the user text, not the schema.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "anthropic")
        self.last_latency_seconds: Optional[float] = None
        self.last_text: Optional[str] = None

    @property
    def config(self) -> Any:
        return self._inner.config

    def generate(self, request: ModelRequest) -> ModelResponse:
        equalised = ModelRequest(
            system=request.system,
            user_text=request.user_text,
            # Dropped for BOTH arms, for the reason recorded at
            # STRUCTURED_OUTPUT_ENABLED. A `None` schema is how the provider
            # already expresses "send no output_config".
            output_schema=(
                request.output_schema if STRUCTURED_OUTPUT_ENABLED else None
            ),
            max_output_tokens=SHARED_MAX_OUTPUT_TOKENS,
        )
        self.last_latency_seconds = None
        self.last_text = None
        start = time.monotonic()
        try:
            response = self._inner.generate(equalised)
        except ProviderError:
            self.last_latency_seconds = time.monotonic() - start
            raise
        self.last_latency_seconds = time.monotonic() - start
        self.last_text = response.text
        return response


# --- one attempt's record --------------------------------------------------


@dataclass
class AttemptRecord:
    """One model call, all the way down. The unit of measurement."""

    case_id: str
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

    category: str = PROVIDER_ERROR
    detail: Optional[str] = None
    operations: Tuple[str, ...] = ()
    volume_mm3: Optional[float] = None
    bounding_box: Optional[Mapping[str, float]] = None
    solid_count: Optional[int] = None
    triangle_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
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
            "category": self.category,
            "detail": self.detail,
            "operations": list(self.operations),
            "volume_mm3": self.volume_mm3,
            "bounding_box": (
                dict(self.bounding_box)
                if self.bounding_box is not None
                else None
            ),
            "solid_count": self.solid_count,
            "triangle_count": self.triangle_count,
        }


# --- geometry checking, identical for both arms ---------------------------


def _geometry_matches(
    expected: Any, volume: Optional[float],
    box: Optional[Mapping[str, float]], solids: Optional[int],
) -> Tuple[bool, str]:
    """Whether built geometry is the part that was asked for.

    Relative tolerance on the volume, absolute on the bounding box. No exact
    float comparison anywhere. Used by both arms with the same numbers.
    """
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


def _build_and_measure(
    service: CadApplicationService,
    document: Mapping[str, Any],
    record: AttemptRecord,
) -> None:
    """Build a canonical document and record what came out.

    The same function for both arms: once the operation plan has become a V1
    document, the two paths are literally the same code from here on.
    """
    outcome = service.build_document(
        BuildDocumentRequest.for_outputs(dict(document), *OUTPUTS)
    )
    record.build_success = bool(outcome.succeeded)
    if not outcome.succeeded:
        error = outcome.error
        record.category = BUILD_FAILED
        record.detail = (
            error.message if error is not None else "the build failed"
        )
        return

    geometry = outcome.artifact("geometry")
    details = dict(geometry.details) if geometry is not None else {}
    record.volume_mm3 = details.get("volume_mm3")
    record.solid_count = details.get("solid_count")
    box = (details.get("bounding_box") or {}).get("size")
    record.bounding_box = dict(box) if isinstance(box, Mapping) else None

    render = outcome.render_model
    if render is None:
        record.category = RENDERMODEL_FAILED
        record.detail = "the build produced no render model"
        return
    record.render_success = True
    # `triangle_count` is a METHOD on RenderModel, not a property. Called,
    # not stored: storing the bound method would put an unserialisable object
    # in the result and silently pass any "greater than zero" check.
    counter = getattr(render, "triangle_count", None)
    record.triangle_count = int(counter()) if callable(counter) else None


# --- arm A: canonical V1 JSON ---------------------------------------------


def run_v1_attempt(
    model: _TimedModel,
    service: CadApplicationService,
    item: ComparisonCase,
    attempt: int,
) -> AttemptRecord:
    """One V1-document attempt, through the existing production AI layer."""
    from cad_ai.generation import GenerationOutcome, TextToCadService

    record = AttemptRecord(
        case_id=item.identifier, representation=V1, attempt=attempt
    )
    planner = TextToCadService(
        model, service, max_output_tokens=SHARED_MAX_OUTPUT_TOKENS
    )
    result = planner.generate_cad_from_text(item.text)

    record.latency_seconds = model.last_latency_seconds
    record.raw_text = model.last_text
    record.usage = dict(result.metadata.usage)
    record.declared_outcome = result.outcome.value

    if result.outcome is GenerationOutcome.MODEL_ERROR:
        record.category = PROVIDER_ERROR
        record.detail = result.message
        return record

    # The model answered. Everything after this is the representation's
    # performance rather than the provider's.
    record.model_output_valid = True

    if result.outcome is GenerationOutcome.INVALID_MODEL_OUTPUT:
        # The production service folds "unparseable" and "invalid document"
        # into one outcome, so the recorded rule codes separate them: a
        # rejection that names S-rules got as far as the validator.
        if result.rule_codes:
            record.structure_valid = True
            record.category = V1_VALIDATION_REJECTED
        else:
            record.category = PARSER_REJECTED
        record.detail = "; ".join(result.issues[:4]) or result.message
        return record

    if result.outcome in (
        GenerationOutcome.UNSUPPORTED, GenerationOutcome.NEEDS_CLARIFICATION
    ):
        record.structure_valid = True
        if item.expect_v1 == EXPECT_UNSUPPORTED:
            record.semantically_correct = (
                result.outcome is GenerationOutcome.UNSUPPORTED
            )
            record.category = (
                CORRECT_UNSUPPORTED if record.semantically_correct
                else WRONGLY_REFUSED
            )
            record.detail = None if record.semantically_correct else (
                "asked for clarification where a refusal was required"
            )
        else:
            record.category = WRONGLY_REFUSED
            record.detail = "; ".join(
                (result.questions or result.issues)[:3]
            ) or result.message
        return record

    # GENERATED: a document that the existing V1 validator already accepted.
    document = result.candidate_document
    record.structure_valid = True
    record.cad_valid = True
    record.operations = _v1_feature_types(document)

    if item.expect_v1 != EXPECT_BUILD:
        # It produced a part where the correct answer was to refuse.
        record.category = WRONGLY_ANSWERED
        record.detail = (
            f"produced {', '.join(record.operations) or 'a document'} where "
            f"{item.expect_v1} was required"
        )
        return record

    _build_and_measure(service, document or {}, record)
    if not record.build_success or not record.render_success:
        return record
    _score_geometry(item, record, record.operations, item.required_v1_features)
    return record


def _v1_feature_types(document: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    features = (document or {}).get("features") or []
    return tuple(
        str(f.get("type")) for f in features if isinstance(f, Mapping)
    )


# --- arm B: the operation plan --------------------------------------------


def run_plan_attempt(
    model: _TimedModel,
    service: CadApplicationService,
    item: ComparisonCase,
    attempt: int,
) -> AttemptRecord:
    """One operation-plan attempt, through the experimental layer."""
    from .config import ExperimentalConfig
    from .generation import OperationPlanService, PlanOutcome

    record = AttemptRecord(
        case_id=item.identifier, representation=PLAN, attempt=attempt
    )
    planner = OperationPlanService(
        model, ExperimentalConfig(model=MODEL, provider="anthropic")
    )
    result = planner.generate(item.text)

    record.latency_seconds = model.last_latency_seconds
    record.raw_text = result.raw_text if result.raw_text else model.last_text
    record.usage = dict(result.metadata.usage)
    record.declared_outcome = result.outcome.value

    if result.outcome is PlanOutcome.MODEL_ERROR:
        record.category = PROVIDER_ERROR
        record.detail = result.error
        return record

    record.model_output_valid = True

    if result.outcome is PlanOutcome.INVALID_MODEL_OUTPUT:
        # The plan layer keeps the two apart already: a parse failure carries
        # no validation, a rule failure carries an invalid one.
        if result.plan_validation is not None:
            record.structure_valid = True
            record.category = PLAN_VALIDATION_REJECTED
            record.detail = "; ".join(
                f"{p.code} {p.message}"
                for p in result.plan_validation.problems[:4]
            )
        else:
            record.category = PARSER_REJECTED
            record.detail = result.error
        return record

    if result.outcome in (
        PlanOutcome.UNSUPPORTED, PlanOutcome.NEEDS_CLARIFICATION
    ):
        record.structure_valid = True
        if item.expect_plan == EXPECT_UNSUPPORTED:
            record.semantically_correct = (
                result.outcome is PlanOutcome.UNSUPPORTED
            )
            record.category = (
                CORRECT_UNSUPPORTED if record.semantically_correct
                else WRONGLY_REFUSED
            )
            record.detail = None if record.semantically_correct else (
                "asked for clarification where a refusal was required"
            )
        else:
            record.category = WRONGLY_REFUSED
            record.detail = "; ".join(
                (result.plan.questions if result.plan else ())[:3]
            ) or result.error
        return record

    # GENERATED, and the plan rules accepted it.
    plan = result.plan
    record.structure_valid = True
    record.operations = tuple(
        op.TYPE for op in (plan.operations if plan is not None else ())
    )

    if item.expect_plan == EXPECT_UNSUPPORTED:
        record.category = WRONGLY_ANSWERED
        record.detail = (
            f"produced {', '.join(record.operations) or 'a plan'} where a "
            "refusal was required"
        )
        return record

    try:
        document = plan_to_document(plan)
    except ExecutionUnsupported as exc:
        # A valid plan this backend cannot build. For the sketch cases that
        # is the *expected* answer, and it is scored as one; anywhere else it
        # is a failure to answer the request with buildable geometry.
        record.detail = f"cannot execute {', '.join(exc.operation_types)}"
        if item.expect_plan == EXPECT_VALID_UNEXECUTABLE:
            required = set(item.required_plan_operations)
            got = set(record.operations)
            if required and not required.issubset(got):
                record.category = SEMANTICALLY_INCORRECT
                record.detail = (
                    f"expected operations {sorted(required)}, got "
                    f"{sorted(got)}"
                )
                return record
            record.semantically_correct = True
            record.category = CORRECT_VALID_UNEXECUTABLE
            return record
        record.category = EXECUTION_UNSUPPORTED
        return record
    except Exception as exc:  # a plan that will not translate at all
        record.category = SEMANTICALLY_INCORRECT
        record.detail = f"{type(exc).__name__}: {exc}"
        return record

    if item.expect_plan == EXPECT_VALID_UNEXECUTABLE:
        # It answered with buildable geometry where the request asked for a
        # sketch. Buildable, and not what was asked for.
        record.category = SEMANTICALLY_INCORRECT
        record.detail = (
            f"produced buildable {', '.join(record.operations)} where the "
            "request asked for a sketch-based feature chain"
        )
        return record

    verdict = service.validate_document(dict(document))
    record.cad_valid = bool(verdict.valid)
    if not verdict.valid:
        record.category = V1_VALIDATION_REJECTED
        record.detail = _validation_detail(verdict)
        return record

    _build_and_measure(service, document, record)
    if not record.build_success or not record.render_success:
        return record
    _score_geometry(
        item, record, record.operations, item.required_plan_operations
    )
    return record


def _validation_detail(verdict: Any) -> str:
    """Why the existing V1 validator refused, in its own words.

    Reads the validator's structured errors when it published them, and its
    stable public sentence otherwise. Nothing here invents a reason.
    """
    error = getattr(verdict, "error", None)
    if error is None:
        return "the document is not valid"
    structured = getattr(error, "validation_errors", None) or ()
    codes = "; ".join(
        f"{getattr(e, 'code', '?')} {getattr(e, 'message', '')}".strip()
        for e in tuple(structured)[:4]
    )
    return codes or str(getattr(error, "message", "the document is not valid"))


# --- shared scoring --------------------------------------------------------


def _score_geometry(
    item: ComparisonCase,
    record: AttemptRecord,
    produced: Sequence[str],
    required: Sequence[str],
) -> None:
    """Decide semantic correctness. One implementation, both arms.

    Geometry first, because "did Claude produce the correct manufacturable
    CAD representation" is a question about the part, not about the JSON. The
    operation types are checked only where the request pins them -- case 6
    accepts a drilled hole or a subtracted cylinder, because both are that
    part.
    """
    matched, why = _geometry_matches(
        item.geometry, record.volume_mm3, record.bounding_box,
        record.solid_count,
    )
    if not matched:
        record.category = SEMANTICALLY_INCORRECT
        record.detail = why
        return
    missing = [kind for kind in required if kind not in set(produced)]
    if missing:
        record.category = SEMANTICALLY_INCORRECT
        record.detail = (
            f"geometry is right but the request named {', '.join(missing)}; "
            f"got {', '.join(produced) or 'nothing'}"
        )
        return
    record.semantically_correct = True
    record.category = OK


# --- the run ---------------------------------------------------------------


#: The stage flags a rate can be taken over, each read by an explicit
#: accessor. Spelled out rather than reached by a computed attribute name:
#: nothing in this package looks an attribute up by a name it was handed, so
#: that a test can assert the absence of the pattern outright.
_STAGE_FLAGS: Dict[str, Any] = {
    "model_output_valid": lambda r: r.model_output_valid,
    "structure_valid": lambda r: r.structure_valid,
    "cad_valid": lambda r: r.cad_valid,
    "build_success": lambda r: r.build_success,
    "render_success": lambda r: r.render_success,
    "semantically_correct": lambda r: r.semantically_correct,
}


def _rate(records: Sequence[AttemptRecord], flag: str) -> Optional[float]:
    """A success rate over attempts that the provider actually answered.

    A provider error is *unmeasured*, not incorrect, and stays out of every
    denominator -- the project's standing rule about not conflating provider
    reliability with model quality.
    """
    read = _STAGE_FLAGS[flag]
    answered = [r for r in records if r.category != PROVIDER_ERROR]
    if not answered:
        return None
    return sum(1 for r in answered if read(r)) / len(answered)


def summarise(records: Sequence[AttemptRecord]) -> Dict[str, Any]:
    """Every headline rate, per representation, over the whole run."""
    summary: Dict[str, Any] = {}
    for representation in REPRESENTATIONS:
        mine = [r for r in records if r.representation == representation]
        answered = [r for r in mine if r.category != PROVIDER_ERROR]
        latencies = [
            r.latency_seconds for r in mine if r.latency_seconds is not None
        ]
        # "Correct unsupported handling" is scored only on the cases whose
        # correct answer for THIS representation is a refusal.
        refusal_cases = {
            c.identifier for c in CASES
            if (c.expect_v1 if representation == V1 else c.expect_plan)
            == EXPECT_UNSUPPORTED
        }
        refusals = [r for r in answered if r.case_id in refusal_cases]
        summary[representation] = {
            "attempts": len(mine),
            "answered": len(answered),
            "provider_errors": len(mine) - len(answered),
            "model_output_valid": _rate(mine, "model_output_valid"),
            "structure_valid": _rate(mine, "structure_valid"),
            "cad_valid": _rate(mine, "cad_valid"),
            "build_success": _rate(mine, "build_success"),
            "render_success": _rate(mine, "render_success"),
            "semantically_correct": _rate(mine, "semantically_correct"),
            "correct_unsupported": (
                sum(1 for r in refusals if r.semantically_correct)
                / len(refusals)
            ) if refusals else None,
            "mean_latency_seconds": (
                sum(latencies) / len(latencies) if latencies else None
            ),
            "input_tokens": sum(r.usage.get("input_tokens", 0) for r in mine),
            "output_tokens": sum(r.usage.get("output_tokens", 0) for r in mine),
            "categories": _counts(mine),
        }
    return summary


def _counts(records: Sequence[AttemptRecord]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        counts[record.category] = counts.get(record.category, 0) + 1
    return dict(sorted(counts.items()))


def per_case(records: Sequence[AttemptRecord]) -> Dict[str, Any]:
    """Semantic correctness per case per representation."""
    table: Dict[str, Any] = {}
    for item in CASES:
        row: Dict[str, Any] = {"text": item.text, "category": item.category}
        for representation in REPRESENTATIONS:
            mine = [
                r for r in records
                if r.case_id == item.identifier
                and r.representation == representation
            ]
            answered = [r for r in mine if r.category != PROVIDER_ERROR]
            row[representation] = {
                "expects": (
                    item.expect_v1 if representation == V1 else item.expect_plan
                ),
                "attempts": len(mine),
                "answered": len(answered),
                "correct": sum(1 for r in answered if r.semantically_correct),
                "categories": _counts(mine),
                "details": sorted({
                    r.detail for r in mine if r.detail
                })[:3],
            }
        table[item.identifier] = row
    return table


def prompt_sizes() -> Dict[str, int]:
    """Both system prompts, measured. A secondary metric, recorded honestly."""
    from cad_ai.prompt import system_prompt as v1_prompt

    from .prompt import system_prompt as plan_prompt

    return {V1: len(v1_prompt()), PLAN: len(plan_prompt())}


def frozen_state() -> Dict[str, Any]:
    """The exact instrument this run used. Recorded with every result."""
    from cad_ai.prompt import PROMPT_VERSION as V1_VERSION
    from cad_ai.prompt import prompt_fingerprint as v1_fingerprint

    from .prompt import PROMPT_VERSION as PLAN_VERSION
    from .prompt import prompt_fingerprint as plan_fingerprint

    from cad_ai.specification import response_schema

    from .plan import plan_schema

    sizes = prompt_sizes()
    return {
        "model": MODEL,
        "corpus_version": CORPUS_VERSION,
        "corpus_fingerprint": corpus_fingerprint(),
        "shared_max_output_tokens": SHARED_MAX_OUTPUT_TOKENS,
        "shared_timeout_seconds": SHARED_TIMEOUT_SECONDS,
        "structured_output_enabled": STRUCTURED_OUTPUT_ENABLED,
        "optional_property_limit": OPTIONAL_PROPERTY_LIMIT,
        "optional_properties": {
            V1: len(optional_properties(response_schema())),
            PLAN: len(optional_properties(plan_schema())),
        },
        "outputs": list(OUTPUTS),
        V1: {
            "prompt_version": V1_VERSION,
            "prompt_fingerprint": v1_fingerprint(),
            "prompt_chars": sizes[V1],
        },
        PLAN: {
            "prompt_version": PLAN_VERSION,
            "prompt_fingerprint": plan_fingerprint(),
            "prompt_chars": sizes[PLAN],
        },
    }


def run(
    *,
    live: bool,
    attempts: int = DEFAULT_ATTEMPTS,
    cases: Optional[Sequence[ComparisonCase]] = None,
    cache_root: Optional[str] = None,
    model_factory: Any = None,
    progress: Any = None,
) -> Dict[str, Any]:
    """Run the comparison. ``live`` is required for a real model.

    Both representations are run for every attempt of every case, in the same
    loop, so neither can be advantaged by drift in the environment between
    them. Attempts are equal by construction: one counter, two arms.
    """
    if not live and model_factory is None:
        raise CredentialUnavailable(
            "a live run must be requested explicitly with --live; a present "
            "credential is never sufficient to begin a paid run"
        )

    selected = tuple(cases) if cases is not None else CASES
    import tempfile

    service = CadApplicationService.local(cache_root or tempfile.mkdtemp())

    if model_factory is not None:
        model = _TimedModel(model_factory())
    else:
        bridge_credential()
        from cad_ai.anthropic_provider import AnthropicTextToCadModel
        from cad_ai.config import AiConfig

        model = _TimedModel(AnthropicTextToCadModel.from_environment(
            AiConfig(
                provider="anthropic", model=MODEL,
                timeout_seconds=SHARED_TIMEOUT_SECONDS,
            )
        ))

    records: List[AttemptRecord] = []
    for attempt in range(1, attempts + 1):
        for item in selected:
            for representation, runner in (
                (V1, run_v1_attempt), (PLAN, run_plan_attempt),
            ):
                record = runner(model, service, item, attempt)
                records.append(record)
                if progress is not None:
                    progress(record)

    return {
        "frozen_state": frozen_state(),
        "attempts_per_case": attempts,
        "cases": [c.identifier for c in selected],
        "summary": summarise(records),
        "per_case": per_case(records),
        "records": [r.to_dict() for r in records],
    }


def format_report(data: Mapping[str, Any]) -> str:
    """The comparison table, and where each side failed."""
    frozen = data["frozen_state"]
    summary = data["summary"]
    lines = [
        "=" * 72,
        "V1 JSON vs CAD OPERATION PLAN",
        "=" * 72,
        f"model                {frozen['model']}",
        f"corpus               {frozen['corpus_version']} "
        f"({frozen['corpus_fingerprint'][:16]})",
        f"attempts per case    {data['attempts_per_case']}",
        f"cases                {len(data['cases'])}",
        f"max output tokens    {frozen['shared_max_output_tokens']} (both arms)",
        f"structured output    {frozen['structured_output_enabled']} "
        f"(both arms; plan schema has "
        f"{frozen['optional_properties'][PLAN]} optional properties against "
        f"an API limit of {frozen['optional_property_limit']}, V1 has "
        f"{frozen['optional_properties'][V1]})",
        "",
        f"{'Metric':<32}{'V1 JSON':>16}{'Operation Plan':>18}",
        "-" * 66,
    ]
    rows = (
        ("Model output valid", "model_output_valid", "rate"),
        ("Parse/structure valid", "structure_valid", "rate"),
        ("CAD validation valid", "cad_valid", "rate"),
        ("Build success", "build_success", "rate"),
        ("Semantic correctness", "semantically_correct", "rate"),
        ("RenderModel success", "render_success", "rate"),
        ("Correct unsupported", "correct_unsupported", "rate"),
        ("Mean latency (s)", "mean_latency_seconds", "seconds"),
        ("Attempts", "attempts", "count"),
        ("Provider errors", "provider_errors", "count"),
    )
    for label, key, kind in rows:
        cells = []
        for representation in REPRESENTATIONS:
            value = summary[representation].get(key)
            if value is None:
                cells.append("n/a")
            elif kind == "rate":
                cells.append(f"{value:.1%}")
            elif kind == "seconds":
                cells.append(f"{value:.2f}")
            else:
                cells.append(str(value))
        lines.append(f"{label:<32}{cells[0]:>16}{cells[1]:>18}")

    sizes = prompt_sizes()
    lines.append(
        f"{'Prompt characters':<32}{sizes[V1]:>16}{sizes[PLAN]:>18}"
    )
    lines.append("")

    for representation in REPRESENTATIONS:
        lines.append(f"{representation} failure categories:")
        for name, count in summary[representation]["categories"].items():
            mark = "  ok " if name in SUCCESS_CATEGORIES else "  -- "
            lines.append(f"{mark}{name:<34}{count:>4}")
        lines.append("")

    lines.append("per case (correct / answered):")
    lines.append(f"{'case':<24}{'V1':>12}{'plan':>12}   expectation")
    lines.append("-" * 72)
    for case_id, row in data["per_case"].items():
        v1 = row[V1]
        plan = row[PLAN]
        expect = (
            v1["expects"] if v1["expects"] == plan["expects"]
            else f"{v1['expects']} / {plan['expects']}"
        )
        lines.append(
            f"{case_id:<24}"
            f"{v1['correct']}/{v1['answered']:<10}"
            f"{plan['correct']}/{plan['answered']:<10} {expect}"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The CLI. ``--live`` is required to spend anything."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare V1 JSON against the CAD operation plan."
    )
    parser.add_argument("--live", action="store_true",
                        help="make real model calls (spends quota)")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help=f"attempts per case per arm (default "
                             f"{DEFAULT_ATTEMPTS}); applied to BOTH arms")
    parser.add_argument("--case", action="append", default=None,
                        help="run only this case id (repeatable)")
    parser.add_argument("--check", action="store_true",
                        help="print the corpus and the frozen state; "
                             "calls no model")
    parser.add_argument("--out", default=None,
                        help="write the full result as JSON to this path")
    arguments = parser.parse_args(argv)

    if arguments.check:
        from .comparison_corpus import describe

        print(describe())
        print(json.dumps(frozen_state(), indent=2))
        print(f"credential present: {credential_present()}")
        return 0

    if not arguments.live:
        print("refusing to run: pass --live to make real model calls.")
        print(f"credential present: {credential_present()}")
        return 2

    if not credential_present():
        print(
            f"REAL HAIKU COMPARISON: NOT RUN -- {OPERATOR_KEY_VARIABLE} is "
            "not set. No other credential or provider may be substituted."
        )
        return 1

    selected = None
    if arguments.case:
        from .comparison_corpus import case as one_case

        selected = [one_case(name) for name in arguments.case]

    def show(record: AttemptRecord) -> None:
        mark = "ok" if record.category in SUCCESS_CATEGORIES else "XX"
        print(
            f"  [{mark}] a{record.attempt} {record.case_id:<24}"
            f"{record.representation:<16}{record.category}",
            flush=True,
        )

    data = run(
        live=True, attempts=arguments.attempts, cases=selected,
        progress=show,
    )
    print()
    print(format_report(data))
    if arguments.out:
        with open(arguments.out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"\nwritten to {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
