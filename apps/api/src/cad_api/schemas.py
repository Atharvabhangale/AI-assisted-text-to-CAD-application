"""Pydantic models for the **transport shape only**.

What these do: check that a request body is a JSON object with exactly the
expected fields of the expected JSON types.

What they deliberately do **not** do: implement a CAD rule, judge a document,
validate geometry, check an output name against the artifact kinds, or compute
anything. The CAD document is typed ``Dict[str, Any]``, so **Pydantic never
looks inside it** -- measured: ``"100"`` stays a string, ``true`` stays a
boolean and ``null`` stays null, and the V1 validator rejects all three with
rule S19. Nothing in the transport can turn a malformed CAD value into a valid
one.

The transport-neutral contract in :mod:`cad_core.api_contract` remains the
conceptual contract. These models are a framework-specific adapter around it,
not a copy of it: there is no Pydantic model of a response, an artifact, an
error or a CAD document anywhere in this package.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from cad_core.api_contract import BUILD_REQUEST_FIELDS, VALIDATE_REQUEST_FIELDS


class ValidateBody(BaseModel):
    """``POST /validate``: ``{"document": {...}}``.

    ``extra="forbid"`` so an unknown field is a 422 rather than something
    quietly ignored -- a client that misspells a field should be told.
    """

    model_config = ConfigDict(extra="forbid")

    document: Dict[str, Any] = Field(
        description="A canonical CAD document, as a JSON object. Its contents "
        "are not inspected here; the V1 validator judges them."
    )

    def to_payload(self) -> Dict[str, Any]:
        """The transport-contract payload, with omitted fields left out."""
        return self.model_dump(exclude_unset=True)


class BuildBody(BaseModel):
    """``POST /build``: ``{"document": {...}, "outputs": ["step", ...]}``.

    ``outputs`` may be omitted, which the contract reads as all of them. An
    **explicit** ``null`` is not the same thing and is not silently treated as
    an omission: it survives into the payload, where the contract refuses it.
    """

    model_config = ConfigDict(extra="forbid")

    document: Dict[str, Any] = Field(
        description="A canonical CAD document, as a JSON object."
    )
    outputs: Optional[List[str]] = Field(
        default=None,
        description="Output names. Omit for all of them. The application "
        "service decides which names are valid.",
    )

    def to_payload(self) -> Dict[str, Any]:
        """The transport-contract payload, with omitted fields left out."""
        return self.model_dump(exclude_unset=True)


#: The fields these models accept, taken from the transport contract itself so
#: the two cannot drift. A test asserts the correspondence.
VALIDATE_FIELDS = VALIDATE_REQUEST_FIELDS
BUILD_FIELDS = BUILD_REQUEST_FIELDS
