"""What a request was understood to mean, and by which route.

The provider boundary already carries no vendor types: a planner returns a
:class:`~cad_experimental.generation.PlanGenerationResult`, which names an
outcome and (sometimes) a plan, and never a response shape. What was missing
is the layer directly beneath it -- the one that decides whether that answer
is *usable*, and what to do when it is not.

Two routes, one destination
---------------------------
```
request --> provider (any)   --> Operation Plan --+
        \\                                         |
         -> canonical intent --> Operation Plan --+--> validate -> graph -> kernel
```

Both routes end at the same canonical Operation Plan, and the plan is parsed,
validated and executed identically whichever produced it. The deterministic
route is **not** a bypass: it reaches the pipeline at exactly the same place,
through the same parser, and is judged by the same validator.

What this module will not do
----------------------------
It does not know or ask which provider ran. :data:`SOURCE_PROVIDER` covers a
hosted model today and a local one tomorrow, and nothing here branches on
which. It reads no response field beyond the neutral result type, so a
provider whose wire format changes entirely does not reach this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .generation import PlanGenerationResult, PlanOutcome
from .intent import (
    IntentError,
    looks_like_plate_assembly,
    PlateAssemblyIntent,
    lower_to_plan,
    plan_from_request,
)
from .normalize import Reading, Refusal, read_request
from .parser import PlanParseError, parse_plan
from .plan import OperationPlan

#: Which route produced a plan. Recorded on every answer, because a part
#: whose origin had to be guessed is a part nobody can account for -- the
#: same rule the backend and the execution path already follow.
SOURCE_PROVIDER = "provider"
SOURCE_DETERMINISTIC = "deterministic"

#: Said to the user when the second route ran, so the fallback is never
#: silent. Deliberately says nothing about *which* model fell short.
DETERMINISTIC_NOTE = (
    "The model's plan was not usable, so I read the request directly."
)

#: Said when there is no model configured at all. A separate sentence from
#: :data:`DETERMINISTIC_NOTE` because it is a separate fact: nothing fell
#: short, nothing was asked. Telling a user their model answered badly when
#: no model was ever configured would be a lie about their own setup.
NO_MODEL_NOTE = (
    "No interpretation model is configured, so I read the request directly."
)


@dataclass(frozen=True)
class Interpretation:
    """One understanding of a request: a plan, its route, and why.

    ``intent`` is populated only by the deterministic route, and it is the
    explanation rather than decoration -- it is the canonical form the plan
    was lowered from, so a caller can show what was understood and not only
    what was built.
    """

    source: str
    plan: Optional[OperationPlan] = None
    intent: Optional[PlateAssemblyIntent] = None
    note: Optional[str] = None
    error: Optional[str] = None
    #: True only when a grammar RECOGNISED the request and cannot honour it --
    #: a hole wider than the stock, five plates for a six-sided box.
    #:
    #: Distinct from ``error`` alone, which also covers "no grammar claimed
    #: this sentence". The two want opposite handling: a refusal is an answer
    #: to give the person, and a non-understanding must fall through to the
    #: model. Conflating them turns every unreadable request into a confident
    #: "no" -- which is how "design me a gearbox" came back as a refusal
    #: rather than as a question for a model.
    refused: bool = False
    #: Set when one of the general mechanical readers understood the request.
    #: Carries the reader's name and the conventions it applied, so a person
    #: can see that "wider" was taken as X and say otherwise.
    reading: Optional[Any] = None

    @property
    def understood(self) -> bool:
        return self.plan is not None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"source": self.source,
                                   "understood": self.understood}
        if self.intent is not None:
            payload["intent"] = self.intent.to_dict()
        if self.reading is not None:
            payload["reading"] = self.reading.to_dict()
        if self.note:
            payload["note"] = self.note
        if self.error:
            payload["error"] = self.error
        if self.refused:
            payload["refused"] = True
        return payload


def provider_interpretation(
    result: PlanGenerationResult,
) -> Interpretation:
    """What the provider gave us, said in this module's terms.

    A plan only counts when the provider actually produced one it stands
    behind. A clarification, a refusal and an unparseable answer all arrive
    here as "no plan", because for the purpose of building a part they are
    the same fact.
    """
    if result.outcome is PlanOutcome.GENERATED and result.plan is not None:
        return Interpretation(source=SOURCE_PROVIDER, plan=result.plan)
    return Interpretation(source=SOURCE_PROVIDER,
                          error=result.error or result.outcome.value)


def deterministic_interpretation(
    text: str,
    plan: Optional[Mapping[str, Any]] = None,
    *,
    note: str = DETERMINISTIC_NOTE,
) -> Interpretation:
    """Read the request locally, against the provider-neutral grammars.

    Two grammars are offered the request, narrowest first:

    1. the **counted plate assembly** (:mod:`cad_experimental.intent`), which
       owns sentences like "a hollow box from 4 plates and 2 plates";
    2. the **general mechanical readers** (:mod:`cad_experimental.normalize`)
       -- primitives, holes, edge treatments, resizes, removals and patterns.

    Order matters and is not a preference. An assembly request says "plate"
    and carries three numbers, so the general box reader matches it too and
    would build ONE of its six plates. The assembly grammar is asked first,
    and the box reader independently declines anything the assembly grammar
    claims, because relying on ordering alone would be relying on this
    function never being refactored.

    ``plan`` is the part as it stands. The second grammar needs it -- "make it
    5 mm taller" has nothing to be taller than without it -- and passing
    ``None`` simply limits the reading to requests that create a part.

    ``note`` is what the user is told about why this route ran. It is a
    parameter because there are two different true answers -- a model
    answered badly, or no model was configured -- and saying the first when
    the second happened misdescribes the user's own setup.

    Calls no model of any kind. Either the grammar reads the request
    completely -- every plate, its thickness and its hole -- or this returns
    nothing at all: a partial reading would be a guess dressed as a fact.

    The plan goes out through :func:`~cad_experimental.parser.parse_plan`
    rather than as a hand-built object, so it meets exactly the checks a
    model's plan meets. There is no shorter path to the kernel from here
    than there is from a provider.
    """
    claimed = looks_like_plate_assembly(text)
    try:
        intent, payload = plan_from_request(text)
    except IntentError as assembly_error:
        reading = read_request(text, plan)
        if isinstance(reading, Refusal):
            # The grammar recognised the request and cannot honour it. That is
            # an answer, not a failure to understand, so it is reported as the
            # reason rather than falling through to a model that would be
            # asked to do the impossible.
            return Interpretation(source=SOURCE_DETERMINISTIC,
                                  error=reading.reason, refused=True)
        if isinstance(reading, Reading):
            try:
                parsed = parse_plan(reading.plan)
            except PlanParseError as exc:  # pragma: no cover - lowering bug
                return Interpretation(source=SOURCE_DETERMINISTIC,
                                      error=str(exc))
            return Interpretation(
                source=SOURCE_DETERMINISTIC, plan=parsed, note=note,
                reading=reading,
            )
        # The assembly grammar refuses two different ways. When the sentence
        # LOOKS like a plate assembly and still fails, that is a refusal with
        # a reason worth reading ("closed by 6 plates; this describes 5").
        # When it never looked like one, the message is only "not mine", and
        # saying that to a person as though it were a verdict would be
        # answering a question nobody asked.
        return Interpretation(source=SOURCE_DETERMINISTIC,
                              error=str(assembly_error), refused=claimed)
    try:
        parsed = parse_plan(payload)
    except PlanParseError as exc:  # pragma: no cover - a bug in lowering
        return Interpretation(source=SOURCE_DETERMINISTIC, error=str(exc))
    return Interpretation(source=SOURCE_DETERMINISTIC, plan=parsed,
                          intent=intent, note=note)


def intent_interpretation(intent: PlateAssemblyIntent) -> Interpretation:
    """Lower canonical intent a provider returned *directly* into a plan.

    The second reason this module exists. A provider that can emit canonical
    intent -- a local model given this vocabulary, say -- does not have to
    learn the Operation Plan's full grammar to reach the kernel: it reaches
    the same lowering the deterministic reader uses, and the same pipeline
    after it.
    """
    payload = lower_to_plan(intent)
    return Interpretation(source=SOURCE_PROVIDER, plan=parse_plan(payload),
                          intent=intent)


def interpret(
    text: str,
    result: PlanGenerationResult,
    plan: Optional[Mapping[str, Any]] = None,
) -> Interpretation:
    """The one question the request path asks: what shall we build?

    The provider is asked first and believed when it answers usefully. When
    it does not -- for any reason, including not answering at all -- the
    deterministic reader is given the same request. If that reads it, the
    part is built from canonical intent; if it does not, the provider's own
    outcome stands and is reported as itself.
    """
    attempt = provider_interpretation(result)
    if attempt.understood:
        return attempt
    fallback = deterministic_interpretation(text, plan)
    if fallback.understood:
        return fallback
    # A deterministic REFUSAL outranks a provider non-answer: the local
    # grammar recognised the request and knows why it cannot be done, which
    # is a better thing to tell someone than "the model did not answer".
    return fallback if fallback.error and not attempt.error else attempt


__all__ = [
    "DETERMINISTIC_NOTE",
    "NO_MODEL_NOTE",
    "SOURCE_DETERMINISTIC",
    "SOURCE_PROVIDER",
    "Interpretation",
    "deterministic_interpretation",
    "intent_interpretation",
    "interpret",
    "provider_interpretation",
]
