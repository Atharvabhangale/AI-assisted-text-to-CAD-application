"""Field-level comparison of two V1 CAD documents, for evaluation only.

This is **document** comparison, never geometry comparison. Two documents
that would build to the same solid but differ in design history are *different
documents*, and this module says so. That is deliberate: the question this
harness answers is whether the model produced the intended CAD
representation, not whether it stumbled onto a shape with the right bounding
box.

The taxonomy below is **local to evaluation**. It is not in
``cad_core.model``, it is not part of the V1 contract, and nothing in the
generation path imports it.

Both sides are expected to be **canonical** documents -- the output of
``cad_core.serialization.serialize_part`` on a validated part -- so:

* key order, whitespace and JSON formatting are already normalised away and
  are never compared;
* optional parameters are already materialised, which means this module
  cannot see whether the model *omitted* a defaulted field or wrote its
  default explicitly. Where that distinction matters, the evaluator observes
  the model's raw text separately (see :mod:`cad_ai.evaluation`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.model import (
    DEFAULT_AXIS,
    FEATURE_PARAMETERS,
    FEATURE_REFERENCE_FIELDS,
    ORIGIN,
)

#: Vector component order, as the canonical serialization writes it.
COMPONENTS: Tuple[str, str, str] = ("x", "y", "z")

#: Root fields that carry **no geometric meaning**. A difference in one of
#: these is reported, but it does not make two documents semantically
#: different: the specification calls ``description`` free text that "carries
#: no geometric meaning", and ``name`` a human-readable identifier.
LABEL_ROOT_FIELDS: Tuple[str, ...] = ("name", "description")

#: Scalar parameters that are lengths, per the Section C tables.
LENGTH_PARAMETERS: Tuple[str, ...] = ("diameter", "height", "radius", "distance")

#: The parameters the specification gives a default, read from the contract's
#: own tables rather than restated.
DEFAULTED_PARAMETERS: Tuple[str, ...] = tuple(
    sorted(
        {
            name
            for _, optional in FEATURE_PARAMETERS.values()
            for name in optional
        }
    )
)

#: The specification's default value for each defaulted parameter, taken from
#: ``cad_core.model``'s own constants.
PARAMETER_DEFAULTS: Mapping[str, Any] = {
    "position": {"x": ORIGIN.x, "y": ORIGIN.y, "z": ORIGIN.z},
    "axis": DEFAULT_AXIS,
}


class SemanticErrorCategory(Enum):
    """How a candidate document differs from the intended one.

    Most entries are **field-level**: they label one difference. The last two
    are **case-level**: they describe an outcome that was wrong regardless of
    any field, and the evaluator assigns them.
    """

    WRONG_SCHEMA_VERSION = "wrong_schema_version"
    UNIT_ERROR = "unit_error"
    WRONG_LABEL = "wrong_label"
    WRONG_FEATURE_TYPE = "wrong_feature_type"
    WRONG_FEATURE_ID = "wrong_feature_id"
    WRONG_FEATURE_ORDER = "wrong_feature_order"
    WRONG_DIMENSION = "wrong_dimension"
    WRONG_POSITION = "wrong_position"
    WRONG_AXIS = "wrong_axis"
    WRONG_DEFAULT = "wrong_default"
    WRONG_REFERENCE = "wrong_reference"
    WRONG_SELECTOR = "wrong_selector"
    MISSING_FEATURE = "missing_feature"
    EXTRA_FEATURE = "extra_feature"

    #: Case-level: the request was ambiguous and the model answered anyway.
    AMBIGUITY_NOT_ASKED = "ambiguity_not_asked"

    #: Case-level: the request needed a feature outside the supported subset
    #: and the model produced a document instead of refusing.
    UNSUPPORTED_FEATURE_ACCEPTED = "unsupported_feature_accepted"


#: The categories that describe an outcome rather than a field.
CASE_LEVEL_CATEGORIES: Tuple[SemanticErrorCategory, ...] = (
    SemanticErrorCategory.AMBIGUITY_NOT_ASKED,
    SemanticErrorCategory.UNSUPPORTED_FEATURE_ACCEPTED,
)

#: Field-level categories that do **not** change the geometry the document
#: describes. Reported, and excluded from the semantic verdict.
LABEL_CATEGORIES: Tuple[SemanticErrorCategory, ...] = (
    SemanticErrorCategory.WRONG_LABEL,
    SemanticErrorCategory.WRONG_FEATURE_ID,
)


class SemanticStatus(Enum):
    """The document-level verdict, defined exactly.

    * :attr:`MATCH` -- no difference at all. Equivalent to an exact canonical
      match.
    * :attr:`LABELS_DIFFER` -- every difference is in a field that carries no
      geometric meaning (``name``, ``description``, a feature ``id``). The
      document describes the intended part under different labels.
    * :attr:`MISMATCH` -- at least one difference changes what the document
      describes.
    * :attr:`NOT_COMPARED` -- there was nothing to compare: no candidate
      document, or the case does not expect one.
    """

    MATCH = "match"
    LABELS_DIFFER = "labels_differ"
    MISMATCH = "mismatch"
    NOT_COMPARED = "not_compared"


@dataclass(frozen=True)
class DocumentDifference:
    """One labelled difference. Never an unlabelled recursive dict diff."""

    #: A dotted path into the document, e.g. ``features[0].size.x``.
    field_path: str
    category: SemanticErrorCategory
    expected: Any
    actual: Any

    #: Set when the difference involves the specification's own default for a
    #: defaulted parameter, naming which side is the default.
    default_side: Optional[str] = None

    @property
    def geometric(self) -> bool:
        """Whether this difference changes what the document describes."""
        return self.category not in LABEL_CATEGORIES

    def __str__(self) -> str:
        return (
            f"{self.field_path}: expected {self.expected!r}, "
            f"actual {self.actual!r} ({self.category.value})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path,
            "category": self.category.value,
            "expected": self.expected,
            "actual": self.actual,
            "default_side": self.default_side,
            "geometric": self.geometric,
        }


@dataclass(frozen=True)
class DocumentComparison:
    """The result of comparing two canonical documents."""

    status: SemanticStatus
    differences: Tuple[DocumentDifference, ...] = ()

    #: Every leaf path that was compared, in comparison order. The
    #: denominator of the field-correctness rate, so it is recorded rather
    #: than inferred.
    compared_paths: Tuple[str, ...] = ()

    @property
    def exact(self) -> bool:
        return self.status is SemanticStatus.MATCH

    @property
    def categories(self) -> Tuple[SemanticErrorCategory, ...]:
        seen: List[SemanticErrorCategory] = []
        for difference in self.differences:
            if difference.category not in seen:
                seen.append(difference.category)
        return tuple(seen)

    @property
    def fields_compared(self) -> int:
        return len(self.compared_paths)

    @property
    def fields_matching(self) -> int:
        differing = {difference.field_path for difference in self.differences}
        return len([path for path in self.compared_paths if path not in differing])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "differences": [item.to_dict() for item in self.differences],
            "fields_compared": self.fields_compared,
            "fields_matching": self.fields_matching,
            "categories": [category.value for category in self.categories],
        }


class _Collector:
    """Accumulates differences and the paths that were looked at."""

    def __init__(self) -> None:
        self.differences: List[DocumentDifference] = []
        self.paths: List[str] = []

    def compare(
        self,
        path: str,
        expected: Any,
        actual: Any,
        category: SemanticErrorCategory,
        *,
        default: Any = None,
    ) -> None:
        self.paths.append(path)
        if _same(expected, actual):
            return
        side: Optional[str] = None
        if default is not None:
            if _same(expected, default):
                side = "expected"
            elif _same(actual, default):
                side = "actual"
            if side is not None:
                category = SemanticErrorCategory.WRONG_DEFAULT
        self.differences.append(
            DocumentDifference(
                field_path=path,
                category=category,
                expected=expected,
                actual=actual,
                default_side=side,
            )
        )

    def record(
        self,
        path: str,
        category: SemanticErrorCategory,
        expected: Any,
        actual: Any,
    ) -> None:
        """A difference with no single leaf field, e.g. a missing feature."""
        self.differences.append(
            DocumentDifference(
                field_path=path,
                category=category,
                expected=expected,
                actual=actual,
            )
        )


def _same(left: Any, right: Any) -> bool:
    """Equality for canonical values.

    Numbers are compared exactly on purpose. Both sides came through the same
    canonical serialization of numbers the model or the corpus wrote as JSON
    literals -- no kernel value is involved -- so a tolerance here would hide
    a model writing 99.999 for 100.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _feature_signature(feature: Mapping[str, Any]) -> str:
    """A feature's identity ignoring its ``id``, for order and membership.

    Used only to tell "the same features in the wrong order" apart from "a
    different set of features". Deliberately crude and deliberately not part
    of any verdict.
    """
    parts = [str(feature.get("type"))]
    for key in sorted(feature):
        if key in ("id", "type"):
            continue
        parts.append(f"{key}={feature[key]!r}")
    return "|".join(parts)


def _compare_vector(
    collector: _Collector,
    path: str,
    expected: Any,
    actual: Any,
    category: SemanticErrorCategory,
    *,
    default: Any = None,
) -> None:
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        collector.compare(path, expected, actual, category, default=default)
        return
    for component in COMPONENTS:
        component_default = (
            None if default is None else default.get(component)
        )
        collector.compare(
            f"{path}.{component}",
            expected.get(component),
            actual.get(component),
            category,
            default=component_default,
        )


def _compare_selector(
    collector: _Collector, path: str, expected: Any, actual: Any
) -> None:
    category = SemanticErrorCategory.WRONG_SELECTOR
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        collector.compare(path, expected, actual, category)
        return
    for key in ("select", "axis"):
        collector.compare(
            f"{path}.{key}", expected.get(key), actual.get(key), category
        )


def _compare_parameter(
    collector: _Collector,
    path: str,
    name: str,
    feature_type: str,
    expected: Any,
    actual: Any,
) -> None:
    """One Section C parameter, labelled by what it means."""
    default = PARAMETER_DEFAULTS.get(name) if name in DEFAULTED_PARAMETERS else None
    if name == "size":
        _compare_vector(
            collector, path, expected, actual, SemanticErrorCategory.WRONG_DIMENSION
        )
    elif name == "position":
        _compare_vector(
            collector,
            path,
            expected,
            actual,
            SemanticErrorCategory.WRONG_POSITION,
            default=default,
        )
    elif name == "axis":
        collector.compare(
            path, expected, actual, SemanticErrorCategory.WRONG_AXIS, default=default
        )
    elif name in LENGTH_PARAMETERS:
        collector.compare(
            path, expected, actual, SemanticErrorCategory.WRONG_DIMENSION
        )
    elif name == "edges":
        _compare_selector(collector, path, expected, actual)
    elif name in FEATURE_REFERENCE_FIELDS.get(feature_type, ()):
        collector.compare(
            path, expected, actual, SemanticErrorCategory.WRONG_REFERENCE
        )
    else:  # pragma: no cover - a guard against the contract growing
        raise AssertionError(
            f"the comparison has no rule for {feature_type}.{name}; "
            "cad_ai.comparison must be extended alongside the specification"
        )


def _compare_feature(
    collector: _Collector,
    index: int,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> None:
    path = f"features[{index}]"
    expected_type = expected.get("type")
    actual_type = actual.get("type")
    collector.compare(
        f"{path}.type",
        expected_type,
        actual_type,
        SemanticErrorCategory.WRONG_FEATURE_TYPE,
    )
    collector.compare(
        f"{path}.id",
        expected.get("id"),
        actual.get("id"),
        SemanticErrorCategory.WRONG_FEATURE_ID,
    )
    if expected_type != actual_type:
        # Parameters are type-specific; comparing a box's `size` against a
        # cylinder's `diameter` would produce noise, not information.
        return
    required, optional = FEATURE_PARAMETERS[str(expected_type)]
    for name in tuple(required) + tuple(optional):
        _compare_parameter(
            collector,
            f"{path}.{name}",
            name,
            str(expected_type),
            expected.get(name),
            actual.get(name),
        )


def compare_documents(
    expected: Optional[Mapping[str, Any]],
    actual: Optional[Mapping[str, Any]],
) -> DocumentComparison:
    """Compare two canonical V1 documents, field by field.

    Neither side is trusted to be well-formed: this is comparison, not
    validation, and it must not raise on a document the validator already
    accepted or on ``None``.
    """
    if expected is None or actual is None:
        return DocumentComparison(status=SemanticStatus.NOT_COMPARED)

    collector = _Collector()
    collector.compare(
        "schema_version",
        expected.get("schema_version"),
        actual.get("schema_version"),
        SemanticErrorCategory.WRONG_SCHEMA_VERSION,
    )
    collector.compare(
        "units",
        expected.get("units"),
        actual.get("units"),
        SemanticErrorCategory.UNIT_ERROR,
    )
    for name in LABEL_ROOT_FIELDS:
        collector.compare(
            name,
            expected.get(name),
            actual.get(name),
            SemanticErrorCategory.WRONG_LABEL,
        )

    expected_features = _features(expected)
    actual_features = _features(actual)
    collector.paths.append("features.length")
    if len(expected_features) != len(actual_features):
        category = (
            SemanticErrorCategory.MISSING_FEATURE
            if len(actual_features) < len(expected_features)
            else SemanticErrorCategory.EXTRA_FEATURE
        )
        collector.record(
            "features.length",
            category,
            len(expected_features),
            len(actual_features),
        )
    else:
        expected_signatures = [
            _feature_signature(feature) for feature in expected_features
        ]
        actual_signatures = [
            _feature_signature(feature) for feature in actual_features
        ]
        if (
            expected_signatures != actual_signatures
            and sorted(expected_signatures) == sorted(actual_signatures)
        ):
            # The same features, permuted. Order is part of the contract, so
            # this is the finding; a field-by-field walk would only restate it.
            collector.record(
                "features",
                SemanticErrorCategory.WRONG_FEATURE_ORDER,
                [feature.get("id") for feature in expected_features],
                [feature.get("id") for feature in actual_features],
            )
            return _finish(collector)

    for index in range(min(len(expected_features), len(actual_features))):
        _compare_feature(
            collector, index, expected_features[index], actual_features[index]
        )
    for index in range(len(actual_features), len(expected_features)):
        collector.record(
            f"features[{index}]",
            SemanticErrorCategory.MISSING_FEATURE,
            expected_features[index].get("id"),
            None,
        )
    for index in range(len(expected_features), len(actual_features)):
        collector.record(
            f"features[{index}]",
            SemanticErrorCategory.EXTRA_FEATURE,
            None,
            actual_features[index].get("id"),
        )
    return _finish(collector)


def _features(document: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    features = document.get("features")
    if not isinstance(features, list):
        return ()
    return tuple(item for item in features if isinstance(item, Mapping))


def _finish(collector: _Collector) -> DocumentComparison:
    differences = tuple(collector.differences)
    if not differences:
        status = SemanticStatus.MATCH
    elif all(not difference.geometric for difference in differences):
        status = SemanticStatus.LABELS_DIFFER
    else:
        status = SemanticStatus.MISMATCH
    return DocumentComparison(
        status=status,
        differences=differences,
        compared_paths=tuple(collector.paths),
    )


def describe(comparison: DocumentComparison, limit: int = 12) -> str:
    """A human-readable difference list, for a report."""
    if comparison.status is SemanticStatus.NOT_COMPARED:
        return "not compared"
    if not comparison.differences:
        return "no differences"
    lines = [
        f"  DIFFERENCE: {difference.field_path}"
        f"\n    expected: {difference.expected!r}"
        f"\n    actual:   {difference.actual!r}"
        f"\n    category: {difference.category.value}"
        for difference in comparison.differences[:limit]
    ]
    if len(comparison.differences) > limit:
        lines.append(
            f"  ... and {len(comparison.differences) - limit} more differences"
        )
    return "\n".join(lines)


__all__ = [
    "CASE_LEVEL_CATEGORIES",
    "COMPONENTS",
    "DEFAULTED_PARAMETERS",
    "LABEL_CATEGORIES",
    "LABEL_ROOT_FIELDS",
    "PARAMETER_DEFAULTS",
    "DocumentComparison",
    "DocumentDifference",
    "SemanticErrorCategory",
    "SemanticStatus",
    "compare_documents",
    "describe",
]
