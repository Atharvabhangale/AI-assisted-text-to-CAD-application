"""Named, repeatable sequences of a FIXED set of CAD actions.

**This is not a scripting system and must never become one.** A macro is a
list of actions drawn from :data:`ACTIONS` -- a closed vocabulary defined in
this file. There is no expression language, no control flow, no user-supplied
code, and nothing here is ever passed to ``eval``, ``exec``, ``import`` or a
subprocess. An action the vocabulary does not contain cannot be stored, which
means it cannot be run.

Running a macro performs the same operations the workspace already exposes,
through the same session and the same backend abstraction. A macro is a
shortcut for things a person could click; it is not a new way to reach the
CAD engine.

Storage is in memory alongside the session, and disappears with the process.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

#: The closed vocabulary. A macro step names one of these and nothing else.
#: Each entry: what it does, and which parameters it accepts.
ACTIONS: Mapping[str, Mapping[str, Any]] = {
    "export_step": {
        "summary": "Export the current part as STEP",
        "parameters": (),
    },
    "export_stl": {
        "summary": "Export the current part as STL",
        "parameters": (),
    },
    "drawing": {
        "summary": "Generate a basic engineering drawing of the current part",
        "parameters": (),
    },
    "engineering_report": {
        "summary": "Collect measured and calculated engineering findings",
        "parameters": (),
    },
    "rename": {
        "summary": "Rename the current part",
        "parameters": ("name",),
    },
}

MAX_MACROS = 32
MAX_STEPS = 12


class MacroError(ValueError):
    """A macro that cannot be stored or run, and precisely why."""


@dataclass(frozen=True)
class MacroStep:
    """One action, with its parameters already checked against the vocabulary."""

    action: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "parameters": dict(self.parameters),
                "summary": ACTIONS[self.action]["summary"]}


@dataclass(frozen=True)
class Macro:
    """A named sequence. Immutable once stored."""

    name: str
    description: str
    steps: Tuple[MacroStep, ...]
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "steps": [s.to_dict() for s in self.steps],
                "created": self.created}


def validate_steps(raw: Sequence[Mapping[str, Any]]) -> Tuple[MacroStep, ...]:
    """Turn requested steps into checked ones, or refuse.

    Every rejection names what was wrong. An unknown action is refused
    outright rather than skipped: a macro that silently dropped a step would
    do something other than what it says it does.
    """
    if not raw:
        raise MacroError("a macro needs at least one action")
    if len(raw) > MAX_STEPS:
        raise MacroError(f"a macro may have at most {MAX_STEPS} actions")

    steps: List[MacroStep] = []
    for index, entry in enumerate(raw, 1):
        action = str((entry or {}).get("action", "")).strip()
        if action not in ACTIONS:
            raise MacroError(
                f"step {index}: '{action}' is not a known action. "
                f"Known actions: {', '.join(sorted(ACTIONS))}"
            )
        allowed = set(ACTIONS[action]["parameters"])
        given = {k: v for k, v in ((entry or {}).get("parameters") or {}).items()}
        unknown = set(given) - allowed
        if unknown:
            raise MacroError(
                f"step {index}: {action} does not take {', '.join(sorted(unknown))}"
            )
        missing = allowed - set(given)
        if missing:
            raise MacroError(
                f"step {index}: {action} needs {', '.join(sorted(missing))}"
            )
        steps.append(MacroStep(action=action, parameters=given))
    return tuple(steps)


class MacroStore:
    """Every macro, in memory, per session. Bounded."""

    def __init__(self, limit: int = MAX_MACROS) -> None:
        self._macros: Dict[str, Dict[str, Macro]] = {}
        self._limit = limit

    def create(self, session_id: str, name: str, description: str,
               steps: Sequence[Mapping[str, Any]]) -> Macro:
        clean = (name or "").strip()
        if not clean:
            raise MacroError("a macro needs a name")
        if len(clean) > 80:
            raise MacroError("that name is too long")
        macro = Macro(name=clean, description=(description or "").strip(),
                      steps=validate_steps(steps))
        bucket = self._macros.setdefault(session_id, {})
        if len(bucket) >= self._limit and clean not in bucket:
            raise MacroError(f"this session already holds {self._limit} macros")
        bucket[clean] = macro
        return macro

    def list(self, session_id: str) -> Tuple[Macro, ...]:
        return tuple(self._macros.get(session_id, {}).values())

    def get(self, session_id: str, name: str) -> Macro:
        macro = self._macros.get(session_id, {}).get((name or "").strip())
        if macro is None:
            raise MacroError(f"no macro named '{name}' in this session")
        return macro

    def delete(self, session_id: str, name: str) -> None:
        self._macros.get(session_id, {}).pop((name or "").strip(), None)


#: What a plain-language macro request maps onto. Matching, not interpretation:
#: the vocabulary is five actions, and a table beats a model call for that.
_PHRASES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("step", "stp"), "export_step"),
    (("stl", "mesh"), "export_stl"),
    (("drawing", "drawings", "draft", "views"), "drawing"),
    (("engineering", "report", "analysis", "analyse", "analyze"),
     "engineering_report"),
)


def steps_from_language(text: str) -> Tuple[Dict[str, Any], ...]:
    """Read a macro request into actions, using the closed vocabulary only.

    Anything it cannot recognise is simply not included -- the caller then
    sees an empty or partial list and can say so, which is far better than
    inventing a step the user did not ask for.
    """
    lowered = (text or "").lower()
    chosen: List[Dict[str, Any]] = []
    for words, action in _PHRASES:
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            if all(s["action"] != action for s in chosen):
                chosen.append({"action": action, "parameters": {}})
    return tuple(chosen)


def suggested_name(text: str) -> Optional[str]:
    """A quoted name in the request, if the user gave one."""
    match = re.search(r"['\"]([^'\"]{1,80})['\"]", text or "")
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(?:called|named)\s+([A-Za-z0-9 _-]{2,60})", text or "",
                      re.I)
    return match.group(1).strip() if match else None


__all__ = ["ACTIONS", "MAX_MACROS", "MAX_STEPS", "Macro", "MacroError",
           "MacroStep", "MacroStore", "steps_from_language", "suggested_name",
           "validate_steps"]
