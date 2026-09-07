"""Rule codes defined by ``docs/cad-specification.md`` (schema version 1.0.0).

The specification splits validation into two tiers:

* ``S1``-``S20`` -- static rules, decidable from the specification document
  alone.  These are the rules implemented by :mod:`cad_core.validator`.
* ``E1``-``E5`` -- geometric rules, which require an evaluated solid.  They are
  declared in :mod:`cad_core.geometry` and are deliberately not implemented.

The descriptions below are condensed restatements of Section E.1 of the
specification and exist so that an emitted error can be traced back to the
contract text.  The specification document is authoritative.
"""

from __future__ import annotations

from typing import Mapping

STATIC_RULES: Mapping[str, str] = {
    "S1": (
        "The document is a single JSON object with the fields of Section B.1; "
        "schema_version, units, name and features are present."
    ),
    "S2": (
        "features is an array with at least one element, and every element is "
        "an object with a string id and a string type."
    ),
    "S3": (
        "No unknown fields anywhere in the document, and type is one of box, "
        "cylinder, through_hole, subtract, fillet, chamfer."
    ),
    "S4": (
        "schema_version is a valid version string and its major version is "
        "supported by the validator."
    ),
    "S5": "units is \"mm\". No other value is accepted or converted in V1.",
    "S6": (
        "Every reference names a solid that exists in the solid set at the "
        "moment the referencing feature is evaluated."
    ),
    "S7": (
        "Every reference points to a feature that appears strictly earlier in "
        "features."
    ),
    "S8": (
        "Feature ids are unique within the part and match "
        "^[A-Za-z_][A-Za-z0-9_-]*$."
    ),
    "S9": (
        "The first feature is constructive (box or cylinder), and after the "
        "last feature the solid set contains exactly one solid."
    ),
    "S10": "box.size.x, box.size.y, box.size.z are each > 0.",
    "S11": "cylinder.diameter > 0 and cylinder.height > 0.",
    "S12": (
        "Every axis on a cylinder or through_hole is one of \"+X\", \"-X\", "
        "\"+Y\", \"-Y\", \"+Z\", \"-Z\"."
    ),
    "S13": "through_hole.diameter > 0.",
    "S14": "subtract.tools is a non-empty array.",
    "S15": (
        "subtract.target does not appear in subtract.tools, and tools contains "
        "no duplicate ids."
    ),
    "S16": "fillet.radius > 0.",
    "S17": "chamfer.distance > 0.",
    "S18": (
        "An edge selector has select of \"all\" or \"axis_parallel\"; axis is "
        "present with value \"X\", \"Y\" or \"Z\" exactly when select is "
        "\"axis_parallel\"."
    ),
    "S19": (
        "Every position and size is an object with exactly the keys x, y, z, "
        "each a finite JSON number."
    ),
    "S20": "Every length value is a finite number.",
}

# How this implementation divides responsibility where two static rules could
# both be read as covering the same input.  The split is fixed so that a given
# document always produces the same rule code.
#
#   * A missing *required feature parameter* (Section C tables) is reported as
#     S2, the rule governing the structural well-formedness of a feature.  The
#     specification numbers no separate rule for it.
#   * Unknown keys on the root object or on a feature object are S3.  Unknown
#     keys inside a position/size object are S19 and inside an edge selector
#     are S18, because those rules state the exact permitted key sets.
#   * S19 owns position and size values entirely: shape, key set and
#     finiteness.  S20 owns the scalar length values (diameter, height, radius,
#     distance): type and finiteness.  Positivity is always reported by the
#     specific rule for the parameter (S10, S11, S13, S16, S17).
#   * A reference field that is not a string is S2 (malformed structure); a
#     reference that is a string but does not resolve is S6 or S7.
STATIC_RULE_CODES = tuple(STATIC_RULES)
