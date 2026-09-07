"""Unit tests for the static CAD specification validator (rules S1-S20).

Terminology and rule codes follow ``docs/cad-specification.md`` exactly.
"""

from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path
from typing import Any, Dict, List

from cad_core import validate
from cad_core.errors import ValidationError, ValidationResult
from cad_core.geometry import GEOMETRIC_RULE_CODES, check_geometric_rules
from cad_core.model import (
    Box,
    Chamfer,
    EdgeSelector,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
    ThroughHole,
)
from cad_core.rules import STATIC_RULE_CODES

REPO_ROOT = Path(__file__).resolve().parents[3]
SPECIFICATION_PATH = REPO_ROOT / "docs" / "cad-specification.md"


# --- helpers ----------------------------------------------------------------


def box_feature(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
    }
    feature.update(overrides)
    return feature


def document(*features: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """A minimal valid document, optionally with root fields overridden."""
    doc: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "test-part",
        "features": list(features) if features else [box_feature()],
    }
    doc.update(overrides)
    return doc


def cylinder_feature(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "pin",
        "type": "cylinder",
        "diameter": 8,
        "height": 20,
    }
    feature.update(overrides)
    return feature


class ValidatorTestCase(unittest.TestCase):
    """Assertions phrased in terms of specification rule codes."""

    def assertValid(self, doc: Any) -> ValidationResult:
        result = validate(doc)
        self.assertTrue(
            result.valid,
            msg=f"expected a statically valid document, got: {[str(e) for e in result.errors]}",
        )
        self.assertEqual(result.errors, ())
        self.assertIsInstance(result.part, Part)
        return result

    def assertRules(self, doc: Any, *expected: str) -> ValidationResult:
        """Assert the exact multiset of rule codes reported, order-insensitive."""
        result = validate(doc)
        self.assertFalse(result.valid, msg="expected the document to be rejected")
        self.assertIsNone(result.part, msg="an invalid document must not yield a Part")
        self.assertEqual(
            sorted(result.rule_codes()),
            sorted(expected),
            msg=f"reported: {[str(e) for e in result.errors]}",
        )
        return result

    def assertRuleAt(self, doc: Any, rule: str, path: str) -> ValidationError:
        """Assert some error has exactly this rule code and field path."""
        result = validate(doc)
        self.assertFalse(result.valid)
        matches = [e for e in result.errors if e.rule == rule and e.field_path == path]
        self.assertEqual(
            len(matches),
            1,
            msg=f"expected one {rule} at {path!r}; got {[str(e) for e in result.errors]}",
        )
        return matches[0]


# --- valid documents --------------------------------------------------------


class TestValidDocuments(ValidatorTestCase):
    def test_valid_box(self) -> None:
        result = self.assertValid(document())
        part = result.part
        assert part is not None
        self.assertEqual(part.schema_version, "1.0.0")
        self.assertEqual(part.units, "mm")
        self.assertEqual(part.name, "test-part")
        self.assertIsNone(part.description)
        self.assertEqual(len(part.features), 1)
        self.assertEqual(
            part.features[0],
            Box(id="plate", size=Size(100.0, 60.0, 10.0), position=Position(0.0, 0.0, 0.0)),
        )

    def test_valid_box_with_explicit_position_and_description(self) -> None:
        doc = document(
            box_feature(position={"x": -5, "y": 0, "z": 2.5}),
            description="a plate placed off the origin",
        )
        part = self.assertValid(doc).part
        assert part is not None
        self.assertEqual(part.description, "a plate placed off the origin")
        self.assertEqual(part.features[0].position, Position(-5.0, 0.0, 2.5))

    def test_optional_defaults_are_materialised(self) -> None:
        """An omitted position defaults to the origin and axis to "+Z"."""
        part = self.assertValid(document(cylinder_feature())).part
        assert part is not None
        cylinder = part.features[0]
        self.assertEqual(cylinder.position, Position(0.0, 0.0, 0.0))
        self.assertEqual(cylinder.axis, "+Z")

    def test_valid_document_using_all_six_feature_types(self) -> None:
        doc = document(
            box_feature(),
            cylinder_feature(id="boss", diameter=20, height=5, position={"x": 50, "y": 30, "z": 10}),
            {
                "id": "bore",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
                "axis": "+Z",
            },
            {"id": "cut_boss", "type": "subtract", "target": "plate", "tools": ["boss"]},
            {
                "id": "round_corners",
                "type": "fillet",
                "target": "plate",
                "radius": 4,
                "edges": {"select": "axis_parallel", "axis": "Z"},
            },
            {
                "id": "break_edges",
                "type": "chamfer",
                "target": "plate",
                "distance": 1,
                "edges": {"select": "all"},
            },
        )
        part = self.assertValid(doc).part
        assert part is not None
        self.assertEqual(len(part.features), 6)
        self.assertIsInstance(part.features[2], ThroughHole)
        self.assertEqual(part.features[3], Subtract(id="cut_boss", target="plate", tools=("boss",)))
        self.assertEqual(
            part.features[4],
            Fillet(
                id="round_corners",
                target="plate",
                radius=4.0,
                edges=EdgeSelector(select="axis_parallel", axis="Z"),
            ),
        )
        self.assertEqual(
            part.features[5],
            Chamfer(id="break_edges", target="plate", distance=1.0, edges=EdgeSelector(select="all")),
        )

    def test_modifier_keeps_targeting_the_same_solid_id(self) -> None:
        """Section B.4: a modifier replaces its target in place, keeping its id."""
        doc = document(
            box_feature(),
            {"id": "h1", "type": "through_hole", "target": "plate", "diameter": 8, "position": {"x": 10, "y": 10, "z": 0}},
            {"id": "h2", "type": "through_hole", "target": "plate", "diameter": 8, "position": {"x": 90, "y": 10, "z": 0}},
        )
        self.assertValid(doc)

    def test_negative_and_zero_positions_are_accepted(self) -> None:
        """Rule S20: position components may be negative or zero."""
        doc = document(
            box_feature(position={"x": -100, "y": 0, "z": -0.5}),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": -50, "y": 0, "z": 0},
                "axis": "-Z",
            },
        )
        self.assertValid(doc)

    def test_minor_version_ahead_is_accepted_while_unknown_content_is_not(self) -> None:
        """Section F: a higher MINOR version is readable; unknown content is not."""
        self.assertValid(document(schema_version="1.4.2"))
        self.assertRules(
            document(box_feature(bevel=1), schema_version="1.4.2"),
            "S3",
        )

    def test_all_six_signed_axes_are_accepted(self) -> None:
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=axis):
                self.assertValid(document(cylinder_feature(axis=axis)))

    def test_both_edge_selector_forms_are_accepted(self) -> None:
        for edges in ({"select": "all"}, {"select": "axis_parallel", "axis": "X"},
                      {"select": "axis_parallel", "axis": "Y"}, {"select": "axis_parallel", "axis": "Z"}):
            with self.subTest(edges=edges):
                doc = document(
                    box_feature(),
                    {"id": "f", "type": "fillet", "target": "plate", "radius": 2, "edges": edges},
                )
                self.assertValid(doc)


# --- the worked example from the specification ------------------------------


class TestSpecificationExample(ValidatorTestCase):
    """The Section D example must pass static validation as written."""

    @staticmethod
    def load_example() -> Dict[str, Any]:
        text = SPECIFICATION_PATH.read_text(encoding="utf-8")
        section = re.search(
            r"^## D\. Example specification$(.*?)^## E\.", text, re.MULTILINE | re.DOTALL
        )
        assert section is not None, f"Section D not found in {SPECIFICATION_PATH}"
        block = re.search(r"^```json$\n(.*?)^```$", section.group(1), re.MULTILINE | re.DOTALL)
        assert block is not None, f"no JSON example found in Section D of {SPECIFICATION_PATH}"
        return json.loads(block.group(1))

    def test_example_plate_is_statically_valid(self) -> None:
        doc = self.load_example()
        part = self.assertValid(doc).part
        assert part is not None
        self.assertEqual(part.name, "plate-100x60x10-4holes")
        self.assertEqual(part.units, "mm")
        self.assertEqual(len(part.features), 5)
        self.assertEqual(part.features[0].size, Size(100.0, 60.0, 10.0))
        holes = part.features[1:]
        self.assertEqual([hole.diameter for hole in holes], [8.0, 8.0, 8.0, 8.0])
        self.assertEqual(
            [(hole.position.x, hole.position.y) for hole in holes],
            [(10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0)],
        )
        self.assertEqual({hole.target for hole in holes}, {"plate"})
        self.assertEqual({hole.axis for hole in holes}, {"+Z"})


# --- root object: S1, S3, S4, S5 -------------------------------------------


class TestRootObject(ValidatorTestCase):
    def test_document_must_be_an_object(self) -> None:
        for value in ([], "a part", 7, None, True):
            with self.subTest(value=value):
                self.assertRules(value, "S1")

    def test_missing_schema_version(self) -> None:
        doc = document()
        del doc["schema_version"]
        self.assertRuleAt(doc, "S1", "schema_version")
        self.assertRules(doc, "S1")

    def test_missing_each_required_root_field(self) -> None:
        for field in ("schema_version", "units", "name", "features"):
            with self.subTest(field=field):
                doc = document()
                del doc[field]
                self.assertRuleAt(doc, "S1", field)

    def test_missing_all_required_root_fields(self) -> None:
        self.assertRules({}, "S1", "S1", "S1", "S1")

    def test_unknown_root_field(self) -> None:
        self.assertRuleAt(document(material="steel"), "S3", "material")

    def test_multiple_unknown_root_fields_are_all_reported(self) -> None:
        self.assertRules(document(material="steel", tolerance=0.1), "S3", "S3")

    def test_name_must_be_a_string(self) -> None:
        self.assertRuleAt(document(name=42), "S1", "name")

    def test_description_must_be_a_string(self) -> None:
        self.assertRuleAt(document(description=["a", "b"]), "S1", "description")

    def test_unsupported_schema_major_version(self) -> None:
        self.assertRuleAt(document(schema_version="2.0.0"), "S4", "schema_version")
        self.assertRules(document(schema_version="0.9.0"), "S4")

    def test_malformed_schema_version(self) -> None:
        for value in ("1.0", "v1.0.0", "1.0.0-beta", "", "01.0.0", 1.0, None):
            with self.subTest(value=value):
                self.assertRules(document(schema_version=value), "S4")

    def test_unsupported_units(self) -> None:
        for value in ("cm", "m", "in", "MM", "millimetres", "", 1, None):
            with self.subTest(value=value):
                self.assertRuleAt(document(units=value), "S5", "units")


# --- feature list structure: S2, S3 ----------------------------------------


class TestFeatureStructure(ValidatorTestCase):
    def test_features_must_be_an_array(self) -> None:
        self.assertRuleAt(document(features={"id": "plate", "type": "box"}), "S2", "features")

    def test_features_must_not_be_empty(self) -> None:
        self.assertRuleAt(document(features=[]), "S2", "features")

    def test_feature_must_be_an_object(self) -> None:
        self.assertRuleAt(document("plate"), "S2", "features[0]")

    def test_feature_requires_an_id(self) -> None:
        feature = box_feature()
        del feature["id"]
        error = self.assertRuleAt(document(feature), "S2", "features[0].id")
        self.assertEqual(error.feature_index, 0)
        self.assertIsNone(error.feature_id)

    def test_feature_id_must_be_a_string(self) -> None:
        self.assertRuleAt(document(box_feature(id=7)), "S2", "features[0].id")

    def test_feature_requires_a_type(self) -> None:
        feature = box_feature()
        del feature["type"]
        self.assertRuleAt(document(feature), "S2", "features[0].type")

    def test_feature_type_must_be_a_string(self) -> None:
        self.assertRuleAt(document(box_feature(type=["box"])), "S2", "features[0].type")

    def test_unknown_feature_type(self) -> None:
        for unknown in ("sketch", "sweep", "loft", "revolve", "thread", "Box", "pattern"):
            with self.subTest(type=unknown):
                error = self.assertRuleAt(
                    document(box_feature(type=unknown)), "S3", "features[0].type"
                )
                self.assertEqual(error.feature_id, "plate")

    def test_unknown_feature_field(self) -> None:
        error = self.assertRuleAt(document(box_feature(colour="red")), "S3", "features[0].colour")
        self.assertEqual(error.feature_id, "plate")

    def test_unknown_feature_fields_are_all_reported(self) -> None:
        self.assertRules(document(box_feature(colour="red", material="steel")), "S3", "S3")

    def test_parameter_valid_for_another_type_is_still_unknown(self) -> None:
        """A box has no 'diameter'; permissiveness across types is not allowed."""
        self.assertRuleAt(document(box_feature(diameter=8)), "S3", "features[0].diameter")

    def test_missing_required_feature_parameters(self) -> None:
        cases = [
            (box_feature(), "size"),
            (cylinder_feature(), "diameter"),
            (cylinder_feature(), "height"),
        ]
        for feature, parameter in cases:
            with self.subTest(parameter=parameter):
                incomplete = dict(feature)
                del incomplete[parameter]
                self.assertRuleAt(document(incomplete), "S2", f"features[0].{parameter}")

    def test_missing_required_modifier_parameters(self) -> None:
        base = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
        )
        for parameter in ("target", "diameter", "position"):
            with self.subTest(parameter=parameter):
                doc = json.loads(json.dumps(base))
                del doc["features"][1][parameter]
                self.assertRuleAt(doc, "S2", f"features[1].{parameter}")


# --- ids: S8 ---------------------------------------------------------------


class TestFeatureIds(ValidatorTestCase):
    def test_duplicate_feature_ids(self) -> None:
        doc = document(box_feature(), cylinder_feature(id="plate"))
        error = self.assertRuleAt(doc, "S8", "features[1].id")
        self.assertEqual(error.feature_id, "plate")

    def test_three_features_sharing_one_id_report_two_duplicates(self) -> None:
        doc = document(
            box_feature(),
            cylinder_feature(id="plate"),
            cylinder_feature(id="plate"),
        )
        codes = [error.rule for error in validate(doc).errors]
        self.assertEqual(codes.count("S8"), 2)

    def test_invalid_feature_ids(self) -> None:
        for invalid in ("1plate", "-plate", "my plate", "", "plate!", "pläte", "plate.top", "9"):
            with self.subTest(id=invalid):
                self.assertRuleAt(document(box_feature(id=invalid)), "S8", "features[0].id")

    def test_valid_feature_ids(self) -> None:
        for valid in ("plate", "_plate", "Plate2", "hole-1", "a", "A_1-b"):
            with self.subTest(id=valid):
                self.assertValid(document(box_feature(id=valid)))


# --- references and order: S6, S7, S9 --------------------------------------


class TestReferencesAndOrder(ValidatorTestCase):
    def test_forward_reference(self) -> None:
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "later_box",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
            box_feature(id="later_box"),
        )
        self.assertRuleAt(doc, "S7", "features[1].target")

    def test_self_reference(self) -> None:
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "hole",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
        )
        self.assertRuleAt(doc, "S7", "features[1].target")

    def test_reference_to_a_nonexistent_feature(self) -> None:
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "nope",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
        )
        self.assertRuleAt(doc, "S6", "features[1].target")

    def test_reference_to_a_consumed_solid(self) -> None:
        """A tool solid is consumed by its subtract and cannot be reused."""
        doc = document(
            box_feature(),
            cylinder_feature(id="tool", position={"x": 10, "y": 10, "z": 0}),
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            {"id": "cut_again", "type": "subtract", "target": "plate", "tools": ["tool"]},
        )
        self.assertRuleAt(doc, "S6", "features[3].tools[0]")

    def test_reference_to_a_modifier_id_is_not_a_solid(self) -> None:
        """Section B.4: a modifier's own id never names a solid."""
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
            {
                "id": "round_it",
                "type": "fillet",
                "target": "hole",
                "radius": 2,
                "edges": {"select": "all"},
            },
        )
        error = self.assertRuleAt(doc, "S6", "features[2].target")
        self.assertIn("modifier", error.message)

    def test_reference_must_be_a_string(self) -> None:
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": 0,
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
        )
        self.assertRuleAt(doc, "S2", "features[1].target")

    def test_first_feature_must_be_constructive(self) -> None:
        doc = document(
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            },
            box_feature(),
        )
        self.assertRuleAt(doc, "S9", "features[0].type")

    def test_first_feature_constructive_check_reports_alongside_reference_error(self) -> None:
        doc = document(
            {"id": "cut", "type": "subtract", "target": "missing", "tools": ["gone"]},
            box_feature(),
        )
        codes = sorted(validate(doc).rule_codes())
        self.assertEqual(codes, ["S6", "S6", "S9"])

    def test_leftover_solid_is_rejected(self) -> None:
        """Rule S9: an unconsumed cylinder is a second solid, not a second body."""
        doc = document(box_feature(), cylinder_feature())
        error = self.assertRuleAt(doc, "S9", "features")
        self.assertIn("exactly one solid", error.message)
        self.assertIn("2", error.message)

    def test_three_leftover_solids_are_rejected_once(self) -> None:
        doc = document(box_feature(), cylinder_feature(), cylinder_feature(id="pin2"))
        self.assertRules(doc, "S9")

    def test_consuming_the_leftover_solid_makes_it_valid(self) -> None:
        doc = document(
            box_feature(),
            cylinder_feature(position={"x": 10, "y": 10, "z": 0}),
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["pin"]},
        )
        self.assertValid(doc)

    def test_final_solid_count_is_suppressed_when_a_feature_is_not_understood(self) -> None:
        """An unknown type already fails; the count must not pile on."""
        doc = document(box_feature(), cylinder_feature(type="sweep"))
        self.assertRules(doc, "S3")


# --- box parameters: S10, S19 ----------------------------------------------


class TestBoxParameters(ValidatorTestCase):
    def test_zero_and_negative_size_components(self) -> None:
        for component in ("x", "y", "z"):
            for value in (0, -1, -0.001):
                with self.subTest(component=component, value=value):
                    size = {"x": 100, "y": 60, "z": 10}
                    size[component] = value
                    self.assertRuleAt(
                        document(box_feature(size=size)), "S10", f"features[0].size.{component}"
                    )

    def test_all_three_size_violations_are_reported(self) -> None:
        doc = document(box_feature(size={"x": 0, "y": -1, "z": 0}))
        self.assertRules(doc, "S10", "S10", "S10")

    def test_size_must_be_an_object(self) -> None:
        for value in ([100, 60, 10], 100, "100x60x10", None):
            with self.subTest(value=value):
                self.assertRuleAt(document(box_feature(size=value)), "S19", "features[0].size")

    def test_size_missing_a_component(self) -> None:
        self.assertRuleAt(
            document(box_feature(size={"x": 100, "y": 60})), "S19", "features[0].size.z"
        )

    def test_size_with_an_unknown_component(self) -> None:
        self.assertRuleAt(
            document(box_feature(size={"x": 100, "y": 60, "z": 10, "w": 1})),
            "S19",
            "features[0].size.w",
        )

    def test_size_component_must_be_a_number(self) -> None:
        for value in ("100", None, True, [100], {"value": 100}):
            with self.subTest(value=value):
                self.assertRuleAt(
                    document(box_feature(size={"x": value, "y": 60, "z": 10})),
                    "S19",
                    "features[0].size.x",
                )

    def test_position_must_be_an_object(self) -> None:
        for value in ([0, 0, 0], 0, "origin", None):
            with self.subTest(value=value):
                self.assertRuleAt(
                    document(box_feature(position=value)), "S19", "features[0].position"
                )

    def test_position_missing_a_component(self) -> None:
        self.assertRuleAt(
            document(box_feature(position={"x": 0, "z": 0})), "S19", "features[0].position.y"
        )

    def test_position_with_an_unknown_component(self) -> None:
        self.assertRuleAt(
            document(box_feature(position={"x": 0, "y": 0, "z": 0, "rx": 90})),
            "S19",
            "features[0].position.rx",
        )

    def test_position_component_must_be_a_number(self) -> None:
        self.assertRuleAt(
            document(box_feature(position={"x": "0", "y": 0, "z": 0})),
            "S19",
            "features[0].position.x",
        )

    def test_booleans_are_not_numbers(self) -> None:
        self.assertRuleAt(
            document(box_feature(size={"x": True, "y": 60, "z": 10})),
            "S19",
            "features[0].size.x",
        )


# --- cylinder parameters: S11, S12, S20 ------------------------------------


class TestCylinderParameters(ValidatorTestCase):
    def test_invalid_diameter(self) -> None:
        for value in (0, -8, -0.5):
            with self.subTest(value=value):
                self.assertRuleAt(
                    document(cylinder_feature(diameter=value)), "S11", "features[0].diameter"
                )

    def test_invalid_height(self) -> None:
        for value in (0, -20):
            with self.subTest(value=value):
                self.assertRuleAt(
                    document(cylinder_feature(height=value)), "S11", "features[0].height"
                )

    def test_both_invalid_dimensions_are_reported(self) -> None:
        self.assertRules(document(cylinder_feature(diameter=0, height=-1)), "S11", "S11")

    def test_non_numeric_dimensions(self) -> None:
        for parameter in ("diameter", "height"):
            for value in ("8", None, True, [8]):
                with self.subTest(parameter=parameter, value=value):
                    self.assertRuleAt(
                        document(cylinder_feature(**{parameter: value})),
                        "S20",
                        f"features[0].{parameter}",
                    )

    def test_invalid_signed_axis(self) -> None:
        for value in ("Z", "z", "+z", "+W", "Z+", "", None, 0, ["+Z"]):
            with self.subTest(axis=value):
                self.assertRuleAt(
                    document(cylinder_feature(axis=value)), "S12", "features[0].axis"
                )


# --- through-hole parameters: S13 ------------------------------------------


class TestThroughHoleParameters(ValidatorTestCase):
    @staticmethod
    def hole_document(**overrides: Any) -> Dict[str, Any]:
        hole: Dict[str, Any] = {
            "id": "hole",
            "type": "through_hole",
            "target": "plate",
            "diameter": 8,
            "position": {"x": 10, "y": 10, "z": 0},
        }
        hole.update(overrides)
        return document(box_feature(), hole)

    def test_invalid_diameter(self) -> None:
        for value in (0, -8):
            with self.subTest(value=value):
                self.assertRuleAt(self.hole_document(diameter=value), "S13", "features[1].diameter")

    def test_non_numeric_diameter(self) -> None:
        self.assertRuleAt(self.hole_document(diameter="8"), "S20", "features[1].diameter")

    def test_invalid_axis(self) -> None:
        self.assertRuleAt(self.hole_document(axis="up"), "S12", "features[1].axis")

    def test_invalid_position(self) -> None:
        self.assertRuleAt(self.hole_document(position=[10, 10, 0]), "S19", "features[1].position")


# --- subtract parameters: S14, S15 -----------------------------------------


class TestSubtractParameters(ValidatorTestCase):
    @staticmethod
    def subtract_document(**overrides: Any) -> Dict[str, Any]:
        feature: Dict[str, Any] = {
            "id": "cut",
            "type": "subtract",
            "target": "plate",
            "tools": ["pin"],
        }
        feature.update(overrides)
        return document(
            box_feature(),
            cylinder_feature(position={"x": 10, "y": 10, "z": 0}),
            feature,
        )

    def test_tools_must_be_present(self) -> None:
        doc = self.subtract_document()
        del doc["features"][2]["tools"]
        self.assertRuleAt(doc, "S2", "features[2].tools")

    def test_tools_must_be_an_array(self) -> None:
        for value in ("pin", {"id": "pin"}, 1, None):
            with self.subTest(value=value):
                self.assertRuleAt(self.subtract_document(tools=value), "S14", "features[2].tools")

    def test_tools_must_not_be_empty(self) -> None:
        self.assertRuleAt(self.subtract_document(tools=[]), "S14", "features[2].tools")

    def test_tool_entries_must_be_strings(self) -> None:
        self.assertRuleAt(self.subtract_document(tools=[{"id": "pin"}]), "S2", "features[2].tools[0]")

    def test_duplicate_tools(self) -> None:
        doc = self.subtract_document(tools=["pin", "pin"])
        error = self.assertRuleAt(doc, "S15", "features[2].tools[1]")
        self.assertIn("duplicate", error.message)
        # S15 is the root cause; consuming 'pin' twice must not also be
        # reported as S6 or as a wrong final solid count.
        self.assertRules(doc, "S15")

    def test_target_must_not_appear_in_tools(self) -> None:
        doc = self.subtract_document(tools=["pin", "plate"])
        self.assertRuleAt(doc, "S15", "features[2].tools[1]")
        self.assertRules(doc, "S15")

    def test_target_only_in_tools(self) -> None:
        doc = self.subtract_document(tools=["plate"])
        self.assertRuleAt(doc, "S15", "features[2].tools[0]")
        self.assertRules(doc, "S15")

    def test_an_unresolvable_tool_is_still_reported(self) -> None:
        """Suppressing S15 fallout must not hide a genuine S6."""
        self.assertRuleAt(self.subtract_document(tools=["ghost"]), "S6", "features[2].tools[0]")


# --- fillet and chamfer: S16, S17, S18 -------------------------------------


class TestFilletAndChamfer(ValidatorTestCase):
    @staticmethod
    def modifier_document(feature_type: str, **overrides: Any) -> Dict[str, Any]:
        size_parameter = "radius" if feature_type == "fillet" else "distance"
        feature: Dict[str, Any] = {
            "id": "edge_treatment",
            "type": feature_type,
            "target": "plate",
            size_parameter: 2,
            "edges": {"select": "all"},
        }
        feature.update(overrides)
        return document(box_feature(), feature)

    def test_invalid_fillet_radius(self) -> None:
        for value in (0, -2):
            with self.subTest(value=value):
                self.assertRuleAt(
                    self.modifier_document("fillet", radius=value), "S16", "features[1].radius"
                )

    def test_non_numeric_fillet_radius(self) -> None:
        self.assertRuleAt(
            self.modifier_document("fillet", radius="2"), "S20", "features[1].radius"
        )

    def test_invalid_chamfer_distance(self) -> None:
        for value in (0, -1):
            with self.subTest(value=value):
                self.assertRuleAt(
                    self.modifier_document("chamfer", distance=value), "S17", "features[1].distance"
                )

    def test_non_numeric_chamfer_distance(self) -> None:
        self.assertRuleAt(
            self.modifier_document("chamfer", distance=None), "S20", "features[1].distance"
        )

    def test_edges_must_be_present(self) -> None:
        doc = self.modifier_document("fillet")
        del doc["features"][1]["edges"]
        self.assertRuleAt(doc, "S2", "features[1].edges")

    def test_edges_must_be_an_object(self) -> None:
        for value in ("all", ["all"], None, 1):
            with self.subTest(value=value):
                self.assertRuleAt(
                    self.modifier_document("fillet", edges=value), "S18", "features[1].edges"
                )

    def test_edge_selector_requires_select(self) -> None:
        self.assertRuleAt(
            self.modifier_document("fillet", edges={}), "S18", "features[1].edges.select"
        )

    def test_unknown_select_value(self) -> None:
        for value in ("ALL", "any", "vertical", "axis-parallel", "", None, 1):
            with self.subTest(value=value):
                self.assertRuleAt(
                    self.modifier_document("fillet", edges={"select": value}),
                    "S18",
                    "features[1].edges.select",
                )

    def test_axis_must_be_absent_for_select_all(self) -> None:
        self.assertRuleAt(
            self.modifier_document("fillet", edges={"select": "all", "axis": "Z"}),
            "S18",
            "features[1].edges.axis",
        )

    def test_axis_is_required_for_axis_parallel(self) -> None:
        self.assertRuleAt(
            self.modifier_document("fillet", edges={"select": "axis_parallel"}),
            "S18",
            "features[1].edges.axis",
        )

    def test_selector_axis_must_be_unsigned(self) -> None:
        for value in ("+Z", "-Z", "z", "W", "", None, 0):
            with self.subTest(axis=value):
                self.assertRuleAt(
                    self.modifier_document(
                        "fillet", edges={"select": "axis_parallel", "axis": value}
                    ),
                    "S18",
                    "features[1].edges.axis",
                )

    def test_unknown_edge_selector_key(self) -> None:
        self.assertRuleAt(
            self.modifier_document("fillet", edges={"select": "all", "tangent": True}),
            "S18",
            "features[1].edges.tangent",
        )


# --- numeric finiteness: S19, S20 ------------------------------------------


class TestNumericFiniteness(ValidatorTestCase):
    """Rules S19 and S20: NaN and infinities are invalid.

    ``json.loads`` accepts the non-standard ``NaN``/``Infinity`` literals by
    default, so these values are representable through the input interface.
    """

    def test_json_admits_non_finite_literals(self) -> None:
        parsed = json.loads('{"x": NaN, "y": Infinity, "z": -Infinity}')
        self.assertTrue(math.isnan(parsed["x"]))
        self.assertTrue(math.isinf(parsed["y"]))

    def test_non_finite_size_component(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertRuleAt(
                    document(box_feature(size={"x": value, "y": 60, "z": 10})),
                    "S19",
                    "features[0].size.x",
                )

    def test_non_finite_position_component(self) -> None:
        self.assertRuleAt(
            document(box_feature(position={"x": float("inf"), "y": 0, "z": 0})),
            "S19",
            "features[0].position.x",
        )

    def test_non_finite_scalar_lengths(self) -> None:
        cases = [
            (document(cylinder_feature(diameter=float("nan"))), "features[0].diameter"),
            (document(cylinder_feature(height=float("inf"))), "features[0].height"),
        ]
        for doc, path in cases:
            with self.subTest(path=path):
                self.assertRuleAt(doc, "S20", path)

    def test_non_finite_through_hole_diameter(self) -> None:
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": float("inf"),
                "position": {"x": 10, "y": 10, "z": 0},
            },
        )
        self.assertRuleAt(doc, "S20", "features[1].diameter")

    def test_non_finite_fillet_radius_and_chamfer_distance(self) -> None:
        for feature_type, parameter in (("fillet", "radius"), ("chamfer", "distance")):
            with self.subTest(feature_type=feature_type):
                doc = document(
                    box_feature(),
                    {
                        "id": "edge_treatment",
                        "type": feature_type,
                        "target": "plate",
                        parameter: float("nan"),
                        "edges": {"select": "all"},
                    },
                )
                self.assertRuleAt(doc, "S20", f"features[1].{parameter}")

    def test_document_parsed_from_json_text_with_nan(self) -> None:
        text = """
        {"schema_version": "1.0.0", "units": "mm", "name": "p",
         "features": [{"id": "b", "type": "box",
                       "size": {"x": NaN, "y": 60, "z": 10}}]}
        """
        self.assertRules(json.loads(text), "S19")


# --- reporting behaviour ---------------------------------------------------


class TestReportingBehaviour(ValidatorTestCase):
    def test_all_violations_are_reported_not_just_the_first(self) -> None:
        doc = {
            "units": "cm",
            "name": 5,
            "material": "steel",
            "schema_version": "3.0.0",
            "features": [
                {"id": "1bad", "type": "box", "size": {"x": 0, "y": 60, "z": 10}, "colour": "red"},
                {"id": "1bad", "type": "sweep"},
            ],
        }
        reported = sorted(
            (error.rule, error.field_path) for error in validate(doc).errors
        )
        self.assertEqual(
            reported,
            [
                ("S1", "name"),
                ("S10", "features[0].size.x"),
                ("S3", "features[0].colour"),
                ("S3", "features[1].type"),
                ("S3", "material"),
                ("S4", "schema_version"),
                ("S5", "units"),
                ("S8", "features[0].id"),
                ("S8", "features[1].id"),
                ("S8", "features[1].id"),
            ],
        )

    def test_validation_is_deterministic(self) -> None:
        doc = {
            "units": "cm",
            "zeta": 1,
            "alpha": 2,
            "features": [
                {"id": "b", "type": "box", "size": {"z": 0, "x": -1}},
                {"id": "b", "type": "fillet", "target": "later", "radius": 0, "edges": {"select": "no"}},
                {"id": "later", "type": "box", "size": {"x": 1, "y": 1, "z": 1}},
            ],
        }
        first = validate(doc)
        second = validate(doc)
        self.assertEqual(first, second)
        self.assertEqual(
            [str(error) for error in first.errors], [str(error) for error in second.errors]
        )

    def test_unknown_key_order_does_not_change_the_report(self) -> None:
        forwards = document(box_feature(), alpha=1, zeta=2)
        backwards = document(box_feature(), zeta=2, alpha=1)
        self.assertEqual(
            [str(error) for error in validate(forwards).errors],
            [str(error) for error in validate(backwards).errors],
        )

    def test_errors_carry_structured_context(self) -> None:
        doc = document(box_feature(size={"x": -1, "y": 60, "z": 10}))
        error = validate(doc).errors[0]
        self.assertEqual(error.rule, "S10")
        self.assertEqual(error.feature_id, "plate")
        self.assertEqual(error.feature_index, 0)
        self.assertEqual(error.field_path, "features[0].size.x")
        self.assertIn("must be > 0", error.message)
        self.assertEqual(str(error), f"S10 at features[0].size.x: {error.message}")

    def test_expected_failures_are_returned_not_raised(self) -> None:
        result = validate({"nonsense": True})
        self.assertIsInstance(result, ValidationResult)
        self.assertFalse(result.valid)
        self.assertNotIsInstance(ValidationError("S1", "m"), Exception)

    def test_every_reported_rule_code_is_a_known_static_rule(self) -> None:
        documents: List[Any] = [
            None,
            {},
            document(box_feature(size={"x": 0, "y": 0, "z": 0})),
            document(box_feature(type="loft")),
            document(cylinder_feature(axis="up")),
            self.hole_and_fillet_document(),
        ]
        for doc in documents:
            for error in validate(doc).errors:
                self.assertIn(error.rule, STATIC_RULE_CODES)

    @staticmethod
    def hole_and_fillet_document() -> Dict[str, Any]:
        return document(
            box_feature(),
            {"id": "f", "type": "fillet", "target": "gone", "radius": -1, "edges": {"select": "x"}},
        )


# --- the geometric boundary: E1-E5 ----------------------------------------


class TestGeometricRuleBoundary(ValidatorTestCase):
    """The static validator must neither implement nor pretend to implement E1-E5."""

    def test_static_validator_never_reports_a_geometric_rule(self) -> None:
        documents: List[Any] = [
            document(),
            document(
                box_feature(),
                {
                    "id": "hole",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 8,
                    "position": {"x": 10_000, "y": 10_000, "z": 0},
                },
            ),
            document(
                box_feature(),
                {
                    "id": "big_fillet",
                    "type": "fillet",
                    "target": "plate",
                    "radius": 500,
                    "edges": {"select": "all"},
                },
            ),
        ]
        for doc in documents:
            result = validate(doc)
            for error in result.errors:
                self.assertNotIn(error.rule, GEOMETRIC_RULE_CODES)

    def test_geometrically_impossible_document_still_passes_static_validation(self) -> None:
        """A hole outside the plate violates E1 only; statically it is valid."""
        doc = document(
            box_feature(),
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10_000, "y": 10_000, "z": 0},
            },
        )
        self.assertValid(doc)

    def test_geometric_rules_are_not_implemented(self) -> None:
        part = self.assertValid(document()).part
        with self.assertRaises(NotImplementedError):
            check_geometric_rules(part)

    def test_geometric_and_static_rule_codes_are_disjoint(self) -> None:
        self.assertEqual(set(GEOMETRIC_RULE_CODES) & set(STATIC_RULE_CODES), set())
        self.assertEqual(set(GEOMETRIC_RULE_CODES), {"E1", "E2", "E3", "E4", "E5"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
