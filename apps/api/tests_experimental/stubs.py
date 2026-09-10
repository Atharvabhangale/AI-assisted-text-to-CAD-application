"""Fake providers for the experimental tests. No credential, no network.

Deliberately separate from the stable path's ``tests/provider_stubs.py``:
these speak the plan vocabulary, and coupling the two suites would let a
change to one silently rewrite the other's expectations.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)

PROVIDER = "stub"
MODEL = "stub-model"


def box_plan(**overrides: Any) -> str:
    payload: Dict[str, Any] = {
        "status": "generated",
        "summary": "a rectangular plate",
        "operations": [
            {
                "id": "body",
                "type": "box",
                "parameters": {"x": 100, "y": 60, "z": 10},
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def cylinder_plan(**overrides: Any) -> str:
    payload: Dict[str, Any] = {
        "status": "generated",
        "summary": "a cylinder",
        "operations": [
            {
                "id": "body",
                "type": "cylinder",
                "parameters": {
                    "diameter": 20,
                    "height": 50,
                    "axis": "+Z",
                },
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def unsupported_plan(reason: str = "a sphere is not a box or a cylinder") -> str:
    return json.dumps(
        {
            "status": "unsupported",
            "summary": "cannot be expressed",
            "reason": reason,
            "operations": [],
        }
    )


def clarification_plan(question: str = "What diameter?") -> str:
    return json.dumps(
        {
            "status": "needs_clarification",
            "summary": "missing a dimension",
            "questions": [question],
            "operations": [],
        }
    )


class StubModel:
    """Returns canned text and records what it was asked."""

    name = PROVIDER

    def __init__(self, text: str, *, model: str = MODEL) -> None:
        self._text = text
        self._model = model
        self.requests: List[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text=self._text,
            provider=PROVIDER,
            model=self._model,
            structured_output=request.output_schema is not None,
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )


class FailingModel:
    """Raises, so a provider failure can be told from a bad answer."""

    name = PROVIDER

    def __init__(
        self, kind: ProviderErrorKind = ProviderErrorKind.RATE_LIMITED
    ) -> None:
        self._kind = kind
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        raise ProviderError(
            "the interpretation service is unavailable",
            detail="stub failure with a secret-looking string sk-ant-XXXX",
            kind=self._kind,
        )


class ExplodingModel:
    """Must never be called. Proves a path short-circuits before the model."""

    name = PROVIDER

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("the provider was called when it must not be")


__all__ = [
    "MODEL",
    "PROVIDER",
    "ExplodingModel",
    "FailingModel",
    "StubModel",
    "box_plan",
    "clarification_plan",
    "cylinder_plan",
    "unsupported_plan",
]
