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
from .plan import (
    OperationPlan,
    PlanStatus,
    strict_selector_provider_schema,
)
from .prompt import PROMPT_VERSION, prompt_fingerprint, system_prompt
from .parser import PlanParseError, parse_plan_text
from .schema_ladder import grammar_metrics
from .validation import PlanValidation, validate_plan

#: Which encoding of the plan this route asks the model to decode against.
#: Recorded on every answer: a narrowed grammar that went unnamed would make
#: a refusal indistinguishable from an inexpressible request, which is the
#: mistake Stage 44 made and Stage 48 found again.
PLAN_SCHEMA_NAME = "strict_selector"

#: Computed once, from the one definition in :mod:`schema_ladder`, so this
#: cannot drift from what the ladder measures.
_PLAN_SCHEMA_METRICS = grammar_metrics(strict_selector_provider_schema())
PLAN_SCHEMA_FINGERPRINT: str = _PLAN_SCHEMA_METRICS["fingerprint"]
PLAN_SCHEMA_INLINED: int = _PLAN_SCHEMA_METRICS["inlined_characters"]


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
    plan_schema: str = PLAN_SCHEMA_NAME
    plan_schema_fingerprint: str = PLAN_SCHEMA_FINGERPRINT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "structured_output": self.structured_output,
            "stop_reason": self.stop_reason,
            "usage": dict(self.usage),
            "plan_schema": self.plan_schema,
            "plan_schema_fingerprint": self.plan_schema_fingerprint,
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
            # `provider_schema` stood here and the provider REFUSED it on
            # every live call -- "The compiled grammar is too large" -- so
            # this route answered 503 for every request while the rest of
            # the stack was healthy. It measures 7351 inlined characters
            # against a ceiling Stages 50/51 bounded to (4481, 4551] by real
            # calls. Nothing was wrong with the model, the credential or the
            # transport; the encoding simply did not fit.
            #
            # This one does, and is the largest that both fits and answers
            # the requests this experiment makes. Measured 3619 inlined --
            # 862 below the proven-accepted 4481 -- and already exercised
            # live in Stage 55, where the provider compiled it and the model
            # scored 4/4 on naming a rim's end under this very prompt.
            #
            # Why the strict (branched) selector rather than the flat one,
            # which is smaller still at 3134: Stage 54 measured the flat
            # encoding at *0/4* on `selector_position_correct` -- an
            # optional field is one a grammar-constrained decoder declines
            # to use -- and Stage 55 measured 4/4 once each mode became its
            # own branch and the circular branch required `position`. A
            # top-rim chamfer is unreachable under the flat encoding in
            # practice, so the extra 485 characters buy the capability back.
            #
            # What this encoding cannot SAY, recorded rather than hidden:
            # `sketch`, `extrude` and `revolve` (which the execution
            # boundary refuses anyway, so no buildable part is lost);
            # `pattern`, which IS buildable and is a real narrowing --
            # adding it measures 4633, past the 4551 refusal, and the only
            # trim that would fit strips `axis`, which these selectors need;
            # and a circular selector with no `position`, meaning both rims,
            # which the parser still accepts from any other source.
            #
            # A schema is what the model may SAY, never what the engine can
            # BUILD, and the parser re-derives every per-type requirement
            # regardless. The chosen encoding is reported in the metadata so
            # a caller can tell which grammar produced an answer.
            output_schema=strict_selector_provider_schema(),
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
    "PLAN_SCHEMA_FINGERPRINT",
    "PLAN_SCHEMA_INLINED",
    "PLAN_SCHEMA_NAME",
    "OperationPlanService",
    "PlanGenerationMetadata",
    "PlanGenerationResult",
    "PlanOutcome",
]
