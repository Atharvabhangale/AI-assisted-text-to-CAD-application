"""Unit tests for canonical CAD document serialization.

The CAD document is the authoritative artifact, so these tests are about the
document and nothing else: no geometry is built anywhere in this file, and two
tests assert that neither serialization nor deserialization can even load a
geometry kernel.

Canonical means *exactly one* byte string per valid part. That is asserted
directly -- repeated serialization, insertion-order independence, the two
spellings of a default, and negative zero all collapse to the same bytes.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cad_core import validate
from cad_core.errors import ValidationResult
from cad_core.model import (
    COMMON_FEATURE_FIELDS,
    FEATURE_PARAMETERS,
    FEATURE_TYPES,
    Box,
    EdgeSelector,
    Part,
    Position,
    Size,
)
from cad_core.serialization import (
    CANONICAL_ENCODING,
    DOCUMENT_EXTENSIONS,
    HASH_ALGORITHM,
    ROOT_FIELD_ORDER,
    SELECTOR_FIELD_ORDER,
    VECTOR_FIELD_ORDER,
    CadDocumentError,
    DocumentParseError,
    DocumentValidationError,
    deserialize_part,
    load_part,
    part_from_json,
    part_hash,
    part_to_bytes,
    part_to_json,
    parts_equivalent,
    save_part,
    serialize_part,
)

# --- documents used throughout ---------------------------------------------


def plate_feature(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
        "position": {"x": 0, "y": 0, "z": 0},
    }
    feature.update(overrides)
    return feature


def document(features: List[Dict[str, Any]], **overrides: Any) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate",
        "features": features,
    }
    doc.update(overrides)
    return doc


#: Section D of the specification: a 100x60x10 plate with four 8 mm holes,
#: 10 mm from each corner.
SECTION_D_HOLES = ((10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0))


def section_d_document() -> Dict[str, Any]:
    features: List[Dict[str, Any]] = [plate_feature()]
    for name, (x, y) in zip(
        ("hole_front_left", "hole_front_right", "hole_back_left", "hole_back_right"),
        SECTION_D_HOLES,
    ):
        features.append(
            {
                "id": name,
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": x, "y": y, "z": 0},
                "axis": "+Z",
            }
        )
    return document(
        features,
        name="plate-100x60x10-4holes",
        description=(
            "100 x 60 x 10 mm plate with four 8 mm through-holes, 10 mm from "
            "each corner"
        ),
    )


def every_feature_document() -> Dict[str, Any]:
    """One document containing all six V1 feature types."""
    return document(
        [
            plate_feature(),
            {
                "id": "tool",
                "type": "cylinder",
                "diameter": 20,
                "height": 20,
                "position": {"x": 20, "y": 20, "z": -5},
                "axis": "+Z",
            },
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            {
                "id": "bore",
                "type": "through_hole",
                "target": "plate",
                "diameter": 10,
                "position": {"x": 80, "y": 40, "z": 0},
                "axis": "-Z",
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
        ],
        name="every-feature",
    )


class SerializationTestCase(unittest.TestCase):
    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def canonical(self, doc: Dict[str, Any]) -> str:
        return part_to_json(self.part_from(doc))


# --- 1 & 2: every feature type, and the Section D plate --------------------


class TestFeatureCoverage(SerializationTestCase):
    def test_every_v1_feature_type_serializes(self) -> None:
        part = self.part_from(every_feature_document())
        document_out = serialize_part(part)
        kinds = [feature["type"] for feature in document_out["features"]]
        self.assertEqual(set(kinds), set(FEATURE_TYPES))
        self.assertEqual(len(kinds), len(FEATURE_TYPES))

    def test_every_feature_round_trips(self) -> None:
        part = self.part_from(every_feature_document())
        self.assertTrue(parts_equivalent(part, part_from_json(part_to_json(part))))

    def test_each_feature_emits_the_specification_field_order(self) -> None:
        """Key order follows the Section C tables, not a hand-written list."""
        part = self.part_from(every_feature_document())
        for feature in serialize_part(part)["features"]:
            required, optional = FEATURE_PARAMETERS[feature["type"]]
            expected = list(COMMON_FEATURE_FIELDS + required + optional)
            with self.subTest(feature=feature["id"]):
                self.assertEqual(list(feature.keys()), expected)

    def test_the_root_emits_the_documented_field_order(self) -> None:
        with_description = serialize_part(self.part_from(section_d_document()))
        self.assertEqual(list(with_description.keys()), list(ROOT_FIELD_ORDER))
        without = serialize_part(self.part_from(document([plate_feature()])))
        self.assertEqual(
            list(without.keys()),
            [field for field in ROOT_FIELD_ORDER if field != "description"],
        )

    def test_a_selector_emits_select_then_axis(self) -> None:
        part = self.part_from(every_feature_document())
        selectors = [
            feature["edges"]
            for feature in serialize_part(part)["features"]
            if "edges" in feature
        ]
        self.assertEqual(len(selectors), 2)
        for selector in selectors:
            with self.subTest(selector=selector):
                self.assertEqual(
                    list(selector.keys()),
                    [
                        field
                        for field in SELECTOR_FIELD_ORDER
                        if field in selector
                    ],
                )
        self.assertEqual(
            [list(selector.keys()) for selector in selectors],
            [["select", "axis"], ["select"]],
        )

    def test_a_vector_emits_x_y_z(self) -> None:
        part = self.part_from(every_feature_document())
        box = serialize_part(part)["features"][0]
        for field in ("size", "position"):
            with self.subTest(field=field):
                self.assertEqual(list(box[field].keys()), list(VECTOR_FIELD_ORDER))


class TestSectionDPlate(SerializationTestCase):
    """The specification's own worked example, end to end."""

    def part(self) -> Part:
        return self.part_from(section_d_document())

    def test_it_serializes_to_the_expected_canonical_text(self) -> None:
        expected = (
            '{"schema_version":"1.0.0","units":"mm",'
            '"name":"plate-100x60x10-4holes",'
            '"description":"100 x 60 x 10 mm plate with four 8 mm '
            'through-holes, 10 mm from each corner",'
            '"features":['
            '{"id":"plate","type":"box","size":{"x":100.0,"y":60.0,"z":10.0},'
            '"position":{"x":0.0,"y":0.0,"z":0.0}},'
            '{"id":"hole_front_left","type":"through_hole","target":"plate",'
            '"diameter":8.0,"position":{"x":10.0,"y":10.0,"z":0.0},"axis":"+Z"},'
            '{"id":"hole_front_right","type":"through_hole","target":"plate",'
            '"diameter":8.0,"position":{"x":90.0,"y":10.0,"z":0.0},"axis":"+Z"},'
            '{"id":"hole_back_left","type":"through_hole","target":"plate",'
            '"diameter":8.0,"position":{"x":10.0,"y":50.0,"z":0.0},"axis":"+Z"},'
            '{"id":"hole_back_right","type":"through_hole","target":"plate",'
            '"diameter":8.0,"position":{"x":90.0,"y":50.0,"z":0.0},"axis":"+Z"}'
            "]}"
        )
        self.assertEqual(part_to_json(self.part()), expected)

    def test_it_round_trips_through_json(self) -> None:
        part = self.part()
        text = part_to_json(part)
        rebuilt = part_from_json(text)
        self.assertEqual(part_to_json(rebuilt), text)
        self.assertTrue(parts_equivalent(part, rebuilt))

    def test_the_rebuilt_part_is_field_for_field_identical(self) -> None:
        part = self.part()
        rebuilt = part_from_json(part_to_json(part))
        self.assertEqual(part, rebuilt)
        self.assertEqual(len(rebuilt.features), 5)
        self.assertEqual(rebuilt.description, part.description)

    def test_its_hash_is_stable(self) -> None:
        part = self.part()
        digests = {part_hash(part) for _ in range(5)}
        self.assertEqual(len(digests), 1)
        self.assertEqual(part_hash(part), part_hash(part_from_json(part_to_json(part))))

    def test_feature_order_is_preserved(self) -> None:
        ids = [
            feature["id"] for feature in serialize_part(self.part())["features"]
        ]
        self.assertEqual(
            ids,
            [
                "plate",
                "hole_front_left",
                "hole_front_right",
                "hole_back_left",
                "hole_back_right",
            ],
        )


# --- 3 & 4: round trip and byte-identical canonical form -------------------


class TestRoundTrip(SerializationTestCase):
    def documents(self) -> Tuple[Dict[str, Any], ...]:
        return (
            document([plate_feature()]),
            section_d_document(),
            every_feature_document(),
            document([plate_feature()], description="a plate"),
            document(
                [
                    {
                        "id": "pin",
                        "type": "cylinder",
                        "diameter": 20,
                        "height": 50,
                        "position": {"x": -10, "y": 20, "z": -30},
                        "axis": "-Y",
                    }
                ],
                name="pin",
            ),
        )

    def test_json_to_part_to_json_is_a_fixed_point(self) -> None:
        for doc in self.documents():
            with self.subTest(name=doc["name"]):
                canonical = self.canonical(doc)
                once = part_from_json(canonical)
                self.assertEqual(part_to_json(once), canonical)
                twice = part_from_json(part_to_json(once))
                self.assertEqual(part_to_json(twice), canonical)

    def test_repeated_serialization_is_byte_identical(self) -> None:
        for doc in self.documents():
            part = self.part_from(doc)
            with self.subTest(name=doc["name"]):
                self.assertEqual(len({part_to_bytes(part) for _ in range(10)}), 1)

    def test_serialization_of_two_equal_parts_is_byte_identical(self) -> None:
        """Two independently validated copies must not differ."""
        for doc in self.documents():
            first = self.part_from(doc)
            second = self.part_from(json.loads(json.dumps(doc)))
            with self.subTest(name=doc["name"]):
                self.assertEqual(part_to_bytes(first), part_to_bytes(second))
                self.assertTrue(parts_equivalent(first, second))

    def test_no_semantic_information_is_lost(self) -> None:
        """Every field of every feature survives the trip."""
        part = self.part_from(every_feature_document())
        rebuilt = part_from_json(part_to_json(part))
        self.assertEqual(part.schema_version, rebuilt.schema_version)
        self.assertEqual(part.units, rebuilt.units)
        self.assertEqual(part.name, rebuilt.name)
        self.assertEqual(part.description, rebuilt.description)
        self.assertEqual(len(part.features), len(rebuilt.features))
        for original, copy in zip(part.features, rebuilt.features):
            with self.subTest(feature=original.id):
                self.assertEqual(type(original), type(copy))
                self.assertEqual(original, copy)

    def test_the_canonical_structure_is_plain_json_data(self) -> None:
        """Only dict/list/str/float appear -- no model or kernel objects."""

        def walk(value: Any, path: str) -> None:
            if isinstance(value, (str, float, int, bool)) or value is None:
                self.assertNotIsInstance(value, bool, msg=path)
                return
            if isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertIsInstance(key, str, msg=path)
                    walk(item, f"{path}.{key}")
                return
            self.fail(f"{path} is a {type(value).__module__}.{type(value).__name__}")

        walk(serialize_part(self.part_from(every_feature_document())), "document")


# --- 5: insertion order must not matter ------------------------------------


class TestInsertionOrderIndependence(SerializationTestCase):
    def shuffled(self, value: Any, reverse: bool = True) -> Any:
        """Rebuild a document with every object's keys in reverse order."""
        if isinstance(value, dict):
            keys = sorted(value, reverse=reverse)
            return {key: self.shuffled(value[key], reverse) for key in keys}
        if isinstance(value, list):
            return [self.shuffled(item, reverse) for item in value]
        return value

    def test_reversed_key_order_gives_the_same_canonical_bytes(self) -> None:
        for doc in (section_d_document(), every_feature_document()):
            with self.subTest(name=doc["name"]):
                straight = self.canonical(doc)
                reordered = self.canonical(self.shuffled(doc))
                self.assertEqual(straight, reordered)

    def test_sorted_key_order_gives_the_same_canonical_bytes(self) -> None:
        for doc in (section_d_document(), every_feature_document()):
            with self.subTest(name=doc["name"]):
                self.assertEqual(
                    self.canonical(doc),
                    self.canonical(self.shuffled(doc, reverse=False)),
                )

    def test_the_input_key_order_really_did_differ(self) -> None:
        """Guard against the test proving nothing."""
        doc = section_d_document()
        self.assertNotEqual(
            json.dumps(doc, sort_keys=False),
            json.dumps(self.shuffled(doc), sort_keys=False),
        )

    def test_the_canonical_order_is_not_alphabetical(self) -> None:
        """It follows the specification's tables, which are not sorted."""
        keys = list(serialize_part(self.part_from(section_d_document())).keys())
        self.assertNotEqual(keys, sorted(keys))
        self.assertEqual(keys[0], "schema_version")


# --- 6: feature and tool ordering are semantic ------------------------------


class TestArrayOrdering(SerializationTestCase):
    def test_feature_order_is_never_sorted(self) -> None:
        doc = document(
            [
                plate_feature(),
                {
                    "id": "zebra",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 8,
                    "position": {"x": 10, "y": 10, "z": 0},
                },
                {
                    "id": "alpha",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 8,
                    "position": {"x": 20, "y": 20, "z": 0},
                },
            ]
        )
        ids = [f["id"] for f in serialize_part(self.part_from(doc))["features"]]
        self.assertEqual(ids, ["plate", "zebra", "alpha"])
        self.assertNotEqual(ids, sorted(ids))

    def test_reordering_features_changes_the_document(self) -> None:
        """Feature order is the design, so it must change the bytes and hash."""
        first = document(
            [
                plate_feature(),
                {
                    "id": "a",
                    "type": "cylinder",
                    "diameter": 8,
                    "height": 20,
                    "position": {"x": 10, "y": 10, "z": -5},
                },
                {
                    "id": "b",
                    "type": "cylinder",
                    "diameter": 6,
                    "height": 20,
                    "position": {"x": 30, "y": 10, "z": -5},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["a", "b"],
                },
            ]
        )
        second = json.loads(json.dumps(first))
        second["features"][1], second["features"][2] = (
            second["features"][2],
            second["features"][1],
        )
        self.assertNotEqual(self.canonical(first), self.canonical(second))
        self.assertNotEqual(
            part_hash(self.part_from(first)), part_hash(self.part_from(second))
        )

    def test_tool_order_is_never_sorted(self) -> None:
        """Section C.4: tools are subtracted in list order."""
        doc = document(
            [
                plate_feature(),
                {
                    "id": "zulu",
                    "type": "cylinder",
                    "diameter": 8,
                    "height": 20,
                    "position": {"x": 10, "y": 10, "z": -5},
                },
                {
                    "id": "alfa",
                    "type": "cylinder",
                    "diameter": 6,
                    "height": 20,
                    "position": {"x": 30, "y": 10, "z": -5},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["zulu", "alfa"],
                },
            ]
        )
        tools = serialize_part(self.part_from(doc))["features"][3]["tools"]
        self.assertEqual(tools, ["zulu", "alfa"])
        self.assertNotEqual(tools, sorted(tools))
        self.assertIsInstance(tools, list)

    def test_reversing_tool_order_changes_the_document(self) -> None:
        doc = document(
            [
                plate_feature(),
                {
                    "id": "a",
                    "type": "cylinder",
                    "diameter": 8,
                    "height": 20,
                    "position": {"x": 10, "y": 10, "z": -5},
                },
                {
                    "id": "b",
                    "type": "cylinder",
                    "diameter": 6,
                    "height": 20,
                    "position": {"x": 30, "y": 10, "z": -5},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["a", "b"],
                },
            ]
        )
        reversed_doc = json.loads(json.dumps(doc))
        reversed_doc["features"][3]["tools"] = ["b", "a"]
        self.assertNotEqual(self.canonical(doc), self.canonical(reversed_doc))


# --- 7: default materialisation --------------------------------------------


class TestDefaults(SerializationTestCase):
    """Policy: every optional parameter with a default is always emitted."""

    def test_an_omitted_box_position_is_materialised(self) -> None:
        doc = document(
            [{"id": "plate", "type": "box", "size": {"x": 100, "y": 60, "z": 10}}]
        )
        emitted = serialize_part(self.part_from(doc))["features"][0]
        self.assertIn("position", emitted)
        self.assertEqual(emitted["position"], {"x": 0.0, "y": 0.0, "z": 0.0})

    def test_an_omitted_cylinder_position_and_axis_are_materialised(self) -> None:
        doc = document(
            [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
            name="pin",
        )
        emitted = serialize_part(self.part_from(doc))["features"][0]
        self.assertEqual(emitted["position"], {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(emitted["axis"], "+Z")
        self.assertEqual(
            list(emitted.keys()),
            ["id", "type", "diameter", "height", "position", "axis"],
        )

    def test_an_omitted_through_hole_axis_is_materialised(self) -> None:
        doc = document(
            [
                plate_feature(),
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 8,
                    "position": {"x": 10, "y": 10, "z": 0},
                },
            ]
        )
        emitted = serialize_part(self.part_from(doc))["features"][1]
        self.assertEqual(emitted["axis"], "+Z")

    def test_the_terse_and_explicit_spellings_are_the_same_document(self) -> None:
        """The heart of the policy: one canonical form, two accepted inputs."""
        terse = document(
            [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
            name="pin",
        )
        explicit = document(
            [
                {
                    "id": "pin",
                    "type": "cylinder",
                    "diameter": 20,
                    "height": 50,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "axis": "+Z",
                }
            ],
            name="pin",
        )
        self.assertEqual(self.canonical(terse), self.canonical(explicit))
        self.assertEqual(
            part_hash(self.part_from(terse)), part_hash(self.part_from(explicit))
        )
        self.assertEqual(self.part_from(terse), self.part_from(explicit))

    def test_only_the_materialised_form_is_canonical(self) -> None:
        """The terse input is accepted, but is not what serialization emits."""
        terse = document(
            [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
            name="pin",
        )
        canonical = json.loads(self.canonical(terse))
        self.assertIn("position", canonical["features"][0])
        self.assertIn("axis", canonical["features"][0])
        self.assertNotEqual(canonical["features"][0], terse["features"][0])

    def test_a_non_default_value_survives(self) -> None:
        doc = document(
            [
                {
                    "id": "pin",
                    "type": "cylinder",
                    "diameter": 20,
                    "height": 50,
                    "position": {"x": 1, "y": 2, "z": 3},
                    "axis": "-Y",
                }
            ],
            name="pin",
        )
        emitted = serialize_part(self.part_from(doc))["features"][0]
        self.assertEqual(emitted["position"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(emitted["axis"], "-Y")

    def test_an_absent_description_is_omitted_not_nulled(self) -> None:
        """The validator rejects an explicit null, so null is never emitted."""
        part = self.part_from(document([plate_feature()]))
        self.assertIsNone(part.description)
        self.assertNotIn("description", serialize_part(part))
        self.assertNotIn("description", part_to_json(part))
        with_null = validate(document([plate_feature()], description=None))
        self.assertIn("S1", with_null.rule_codes())

    def test_a_present_description_is_emitted(self) -> None:
        part = self.part_from(document([plate_feature()], description="a plate"))
        self.assertEqual(serialize_part(part)["description"], "a plate")


# --- 8, 9, 10: numbers ------------------------------------------------------


class TestNumbers(SerializationTestCase):
    def test_lengths_are_json_numbers_not_strings(self) -> None:
        part = self.part_from(section_d_document())
        emitted = serialize_part(part)
        box = emitted["features"][0]
        for field in ("size", "position"):
            for component in VECTOR_FIELD_ORDER:
                with self.subTest(field=field, component=component):
                    self.assertIsInstance(box[field][component], float)
        self.assertIsInstance(emitted["features"][1]["diameter"], float)
        # ... and nothing numeric appears quoted in the text.
        self.assertNotIn('"100.0"', part_to_json(part))
        self.assertIn('"x":100.0', part_to_json(part))

    def test_integer_input_becomes_a_float_in_the_document(self) -> None:
        """The validator converts on the way in, so 100 comes back as 100.0."""
        part = self.part_from(
            document([plate_feature(size={"x": 100, "y": 60, "z": 10})])
        )
        self.assertEqual(part.features[0].size, Size(100.0, 60.0, 10.0))
        self.assertIn('"size":{"x":100.0,"y":60.0,"z":10.0}', part_to_json(part))

    def test_negative_zero_is_normalised_to_zero(self) -> None:
        """-0.0 == 0.0, so two equal parts must not serialize differently.

        Documented rule: the sign of zero is dropped. Rule S19 asks only that
        a component be finite, and -0.0 mm is 0.0 mm.
        """
        negative = document(
            [plate_feature(position={"x": -0.0, "y": -0.0, "z": -0.0})]
        )
        positive = document([plate_feature(position={"x": 0, "y": 0, "z": 0})])

        # The typed parts really are equal, and really do hold -0.0 ...
        negative_part = self.part_from(negative)
        self.assertEqual(
            repr(negative_part.features[0].position.x), "-0.0"
        )
        self.assertEqual(negative_part, self.part_from(positive))

        # ... and the canonical documents agree.
        self.assertEqual(self.canonical(negative), self.canonical(positive))
        self.assertNotIn("-0.0", self.canonical(negative))
        self.assertEqual(
            part_hash(negative_part), part_hash(self.part_from(positive))
        )

    def test_negative_zero_would_otherwise_have_differed(self) -> None:
        """Evidence the normalisation is doing something: json emits -0.0."""
        self.assertEqual(json.dumps(-0.0), "-0.0")
        self.assertEqual(json.dumps(0.0), "0.0")
        self.assertTrue(-0.0 == 0.0)

    def test_a_genuine_negative_value_keeps_its_sign(self) -> None:
        doc = document([plate_feature(position={"x": -10, "y": -0.5, "z": -1e-3})])
        emitted = serialize_part(self.part_from(doc))["features"][0]["position"]
        self.assertEqual(emitted, {"x": -10.0, "y": -0.5, "z": -0.001})
        self.assertIn('"x":-10.0', self.canonical(doc))

    def test_very_small_values_round_trip_exactly(self) -> None:
        for value in (1e-9, 1e-300, 5e-324, 2.220446049250313e-16):
            doc = document(
                [plate_feature(size={"x": value, "y": 60, "z": 10})],
                name="tiny",
            )
            with self.subTest(value=value):
                part = self.part_from(doc)
                self.assertEqual(part.features[0].size.x, value)
                rebuilt = part_from_json(part_to_json(part))
                self.assertEqual(rebuilt.features[0].size.x, value)
                self.assertEqual(part_to_json(rebuilt), part_to_json(part))

    def test_large_values_round_trip_exactly(self) -> None:
        for value in (1e16, 1e17, 1e308, 123456789012345.6):
            doc = document(
                [plate_feature(size={"x": value, "y": 60, "z": 10})],
                name="huge",
            )
            with self.subTest(value=value):
                part = self.part_from(doc)
                rebuilt = part_from_json(part_to_json(part))
                self.assertEqual(rebuilt.features[0].size.x, value)
                self.assertEqual(part_to_json(rebuilt), part_to_json(part))

    def test_awkward_binary_fractions_round_trip_exactly(self) -> None:
        for value in (0.1, 1.0 / 3.0, 0.30000000000000004, 2.675):
            doc = document(
                [plate_feature(size={"x": value, "y": 60, "z": 10})],
                name="fractions",
            )
            with self.subTest(value=value):
                rebuilt = part_from_json(self.canonical(doc))
                self.assertEqual(rebuilt.features[0].size.x, value)

    def test_non_finite_values_are_rejected_by_the_validator(self) -> None:
        """NaN and infinity never reach a canonical document (rule S19).

        ``json.loads`` accepts the bare ``NaN`` token, so this is checked at
        the validation boundary rather than assumed away.
        """
        self.assertTrue(json.loads('{"a": NaN}')["a"] != json.loads('{"a": NaN}')["a"])
        for literal in ("NaN", "Infinity", "-Infinity"):
            text = json.dumps(document([plate_feature()])).replace(
                '"x": 100', f'"x": {literal}'
            )
            text = text.replace('"x":100', f'"x":{literal}')
            with self.subTest(literal=literal):
                with self.assertRaises(DocumentValidationError) as caught:
                    part_from_json(text)
                self.assertIn("S19", caught.exception.rule_codes())


# --- 11-21: invalid documents ----------------------------------------------


class TestInvalidDocuments(SerializationTestCase):
    def assertRejected(self, doc: Any, rule: str) -> DocumentValidationError:
        with self.assertRaises(DocumentValidationError) as caught:
            deserialize_part(doc)
        self.assertIn(rule, caught.exception.rule_codes())
        return caught.exception

    def test_an_invalid_schema_version_is_rejected(self) -> None:
        for version in ("2.0.0", "0.9", "one", "", "1"):
            with self.subTest(version=version):
                self.assertRejected(
                    document([plate_feature()], schema_version=version), "S4"
                )

    def test_an_unknown_root_field_is_rejected(self) -> None:
        self.assertRejected(document([plate_feature()], author="someone"), "S3")

    def test_an_unknown_feature_field_is_rejected(self) -> None:
        self.assertRejected(
            document([plate_feature(colour="red")]), "S3"
        )

    def test_an_unknown_feature_type_is_rejected(self) -> None:
        self.assertRejected(
            document(
                [{"id": "loft", "type": "loft", "size": {"x": 1, "y": 1, "z": 1}}]
            ),
            "S3",
        )

    def test_an_invalid_reference_is_rejected(self) -> None:
        self.assertRejected(
            document(
                [
                    plate_feature(),
                    {
                        "id": "bore",
                        "type": "through_hole",
                        "target": "absent",
                        "diameter": 8,
                        "position": {"x": 10, "y": 10, "z": 0},
                    },
                ]
            ),
            "S6",
        )

    def test_a_forward_reference_is_rejected(self) -> None:
        self.assertRejected(
            document(
                [
                    {
                        "id": "bore",
                        "type": "through_hole",
                        "target": "plate",
                        "diameter": 8,
                        "position": {"x": 10, "y": 10, "z": 0},
                    },
                    plate_feature(),
                ]
            ),
            "S7",
        )

    def test_invalid_dimensions_are_rejected(self) -> None:
        for extent in (-1, 0):
            with self.subTest(extent=extent):
                self.assertRejected(
                    document([plate_feature(size={"x": extent, "y": 60, "z": 10})]),
                    "S10",
                )
        self.assertRejected(
            document(
                [
                    {
                        "id": "pin",
                        "type": "cylinder",
                        "diameter": 0,
                        "height": 50,
                    }
                ]
            ),
            "S11",
        )

    def test_an_invalid_axis_is_rejected(self) -> None:
        for axis in ("Z", "+W", "z", "", "+z"):
            with self.subTest(axis=axis):
                self.assertRejected(
                    document(
                        [
                            {
                                "id": "pin",
                                "type": "cylinder",
                                "diameter": 20,
                                "height": 50,
                                "axis": axis,
                            }
                        ]
                    ),
                    "S12",
                )

    def test_an_invalid_selector_is_rejected(self) -> None:
        for selector in (
            {"select": "axis_parallel"},
            {"select": "all", "axis": "X"},
            {"select": "axis_parallel", "axis": "+X"},
            {"select": "sideways"},
        ):
            with self.subTest(selector=selector):
                self.assertRejected(
                    document(
                        [
                            plate_feature(),
                            {
                                "id": "round",
                                "type": "fillet",
                                "target": "plate",
                                "radius": 2,
                                "edges": selector,
                            },
                        ]
                    ),
                    "S18",
                )

    def test_a_rejected_document_returns_no_part(self) -> None:
        error = self.assertRejected(document([plate_feature(colour="red")]), "S3")
        self.assertIsInstance(error.result, ValidationResult)
        self.assertFalse(error.result.valid)
        self.assertIsNone(error.result.part)

    def test_the_structured_errors_survive_the_raise(self) -> None:
        error = self.assertRejected(
            document([plate_feature(size={"x": -1, "y": 60, "z": 10})]), "S10"
        )
        self.assertGreater(len(error.errors), 0)
        first = error.errors[0]
        self.assertEqual(first.rule, "S10")
        self.assertIsNotNone(first.field_path)
        self.assertIn("S10", str(error))

    def test_every_violation_is_reported_not_just_the_first(self) -> None:
        doc = document(
            [plate_feature(size={"x": -1, "y": -2, "z": 10}, colour="red")],
            author="someone",
        )
        with self.assertRaises(DocumentValidationError) as caught:
            deserialize_part(doc)
        self.assertGreater(len(caught.exception.errors), 1)
        self.assertEqual(
            caught.exception.rule_codes(),
            validate(doc).rule_codes(),
            msg="deserialize_part must report exactly what validate() reports",
        )

    def test_nothing_is_repaired(self) -> None:
        """A near-miss document is refused, not corrected."""
        for doc in (
            document([plate_feature(size={"x": 100, "y": 60})]),
            document([plate_feature()], units="cm"),
            document([]),
        ):
            with self.subTest(doc=doc):
                with self.assertRaises(DocumentValidationError):
                    deserialize_part(doc)

    def test_invalid_json_syntax_is_a_parse_error(self) -> None:
        for text in ("{", "", "not json", '{"a": }', '{"a": 1,}', "[1, 2"):
            with self.subTest(text=text):
                with self.assertRaises(DocumentParseError) as caught:
                    part_from_json(text)
                self.assertIn("not valid JSON", str(caught.exception))

    def test_a_parse_error_is_not_a_validation_error(self) -> None:
        """A malformed file must never be reported as a rule violation."""
        with self.assertRaises(DocumentParseError):
            part_from_json("{")
        self.assertFalse(issubclass(DocumentParseError, DocumentValidationError))
        self.assertTrue(issubclass(DocumentParseError, CadDocumentError))
        self.assertTrue(issubclass(DocumentValidationError, CadDocumentError))

    def test_json_that_is_not_an_object_is_a_parse_error(self) -> None:
        for text in ("[]", "1", '"plate"', "null", "true"):
            with self.subTest(text=text):
                with self.assertRaises(DocumentParseError):
                    part_from_json(text)

    def test_invalid_utf8_bytes_are_a_parse_error(self) -> None:
        with self.assertRaises(DocumentParseError) as caught:
            part_from_json(b"\xff\xfe{}")
        self.assertIn("utf-8", str(caught.exception))

    def test_a_raw_dictionary_cannot_be_serialized(self) -> None:
        """serialize_part's input is the typed, validated boundary."""
        for candidate in (section_d_document(), None, "plate", 42, []):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(TypeError) as caught:
                    serialize_part(candidate)  # type: ignore[arg-type]
                self.assertIn("Part", str(caught.exception))

    def test_deserialization_requires_a_mapping(self) -> None:
        for candidate in (None, "plate", 42, [], ()):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(DocumentParseError):
                    deserialize_part(candidate)  # type: ignore[arg-type]

    def test_a_hand_built_part_is_not_validated_again(self) -> None:
        """Serialization trusts the typed boundary, as the engine does.

        A ``Part`` can only come from the validator on the normal path. One
        built by hand is serialized as given -- this records that, so nobody
        mistakes serialization for a second validator.
        """
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(Box(id="plate", size=Size(100.0, 60.0, 10.0)),),
        )
        text = part_to_json(part)
        self.assertIn('"name":"hand-built"', text)
        # ... and it still has to pass validation to come back.
        self.assertEqual(part_from_json(text), part)


# --- 22, 23, 24: hashing ----------------------------------------------------


class TestHashing(SerializationTestCase):
    def corpus(self) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
        return (
            ("plate", document([plate_feature()])),
            ("section-d", section_d_document()),
            ("every-feature", every_feature_document()),
            (
                "described",
                document([plate_feature()], description="a plate"),
            ),
            (
                "renamed",
                document([plate_feature()], name="other-plate"),
            ),
            (
                "resized",
                document([plate_feature(size={"x": 100, "y": 60, "z": 11})]),
            ),
            (
                "moved",
                document([plate_feature(position={"x": 1, "y": 0, "z": 0})]),
            ),
            (
                "cylinder",
                document(
                    [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
                    name="pin",
                ),
            ),
        )

    def test_the_same_part_always_hashes_the_same(self) -> None:
        for name, doc in self.corpus():
            with self.subTest(name=name):
                part = self.part_from(doc)
                self.assertEqual(len({part_hash(part) for _ in range(5)}), 1)
                self.assertEqual(part_hash(part), part_hash(self.part_from(doc)))

    def test_different_documents_hash_differently(self) -> None:
        digests = {name: part_hash(self.part_from(doc)) for name, doc in self.corpus()}
        self.assertEqual(len(set(digests.values())), len(digests))

    def test_the_hash_is_the_sha256_of_the_canonical_bytes(self) -> None:
        """Stated exactly, and checked against hashlib directly."""
        self.assertEqual(HASH_ALGORITHM, "sha256")
        for name, doc in self.corpus():
            part = self.part_from(doc)
            with self.subTest(name=name):
                self.assertEqual(
                    part_hash(part),
                    hashlib.sha256(part_to_json(part).encode("utf-8")).hexdigest(),
                )

    def test_the_hash_input_is_the_document_not_geometry(self) -> None:
        """Two documents that build the same solid still hash differently.

        A box at the origin written terse and a *different* box that happens
        to occupy the same space via a different feature history are not the
        same document. Nothing here builds geometry to compare.
        """
        one_box = document([plate_feature()])
        box_and_cut = document(
            [
                plate_feature(size={"x": 100, "y": 60, "z": 20}),
                {
                    "id": "trim",
                    "type": "cylinder",
                    "diameter": 500,
                    "height": 10,
                    "position": {"x": 0, "y": 0, "z": 10},
                },
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["trim"]},
            ]
        )
        self.assertNotEqual(
            part_hash(self.part_from(one_box)), part_hash(self.part_from(box_and_cut))
        )

    def test_the_hash_is_a_hex_sha256_digest(self) -> None:
        digest = part_hash(self.part_from(section_d_document()))
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in digest))

    #: Measured digest of this file's Section D fixture. Pinned so that a
    #: change to the canonical form has to be a deliberate one; it is a
    #: property of this fixture, not of the specification document.
    SECTION_D_HASH = (
        "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
    )

    def test_the_section_d_hash_is_pinned(self) -> None:
        """A change to the canonical form must be a deliberate one."""
        self.assertEqual(
            part_hash(self.part_from(section_d_document())), self.SECTION_D_HASH
        )
        self.assertEqual(
            self.SECTION_D_HASH,
            hashlib.sha256(
                part_to_json(self.part_from(section_d_document())).encode("utf-8")
            ).hexdigest(),
        )


# --- equivalence ------------------------------------------------------------


class TestEquivalence(SerializationTestCase):
    def test_equivalence_is_canonical_document_identity(self) -> None:
        doc = section_d_document()
        self.assertTrue(parts_equivalent(self.part_from(doc), self.part_from(doc)))

    def test_terse_and_explicit_defaults_are_equivalent(self) -> None:
        terse = self.part_from(
            document(
                [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
                name="pin",
            )
        )
        explicit = self.part_from(
            document(
                [
                    {
                        "id": "pin",
                        "type": "cylinder",
                        "diameter": 20,
                        "height": 50,
                        "position": {"x": 0, "y": 0, "z": 0},
                        "axis": "+Z",
                    }
                ],
                name="pin",
            )
        )
        self.assertTrue(parts_equivalent(terse, explicit))

    def test_a_different_name_makes_a_different_document(self) -> None:
        first = self.part_from(document([plate_feature()]))
        second = self.part_from(document([plate_feature()], name="other"))
        self.assertFalse(parts_equivalent(first, second))

    def test_a_different_feature_id_makes_a_different_document(self) -> None:
        first = self.part_from(document([plate_feature()]))
        second = self.part_from(document([plate_feature(id="panel")]))
        self.assertFalse(parts_equivalent(first, second))


# --- 25, 26, 27: no geometry anywhere ---------------------------------------


class TestNoGeometryDependency(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "src" / "cad_core"

    def imported_modules(self, filename: str) -> set:
        tree = ast.parse((self.ROOT / filename).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_the_serialization_module_imports_no_kernel(self) -> None:
        imported = self.imported_modules("serialization.py")
        for forbidden in ("cadquery", "OCP"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(name.split(".")[0] == forbidden for name in imported),
                    msg=f"serialization.py imports {forbidden}",
                )

    def test_the_serialization_module_imports_no_geometry_of_ours(self) -> None:
        forbidden = {
            "cad_core.local_cad",
            "cad_core.edge_selection",
            "cad_core.step_export",
            "cad_core.iges_export",
            "cad_core.stl_export",
            "cad_core.render_model",
            "cad_core.featurescript",
        }
        self.assertEqual(forbidden & self.imported_modules("serialization.py"), set())

    def test_importing_the_package_does_not_load_a_kernel(self) -> None:
        """``import cad_core`` must stay usable with CadQuery absent."""
        import subprocess

        code = (
            "import sys, cad_core, cad_core.serialization\n"
            "loaded = [m for m in sys.modules "
            "if m == 'cadquery' or m.split('.')[0] == 'OCP']\n"
            "assert not loaded, loaded\n"
            "print('clean')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={
                "PYTHONPATH": str(self.ROOT.parents[1] / "src"),
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("clean", completed.stdout)

    def test_serialization_works_with_the_kernel_blocked(self) -> None:
        """Serialize and deserialize in a process where importing CadQuery fails."""
        import subprocess

        code = (
            "import sys\n"
            "class Blocker:\n"
            "    def find_module(self, name, path=None):\n"
            "        if name == 'cadquery' or name.split('.')[0] == 'OCP':\n"
            "            raise ImportError('blocked: ' + name)\n"
            "        return None\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        return self.find_module(name, path)\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "from cad_core.serialization import (\n"
            "    part_to_json, part_from_json, part_hash)\n"
            "from cad_core import validate\n"
            "doc = {'schema_version': '1.0.0', 'units': 'mm', 'name': 'p',\n"
            "       'features': [{'id': 'plate', 'type': 'box',\n"
            "                     'size': {'x': 100, 'y': 60, 'z': 10}}]}\n"
            "part = validate(doc).part\n"
            "text = part_to_json(part)\n"
            "assert part_from_json(text) == part\n"
            "assert len(part_hash(part)) == 64\n"
            "print(text)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={
                "PYTHONPATH": str(self.ROOT.parents[1] / "src"),
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn('"schema_version":"1.0.0"', completed.stdout)

    def test_the_canonical_document_carries_no_kernel_content(self) -> None:
        """No geometry, mesh, export or generated-source vocabulary anywhere."""
        result = validate(every_feature_document())
        assert result.part is not None
        text = part_to_json(result.part)
        forbidden = (
            "cadquery",
            "OCP",
            "TopoDS",
            "BRep",
            "GeomAbs",
            "triangle",
            "vertices",
            "normals",
            "mesh",
            "STEP",
            "IGES",
            "STL",
            "featurescript",
            "solid",
            "shape",
        )
        lowered = text.lower()
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token.lower(), lowered)

    def test_the_document_field_names_are_exactly_the_specification_s(self) -> None:
        """Nothing invented: every key comes from the V1 field tables."""
        result = validate(every_feature_document())
        assert result.part is not None
        emitted = serialize_part(result.part)
        permitted_root = set(ROOT_FIELD_ORDER)
        self.assertTrue(set(emitted) <= permitted_root)
        for feature in emitted["features"]:
            required, optional = FEATURE_PARAMETERS[feature["type"]]
            permitted = set(COMMON_FEATURE_FIELDS + required + optional)
            with self.subTest(feature=feature["id"]):
                self.assertTrue(set(feature) <= permitted)

    def test_no_serialization_format_version_was_invented(self) -> None:
        """The only version in a document is the specification's own."""
        result = validate(section_d_document())
        assert result.part is not None
        emitted = serialize_part(result.part)
        versions = [key for key in emitted if "version" in key]
        self.assertEqual(versions, ["schema_version"])
        self.assertEqual(emitted["schema_version"], "1.0.0")


# --- file persistence -------------------------------------------------------


class TestPersistence(SerializationTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_save_then_load_round_trips(self) -> None:
        part = self.part_from(section_d_document())
        path = save_part(part, self.tmp / "plate.json")
        self.assertTrue(path.is_file())
        loaded = load_part(path)
        self.assertEqual(loaded, part)
        self.assertTrue(parts_equivalent(loaded, part))

    def test_the_file_holds_exactly_the_canonical_bytes(self) -> None:
        part = self.part_from(section_d_document())
        path = save_part(part, self.tmp / "plate.json")
        self.assertEqual(path.read_bytes(), part_to_bytes(part))
        self.assertFalse(path.read_bytes().endswith(b"\n"))

    def test_repeated_saves_are_byte_identical(self) -> None:
        part = self.part_from(every_feature_document())
        digests = set()
        for index in range(3):
            path = save_part(part, self.tmp / f"p{index}.json")
            digests.add(hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(digests), 1)
        self.assertEqual(next(iter(digests)), part_hash(part))

    def test_reserialization_after_a_load_is_byte_identical(self) -> None:
        part = self.part_from(every_feature_document())
        path = save_part(part, self.tmp / "p.json")
        self.assertEqual(part_to_bytes(load_part(path)), part_to_bytes(part))

    def test_an_existing_file_is_not_silently_overwritten(self) -> None:
        part = self.part_from(document([plate_feature()]))
        path = save_part(part, self.tmp / "p.json")
        with self.assertRaises(CadDocumentError) as caught:
            save_part(part, path)
        self.assertIn("already exists", str(caught.exception))

    def test_overwrite_is_explicit(self) -> None:
        first = self.part_from(document([plate_feature()]))
        second = self.part_from(document([plate_feature()], name="other"))
        path = save_part(first, self.tmp / "p.json")
        save_part(second, path, overwrite=True)
        self.assertEqual(load_part(path), second)

    def test_an_unsupported_extension_is_refused(self) -> None:
        part = self.part_from(document([plate_feature()]))
        for name in ("p.cad", "p.txt", "p", "p.step"):
            with self.subTest(name=name):
                with self.assertRaises(CadDocumentError) as caught:
                    save_part(part, self.tmp / name)
                self.assertIn("extension", str(caught.exception))
        self.assertEqual(DOCUMENT_EXTENSIONS, (".json",))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(CadDocumentError) as caught:
            load_part(self.tmp / "absent.json")
        self.assertIn("not a file", str(caught.exception))

    def test_an_invalid_document_on_disk_fails_safely(self) -> None:
        broken = self.tmp / "broken.json"
        broken.write_text('{"schema_version": "1.0.0"}', encoding="utf-8")
        with self.assertRaises(DocumentValidationError):
            load_part(broken)

        malformed = self.tmp / "malformed.json"
        malformed.write_text("{not json", encoding="utf-8")
        with self.assertRaises(DocumentParseError):
            load_part(malformed)

    def test_a_non_ascii_name_survives_the_file(self) -> None:
        """UTF-8, not escapes."""
        part = self.part_from(document([plate_feature()], name="plaque 100x60 café"))
        path = save_part(part, self.tmp / "cafe.json")
        raw = path.read_bytes()
        self.assertIn("café".encode("utf-8"), raw)
        self.assertNotIn(b"\\u", raw)
        self.assertEqual(load_part(path).name, "plaque 100x60 café")
        self.assertEqual(CANONICAL_ENCODING, "utf-8")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
