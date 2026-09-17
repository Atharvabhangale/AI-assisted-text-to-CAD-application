"""Experimental, in-memory CAD session state for a multi-turn copilot.

**Experimental and deliberately disposable.** Everything here lives in one
process's memory, is bounded, and is thrown away when the process exits.
There is no database, no persistence, no authentication and no cross-process
sharing, and none of those belong here: the point of this milestone is to
learn what a conversational CAD session needs to remember, not to build the
storage it will eventually need.

What a session remembers
------------------------
The canonical Operation Plan remains the authoritative representation of the
part. This module holds **no second CAD state**: it stores the plan the
executor last built successfully, a bounded stack of the plans before it, the
evidence that build produced, and a bounded window of what was said. The
FreeCAD or CadQuery shape is an execution result and is never the record of
what the part *is* -- rebuild the plan and you have it back.

The failure rule this module exists to enforce
----------------------------------------------
A failed or unsupported edit must never replace the last good model. So
``current`` is only ever advanced by :meth:`CadSession.commit`, which is
called after a build has actually succeeded. Everything else -- a refusal, a
clarification, an invalid plan, a kernel error -- leaves the session exactly
as it was, and the viewport keeps showing the part that still builds.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple
from collections import deque

#: How many conversation turns travel with a revision request. Bounded on
#: purpose: the current plan already carries the part's whole state, so
#: history is only needed to resolve *language* ("that hole", "make it
#: wider"), and a handful of turns covers that. An unbounded transcript would
#: grow the request without making the reference any clearer.
CONTEXT_TURNS = 6

#: How many previous plans undo can reach back through. A small stack is
#: enough for the experiment and keeps a session's memory flat.
MAX_REVISIONS = 12

#: How many sessions the store keeps before evicting the least recently used.
#: A cap rather than a leak: this is a developer workspace, not a service.
MAX_SESSIONS = 32

USER = "user"
ASSISTANT = "assistant"


@dataclass(frozen=True)
class Turn:
    """One thing that was said, by one side."""

    role: str
    text: str
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "text": self.text, "at": self.at}


@dataclass(frozen=True)
class Revision:
    """One plan that built successfully, and what the build measured.

    Kept as the plan's plain wire form rather than a parsed object: it is
    what the parser accepts, what the model is shown, and what a caller can
    serialize, so storing anything richer would only add a conversion.
    """

    plan: Dict[str, Any]
    summary: str
    request: str
    measurement: Dict[str, Any] = field(default_factory=dict)
    backend: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "request": self.request,
            "measurement": dict(self.measurement),
            "backend": self.backend,
            "at": self.at,
        }


@dataclass
class CadSession:
    """One workspace: what is built, what came before, and what was said."""

    session_id: str
    conversation: Deque[Turn] = field(default_factory=deque)
    #: The plan that last built successfully. `None` until one does.
    current: Optional[Revision] = None
    #: Plans that built successfully before `current`, newest last.
    history: List[Revision] = field(default_factory=list)
    touched: float = field(default_factory=time.time)

    # --- conversation ----------------------------------------------------

    def said(self, role: str, text: str) -> None:
        self.conversation.append(Turn(role=role, text=text))
        # Keep a generous transcript for display, but bounded: this is the
        # only unbounded-looking structure here and it must not be one.
        while len(self.conversation) > CONTEXT_TURNS * 6:
            self.conversation.popleft()
        self.touched = time.time()

    def recent(self, turns: int = CONTEXT_TURNS) -> Tuple[Turn, ...]:
        """The last few turns, for resolving references in the next request."""
        return tuple(self.conversation)[-turns:]

    # --- the model --------------------------------------------------------

    def commit(self, revision: Revision) -> None:
        """Accept a plan that actually built. The ONLY way `current` moves.

        A failed edit never reaches here, which is what keeps the last good
        part on screen when a modification is refused.
        """
        if self.current is not None:
            self.history.append(self.current)
            while len(self.history) > MAX_REVISIONS:
                self.history.pop(0)
        self.current = revision
        self.touched = time.time()

    def undo(self) -> Optional[Revision]:
        """Step back to the previous successful plan, and return it.

        Returns ``None`` when there is nothing to go back to, which the
        caller reports rather than treating as an error: asking to undo the
        first thing you built is a reasonable thing to say.
        """
        if not self.history:
            return None
        self.current = self.history.pop()
        self.touched = time.time()
        return self.current

    def reset(self, *, keep_conversation: bool = True) -> None:
        """Start a new part.

        The conversation is kept by default and a marker turn is appended by
        the caller, because a thread that silently empties itself loses the
        record of what was built before. The CAD state -- plan, evidence and
        revision history -- is cleared completely.
        """
        self.current = None
        self.history.clear()
        if not keep_conversation:
            self.conversation.clear()
        self.touched = time.time()

    @property
    def has_model(self) -> bool:
        return self.current is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "has_model": self.has_model,
            "can_undo": bool(self.history),
            "revisions": len(self.history),
            "conversation": [turn.to_dict() for turn in self.conversation],
            "current": self.current.to_dict() if self.current else None,
        }


class SessionStore:
    """Every live session, in memory, bounded, least-recently-used evicted."""

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self._sessions: Dict[str, CadSession] = {}
        self._limit = limit

    def get(self, session_id: Optional[str]) -> CadSession:
        """The named session, created if this is the first time it is seen."""
        key = (session_id or "").strip() or uuid.uuid4().hex
        session = self._sessions.get(key)
        if session is None:
            session = CadSession(session_id=key)
            self._sessions[key] = session
            self._evict()
        return session

    def _evict(self) -> None:
        while len(self._sessions) > self._limit:
            oldest = min(self._sessions.values(), key=lambda s: s.touched)
            self._sessions.pop(oldest.session_id, None)

    def forget(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)


# --- what the model is shown ------------------------------------------------


def describe_model(revision: Revision) -> str:
    """One line a person could read, describing the part as it stands."""
    operations = revision.plan.get("operations") or []
    kinds: Dict[str, int] = {}
    for operation in operations:
        kind = str(operation.get("type", "?"))
        kinds[kind] = kinds.get(kind, 0) + 1
    parts = ", ".join(
        f"{count} {kind}" if count > 1 else kind
        for kind, count in kinds.items()
    )
    measured = revision.measurement or {}
    size = measured.get("size") or []
    extent = ""
    if len(size) == 3 and all(isinstance(v, (int, float)) for v in size):
        extent = f", bounding box {size[0]:g} x {size[1]:g} x {size[2]:g} mm"
    return f"{revision.summary or 'the current part'} ({parts}){extent}"



#: Words that mean "tell me about the part you already built" rather than
#: "change it". Deliberately conservative: a phrase has to look like a
#: question AND name a quantity we actually hold, or it goes to the model.
_MEASURE_WORDS = ("volume", "dimension", "dimensions", "size", "how big",
                  "how thick", "how wide", "how long", "bounding box",
                  "how many faces", "how many edges", "face count",
                  "edge count", "measure", "measurements")
_QUESTION_WORDS = ("what", "how", "?", "tell me", "show me", "give me")


def measurement_answer(session: "CadSession", request: str) -> Optional[str]:
    """Answer a question about the current part from evidence, or ``None``.

    The numbers come from the executor's own measurement of the build that
    actually succeeded -- never from the model, which would be guessing at
    geometry it cannot see. A question we cannot answer from what we hold
    returns ``None`` and takes the ordinary path.

    Returning ``None`` is the safe direction: the worst case is spending a
    model call on something we could have answered ourselves, where the worst
    case of the opposite is a fabricated dimension.
    """
    if session.current is None:
        return None
    text = request.lower().strip()
    if not any(word in text for word in _QUESTION_WORDS):
        return None
    if not any(word in text for word in _MEASURE_WORDS):
        return None

    measured = session.current.measurement or {}
    if not measured:
        return None

    lines: List[str] = []
    size = measured.get("size") or []
    if len(size) == 3 and all(isinstance(v, (int, float)) for v in size):
        lines.append(
            f"Overall dimensions: {size[0]:g} x {size[1]:g} x {size[2]:g} mm."
        )
    volume = measured.get("volume")
    if isinstance(volume, (int, float)):
        lines.append(f"Volume: {volume:.3f} mm3.")
    faces, edges = measured.get("face_count"), measured.get("edge_count")
    if isinstance(faces, int):
        counted = f"Faces: {faces}"
        if isinstance(edges, int):
            counted += f", edges: {edges}"
        lines.append(counted + ".")
    solids = measured.get("solid_count")
    if isinstance(solids, int) and solids != 1:
        lines.append(f"Solids: {solids}.")
    if not lines:
        return None
    backend = session.current.backend or "the CAD engine"
    lines.append(f"Measured by {backend} on the current build.")
    return " ".join(lines)


def revision_context(
    session: CadSession, request: str, *, turns: int = CONTEXT_TURNS
) -> str:
    """The user message for a request that modifies an existing part.

    Everything the model needs to resolve a reference, and nothing else:

    * the **current canonical Operation Plan**, verbatim, because it already
      states every id, dimension, target and selector the request might mean
      by "that hole" or "the same edges";
    * a one-line summary of what the part currently measures;
    * a bounded window of the conversation, for language the plan cannot
      carry ("make it wider" means wider than what was just discussed);
    * the new request.

    Deliberately NOT included: execution internals, edge indices, backend
    names, render models, selector resolutions. None of them help the model
    write a plan, and all of them would make the request bigger and the
    model's job noisier.

    The reply is asked for as a **complete** plan rather than a patch. The
    canonical IR has no patch form, inventing one would be a second
    representation, and the parser and validator already judge a whole plan.
    """
    assert session.current is not None
    plan_text = json.dumps(session.current.plan, indent=1, sort_keys=False)

    spoken: List[str] = []
    for turn in session.recent(turns):
        who = "User" if turn.role == USER else "Assistant"
        spoken.append(f"{who}: {turn.text}")
    transcript = "\n".join(spoken) if spoken else "(nothing yet)"

    return (
        "You are modifying an existing part, not starting a new one.\n\n"
        f"CURRENT PART: {describe_model(session.current)}\n\n"
        "CURRENT OPERATION PLAN (the authoritative description of the part; "
        "operation ids, dimensions and selectors in it are what the request "
        "may refer to):\n"
        f"{plan_text}\n\n"
        "RECENT CONVERSATION:\n"
        f"{transcript}\n\n"
        f"REQUEST: {request}\n\n"
        "Reply with the COMPLETE revised operation plan for the whole part "
        "-- every operation it should now have, not only the change. Keep "
        "the ids of operations that are unchanged so the part stays "
        "recognisable, and change only what the request asks for.\n"
        # Measured, 0/5 live calls: asked to repeat a feature, the model
        # expands it by hand into one operation per instance rather than
        # using the `pattern` operation the language has for exactly this.
        # The geometry comes out right either way, but the intent is lost --
        # a hand-expanded row of holes cannot be re-spaced or re-counted as
        # one thing. This says so explicitly at the point of use.
        "To repeat an existing feature at regular intervals, use one "
        "`pattern` operation whose `source` is that feature's id, rather "
        "than duplicating the feature several times.\n"
        "If the request is ambiguous about which feature it "
        "means or what the new value should be, ask instead of guessing. If "
        "the request needs geometry this language cannot express, say it is "
        "unsupported rather than approximating it."
    )


__all__ = [
    "ASSISTANT",
    "CONTEXT_TURNS",
    "MAX_REVISIONS",
    "MAX_SESSIONS",
    "USER",
    "CadSession",
    "Revision",
    "SessionStore",
    "Turn",
    "describe_model",
    "measurement_answer",
    "revision_context",
]
