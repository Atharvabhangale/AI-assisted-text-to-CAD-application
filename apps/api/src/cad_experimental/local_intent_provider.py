"""A provider that returns **canonical intent**, and a fake one to prove it.

Why this boundary exists
------------------------
``cad_ai.provider`` asks a model for an Operation Plan. That works, and it
asks the model to learn a ten-operation grammar with a JSON schema the
provider has to compile. A smaller model -- the local one this project is
heading towards -- may never manage that grammar and still be perfectly able
to say *what kind of part this is*:

```
"a hollow box from six plates, four of them 40x20, two 20x20,
 5 mm thick, 8 mm hole through the middle of each"
```

That is :class:`~cad_experimental.intent.PlateAssemblyIntent`, and it is a
much smaller thing to emit correctly. This module is the boundary for a
provider that returns it.

```
any provider -> canonical intent -> lower_to_plan -> parse -> validate
                                 -> graph -> backend -> kernel
```

**The lowering is the same lowering the deterministic reader uses**, and the
plan goes through the same parser and the same validator. A provider that
takes this route gets no shortcut and no leniency: it reaches the pipeline at
exactly the place a plan-emitting provider reaches it.

What is deliberately absent
---------------------------
No vendor name, no model name, no prompt, no schema, no SDK import, and no
branch on any of those -- in this module or anywhere below it. A test asserts
that the whole geometry core stays free of them. The only thing downstream
learns about the origin of a part is
:data:`~cad_experimental.interpretation.SOURCE_PROVIDER` versus
``SOURCE_DETERMINISTIC``, and that is a route, not a vendor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .intent import PlateAssemblyIntent, intent_from_dict

#: Stamped on every answer from a provider in this module, so a development
#: result can never be read as a model result. The same discipline
#: ``local_plan_provider`` keeps, for the same reason.
SOURCE_LABEL = "LOCAL_DEVELOPMENT_INTENT"


class IntentProvider:
    """The boundary: propose canonical intent for a request, or decline.

    One method, and it may answer ``None``. Declining is a first-class
    answer: a provider that cannot read a request must be able to say so
    rather than invent an intent, because an invented plate count builds a
    confidently wrong part.

    Subclassing is not required -- anything with this method satisfies
    :func:`intent_from_provider`. It is written out as a class so the
    contract has somewhere to be read.
    """

    #: Absent on a real provider. Present and true on every provider in this
    #: module, so a caller can tell a fixture from a model by type rather
    #: than by matching a string.
    is_local_development = False

    name = "intent-provider"

    def propose_intent(self, text: str) -> Optional[Mapping[str, Any]]:
        raise NotImplementedError


class FakeLocalProvider(IntentProvider):
    """Stands in for the future local model. **Not a model.**

    It holds canonical intent it was given and returns it. It does not read
    the request, does not interpret anything, imports no SDK, opens no
    socket and reads no credential -- and it is deliberately *not* wired to
    :func:`~cad_experimental.intent.extract_intent`, because a fake that
    quietly delegated to the deterministic reader would prove only that the
    deterministic reader works.

    What it does prove is the thing that matters: that a provider which has
    never seen the Operation Plan grammar can reach the kernel through
    canonical intent alone, and that nothing downstream can tell where the
    intent came from.
    """

    is_local_development = True
    name = "fake-local (not a model)"

    def __init__(self, intent: Mapping[str, Any]) -> None:
        #: Copied, so a caller mutating its own dictionary afterwards cannot
        #: change what this provider answers.
        self._intent: Dict[str, Any] = _copy(intent)
        #: Every request it was asked. Read by tests to prove it ignores the
        #: text rather than parsing it.
        self.requests: List[str] = []

    def propose_intent(self, text: str) -> Optional[Mapping[str, Any]]:
        self.requests.append(text)
        return _copy(self._intent)


class DecliningProvider(IntentProvider):
    """Answers ``None`` to everything. The "model was no use" case.

    Exists so the fallback can be exercised as the outcome it is, rather
    than by breaking a real provider to see what happens.
    """

    is_local_development = True
    name = "declining (not a model)"

    def propose_intent(self, text: str) -> Optional[Mapping[str, Any]]:
        return None


def intent_from_provider(
    provider: Any, text: str,
) -> Optional[PlateAssemblyIntent]:
    """Ask ``provider`` for intent and validate it as canonical intent.

    Returns ``None`` when the provider declines. A provider that answers
    with something that is *not* canonical intent raises out of
    :func:`~cad_experimental.intent.intent_from_dict` -- it is not silently
    repaired, and it is not silently dropped either, because those are
    different facts and a caller needs to tell them apart.
    """
    payload = provider.propose_intent(text)
    if payload is None:
        return None
    return intent_from_dict(payload)


def _copy(payload: Mapping[str, Any]) -> Dict[str, Any]:
    import copy as _c

    return _c.deepcopy(dict(payload))


__all__ = [
    "SOURCE_LABEL",
    "DecliningProvider",
    "FakeLocalProvider",
    "IntentProvider",
    "intent_from_provider",
]
