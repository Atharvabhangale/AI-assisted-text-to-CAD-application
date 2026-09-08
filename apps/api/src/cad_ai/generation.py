"""Natural language to a validated CAD document: the interpretation layer.

```
text
 │  cad_ai.prompt.system_prompt()          the one prompt
 │  cad_ai.provider.TextToCadModel         one call, no tools
model text
 │  json.loads                             plain parsing, nothing clever
candidate response
 │  cad_core.application_service           the EXISTING boundary
 │    -> deserialize_part -> validate()      S1-S20, unchanged
AiGenerationResult
```

Three properties this module exists to guarantee:

* **the CAD document is authoritative, the prose is not.** They travel in
  separate fields and the prose is never read for meaning;
* **"the model answered" is not "the document is valid".**
  :attr:`GenerationOutcome.GENERATED` is set only after the existing validator
  says the candidate is valid;
* **no CAD kernel runs here.** This module reaches the validator through
  :meth:`~cad_core.application_service.CadApplicationService.validate_document`,
  which builds nothing and starts no child process. Building is the caller's
  separate, deliberate step.

There is **one attempt**. No repair loop, no retry with the validator's
complaints, no second call. That is a later stage, and leaving it out is what
makes this stage's failure behaviour measurable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from cad_core.application_service import CadApplicationService, DocumentValidation

from cad_ai.prompt import PROMPT_VERSION, system_prompt
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    TextToCadModel,
)
from cad_ai.specification import response_schema

#: How long a model answer may be. A CAD document for this stage's subset is a
#: few hundred bytes; this is generous and still bounded.
MAX_OUTPUT_TOKENS = 4096


class GenerationOutcome(Enum):
    """What happened, at the AI level. Five states, kept apart on purpose."""

    #: A candidate document was produced **and passed the existing
    #: validator**. The only outcome that carries a document.
    GENERATED = "generated"

    #: The request is missing geometry information the specification does not
    #: default. Carries questions, not a document.
    NEEDS_CLARIFICATION = "needs_clarification"

    #: The request cannot be expressed in the supported subset.
    UNSUPPORTED = "unsupported"

    #: The provider did not return a usable response. Nothing was interpreted.
    MODEL_ERROR = "model_error"

    #: The model answered, but its answer is not a valid V1 CAD document --
    #: unparseable, the wrong shape, or rejected by the validator.
    INVALID_MODEL_OUTPUT = "invalid_model_output"


#: The statuses the model may report, mapped to this layer's outcomes. A
#: status the model invents is not honoured: it falls through to
#: ``INVALID_MODEL_OUTPUT``.
MODEL_STATUSES: Mapping[str, GenerationOutcome] = {
    "document": GenerationOutcome.GENERATED,
    "needs_clarification": GenerationOutcome.NEEDS_CLARIFICATION,
    "unsupported": GenerationOutcome.UNSUPPORTED,
}

#: The stable public message for each non-success outcome. A caller sees one of
#: these sentences, never a provider diagnostic, a traceback or a path.
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


@dataclass(frozen=True)
class GenerationMetadata:
    """Minimal development metadata. No prompt text, no credential.

    The user's description is **not** recorded here: it is the caller's own
    input, and copying it into a result that may be logged is a privacy cost
    with no benefit at this stage. Its length is kept, which is enough to
    correlate a result with a request without storing the request.
    """

    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    structured_output: bool = False
    stop_reason: Optional[str] = None
    usage: Mapping[str, int] = field(default_factory=dict)
    request_characters: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "structured_output": self.structured_output,
            "stop_reason": self.stop_reason,
            "usage": dict(self.usage),
            "request_characters": self.request_characters,
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
    #: validator rejection. For a rejection these are the validator's own
    #: messages, which name rules and fields and nothing internal.
    issues: Tuple[str, ...] = ()

    #: Specification rule codes, when the validator rejected the candidate.
    rule_codes: Tuple[str, ...] = ()

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
            "metadata": self.metadata.to_dict(),
        }


def _texts(value: Any, limit: int = 8) -> Tuple[str, ...]:
    """Read a list-of-strings field defensively. Model output is untrusted."""
    if not isinstance(value, list):
        return ()
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return tuple(items[:limit])


def _summary(value: Any) -> Optional[str]:
    """The model's prose, bounded. Not parsed, not interpreted, just carried."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:500] if text else None


class TextToCadService:
    """Turns a description into a validated candidate CAD document.

    Holds a model and the existing application service. It owns no CAD logic,
    no validation rule and no geometry: the only thing it knows how to do is
    call one model once and hand the answer to
    :meth:`~cad_core.application_service.CadApplicationService.validate_document`.
    """

    def __init__(
        self,
        model: TextToCadModel,
        service: CadApplicationService,
        *,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        if not isinstance(service, CadApplicationService):
            raise TypeError(
                "expected a CadApplicationService; got "
                f"{type(service).__name__}"
            )
        if not callable(getattr(model, "generate", None)):
            raise TypeError("a text-to-CAD model must have a generate method")
        self._model = model
        self._service = service
        self._max_output_tokens = int(max_output_tokens)

    @property
    def service(self) -> CadApplicationService:
        return self._service

    @property
    def model(self) -> TextToCadModel:
        return self._model

    def generate_cad_from_text(self, text: str) -> AiGenerationResult:
        """One generation attempt, then the existing validation boundary.

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

        # The user's text goes to the model as written. No unit conversion, no
        # number rewriting, no paraphrasing: normalising the request before
        # interpretation would move meaning out of the model's view and into
        # undocumented code.
        request = ModelRequest(
            system=system_prompt(),
            user_text=text,
            output_schema=response_schema(),
            max_output_tokens=self._max_output_tokens,
        )
        metadata = GenerationMetadata(request_characters=len(text))
        try:
            response = self._model.generate(request)
        except ProviderError as exc:
            return AiGenerationResult(
                outcome=GenerationOutcome.MODEL_ERROR,
                message=PUBLIC_MESSAGES[GenerationOutcome.MODEL_ERROR],
                metadata=metadata,
                detail=exc.detail or exc.message,
            )
        return self._interpret(response, metadata)

    # --- interpretation ---------------------------------------------------

    def _interpret(
        self, response: ModelResponse, metadata: GenerationMetadata
    ) -> AiGenerationResult:
        metadata = GenerationMetadata(
            provider=response.provider,
            model=response.model,
            structured_output=response.structured_output,
            stop_reason=response.stop_reason,
            usage=dict(response.usage),
            request_characters=metadata.request_characters,
        )
        payload = self._parse(response.text)
        if payload is None:
            return self._invalid(
                metadata,
                issues=("the interpretation service returned an unusable answer",),
                detail=f"model text was not a JSON object: {response.text[:400]!r}",
            )

        status = payload.get("status")
        outcome = MODEL_STATUSES.get(status) if isinstance(status, str) else None
        if outcome is None:
            return self._invalid(
                metadata,
                issues=("the interpretation service returned an unusable answer",),
                detail=f"unknown status {status!r}",
            )

        summary = _summary(payload.get("summary"))
        if outcome is GenerationOutcome.NEEDS_CLARIFICATION:
            questions = _texts(payload.get("questions"))
            if not questions:
                # A clarification request with no question is useless; treat
                # the answer as unusable rather than returning an empty ask.
                return self._invalid(
                    metadata,
                    issues=("the interpretation service returned an unusable answer",),
                    detail="needs_clarification with no questions",
                )
            return AiGenerationResult(
                outcome=outcome,
                message=PUBLIC_MESSAGES[outcome],
                summary=summary,
                questions=questions,
                metadata=metadata,
            )
        if outcome is GenerationOutcome.UNSUPPORTED:
            return AiGenerationResult(
                outcome=outcome,
                message=PUBLIC_MESSAGES[outcome],
                summary=summary,
                issues=_texts(payload.get("issues")),
                metadata=metadata,
            )

        candidate = payload.get("document")
        if not isinstance(candidate, dict):
            return self._invalid(
                metadata,
                issues=("the interpretation service returned no CAD document",),
                detail=f"document field was {type(candidate).__name__}",
            )
        return self._validate(candidate, summary, metadata)

    @staticmethod
    def _parse(text: Any) -> Optional[Dict[str, Any]]:
        """Read the model's text as one JSON object, or give up.

        Deliberately unforgiving: no fenced-code stripping, no substring
        hunting for the first ``{``, no repair. A provider asked for JSON that
        answers with prose has failed, and pretending otherwise is how prose
        starts to become CAD.
        """
        if not isinstance(text, str):
            return None
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _validate(
        self,
        candidate: Mapping[str, Any],
        summary: Optional[str],
        metadata: GenerationMetadata,
    ) -> AiGenerationResult:
        """Hand the candidate to the **existing** validation boundary.

        No rule is checked here and no document is repaired. The service
        deserializes and validates; nothing is built.
        """
        validation: DocumentValidation = self._service.validate_document(
            dict(candidate)
        )
        if not validation.valid:
            error = validation.error
            issues = tuple(
                str(item.get("message", ""))
                for item in (error.validation_errors if error else ())
                if item.get("message")
            )
            return AiGenerationResult(
                outcome=GenerationOutcome.INVALID_MODEL_OUTPUT,
                message=PUBLIC_MESSAGES[GenerationOutcome.INVALID_MODEL_OUTPUT],
                summary=summary,
                issues=issues or ((error.message,) if error else ()),
                rule_codes=tuple(error.rule_codes) if error else (),
                metadata=metadata,
                detail=None if error is None else error.message,
            )
        return AiGenerationResult(
            outcome=GenerationOutcome.GENERATED,
            message=PUBLIC_MESSAGES[GenerationOutcome.GENERATED],
            candidate_document=validation.document,
            document_hash=validation.document_hash,
            summary=summary,
            metadata=metadata,
        )

    @staticmethod
    def _invalid(
        metadata: GenerationMetadata,
        *,
        issues: Sequence[str],
        detail: Optional[str],
    ) -> AiGenerationResult:
        return AiGenerationResult(
            outcome=GenerationOutcome.INVALID_MODEL_OUTPUT,
            message=PUBLIC_MESSAGES[GenerationOutcome.INVALID_MODEL_OUTPUT],
            issues=tuple(issues),
            metadata=metadata,
            detail=detail,
        )


__all__ = [
    "MAX_OUTPUT_TOKENS",
    "MODEL_STATUSES",
    "PUBLIC_MESSAGES",
    "AiGenerationResult",
    "GenerationMetadata",
    "GenerationOutcome",
    "TextToCadService",
]
