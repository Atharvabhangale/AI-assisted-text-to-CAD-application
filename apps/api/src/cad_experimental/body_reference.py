"""Which body a request means, and when that question has no honest answer.

Stage 71 made several independent bodies real, and left one gap: the product
could not EDIT a multi-body part, because every edit reader asked for "the
body" and there was no such thing. `normalize._body_id` returned ``None``
whenever more than one body was live, so the readers declined rather than
guess which one was meant. Declining was right; it is not an answer.

This module is the answer, and it is deliberately the ONLY place that gives
one. Four rules, in order, and every one of them is decidable from the
request text and the plan's own history:

1. **The request names a body** -- one of the plan's body ids appears in it
   as a word -- and that body is live. That is the body.
2. **The request names no body and exactly one is live.** That is the body,
   and this is the single-body behaviour every plan had before Stage 71,
   unchanged.
3. **The request names no body and several are live.** REFUSE, and name
   them. There is no *the* body, and picking one is the silent behaviour
   this project exists to avoid.
4. **The request names something that is not a live body** -- an id that
   does not exist, one that was consumed, or two ids at once. REFUSE, and
   say which.

WHY A BODY IS NAMED BY ITS ID, AND BY NOTHING ELSE.

A body's id is its identity, for life -- every modifier keeps its target's
id, so a body that has been drilled and filleted is still the same body with
the same name (`history.Body`). The product shows those ids, and the
deterministic reader that creates two bodies names them for what they are
(`cube`, `cylinder`). Matching on the id is therefore exact, stable and
needs no synonym table.

The alternative -- matching a body by the NOUN of the primitive it came
from, or by a synonym list -- was rejected. "The block" for a body named
`plate`, or "the pin" for one named `cylinder`, is a guess about what the
person meant, and a wrong guess here edits the wrong body and reports
success. A refusal that says *"this part has two bodies (cube, cylinder);
say which one"* costs the person one word and cannot be wrong.

ONE WALK, ONE ANSWER. The live set comes from
:func:`cad_experimental.history.plan_history`, the single implementation of
the solid-set walk, and never from a second pass over the operations. The
function this replaces learned that the hard way: it used to return the first
`box` or `cylinder`, which answers "what was built first" rather than "what is
still standing", and the two diverge the moment anything consumes a solid. On
a plan whose union target is the second constructive operation it returned the
CONSUMED tool, so every following hole and fillet targeted a solid that no
longer existed and the validator rejected the plan with P12.

Imports the standard library, the parser and the history walk, and nothing
else: no kernel, no backend, no vendor SDK, no provider module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .history import plan_history
from .parser import PlanParseError, parse_plan


@dataclass(frozen=True)
class BodyChoice:
    """Which body, or why the question cannot be answered.

    Exactly one of :attr:`body` and :attr:`reason` is set. The third state a
    caller needs -- "this reader is not interested in the sentence at all" --
    is not represented here on purpose: that is the reader's judgement about
    the VERB, made before it ever asks which body, and folding it in would
    let "not my sentence" and "your sentence, and it is ambiguous" reach the
    person as the same answer.
    """

    #: The body to act on, when there is exactly one honest answer.
    body: Optional[str] = None

    #: Why there is not, phrased for the person who asked.
    reason: Optional[str] = None

    #: Whether the request NAMED the body, as opposed to there being only one.
    #: Readers use it to decide what to say, never to decide what to do.
    named: bool = False

    #: Every live body at the end of the plan, in creation order.
    live: Tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.body is not None


def _mentions(text: str, identifier: str) -> Tuple[Tuple[int, int], ...]:
    """Where ``identifier`` appears in ``text`` as a whole word."""
    pattern = re.compile(rf"\b{re.escape(identifier)}\b", re.I)
    return tuple(match.span() for match in pattern.finditer(text or ""))


def _longest_distinct(found: Mapping[str, Tuple[Tuple[int, int], ...]]) -> Tuple[str, ...]:
    """Drop an id whose every mention sits inside another id's mention.

    Ids may overlap textually: `plate` is a whole word inside `plate-2`,
    because `-` is not a word character. "Make plate-2 wider" mentions both,
    and reporting that as ambiguous would be wrong -- the person named one
    body and the other merely shares a prefix. This is settled by the TEXT
    (one span contains the other), never by preferring a longer name in
    general, so it cannot quietly pick a body the request did not mention.
    """
    kept = []
    for identifier, spans in found.items():
        covered = all(
            any(other_start <= start and end <= other_end
                for other, other_spans in found.items() if other != identifier
                for other_start, other_end in other_spans)
            for start, end in spans
        )
        if not covered:
            kept.append(identifier)
    return tuple(kept)


def resolve_body(
    text: str, plan: Optional[Mapping[str, Any]]
) -> BodyChoice:
    """Which body this request means. Never guesses; never raises."""
    if plan is None:
        return BodyChoice(reason="there is no part yet")
    try:
        history = plan_history(parse_plan(dict(plan)))
    except PlanParseError:
        # An unparseable plan has no history to read. Declining here rather
        # than falling back to any other rule keeps the one answer honest.
        return BodyChoice(reason="the current plan cannot be read")

    live = tuple(str(body.id) for body in history.live_bodies)
    every = tuple(str(body.id) for body in history.bodies)

    found = {}
    for identifier in every:
        spans = _mentions(text, identifier)
        if spans:
            found[identifier] = spans
    mentioned = _longest_distinct(found) if found else ()

    if len(mentioned) > 1:
        listed = ", ".join(repr(name) for name in sorted(mentioned))
        return BodyChoice(
            reason=(f"this request names more than one body ({listed}); one "
                    f"operation changes one body, so say which"),
            live=live,
        )

    if len(mentioned) == 1:
        chosen = mentioned[0]
        if chosen in live:
            return BodyChoice(body=chosen, named=True, live=live)
        # Named, real, and gone: it was consumed by a union or a subtract.
        # Saying so is the useful answer -- "no such body" would send someone
        # looking for a typo that is not there.
        consumed_by = history.body(chosen)
        taken = getattr(consumed_by, "consumed_by", None)
        detail = f" -- {taken!r} consumed it" if taken else ""
        return BodyChoice(
            reason=(f"{chosen!r} is not a body of this part any more{detail}. "
                    f"The bodies are: {', '.join(repr(n) for n in live)}"),
            live=live,
        )

    if len(live) == 1:
        # The single-body case, unchanged: one body, so naming it is optional.
        return BodyChoice(body=live[0], named=False, live=live)

    if not live:
        return BodyChoice(reason="this part has no body to change", live=live)

    listed = ", ".join(repr(name) for name in live)
    return BodyChoice(
        reason=(f"this part has {len(live)} separate bodies ({listed}), and "
                f"the request does not say which one to change. Name it and "
                f"it will be done"),
        live=live,
    )


__all__ = ["BodyChoice", "resolve_body"]
