"""Structured validation errors for the V1 CAD specification.

Expected validation failures are *returned*, never raised: a caller receives a
:class:`ValidationResult` listing every violation the validator could
determine.  Exceptions are reserved for programming errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from cad_core.model import Part


@dataclass(frozen=True)
class ValidationError:
    """A single static validation violation.

    This is a record, not an exception.

    Attributes:
        rule: Rule code from the specification, e.g. ``"S10"``.
        message: Human-readable description of the violation.
        feature_id: The offending feature's ``id``, when it has a usable one.
        field_path: Path to the offending field, e.g. ``"features[1].size.x"``.
            ``None`` for a violation of the document as a whole.
        feature_index: Index of the offending feature in ``features``, which
            identifies a feature whose ``id`` is missing or duplicated.
    """

    rule: str
    message: str
    feature_id: Optional[str] = None
    field_path: Optional[str] = None
    feature_index: Optional[int] = None

    def __str__(self) -> str:
        location = self.field_path if self.field_path is not None else "<document>"
        return f"{self.rule} at {location}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of statically validating one specification document.

    Attributes:
        valid: True when ``errors`` is empty.
        errors: Every violation found, in a deterministic order (see
            :func:`cad_core.validator.validate`).
        part: The typed :class:`~cad_core.model.Part` when the document is
            valid, otherwise ``None``.  Static validity is not a geometric
            guarantee: rules E1-E5 are checked by the future CAD engine.
    """

    valid: bool
    errors: Tuple[ValidationError, ...]
    part: Optional["Part"] = None

    def rule_codes(self) -> Tuple[str, ...]:
        """Rule codes of all errors, in order. Convenient for assertions."""
        return tuple(error.rule for error in self.errors)
