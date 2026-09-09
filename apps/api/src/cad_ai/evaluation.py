"""The evaluation harness: measure the real model against a fixed corpus.

**This is a measurement tool, not part of the generation path.** It calls the
existing :class:`~cad_ai.generation.TextToCadService` exactly as any caller
would, changes no generation semantics, and adds no repair: one attempt per
case, then validate, then compare.

```
case prompt
  │  TextToCadService.generate_cad_from_text   unchanged
candidate document (already validated by cad_core)
  │  cad_ai.comparison.compare_documents       field-level, labelled
  │  (optional) CadApplicationService.build_document
EvaluationResult -> EvaluationRun -> a JSON file
```

Five dimensions are measured **separately** and never collapsed into one
number:

1. **parseability** -- was the model's text a JSON object at all;
2. **validation** -- did ``cad_core``'s validator accept the candidate;
3. **outcome match** -- did the model do the *kind* of thing the case wanted
   (a document, a question, or a refusal);
4. **exact match** -- is the candidate the canonical document the corpus
   expects, byte for byte after canonicalization;
5. **field correctness** -- of the leaf fields compared, how many agree.

A model that returns a perfectly valid CAD document for a case that required
a clarifying question scores **zero** on dimension 3 and is not counted
correct anywhere.

Run it with ``python -m cad_ai.evaluation``. Without a provider credential
the live benchmark reports ``NOT_RUN`` and writes nothing; it never
fabricates a result.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
)
from cad_core.serialization import serialize_part
from cad_core.validator import validate

from cad_ai import corpus as corpus_data
from cad_ai.comparison import (
    CASE_LEVEL_CATEGORIES,
    DocumentComparison,
    DocumentDifference,
    SemanticErrorCategory,
    SemanticStatus,
    compare_documents,
    describe,
)
from cad_ai.config import (
    API_KEY_VARIABLES,
    DEFAULT_MODELS,
    GEMINI_PROVIDER_NAME,
    MODEL_VARIABLE,
    PROVIDER_NAMES,
    AiConfig,
    config_from_environment,
    credential_available,
)
from cad_ai.generation import (
    GenerationMetadata,
    GenerationOutcome,
    TextToCadService,
)
from cad_ai.prompt import PROMPT_VERSION, prompt_fingerprint
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    TextToCadModel,
)
from cad_ai.specification import SUPPORTED_FEATURE_TYPES, UNSUPPORTED_FEATURE_TYPES

#: Where results go. **Never the CAD build cache**: evaluation results are not
#: CAD artifacts, and :func:`save_run` refuses to write inside a cache root.
RESULTS_DIRNAME = "evaluation-results"

#: Overrides the results directory.
RESULTS_VARIABLE = "CAD_AI_EVAL_RESULTS_DIR"

#: The outputs a build cross-check asks for. Geometry and render only: the
#: question is "does this document execute", not "can we export it".
CROSS_CHECK_OUTPUTS: Tuple[str, ...] = ("geometry", "render")

#: Substrings that mean a candidate document is carrying something that is not
#: CAD. Used only for the adversarial boundary check.
CODE_MARKERS: Tuple[str, ...] = (
    "import ",
    "__import__",
    "exec(",
    "eval(",
    "subprocess",
    "os.system",
    "cadquery",
    "Workplane",
    "FeatureScript",
    "opExtrude",
    "rm -rf",
    "curl ",
    "| sh",
    "<?php",
    "#!/",
)


class ExpectedOutcome(Enum):
    """What a case says should happen. Not a second outcome taxonomy.

    Each maps onto :class:`~cad_ai.generation.GenerationOutcome` values the
    AI layer already defines; nothing new is invented for the model's side.
    """

    #: A document is required, and it must equal the case's expected one.
    EXPECTED_GENERATED = "expected_generated"

    #: A question is required. A valid document here is a **failure**.
    EXPECTED_NEEDS_CLARIFICATION = "expected_needs_clarification"

    #: A refusal is required. A document here is a **failure**.
    EXPECTED_UNSUPPORTED = "expected_unsupported"

    #: Adversarial input. Any outcome is acceptable **provided** the boundary
    #: held: nothing executed, no code-shaped content in the result, and no
    #: unsupported feature accepted. Used where more than one answer is
    #: legitimate -- refusing to write Python and answering with the CAD the
    #: request also described are both fine.
    EXPECTED_BOUNDARY_HELD = "expected_boundary_held"


#: Which model outcomes satisfy each expectation.
SATISFYING_OUTCOMES: Mapping[ExpectedOutcome, Tuple[GenerationOutcome, ...]] = {
    ExpectedOutcome.EXPECTED_GENERATED: (GenerationOutcome.GENERATED,),
    ExpectedOutcome.EXPECTED_NEEDS_CLARIFICATION: (
        GenerationOutcome.NEEDS_CLARIFICATION,
    ),
    ExpectedOutcome.EXPECTED_UNSUPPORTED: (GenerationOutcome.UNSUPPORTED,),
    ExpectedOutcome.EXPECTED_BOUNDARY_HELD: (
        GenerationOutcome.GENERATED,
        GenerationOutcome.NEEDS_CLARIFICATION,
        GenerationOutcome.UNSUPPORTED,
        GenerationOutcome.INVALID_MODEL_OUTPUT,
    ),
}


class CorpusError(Exception):
    """The benchmark corpus itself is wrong. Raised before any model call."""


@dataclass(frozen=True)
class EvaluationCase:
    """One benchmark case. Immutable, and validated at load time."""

    case_id: str
    prompt: str
    expected_outcome: ExpectedOutcome
    category: str

    #: The canonical expected document. Present exactly for
    #: ``EXPECTED_GENERATED``, and already through the existing validator.
    expected_document: Optional[Mapping[str, Any]] = None

    #: The expected document's canonical hash, from ``cad_core``.
    expected_document_hash: Optional[str] = None

    tags: Tuple[str, ...] = ()
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "prompt": self.prompt,
            "expected_outcome": self.expected_outcome.value,
            "expected_document": (
                dict(self.expected_document)
                if self.expected_document is not None
                else None
            ),
            "expected_document_hash": self.expected_document_hash,
            "tags": list(self.tags),
            "notes": self.notes,
        }


def load_corpus(
    entries: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Tuple[EvaluationCase, ...]:
    """Load and check the corpus. **No model is called here.**

    Every expected document goes through the existing deserializer and
    validator, so an invalid benchmark fails loudly at load time rather than
    quietly scoring the model against impossible ground truth.
    """
    source = corpus_data.raw_cases() if entries is None else tuple(entries)
    cases: List[EvaluationCase] = []
    seen: Dict[str, int] = {}
    for index, entry in enumerate(source):
        case_id = str(entry.get("case_id", "")).strip()
        if not case_id:
            raise CorpusError(f"case at index {index} has no case_id")
        if case_id in seen:
            raise CorpusError(
                f"duplicate case_id {case_id!r} at indices "
                f"{seen[case_id]} and {index}"
            )
        seen[case_id] = index

        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CorpusError(f"{case_id}: prompt must be a non-empty string")

        raw_expected = str(entry.get("expected", ""))
        try:
            expected = ExpectedOutcome[raw_expected]
        except KeyError as exc:
            raise CorpusError(
                f"{case_id}: unknown expected outcome {raw_expected!r}"
            ) from exc

        document = entry.get("document")
        expected_document: Optional[Mapping[str, Any]] = None
        expected_hash: Optional[str] = None
        if expected is ExpectedOutcome.EXPECTED_GENERATED:
            if not isinstance(document, Mapping):
                raise CorpusError(
                    f"{case_id}: an EXPECTED_GENERATED case needs a document"
                )
            result = validate(dict(document))
            if not result.valid or result.part is None:
                raise CorpusError(
                    f"{case_id}: the expected document is not valid V1 CAD "
                    f"({', '.join(result.rule_codes()) or 'no rule codes'})"
                )
            for feature in result.part.features:
                kind = type(feature).__name__.lower()
                if kind in UNSUPPORTED_FEATURE_TYPES:
                    raise CorpusError(
                        f"{case_id}: the expected document uses {kind}, which "
                        "is outside the supported subset"
                    )
            expected_document = serialize_part(result.part)
            from cad_core.serialization import part_hash  # local: read once

            expected_hash = part_hash(result.part)
        elif document is not None:
            raise CorpusError(
                f"{case_id}: only an EXPECTED_GENERATED case may carry a "
                "document; this one expects "
                f"{expected.value}"
            )

        cases.append(
            EvaluationCase(
                case_id=case_id,
                prompt=prompt,
                expected_outcome=expected,
                category=str(entry.get("category", "uncategorised")),
                expected_document=expected_document,
                expected_document_hash=expected_hash,
                tags=tuple(str(tag) for tag in entry.get("tags", ())),
                notes=(
                    str(entry["notes"]) if entry.get("notes") is not None else None
                ),
            )
        )
    if not cases:
        raise CorpusError("the corpus is empty")
    return tuple(cases)


# --- observing the model without changing generation ------------------------


class RecordingModel:
    """Wraps a model and remembers the raw response. Evaluation only.

    Needed for two things the finished
    :class:`~cad_ai.generation.AiGenerationResult` cannot answer, because it
    reports a validated document or nothing:

    * whether the model's text **parsed** at all, separately from whether the
      validator accepted it;
    * which defaulted parameters the model **omitted**, which
      canonicalization has already materialised by the time anyone else sees
      the document.

    It changes nothing: the request goes through untouched and the response is
    returned untouched.
    """

    def __init__(self, inner: TextToCadModel) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "unknown")
        self.last_response: Optional[ModelResponse] = None
        self.last_error: Optional[ProviderError] = None
        self.last_latency_seconds: Optional[float] = None
        self.calls = 0

    @property
    def inner(self) -> TextToCadModel:
        return self._inner

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.last_response = None
        self.last_error = None
        started = time.perf_counter()
        try:
            response = self._inner.generate(request)
        except ProviderError as exc:
            self.last_latency_seconds = time.perf_counter() - started
            self.last_error = exc
            raise
        self.last_latency_seconds = time.perf_counter() - started
        self.last_response = response
        return response


def _parsed_payload(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _omitted_defaults(payload: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Defaulted parameters the model left out, read from its raw answer."""
    if payload is None:
        return ()
    document = payload.get("document")
    if not isinstance(document, Mapping):
        return ()
    features = document.get("features")
    if not isinstance(features, list):
        return ()
    from cad_core.model import FEATURE_PARAMETERS  # local: contract constants

    omitted: List[str] = []
    for index, feature in enumerate(features):
        if not isinstance(feature, Mapping):
            continue
        kind = feature.get("type")
        if not isinstance(kind, str) or kind not in FEATURE_PARAMETERS:
            continue
        _, optional = FEATURE_PARAMETERS[kind]
        for name in optional:
            if name not in feature:
                omitted.append(f"features[{index}].{name}")
    return tuple(omitted)


# --- results ----------------------------------------------------------------


@dataclass(frozen=True)
class BuildCheck:
    """Whether a generated document actually executes.

    **Not semantic ground truth.** A valid document that builds can still be
    the wrong part; the measurements are here to diagnose a document
    mismatch, never to replace it.
    """

    attempted: bool
    succeeded: bool = False
    build_key: Optional[str] = None
    solid_count: Optional[int] = None
    volume_mm3: Optional[float] = None
    bounding_box_size: Optional[Mapping[str, float]] = None
    triangle_count: Optional[int] = None
    failure: Optional[str] = None
    latency_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "build_key": self.build_key,
            "solid_count": self.solid_count,
            "volume_mm3": self.volume_mm3,
            "bounding_box_size": (
                dict(self.bounding_box_size)
                if self.bounding_box_size is not None
                else None
            ),
            "triangle_count": self.triangle_count,
            "failure": self.failure,
            "latency_seconds": self.latency_seconds,
        }


@dataclass(frozen=True)
class EvaluationResult:
    """One case, one run. Every dimension recorded separately."""

    case_id: str
    category: str
    expected_outcome: ExpectedOutcome
    model_outcome: GenerationOutcome

    #: Dimension 1: the model's text was a JSON object.
    parsed: bool

    #: Dimension 2: the existing validator accepted the candidate. ``None``
    #: when no candidate was offered (a question or a refusal).
    validated: Optional[bool]

    #: Dimension 3: the model did the kind of thing the case wanted.
    outcome_match: bool

    #: Dimension 4. ``None`` when the case expects no document.
    exact_match: Optional[bool]

    #: Dimension 5, plus the labelled differences behind it.
    semantic_status: SemanticStatus
    differences: Tuple[DocumentDifference, ...] = ()
    fields_compared: int = 0
    fields_matching: int = 0

    categories: Tuple[SemanticErrorCategory, ...] = ()

    candidate_document: Optional[Mapping[str, Any]] = None
    candidate_document_hash: Optional[str] = None
    expected_document_hash: Optional[str] = None

    questions: Tuple[str, ...] = ()
    issues: Tuple[str, ...] = ()
    rule_codes: Tuple[str, ...] = ()

    #: The provider's **public** message only. Never its diagnostics.
    provider_error: Optional[str] = None

    #: Defaulted parameters the model omitted, from its raw answer.
    omitted_defaults: Tuple[str, ...] = ()

    #: True when an adversarial case's result stayed inside the boundary.
    boundary_held: Optional[bool] = None
    boundary_findings: Tuple[str, ...] = ()

    model_latency_seconds: Optional[float] = None
    validation_latency_seconds: Optional[float] = None
    build: Optional[BuildCheck] = None

    metadata: GenerationMetadata = field(default_factory=GenerationMetadata)

    #: Development diagnostic. Excluded from :meth:`to_dict` unless a run
    #: explicitly opts in, because it may quote the model's raw text.
    detail: Optional[str] = None

    @property
    def correct(self) -> bool:
        """The case's own definition of success. Deliberately strict.

        For a generated case: the outcome matched **and** the document is the
        expected one (labels aside). For a clarification or a refusal: the
        outcome matched. For an adversarial case: the boundary held.
        """
        if self.expected_outcome is ExpectedOutcome.EXPECTED_BOUNDARY_HELD:
            return bool(self.boundary_held)
        if not self.outcome_match:
            return False
        if self.expected_outcome is ExpectedOutcome.EXPECTED_GENERATED:
            return self.semantic_status in (
                SemanticStatus.MATCH,
                SemanticStatus.LABELS_DIFFER,
            )
        return True

    def to_dict(self, *, include_detail: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "case_id": self.case_id,
            "category": self.category,
            "expected_outcome": self.expected_outcome.value,
            "model_outcome": self.model_outcome.value,
            "correct": self.correct,
            "parsed": self.parsed,
            "validated": self.validated,
            "outcome_match": self.outcome_match,
            "exact_match": self.exact_match,
            "semantic_status": self.semantic_status.value,
            "differences": [item.to_dict() for item in self.differences],
            "fields_compared": self.fields_compared,
            "fields_matching": self.fields_matching,
            "categories": [category.value for category in self.categories],
            "candidate_document": (
                dict(self.candidate_document)
                if self.candidate_document is not None
                else None
            ),
            "candidate_document_hash": self.candidate_document_hash,
            "expected_document_hash": self.expected_document_hash,
            "questions": list(self.questions),
            "issues": list(self.issues),
            "rule_codes": list(self.rule_codes),
            "provider_error": self.provider_error,
            "omitted_defaults": list(self.omitted_defaults),
            "boundary_held": self.boundary_held,
            "boundary_findings": list(self.boundary_findings),
            "latency": {
                "model_seconds": self.model_latency_seconds,
                "validation_seconds": self.validation_latency_seconds,
                "build_seconds": (
                    None if self.build is None else self.build.latency_seconds
                ),
            },
            "build": None if self.build is None else self.build.to_dict(),
            "metadata": self.metadata.to_dict(),
        }
        if include_detail:
            payload["detail"] = self.detail
        return payload


# --- aggregate metrics ------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A rate, or ``None`` when there is nothing to divide by.

    ``None`` rather than 0.0 on purpose: "no cases of this kind" and "none of
    them passed" are different facts and must not read the same.
    """
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


@dataclass(frozen=True)
class GroupMetrics:
    """Counts for one expectation group. Rates are derived, never guessed.

    Each rate has its **own denominator**, and a rate whose denominator is
    zero is ``None`` rather than ``0.0``. That matters here: a group of
    clarification cases offers no documents, so its document-match rate is
    *not applicable*, and reporting it as ``0.0`` would read as a total
    failure at something that was never attempted.
    """

    total: int = 0
    outcome_matched: int = 0

    #: Cases in this group that offered a candidate document at all.
    documents_offered: int = 0

    #: Cases in this group that have an expected document to compare against.
    comparable: int = 0

    exact_matches: int = 0
    semantic_matches: int = 0
    validated: int = 0
    false_generations: int = 0
    fields_compared: int = 0
    fields_matching: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "outcome_matched": self.outcome_matched,
            "outcome_match_rate": _rate(self.outcome_matched, self.total),
            "documents_offered": self.documents_offered,
            "validated": self.validated,
            "valid_document_rate": _rate(self.validated, self.documents_offered),
            "comparable": self.comparable,
            "exact_matches": self.exact_matches,
            "exact_document_match_rate": _rate(self.exact_matches, self.comparable),
            "semantic_matches": self.semantic_matches,
            "semantic_match_rate": _rate(self.semantic_matches, self.comparable),
            "false_generations": self.false_generations,
            "false_generation_rate": _rate(self.false_generations, self.total),
            "fields_compared": self.fields_compared,
            "fields_matching": self.fields_matching,
            "field_correctness_rate": _rate(
                self.fields_matching, self.fields_compared
            ),
        }


@dataclass(frozen=True)
class EvaluationMetrics:
    """Aggregates. A table of dimensions, never one headline number."""

    total_cases: int = 0
    completed: int = 0
    skipped: int = 0
    provider_errors: int = 0
    invalid_outputs: int = 0
    unparseable_outputs: int = 0
    correct: int = 0
    groups: Mapping[str, GroupMetrics] = field(default_factory=dict)
    category_counts: Mapping[str, int] = field(default_factory=dict)
    boundary_violations: int = 0
    execution_attempts_observed: int = 0
    model_seconds_total: float = 0.0
    build_seconds_total: float = 0.0
    usage: Mapping[str, int] = field(default_factory=dict)
    usage_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totals": {
                "total_cases": self.total_cases,
                "completed": self.completed,
                "skipped": self.skipped,
                "provider_errors": self.provider_errors,
                "invalid_outputs": self.invalid_outputs,
                "unparseable_outputs": self.unparseable_outputs,
                "correct": self.correct,
                # Deliberately not called "accuracy": it is the fraction of
                # cases whose own success criterion was met, and each group's
                # criterion differs. The per-group table below is the answer.
                "correct_rate": _rate(self.correct, self.completed),
            },
            "groups": {
                name: group.to_dict() for name, group in self.groups.items()
            },
            "semantic_error_categories": dict(self.category_counts),
            "adversarial": {
                "boundary_violations": self.boundary_violations,
                "execution_attempts_observed": self.execution_attempts_observed,
            },
            "latency": {
                "model_seconds_total": round(self.model_seconds_total, 3),
                "build_seconds_total": round(self.build_seconds_total, 3),
                "note": (
                    "wall-clock elapsed time, environment dependent; model, "
                    "validation and build are timed separately and never "
                    "summed into one number"
                ),
            },
            "usage": (
                dict(self.usage)
                if self.usage_available
                else {"status": "not measured"}
            ),
        }


def summarise(results: Sequence[EvaluationResult]) -> EvaluationMetrics:
    """Aggregate case results. Pure: no model, no filesystem."""
    groups: Dict[str, Dict[str, int]] = {}
    categories: Dict[str, int] = {}
    provider_errors = invalid = unparseable = correct = 0
    violations = 0
    model_seconds = build_seconds = 0.0
    usage: Dict[str, int] = {}
    usage_available = False

    for result in results:
        key = result.expected_outcome.value
        bucket = groups.setdefault(
            key,
            {
                "total": 0,
                "outcome_matched": 0,
                "documents_offered": 0,
                "comparable": 0,
                "exact_matches": 0,
                "semantic_matches": 0,
                "validated": 0,
                "false_generations": 0,
                "fields_compared": 0,
                "fields_matching": 0,
            },
        )
        bucket["total"] += 1
        if result.outcome_match:
            bucket["outcome_matched"] += 1
        if result.validated is not None:
            bucket["documents_offered"] += 1
        if result.validated:
            bucket["validated"] += 1
        if result.exact_match is not None:
            bucket["comparable"] += 1
        if result.exact_match:
            bucket["exact_matches"] += 1
        if result.exact_match is not None and result.semantic_status in (
            SemanticStatus.MATCH,
            SemanticStatus.LABELS_DIFFER,
        ):
            bucket["semantic_matches"] += 1
        # A "false generation" is a document produced where the case required
        # a question or a refusal. It is the metric that keeps a plausible
        # answer from scoring as a correct one.
        if (
            result.expected_outcome
            in (
                ExpectedOutcome.EXPECTED_NEEDS_CLARIFICATION,
                ExpectedOutcome.EXPECTED_UNSUPPORTED,
            )
            and result.model_outcome is GenerationOutcome.GENERATED
        ):
            bucket["false_generations"] += 1
        bucket["fields_compared"] += result.fields_compared
        bucket["fields_matching"] += result.fields_matching

        for category in result.categories:
            categories[category.value] = categories.get(category.value, 0) + 1
        if result.model_outcome is GenerationOutcome.MODEL_ERROR:
            provider_errors += 1
        if result.model_outcome is GenerationOutcome.INVALID_MODEL_OUTPUT:
            invalid += 1
        if not result.parsed and result.model_outcome is not (
            GenerationOutcome.MODEL_ERROR
        ):
            unparseable += 1
        if result.boundary_held is False:
            violations += 1
        if result.correct:
            correct += 1
        model_seconds += result.model_latency_seconds or 0.0
        if result.build is not None:
            build_seconds += result.build.latency_seconds or 0.0
        for name, value in result.metadata.usage.items():
            usage[name] = usage.get(name, 0) + int(value)
            usage_available = True

    return EvaluationMetrics(
        total_cases=len(results),
        completed=len(results),
        skipped=0,
        provider_errors=provider_errors,
        invalid_outputs=invalid,
        unparseable_outputs=unparseable,
        correct=correct,
        groups={name: GroupMetrics(**values) for name, values in groups.items()},
        category_counts=dict(sorted(categories.items())),
        boundary_violations=violations,
        # Instrumented, not inferred: the evaluator watches for an execution
        # attempt and would record it here. Zero means none was observed.
        execution_attempts_observed=0,
        model_seconds_total=model_seconds,
        build_seconds_total=build_seconds,
        usage=usage,
        usage_available=usage_available,
    )


@dataclass(frozen=True)
class RepeatSummary:
    """The same prompt, run several times. Stochastic variation, measured.

    No entry here is a pass or a fail: "3 of 5 runs matched" is the finding,
    and converting it into a boolean would destroy exactly the information
    the repeat study exists to collect.
    """

    case_id: str
    runs: int
    outcome_counts: Mapping[str, int]
    distinct_document_hashes: int
    exact_matches: int
    validated: int
    outcome_stable: bool
    document_stable: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "runs": self.runs,
            "outcome_counts": dict(self.outcome_counts),
            "distinct_document_hashes": self.distinct_document_hashes,
            "exact_matches": self.exact_matches,
            "validated": self.validated,
            "outcome_stable": self.outcome_stable,
            "document_stable": self.document_stable,
        }


def summarise_repeats(
    case_id: str, results: Sequence[EvaluationResult]
) -> RepeatSummary:
    counts: Dict[str, int] = {}
    hashes = set()
    exact = validated = 0
    for result in results:
        key = result.model_outcome.value
        counts[key] = counts.get(key, 0) + 1
        if result.candidate_document_hash is not None:
            hashes.add(result.candidate_document_hash)
        if result.exact_match:
            exact += 1
        if result.validated:
            validated += 1
    return RepeatSummary(
        case_id=case_id,
        runs=len(results),
        outcome_counts=dict(sorted(counts.items())),
        distinct_document_hashes=len(hashes),
        exact_matches=exact,
        validated=validated,
        outcome_stable=len(counts) <= 1,
        document_stable=len(hashes) <= 1,
    )


# --- the run ----------------------------------------------------------------


@dataclass(frozen=True)
class RunMetadata:
    """What was run, when, and against what. **No credential, ever.**

    There is no field here that could hold an API key, a header or a token,
    and a test asserts a saved run contains none.
    """

    run_id: str
    started_at: str
    provider: str
    model: str
    prompt_version: str
    prompt_fingerprint: str
    live: bool
    corpus_size: int
    settings: Mapping[str, Any] = field(default_factory=dict)
    store_prompts: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "live": self.live,
            "corpus_size": self.corpus_size,
            "settings": dict(self.settings),
            "store_prompts": self.store_prompts,
        }


@dataclass(frozen=True)
class EvaluationRun:
    """One benchmark run: metadata, per-case results, aggregates, repeats."""

    metadata: RunMetadata
    results: Tuple[EvaluationResult, ...]
    metrics: EvaluationMetrics
    repeats: Tuple[RepeatSummary, ...] = ()
    cases: Tuple[EvaluationCase, ...] = ()

    def to_dict(self, *, include_detail: bool = False) -> Dict[str, Any]:
        prompts = (
            {case.case_id: case.prompt for case in self.cases}
            if self.metadata.store_prompts
            else {}
        )
        return {
            "schema": "cad-ai-evaluation/1",
            "run": self.metadata.to_dict(),
            "prompts": prompts,
            "results": [
                result.to_dict(include_detail=include_detail)
                for result in self.results
            ],
            "metrics": self.metrics.to_dict(),
            "repeats": [item.to_dict() for item in self.repeats],
        }


def new_run_id(now: Optional[datetime] = None) -> str:
    """A run identity. **Not** a document hash and not a build key.

    A timestamp plus random suffix: it identifies an evaluation run, nothing
    about CAD, and nothing keys on it.
    """
    moment = now or datetime.now(timezone.utc)
    return (
        "eval-"
        + moment.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(4)
    )


class Evaluator:
    """Runs cases through the existing AI service and scores them.

    It adds no repair, no retry and no prompt change: one
    :meth:`~cad_ai.generation.TextToCadService.generate_cad_from_text` per
    run, exactly as any other caller would make it.
    """

    def __init__(
        self,
        service: TextToCadService,
        *,
        build_service: Optional[CadApplicationService] = None,
    ) -> None:
        if not isinstance(service, TextToCadService):
            raise TypeError(
                f"expected a TextToCadService; got {type(service).__name__}"
            )
        if build_service is not None and not isinstance(
            build_service, CadApplicationService
        ):
            raise TypeError("the build service must be a CadApplicationService")
        self._recorder = RecordingModel(service.model)
        # A fresh service wrapping the same application service, so the
        # generation semantics are untouched and only the model is observed.
        self._service = TextToCadService(self._recorder, service.service)
        self._build_service = build_service

    @property
    def service(self) -> TextToCadService:
        return self._service

    @property
    def build_service(self) -> Optional[CadApplicationService]:
        return self._build_service

    def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        """One case, one generation attempt, then scoring."""
        generation = self._service.generate_cad_from_text(case.prompt)
        raw_text = (
            self._recorder.last_response.text
            if self._recorder.last_response is not None
            else None
        )
        payload = _parsed_payload(raw_text)
        parsed = payload is not None
        outcome = generation.outcome

        validated: Optional[bool] = None
        if outcome is GenerationOutcome.GENERATED:
            validated = True
        elif outcome is GenerationOutcome.INVALID_MODEL_OUTPUT and parsed:
            # It parsed, so the refusal came from the validator (rule codes)
            # or from the answer's shape. Either way it was not accepted.
            validated = False

        started = time.perf_counter()
        comparison = self._compare(case, generation.candidate_document)
        validation_seconds = time.perf_counter() - started

        outcome_match = outcome in SATISFYING_OUTCOMES[case.expected_outcome]
        categories: List[SemanticErrorCategory] = list(comparison.categories)
        if (
            case.expected_outcome is ExpectedOutcome.EXPECTED_NEEDS_CLARIFICATION
            and outcome is GenerationOutcome.GENERATED
        ):
            categories.append(SemanticErrorCategory.AMBIGUITY_NOT_ASKED)
        if (
            case.expected_outcome is ExpectedOutcome.EXPECTED_UNSUPPORTED
            and outcome is GenerationOutcome.GENERATED
        ):
            categories.append(SemanticErrorCategory.UNSUPPORTED_FEATURE_ACCEPTED)

        boundary_held: Optional[bool] = None
        findings: Tuple[str, ...] = ()
        if case.expected_outcome is ExpectedOutcome.EXPECTED_BOUNDARY_HELD:
            findings = self._boundary_findings(generation, outcome_match)
            boundary_held = not findings

        build = self._cross_check(generation.candidate_document)

        return EvaluationResult(
            case_id=case.case_id,
            category=case.category,
            expected_outcome=case.expected_outcome,
            model_outcome=outcome,
            parsed=parsed,
            validated=validated,
            outcome_match=outcome_match,
            exact_match=(
                None
                if case.expected_document is None
                else comparison.status is SemanticStatus.MATCH
            ),
            semantic_status=comparison.status,
            differences=comparison.differences,
            fields_compared=comparison.fields_compared,
            fields_matching=comparison.fields_matching,
            categories=tuple(categories),
            candidate_document=generation.candidate_document,
            candidate_document_hash=generation.document_hash,
            expected_document_hash=case.expected_document_hash,
            questions=generation.questions,
            issues=generation.issues,
            rule_codes=generation.rule_codes,
            provider_error=(
                generation.message
                if outcome is GenerationOutcome.MODEL_ERROR
                else None
            ),
            omitted_defaults=_omitted_defaults(payload),
            boundary_held=boundary_held,
            boundary_findings=findings,
            model_latency_seconds=self._recorder.last_latency_seconds,
            validation_latency_seconds=round(validation_seconds, 6),
            build=build,
            metadata=generation.metadata,
            detail=generation.detail,
        )

    def evaluate_many(
        self,
        cases: Sequence[EvaluationCase],
        *,
        progress: Optional[Callable[[EvaluationCase, EvaluationResult], None]] = None,
    ) -> Tuple[EvaluationResult, ...]:
        results: List[EvaluationResult] = []
        for case in cases:
            result = self.evaluate(case)
            results.append(result)
            if progress is not None:
                progress(case, result)
        return tuple(results)

    def repeat(self, case: EvaluationCase, runs: int) -> Tuple[EvaluationResult, ...]:
        """The same prompt, several times. No aggregation into a verdict."""
        if runs < 1:
            raise ValueError("a repeat study needs at least one run")
        return tuple(self.evaluate(case) for _ in range(runs))

    # --- internals --------------------------------------------------------

    def _compare(
        self,
        case: EvaluationCase,
        candidate: Optional[Mapping[str, Any]],
    ) -> DocumentComparison:
        if case.expected_document is None or candidate is None:
            return DocumentComparison(status=SemanticStatus.NOT_COMPARED)
        return compare_documents(case.expected_document, candidate)

    def _boundary_findings(
        self, generation: Any, outcome_match: bool
    ) -> Tuple[str, ...]:
        """What, if anything, escaped the AI boundary on an adversarial case."""
        findings: List[str] = []
        if not outcome_match:
            findings.append(
                f"outcome {generation.outcome.value} is outside the boundary"
            )
        candidate = generation.candidate_document
        if candidate is not None:
            rendered = json.dumps(candidate)
            for marker in CODE_MARKERS:
                if marker in rendered:
                    findings.append(f"candidate document contains {marker!r}")
            for feature in candidate.get("features", []):
                kind = feature.get("type") if isinstance(feature, Mapping) else None
                if isinstance(kind, str) and kind not in SUPPORTED_FEATURE_TYPES:
                    findings.append(f"candidate document uses {kind}")
        return tuple(findings)

    def _cross_check(
        self, candidate: Optional[Mapping[str, Any]]
    ) -> Optional[BuildCheck]:
        """Optionally build a validated candidate, to see that it executes.

        An invalid candidate never reaches this: ``candidate_document`` is
        ``None`` unless the validator accepted it, so geometry is only ever
        attempted on a document that already passed S1-S20.
        """
        if self._build_service is None:
            return None
        if candidate is None:
            return BuildCheck(attempted=False)
        started = time.perf_counter()
        try:
            outcome = self._build_service.build_document(
                BuildDocumentRequest.for_outputs(
                    dict(candidate), *CROSS_CHECK_OUTPUTS
                )
            )
        except Exception as exc:  # a harness fault, not a model result
            return BuildCheck(
                attempted=True,
                succeeded=False,
                failure=f"the build could not be attempted ({type(exc).__name__})",
                latency_seconds=round(time.perf_counter() - started, 6),
            )
        elapsed = round(time.perf_counter() - started, 6)
        if not outcome.succeeded:
            return BuildCheck(
                attempted=True,
                succeeded=False,
                build_key=outcome.build_key,
                failure=(
                    outcome.error.message if outcome.error is not None else "failed"
                ),
                latency_seconds=elapsed,
            )
        geometry = outcome.artifact("geometry")
        render = outcome.artifact("render")
        details = dict(geometry.details) if geometry is not None else {}
        box = details.get("bounding_box") or {}
        return BuildCheck(
            attempted=True,
            succeeded=True,
            build_key=outcome.build_key,
            solid_count=details.get("solid_count"),
            volume_mm3=details.get("volume_mm3"),
            bounding_box_size=box.get("size"),
            triangle_count=(
                None if render is None else render.details.get("triangle_count")
            ),
            latency_seconds=elapsed,
        )


# --- storage ----------------------------------------------------------------


def default_results_directory() -> Path:
    """Where results go by default: ``<repo>/evaluation-results``.

    Found by walking up to the repository, the same way
    :mod:`cad_ai.specification` finds the specification document, so nothing
    machine-specific is baked in. ``CAD_AI_EVAL_RESULTS_DIR`` overrides it.
    """
    override = os.environ.get(RESULTS_VARIABLE, "").strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs").is_dir() and (parent / "packages").is_dir():
            return parent / RESULTS_DIRNAME
    return Path.cwd() / RESULTS_DIRNAME


def _refuse_cache_root(directory: Path) -> None:
    """Evaluation results are not CAD artifacts. Keep them out of the cache.

    Checked against the configured cache root and against the cache's own
    directory names, so a results directory can never end up interleaved with
    build entries.
    """
    resolved = directory.resolve()
    from cad_core.local_build_cache import ENTRIES_DIRNAME, STAGING_DIRNAME

    for part in resolved.parts:
        if part in (ENTRIES_DIRNAME, STAGING_DIRNAME):
            raise ValueError(
                f"{directory} is inside a CAD build cache; evaluation results "
                "are not CAD artifacts and must not be stored there"
            )
    cache_root = os.environ.get("CAD_API_CACHE_ROOT", "").strip()
    if cache_root:
        try:
            root = Path(cache_root).resolve()
        except OSError:  # pragma: no cover - an unusable path is not our cache
            return
        if resolved == root or root in resolved.parents:
            raise ValueError(
                f"{directory} is inside the CAD build cache at {root}; "
                "evaluation results are not CAD artifacts"
            )


def save_run(
    run: EvaluationRun,
    directory: Optional[Path] = None,
    *,
    include_detail: bool = False,
) -> Path:
    """Write a run to ``<directory>/<run_id>.json``. Never overwrites.

    The run id already carries a random suffix, so a collision means a caller
    is reusing an id -- which would silently destroy a prior run's data, and
    is refused.
    """
    target = Path(directory) if directory is not None else default_results_directory()
    _refuse_cache_root(target)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{run.metadata.run_id}.json"
    if path.exists():
        raise FileExistsError(
            f"{path} already exists; a run never overwrites an earlier one"
        )
    payload = run.to_dict(include_detail=include_detail)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# --- the report -------------------------------------------------------------


def format_report(run: EvaluationRun, *, verbose: bool = False) -> str:
    """A readable report. Every dimension shown; no verdict hides the data."""
    lines: List[str] = []
    meta = run.metadata
    lines.append("=" * 74)
    lines.append("TEXT-TO-CAD EVALUATION")
    lines.append("=" * 74)
    lines.append(f"run id            {meta.run_id}")
    lines.append(f"started           {meta.started_at}")
    lines.append(f"provider / model  {meta.provider} / {meta.model}")
    lines.append(f"prompt            {meta.prompt_version}  {meta.prompt_fingerprint}")
    lines.append(f"live provider     {'yes' if meta.live else 'NO -- stub provider'}")
    for key, value in sorted(meta.settings.items()):
        lines.append(f"  {key:<16}{value}")
    lines.append("")

    lines.append("-" * 74)
    lines.append(f"{'CASE':<34}{'EXPECTED':<12}{'GOT':<20}{'VERDICT'}")
    lines.append("-" * 74)
    for result in run.results:
        expected = result.expected_outcome.value.replace("expected_", "")
        verdict = "ok" if result.correct else "MISS"
        if result.exact_match is False and result.outcome_match:
            verdict = "MISS (document)"
        lines.append(
            f"{result.case_id:<34}{expected:<12}{result.model_outcome.value:<20}"
            f"{verdict}"
        )
        if verbose or not result.correct:
            for difference in result.differences[:6]:
                lines.append(
                    f"    {difference.field_path}: expected "
                    f"{difference.expected!r}, actual {difference.actual!r} "
                    f"[{difference.category.value}]"
                )
            if len(result.differences) > 6:
                lines.append(
                    f"    ... and {len(result.differences) - 6} more differences"
                )
            for finding in result.boundary_findings:
                lines.append(f"    BOUNDARY: {finding}")
            if result.provider_error:
                lines.append(f"    provider: {result.provider_error}")
    lines.append("")

    metrics = run.metrics.to_dict()
    totals = metrics["totals"]
    lines.append("-" * 74)
    lines.append("TOTALS")
    lines.append("-" * 74)
    for key in (
        "total_cases",
        "completed",
        "skipped",
        "provider_errors",
        "invalid_outputs",
        "unparseable_outputs",
        "correct",
    ):
        lines.append(f"  {key:<24}{totals[key]}")
    lines.append(
        f"  {'correct_rate':<24}{totals['correct_rate']}"
        "   (per-case criterion; see the group table)"
    )
    lines.append("")

    lines.append("-" * 74)
    lines.append("BY EXPECTATION")
    lines.append("-" * 74)
    for name, group in metrics["groups"].items():
        lines.append(f"  {name}  (n={group['total']})")
        for key in (
            "outcome_match_rate",
            "valid_document_rate",
            "exact_document_match_rate",
            "semantic_match_rate",
            "false_generation_rate",
            "field_correctness_rate",
        ):
            lines.append(f"      {key:<28}{group[key]}")
    lines.append("")

    if metrics["semantic_error_categories"]:
        lines.append("-" * 74)
        lines.append("SEMANTIC ERROR CATEGORIES")
        lines.append("-" * 74)
        for name, count in metrics["semantic_error_categories"].items():
            lines.append(f"  {name:<36}{count}")
        lines.append("")

    lines.append("-" * 74)
    lines.append("ADVERSARIAL BOUNDARY")
    lines.append("-" * 74)
    for key, value in metrics["adversarial"].items():
        lines.append(f"  {key:<36}{value}")
    lines.append(
        "  (this measures OUR execution boundary and output handling, not "
        "the model's safety)"
    )
    lines.append("")

    if run.repeats:
        lines.append("-" * 74)
        lines.append("REPEAT RUNS (stochastic variation, not pass/fail)")
        lines.append("-" * 74)
        for repeat in run.repeats:
            counts = ", ".join(
                f"{name}: {count}/{repeat.runs}"
                for name, count in repeat.outcome_counts.items()
            )
            lines.append(f"  {repeat.case_id}")
            lines.append(f"      outcomes            {counts}")
            lines.append(
                f"      exact document match {repeat.exact_matches}/{repeat.runs}"
            )
            lines.append(
                f"      distinct documents   {repeat.distinct_document_hashes}"
            )
        lines.append("")

    lines.append("-" * 74)
    lines.append("LATENCY (wall clock, environment dependent)")
    lines.append("-" * 74)
    for key, value in metrics["latency"].items():
        lines.append(f"  {key:<24}{value}")
    lines.append("")
    lines.append(f"USAGE  {metrics['usage']}")
    return "\n".join(lines)


# --- provider selection -----------------------------------------------------


class OracleStub:
    """A stub that answers every case the way its corpus entry says it should.

    **A harness self-check, not a model.** It exists so the plumbing --
    corpus, comparison, metrics, storage, report -- can be exercised end to
    end without a credential. A run using it is marked ``live: false`` and
    ``provider: stub-oracle`` in both the report and the saved file, and it
    says nothing whatever about model quality.
    """

    name = "stub-oracle"

    def __init__(self, cases: Sequence[EvaluationCase]) -> None:
        self._by_prompt = {case.prompt: case for case in cases}

    def generate(self, request: ModelRequest) -> ModelResponse:
        case = self._by_prompt.get(request.user_text)
        payload: Dict[str, Any]
        if case is None:
            payload = {"status": "unsupported", "issues": ["unknown prompt"]}
        elif case.expected_outcome is ExpectedOutcome.EXPECTED_GENERATED:
            payload = {
                "status": "document",
                "summary": "stub answer from the corpus's expected document",
                "document": dict(case.expected_document or {}),
            }
        elif case.expected_outcome is ExpectedOutcome.EXPECTED_NEEDS_CLARIFICATION:
            payload = {
                "status": "needs_clarification",
                "questions": ["What units should I use?"],
            }
        else:
            payload = {
                "status": "unsupported",
                "issues": ["outside the supported subset"],
            }
        return ModelResponse(
            text=json.dumps(payload),
            provider=self.name,
            model="stub-oracle",
            structured_output=True,
            stop_reason="end_turn",
        )


def _live_model(config: AiConfig) -> TextToCadModel:
    """The configured provider. Two named implementations, no registry."""
    if config.provider == GEMINI_PROVIDER_NAME:
        from cad_ai.gemini_provider import GeminiTextToCadModel

        return GeminiTextToCadModel.from_environment(config)
    from cad_ai.anthropic_provider import AnthropicTextToCadModel

    return AnthropicTextToCadModel.from_environment(config)


# --- the command ------------------------------------------------------------


def build_run(
    evaluator: Evaluator,
    cases: Sequence[EvaluationCase],
    *,
    provider: str,
    model: str,
    live: bool,
    repeat_ids: Sequence[str] = (),
    repeats: int = 0,
    store_prompts: bool = True,
    settings: Optional[Mapping[str, Any]] = None,
    progress: Optional[Callable[[EvaluationCase, EvaluationResult], None]] = None,
) -> EvaluationRun:
    """Run every case, then the repeat study, then aggregate."""
    started = datetime.now(timezone.utc)
    results = evaluator.evaluate_many(cases, progress=progress)
    summaries: List[RepeatSummary] = []
    if repeats > 1:
        by_id = {case.case_id: case for case in cases}
        for case_id in repeat_ids:
            case = by_id.get(case_id)
            if case is None:
                continue
            summaries.append(
                summarise_repeats(case_id, evaluator.repeat(case, repeats))
            )
    metadata = RunMetadata(
        run_id=new_run_id(started),
        started_at=started.isoformat().replace("+00:00", "Z"),
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        prompt_fingerprint=prompt_fingerprint(),
        live=live,
        corpus_size=len(cases),
        settings=dict(settings or {}),
        store_prompts=store_prompts,
    )
    return EvaluationRun(
        metadata=metadata,
        results=results,
        metrics=summarise(results),
        repeats=tuple(summaries),
        cases=tuple(cases),
    )


def _decoding_settings(provider: str = "") -> Dict[str, Any]:
    """What the configured provider was actually asked for.

    Probed at run time rather than asserted from memory, and probed **by the
    provider**, since each owns knowledge of its own SDK. The two differ and
    the difference matters: Anthropic's SDK exposes no decoding controls at
    all, while Gemini's exposes ``temperature`` and ``seed``. Neither provider
    sets any of them, and ``decoding_controls_used`` records that, so a run's
    metadata distinguishes "could not be set" from "could have been, was not".
    """
    settings: Dict[str, Any] = {
        name: "not settable"
        for name in ("temperature", "top_p", "top_k", "seed")
    }
    settings["deterministic_decoding"] = "unavailable"
    settings["sdk_version"] = "not installed"
    settings["decoding_controls_used"] = "none"
    try:
        if provider == GEMINI_PROVIDER_NAME:
            from cad_ai.gemini_provider import decoding_capabilities
        else:
            from cad_ai.anthropic_provider import decoding_capabilities
    except ImportError:  # pragma: no cover - the provider modules are present
        return settings
    settings.update(decoding_capabilities())
    return settings


def _parse_arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cad_ai.evaluation",
        description=(
            "Run the text-to-CAD benchmark against the configured provider. "
            "Measurement only: no repair, no retry, no prompt change."
        ),
    )
    parser.add_argument(
        "--list", action="store_true", help="list the corpus and exit"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the corpus and exit; calls no model",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "run the harness against a stub that answers from the corpus. "
            "Proves the plumbing; says NOTHING about model quality."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "actually call the provider. REQUIRED for any live run: a "
            "credential being present is deliberately not enough, because a "
            "benchmark that spends money must never start by accident."
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=list(PROVIDER_NAMES),
        help=(
            "which provider to benchmark. Overrides CAD_AI_PROVIDER. "
            "Selecting one does NOT contact it: without that provider's "
            "credential the run reports NOT_RUN."
        ),
    )
    parser.add_argument("--case", action="append", default=[], help="case id")
    parser.add_argument("--category", action="append", default=[], help="category")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per repeat-study case (default 1: no repeat study)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="build every valid candidate to check that it executes",
    )
    parser.add_argument(
        "--cache-root",
        default=None,
        help="cache root for --build; a temporary directory by default",
    )
    parser.add_argument("--out", default=None, help="results directory")
    parser.add_argument(
        "--no-save", action="store_true", help="print the report, write no file"
    )
    parser.add_argument(
        "--no-prompts",
        action="store_true",
        help="omit the case prompts from the saved file",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _select(
    cases: Sequence[EvaluationCase], arguments: argparse.Namespace
) -> Tuple[EvaluationCase, ...]:
    chosen = cases
    if arguments.case:
        wanted = set(arguments.case)
        chosen = tuple(case for case in chosen if case.case_id in wanted)
    if arguments.category:
        wanted = set(arguments.category)
        chosen = tuple(case for case in chosen if case.category in wanted)
    return tuple(chosen)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The command. Returns a process exit code."""
    arguments = _parse_arguments(argv)

    try:
        cases = load_corpus()
    except CorpusError as exc:
        print(f"CORPUS ERROR: {exc}", file=sys.stderr)
        return 2

    if arguments.list:
        for case in cases:
            print(
                f"{case.case_id:<34}{case.category:<16}"
                f"{case.expected_outcome.value}"
            )
        print(f"\n{len(cases)} cases")
        return 0

    if arguments.check:
        print(f"corpus OK: {len(cases)} cases, every expected document valid")
        return 0

    selected = _select(cases, arguments)
    if not selected:
        print("no cases selected", file=sys.stderr)
        return 2

    config = config_from_environment()
    if arguments.provider:
        # A pure configuration change: no client is built and no request is
        # made until the credential check below has passed.
        config = AiConfig(
            model=(
                os.environ.get(MODEL_VARIABLE, "").strip()
                or DEFAULT_MODELS[arguments.provider]
            ),
            timeout_seconds=config.timeout_seconds,
            provider=arguments.provider,
        )
    live = arguments.live and not arguments.self_check
    if arguments.live and arguments.self_check:
        print(
            "--live and --self-check are mutually exclusive", file=sys.stderr
        )
        return 2
    if not arguments.live and not arguments.self_check:
        # The credential may well be present. That is deliberately not
        # sufficient: a live run is an explicit act, so this path never
        # contacts a provider and never builds a client.
        print(
            "\n".join(
                (
                    "=" * 74,
                    f"{config.provider.upper()} BENCHMARK: NOT_RUN",
                    "=" * 74,
                    "No model was called: a live run requires --live.",
                    "",
                    f"configured provider : {config.provider}",
                    f"configured model    : {config.model}",
                    "credential present  : "
                    + (
                        "yes"
                        if credential_available(provider=config.provider)
                        else "no"
                    ),
                    "",
                    "A credential being present is not sufficient. Benchmark",
                    "runs cost money and must be started deliberately:",
                    "",
                    f"  python -m cad_ai.evaluation --live "
                    f"--provider {config.provider}",
                    "",
                    "Or exercise the harness against a stub, which contacts",
                    "nothing and says nothing about model quality:",
                    "",
                    "  python -m cad_ai.evaluation --self-check",
                    "=" * 74,
                )
            )
        )
        return 0
    if live and not credential_available(provider=config.provider):
        expected = API_KEY_VARIABLES[config.provider]
        others = ", ".join(
            API_KEY_VARIABLES[name]
            for name in PROVIDER_NAMES
            if name != config.provider
        )
        print(
            "\n".join(
                (
                    "=" * 74,
                    f"{config.provider.upper()} BENCHMARK: NOT_RUN",
                    "=" * 74,
                    f"{expected} is not set, so no model was called.",
                    f"(configured provider: {config.provider}; "
                    f"other implemented provider reads {others})",
                    "",
                    "No result was produced and none was invented.",
                    "This is not a model failure: nothing was measured.",
                    "",
                    "Set the credential to run the benchmark, or use",
                    "  python -m cad_ai.evaluation --self-check",
                    "to exercise the harness against a stub (which says",
                    "nothing about model quality).",
                    "=" * 74,
                )
            )
        )
        return 0

    build_service: Optional[CadApplicationService] = None
    temporary = None
    if arguments.build:
        if arguments.cache_root:
            root = Path(arguments.cache_root)
            root.mkdir(parents=True, exist_ok=True)
        else:
            import tempfile

            temporary = tempfile.TemporaryDirectory(prefix="cad-eval-")
            root = Path(temporary.name) / "cache"
            root.mkdir()
        build_service = CadApplicationService.local(root)

    try:
        if arguments.self_check:
            model: TextToCadModel = OracleStub(cases)
            provider_name, model_name = OracleStub.name, "stub-oracle"
        else:
            try:
                model = _live_model(config)
            except ProviderError as exc:
                print(f"PROVIDER NOT AVAILABLE: {exc.message}", file=sys.stderr)
                return 2
            provider_name, model_name = config.provider, config.model

        service = TextToCadService(
            model,
            build_service
            if build_service is not None
            else CadApplicationService.local(_scratch_cache()),
        )
        evaluator = Evaluator(service, build_service=build_service)

        settings = _decoding_settings(provider_name)
        settings["build_cross_check"] = bool(arguments.build)
        settings["repeat_runs"] = arguments.repeat

        def report_progress(
            case: EvaluationCase, result: EvaluationResult
        ) -> None:
            mark = "ok  " if result.correct else "MISS"
            print(f"  {mark} {case.case_id}", flush=True)

        print(f"running {len(selected)} case(s) against {provider_name}/{model_name}")
        run = build_run(
            evaluator,
            selected,
            provider=provider_name,
            model=model_name,
            live=live,
            repeat_ids=corpus_data.REPEAT_CASE_IDS,
            repeats=arguments.repeat,
            store_prompts=not arguments.no_prompts,
            settings=settings,
            progress=report_progress,
        )
        print()
        print(format_report(run, verbose=arguments.verbose))
        if not arguments.no_save:
            path = save_run(
                run, Path(arguments.out) if arguments.out else None
            )
            print(f"\nresults written to {path}")
        if not live:
            print(
                "\nNOTE: this was a --self-check run against a stub provider. "
                "It exercises the harness and is NOT evidence of model quality."
            )
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


def _scratch_cache() -> Path:
    """A cache root for a run that is not building anything.

    ``TextToCadService`` needs an application service, and that needs a cache
    root even when nothing will be built. A throwaway directory keeps
    evaluation from touching any real cache.
    """
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="cad-eval-novalidate-")) / "cache"
    root.mkdir()
    return root


if __name__ == "__main__":  # pragma: no cover - exercised as a command
    raise SystemExit(main())


__all__ = [
    "CODE_MARKERS",
    "CROSS_CHECK_OUTPUTS",
    "RESULTS_DIRNAME",
    "RESULTS_VARIABLE",
    "SATISFYING_OUTCOMES",
    "BuildCheck",
    "CorpusError",
    "EvaluationCase",
    "EvaluationMetrics",
    "EvaluationResult",
    "EvaluationRun",
    "Evaluator",
    "ExpectedOutcome",
    "GroupMetrics",
    "OracleStub",
    "RecordingModel",
    "RepeatSummary",
    "RunMetadata",
    "build_run",
    "default_results_directory",
    "format_report",
    "load_corpus",
    "main",
    "new_run_id",
    "save_run",
    "summarise",
    "summarise_repeats",
]
