"""Natural language -> operation plan. The experimental interpretation layer.

The shape mirrors the stable path's ``cad_ai.generation`` on purpose, so the
two are comparable: one call, no repair, no retry, and five outcomes that keep
"the model answered" strictly apart from "the answer is usable".

It reuses the stable path's provider boundary (``cad_ai.provider``) rather
than defining a second one -- that boundary is already vendor-neutral, already
tested, and already refuses to carry tools. What this module supplies is a
different prompt and a different parser, which is exactly the experiment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    TextToCadModel,
)

from .config import MAX_OUTPUT_TOKENS, ExperimentalConfig
from .plan import OperationPlan, PlanStatus, plan_schema
from .prompt import PROMPT_VERSION, prompt_fingerprint, system_prompt
from .parser import PlanParseError, parse_plan_text
from .validation import PlanValidation, validate_plan


class PlanOutcome(Enum):
    """What came of one attempt. The model's vocabulary is only part of it."""

    #: A plan was produced and passed the experimental plan validator.
    GENERATED = "generated"

    #: The model asked for missing information.
    NEEDS_CLARIFICATION = "needs_clarification"

    #: The model declined: the request needs an unimplemented operation.
    UNSUPPORTED = "unsupported"

    #: The provider returned nothing usable. Nothing was interpreted, and
    #: this says nothing at all about the model's ability.
    MODEL_ERROR = "model_error"

    #: The model answered, but the answer is not a usable plan. Unreachable
    #: from anything the model claims about itself.
    INVALID_MODEL_OUTPUT = "invalid_model_output"


#: The statuses a model may claim, mapped to outcomes. ``MODEL_ERROR`` and
#: ``INVALID_MODEL_OUTPUT`` are deliberately absent: a model cannot award
#: itself either one.
STATUS_OUTCOMES: Mapping[PlanStatus, PlanOutcome] = {
    PlanStatus.GENERATED: PlanOutcome.GENERATED,
    PlanStatus.NEEDS_CLARIFICATION: PlanOutcome.NEEDS_CLARIFICATION,
    PlanStatus.UNSUPPORTED: PlanOutcome.UNSUPPORTED,
}


@dataclass(frozen=True)
class PlanGenerationMetadata:
    """What was asked, and of what. Never a credential, never a raw key."""

    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    prompt_fingerprint: str = field(default_factory=prompt_fingerprint)
    structured_output: bool = False
    stop_reason: Optional[str] = None
    usage: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "structured_output": self.structured_output,
            "stop_reason": self.stop_reason,
            "usage": dict(self.usage),
        }


@dataclass(frozen=True)
class PlanGenerationResult:
    """One attempt, in full: what happened and why.

    ``raw_text`` is kept because a measurement of a model is worthless
    without the actual output. It is model-authored text of unknown
    provenance and is treated as data everywhere -- never executed, and never
    echoed into an HTTP payload.
    """

    outcome: PlanOutcome
    metadata: PlanGenerationMetadata
    plan: Optional[OperationPlan] = None
    plan_validation: Optional[PlanValidation] = None
    raw_text: Optional[str] = None
    error: Optional[str] = None
    error_detail: Optional[str] = None
    error_kind: Optional[ProviderErrorKind] = None

    @property
    def answered(self) -> bool:
        """Whether the model actually replied. A rate limit is not an answer."""
        return self.outcome is not PlanOutcome.MODEL_ERROR

    def to_dict(self, *, include_raw: bool = False) -> Dict[str, Any]:
        """A publishable record. ``raw_text`` and details are opt-in.

        The default omits both, because a public payload must not echo model
        text or a provider's own error string back to a caller.
        """
        payload: Dict[str, Any] = {
            "outcome": self.outcome.value,
            "metadata": self.metadata.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "plan_validation": (
                self.plan_validation.to_dict()
                if self.plan_validation is not None
                else None
            ),
            "error": self.error,
            "error_kind": (
                self.error_kind.value if self.error_kind is not None else None
            ),
        }
        if include_raw:
            payload["raw_text"] = self.raw_text
            payload["error_detail"] = self.error_detail
        return payload


class OperationPlanService:
    """Description in, :class:`PlanGenerationResult` out. One call."""

    def __init__(
        self, model: TextToCadModel, config: ExperimentalConfig
    ) -> None:
        self._model = model
        self._config = config

    @property
    def config(self) -> ExperimentalConfig:
        return self._config

    def generate(self, description: str) -> PlanGenerationResult:
        """Interpret one description. Never raises for a model's mistake."""
        text = (description or "").strip()
        metadata = PlanGenerationMetadata(
            provider=self._config.provider, model=self._config.model
        )

        if not text:
            # No provider call at all: there is nothing to interpret, and
            # spending a request to be told so is waste.
            return PlanGenerationResult(
                outcome=PlanOutcome.NEEDS_CLARIFICATION,
                metadata=metadata,
                plan=OperationPlan(
                    status=PlanStatus.NEEDS_CLARIFICATION,
                    summary="no description was given",
                    questions=("What part would you like to create?",),
                ),
                plan_validation=PlanValidation(valid=True),
            )

        request = ModelRequest(
            system=system_prompt(),
            user_text=text,
            output_schema=plan_schema(),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        try:
            response: ModelResponse = self._model.generate(request)
        except ProviderError as exc:
            return PlanGenerationResult(
                outcome=PlanOutcome.MODEL_ERROR,
                metadata=metadata,
                error=exc.message,
                error_detail=exc.detail,
                error_kind=exc.kind,
            )

        metadata = PlanGenerationMetadata(
            provider=response.provider,
            model=response.model,
            structured_output=response.structured_output,
            stop_reason=response.stop_reason,
            usage=response.usage,
        )

        try:
            plan = parse_plan_text(response.text)
        except PlanParseError as exc:
            return PlanGenerationResult(
                outcome=PlanOutcome.INVALID_MODEL_OUTPUT,
                metadata=metadata,
                raw_text=response.text,
                error=exc.message,
                error_detail=exc.detail,
            )

        validation = validate_plan(plan)
        if not validation.valid:
            return PlanGenerationResult(
                outcome=PlanOutcome.INVALID_MODEL_OUTPUT,
                metadata=metadata,
                plan=plan,
                plan_validation=validation,
                raw_text=response.text,
                error="the plan is not valid",
                error_detail="; ".join(
                    f"{p.code} {p.where} {p.message}".strip()
                    for p in validation.problems
                ),
            )

        return PlanGenerationResult(
            outcome=STATUS_OUTCOMES[plan.status],
            metadata=metadata,
            plan=plan,
            plan_validation=validation,
            raw_text=response.text,
        )


__all__ = [
    "STATUS_OUTCOMES",
    "OperationPlanService",
    "PlanGenerationMetadata",
    "PlanGenerationResult",
    "PlanOutcome",
]
