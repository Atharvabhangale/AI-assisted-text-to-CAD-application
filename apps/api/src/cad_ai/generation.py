"""Natural language to a validated, buildable CAD document: the interpretation layer.

```
text
 │  cad_ai.prompt.system_prompt()          the one prompt, identical every call
 │  cad_ai.provider.TextToCadModel         one call, no tools
model text
 │  json.loads                             plain parsing, nothing clever
candidate answer ───────────────────────── unusable ──────────┐
 │  cad_core.application_service           the EXISTING boundary              │
 │    validate_document   S-rules, unchanged ── rejected ─────┤
 │    build_document      E-rules, in a child ─ rejected ─────┤
accepted document                                             │
 │                                    compose_retry_text ◄────┘
AiGenerationResult                    (ask again, while attempts remain)
```

Four properties this module exists to guarantee:

* **the CAD document is authoritative, the prose is not.** They travel in
  separate fields and the prose is never read for meaning;
* **"the model answered" is not "the document is valid".**
  :attr:`GenerationOutcome.GENERATED` is set only after the existing validator
  says the candidate is valid and -- unless verification was switched off --
  the existing engine has built it;
* **nothing here decides validity or geometry.** Both verdicts come from
  :class:`~cad_core.application_service.CadApplicationService`: this module
  calls it and reads its structured errors. It implements no rule, holds no
  kernel, and never edits a document;
* **a rejected document is re-authored, never repaired.** When an answer is
  turned down, the only thing that changes it is the model, asked again with
  the reasons. No code in this layer patches, defaults, trims or re-orders a
  document to make it pass.

The loop is **bounded** (:data:`~cad_ai.config.DEFAULT_MAX_ATTEMPTS` model
calls unless configured otherwise) and **visible**: every model call that
returned an answer is one :class:`GenerationAttempt` in the result, saying
whether it was accepted and, if not, where and why it was turned down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.application_service import (
    OUTPUT_NAMES,
    BuildDocumentRequest,
    CadApplicationService,
    DocumentValidation,
    ServiceError,
    ServiceFailure,
)

from cad_ai.config import DEFAULT_MAX_ATTEMPTS
from cad_ai.prompt import PROMPT_VERSION, system_prompt
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    TextToCadModel,
)
from cad_ai.specification import response_schema

#: How long a model answer may be, **including the model's own reasoning**:
#: the default model thinks before it writes, and those tokens are counted
#: against this bound. A document is a few hundred tokens; the rest is room to
#: think. Bounded so a runaway generation still ends.
MAX_OUTPUT_TOKENS = 16000

#: The outputs the verification build requests: exactly the set the web page
#: asks ``POST /build`` for when it draws a generated part. Requesting the
#: same set gives the same build key, so the page's build is answered from the
#: cache instead of being built a second time.
VERIFICATION_OUTPUTS: Tuple[str, ...] = (
    "geometry",
    "step",
    "iges",
    "stl",
    "render",
)

#: How many assumptions are carried, and how long each may be. The model's
#: prose is bounded like every other field it writes.
MAX_ASSUMPTIONS = 12
MAX_ASSUMPTION_CHARACTERS = 300


class GenerationOutcome(Enum):
    """What happened, at the AI level. Five states, kept apart on purpose."""

    #: A candidate document was produced **and passed the existing
    #: validator** -- and, when verification is on, was built by the existing
    #: engine. The only outcome that carries a document.
    GENERATED = "generated"

    #: The request gives nothing to build from. Carries questions, not a
    #: document.
    NEEDS_CLARIFICATION = "needs_clarification"

    #: The request is not something this application produces.
    UNSUPPORTED = "unsupported"

    #: The provider did not return a usable response.
    MODEL_ERROR = "model_error"

    #: The model answered, but no answer it gave within the attempt limit was
    #: a valid, buildable CAD document -- unparseable, the wrong shape,
    #: rejected by the validator, or rejected by the engine.
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class AttemptStage(Enum):
    """Where a rejected attempt was rejected."""

    #: The answer itself was unusable: not one JSON object, an unknown
    #: status, a ``"document"`` status with no document, or a clarification
    #: with no question.
    ANSWER = "answer"

    #: The static validator rejected the document.
    VALIDATION = "validation"

    #: The verification build failed.
    GEOMETRY = "geometry"


#: The statuses the model may report, mapped to this layer's outcomes. A
#: status the model invents is not honoured: the answer is unusable.
MODEL_STATUSES: Mapping[str, GenerationOutcome] = {
    "document": GenerationOutcome.GENERATED,
    "needs_clarification": GenerationOutcome.NEEDS_CLARIFICATION,
    "unsupported": GenerationOutcome.UNSUPPORTED,
}

#: The stable public message for each outcome. A caller sees one of these
#: sentences, never a provider diagnostic, a traceback or a path.
PUBLIC_MESSAGES: Mapping[GenerationOutcome, str] = {
    GenerationOutcome.GENERATED: "a CAD document was generated from the description",
    GenerationOutcome.NEEDS_CLARIFICATION: (
        "the description is missing information needed to define the geometry"
    ),
    GenerationOutcome.UNSUPPORTED: (
        "the description asks for something this version cannot express"
    ),
    GenerationOutcome.MODEL_ERROR: (
        "the interpretation service is unavailable; the description was not "
        "interpreted"
    ),
    GenerationOutcome.INVALID_MODEL_OUTPUT: (
        "the description could not be turned into a valid CAD document"
    ),
}

#: The public sentences for an answer that could not be used at all. Fixed
#: text: the model's own words are never published as a reason.
UNUSABLE_ANSWER = "the interpretation service returned an unusable answer"
NO_DOCUMENT = "the interpretation service returned no CAD document"


@dataclass(frozen=True)
class GenerationAttempt:
    """One model call that returned an answer, and what became of it.

    Every string here is safe to publish: :attr:`issues` holds the
    validator's or the engine's own messages, or one of this module's fixed
    sentences -- never the model's raw text, a provider's text, a path or a
    traceback.
    """

    #: 1-based, in the order the calls were made.
    number: int

    #: ``True`` only for the attempt whose document was accepted.
    accepted: bool = False

    #: Where a rejected attempt was rejected. ``None`` when it was accepted,
    #: and ``None`` for a final clarification or refusal, which is an answer
    #: and not a rejection.
    stage: Optional[AttemptStage] = None

    #: Specification rule codes the validator or the engine reported.
    rule_codes: Tuple[str, ...] = ()

    #: Human-readable reasons for the rejection.
    issues: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "accepted": self.accepted,
            "stage": self.stage.value if self.stage is not None else None,
            "rule_codes": list(self.rule_codes),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class GenerationMetadata:
    """Minimal development metadata. No prompt text, no credential.

    The user's description is **not** recorded here: it is the caller's own
    input, and copying it into a result that may be logged is a privacy cost
    with no benefit at this stage. Its length is kept, which is enough to
    correlate a result with a request without storing the request.

    :attr:`provider`, :attr:`model`, :attr:`structured_output` and
    :attr:`stop_reason` describe the **last** call that returned an answer.
    :attr:`usage` and :attr:`model_calls` are totals over every call the
    request made.
    """

    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    structured_output: bool = False
    stop_reason: Optional[str] = None

    #: Token counts summed over every call that returned an answer, under the
    #: names the provider reported them by -- including the cache counters,
    #: when a provider reports those.
    usage: Mapping[str, int] = field(default_factory=dict)
    request_characters: int = 0

    #: How many model calls the request made, counting one that failed. A
    #: request is billed for these, so the number is reported rather than
    #: left to be inferred from the attempt list (which omits a failed call).
    model_calls: int = 0

    #: Whether an accepted document was built before being returned. ``False``
    #: when verification was switched off, and for every outcome that carries
    #: no document.
    build_verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "structured_output": self.structured_output,
            "stop_reason": self.stop_reason,
            "usage": dict(self.usage),
            "request_characters": self.request_characters,
            "model_calls": self.model_calls,
            "build_verified": self.build_verified,
        }


@dataclass(frozen=True)
class AiGenerationResult:
    """The AI layer's answer. The document is separate from every word of prose.

    :attr:`candidate_document` is the canonical serialization of the validated
    part -- so what a caller receives is a document the existing validator
    already accepted, in the form the rest of the system uses. It is ``None``
    for every outcome but :data:`GenerationOutcome.GENERATED`.
    """

    outcome: GenerationOutcome

    #: The stable public sentence. Safe to show anyone.
    message: str

    #: The validated candidate CAD document, or ``None``.
    candidate_document: Optional[Mapping[str, Any]] = None

    #: The canonical document hash, when there is a document.
    document_hash: Optional[str] = None

    #: The model's one-sentence explanation. **Explanatory only.** Never read
    #: for geometric meaning by anything in this system.
    summary: Optional[str] = None

    #: Questions to put to the user, for ``NEEDS_CLARIFICATION``.
    questions: Tuple[str, ...] = ()

    #: Why the request could not be served, for ``UNSUPPORTED`` and for a
    #: final rejection. For a rejection these repeat the last attempt's
    #: :attr:`GenerationAttempt.issues`.
    issues: Tuple[str, ...] = ()

    #: Specification rule codes, when the last attempt was rejected by the
    #: validator or the engine.
    rule_codes: Tuple[str, ...] = ()

    #: Why the provider produced nothing, for ``MODEL_ERROR``. A
    #: vendor-neutral category, safe to publish -- it names a class of
    #: failure, never a provider's own status text. ``None`` otherwise.
    error_kind: Optional[ProviderErrorKind] = None

    #: The choices the model says it made that the description did not state:
    #: an assumed unit, a chosen dimension, a simplified shape. The model's
    #: own words, bounded, **explanatory only** -- carried so a guess is
    #: visible to the user, and never read for meaning. Non-empty only for
    #: ``GENERATED``.
    assumptions: Tuple[str, ...] = ()

    #: One record per model call that returned an answer, in order. A call
    #: that raised is not a record: the result is then ``MODEL_ERROR`` with
    #: whatever earlier records exist.
    attempts: Tuple[GenerationAttempt, ...] = ()

    metadata: GenerationMetadata = field(default_factory=GenerationMetadata)

    #: Development-only diagnostic. **Never** part of a public payload:
    #: excluded from :meth:`to_dict`, and it may quote the model's raw text or
    #: a provider's exception class name.
    detail: Optional[str] = None

    @property
    def generated(self) -> bool:
        return self.outcome is GenerationOutcome.GENERATED

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-compatible data, safe to send. Excludes :attr:`detail`."""
        return {
            "outcome": self.outcome.value,
            "message": self.message,
            "document": (
                dict(self.candidate_document)
                if self.candidate_document is not None
                else None
            ),
            "document_hash": self.document_hash,
            "summary": self.summary,
            "questions": list(self.questions),
            "issues": list(self.issues),
            "rule_codes": list(self.rule_codes),
            "error_kind": (
                self.error_kind.value if self.error_kind is not None else None
            ),
            "assumptions": list(self.assumptions),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True)
class Rejection:
    """Why one answer was turned down, in two registers.

    :attr:`issues` and :attr:`rule_codes` are the public record, copied into
    a :class:`GenerationAttempt`. :attr:`feedback` is what the model is told
    on the re-ask: the same reasons with rule codes, feature ids and field
    paths attached, plus -- for an unusable answer -- what was wrong with its
    shape, which is no use to a user and so is not published.
    """

    stage: AttemptStage
    issues: Tuple[str, ...] = ()
    rule_codes: Tuple[str, ...] = ()

    #: One line per reason, written for the model.
    feedback: Tuple[str, ...] = ()

    #: The document the model sent, when it sent one. Returned to the model
    #: verbatim on the re-ask and to nobody else.
    document: Optional[Mapping[str, Any]] = None

    #: Whether asking the model again could plausibly help. ``False`` when
    #: the failure was the build infrastructure's and not the document's.
    retryable: bool = True

    #: Development-only diagnostic for :attr:`AiGenerationResult.detail`.
    detail: Optional[str] = None


#: How each stage is named to the model on a re-ask.
_STAGE_DESCRIPTIONS: Mapping[AttemptStage, str] = {
    AttemptStage.ANSWER: (
        "the answer itself was unusable, before any document was checked"
    ),
    AttemptStage.VALIDATION: (
        "the validator rejected the document against the static rules"
    ),
    AttemptStage.GEOMETRY: (
        "the document was valid, but the CAD engine could not build it"
    ),
}

#: The line that separates the user's description from the feedback on a
#: re-ask. Fixed text, so a test can find it.
RETRY_MARKER = "=== END OF THE REQUEST. WHAT FOLLOWS IS FEEDBACK, NOT A REQUEST. ==="


def compose_retry_text(description: str, rejection: Rejection) -> str:
    """The user text for a re-ask: the description, then why the last answer failed.

    The provider interface carries one system prompt and one user text, and
    no conversation. So a re-ask is one fresh, self-contained user text:

    1. the user's description, **verbatim and first** -- unchanged by this
       layer, exactly as on the first call;
    2. the rejected document as JSON, when the model sent one;
    3. each rejection reason, with its rule code, feature and field;
    4. an instruction to return a whole corrected answer, not a patch.

    The system prompt is not part of this and does not change between
    attempts, which is what lets a provider cache it.
    """
    lines: List[str] = [
        description,
        "",
        RETRY_MARKER,
        "",
        "Your previous answer to the request above was rejected: "
        + _STAGE_DESCRIPTIONS[rejection.stage]
        + ".",
    ]
    if rejection.document is not None:
        lines += [
            "",
            "The document you sent:",
            json.dumps(rejection.document, ensure_ascii=False),
        ]
    lines += ["", "Why it was rejected:"]
    lines += [f"- {reason}" for reason in rejection.feedback] or [
        "- no further detail is available"
    ]
    lines += [
        "",
        "Answer the request again. Return a complete answer in the same "
        "response format, with a full corrected document -- not a patch and "
        "not a description of the change. Fix the cause of every reason "
        "above, re-check the rest of the document against the same rules, "
        "and keep everything the request asked for. If a feature cannot be "
        "made to build, leave it out and say so in `assumptions` rather than "
        "sending it again unchanged.",
    ]
    return "\n".join(lines)


def _texts(value: Any, limit: int = 8, characters: Optional[int] = None) -> Tuple[str, ...]:
    """Read a list-of-strings field defensively. Model output is untrusted."""
    if not isinstance(value, list):
        return ()
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if characters is not None:
        items = [item[:characters] for item in items]
    return tuple(items[:limit])


def _summary(value: Any) -> Optional[str]:
    """The model's prose, bounded. Not parsed, not interpreted, just carried."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:500] if text else None


@dataclass(frozen=True)
class _Answer:
    """What one model answer came to. Exactly one of the three is set."""

    #: A clarification or a refusal: final, never re-asked.
    outcome: Optional[GenerationOutcome] = None

    #: An accepted document.
    validation: Optional[DocumentValidation] = None

    #: A rejection, to be recorded and possibly re-asked.
    rejection: Optional[Rejection] = None

    summary: Optional[str] = None
    questions: Tuple[str, ...] = ()
    issues: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()


class TextToCadService:
    """Turns a description into a validated, buildable candidate CAD document.

    Holds a model and the existing application service. It owns no CAD logic,
    no validation rule and no geometry: it calls the model, hands each answer
    to :meth:`~cad_core.application_service.CadApplicationService.validate_document`
    and then to :meth:`~cad_core.application_service.CadApplicationService.build_document`,
    and asks the model again when either turns the answer down.

    Args:
        model: The provider to call.
        service: The application service that validates and builds.
        max_output_tokens: The bound on one answer, reasoning included.
        max_attempts: The most model calls one description may cost. ``1``
            is single-shot interpretation with no re-ask, which is what the
            evaluation harness measures.
        verify_build: Whether an accepted document must also build. Off, a
            document is returned on the validator's word alone and no child
            process runs; the result's metadata says which it was.
        verification_outputs: The outputs the verification build requests.
            The default is the set the web page builds, so its build is a
            cache hit.
    """

    def __init__(
        self,
        model: TextToCadModel,
        service: CadApplicationService,
        *,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        verify_build: bool = True,
        verification_outputs: Sequence[str] = VERIFICATION_OUTPUTS,
    ) -> None:
        if not isinstance(service, CadApplicationService):
            raise TypeError(
                "expected a CadApplicationService; got "
                f"{type(service).__name__}"
            )
        if not callable(getattr(model, "generate", None)):
            raise TypeError("a text-to-CAD model must have a generate method")
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 1
        ):
            raise ValueError(
                f"max_attempts must be an integer >= 1; got {max_attempts!r}"
            )
        if isinstance(verification_outputs, (str, bytes)):
            raise ValueError(
                "verification_outputs is a sequence of output names, not one "
                "string"
            )
        outputs = tuple(verification_outputs)
        unknown = [name for name in outputs if name not in OUTPUT_NAMES]
        if not outputs or unknown:
            raise ValueError(
                "verification_outputs must name at least one of "
                f"{', '.join(OUTPUT_NAMES)}; got {list(outputs)!r}"
            )
        self._model = model
        self._service = service
        self._max_output_tokens = int(max_output_tokens)
        self._max_attempts = max_attempts
        self._verify_build = bool(verify_build)
        self._verification_outputs = outputs

    @property
    def service(self) -> CadApplicationService:
        return self._service

    @property
    def model(self) -> TextToCadModel:
        return self._model

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def verify_build(self) -> bool:
        return self._verify_build

    @property
    def verification_outputs(self) -> Tuple[str, ...]:
        return self._verification_outputs

    def generate_cad_from_text(self, text: str) -> AiGenerationResult:
        """Interpret one description, asking again while answers are rejected.

        At most :attr:`max_attempts` model calls. Each answer ends the request
        or earns one more call:

        * the provider raises -> ``MODEL_ERROR``, at once. Never re-asked:
          asking again does not fix an outage, and every call costs money;
        * a clarification or a refusal -> returned as it is. It is an answer;
        * an unusable answer, a validator rejection or a failed verification
          build -> recorded, and the model is asked again with the reasons
          while attempts remain;
        * a document that validates and builds -> ``GENERATED``.

        Never raises for an ordinary failure: an unavailable provider, an
        unparseable answer and an invalid document are all results.
        """
        if not isinstance(text, str) or not text.strip():
            return AiGenerationResult(
                outcome=GenerationOutcome.NEEDS_CLARIFICATION,
                message=PUBLIC_MESSAGES[GenerationOutcome.NEEDS_CLARIFICATION],
                questions=("What part would you like to create?",),
                metadata=GenerationMetadata(request_characters=len(text or "")),
            )

        # The same system text and schema go with every attempt. Only the
        # user text changes on a re-ask, and its first part is always the
        # description exactly as the user wrote it: no unit conversion, no
        # number rewriting, no paraphrasing happens in this layer.
        system = system_prompt()
        schema = response_schema()
        user_text = text
        attempts: List[GenerationAttempt] = []
        usage: Dict[str, int] = {}
        calls = 0
        last: Optional[ModelResponse] = None
        rejection: Optional[Rejection] = None
        summary: Optional[str] = None

        def metadata(*, verified: bool = False) -> GenerationMetadata:
            return GenerationMetadata(
                provider=last.provider if last is not None else None,
                model=last.model if last is not None else None,
                structured_output=(
                    last.structured_output if last is not None else False
                ),
                stop_reason=last.stop_reason if last is not None else None,
                usage=dict(usage),
                request_characters=len(text),
                model_calls=calls,
                build_verified=verified,
            )

        for number in range(1, self._max_attempts + 1):
            request = ModelRequest(
                system=system,
                user_text=user_text,
                output_schema=schema,
                max_output_tokens=self._max_output_tokens,
            )
            calls += 1
            try:
                response = self._model.generate(request)
            except ProviderError as exc:
                return AiGenerationResult(
                    outcome=GenerationOutcome.MODEL_ERROR,
                    message=PUBLIC_MESSAGES[GenerationOutcome.MODEL_ERROR],
                    attempts=tuple(attempts),
                    metadata=metadata(),
                    error_kind=exc.kind,
                    detail=exc.detail or exc.message,
                )
            last = response
            for name, value in response.usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[name] = usage.get(name, 0) + value

            answer = self._interpret(response)
            summary = answer.summary

            if answer.outcome is not None:
                attempts.append(GenerationAttempt(number=number))
                return AiGenerationResult(
                    outcome=answer.outcome,
                    message=PUBLIC_MESSAGES[answer.outcome],
                    summary=answer.summary,
                    questions=answer.questions,
                    issues=answer.issues,
                    attempts=tuple(attempts),
                    metadata=metadata(),
                )

            if answer.validation is not None:
                attempts.append(GenerationAttempt(number=number, accepted=True))
                return AiGenerationResult(
                    outcome=GenerationOutcome.GENERATED,
                    message=PUBLIC_MESSAGES[GenerationOutcome.GENERATED],
                    candidate_document=answer.validation.document,
                    document_hash=answer.validation.document_hash,
                    summary=answer.summary,
                    assumptions=answer.assumptions,
                    attempts=tuple(attempts),
                    metadata=metadata(verified=self._verify_build),
                )

            rejection = answer.rejection
            assert rejection is not None
            attempts.append(
                GenerationAttempt(
                    number=number,
                    stage=rejection.stage,
                    rule_codes=rejection.rule_codes,
                    issues=rejection.issues,
                )
            )
            if not rejection.retryable:
                break
            user_text = compose_retry_text(text, rejection)

        assert rejection is not None
        return AiGenerationResult(
            outcome=GenerationOutcome.INVALID_MODEL_OUTPUT,
            message=PUBLIC_MESSAGES[GenerationOutcome.INVALID_MODEL_OUTPUT],
            summary=summary,
            issues=rejection.issues,
            rule_codes=rejection.rule_codes,
            attempts=tuple(attempts),
            metadata=metadata(),
            detail=rejection.detail,
        )

    # --- interpretation ---------------------------------------------------

    def _interpret(self, response: ModelResponse) -> _Answer:
        """Read one model answer. Decides nothing about CAD."""
        payload = self._parse(response.text)
        if payload is None:
            return _unusable(
                UNUSABLE_ANSWER,
                feedback=(
                    "the answer was not a single JSON object in the response "
                    "format; return exactly one JSON object and nothing else "
                    "-- no prose, no code fence",
                ),
                detail=f"model text was not a JSON object: {response.text[:400]!r}",
            )

        status = payload.get("status")
        outcome = MODEL_STATUSES.get(status) if isinstance(status, str) else None
        if outcome is None:
            return _unusable(
                UNUSABLE_ANSWER,
                feedback=(
                    "`status` was not one of "
                    + ", ".join(sorted(MODEL_STATUSES)),
                ),
                detail=f"unknown status {status!r}",
            )

        summary = _summary(payload.get("summary"))
        if outcome is GenerationOutcome.NEEDS_CLARIFICATION:
            questions = _texts(payload.get("questions"))
            if not questions:
                # A clarification request with no question is useless; the
                # answer is unusable rather than an empty ask.
                return _unusable(
                    UNUSABLE_ANSWER,
                    feedback=(
                        "`status` was needs_clarification but `questions` "
                        "held no question",
                    ),
                    detail="needs_clarification with no questions",
                )
            return _Answer(outcome=outcome, summary=summary, questions=questions)
        if outcome is GenerationOutcome.UNSUPPORTED:
            return _Answer(
                outcome=outcome,
                summary=summary,
                issues=_texts(payload.get("issues")),
            )

        candidate = payload.get("document")
        if not isinstance(candidate, dict):
            return _unusable(
                NO_DOCUMENT,
                feedback=(
                    "`status` was document but `document` was not a CAD "
                    "document object; write the whole document in `document`",
                ),
                detail=f"document field was {type(candidate).__name__}",
                summary=summary,
            )
        return self._check(
            candidate,
            summary,
            _texts(
                payload.get("assumptions"),
                limit=MAX_ASSUMPTIONS,
                characters=MAX_ASSUMPTION_CHARACTERS,
            ),
        )

    @staticmethod
    def _parse(text: Any) -> Optional[Dict[str, Any]]:
        """Read the model's text as one JSON object, or give up.

        Deliberately unforgiving: no fenced-code stripping, no substring
        hunting for the first ``{``, no repair. A provider asked for JSON that
        answers with prose has failed, and pretending otherwise is how prose
        starts to become CAD. The model is told so and asked again.
        """
        if not isinstance(text, str):
            return None
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _check(
        self,
        candidate: Mapping[str, Any],
        summary: Optional[str],
        assumptions: Tuple[str, ...],
    ) -> _Answer:
        """Hand the candidate to the **existing** boundary: validate, then build.

        No rule is checked here and no document is changed. The service
        deserializes and validates; if that passes and verification is on,
        the same service builds the canonical document in its isolated child
        process, which is the only place the geometric rules can be decided.
        """
        validation: DocumentValidation = self._service.validate_document(
            dict(candidate)
        )
        if not validation.valid:
            return _Answer(
                rejection=_validation_rejection(candidate, validation.error),
                summary=summary,
            )
        if not self._verify_build:
            return _Answer(
                validation=validation, summary=summary, assumptions=assumptions
            )
        built = self._service.build_document(
            BuildDocumentRequest(
                document=validation.document,
                outputs=self._verification_outputs,
            )
        )
        if not built.succeeded:
            return _Answer(
                rejection=_build_rejection(candidate, built.error),
                summary=summary,
            )
        return _Answer(
            validation=validation, summary=summary, assumptions=assumptions
        )


def _unusable(
    issue: str,
    *,
    feedback: Sequence[str],
    detail: str,
    summary: Optional[str] = None,
) -> _Answer:
    """An answer rejected for its shape, before any document was checked."""
    return _Answer(
        rejection=Rejection(
            stage=AttemptStage.ANSWER,
            issues=(issue,),
            feedback=tuple(feedback),
            detail=detail,
        ),
        summary=summary,
    )


def _validation_rejection(
    candidate: Mapping[str, Any], error: Optional[ServiceError]
) -> Rejection:
    """The validator's own verdict, as a rejection. Adds no rule of its own."""
    entries = tuple(error.validation_errors) if error is not None else ()
    issues = tuple(
        str(item.get("message", "")) for item in entries if item.get("message")
    )
    feedback: List[str] = []
    for item in entries:
        where: List[str] = []
        if item.get("feature_id"):
            where.append(f"feature {item['feature_id']!r}")
        if item.get("feature_index") is not None:
            where.append(f"features[{item['feature_index']}]")
        if item.get("field_path"):
            where.append(f"field {item['field_path']}")
        located = f" ({', '.join(where)})" if where else ""
        feedback.append(
            f"rule {item.get('rule', '?')}{located}: {item.get('message', '')}"
        )
    message = error.message if error is not None else "the document is invalid"
    return Rejection(
        stage=AttemptStage.VALIDATION,
        issues=issues or (message,),
        rule_codes=tuple(error.rule_codes) if error is not None else (),
        feedback=tuple(feedback) or (message,),
        document=dict(candidate),
        detail=message,
    )


def _build_rejection(
    candidate: Mapping[str, Any], error: Optional[ServiceError]
) -> Rejection:
    """The engine's own verdict, as a rejection.

    A geometry failure is the document's: the model is asked again. Any other
    build failure -- a crashed or timed-out build process, an unusable cache,
    an exporter fault -- is the infrastructure's, and no other document would
    fare better, so it ends the request instead of spending another call.
    Both are recorded at the geometry stage, because that is where the
    attempt stopped.
    """
    message = error.message if error is not None else "the build failed"
    codes = tuple(error.rule_codes) if error is not None else ()
    geometric = error is not None and error.failure is ServiceFailure.GEOMETRY_FAILED
    prefix = f"rule {', '.join(codes)}: " if codes else ""
    return Rejection(
        stage=AttemptStage.GEOMETRY,
        issues=(message,),
        rule_codes=codes,
        feedback=(prefix + message,),
        document=dict(candidate),
        retryable=geometric,
        detail=message,
    )


__all__ = [
    "MAX_ASSUMPTIONS",
    "MAX_ASSUMPTION_CHARACTERS",
    "MAX_OUTPUT_TOKENS",
    "MODEL_STATUSES",
    "NO_DOCUMENT",
    "PUBLIC_MESSAGES",
    "RETRY_MARKER",
    "UNUSABLE_ANSWER",
    "VERIFICATION_OUTPUTS",
    "AiGenerationResult",
    "AttemptStage",
    "GenerationAttempt",
    "GenerationMetadata",
    "GenerationOutcome",
    "Rejection",
    "TextToCadService",
    "compose_retry_text",
]
