"""Canonical serialization of a V1 CAD document.

The CAD document *is* the serialized V1 specification (``docs/cad-specification.md``,
schema version 1.0.0). It records engineering intent and feature history, and
it is the only authoritative artifact in the system:

```
CAD document (canonical JSON)
    |-- local B-rep      (cad_core.local_cad)
    |-- FeatureScript    (cad_core.featurescript)
    |-- STEP / IGES / STL
    +-- RenderModel
```

Everything below the document is derived. If a derived artifact is lost it is
rebuilt from the document; if the document is lost, nothing can rebuild it.

What is deliberately **not** in a CAD document: CadQuery or OpenCascade
objects, meshes, triangles, STEP/IGES/STL payloads, FeatureScript source,
generated code, or cached geometry of any kind. This module imports no
geometry backend, and a test asserts that -- ``import cad_core`` and
``import cad_core.serialization`` both work with CadQuery absent.

The pipeline
------------
```
JSON text -> json.loads -> cad_core.validator.validate -> Part
Part      -> serialize_part -> canonical structure -> json.dumps -> JSON text
```

Deserialization runs the **existing** validator; there is no second validation
implementation here and no repair of an invalid document. A document that
fails validation yields no :class:`~cad_core.model.Part`:
:func:`deserialize_part` raises :class:`DocumentValidationError` carrying the
full :class:`~cad_core.errors.ValidationResult`, so every structured error
survives. Callers that prefer the non-raising form keep using
:func:`cad_core.validate` directly.

Canonicalization rules
----------------------
For any valid :class:`~cad_core.model.Part` there is exactly **one** canonical
document and exactly one canonical byte string.

1. **Object key order is fixed**, and taken from the specification's own
   tables rather than written out by hand: the root emits
   ``schema_version``, ``units``, ``name``, ``description`` (only when the part
   has one), then ``features``; a feature emits
   :data:`~cad_core.model.COMMON_FEATURE_FIELDS` followed by its required and
   then optional parameters from
   :data:`~cad_core.model.FEATURE_PARAMETERS`; a position or size emits
   ``x``, ``y``, ``z``; an edge selector emits ``select`` then ``axis``.
   JSON object key order carries no meaning in the contract, so fixing it is
   safe -- and it is fixed explicitly, not left to dictionary insertion order.
2. **Array order is preserved exactly**, never sorted. ``features`` order is
   the design (Section B.4) and ``subtract.tools`` order decides the order of
   subtraction (Section C.4). Sorting either would change the part.
3. **Numbers are JSON numbers**, never strings. Every numeric field of a
   ``Part`` is a Python ``float`` -- the validator converts on the way in --
   so a length written as ``100`` in a source document comes back as ``100.0``.
   Formatting is ``json``'s own, which uses ``repr`` and is therefore the
   shortest representation that round-trips exactly.
4. **Negative zero is normalised to ``0.0``.** ``-0.0 == 0.0`` in Python, so a
   ``Part`` holding ``-0.0`` is equal to one holding ``0.0``; emitting
   ``-0.0`` would give two equal parts two different documents. ``-0.0 mm``
   and ``0.0 mm`` are the same coordinate, and rule S19 cares only that a
   component is finite, so the sign of zero is dropped.
5. **Defaults are always materialised** -- see below.
6. **Whitespace is minimal**: separators ``","`` and ``":"``, no indentation,
   no trailing newline. The canonical bytes are exactly what
   :func:`part_to_json` returns, UTF-8 encoded.
7. **UTF-8, not escapes**: ``ensure_ascii=False``, so a name like
   ``"plaque 100x60 cafe"`` keeps its real characters and the document is
   encoded as UTF-8.

Defaults: always materialised
-----------------------------
Section C makes ``box.position``, ``cylinder.position``, ``cylinder.axis`` and
``through_hole.axis`` optional, and :mod:`cad_core.model` materialises those
defaults when a ``Part`` is built. A typed ``Part`` therefore cannot tell an
omitted ``position`` from one written ``{"x":0,"y":0,"z":0}`` -- the
information is already gone before this module sees it.

The canonical form is therefore **fully materialised**: every optional
parameter with a default is emitted with its value. Two consequences, both
tested:

* a document that omits a default and a document that states it explicitly
  parse to the same ``Part``, serialize to the same canonical bytes and have
  the same hash. Only the materialised form is canonical; the terse form is
  accepted input, not a second canonical representation.
* a canonical document is self-describing: a reader never needs the Section C
  default table to know where a box is.

``description`` is not a default but an optional field with no value when
absent, and the validator rejects ``"description": null``, so it is omitted
when the part has none.

Hashing
-------
:func:`part_hash` returns the hex **SHA-256** of the canonical UTF-8 bytes --
exactly the bytes :func:`part_to_json` produces, encoded UTF-8, with nothing
prepended or appended. It hashes the document and nothing else: no kernel
geometry, no FeatureScript, no exported file. The same ``Part`` always hashes
the same, and no stronger claim is made than SHA-256 itself provides.

Equivalence
-----------
Two parts are semantically equivalent iff their canonical documents are
identical -- :func:`parts_equivalent`. Generated geometry is never compared,
and no geometric tolerance enters document equality.

Versioning
----------
The only version in a CAD document is the specification's own
``schema_version``. This module adds none: a serialization-format version
would be a second thing to keep in step for no benefit, since the canonical
form is a pure function of the specification's own field tables. Application,
geometry-kernel and exporter versions are separate concerns and appear
nowhere in a document.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, Union

from cad_core.errors import ValidationResult
from cad_core.model import (
    COMMON_FEATURE_FIELDS,
    FEATURE_PARAMETERS,
    Box,
    Chamfer,
    Cylinder,
    EdgeSelector,
    Feature,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
    ThroughHole,
)
from cad_core.validator import validate

PathLike = Union[str, "os.PathLike[str]"]

#: Root field order of a canonical document (Section B.1). ``description`` is
#: emitted only when the part carries one.
ROOT_FIELD_ORDER: Tuple[str, ...] = (
    "schema_version",
    "units",
    "name",
    "description",
    "features",
)

#: Component order of a position or a size (Section A.4).
VECTOR_FIELD_ORDER: Tuple[str, ...] = ("x", "y", "z")

#: Field order of an edge selector (Section C.7). ``axis`` is emitted only for
#: ``axis_parallel``.
SELECTOR_FIELD_ORDER: Tuple[str, ...] = ("select", "axis")

#: ``json.dumps`` separators for the canonical form: no whitespace at all.
CANONICAL_SEPARATORS: Tuple[str, str] = (",", ":")

#: Text encoding of a canonical document.
CANONICAL_ENCODING = "utf-8"

#: Hash used by :func:`part_hash`, from :mod:`hashlib`.
HASH_ALGORITHM = "sha256"

#: File extensions :func:`save_part` and :func:`load_part` accept.
DOCUMENT_EXTENSIONS: Tuple[str, ...] = (".json",)


class CadDocumentError(Exception):
    """Base class for CAD document serialization failures."""


class DocumentParseError(CadDocumentError):
    """Raised when a document is not well-formed JSON, or is not an object.

    A syntax error is reported as itself, never as a validation failure: the
    document could not be read at all, so no rule could be checked.
    """


class DocumentValidationError(CadDocumentError):
    """Raised when a well-formed document violates the V1 static rules.

    The full :class:`~cad_core.errors.ValidationResult` is attached, so every
    structured error -- rule code, message, feature id, field path -- survives
    the raise. No :class:`~cad_core.model.Part` is produced and no geometry is
    generated.
    """

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        self.errors = result.errors
        listed = "; ".join(str(error) for error in result.errors)
        super().__init__(
            f"the document violates {len(result.errors)} V1 rule(s): {listed}"
        )

    def rule_codes(self) -> Tuple[str, ...]:
        """Rule codes of every violation, in the validator's order."""
        return self.result.rule_codes()


# --- Part -> canonical document --------------------------------------------


def serialize_part(part: Part) -> Dict[str, Any]:
    """Return the canonical JSON-compatible document for ``part``.

    The result contains only ``dict``, ``list``, ``str`` and ``float`` -- no
    typed model objects, and nothing from a geometry kernel. It is accepted by
    :func:`json.dumps` with no custom encoder, and by
    :func:`cad_core.validate` unchanged.

    Args:
        part: A typed :class:`~cad_core.model.Part`, as returned in the
            ``part`` of a successful :func:`cad_core.validate` result.

    Raises:
        TypeError: if ``part`` is not a ``Part``. A raw dictionary is not
            accepted: this function's input is the typed, validated boundary.
    """
    if not isinstance(part, Part):
        raise TypeError(
            "serialize_part requires a typed cad_core.model.Part, such as the "
            "'part' of a successful validate() result; got "
            f"{type(part).__name__}"
        )

    document: Dict[str, Any] = {}
    for field in ROOT_FIELD_ORDER:
        if field == "features":
            document["features"] = [
                _serialize_feature(feature) for feature in part.features
            ]
        elif field == "description":
            # An optional field with no default: omitted when absent, because
            # the validator rejects an explicit null.
            if part.description is not None:
                document["description"] = part.description
        else:
            document[field] = getattr(part, field)
    return document


def part_to_json(part: Part) -> str:
    """Return the canonical JSON text for ``part``.

    Minimal whitespace, no trailing newline, real UTF-8 characters rather than
    escapes. Repeated calls return an identical string, and
    ``part_to_json(part).encode("utf-8")`` is the byte string
    :func:`part_hash` digests.
    """
    return json.dumps(
        serialize_part(part),
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=False,
    )


def part_to_bytes(part: Part) -> bytes:
    """Return the canonical UTF-8 bytes for ``part``."""
    return part_to_json(part).encode(CANONICAL_ENCODING)


def part_hash(part: Part) -> str:
    """Return the hex SHA-256 of ``part``'s canonical UTF-8 bytes.

    The digest input is exactly :func:`part_to_bytes` -- nothing prepended,
    appended or normalised further. Geometry, FeatureScript and exported files
    are never hashed, and are not reachable from here.
    """
    return hashlib.new(HASH_ALGORITHM, part_to_bytes(part)).hexdigest()


def parts_equivalent(first: Part, second: Part) -> bool:
    """True if two parts have identical canonical documents.

    This is the project's definition of semantic equivalence for V1. Generated
    geometry is not compared and no tolerance is involved: the question is
    whether the two documents describe the same design.
    """
    return part_to_bytes(first) == part_to_bytes(second)


# --- canonical document -> Part --------------------------------------------


def deserialize_part(data: Mapping[str, Any]) -> Part:
    """Validate a JSON-compatible document and return the typed part.

    Runs :func:`cad_core.validate` -- the same static validator every other
    entry point uses. Nothing is repaired, and unknown fields are rejected
    rather than preserved (rule S3).

    Raises:
        DocumentParseError: if ``data`` is not a JSON object (mapping).
        DocumentValidationError: if the document violates any of S1-S20. The
            exception carries the complete
            :class:`~cad_core.errors.ValidationResult`; no part is returned.
    """
    if not isinstance(data, Mapping):
        raise DocumentParseError(
            "a CAD document is a JSON object; got "
            f"{type(data).__name__}"
        )

    result = validate(dict(data))
    if not result.valid or result.part is None:
        raise DocumentValidationError(result)
    return result.part


def part_from_json(text: Union[str, bytes, bytearray]) -> Part:
    """Parse canonical (or any valid) JSON text and return the typed part.

    ``bytes`` are decoded as UTF-8. A syntax error raises
    :class:`DocumentParseError` and never reaches the validator, so a
    malformed file is never reported as a rule violation.
    """
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode(CANONICAL_ENCODING)
        except UnicodeDecodeError as exc:
            raise DocumentParseError(
                f"the document is not valid {CANONICAL_ENCODING}: {exc}"
            ) from exc
    if not isinstance(text, str):
        raise DocumentParseError(
            f"expected JSON text; got {type(text).__name__}"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentParseError(f"the document is not valid JSON: {exc}") from exc
    return deserialize_part(data)


# --- file persistence -------------------------------------------------------


def save_part(part: Part, path: PathLike, *, overwrite: bool = False) -> Path:
    """Write ``part``'s canonical document to ``path`` and return the path.

    The file holds exactly :func:`part_to_bytes` -- the canonical bytes, UTF-8,
    with no trailing newline -- so two saves of the same part produce identical
    files, and a file's bytes are the same bytes :func:`part_hash` digests. No
    geometry is generated, and nothing is cached in the file.

    Args:
        part: The typed part to write.
        path: Destination, ending in ``.json`` (case-insensitive).
        overwrite: ``False`` (the default) refuses to replace an existing file.

    Raises:
        TypeError: if ``part`` is not a ``Part``.
        CadDocumentError: for an unsupported extension, or when the
            destination exists and ``overwrite`` is false.
    """
    destination = Path(os.fspath(path))
    if destination.suffix.lower() not in DOCUMENT_EXTENSIONS:
        supported = ", ".join(repr(item) for item in DOCUMENT_EXTENSIONS)
        raise CadDocumentError(
            f"unsupported document extension {destination.suffix!r}; this "
            f"module writes {supported}"
        )
    payload = part_to_bytes(part)
    if destination.exists() and not overwrite:
        raise CadDocumentError(
            f"{destination} already exists; pass overwrite=True to replace it"
        )
    destination.write_bytes(payload)
    return destination


def load_part(path: PathLike) -> Part:
    """Read a CAD document from ``path`` and return the typed part.

    Raises:
        CadDocumentError: for an unsupported extension or a missing file.
        DocumentParseError: if the file is not valid UTF-8 JSON.
        DocumentValidationError: if the document violates the V1 rules.
    """
    source = Path(os.fspath(path))
    if source.suffix.lower() not in DOCUMENT_EXTENSIONS:
        supported = ", ".join(repr(item) for item in DOCUMENT_EXTENSIONS)
        raise CadDocumentError(
            f"unsupported document extension {source.suffix!r}; this module "
            f"reads {supported}"
        )
    if not source.is_file():
        raise CadDocumentError(f"{source} is not a file")
    return part_from_json(source.read_bytes())


# --- internals --------------------------------------------------------------


def _serialize_feature(feature: Feature) -> Dict[str, Any]:
    """Serialize one feature in the specification's own field order.

    The order comes from :data:`~cad_core.model.COMMON_FEATURE_FIELDS` plus the
    required and optional columns of
    :data:`~cad_core.model.FEATURE_PARAMETERS`, so the canonical key order
    follows the Section C tables instead of a second hand-written list that
    could drift from them.
    """
    kind = feature.TYPE
    required, optional = FEATURE_PARAMETERS[kind]
    serialized: Dict[str, Any] = {}
    for field in COMMON_FEATURE_FIELDS + required + optional:
        serialized[field] = _serialize_field(feature, field)
    return serialized


def _serialize_field(feature: Feature, field: str) -> Any:
    if field == "type":
        return feature.TYPE
    value = getattr(feature, field)
    if isinstance(value, (Position, Size)):
        return _serialize_vector(value)
    if isinstance(value, EdgeSelector):
        return _serialize_selector(value)
    if isinstance(value, tuple):  # subtract.tools -- order is significant
        return list(value)
    if isinstance(value, float):
        return _canonical_number(value)
    return value


def _serialize_vector(value: Union[Position, Size]) -> Dict[str, float]:
    return {
        component: _canonical_number(getattr(value, component))
        for component in VECTOR_FIELD_ORDER
    }


def _serialize_selector(selector: EdgeSelector) -> Dict[str, str]:
    serialized: Dict[str, str] = {}
    for field in SELECTOR_FIELD_ORDER:
        value = getattr(selector, field)
        # 'axis' is present exactly for axis_parallel (rule S18), so an absent
        # axis is omitted rather than emitted as null.
        if value is not None:
            serialized[field] = value
    return serialized


def _canonical_number(value: float) -> float:
    """Return ``value`` with negative zero normalised to ``0.0``.

    ``-0.0 == 0.0``, so two equal parts must not serialize differently. Every
    other float is returned unchanged: ``json`` already emits the shortest
    representation that round-trips exactly.
    """
    number = float(value)
    if number == 0.0:
        return 0.0
    return number


#: Public surface of this module, for :mod:`cad_core`.
__all__ = [
    "CANONICAL_ENCODING",
    "CANONICAL_SEPARATORS",
    "CadDocumentError",
    "DOCUMENT_EXTENSIONS",
    "DocumentParseError",
    "DocumentValidationError",
    "HASH_ALGORITHM",
    "ROOT_FIELD_ORDER",
    "SELECTOR_FIELD_ORDER",
    "VECTOR_FIELD_ORDER",
    "deserialize_part",
    "load_part",
    "part_from_json",
    "part_hash",
    "part_to_bytes",
    "part_to_json",
    "parts_equivalent",
    "save_part",
    "serialize_part",
]
