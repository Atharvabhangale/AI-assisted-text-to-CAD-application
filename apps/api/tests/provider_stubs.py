"""Deterministic provider stubs. Test support, not production code.

Not under ``src/``: nothing shippable imports these, and the test-discovery
pattern (``test*.py``) does not collect this file as a test module.

Every payload here was written **by hand in this file**. None came from a
model, and none is evidence about any model. The point is to drive the
generation service through each shape a provider could return -- including
the ugly ones -- without contacting anything.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderNotConfigured,
)

#: A canonical-shaped box document: the Section D plate's dimensions.
BOX_DOCUMENT: Mapping[str, Any] = {
    "schema_version": "1.0.0",
    "units": "mm",
    "name": "plate-100x60x10",
    "features": [
        {
            "id": "plate",
            "type": "box",
            "size": {"x": 100, "y": 60, "z": 10},
        }
    ],
}

#: A cylinder document, both optional parameters omitted so the existing
#: deserializer applies the specification's own defaults.
CYLINDER_DOCUMENT: Mapping[str, Any] = {
    "schema_version": "1.0.0",
    "units": "mm",
    "name": "cylinder-d20-h50",
    "features": [
        {"id": "cylinder", "type": "cylinder", "diameter": 20, "height": 50}
    ],
}

#: Syntactically fine JSON whose CAD is invalid: rule S10 (size.x must be > 0).
INVALID_CAD_DOCUMENT: Mapping[str, Any] = {
    "schema_version": "1.0.0",
    "units": "mm",
    "name": "zero-width",
    "features": [
        {"id": "plate", "type": "box", "size": {"x": 0, "y": 60, "z": 10}}
    ],
}

#: A document using a feature outside the supported subset.
UNSUPPORTED_FEATURE_DOCUMENT: Mapping[str, Any] = {
    "schema_version": "1.0.0",
    "units": "mm",
    "name": "drilled-plate",
    "features": [
        {"id": "plate", "type": "box", "size": {"x": 100, "y": 60, "z": 10}},
        {
            "id": "hole",
            "type": "through_hole",
            "target": "plate",
            "diameter": 8,
            "position": {"x": 50, "y": 30, "z": 0},
        },
    ],
}


def _answer(status: str, **fields: Any) -> str:
    payload: Dict[str, Any] = {"status": status}
    payload.update(fields)
    return json.dumps(payload)


#: Every response shape the stage names, keyed by a short label. Each value is
#: the **raw text** a provider would hand back, so the existing parser,
#: deserializer and validator all get their real work to do.
RESPONSE_SHAPES: Mapping[str, str] = {
    "valid_box": _answer(
        "document", summary="A 100 x 60 x 10 mm plate.", document=dict(BOX_DOCUMENT)
    ),
    "valid_cylinder": _answer(
        "document",
        summary="A 20 mm diameter, 50 mm tall cylinder.",
        document=dict(CYLINDER_DOCUMENT),
    ),
    "malformed_json": '{"status": "document", "document": {',
    "prose": "Sure! Here is a 100 x 60 x 10 mm plate for you.",
    "empty": "",
    "whitespace": "   \n\t  ",
    "null": "null",
    "json_array": "[]",
    # A fenced block. The Stage 26 parser deliberately does NOT unwrap these:
    # unwrapping would be a repair step, and this stage adds none.
    "markdown_fenced": "```json\n"
    + _answer("document", document=dict(BOX_DOCUMENT))
    + "\n```",
    "invalid_cad": _answer("document", document=dict(INVALID_CAD_DOCUMENT)),
    "unsupported_feature_document": _answer(
        "document", document=dict(UNSUPPORTED_FEATURE_DOCUMENT)
    ),
    "needs_clarification": _answer(
        "needs_clarification",
        summary="No unit is stated.",
        questions=["What units should I use?"],
    ),
    "unsupported": _answer(
        "unsupported", issues=["a through_hole is not supported yet"]
    ),
    "unknown_status": _answer("ok", document=dict(BOX_DOCUMENT)),
}


class StubTextToCadModel:
    """A provider that answers from :data:`RESPONSE_SHAPES`. Contacts nothing.

    Satisfies :class:`~cad_ai.provider.TextToCadModel` exactly, so anything
    above the boundary cannot tell it from a real provider -- which is the
    property that makes it useful for testing the generation service and the
    evaluation harness.
    """

    def __init__(
        self,
        shape: str = "valid_box",
        *,
        name: str = "stub",
        model: str = "stub-model",
        error: Optional[ProviderError] = None,
        structured: bool = True,
        usage: Optional[Mapping[str, int]] = None,
    ) -> None:
        if error is None and shape not in RESPONSE_SHAPES:
            raise KeyError(f"no stub response shape named {shape!r}")
        self.name = name
        self._shape = shape
        self._model = model
        self._error = error
        self._structured = structured
        self._usage = dict(usage or {"input_tokens": 3800, "output_tokens": 120})
        self.requests: List[ModelRequest] = []

    @property
    def shape(self) -> str:
        return self._shape

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return ModelResponse(
            text=RESPONSE_SHAPES[self._shape],
            provider=self.name,
            model=self._model,
            structured_output=self._structured,
            stop_reason="end_turn",
            usage=dict(self._usage),
        )


def failing_stub(
    detail: str = "APIError: 429 quota exceeded for x-goog-api-key=REDACTED",
    *,
    name: str = "stub",
    unavailable: bool = False,
) -> StubTextToCadModel:
    """A stub whose provider cannot answer.

    ``unavailable`` distinguishes "no credential, nothing attempted" from "a
    call was made and failed"; both reach the caller as the same generic
    outcome, which is the behaviour under test.
    """
    error_class = ProviderNotConfigured if unavailable else ProviderError
    return StubTextToCadModel(
        error=error_class(
            "the interpretation service is unavailable", detail=detail
        ),
        name=name,
    )


def gemini_shaped_stub(shape: str = "valid_box") -> StubTextToCadModel:
    """A stub that reports itself as the Gemini provider.

    Only the reported ``name`` differs. That is the point: the boundary makes
    provider identity metadata, not behaviour, so the same stub serves both.
    """
    return StubTextToCadModel(shape, name="gemini", model="gemini-stub")


__all__ = [
    "BOX_DOCUMENT",
    "CYLINDER_DOCUMENT",
    "INVALID_CAD_DOCUMENT",
    "RESPONSE_SHAPES",
    "UNSUPPORTED_FEATURE_DOCUMENT",
    "StubTextToCadModel",
    "failing_stub",
    "gemini_shaped_stub",
]
