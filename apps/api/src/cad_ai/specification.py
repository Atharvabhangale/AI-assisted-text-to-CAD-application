"""The model-facing view of the V1 CAD specification, **derived** from it.

There is no second authoritative schema here. Two things are assembled, both
from sources that already exist:

* **the prose** -- sections read verbatim out of ``docs/cad-specification.md``
  at import time. That file stays the single source of truth; if it changes,
  what the model is told changes with it, and a test asserts the extracted
  text is a literal substring of the file.
* **the JSON Schema** -- built from :mod:`cad_core.model`'s own constants:
  ``SCHEMA_VERSION``, ``SUPPORTED_UNITS``, ``AXIS_VALUES``, ``DEFAULT_AXIS``,
  ``ID_PATTERN``, ``REQUIRED_ROOT_FIELDS``, ``OPTIONAL_ROOT_FIELDS`` and
  ``FEATURE_PARAMETERS``. Not one field name, enum value or default is typed
  out again, so the schema cannot drift from the validator's own idea of the
  contract. A test asserts every field it names comes from those constants.

The schema is an *aid to generation*, not a validator. It constrains the
model's output toward the right shape; the authority on validity remains the
existing deserializer plus :func:`cad_core.validator.validate`, which run on
whatever comes back regardless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from cad_core.model import (
    AXIS_VALUES,
    CONSTRUCTIVE_TYPES,
    DEFAULT_AXIS,
    EDGE_SELECT_VALUES,
    FEATURE_PARAMETERS,
    FEATURE_TYPES,
    ID_PATTERN,
    SELECTOR_AXIS_VALUES,
    OPTIONAL_ROOT_FIELDS,
    REQUIRED_ROOT_FIELDS,
    SCHEMA_VERSION,
    SUPPORTED_UNITS,
)

#: The specification document, found by walking up from this file. Located
#: rather than configured: it is repository content, not deployment state.
SPECIFICATION_FILENAME = "cad-specification.md"


def _locate_specification() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / SPECIFICATION_FILENAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"docs/{SPECIFICATION_FILENAME} was not found above {here}"
    )


SPECIFICATION_PATH = _locate_specification()


def specification_text() -> str:
    """The whole specification document, verbatim."""
    return SPECIFICATION_PATH.read_text(encoding="utf-8")


def section(heading: str) -> str:
    """One section of the specification, verbatim, by its exact heading line.

    Returns the heading and everything up to the next heading of the same or
    higher level. Raises :class:`KeyError` if the heading is not present --
    so a renamed section fails loudly here instead of silently shrinking what
    the model is told.
    """
    text = specification_text()
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise KeyError(f"no such specification heading: {heading!r}") from exc
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.startswith("#"):
            continue
        depth = len(line) - len(line.lstrip("#"))
        if depth <= level and line[depth : depth + 1] == " ":
            return "\n".join(lines[start:index]).rstrip()
    return "\n".join(lines[start:]).rstrip()


#: The sections a generating model needs, in document order.
#:
#: Until Stage 31 this stopped at C.2, because the prompt refused modifiers and
#: showing a model a feature it must not use invites it to use one. Now that
#: the prompt admits the whole V1 vocabulary the reasoning inverts: a model
#: allowed to emit a ``through_hole`` must be given C.3's parameter table
#: verbatim, or it will guess field names. C.7 comes with the two selector
#: features that need it, and E.2 with them because a hole that misses its
#: material is an error rather than a quiet no-op.
PROMPT_SECTIONS: Tuple[str, ...] = (
    "## A. Coordinate system",
    "## B. Part structure",
    "### C.1 `box`",
    "### C.2 `cylinder`",
    # Stage 31: the modifier sections, added when the prompt stopped refusing
    # modifiers. The model may now emit these features, so it must be given
    # their parameter tables verbatim rather than left to guess field names.
    # C.3 `through_hole` is not quoted since prompt 2026-11-13.7: the model
    # is not offered that type (see SUPPORTED_FEATURE_TYPES), so its table
    # would describe a field set the response schema cannot produce.
    "### C.4 `subtract`",
    "### C.5 `fillet`",
    "### C.6 `chamfer`",
    "### C.7 Edge selector",
    # Schema 1.1.0: the model may emit a `union`, so it is given C.8 verbatim
    # for the same reason it is given C.3 -- and because C.8 is where the
    # contract says which contacts join two solids and which do not.
    "### C.8 `union`",
    "### E.1 Static rules (`S`) — decidable from the document alone",
    # The geometric rules matter now too: a hole that misses its material or a
    # fillet radius the edge cannot take is an error, not a silent no-op, and
    # the model should avoid describing one.
    "### E.2 Geometric rules (`E`) — require evaluation by the engine",
    "## F. Versioning",
)


def specification_excerpt() -> str:
    """The prompt's specification text: the sections above, verbatim."""
    return "\n\n".join(section(heading) for heading in PROMPT_SECTIONS)


# --- the JSON Schema, derived from cad_core.model's constants ---------------

#: The feature types the prompt and the response schema admit: **the whole V1
#: vocabulary**, taken from :data:`cad_core.model.FEATURE_TYPES` rather than
#: retyped.
#:
#: Until Stage 31 this was only :data:`~cad_core.model.CONSTRUCTIVE_TYPES`.
#: That was a Stage 26 scoping decision made when the AI layer was new, and it
#: had gone stale: the local engine implements ``through_hole``, ``subtract``,
#: ``fillet`` and ``chamfer`` (Stages 10-14.1), the validator enforces their
#: rules, and the specification defines all six. The prompt was therefore
#: refusing parts this system can actually build -- a four-hole plate, the
#: specification's *own* worked example in Section D, came back "unsupported".
#:
#: Widening it here widens the prompt text and the derived JSON Schema
#: together, because both read this tuple. It changes **no** CAD rule: the
#: validator, the engine and the specification are untouched, and a document
#: using these features had always been valid V1 CAD.
#:
#: **Since prompt ``2026-11-13.7`` ``through_hole`` is held back.** Schema
#: 1.2.0 added a taper and polygon sides to ``cylinder`` and a pattern to
#: ``subtract``/``union``, and the structured-output compiler refuses the
#: grammar that results -- measured live: any schema with the six existing
#: branches plus the new parameters is "too large", on Haiku and Sonnet alike,
#: while the same schema without the ``through_hole`` branch (and without the
#: nullable ``document``) compiles. A through-hole is exactly a cylinder tool
#: that spans the part, subtracted -- and a patterned subtract makes a whole
#: bolt circle one feature -- so the model loses no shape. ``through_hole``
#: remains valid V1 CAD: the specification, validator and engine still accept
#: it, and documents that use it still build.
SUPPORTED_FEATURE_TYPES: Tuple[str, ...] = tuple(
    name for name in FEATURE_TYPES if name != "through_hole"
)

#: The types V1 defines but the model is not offered: ``through_hole`` (see
#: above). They stay valid in documents; the model writes the equivalent.
UNSUPPORTED_FEATURE_TYPES: Tuple[str, ...] = tuple(
    name for name in FEATURE_TYPES if name not in SUPPORTED_FEATURE_TYPES
)


def _vector(description: str, *, positive: bool) -> Dict[str, Any]:
    """A position or a size: exactly x, y, z, each a number (rule S19)."""
    component: Dict[str, Any] = {"type": "number"}
    if positive:
        component = {"type": "number", "exclusiveMinimum": 0}
    return {
        "type": "object",
        "description": description,
        "properties": {axis: dict(component) for axis in ("x", "y", "z")},
        "required": ["x", "y", "z"],
        "additionalProperties": False,
    }


#: How each Section C parameter is expressed in JSON Schema. Keyed by the
#: parameter names :data:`~cad_core.model.FEATURE_PARAMETERS` already lists,
#: so a parameter that exists in the model but is missing here is a loud
#: KeyError rather than a silently omitted field.
_PARAMETER_SCHEMAS: Mapping[str, Dict[str, Any]] = {
    "size": _vector("Extents along each axis. Every component > 0.", positive=True),
    "position": _vector(
        "An absolute point in the part coordinate system.", positive=False
    ),
    "diameter": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Diameter, in the part's declared unit.",
    },
    "height": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Height along the axis, in the part's declared unit.",
    },
    "axis": {
        "type": "string",
        "enum": list(AXIS_VALUES),
        "description": (
            f"A signed principal direction. Defaults to {DEFAULT_AXIS} when "
            "omitted."
        ),
    },
    # --- modifier parameters (Sections C.3-C.7), added in Stage 31 ----------
    "target": {
        "type": "string",
        "pattern": ID_PATTERN,
        "description": (
            "The id of the solid this feature modifies: always the id of a "
            "box or cylinder, never the id of a modifier. It must appear "
            "strictly earlier in the feature list (rule S7)."
        ),
    },
    "tools": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "pattern": ID_PATTERN},
        "description": (
            "Ids of the solids this feature consumes: a subtract removes "
            "them from the target, a union fuses them into it. Each must "
            "appear strictly earlier in the feature list (rule S7)."
        ),
    },
    # --- schema 1.2.0: taper, polygon sides, pattern ------------------------
    "top_diameter": {
        "type": "number",
        "description": (
            "Diameter at the far end of the cylinder. Equal to `diameter` for "
            "a straight cylinder; smaller or larger for a taper; 0 for a "
            "pointed cone (round only)."
        ),
    },
    "sides": {
        "type": "integer",
        "description": "0 for round; 3-12 for a regular polygon prism (6 = hexagon).",
    },
    "pattern_count": {
        "type": "integer",
        "description": (
            "How many copies of each tool, 1-64. 1 with angle 0 and step 0 "
            "means the tools exactly as placed."
        ),
    },
    "pattern_axis": {
        "type": "string",
        "enum": list(SELECTOR_AXIS_VALUES),
        "description": "The axis line through the origin that copies turn about and step along.",
    },
    "pattern_angle": {
        "type": "number",
        "description": "Degrees each copy turns further about pattern_axis (360/n for n around).",
    },
    "pattern_step": {
        "type": "number",
        "description": "Distance each copy moves further along pattern_axis.",
    },
    "radius": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Fillet radius, in the part's declared unit.",
    },
    "distance": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Chamfer distance, in the part's declared unit.",
    },
    "edges": {
        "type": "object",
        "description": (
            "Which edges of the target to operate on. `axis` is present "
            "exactly when `select` is \"axis_parallel\" (rule S18)."
        ),
        "properties": {
            "select": {"type": "string", "enum": list(EDGE_SELECT_VALUES)},
            "axis": {"type": "string", "enum": list(SELECTOR_AXIS_VALUES)},
        },
        "required": ["select"],
        "additionalProperties": False,
    },
}


def feature_groups() -> Tuple[Tuple[str, ...], ...]:
    """The supported feature types, grouped by identical parameter tables.

    ``subtract`` and ``union`` take exactly the same parameters, so they share
    one schema branch with ``type`` as an enum. Each branch costs
    structured-output grammar, and the compiler refuses a grammar past a size
    limit -- measured: the seventh branch, ``union``, pushed this schema over
    it. Grouping loses nothing, because ``type`` still names exactly one
    feature and the validator still checks every rule.
    """
    groups: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], list] = {}
    for name in SUPPORTED_FEATURE_TYPES:
        groups.setdefault(tuple(FEATURE_PARAMETERS[name]), []).append(name)
    return tuple(tuple(names) for names in groups.values())


def _feature_schema(feature_types: Sequence[str]) -> Dict[str, Any]:
    """The schema for one group of feature types sharing a parameter table.

    **Every parameter is required here**, including those the specification
    gives a default (``position``, ``axis``). The model writes them out
    explicitly, which is a valid document; and an optional property costs far
    more grammar than a required one, so leaving them optional is what pushes
    the schema past the compiler's limit.
    """
    names = tuple(feature_types)
    required, optional = FEATURE_PARAMETERS[names[0]]
    properties: Dict[str, Any] = {
        "id": {
            "type": "string",
            "pattern": ID_PATTERN,
            "description": "Unique within the part.",
        },
        "type": (
            {"const": names[0]}
            if len(names) == 1
            else {"type": "string", "enum": list(names)}
        ),
    }
    for name in tuple(required) + tuple(optional):
        properties[name] = dict(_PARAMETER_SCHEMAS[name])
    return {
        "type": "object",
        "title": " / ".join(names),
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def document_schema() -> Dict[str, Any]:
    """A JSON Schema for a V1 CAD document restricted to this stage's subset.

    Every name, enum and default in it comes from :mod:`cad_core.model`. It is
    a generation aid: the existing validator is still the authority, and a
    document that satisfies this schema can still be invalid (rule S9, for
    instance, is not expressible here).
    """
    root: Dict[str, Any] = {
        "type": "object",
        "description": (
            "A V1 CAD specification document. A data document, never a "
            "program."
        ),
        "properties": {
            "schema_version": {
                "const": SCHEMA_VERSION,
                "description": "The contract version this document conforms to.",
            },
            "units": {
                "type": "string",
                "enum": list(SUPPORTED_UNITS),
                "description": "The unit every length in the document is in.",
            },
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "A short human-readable identifier for the part.",
            },
            "description": {
                "type": "string",
                "description": "Free text. Carries no geometric meaning.",
            },
            "features": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Ordered feature list, evaluated first to last. The "
                    "order is significant."
                ),
                "items": {
                    "anyOf": [
                        _feature_schema(group) for group in feature_groups()
                    ]
                },
            },
        },
        # The optional `description` is required here too, for the same
        # grammar-size reason as the feature parameters.
        "required": list(REQUIRED_ROOT_FIELDS) + list(OPTIONAL_ROOT_FIELDS),
        "additionalProperties": False,
    }
    known = set(REQUIRED_ROOT_FIELDS) | set(OPTIONAL_ROOT_FIELDS)
    missing = known - set(root["properties"])
    if missing:  # pragma: no cover - a guard against the contract growing
        raise AssertionError(
            f"the derived schema is missing root fields: {sorted(missing)}"
        )
    return root


#: The fields of the model's answer, **in the order they are emitted**.
#: Structured output is written in schema order, so the order is a design
#: decision and not a layout: the model commits to a ``status``, works the
#: geometry out in ``plan``, states what it assumed in ``assumptions``, and only
#: then writes the ``document`` -- with the arithmetic already on the page.
RESPONSE_FIELDS: Tuple[str, ...] = (
    "status",
    "plan",
    "assumptions",
    "document",
    "summary",
    "questions",
    "issues",
)


def response_schema() -> Dict[str, Any]:
    """The schema of the model's whole answer.

    The CAD document is **one field**, kept apart from the model's prose and
    from its questions, so explanatory text can never become CAD semantics.

    **Every field is required.** A field that does not apply is written as
    an empty string, an empty array or (``document``) a placeholder rather
    than left out. That is deliberate:
    with optional fields, a model could answer ``status: "document"`` and
    simply never emit ``document`` -- a measured failure. A required field
    cannot be skipped by a schema-constrained decoder, so the model has to
    write a document. (Until prompt ``2026-11-13.7`` it could be ``null``; that
    union cost grammar the 1.2.0 parameters needed.)

    The fields come in :data:`RESPONSE_FIELDS` order, which is the order they
    are generated in.
    """
    properties: Dict[str, Any] = {
        "status": {
            "type": "string",
            "enum": ["document", "needs_clarification", "unsupported"],
            "description": (
                "'document' when 'document' below is a complete CAD document "
                "for the request -- the normal answer. 'needs_clarification' "
                "only when the request gives nothing to build from. "
                "'unsupported' only when the request is not for a physical "
                "object or asks for something other than a CAD document."
            ),
        },
        "plan": {
            "type": "string",
            "description": (
                "A few terse lines, written BEFORE the document: each solid "
                "with its coordinates and extents, where solids overlap, and "
                "the feature order. Working notes only; nothing reads them "
                "for meaning. Empty when status is not 'document'."
            ),
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One short entry for each choice the request did not state: "
                "an assumed unit, a chosen or converted dimension, a default "
                "position, a shape that was simplified. Empty when nothing "
                "was assumed, and when status is not 'document'."
            ),
        },
        # Not nullable: `anyOf [document, null]` is a union around the whole
        # feature grammar, and dropping it is part of what lets the 1.2.0
        # parameters compile. The answer is read only when status is
        # "document", so a placeholder is harmless and never validated.
        "document": dict(
            document_schema(),
            description=(
                "The CAD document when status is 'document'. Otherwise an "
                "unused placeholder with an empty features list."
            ),
        ),
        "summary": {
            "type": "string",
            "description": (
                "One sentence on how the request was interpreted. "
                "Explanatory only; it carries no geometric meaning."
            ),
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "The questions to put to the user when status is "
                "'needs_clarification'. Empty otherwise."
            ),
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Why the request is unsupported, when status is "
                "'unsupported'. Empty otherwise."
            ),
        },
    }
    return {
        "type": "object",
        "properties": {name: properties[name] for name in RESPONSE_FIELDS},
        "required": list(RESPONSE_FIELDS),
        "additionalProperties": False,
    }


def optional_property_counts(schema: Any) -> Tuple[int, int]:
    """``(total, largest in one object)`` optional properties in ``schema``.

    A property is optional when its object does not list it in ``required``.
    Structured-output compilers bound both numbers, so a test holds this
    schema under the bounds; the count lives here, beside the schema it
    describes, so the test and any other caller count the same way.
    """
    total = 0
    largest = 0

    def walk(node: Any) -> None:
        nonlocal total, largest
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                required = node.get("required") or ()
                optional = sum(1 for name in properties if name not in required)
                total += optional
                largest = max(largest, optional)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return total, largest


def schema_field_names(schema: Any) -> Sequence[str]:
    """Every property name appearing anywhere in ``schema``. For tests."""
    found: list = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                found.extend(properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return tuple(found)


__all__ = [
    "PROMPT_SECTIONS",
    "RESPONSE_FIELDS",
    "SPECIFICATION_PATH",
    "SUPPORTED_FEATURE_TYPES",
    "UNSUPPORTED_FEATURE_TYPES",
    "document_schema",
    "feature_groups",
    "optional_property_counts",
    "response_schema",
    "schema_field_names",
    "section",
    "specification_excerpt",
    "specification_text",
]
