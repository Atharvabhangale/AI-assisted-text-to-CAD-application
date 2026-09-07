"""FeatureScript generation for the supported V1 subset: a single box.

Translates a validated :class:`~cad_core.model.Part` into Onshape FeatureScript
**source text**.  Nothing here talks to Onshape: there is no API client, no
network access and no code execution.  The output is text for a human to paste
into an Onshape Feature Studio.

Supported subset
----------------
Exactly one feature, of type ``box``, in millimetres.  Anything else raises
:class:`UnsupportedPartError`.  The generator does not repair, reinterpret or
silently skip input outside that subset.

Box semantics (specification Section C.1)
-----------------------------------------
The specification's ``position`` is the box's **minimum corner** and the box
occupies ``[position, position + size]`` on each axis, axis-aligned with no
rotation.  ``fCuboid`` is used precisely because it is defined by two opposite
corners rather than by a centre and extents, so the absolute placement is
expressed directly and no centring convention has to be assumed.

Determinism
-----------
The same :class:`~cad_core.model.Part` always produces byte-identical output.
There are no timestamps, random or generated identifiers, hostnames or
filesystem paths in the generated source.

Evidence for the FeatureScript used here
----------------------------------------
Every construct emitted below was read from the Onshape FeatureScript standard
library source at version ``2960`` (MIT, Copyright (c) 2013-Present PTC Inc.),
via the auto-updating mirror ``github.com/javawizard/onshape-std-library-mirror``:

* Version declaration and import -- ``geometry.fs:1`` is
  ``FeatureScript 2960;`` and ``geometry.fs:17`` is
  ``export import(path : "onshape/std/common.fs", version : "2960.0");``, so the
  declaration takes the bare number and an import takes ``"<number>.0"``.
  ``geometry.fs`` documents itself: "New Feature Studios begin with an import of
  this module".
* ``fCuboid`` reachability -- ``geometry.fs:17`` re-exports ``common.fs``, and
  ``common.fs:61`` re-exports ``primitives.fs``, which defines ``fCuboid``.
  Importing ``geometry.fs`` alone therefore suffices.
* ``fCuboid`` signature and semantics -- ``primitives.fs:101-118``: "Create a
  simple rectangular prism between two specified corners", with ``@field
  corner1 {Vector}`` / ``@field corner2 {Vector}`` and ``@eg vector(0, 0, 0) *
  inch``.  Its body sketches the rectangle on ``XY_PLANE`` moved to
  ``min(corner1[2], corner2[2])`` and extrudes by
  ``abs(corner2[2] - corner1[2])`` -- absolute world-space corners, no centring.
* Empty ``precondition`` -- ``feature.fs`` defines ``dummyFeature`` as a real
  exported feature whose precondition block is empty, and ``context.fs``
  documents a "minimal example following good practices" written as
  ``precondition {}`` whose body calls ``fCuboid``.
* One-argument ``defineFeature`` -- ``feature.fs:120``
  ``export function defineFeature(feature is function) returns function``, so
  the trailing defaults map is optional.
* ``millimeter`` -- ``units.fs:157`` ``export const millimeter = 0.001 * meter;``

``fCuboid``'s own precondition requires ``corner1[dim] != corner2[dim]`` on all
three axes.  Specification rule S10 (every ``size`` component ``> 0``)
guarantees that for any valid part, which is why this generator does not need to
check it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from cad_core.model import ID_PATTERN, Box, Part

#: FeatureScript language version pinned in the generated source.
#:
#: Read from the standard library itself: ``geometry.fs:1`` declares
#: ``FeatureScript 2960;`` and its imports use ``version : "2960.0"``.  The
#: declaration and the import version must agree, and pinning a released version
#: is the mechanism by which FeatureScript keeps a script stable across Onshape
#: releases.  ``fCuboid`` is present in ``primitives.fs`` at this version.
FEATURESCRIPT_VERSION = "2960"

#: Standard library module imported by the generated source.  ``geometry.fs``
#: documents itself as the module new Feature Studios import, and it reaches
#: ``fCuboid`` by re-exporting ``common.fs``, which re-exports ``primitives.fs``.
STANDARD_LIBRARY_PATH = "onshape/std/geometry.fs"

#: Name of the exported custom feature in the generated source.
FEATURE_CONST_NAME = "cadCoreBox"

#: Id component passed to ``fCuboid``.  A constant, rather than the
#: specification's feature id, because the set of characters valid in an
#: Onshape id component is not something this project has verified.
FEATURE_ID_COMPONENT = "box"

#: Specification unit system -> FeatureScript unit constant.
_UNIT_CONSTANTS: Mapping[str, str] = {"mm": "millimeter"}

_ID_RE = re.compile(ID_PATTERN)


class UnsupportedPartError(Exception):
    """Raised when a part is outside the subset this generator supports.

    The generator fails explicitly rather than emitting partial geometry for a
    part it cannot fully express.
    """


def generate_featurescript(part: Part) -> str:
    """Generate Onshape FeatureScript source text for a single-box part.

    Args:
        part: A validated :class:`~cad_core.model.Part`, as returned in
            :attr:`~cad_core.errors.ValidationResult.part`.  The part is assumed
            to be statically valid; this function does not re-validate it and
            will not repair it.

    Returns:
        Complete FeatureScript source text, ending with a newline.  The same
        part always yields exactly the same string.

    Raises:
        TypeError: if ``part`` is not a :class:`~cad_core.model.Part`.  Raw
            dictionaries and JSON text cannot bypass the typed boundary.
        UnsupportedPartError: if the part is outside the supported subset --
            units other than millimetres, a feature count other than one, a
            feature that is not a box, or a feature id outside the pattern the
            specification defines.
    """
    box = _require_supported_box(part)
    unit = _UNIT_CONSTANTS[part.units]

    minimum = (box.position.x, box.position.y, box.position.z)
    maximum = (
        box.position.x + box.size.x,
        box.position.y + box.size.y,
        box.position.z + box.size.z,
    )

    lines = [
        f"FeatureScript {FEATURESCRIPT_VERSION};",
        f'import(path : "{STANDARD_LIBRARY_PATH}", version : "{FEATURESCRIPT_VERSION}.0");',
        "",
        "// Generated by cad-core from a V1 CAD specification.",
        "// Do not edit by hand: regenerate from the specification instead.",
        "//",
        f"// Contract : docs/cad-specification.md, schema version {_comment_safe(part.schema_version)}",
        f"// Part     : {_comment_safe(part.name)}",
        f"// Feature  : '{box.id}' (box)",
        f"// Units    : {_comment_safe(part.units)} -> {unit}",
        "//",
        "// The box is axis-aligned with no rotation, and the specification's",
        "// position is its minimum corner (Section C.1), so the corners below",
        "// are absolute coordinates in the part coordinate system:",
        "//     corner1 = position",
        "//     corner2 = position + size",
        "//",
        "// Onshape execution is not implemented. This is source text to paste",
        "// into an Onshape Feature Studio.",
        "",
        f'annotation {{ "Feature Type Name" : "cad-core box: {box.id}" }}',
        f"export const {FEATURE_CONST_NAME} = defineFeature("
        "function(context is Context, id is Id, definition is map)",
        "    precondition",
        "    {",
        "        // No user-facing parameters: the geometry is fully determined",
        "        // by the CAD specification this source was generated from.",
        "    }",
        "    {",
        f"        // size {_format_length(box.size.x)} x {_format_length(box.size.y)}"
        f" x {_format_length(box.size.z)} {part.units}",
        f'        fCuboid(context, id + "{FEATURE_ID_COMPONENT}", {{',
        f'                "corner1" : {_format_vector(minimum)} * {unit},',
        f'                "corner2" : {_format_vector(maximum)} * {unit}',
        "        });",
        "    });",
    ]
    return "\n".join(lines) + "\n"


def _require_supported_box(part: Any) -> Box:
    """Return the part's single box, or fail explicitly."""
    if not isinstance(part, Part):
        raise TypeError(
            "generate_featurescript requires a typed cad_core.model.Part, such "
            "as the 'part' of a successful validate() result; got "
            f"{type(part).__name__}. Raw specification documents are not accepted."
        )

    if part.units not in _UNIT_CONSTANTS:
        supported = ", ".join(repr(unit) for unit in _UNIT_CONSTANTS)
        raise UnsupportedPartError(
            f"unsupported units {part.units!r}; this generator supports {supported}"
        )

    if len(part.features) != 1:
        raise UnsupportedPartError(
            "this generator supports a part with exactly one feature; got "
            f"{len(part.features)}. Multi-feature histories are not supported yet."
        )

    feature = part.features[0]
    if not isinstance(feature, Box):
        raise UnsupportedPartError(
            f"unsupported feature type {feature.TYPE!r}; this generator supports "
            "only 'box'."
        )

    if _ID_RE.match(feature.id) is None:
        raise UnsupportedPartError(
            f"feature id {feature.id!r} does not match the specification id "
            f"pattern {ID_PATTERN}"
        )
    return feature


def _format_length(value: float) -> str:
    """Format one length as a stable FeatureScript numeric literal."""
    number = float(value)
    if number.is_integer() and abs(number) < 1e16:
        return str(int(number))
    return repr(number)


def _format_vector(components: "tuple[float, float, float]") -> str:
    """Format a 3-component vector as a FeatureScript ``vector(...)`` call."""
    return f"vector({', '.join(_format_length(value) for value in components)})"


def _comment_safe(text: str) -> str:
    """Neutralise free text for use on a ``//`` comment line.

    A part's ``name`` is free text, so it may contain newlines or control
    characters that would end the comment and corrupt the generated source.
    They are replaced with spaces; nothing else about the text is changed.
    """
    return "".join(" " if character < " " or character == "\x7f" else character for character in text)
