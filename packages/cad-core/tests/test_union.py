"""The ``union`` feature across the validator, the engine and the pipeline.

Boolean union (specification Section C.8, schema version 1.1.0): one target,
one or more tool solids, fused in ``tools`` order, target replaced in place,
tools consumed. It is the only way two solids become one, so it is what makes
an L-bracket, a boss on a plate or a flange on a tube expressible at all.

Three things get the attention here:

* **the contract** -- static rules S21 and S22, the solid-set rules S6/S7/S9 as
  they apply to a feature that consumes solids, and the version gate that
  keeps ``union`` out of a document declaring 1.0.0;
* **the geometry** -- expected volumes are closed forms computed in the test
  (``V1 + V2 - V_overlap``), never read back from the kernel, and compared
  with explicit tolerances. Topology counts are measured and recorded as
  backend observations, not treated as contract;
* **what must not happen silently** -- operands that do not form one body are
  rule E3, whether they are apart or touch only along an edge, along a tangent
  line or at a point.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Sequence

from cad_core import validate
from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
    ServiceFailure,
)
from cad_core.build_job import BuildStatus
from cad_core.featurescript import UnsupportedPartError, generate_featurescript
from cad_core.geometry import GEOMETRIC_RULE_CODES, GEOMETRIC_RULES
from cad_core.iges_export import export_iges, read_iges
from cad_core.local_cad import (
    GeometryOperationError,
    LocalCadResult,
    build_part,
)
from cad_core.model import (
    CONSTRUCTIVE_TYPES,
    FEATURE_INTRODUCED_IN,
    FEATURE_PARAMETERS,
    FEATURE_REFERENCE_FIELDS,
    FEATURE_TYPES,
    MODIFIER_TYPES,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_MAJOR,
    TOOL_CONSUMING_TYPES,
    Box,
    Part,
    Position,
    Size,
    UnionFeature,
)
from cad_core.render_model import build_render_model
from cad_core.rules import STATIC_RULE_CODES, STATIC_RULES
from cad_core.serialization import (
    part_from_json,
    part_hash,
    part_to_json,
    parts_equivalent,
    serialize_part,
)
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl

SPECIFICATION_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "cad-specification.md"
)

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres.
TOLERANCE_MM = 1e-6

#: Volume, in cubic millimetres. The largest deviation measured on any
#: geometry in this module is of the order of 1e-11 mm^3.
VOLUME_TOLERANCE_MM3 = 1e-6

# --- the primary test geometry ---------------------------------------------
#
# The specification's own Section D.2 example: an L-bracket. A 60x40x5 base
# at the origin and a 5x40x45 upright standing on it at x = 0, sharing the
# 5x40 face at z = 5.

BASE_SIZE = (60.0, 40.0, 5.0)
UPRIGHT_SIZE = (5.0, 40.0, 45.0)
UPRIGHT_POSITION = (0.0, 0.0, 5.0)

BASE_VOLUME = BASE_SIZE[0] * BASE_SIZE[1] * BASE_SIZE[2]
UPRIGHT_VOLUME = UPRIGHT_SIZE[0] * UPRIGHT_SIZE[1] * UPRIGHT_SIZE[2]
#: Face-sharing operands overlap in no volume, so the volumes simply add.
BRACKET_VOLUME = BASE_VOLUME + UPRIGHT_VOLUME

#: Area of the L cross-section in the XZ plane; the bracket is that profile
#: extruded 40 mm along Y.
BRACKET_PROFILE_AREA = BASE_SIZE[0] * BASE_SIZE[2] + UPRIGHT_SIZE[0] * UPRIGHT_SIZE[2]
BRACKET_DEPTH = BASE_SIZE[1]

BRACKET_MINIMUM = (0.0, 0.0, 0.0)
BRACKET_MAXIMUM = (60.0, 40.0, 50.0)

#: SHA-256 of the canonical bytes of :func:`bracket_document`. Pinned so that
#: any change to how a union serializes shows up as a failure here.
BRACKET_DOCUMENT_HASH = (
    "cc95ee561da74a2cdf96727bef9bd59e95d97baf401fc0aba0920c8c1e1ab49c"
)

#: The Section D.1 plate's hash, as pinned since Stage 15 in
#: ``test_serialization.py``. A 1.0.0 document; it must not move.
SECTION_D_PLATE_HASH = (
    "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
)


def box_feature(
    identifier: str, size: Sequence[float], position: Sequence[float] = (0, 0, 0)
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "type": "box",
        "size": {"x": size[0], "y": size[1], "z": size[2]},
        "position": {"x": position[0], "y": position[1], "z": position[2]},
    }


def cylinder_feature(
    identifier: str,
    diameter: float,
    height: float,
    position: Sequence[float],
    axis: str = "+Z",
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "type": "cylinder",
        "diameter": diameter,
        "height": height,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "axis": axis,
    }


def union_feature(
    target: str, tools: Sequence[Any], identifier: str = "join"
) -> Dict[str, Any]:
    return {"id": identifier, "type": "union", "target": target, "tools": list(tools)}


def document(
    features: List[Dict[str, Any]],
    name: str = "joined",
    schema_version: str = "1.1.0",
) -> Dict[str, Any]:
    return {
        "schema_version": schema_version,
        "units": "mm",
        "name": name,
        "features": features,
    }


def bracket_features() -> List[Dict[str, Any]]:
    return [
        box_feature("base", BASE_SIZE),
        box_feature("upright", UPRIGHT_SIZE, UPRIGHT_POSITION),
        union_feature("base", ["upright"], "join_upright"),
    ]


def bracket_document() -> Dict[str, Any]:
    return document(bracket_features(), name="l-bracket-60x40x50")


def cube(identifier: str, position: Sequence[float] = (0, 0, 0)) -> Dict[str, Any]:
    """A 10 mm cube, the unit of the contact tests."""
    return box_feature(identifier, (10.0, 10.0, 10.0), position)


CUBE_VOLUME = 1000.0


def specification_examples() -> List[Dict[str, Any]]:
    """Every JSON example in Section D of the specification, in order."""
    text = SPECIFICATION_PATH.read_text(encoding="utf-8")
    section = re.search(
        r"^## D\. Example specification$(.*?)^## E\.", text, re.MULTILINE | re.DOTALL
    )
    assert section is not None, f"Section D not found in {SPECIFICATION_PATH}"
    blocks = re.findall(
        r"^```json$\n(.*?)^```$", section.group(1), re.MULTILINE | re.DOTALL
    )
    return [json.loads(block) for block in blocks]


class UnionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def build_document(self, doc: Dict[str, Any]) -> LocalCadResult:
        return build_part(self.part_from(doc))

    def build(self) -> LocalCadResult:
        return self.build_document(bracket_document())

    def geometry_error(self, doc: Dict[str, Any]) -> str:
        """Build a statically valid document and return the engine's refusal."""
        part = self.part_from(doc)
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        return str(caught.exception)

    def assertRuleAt(self, doc: Dict[str, Any], rule: str, path: str) -> Any:
        result = validate(doc)
        self.assertFalse(result.valid)
        matching = [
            error
            for error in result.errors
            if error.rule == rule and error.field_path == path
        ]
        self.assertTrue(
            matching,
            msg=f"expected {rule} at {path}; got {[str(e) for e in result.errors]}",
        )
        return matching[0]

    def assertTripleAlmostEqual(
        self, actual: Any, expected: tuple, delta: float = TOLERANCE_MM
    ) -> None:
        values = (
            (actual.x, actual.y, actual.z) if hasattr(actual, "x") else tuple(actual)
        )
        for axis, (got, want) in enumerate(zip(values, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- the contract constants ------------------------------------------------


class TestContractConstants(unittest.TestCase):
    """What changed in the vocabulary, and what deliberately did not."""

    def test_the_schema_version_is_1_2_0(self) -> None:
        """1.1.0 added ``union``; 1.1.1 narrowed edge selectors to sharp edges;
        1.2.0 added the taper, polygon and pattern parameters."""
        self.assertEqual(SCHEMA_VERSION, "1.2.0")
        # A MINOR change (Section F): the major version did not move.
        self.assertEqual(SUPPORTED_SCHEMA_MAJOR, 1)

    def test_union_is_the_seventh_feature_type_and_a_modifier(self) -> None:
        self.assertEqual(
            FEATURE_TYPES,
            ("box", "cylinder", "through_hole", "subtract", "fillet", "chamfer", "union"),
        )
        self.assertIn("union", MODIFIER_TYPES)
        self.assertNotIn("union", CONSTRUCTIVE_TYPES)

    def test_the_six_1_0_0_types_are_unchanged_and_in_their_old_order(self) -> None:
        original = tuple(
            kind for kind in FEATURE_TYPES if kind not in FEATURE_INTRODUCED_IN
        )
        self.assertEqual(
            original,
            ("box", "cylinder", "through_hole", "subtract", "fillet", "chamfer"),
        )
        self.assertEqual(FEATURE_INTRODUCED_IN, {"union": (1, 1)})

    def test_union_has_exactly_the_parameters_of_subtract(self) -> None:
        # 1.2.0 gave both the same four optional pattern parameters.
        self.assertEqual(
            FEATURE_PARAMETERS["union"],
            (
                ("target", "tools"),
                ("pattern_count", "pattern_axis", "pattern_angle", "pattern_step"),
            ),
        )
        self.assertEqual(FEATURE_PARAMETERS["union"], FEATURE_PARAMETERS["subtract"])
        self.assertEqual(FEATURE_REFERENCE_FIELDS["union"], ("target", "tools"))
        self.assertEqual(TOOL_CONSUMING_TYPES, ("subtract", "union"))

    def test_the_typed_feature_does_not_shadow_typing_union(self) -> None:
        import typing

        import cad_core.model as model

        self.assertEqual(UnionFeature.TYPE, "union")
        self.assertIs(model.Union, typing.Union)
        self.assertFalse(isinstance(model.Union, type))

    def test_two_static_rules_were_added_and_none_renumbered(self) -> None:
        # S23-S28 followed in 1.2.0, appended after S22 for the same reason.
        self.assertEqual(
            STATIC_RULE_CODES, tuple(f"S{number}" for number in range(1, 29))
        )
        self.assertIn("union.tools", STATIC_RULES["S21"])
        self.assertIn("non-empty", STATIC_RULES["S21"])
        self.assertIn("union.target", STATIC_RULES["S22"])
        self.assertIn("duplicate", STATIC_RULES["S22"])
        # S14 and S15 still speak about subtract, and only about subtract.
        self.assertEqual(STATIC_RULES["S14"], "subtract.tools is a non-empty array.")
        self.assertIn("subtract.target", STATIC_RULES["S15"])
        self.assertNotIn("union", STATIC_RULES["S14"] + STATIC_RULES["S15"])

    def test_no_geometric_rule_was_added(self) -> None:
        """A disconnected union is the existing E3, not a new code."""
        self.assertEqual(GEOMETRIC_RULE_CODES, ("E1", "E2", "E3", "E4", "E5"))
        self.assertIn("single connected solid", GEOMETRIC_RULES["E3"])
        self.assertIn("union", GEOMETRIC_RULES["E3"])

    def test_the_specification_document_defines_union(self) -> None:
        text = SPECIFICATION_PATH.read_text(encoding="utf-8")
        for required in (
            "Schema version described by this document: **`1.2.0`**",
            "### C.8 `union`",
            "| **S21** | `union.tools` is a non-empty array. |",
            "| **S22** |",
            "| `type` | `\"union\"` | yes |",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        # The statement that used to exclude it is gone; intersection is
        # still excluded.
        self.assertNotIn("Union and intersection are not part of this", text)
        self.assertIn("intersection is not part of this contract", text)

    def test_the_headings_other_layers_look_up_are_unchanged(self) -> None:
        """``union`` was appended as C.8 so no existing section moved.

        The AI layer extracts sections of this file by their exact heading
        line, and docstrings throughout the code cite "Section C.4",
        "Section C.7" and so on. Renumbering would have broken both.
        """
        lines = SPECIFICATION_PATH.read_text(encoding="utf-8").splitlines()
        for heading in (
            "## A. Coordinate system",
            "## B. Part structure",
            "### C.1 `box`",
            "### C.2 `cylinder`",
            "### C.3 `through_hole`",
            "### C.4 `subtract`",
            "### C.5 `fillet`",
            "### C.6 `chamfer`",
            "### C.7 Edge selector",
            "## D. Example specification",
            "### E.1 Static rules (`S`) — decidable from the document alone",
            "### E.2 Geometric rules (`E`) — require evaluation by the engine",
            "## F. Versioning",
        ):
            with self.subTest(heading=heading):
                self.assertEqual(lines.count(heading), 1)


# --- static validation: the shape of a union -------------------------------


class TestStaticValidation(UnionTestCase):
    def test_a_union_validates_to_a_typed_feature(self) -> None:
        part = self.part_from(bracket_document())
        self.assertEqual(len(part.features), 3)
        self.assertEqual(
            part.features[2],
            UnionFeature(id="join_upright", target="base", tools=("upright",)),
        )
        self.assertIsInstance(part.features[2].tools, tuple)
        self.assertEqual(part.schema_version, "1.1.0")

    def test_tools_order_is_preserved_in_the_typed_feature(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("b", (5, 0, 0)),
                cube("c", (0, 5, 0)),
                union_feature("a", ["c", "b"]),
            ]
        )
        self.assertEqual(self.part_from(doc).features[3].tools, ("c", "b"))

    def test_an_empty_tool_list_is_s21(self) -> None:
        doc = document([cube("a"), union_feature("a", [])])
        self.assertRuleAt(doc, "S21", "features[1].tools")

    def test_tools_that_is_not_an_array_is_s21(self) -> None:
        for value in ("b", {"b": 1}, 3, None, True):
            with self.subTest(value=value):
                doc = document([cube("a"), cube("b", (5, 0, 0)), union_feature("a", [])])
                doc["features"][2]["tools"] = value
                self.assertRuleAt(doc, "S21", "features[2].tools")

    def test_a_tool_entry_that_is_not_a_string_is_s2(self) -> None:
        doc = document([cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["b", 7])])
        self.assertRuleAt(doc, "S2", "features[2].tools[1]")

    def test_the_target_listed_as_a_tool_is_s22(self) -> None:
        doc = document(
            [cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["a", "b"])]
        )
        error = self.assertRuleAt(doc, "S22", "features[2].tools[0]")
        self.assertIn("'a'", error.message)

    def test_a_duplicated_tool_is_s22(self) -> None:
        doc = document(
            [cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["b", "b"])]
        )
        error = self.assertRuleAt(doc, "S22", "features[2].tools[1]")
        self.assertIn("duplicate", error.message)

    def test_missing_parameters_are_s2(self) -> None:
        for field in ("target", "tools"):
            with self.subTest(field=field):
                doc = document(
                    [cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["b"])]
                )
                del doc["features"][2][field]
                self.assertRuleAt(doc, "S2", f"features[2].{field}")

    def test_a_target_that_is_not_a_string_is_s2(self) -> None:
        doc = document([cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["b"])])
        doc["features"][2]["target"] = ["a"]
        self.assertRuleAt(doc, "S2", "features[2].target")

    def test_an_unknown_field_is_s3(self) -> None:
        """Strictness holds for the new type: a union takes no geometry."""
        for field, value in (
            ("position", {"x": 0, "y": 0, "z": 0}),
            ("clean", True),
            ("operation", "fuse"),
        ):
            with self.subTest(field=field):
                doc = document(
                    [cube("a"), cube("b", (5, 0, 0)), union_feature("a", ["b"])]
                )
                doc["features"][2][field] = value
                self.assertRuleAt(doc, "S3", f"features[2].{field}")

    def test_union_violations_never_report_the_subtract_rules(self) -> None:
        """S14/S15 are subtract's; a union's own codes are S21/S22."""
        documents = [
            document([cube("a"), union_feature("a", [])]),
            document([cube("a"), cube("b"), union_feature("a", ["a", "b"])]),
            document([cube("a"), cube("b"), union_feature("a", ["b", "b"])]),
        ]
        for doc in documents:
            codes = validate(doc).rule_codes()
            self.assertTrue({"S21", "S22"} & set(codes), msg=str(codes))
            self.assertFalse({"S14", "S15"} & set(codes), msg=str(codes))

    def test_subtract_violations_still_report_the_subtract_rules(self) -> None:
        """The existing rules kept their meaning: nothing moved to S21/S22."""
        cases = (
            ({"id": "cut", "type": "subtract", "target": "a", "tools": []}, "S14"),
            ({"id": "cut", "type": "subtract", "target": "a", "tools": ["a", "b"]}, "S15"),
            ({"id": "cut", "type": "subtract", "target": "a", "tools": ["b", "b"]}, "S15"),
        )
        for feature, rule in cases:
            for version in ("1.0.0", "1.1.0"):
                with self.subTest(rule=rule, tools=feature["tools"], version=version):
                    doc = document(
                        [cube("a"), cube("b"), feature], schema_version=version
                    )
                    codes = validate(doc).rule_codes()
                    self.assertIn(rule, codes)
                    self.assertFalse({"S21", "S22"} & set(codes))

    def test_every_violation_is_reported_not_just_the_first(self) -> None:
        doc = document(
            [cube("a"), cube("b"), union_feature("a", ["a", "b", "b"])]
        )
        errors = [error for error in validate(doc).errors if error.rule == "S22"]
        self.assertEqual(
            [error.field_path for error in errors],
            ["features[2].tools[0]", "features[2].tools[2]"],
        )

    def test_validation_is_deterministic(self) -> None:
        doc = document(
            [cube("a"), union_feature("a", ["ghost", "a"]), cube("late")]
        )
        first = validate(doc)
        for _ in range(5):
            self.assertEqual(validate(doc).errors, first.errors)

    def test_the_static_validator_never_decides_connectivity(self) -> None:
        """Operands far apart are statically valid; only the engine knows (E3)."""
        doc = document([cube("a"), cube("b", (500, 500, 500)), union_feature("a", ["b"])])
        result = validate(doc)
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())


# --- static validation: the solid set (Section B.4) ------------------------


class TestSolidSetRules(UnionTestCase):
    def hole_into(self, target: str) -> Dict[str, Any]:
        return {
            "id": "bore",
            "type": "through_hole",
            "target": target,
            "diameter": 4,
            "position": {"x": 2.5, "y": 20, "z": 0},
        }

    def test_a_union_leaves_exactly_one_solid(self) -> None:
        """Two constructive features are valid once a union consumes one."""
        without = document(bracket_features()[:2])
        self.assertIn("S9", validate(without).rule_codes())
        self.assertTrue(validate(bracket_document()).valid)

    def test_a_tool_is_consumed_and_cannot_be_a_later_target(self) -> None:
        doc = bracket_document()
        doc["features"].append(self.hole_into("upright"))
        error = self.assertRuleAt(doc, "S6", "features[3].target")
        # The message says what happened and what to write instead.
        self.assertIn("consumed by union 'join_upright'", error.message)
        self.assertIn("'base'", error.message)

    def test_a_tool_is_consumed_and_cannot_be_reused_by_another_union(self) -> None:
        doc = bracket_document()
        doc["features"].append(union_feature("base", ["upright"], "again"))
        self.assertRuleAt(doc, "S6", "features[3].tools[0]")

    def test_a_tool_is_consumed_and_cannot_be_subtracted_later(self) -> None:
        doc = bracket_document()
        doc["features"].append(
            {"id": "cut", "type": "subtract", "target": "base", "tools": ["upright"]}
        )
        self.assertRuleAt(doc, "S6", "features[3].tools[0]")

    def test_a_solid_consumed_by_a_subtract_cannot_be_joined(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("b", (5, 0, 0)),
                {"id": "cut", "type": "subtract", "target": "a", "tools": ["b"]},
                union_feature("a", ["b"]),
            ]
        )
        error = self.assertRuleAt(doc, "S6", "features[3].tools[0]")
        self.assertIn("consumed by subtract 'cut'", error.message)

    def test_the_unions_own_id_never_names_a_solid(self) -> None:
        doc = bracket_document()
        doc["features"].append(self.hole_into("join_upright"))
        error = self.assertRuleAt(doc, "S6", "features[3].target")
        self.assertIn("modifier", error.message)
        # ... and it points at the id that does name the joined solid.
        self.assertIn("'base'", error.message)

    def test_the_joined_solid_is_addressed_by_the_targets_id(self) -> None:
        doc = bracket_document()
        doc["features"].append(self.hole_into("base"))
        self.assertTrue(validate(doc).valid)

    def test_a_tool_that_does_not_exist_is_s6(self) -> None:
        doc = document([cube("a"), union_feature("a", ["ghost"])])
        self.assertRuleAt(doc, "S6", "features[1].tools[0]")

    def test_a_target_that_does_not_exist_is_s6(self) -> None:
        doc = document([cube("a"), cube("b"), union_feature("ghost", ["b"])])
        self.assertRuleAt(doc, "S6", "features[2].target")

    def test_a_forward_reference_is_s7(self) -> None:
        doc = document(
            [cube("a"), union_feature("a", ["later"]), cube("later", (5, 0, 0))]
        )
        self.assertRuleAt(doc, "S7", "features[1].tools[0]")

    def test_a_union_referring_to_itself_is_s7(self) -> None:
        doc = document([cube("a"), union_feature("a", ["join"])])
        self.assertRuleAt(doc, "S7", "features[1].tools[0]")

    def test_a_union_cannot_be_the_first_feature(self) -> None:
        doc = document([union_feature("a", ["b"]), cube("a"), cube("b")])
        self.assertRuleAt(doc, "S9", "features[0].type")

    def test_a_solid_left_unjoined_is_s9(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("b", (5, 0, 0)),
                cube("spare", (0, 5, 0)),
                union_feature("a", ["b"]),
            ]
        )
        result = validate(doc)
        self.assertEqual(result.rule_codes(), ("S9",))
        self.assertIn("'spare'", result.errors[0].message)

    def test_several_tools_are_all_consumed(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("b", (5, 0, 0)),
                cube("c", (0, 5, 0)),
                union_feature("a", ["b", "c"]),
            ]
        )
        self.assertTrue(validate(doc).valid)
        for consumed in ("b", "c"):
            with self.subTest(consumed=consumed):
                later = json.loads(json.dumps(doc))
                later["features"].append(self.hole_into(consumed))
                self.assertRuleAt(later, "S6", "features[4].target")

    def test_a_malformed_tool_list_raises_no_derived_solid_set_error(self) -> None:
        """S22 is reported once; no second, misleading S6 or S9 follows."""
        doc = document([cube("a"), cube("b"), union_feature("a", ["b", "b"])])
        self.assertEqual(validate(doc).rule_codes(), ("S22",))


# --- the version gate (Section F) ------------------------------------------


class TestSchemaVersionGate(UnionTestCase):
    def six_type_features(self) -> List[Dict[str, Any]]:
        return [
            box_feature("plate", (100, 60, 10)),
            cylinder_feature("tool", 20, 20, (20, 20, -5)),
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            {
                "id": "bore",
                "type": "through_hole",
                "target": "plate",
                "diameter": 10,
                "position": {"x": 80, "y": 40, "z": 0},
            },
            {
                "id": "round",
                "type": "fillet",
                "target": "plate",
                "radius": 2,
                "edges": {"select": "axis_parallel", "axis": "X"},
            },
            {
                "id": "bevel",
                "type": "chamfer",
                "target": "plate",
                "distance": 1,
                "edges": {"select": "all"},
            },
        ]

    def test_a_1_0_0_document_using_union_is_rejected_under_s3(self) -> None:
        """Validated against the version it declares, as a 1.0.0 consumer would."""
        doc = bracket_document()
        doc["schema_version"] = "1.0.0"
        error = self.assertRuleAt(doc, "S3", "features[2].type")
        self.assertEqual(error.feature_id, "join_upright")
        # Actionable: it names the version that has the feature.
        self.assertIn("1.1.0", error.message)
        self.assertIn("'union'", error.message)

    def test_the_rejection_is_a_single_error_with_no_cascade(self) -> None:
        doc = bracket_document()
        doc["schema_version"] = "1.0.0"
        result = validate(doc)
        self.assertEqual(result.rule_codes(), ("S3",))
        self.assertEqual(len(result.errors), 1)
        self.assertIsNone(result.part)

    def test_union_is_accepted_from_1_1_0_onward(self) -> None:
        for version in ("1.1.0", "1.1.7", "1.2.0", "1.10.3"):
            with self.subTest(version=version):
                doc = bracket_document()
                doc["schema_version"] = version
                part = self.part_from(doc)
                self.assertEqual(part.schema_version, version)

    def test_union_is_rejected_for_every_1_0_patch_version(self) -> None:
        for version in ("1.0.0", "1.0.1", "1.0.12"):
            with self.subTest(version=version):
                doc = bracket_document()
                doc["schema_version"] = version
                self.assertEqual(validate(doc).rule_codes(), ("S3",))

    def test_a_six_type_document_is_valid_under_either_version(self) -> None:
        """Backward compatibility: 1.1.0 took nothing away from 1.0.0."""
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(version=version):
                doc = document(self.six_type_features(), schema_version=version)
                part = self.part_from(doc)
                self.assertEqual(part.schema_version, version)
                self.assertEqual(len(part.features), 6)

    def test_an_unreadable_version_reports_s4_only(self) -> None:
        """One bad version must not also condemn every newer feature."""
        for version in ("one", "1.1", 1.1, None):
            with self.subTest(version=version):
                doc = bracket_document()
                doc["schema_version"] = version
                self.assertEqual(validate(doc).rule_codes(), ("S4",))

    def test_an_unsupported_major_version_reports_s4_only(self) -> None:
        doc = bracket_document()
        doc["schema_version"] = "2.0.0"
        self.assertEqual(validate(doc).rule_codes(), ("S4",))

    def test_an_unknown_type_is_still_s3_and_lists_union(self) -> None:
        doc = document(
            [cube("a"), {"id": "x", "type": "intersect", "target": "a", "tools": []}]
        )
        error = self.assertRuleAt(doc, "S3", "features[1].type")
        self.assertIn("union", error.message)


# --- the engine: the primary union -----------------------------------------


class TestPrimaryUnion(UnionTestCase):
    """The Section D.2 L-bracket: two boxes sharing a face."""

    def test_build_succeeds(self) -> None:
        self.assertIsInstance(self.build(), LocalCadResult)

    def test_result_is_a_single_valid_solid(self) -> None:
        result = self.build()
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertTrue(result.is_solid())
        self.assertTrue(result.shape.isValid())
        self.assertEqual(result.solid_count(), 1)

    def test_the_joined_solid_keeps_the_targets_identity(self) -> None:
        result = self.build()
        self.assertEqual(result.feature_id, "base")
        self.assertNotEqual(result.feature_id, "upright")
        self.assertNotEqual(result.feature_id, "join_upright")

    def test_volume_is_the_sum_for_face_sharing_operands(self) -> None:
        """Reference value from geometry: no shared volume, so V1 + V2."""
        self.assertEqual(BRACKET_VOLUME, 21000.0)
        self.assertAlmostEqual(
            self.build().volume(), BRACKET_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_bounding_box_spans_both_operands(self) -> None:
        box = self.build().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, BRACKET_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, BRACKET_MAXIMUM)

    def test_the_shared_face_is_gone(self) -> None:
        """No face of the result lies in the interior where the boxes met."""
        for face in self.build().shape.Faces():
            centre = face.Center()
            inside_the_joint = (
                abs(centre.z - 5.0) < TOLERANCE_MM and centre.x < 5.0 - TOLERANCE_MM
            )
            self.assertFalse(inside_the_joint, msg=f"interior face at {centre}")

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        """An L-profile prism: 8 faces, 18 edges, 12 vertices. Not contract."""
        shape = self.build().shape
        self.assertEqual(len(shape.Faces()), 8)
        self.assertEqual(len(shape.Edges()), 18)
        self.assertEqual(len(shape.Vertices()), 12)
        self.assertEqual(len(shape.Shells()), 1)

    def test_the_specification_example_bracket_builds(self) -> None:
        """Section D.2's worked example, read out of the specification."""
        examples = specification_examples()
        self.assertEqual(len(examples), 2)
        plate, bracket = examples
        # D.1 is still the first example, still a 1.0.0 document.
        self.assertEqual(plate["schema_version"], "1.0.0")
        self.assertEqual(plate["name"], "plate-100x60x10-4holes")
        self.assertEqual(bracket["schema_version"], "1.1.0")
        self.assertEqual(
            [feature["type"] for feature in bracket["features"]],
            ["box", "box", "union"],
        )
        result = self.build_document(bracket)
        self.assertEqual(result.feature_id, "base")
        self.assertEqual(result.solid_count(), 1)
        self.assertAlmostEqual(
            result.volume(), 60 * 40 * 5 + 5 * 40 * 45, delta=VOLUME_TOLERANCE_MM3
        )
        # ... and its features are the ones this module builds everywhere
        # else (the specification's copy also carries a description).
        self.assertEqual(
            self.part_from(bracket).features,
            self.part_from(bracket_document()).features,
        )


# --- the engine: overlapping operands --------------------------------------


class TestOverlappingOperands(UnionTestCase):
    def test_overlap_is_counted_once(self) -> None:
        """Two 10 mm cubes offset by (5, 5, 5): V = V1 + V2 - V_overlap."""
        doc = document([cube("a"), cube("b", (5, 5, 5)), union_feature("a", ["b"])])
        overlap = 5.0 * 5.0 * 5.0
        expected = CUBE_VOLUME + CUBE_VOLUME - overlap
        self.assertEqual(expected, 1875.0)
        result = self.build_document(doc)
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)
        box = result.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, (0.0, 0.0, 0.0))
        self.assertTripleAlmostEqual(box.maximum, (15.0, 15.0, 15.0))

    def test_overlapping_and_face_sharing_descriptions_give_the_same_solid(self) -> None:
        """The upright from z = 0 (overlapping) or from z = 5 (standing on).

        V = 12000 + 10000 - 1000 either way: shared material is counted once.
        """
        overlapping = document(
            [
                box_feature("base", BASE_SIZE),
                box_feature("upright", (5.0, 40.0, 50.0)),
                union_feature("base", ["upright"]),
            ]
        )
        overlap = 5.0 * 40.0 * 5.0
        expected = BASE_VOLUME + 5.0 * 40.0 * 50.0 - overlap
        self.assertEqual(expected, BRACKET_VOLUME)
        first = self.build_document(overlapping)
        second = self.build()
        self.assertAlmostEqual(first.volume(), expected, delta=VOLUME_TOLERANCE_MM3)
        self.assertAlmostEqual(
            first.volume(), second.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        for counter in ("Faces", "Edges", "Vertices"):
            with self.subTest(counter=counter):
                self.assertEqual(
                    len(getattr(first.shape, counter)()),
                    len(getattr(second.shape, counter)()),
                )

    def test_two_boxes_end_to_end_become_one_plain_box(self) -> None:
        """Full-face contact: 10x10x10 + 10x10x10 = one 20x10x10 box.

        The joined body has a box's topology -- 6 faces, 12 edges -- because
        the operands' coplanar faces are merged. Measured: the raw kernel
        fuse leaves 10 faces here.
        """
        doc = document([cube("a"), cube("b", (10, 0, 0)), union_feature("a", ["b"])])
        result = self.build_document(doc)
        self.assertAlmostEqual(
            result.volume(), 2 * CUBE_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(result.shape.Faces()), 6)
        self.assertEqual(len(result.shape.Edges()), 12)
        self.assertEqual(len(result.shape.Vertices()), 8)

    def test_a_boss_standing_on_a_plate(self) -> None:
        """A cylinder sharing a circular face area with the plate's top."""
        doc = document(
            [
                box_feature("plate", (100, 60, 10)),
                cylinder_feature("boss", 20, 15, (50, 30, 10)),
                union_feature("plate", ["boss"]),
            ]
        )
        expected = 100 * 60 * 10 + math.pi * 10.0**2 * 15
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "plate")
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)
        box = result.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, (0.0, 0.0, 0.0))
        self.assertTripleAlmostEqual(box.maximum, (100.0, 60.0, 25.0))
        walls = [f for f in result.shape.Faces() if f.geomType() == "CYLINDER"]
        self.assertEqual(len(walls), 1)

    def test_a_boss_sunk_into_a_plate_gives_the_same_solid(self) -> None:
        """Base at z = 5, height 20: 5 mm of it is inside the plate already."""
        doc = document(
            [
                box_feature("plate", (100, 60, 10)),
                cylinder_feature("boss", 20, 20, (50, 30, 5)),
                union_feature("plate", ["boss"]),
            ]
        )
        cylinder = math.pi * 10.0**2 * 20
        overlap = math.pi * 10.0**2 * 5
        expected = 100 * 60 * 10 + cylinder - overlap
        result = self.build_document(doc)
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)
        self.assertTripleAlmostEqual(result.bounding_box().maximum, (100.0, 60.0, 25.0))

    def test_the_raw_kernel_fuse_keeps_seams_that_the_engine_removes(self) -> None:
        """Why the result is unified, shown against the kernel directly.

        Measured: fusing the two overlapping boxes of an L-bracket leaves 14
        faces; the engine returns 8, at the same volume.
        """
        import cadquery as cq

        base = cq.Solid.makeBox(60, 40, 5)
        upright = cq.Solid.makeBox(5, 40, 50)
        raw = base.fuse(upright)
        self.assertEqual(len(raw.Solids()), 1)
        self.assertEqual(len(raw.Faces()), 14)
        built = self.build()
        self.assertEqual(len(built.shape.Faces()), 8)
        self.assertAlmostEqual(
            raw.Volume(), built.volume(), delta=VOLUME_TOLERANCE_MM3
        )


# --- the engine: several tools ---------------------------------------------


class TestMultipleTools(UnionTestCase):
    """A U-channel: a base and two uprights, joined by one union."""

    def channel(self, tools: Sequence[str] = ("left", "right")) -> Dict[str, Any]:
        return document(
            [
                box_feature("base", BASE_SIZE),
                box_feature("left", UPRIGHT_SIZE, (0, 0, 5)),
                box_feature("right", UPRIGHT_SIZE, (55, 0, 5)),
                union_feature("base", list(tools)),
            ],
            name="u-channel",
        )

    EXPECTED = BASE_VOLUME + 2 * UPRIGHT_VOLUME

    def test_one_solid_remains_with_the_targets_identity(self) -> None:
        result = self.build_document(self.channel())
        self.assertEqual(result.solid_count(), 1)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.feature_id, "base")

    def test_both_tools_are_present_by_volume(self) -> None:
        self.assertEqual(self.EXPECTED, 30000.0)
        self.assertAlmostEqual(
            self.build_document(self.channel()).volume(),
            self.EXPECTED,
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_geometry_is_independent_of_tool_order(self) -> None:
        """Set union is commutative; the measurement agrees."""
        forward = self.build_document(self.channel(("left", "right")))
        reverse = self.build_document(self.channel(("right", "left")))
        self.assertAlmostEqual(
            forward.volume(), reverse.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(forward.shape.Faces()), len(reverse.shape.Faces()))
        self.assertEqual(len(forward.shape.Edges()), len(reverse.shape.Edges()))

    def test_one_union_and_two_unions_agree(self) -> None:
        chained = document(
            [
                box_feature("base", BASE_SIZE),
                box_feature("left", UPRIGHT_SIZE, (0, 0, 5)),
                union_feature("base", ["left"], "join_left"),
                box_feature("right", UPRIGHT_SIZE, (55, 0, 5)),
                union_feature("base", ["right"], "join_right"),
            ]
        )
        once = self.build_document(self.channel())
        twice = self.build_document(chained)
        self.assertAlmostEqual(once.volume(), twice.volume(), delta=VOLUME_TOLERANCE_MM3)
        self.assertEqual(len(once.shape.Faces()), len(twice.shape.Faces()))
        self.assertEqual(twice.feature_id, "base")

    def test_which_operand_is_the_target_decides_only_the_identity(self) -> None:
        swapped = document(
            [
                box_feature("base", BASE_SIZE),
                box_feature("upright", UPRIGHT_SIZE, UPRIGHT_POSITION),
                union_feature("upright", ["base"]),
            ]
        )
        result = self.build_document(swapped)
        self.assertEqual(result.feature_id, "upright")
        self.assertAlmostEqual(
            result.volume(), BRACKET_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_a_later_tool_may_bridge_an_earlier_one_to_the_target(self) -> None:
        """E3 is checked on what the feature leaves, not after each tool.

        ``far`` does not touch ``a``; ``bridge`` overlaps both. In either
        order the feature leaves one body: a 40x10x10 bar.
        """
        for tools in (("far", "bridge"), ("bridge", "far")):
            with self.subTest(tools=tools):
                doc = document(
                    [
                        cube("a"),
                        cube("far", (30, 0, 0)),
                        box_feature("bridge", (30, 10, 10), (5, 0, 0)),
                        union_feature("a", list(tools)),
                    ]
                )
                result = self.build_document(doc)
                self.assertTrue(result.is_solid())
                self.assertAlmostEqual(
                    result.volume(), 40 * 10 * 10, delta=VOLUME_TOLERANCE_MM3
                )
                self.assertEqual(len(result.shape.Faces()), 6)


# --- rule E3: operands that do not form one body ---------------------------


class TestRuleE3(UnionTestCase):
    def joined_with(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        return document([cube("a"), tool, union_feature("a", [tool["id"]])])

    def assertIsE3(self, message: str, target: str, tool: str) -> None:
        self.assertIn("rule E3", message)
        self.assertIn("union 'join'", message)
        self.assertIn(f"target {target!r}", message)
        self.assertIn(f"tool {tool!r}", message)
        self.assertIn("not connected", message)
        # Actionable: it says what would have joined them.
        self.assertIn("overlap or share a face", message)
        for other in ("rule E1", "rule E2", "rule E4", "rule E5"):
            self.assertNotIn(other, message)

    def test_operands_that_are_apart_are_rejected(self) -> None:
        message = self.geometry_error(self.joined_with(cube("b", (30, 0, 0))))
        self.assertIsE3(message, "a", "b")
        self.assertIn("tools[0]", message)
        self.assertIn("2 separate bodies", message)

    def test_edge_only_contact_is_rejected(self) -> None:
        """Two cubes sharing one edge and nothing else."""
        message = self.geometry_error(self.joined_with(cube("b", (10, 10, 0))))
        self.assertIsE3(message, "a", "b")
        self.assertIn("along an edge", message)

    def test_partial_edge_contact_is_rejected(self) -> None:
        message = self.geometry_error(self.joined_with(cube("b", (10, 10, 5))))
        self.assertIsE3(message, "a", "b")

    def test_point_only_contact_is_rejected(self) -> None:
        """Two cubes sharing one corner and nothing else."""
        message = self.geometry_error(self.joined_with(cube("b", (10, 10, 10))))
        self.assertIsE3(message, "a", "b")
        self.assertIn("at a point", message)

    def test_tangent_line_contact_is_rejected(self) -> None:
        """A cylinder just touching a cube's face along one line."""
        tool = cylinder_feature("post", 10, 10, (15, 5, 0))
        message = self.geometry_error(self.joined_with(tool))
        self.assertIsE3(message, "a", "post")

    def test_a_small_gap_is_rejected(self) -> None:
        message = self.geometry_error(self.joined_with(cube("b", (10.001, 0, 0))))
        self.assertIsE3(message, "a", "b")

    def test_a_micron_scale_gap_is_a_measured_kernel_observation(self) -> None:
        """Measured, not contract: a 1e-6 mm gap still keeps two bodies.

        The kernel's coincidence tolerance is about 1e-7 mm; at or below it a
        gap is treated as contact. See docs/local-cad-engine.md.
        """
        message = self.geometry_error(self.joined_with(cube("b", (10.000001, 0, 0))))
        self.assertIn("rule E3", message)

    def test_the_kernel_returns_a_valid_compound_not_a_failure(self) -> None:
        """Why the solid count is the test and ``isValid`` is not.

        For edge-only and point-only contact the kernel neither fails nor
        builds a non-manifold solid: it hands back a compound that passes its
        own validity analysis and holds two solids, at the summed volume.
        Nothing but counting the solids would notice.
        """
        import cadquery as cq

        first = cq.Solid.makeBox(10, 10, 10)
        for name, corner in (
            ("apart", (30, 0, 0)),
            ("edge", (10, 10, 0)),
            ("point", (10, 10, 10)),
        ):
            with self.subTest(contact=name):
                second = cq.Solid.makeBox(10, 10, 10, pnt=cq.Vector(*corner))
                fused = first.fuse(second)
                self.assertEqual(fused.ShapeType(), "Compound")
                self.assertTrue(fused.isValid())
                self.assertEqual(len(fused.Solids()), 2)
                self.assertTrue(all(solid.isValid() for solid in fused.Solids()))
                self.assertAlmostEqual(
                    fused.Volume(), 2 * CUBE_VOLUME, delta=VOLUME_TOLERANCE_MM3
                )

    def test_only_the_detached_tool_is_named(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("near", (5, 0, 0)),
                cube("far", (50, 0, 0)),
                union_feature("a", ["near", "far"]),
            ]
        )
        message = self.geometry_error(doc)
        self.assertIn("rule E3", message)
        self.assertIn("tool 'far' (tools[1])", message)
        self.assertNotIn("'near'", message)
        self.assertIn("2 separate bodies", message)

    def test_every_detached_tool_is_named(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("left", (30, 0, 0)),
                cube("right", (60, 0, 0)),
                union_feature("a", ["left", "right"]),
            ]
        )
        message = self.geometry_error(doc)
        self.assertIn("rule E3", message)
        self.assertIn("'left' (tools[0])", message)
        self.assertIn("'right' (tools[1])", message)
        self.assertIn("3 separate bodies", message)

    def test_tools_joined_to_each_other_but_not_to_the_target_are_rejected(self) -> None:
        doc = document(
            [
                cube("a"),
                cube("left", (30, 0, 0)),
                cube("right", (35, 0, 0)),
                union_feature("a", ["left", "right"]),
            ]
        )
        message = self.geometry_error(doc)
        self.assertIn("rule E3", message)
        self.assertIn("'left' (tools[0])", message)
        self.assertIn("'right' (tools[1])", message)
        self.assertIn("2 separate bodies", message)

    def test_no_partial_geometry_escapes(self) -> None:
        """A refused union yields an exception, never the target on its own."""
        part = self.part_from(self.joined_with(cube("b", (30, 0, 0))))
        with self.assertRaises(GeometryOperationError):
            build_part(part)


# --- an operand that adds no material --------------------------------------


class TestOperandThatAddsNothing(UnionTestCase):
    """The specification gives no rule for this; the engine refuses.

    A tool lying wholly inside the body leaves one connected solid, so E3 is
    satisfied -- and the geometry is simply unchanged. That is the same
    situation as a ``subtract`` tool that removes nothing, and it is handled
    the same way: refused, with no rule code claimed.
    """

    def contained(self) -> Dict[str, Any]:
        return document(
            [
                cube("a"),
                box_feature("rib", (4, 4, 4), (3, 3, 3)),
                union_feature("a", ["rib"]),
            ]
        )

    def assertClaimsNoRule(self, message: str) -> None:
        self.assertIn("does not classify", message)
        # Neither as "(rule E3)" nor as a bare code the build layer would
        # read back out of the message as a rule.
        self.assertIsNone(re.search(r"\b[SE][0-9]{1,2}\b", message), msg=message)

    def test_a_tool_inside_the_target_is_refused(self) -> None:
        message = self.geometry_error(self.contained())
        self.assertIn("union 'join'", message)
        self.assertIn("tool 'rib' (tools[0])", message)
        self.assertIn("target 'a'", message)
        self.assertIn("add no material", message)
        self.assertClaimsNoRule(message)

    def test_a_target_inside_its_tool_is_refused(self) -> None:
        doc = document(
            [
                box_feature("rib", (4, 4, 4), (3, 3, 3)),
                cube("a"),
                union_feature("rib", ["a"]),
            ]
        )
        message = self.geometry_error(doc)
        self.assertIn("target 'rib'", message)
        self.assertIn("lies entirely inside tool 'a'", message)
        self.assertClaimsNoRule(message)

    def test_a_tool_flush_inside_a_face_is_refused_too(self) -> None:
        """Inside the target and touching its face from within: still nothing added."""
        doc = document(
            [
                cube("a"),
                box_feature("rib", (4, 4, 4), (0, 3, 3)),
                union_feature("a", ["rib"]),
            ]
        )
        self.assertIn("add no material", self.geometry_error(doc))

    def test_an_identical_tool_is_refused(self) -> None:
        doc = document([cube("a"), cube("twin"), union_feature("a", ["twin"])])
        self.assertIn("add no material", self.geometry_error(doc))

    def test_a_tool_swallowed_by_an_earlier_tool_is_refused(self) -> None:
        """Order is observable here, as it is for a shadowed subtract tool."""
        doc = document(
            [
                cube("a"),
                cube("wide", (5, 0, 0)),
                box_feature("rib", (4, 4, 4), (10, 3, 3)),
                union_feature("a", ["wide", "rib"]),
            ]
        )
        message = self.geometry_error(doc)
        self.assertIn("tool 'rib' (tools[1])", message)
        self.assertIn("earlier tools", message)
        self.assertClaimsNoRule(message)

    def test_the_kernel_would_have_accepted_it_silently(self) -> None:
        """Why refusing matters: the missing rib would be invisible."""
        import cadquery as cq

        body = cq.Solid.makeBox(10, 10, 10)
        rib = cq.Solid.makeBox(4, 4, 4, pnt=cq.Vector(3, 3, 3))
        fused = body.fuse(rib)
        self.assertEqual(len(fused.Solids()), 1)
        self.assertTrue(fused.isValid())
        self.assertEqual(fused.Volume(), body.Volume())
        self.assertEqual(len(fused.Faces()), len(body.Faces()))

    def test_a_tool_that_adds_even_a_little_is_accepted(self) -> None:
        doc = document(
            [
                cube("a"),
                box_feature("rib", (4, 4, 4), (8, 3, 3)),
                union_feature("a", ["rib"]),
            ]
        )
        expected = CUBE_VOLUME + 2.0 * 4.0 * 4.0
        self.assertAlmostEqual(
            self.build_document(doc).volume(), expected, delta=VOLUME_TOLERANCE_MM3
        )


# --- defences against a hand-built part ------------------------------------


class TestEvaluatorDefences(UnionTestCase):
    """The validator guarantees S6/S21/S22; the evaluator still checks.

    None of these parts can come from ``validate()``. They are built by hand
    to prove the evaluator reports them instead of crashing or producing
    nonsense geometry.
    """

    def hand_built(self, union: UnionFeature, extra: tuple = ()) -> Part:
        return Part(
            schema_version="1.1.0",
            units="mm",
            name="hand-built",
            features=(Box(id="base", size=Size(*BASE_SIZE)),) + extra + (union,),
        )

    def upright(self, identifier: str = "upright") -> Box:
        return Box(
            id=identifier,
            size=Size(*UPRIGHT_SIZE),
            position=Position(*UPRIGHT_POSITION),
        )

    def refusal(self, part: Part) -> str:
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        return str(caught.exception)

    def test_an_unresolvable_target_is_reported(self) -> None:
        message = self.refusal(
            self.hand_built(
                UnionFeature(id="join", target="absent", tools=("upright",)),
                extra=(self.upright(),),
            )
        )
        self.assertIn("S6", message)
        self.assertIn("'absent'", message)

    def test_an_unresolvable_tool_is_reported(self) -> None:
        message = self.refusal(
            self.hand_built(UnionFeature(id="join", target="base", tools=("ghost",)))
        )
        self.assertIn("S6", message)
        self.assertIn("'ghost'", message)

    def test_an_empty_tool_list_is_reported(self) -> None:
        message = self.refusal(
            self.hand_built(UnionFeature(id="join", target="base", tools=()))
        )
        self.assertIn("S21", message)

    def test_the_target_listed_as_its_own_tool_is_reported(self) -> None:
        message = self.refusal(
            self.hand_built(UnionFeature(id="join", target="base", tools=("base",)))
        )
        self.assertIn("S22", message)

    def test_a_duplicated_tool_is_reported(self) -> None:
        message = self.refusal(
            self.hand_built(
                UnionFeature(id="join", target="base", tools=("upright", "upright")),
                extra=(self.upright(),),
            )
        )
        self.assertIn("S22", message)
        self.assertIn("twice", message)

    def test_a_failed_union_consumes_nothing(self) -> None:
        """A refusal must not leave the solid set half-modified.

        A two-tool union whose second tool is unresolvable: nothing is
        written back, so the failure is reported against the union rather
        than surfacing later as a rule S9 error about a mutated solid set.
        """
        message = self.refusal(
            self.hand_built(
                UnionFeature(id="join", target="base", tools=("upright", "ghost")),
                extra=(self.upright(),),
            )
        )
        self.assertIn("'ghost'", message)
        self.assertIn("S6", message)
        self.assertNotIn("S9", message)

    def test_an_unjoined_solid_is_refused_by_the_engine_too(self) -> None:
        """Defence in depth for S9: two solids and no union."""
        part = Part(
            schema_version="1.1.0",
            units="mm",
            name="two-bodies",
            features=(Box(id="base", size=Size(*BASE_SIZE)), self.upright()),
        )
        message = self.refusal(part)
        self.assertIn("S9", message)
        self.assertIn("'base'", message)
        self.assertIn("'upright'", message)

    def test_a_raw_dictionary_cannot_invoke_the_build_api(self) -> None:
        with self.assertRaises(TypeError):
            build_part(bracket_document())  # type: ignore[arg-type]


# --- features applied to a joined body -------------------------------------


class TestFeaturesAfterUnion(UnionTestCase):
    def bracket_then(self, *features: Dict[str, Any]) -> Dict[str, Any]:
        return document(bracket_features() + list(features), name="bracket-plus")

    def test_a_through_hole_in_the_joined_body(self) -> None:
        """A 6 mm hole down through the 5 mm base: removes pi * r^2 * 5."""
        doc = self.bracket_then(
            {
                "id": "bore",
                "type": "through_hole",
                "target": "base",
                "diameter": 6,
                "position": {"x": 40, "y": 20, "z": 0},
            }
        )
        expected = BRACKET_VOLUME - math.pi * 3.0**2 * 5.0
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "base")
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_a_through_hole_in_material_that_came_from_the_tool(self) -> None:
        """A hole along X through the upright, at z = 30: it is part of 'base' now."""
        doc = self.bracket_then(
            {
                "id": "bore",
                "type": "through_hole",
                "target": "base",
                "diameter": 6,
                "position": {"x": 0, "y": 20, "z": 30},
                "axis": "+X",
            }
        )
        expected = BRACKET_VOLUME - math.pi * 3.0**2 * 5.0
        self.assertAlmostEqual(
            self.build_document(doc).volume(), expected, delta=VOLUME_TOLERANCE_MM3
        )

    def test_a_subtract_from_the_joined_body(self) -> None:
        """A 10x10 notch through the base: removes 10 * 10 * 5."""
        doc = self.bracket_then(
            box_feature("notch", (10, 10, 20), (50, 15, -5)),
            {"id": "cut", "type": "subtract", "target": "base", "tools": ["notch"]},
        )
        expected = BRACKET_VOLUME - 10 * 10 * 5
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "base")
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_a_fillet_on_the_joined_body(self) -> None:
        """Every Y-parallel edge of the L profile rounded at r = 2.

        The profile has six corners: five convex, each losing
        ``r^2 (1 - pi/4)``, and the one concave inside corner, which gains
        the same amount. Net change of area: ``-4 r^2 (1 - pi/4)``.

        This is also the test that the union left no seam behind: the raw
        kernel fuse keeps a Y-parallel seam edge between coplanar faces,
        which this selector would match and the kernel cannot blend.
        """
        radius = 2.0
        corner = radius**2 * (1.0 - math.pi / 4.0)
        expected = BRACKET_DEPTH * (BRACKET_PROFILE_AREA - 4.0 * corner)
        doc = self.bracket_then(
            {
                "id": "round",
                "type": "fillet",
                "target": "base",
                "radius": radius,
                "edges": {"select": "axis_parallel", "axis": "Y"},
            }
        )
        result = self.build_document(doc)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.feature_id, "base")
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_a_chamfer_on_the_joined_body(self) -> None:
        """Same six edges bevelled at d = 1: net change of area -4 * d^2 / 2."""
        distance = 1.0
        expected = BRACKET_DEPTH * (BRACKET_PROFILE_AREA - 4.0 * distance**2 / 2.0)
        doc = self.bracket_then(
            {
                "id": "bevel",
                "type": "chamfer",
                "target": "base",
                "distance": distance,
                "edges": {"select": "axis_parallel", "axis": "Y"},
            }
        )
        result = self.build_document(doc)
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_select_all_sees_the_joined_bodys_edges_only(self) -> None:
        """'all' on the bracket selects its 18 real edges and blends them."""
        doc = self.bracket_then(
            {
                "id": "round",
                "type": "fillet",
                "target": "base",
                "radius": 1,
                "edges": {"select": "all"},
            }
        )
        result = self.build_document(doc)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertLess(result.volume(), BRACKET_VOLUME)
        self.assertGreater(result.volume(), BRACKET_VOLUME - 500.0)

    def test_a_union_after_other_modifiers(self) -> None:
        """A tool that was drilled before it was joined keeps its hole."""
        doc = document(
            [
                box_feature("base", BASE_SIZE),
                box_feature("upright", UPRIGHT_SIZE, UPRIGHT_POSITION),
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "upright",
                    "diameter": 6,
                    "position": {"x": 0, "y": 20, "z": 30},
                    "axis": "+X",
                },
                union_feature("base", ["upright"]),
            ]
        )
        expected = BRACKET_VOLUME - math.pi * 3.0**2 * 5.0
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "base")
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)
        walls = [f for f in result.shape.Faces() if f.geomType() == "CYLINDER"]
        self.assertEqual(len(walls), 1)

    def test_every_feature_type_builds_in_one_document(self) -> None:
        """All seven types of schema 1.1.0, ending in a single solid."""
        doc = document(
            [
                box_feature("plate", (100, 60, 10)),
                cylinder_feature("boss", 20, 15, (50, 30, 10)),
                union_feature("plate", ["boss"]),
                cylinder_feature("tool", 10, 40, (50, 30, -5)),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 6,
                    "position": {"x": 15, "y": 15, "z": 0},
                },
                {
                    "id": "bevel",
                    "type": "chamfer",
                    "target": "plate",
                    "distance": 1,
                    "edges": {"select": "axis_parallel", "axis": "X"},
                },
                {
                    "id": "round",
                    "type": "fillet",
                    "target": "plate",
                    "radius": 1,
                    "edges": {"select": "axis_parallel", "axis": "Y"},
                },
            ],
            name="all-seven",
        )
        part = self.part_from(doc)
        self.assertEqual(
            {feature.TYPE for feature in part.features}, set(FEATURE_TYPES)
        )
        result = build_part(part)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")
        # Bounded by closed forms on either side: the bossed plate less the
        # 10 mm bore through plate and boss and the 6 mm hole, before the two
        # edge treatments, which only remove material here.
        before_edges = (
            100 * 60 * 10
            + math.pi * 10.0**2 * 15
            - math.pi * 5.0**2 * 25
            - math.pi * 3.0**2 * 10
        )
        self.assertLess(result.volume(), before_edges)
        self.assertGreater(result.volume(), before_edges - 400.0)


# --- serialization and hashing ---------------------------------------------


class TestSerialization(UnionTestCase):
    def test_canonical_text_of_the_bracket(self) -> None:
        self.assertEqual(
            part_to_json(self.part_from(bracket_document())),
            '{"schema_version":"1.1.0","units":"mm","name":"l-bracket-60x40x50",'
            '"features":['
            '{"id":"base","type":"box","size":{"x":60.0,"y":40.0,"z":5.0},'
            '"position":{"x":0.0,"y":0.0,"z":0.0}},'
            '{"id":"upright","type":"box","size":{"x":5.0,"y":40.0,"z":45.0},'
            '"position":{"x":0.0,"y":0.0,"z":5.0}},'
            '{"id":"join_upright","type":"union","target":"base","tools":["upright"]}'
            "]}",
        )

    def test_a_union_emits_the_specification_field_order(self) -> None:
        feature = serialize_part(self.part_from(bracket_document()))["features"][2]
        self.assertEqual(list(feature.keys()), ["id", "type", "target", "tools"])
        self.assertIsInstance(feature["tools"], list)

    def test_key_order_in_the_source_does_not_matter(self) -> None:
        shuffled = bracket_document()
        shuffled["features"][2] = {
            "tools": ["upright"],
            "target": "base",
            "type": "union",
            "id": "join_upright",
        }
        self.assertEqual(
            part_hash(self.part_from(shuffled)),
            part_hash(self.part_from(bracket_document())),
        )

    def test_round_trip(self) -> None:
        part = self.part_from(bracket_document())
        text = part_to_json(part)
        again = part_from_json(text)
        self.assertEqual(again, part)
        self.assertTrue(parts_equivalent(part, again))
        self.assertEqual(part_to_json(again), text)
        self.assertIsInstance(again.features[2], UnionFeature)

    def test_the_hash_is_stable_and_pinned(self) -> None:
        part = self.part_from(bracket_document())
        digests = {part_hash(self.part_from(bracket_document())) for _ in range(5)}
        self.assertEqual(digests, {BRACKET_DOCUMENT_HASH})
        self.assertEqual(
            hashlib.sha256(part_to_json(part).encode("utf-8")).hexdigest(),
            BRACKET_DOCUMENT_HASH,
        )

    def test_tools_order_is_preserved_never_sorted(self) -> None:
        def channel(tools: Sequence[str]) -> Part:
            return self.part_from(
                document(
                    [
                        box_feature("base", BASE_SIZE),
                        box_feature("left", UPRIGHT_SIZE, (0, 0, 5)),
                        box_feature("right", UPRIGHT_SIZE, (55, 0, 5)),
                        union_feature("base", list(tools)),
                    ]
                )
            )

        forward, reverse = channel(("right", "left")), channel(("left", "right"))
        self.assertEqual(
            serialize_part(forward)["features"][3]["tools"], ["right", "left"]
        )
        self.assertFalse(parts_equivalent(forward, reverse))
        self.assertNotEqual(part_hash(forward), part_hash(reverse))

    def test_a_union_and_a_subtract_of_the_same_solids_differ(self) -> None:
        def with_modifier(kind: str) -> Part:
            return self.part_from(
                document(
                    [
                        cube("a"),
                        cube("b", (5, 0, 0)),
                        {"id": "op", "type": kind, "target": "a", "tools": ["b"]},
                    ]
                )
            )

        self.assertNotEqual(
            part_hash(with_modifier("union")), part_hash(with_modifier("subtract"))
        )

    def test_a_1_0_0_document_keeps_its_bytes_and_its_hash(self) -> None:
        """1.1.0 changed nothing for a document that declares 1.0.0.

        The Section D.1 plate, read out of the specification, still hashes to
        the value pinned since Stage 15, and still serializes with the
        version it declared -- nothing upgrades it on the way through.
        """
        plate = specification_examples()[0]
        part = self.part_from(plate)
        self.assertEqual(part.schema_version, "1.0.0")
        self.assertEqual(part_hash(part), SECTION_D_PLATE_HASH)
        self.assertTrue(
            part_to_json(part).startswith('{"schema_version":"1.0.0",')
        )

    def test_the_declared_version_is_carried_never_rewritten(self) -> None:
        features = [cube("a")]
        old = self.part_from(document(features, schema_version="1.0.0"))
        new = self.part_from(document(features, schema_version="1.1.0"))
        self.assertEqual(serialize_part(old)["schema_version"], "1.0.0")
        self.assertEqual(serialize_part(new)["schema_version"], "1.1.0")
        # The version is part of the document, so it is part of its identity.
        self.assertNotEqual(part_hash(old), part_hash(new))
        self.assertEqual(part_from_json(part_to_json(old)).schema_version, "1.0.0")


# --- derived artifacts -----------------------------------------------------


class TestDerivedArtifacts(UnionTestCase):
    def test_step_round_trip(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "bracket.step"))
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), BRACKET_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, BRACKET_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, BRACKET_MAXIMUM)
        self.assertEqual(len(imported.shape.Faces()), 8)

    def test_iges_round_trip(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "bracket.igs"))
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), BRACKET_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(imported.face_count(), 8)

    def test_stl_is_structurally_valid(self) -> None:
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "bracket.stl"))
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        # Measured: 8 planar faces, two of them L-shaped hexagons (4
        # triangles each) and six rectangles (2 each) -- 20 triangles.
        self.assertEqual(facts.declared_triangles, 20)

    def test_render_model(self) -> None:
        model = build_render_model(self.build())
        self.assertEqual(model.feature_id, "base")
        self.assertGreater(model.triangle_count(), 0)
        self.assertTripleAlmostEqual(model.bounds.minimum, BRACKET_MINIMUM)
        self.assertTripleAlmostEqual(model.bounds.maximum, BRACKET_MAXIMUM)
        payloads = {
            json.dumps(build_render_model(self.build()).to_dict(), sort_keys=True)
            for _ in range(3)
        }
        self.assertEqual(len(payloads), 1)


# --- determinism -----------------------------------------------------------


class TestDeterminism(UnionTestCase):
    def test_repeated_builds_measure_identically(self) -> None:
        measurements = set()
        for _ in range(5):
            result = self.build()
            box = result.bounding_box()
            measurements.add(
                (
                    round(result.volume(), 9),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    result.solid_count(),
                    len(result.shape.Faces()),
                    len(result.shape.Edges()),
                    len(result.shape.Vertices()),
                )
            )
        self.assertEqual(len(measurements), 1)

    def test_each_build_returns_a_fresh_object(self) -> None:
        self.assertIsNot(self.build(), self.build())
        self.assertIsNot(self.build().shape, self.build().shape)

    def test_a_refusal_is_deterministic(self) -> None:
        doc = document([cube("a"), cube("b", (30, 0, 0)), union_feature("a", ["b"])])
        messages = {self.geometry_error(doc) for _ in range(3)}
        self.assertEqual(len(messages), 1)


# --- through the application service, in an isolated child -----------------


class TestThroughTheApplicationService(UnionTestCase):
    """The same path the HTTP transport uses: validate, build key, child."""

    def service(self) -> CadApplicationService:
        cache_root = self.tmp / "cache"
        cache_root.mkdir(exist_ok=True)
        return CadApplicationService.local(cache_root)

    def test_the_bracket_builds_end_to_end(self) -> None:
        service = self.service()
        outcome = service.build_document(
            BuildDocumentRequest(document=json.dumps(bracket_document()))
        )
        self.assertIs(
            outcome.status,
            BuildStatus.SUCCEEDED,
            msg=outcome.error.message if outcome.error else "",
        )
        self.assertEqual(
            outcome.produced_outputs(), ("geometry", "step", "iges", "stl", "render")
        )
        details = outcome.artifact("geometry").details
        self.assertTrue(details["is_solid"])
        self.assertEqual(details["solid_count"], 1)
        self.assertAlmostEqual(
            details["volume_mm3"], BRACKET_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        # The document the service reports is the one that was sent.
        validation = service.validate_document(json.dumps(bracket_document()))
        self.assertTrue(validation.valid)
        self.assertEqual(validation.document_hash, BRACKET_DOCUMENT_HASH)

    def test_a_disconnected_union_is_a_structured_e3_failure(self) -> None:
        doc = document([cube("a"), cube("b", (30, 0, 0)), union_feature("a", ["b"])])
        outcome = self.service().build_document(
            BuildDocumentRequest.for_outputs(doc, "geometry")
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.GEOMETRY_FAILED)
        self.assertEqual(outcome.error.rule_codes, ("E3",))
        self.assertIn("'b'", outcome.error.message)
        self.assertIn("overlap or share a face", outcome.error.message)
        self.assertIsNone(outcome.manifest)

    def test_an_operand_adding_nothing_reports_no_rule_code(self) -> None:
        doc = document(
            [
                cube("a"),
                box_feature("rib", (4, 4, 4), (3, 3, 3)),
                union_feature("a", ["rib"]),
            ]
        )
        outcome = self.service().build_document(
            BuildDocumentRequest.for_outputs(doc, "geometry")
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.GEOMETRY_FAILED)
        self.assertEqual(outcome.error.rule_codes, ())

    def test_a_static_violation_never_reaches_the_engine(self) -> None:
        doc = document([cube("a"), union_feature("a", [])])
        outcome = self.service().build_document(
            BuildDocumentRequest.for_outputs(doc, "geometry")
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.INVALID_DOCUMENT)
        self.assertEqual(outcome.error.rule_codes, ("S21",))


# --- the other backend -----------------------------------------------------


class TestFeatureScriptStillRefuses(UnionTestCase):
    """FeatureScript generation supports a single box, and nothing else."""

    def test_a_part_with_a_union_is_rejected(self) -> None:
        part = self.part_from(bracket_document())
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(part)
        self.assertIn("exactly one feature", str(caught.exception))

    def test_a_lone_union_is_rejected_as_not_a_box(self) -> None:
        """Hand-built, since a validated part never starts with a modifier."""
        part = Part(
            schema_version="1.1.0",
            units="mm",
            name="lone",
            features=(UnionFeature(id="join", target="a", tools=("b",)),),
        )
        with self.assertRaises(UnsupportedPartError):
            generate_featurescript(part)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
