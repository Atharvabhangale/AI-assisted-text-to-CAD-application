"""Rule codes defined by ``docs/cad-specification.md`` (schema version 1.2.0).

The specification splits validation into two tiers:

* ``S1``-``S28`` -- static rules, decidable from the specification document
  alone.  These are the rules implemented by :mod:`cad_core.validator`.
  ``S21`` and ``S22`` were added with the ``union`` feature in ``1.1.0``, and
  ``S23``-``S28`` with the taper, polygon and pattern parameters in ``1.2.0``;
  no earlier rule was renumbered or changed in meaning.
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
        "cylinder, through_hole, subtract, fillet, chamfer, union. union is "
        "defined from schema version 1.1.0 and is an unknown type in a "
        "document that declares an earlier version; cylinder.top_diameter, "
        "cylinder.sides and the pattern_* parameters of subtract and union "
        "are defined from 1.2.0 and are unknown fields in a document that "
        "declares an earlier version."
    ),
    "S4": (
        "schema_version is a valid version string and its major version is "
        "supported by the validator."
    ),
    "S5": "units is \"mm\". No other value is accepted or converted in V1.",
    "S6": (
        "Every reference names a solid that exists in the solid set at the "
        "moment the referencing feature is evaluated -- created earlier and "
        "not consumed by a subtract or a union."
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
    "S20": (
        "Every length value is a finite number. top_diameter may be 0 (S23) "
        "and pattern_step may be negative or 0 (S27)."
    ),
    "S21": "union.tools is a non-empty array.",
    "S22": (
        "union.target does not appear in union.tools, and tools contains no "
        "duplicate ids."
    ),
    "S23": (
        "cylinder.top_diameter is a finite number >= 0; when cylinder.sides "
        "is not 0 it is > 0."
    ),
    "S24": (
        "cylinder.sides is an integer equal to 0 or from 3 to 12. A number "
        "with no fractional part is an integer: 6 and 6.0 are the same value."
    ),
    "S25": "pattern_count is an integer from 1 to 64.",
    "S26": "pattern_axis is one of \"X\", \"Y\", \"Z\".",
    "S27": (
        "pattern_angle and pattern_step are finite numbers, and "
        "|pattern_angle| <= 360."
    ),
    "S28": (
        "When pattern_count > 1 no two copies coincide: pattern_angle and "
        "pattern_step are not both 0, and when pattern_step is 0 no multiple "
        "j * pattern_angle with 1 <= j < pattern_count is a whole number of "
        "turns (a multiple of 360)."
    ),
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
#   * 'subtract' and 'union' carry the same 'tools' parameter and the same two
#     structural requirements on it, under separate codes: S14/S15 are stated
#     for subtract and S21/S22 for union.  The code reported is always the one
#     for the feature's own type, so S14 and S15 never appear on a union.
#   * A 'union' in a document whose declared schema_version predates 1.1.0 is
#     S3 (a feature type unknown to that version), reported once on the
#     feature's 'type'.  Its parameters are then not examined, exactly as for
#     any other unknown type.
#   * A 1.2.0 parameter (top_diameter, sides, pattern_*) in a document whose
#     declared schema_version predates 1.2.0 is S3 (a field unknown to that
#     version), reported once on the parameter.  Its value is then not
#     examined, exactly as for any other unknown field.
#   * Each 1.2.0 parameter is owned entirely by its own rule -- type,
#     finiteness, integrality and range: top_diameter by S23, sides by S24,
#     pattern_count by S25, pattern_axis by S26, pattern_angle and
#     pattern_step by S27.  S20 is never reported for them, although
#     top_diameter and pattern_step are lengths, because S23 and S27 state
#     their type themselves.  S28 relates three valid values to each other and
#     is reported only when pattern_count, pattern_angle and pattern_step are
#     each valid, on 'pattern_count'.
#   * An integer parameter (sides, pattern_count) accepts a JSON number with
#     no fractional part, so 6 and 6.0 are the same value -- JSON itself does
#     not distinguish them, and neither does JSON Schema's "integer" -- and the
#     typed value is a Python int.  6.5, booleans and strings are rejected.
STATIC_RULE_CODES = tuple(STATIC_RULES)
