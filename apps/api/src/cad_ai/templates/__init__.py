"""Reference designs the interpreter is shown when a request names them.

Each template is a complete, verified answer -- a CAD document built by hand
from parameters and checked on the real engine -- plus the words that select
it. :func:`select_templates` picks the best one or two for a description by
plain keyword matching: deterministic, no model involved, and nothing is
selected for a request that names none of them. The chosen templates travel
in the request's user text after the description (see
:func:`cad_ai.generation.compose_user_text`), so the system prompt stays the
same for every request and keeps its cache.

The model is shown a template to imitate and adapt; it is not trained on it,
and everything it returns is validated and built like any other answer.
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Mapping, Sequence, Tuple

#: Every template module, in a fixed order (it breaks ties). Adding a template
#: means adding a module here; there is no discovery.
TEMPLATE_MODULES: Tuple[str, ...] = (
    "engine",
    "rocket",
    "toy_car",
    "pillow_block",
    "table",
)  # TEMPORARY during the 1.2.0 rewrite; the other five are re-added after it

#: How many templates one request may carry.
MAX_TEMPLATES = 2


def _load() -> Tuple[Mapping[str, Any], ...]:
    loaded = []
    for name in TEMPLATE_MODULES:
        module = importlib.import_module(f"cad_ai.templates.{name}")
        loaded.append(module.TEMPLATE)
    return tuple(loaded)


#: The loaded library.
TEMPLATES: Tuple[Mapping[str, Any], ...] = _load()


def _mentions(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def _score(template: Mapping[str, Any], text: str) -> int:
    """How many keywords the text names; 0 if it names an exclusion.

    ``excludes`` (optional) lists words that mean the request is about
    something else that merely shares a keyword -- "a rocket engine" is not
    the car engine, "a motor mount bracket" is not a motor.
    """
    if any(_mentions(text, word) for word in template.get("excludes", ())):
        return 0
    return sum(1 for keyword in template["keywords"] if _mentions(text, keyword))


def select_templates(description: str, limit: int = MAX_TEMPLATES) -> Sequence[Mapping[str, Any]]:
    """The templates whose keywords the description mentions, best first."""
    text = description.lower()
    scored = [(_score(t, text), i, t) for i, t in enumerate(TEMPLATES)]
    chosen = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    return tuple(t for _, _, t in chosen[:limit])


__all__ = ["MAX_TEMPLATES", "TEMPLATES", "TEMPLATE_MODULES", "select_templates"]
