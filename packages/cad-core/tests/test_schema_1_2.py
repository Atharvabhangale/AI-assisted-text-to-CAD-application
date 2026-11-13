"""Schema version 1.2.0: taper, polygon and the tool pattern.

``1.2.0`` adds optional parameters and nothing else (specification Sections
C.2, C.4, C.8, rules S23-S28):

* ``cylinder.top_diameter`` -- a cone frustum, or a pointed cone at 0;
* ``cylinder.sides`` -- 0 for round, 3-12 for a regular polygon prism or
  pyramid frustum;
* ``pattern_count`` / ``pattern_axis`` / ``pattern_angle`` / ``pattern_step``
  on ``subtract`` and ``union`` -- every tool replaced by copies turned about
  and shifted along an axis through the origin.

Tested here, in this order: the contract constants and the specification
text; the static rules and their version gating; serialization, including
the byte-identity of every pre-1.2.0 document; and the engine, with
closed-form volumes compared under explicit tolerances, never exact float
equality.
"""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from typing import Any, Dict, List

import cadquery as cq

from cad_core import validate
from cad_core.edge_selection import SEAM, edge_kind, select_edges
from cad_core.local_cad import (
    GeometryOperationError,
    LocalCadResult,
    build_part,
)
from cad_core.model import (
    FEATURE_PARAMETERS,
    PARAMETER_INTRODUCED_IN,
    PATTERN_DEFAULTS,
    PATTERN_PARAMETERS,
    SCHEMA_VERSION,
    Cylinder,
    EdgeSelector,
    Part,
    Position,
    Subtract,
    feature_parameters,
)
from cad_core.rules import STATIC_RULE_CODES, STATIC_RULES
from cad_core.serialization import (
    part_from_json,
    part_hash,
    part_to_json,
    serialize_part,
)

from test_serialization import (
    every_feature_document,
    section_d_document,
    union_document,
)

SPECIFICATION_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "cad-specification.md"
)

#: Explicit tolerances. Volumes are compared relative to their size: the
#: kernel's polygonal and conical volumes agree with the closed forms to about
#: 1e-15 relative (measured), so 1e-9 is generous headroom, not a fudge.
RELATIVE_VOLUME_TOLERANCE = 1e-9
TOLERANCE_MM = 1e-6


def doc(features: List[Dict[str, Any]], version: str = "1.2.0") -> Dict[str, Any]:
    return {
        "schema_version": version,
        "units": "mm",
        "name": "schema-1-2",
        "features": features,
    }


def cylinder(**fields: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {"id": "c", "type": "cylinder", "diameter": 20, "height": 30}
    feature.update(fields)
    return feature


def plate_with_tool(kind: str = "subtract", **pattern: Any) -> Dict[str, Any]:
    """A plate and a small box tool, joined or cut with ``pattern``."""
    features: List[Dict[str, Any]] = [
        {"id": "plate", "type": "box", "size": {"x": 100, "y": 30, "z": 5}},
        {
            "id": "slot",
            "type": "box",
            "size": {"x": 5, "y": 20, "z": 10},
            "position": {"x": 0, "y": 5, "z": -2},
        },
        {"id": "op", "type": kind, "target": "plate", "tools": ["slot"]},
    ]
    features[2].update(pattern)
    return doc(features)


def regular_polygon_area(sides: int, circumradius: float) -> float:
    return sides / 2.0 * circumradius**2 * math.sin(2.0 * math.pi / sides)


def frustum_volume(bottom_area: float, top_area: float, height: float) -> float:
    return height / 3.0 * (bottom_area + top_area + math.sqrt(bottom_area * top_area))


#: Canonical hashes of four pre-1.2.0 documents, recorded with the code as it
#: was before 1.2.0 existed. 1.2.0 must not move a single byte of them.
LEGACY_HASHES = {
    "section_d_1_0_0": "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc",
    "union_1_1_0": "da69b09d96e0ef8ec3e7a18e2aa9a8e9f67a6f1916af11502acbea9613b6fe51",
    "every_feature_1_0_0": "bb4f5a09885fc8d6f01d7eadcbd73a201ac210fbf7690bab365fba3386554ea1",
    "hub_1_1_1": "6e4f208b4d66f30b301b3672c520281d0ea121c1ca8fa6f9f29701f2f31ee02c",
}

#: The canonical text of the 1.1.1 hub, recorded before 1.2.0.
HUB_1_1_1_CANONICAL = (
    '{"schema_version":"1.1.1","units":"mm","name":"hub","features":['
    '{"id":"hub","type":"cylinder","diameter":40.0,"height":10.0,'
    '"position":{"x":0.0,"y":0.0,"z":0.0},"axis":"+Z"},'
    '{"id":"boss","type":"cylinder","diameter":10.0,"height":20.0,'
    '"position":{"x":0.0,"y":0.0,"z":5.0},"axis":"+Z"},'
    '{"id":"join","type":"union","target":"hub","tools":["boss"]},'
    '{"id":"bolt","type":"cylinder","diameter":4.0,"height":30.0,'
    '"position":{"x":14.0,"y":0.0,"z":-5.0},"axis":"+Z"},'
    '{"id":"cut","type":"subtract","target":"hub","tools":["bolt"]}]}'
)


def hub_document(version: str = "1.1.1") -> Dict[str, Any]:
    """A 1.1.1 document with a cylinder, a union and a subtract."""
    return doc(
        [
            {"id": "hub", "type": "cylinder", "diameter": 40, "height": 10},
            {
                "id": "boss",
                "type": "cylinder",
                "diameter": 10,
                "height": 20,
                "position": {"x": 0, "y": 0, "z": 5},
                "axis": "+Z",
            },
            {"id": "join", "type": "union", "target": "hub", "tools": ["boss"]},
            {
                "id": "bolt",
                "type": "cylinder",
                "diameter": 4,
                "height": 30,
                "position": {"x": 14, "y": 0, "z": -5},
            },
            {"id": "cut", "type": "subtract", "target": "hub", "tools": ["bolt"]},
        ],
        version=version,
    ) | {"name": "hub"}


class SchemaTestCase(unittest.TestCase):
    def part(self, document: Dict[str, Any]) -> Part:
        result = validate(document)
        self.assertTrue(result.valid, msg=[str(error) for error in result.errors])
        assert result.part is not None
        return result.part

    def build(self, document: Dict[str, Any]) -> LocalCadResult:
        return build_part(self.part(document))

    def build_error(self, document: Dict[str, Any]) -> str:
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(self.part(document))
        return str(caught.exception)

    def codes(self, document: Dict[str, Any]) -> List[str]:
        return list(validate(document).rule_codes())

    def assertRuleAt(self, document: Dict[str, Any], rule: str, path: str) -> str:
        result = validate(document)
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
        return matching[0].message

    def assertVolume(self, result: LocalCadResult, expected: float) -> None:
        self.assertAlmostEqual(
            result.volume(), expected, delta=abs(expected) * RELATIVE_VOLUME_TOLERANCE
        )

    def assertInside(self, result: LocalCadResult, point: tuple, inside: bool) -> None:
        self.assertEqual(
            result.shape.isInside(cq.Vector(*point)),
            inside,
            msg=f"point {point} should be {'inside' if inside else 'outside'}",
        )


# --- the contract ------------------------------------------------------------


class TestContract(SchemaTestCase):
    def test_the_schema_version_is_1_2_0(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.2.0")

    def test_the_new_parameters_are_optional_and_appended(self) -> None:
        self.assertEqual(
            FEATURE_PARAMETERS["cylinder"],
            (("diameter", "height"), ("position", "axis", "top_diameter", "sides")),
        )
        for kind in ("subtract", "union"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    FEATURE_PARAMETERS[kind], (("target", "tools"), PATTERN_PARAMETERS)
                )
        self.assertEqual(
            PATTERN_PARAMETERS,
            ("pattern_count", "pattern_axis", "pattern_angle", "pattern_step"),
        )
        self.assertEqual(
            dict(PATTERN_DEFAULTS),
            {"pattern_count": 1, "pattern_axis": "Z", "pattern_angle": 0.0, "pattern_step": 0.0},
        )

    def test_every_new_parameter_is_introduced_in_1_2(self) -> None:
        self.assertEqual(set(PARAMETER_INTRODUCED_IN.values()), {(1, 2)})
        self.assertEqual(
            set(PARAMETER_INTRODUCED_IN),
            {("cylinder", "top_diameter"), ("cylinder", "sides")}
            | {(kind, name) for kind in ("subtract", "union") for name in PATTERN_PARAMETERS},
        )

    def test_a_declared_version_selects_its_parameter_table(self) -> None:
        self.assertEqual(
            feature_parameters("cylinder", (1, 1)),
            (("diameter", "height"), ("position", "axis")),
        )
        self.assertEqual(feature_parameters("union", (1, 1)), (("target", "tools"), ()))
        self.assertEqual(feature_parameters("subtract", (1, 0)), (("target", "tools"), ()))
        self.assertEqual(feature_parameters("subtract", (1, 2)), FEATURE_PARAMETERS["subtract"])

    def test_six_static_rules_were_appended(self) -> None:
        self.assertEqual(STATIC_RULE_CODES, tuple(f"S{n}" for n in range(1, 29)))
        self.assertIn("top_diameter", STATIC_RULES["S23"])
        self.assertIn("sides", STATIC_RULES["S24"])
        self.assertIn("pattern_count", STATIC_RULES["S25"])
        self.assertIn("pattern_axis", STATIC_RULES["S26"])
        self.assertIn("pattern_angle", STATIC_RULES["S27"])
        self.assertIn("pattern_step", STATIC_RULES["S27"])
        self.assertIn("coincide", STATIC_RULES["S28"])

    def test_the_specification_document_defines_1_2_0(self) -> None:
        text = SPECIFICATION_PATH.read_text(encoding="utf-8")
        for required in (
            "Schema version described by this document: **`1.2.0`**",
            "defines version **`1.2.0`**",
            "| `top_diameter` | length | no | equal to `diameter` | length |",
            "| `sides` | integer | no | `0` | — |",
            "| `pattern_count` | integer | no | `1` | — |",
            "| `pattern_angle` | number | no | `0` | degrees |",
            "| `pattern_step` | length | no | `0` | length |",
            "| **S23** |",
            "| **S24** |",
            "| **S25** |",
            "| **S26** |",
            "| **S27** |",
            "| **S28** |",
            "| `1.2.0` |",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        # Each pattern table appears twice: subtract (C.4) and union (C.8).
        self.assertEqual(text.count("| `pattern_count` | integer | no | `1` | — |"), 2)
        # 'patterns' is no longer flatly out of scope; arbitrary ones are.
        self.assertIn("arbitrary patterns of features", text)
        self.assertNotIn("threads, patterns, GD&T", text)
        self.assertNotIn("there is no rotation in V1", text)

    def test_the_headings_the_ai_layer_quotes_are_unchanged(self) -> None:
        text = SPECIFICATION_PATH.read_text(encoding="utf-8")
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
            "### C.8 `union`",
            "### E.1 Static rules (`S`) — decidable from the document alone",
            "### E.2 Geometric rules (`E`) — require evaluation by the engine",
            "## F. Versioning",
        ):
            with self.subTest(heading=heading):
                self.assertIn("\n" + heading + "\n", text)


# --- static rules ------------------------------------------------------------


class TestCylinderRules(SchemaTestCase):
    def test_defaults_are_materialised(self) -> None:
        part = self.part(doc([cylinder()]))
        feature = part.features[0]
        assert isinstance(feature, Cylinder)
        self.assertEqual(feature.top_diameter, 20.0)
        self.assertEqual(feature.sides, 0)

    def test_a_taper_and_a_cone_are_valid(self) -> None:
        for top in (10, 35.5, 0, 0.0):
            with self.subTest(top=top):
                feature = self.part(doc([cylinder(top_diameter=top)])).features[0]
                self.assertEqual(feature.top_diameter, float(top))

    def test_sides_accepts_0_and_3_to_12(self) -> None:
        for sides in (0, 3, 4, 6, 12):
            with self.subTest(sides=sides):
                feature = self.part(doc([cylinder(sides=sides)])).features[0]
                self.assertEqual(feature.sides, sides)
                self.assertIs(type(feature.sides), int)

    def test_an_integral_float_is_the_same_integer(self) -> None:
        feature = self.part(doc([cylinder(sides=6.0)])).features[0]
        self.assertEqual(feature.sides, 6)
        self.assertIs(type(feature.sides), int)

    def test_sides_outside_the_range_is_s24(self) -> None:
        for sides in (1, 2, 13, -3, 64):
            with self.subTest(sides=sides):
                self.assertRuleAt(doc([cylinder(sides=sides)]), "S24", "features[0].sides")
                self.assertEqual(self.codes(doc([cylinder(sides=sides)])), ["S24"])

    def test_a_non_integer_sides_is_s24(self) -> None:
        for sides in (6.5, True, "6", None, [6], float("inf"), float("nan")):
            with self.subTest(sides=sides):
                self.assertEqual(self.codes(doc([cylinder(sides=sides)])), ["S24"])

    def test_a_negative_or_non_numeric_top_diameter_is_s23(self) -> None:
        for top in (-1, -0.001, "10", None, True, float("inf"), float("nan")):
            with self.subTest(top=top):
                self.assertRuleAt(
                    doc([cylinder(top_diameter=top)]), "S23", "features[0].top_diameter"
                )
                # S23 owns the parameter entirely: never a second S20.
                self.assertEqual(self.codes(doc([cylinder(top_diameter=top)])), ["S23"])

    def test_a_pointed_pyramid_is_s23(self) -> None:
        message = self.assertRuleAt(
            doc([cylinder(top_diameter=0, sides=6)]), "S23", "features[0].top_diameter"
        )
        self.assertIn("sides", message)

    def test_a_pointed_polygon_with_invalid_sides_reports_only_s24(self) -> None:
        self.assertEqual(self.codes(doc([cylinder(top_diameter=0, sides=13)])), ["S24"])

    def test_a_tapered_polygon_is_valid(self) -> None:
        feature = self.part(doc([cylinder(top_diameter=8, sides=6)])).features[0]
        self.assertEqual((feature.top_diameter, feature.sides), (8.0, 6))


class TestPatternRules(SchemaTestCase):
    KINDS = ("subtract", "union")

    def test_defaults_are_materialised(self) -> None:
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                feature = self.part(plate_with_tool(kind)).features[2]
                self.assertEqual(
                    (feature.pattern_count, feature.pattern_axis,
                     feature.pattern_angle, feature.pattern_step),
                    (1, "Z", 0.0, 0.0),
                )
                self.assertFalse(feature.is_patterned)

    def test_valid_patterns(self) -> None:
        cases = (
            dict(pattern_count=6, pattern_angle=60),
            dict(pattern_count=1, pattern_angle=30),
            dict(pattern_count=4, pattern_axis="X", pattern_step=20),
            dict(pattern_count=4, pattern_axis="X", pattern_step=-20),
            dict(pattern_count=64, pattern_angle=360 / 64),
            dict(pattern_count=7, pattern_angle=360 / 7),
            dict(pattern_count=3, pattern_angle=240),  # 240, 480, 720: distinct
            dict(pattern_count=4, pattern_angle=90, pattern_step=10),  # a stair
            dict(pattern_count=2, pattern_angle=360, pattern_step=10),
            dict(pattern_count=1, pattern_angle=-360),
            dict(pattern_count=3.0, pattern_axis="Y", pattern_angle=-120.0),
        )
        for kind in self.KINDS:
            for pattern in cases:
                with self.subTest(kind=kind, pattern=pattern):
                    feature = self.part(plate_with_tool(kind, **pattern)).features[2]
                    self.assertEqual(feature.pattern_count, int(pattern["pattern_count"]))
                    self.assertIs(type(feature.pattern_count), int)

    def test_pattern_count_out_of_range_or_not_an_integer_is_s25(self) -> None:
        for count in (0, -1, 65, 2.5, "3", True, None):
            for kind in self.KINDS:
                with self.subTest(kind=kind, count=count):
                    document = plate_with_tool(kind, pattern_count=count, pattern_angle=10)
                    self.assertRuleAt(document, "S25", "features[2].pattern_count")
                    self.assertEqual(self.codes(document), ["S25"])

    def test_a_bad_pattern_axis_is_s26(self) -> None:
        for axis in ("+Z", "z", "W", 2, None):
            for kind in self.KINDS:
                with self.subTest(kind=kind, axis=axis):
                    document = plate_with_tool(kind, pattern_axis=axis)
                    self.assertRuleAt(document, "S26", "features[2].pattern_axis")
                    self.assertEqual(self.codes(document), ["S26"])

    def test_a_bad_angle_or_step_is_s27(self) -> None:
        cases = (
            ("pattern_angle", 360.5),
            ("pattern_angle", -361),
            ("pattern_angle", float("inf")),
            ("pattern_angle", "30"),
            ("pattern_angle", None),
            ("pattern_step", float("nan")),
            ("pattern_step", "5"),
            ("pattern_step", False),
        )
        for kind in self.KINDS:
            for field, value in cases:
                with self.subTest(kind=kind, field=field, value=value):
                    document = plate_with_tool(kind, **{field: value})
                    self.assertRuleAt(document, "S27", f"features[2].{field}")
                    self.assertEqual(self.codes(document), ["S27"])

    def test_coincident_copies_are_s28(self) -> None:
        cases = (
            dict(pattern_count=2),
            dict(pattern_count=4, pattern_angle=0, pattern_step=0),
            dict(pattern_count=2, pattern_angle=360),
            dict(pattern_count=2, pattern_angle=-360),
            dict(pattern_count=4, pattern_angle=180),  # copies 1 and 3
            dict(pattern_count=6, pattern_angle=120),  # copies 1 and 4
            dict(pattern_count=9, pattern_angle=45 * 1.0),  # copies 1 and 9
        )
        for kind in self.KINDS:
            for pattern in cases:
                with self.subTest(kind=kind, pattern=pattern):
                    document = plate_with_tool(kind, **pattern)
                    self.assertRuleAt(document, "S28", "features[2].pattern_count")
                    self.assertEqual(self.codes(document), ["S28"])

    def test_s28_is_not_derived_from_an_invalid_value(self) -> None:
        document = plate_with_tool("union", pattern_count=4, pattern_angle="x")
        self.assertEqual(self.codes(document), ["S27"])
        document = plate_with_tool("union", pattern_count=99)
        self.assertEqual(self.codes(document), ["S25"])

    def test_a_negative_zero_angle_is_zero(self) -> None:
        feature = self.part(plate_with_tool("union", pattern_angle=-0.0)).features[2]
        self.assertEqual(math.copysign(1.0, feature.pattern_angle), 1.0)


class TestVersionGating(SchemaTestCase):
    """A 1.2.0 parameter in an earlier document is an unknown field (S3)."""

    def test_cylinder_parameters_in_earlier_documents_are_s3(self) -> None:
        for version in ("1.0.0", "1.1.0", "1.1.1"):
            for field, value in (("top_diameter", 10), ("sides", 6)):
                with self.subTest(version=version, field=field):
                    document = doc([cylinder(**{field: value})], version=version)
                    message = self.assertRuleAt(document, "S3", f"features[0].{field}")
                    self.assertEqual(self.codes(document), ["S3"])
                    self.assertIn("1.2.0", message)

    def test_pattern_parameters_in_earlier_documents_are_s3(self) -> None:
        for version in ("1.1.0", "1.1.1"):
            for kind in ("subtract", "union"):
                for field, value in (
                    ("pattern_count", 1),
                    ("pattern_axis", "Z"),
                    ("pattern_angle", 0),
                    ("pattern_step", 0),
                ):
                    with self.subTest(version=version, kind=kind, field=field):
                        document = plate_with_tool(kind, **{field: value})
                        document["schema_version"] = version
                        self.assertRuleAt(document, "S3", f"features[2].{field}")
                        self.assertEqual(self.codes(document), ["S3"])

    def test_the_value_of_an_unknown_field_is_not_examined(self) -> None:
        """Only S3: no derived S24 or S28 from a parameter the version lacks."""
        document = doc([cylinder(sides="hexagon", top_diameter=-1)], version="1.1.1")
        self.assertEqual(self.codes(document), ["S3", "S3"])
        document = plate_with_tool("subtract", pattern_count=4)
        document["schema_version"] = "1.1.1"
        self.assertEqual(self.codes(document), ["S3"])

    def test_an_earlier_document_gets_the_defaults(self) -> None:
        part = self.part(hub_document())
        hub, _, join, bolt, cut = part.features
        self.assertEqual((hub.top_diameter, hub.sides), (40.0, 0))
        for feature in (join, cut):
            self.assertFalse(feature.is_patterned)

    def test_an_unknown_parameter_name_is_still_a_plain_unknown_field(self) -> None:
        message = self.assertRuleAt(
            doc([cylinder(corners=6)]), "S3", "features[0].corners"
        )
        self.assertNotIn("1.2.0", message)


# --- serialization -----------------------------------------------------------


class TestSerialization(SchemaTestCase):
    def test_pre_1_2_documents_keep_their_bytes_and_hashes(self) -> None:
        documents = {
            "section_d_1_0_0": section_d_document(),
            "union_1_1_0": union_document(),
            "every_feature_1_0_0": every_feature_document(),
            "hub_1_1_1": hub_document(),
        }
        for name, document in documents.items():
            with self.subTest(document=name):
                self.assertEqual(part_hash(self.part(document)), LEGACY_HASHES[name])
        self.assertEqual(part_to_json(self.part(hub_document())), HUB_1_1_1_CANONICAL)

    def test_a_pre_1_2_canonical_document_never_mentions_the_new_parameters(self) -> None:
        text = part_to_json(self.part(hub_document()))
        for name in ("top_diameter", "sides") + PATTERN_PARAMETERS:
            self.assertNotIn(name, text)

    def test_a_1_2_document_materialises_every_default_in_table_order(self) -> None:
        emitted = serialize_part(self.part(hub_document("1.2.0")))
        hub, _, join, _, cut = emitted["features"]
        self.assertEqual(
            list(hub),
            ["id", "type", "diameter", "height", "position", "axis", "top_diameter", "sides"],
        )
        self.assertEqual(hub["top_diameter"], 40.0)
        self.assertEqual(hub["sides"], 0)
        for feature in (join, cut):
            self.assertEqual(
                list(feature)[4:], list(PATTERN_PARAMETERS)
            )
            self.assertEqual(
                {name: feature[name] for name in PATTERN_PARAMETERS},
                {"pattern_count": 1, "pattern_axis": "Z", "pattern_angle": 0.0, "pattern_step": 0.0},
            )

    def test_integers_are_emitted_as_integers(self) -> None:
        text = part_to_json(
            self.part(doc([cylinder(sides=6.0, top_diameter=12)]))
        )
        self.assertIn('"sides":6}', text)
        self.assertIn('"top_diameter":12.0', text)
        text = part_to_json(self.part(plate_with_tool("union", pattern_count=4.0, pattern_step=7)))
        self.assertIn('"pattern_count":4,', text)
        self.assertIn('"pattern_step":7.0', text)

    def test_terse_and_explicit_spellings_are_one_document(self) -> None:
        terse = self.part(doc([cylinder()]))
        explicit = self.part(doc([cylinder(top_diameter=20.0, sides=0)]))
        self.assertEqual(part_hash(terse), part_hash(explicit))
        terse = self.part(plate_with_tool("subtract"))
        explicit = self.part(
            plate_with_tool(
                "subtract", pattern_count=1, pattern_axis="Z", pattern_angle=0, pattern_step=-0.0
            )
        )
        self.assertEqual(part_hash(terse), part_hash(explicit))

    def test_a_1_2_document_round_trips(self) -> None:
        part = self.part(
            doc(
                [
                    cylinder(sides=6, top_diameter=10, axis="-X"),
                    cylinder(id="t", diameter=2, height=50, position={"x": -40, "y": 3, "z": 0}),
                    {
                        "id": "cut",
                        "type": "subtract",
                        "target": "c",
                        "tools": ["t"],
                        "pattern_count": 3,
                        "pattern_axis": "X",
                        "pattern_angle": 120,
                    },
                ]
            )
        )
        again = part_from_json(part_to_json(part))
        self.assertEqual(part_to_json(again), part_to_json(part))
        self.assertEqual(again, part)

    def test_the_declared_version_is_part_of_the_content(self) -> None:
        self.assertNotEqual(
            part_hash(self.part(hub_document("1.1.1"))),
            part_hash(self.part(hub_document("1.2.0"))),
        )

    def test_a_typed_part_that_its_version_cannot_carry_is_refused(self) -> None:
        part = self.part(hub_document())
        hub = part.features[0]
        changed = Part(
            schema_version="1.1.1",
            units="mm",
            name="hub",
            features=(
                Cylinder(id=hub.id, diameter=hub.diameter, height=hub.height, sides=6),
            )
            + part.features[1:],
        )
        with self.assertRaises(ValueError):
            part_to_json(changed)
        changed = Part(
            schema_version="1.1.1",
            units="mm",
            name="t",
            features=(
                part.features[0],
                Subtract(id="s", target="hub", tools=("x",), pattern_count=3, pattern_angle=10.0),
            ),
        )
        with self.assertRaises(ValueError):
            part_to_json(changed)


# --- the engine: taper and polygon -----------------------------------------


class TestTaperAndPolygon(SchemaTestCase):
    def test_a_cone_frustum(self) -> None:
        result = self.build(doc([cylinder(diameter=20, top_diameter=10, height=30)]))
        self.assertVolume(result, math.pi * 30 / 3 * (10**2 + 5**2 + 10 * 5))
        box = result.bounding_box()
        self.assertAlmostEqual(box.minimum.z, 0.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.maximum.z, 30.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.maximum.x, 10.0, delta=TOLERANCE_MM)

    def test_a_widening_frustum(self) -> None:
        result = self.build(doc([cylinder(diameter=10, top_diameter=30, height=12)]))
        self.assertVolume(result, math.pi * 12 / 3 * (5**2 + 15**2 + 5 * 15))

    def test_a_pointed_cone_on_each_axis(self) -> None:
        expected = math.pi * 10**2 * 30 / 3
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=axis):
                result = self.build(
                    doc([cylinder(top_diameter=0, axis=axis, position={"x": 1, "y": 2, "z": 3})])
                )
                self.assertVolume(result, expected)
                # The apex is 30 along the axis from the base centre.
                index = "XYZ".index(axis[1])
                sign = 1.0 if axis[0] == "+" else -1.0
                apex = [1.0, 2.0, 3.0]
                apex[index] += sign * 30.0
                box = result.bounding_box()
                far = (box.maximum if sign > 0 else box.minimum)
                self.assertAlmostEqual((far.x, far.y, far.z)[index], apex[index], delta=TOLERANCE_MM)

    def test_a_hexagonal_prism(self) -> None:
        """Area = 3*sqrt(3)/2 * R^2, corners on +-X, flats facing +-Y."""
        result = self.build(doc([cylinder(diameter=20, height=30, sides=6)]))
        self.assertVolume(result, 3 * math.sqrt(3) / 2 * 10**2 * 30)
        box = result.bounding_box()
        self.assertAlmostEqual(box.maximum.x, 10.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.minimum.x, -10.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.maximum.y, 10 * math.sqrt(3) / 2, delta=TOLERANCE_MM)
        self.assertEqual(len(result.shape.Faces()), 8)
        self.assertTrue(all(face.geomType() == "PLANE" for face in result.shape.Faces()))

    def test_every_polygon_has_its_closed_form_volume(self) -> None:
        for sides in range(3, 13):
            with self.subTest(sides=sides):
                result = self.build(doc([cylinder(diameter=16, height=7, sides=sides)]))
                self.assertVolume(result, regular_polygon_area(sides, 8.0) * 7)
                self.assertEqual(len(result.shape.Faces()), sides + 2)

    def test_a_tapered_hexagon(self) -> None:
        result = self.build(doc([cylinder(diameter=20, top_diameter=10, height=30, sides=6)]))
        bottom = 3 * math.sqrt(3) / 2 * 10**2
        top = 3 * math.sqrt(3) / 2 * 5**2
        self.assertVolume(result, frustum_volume(bottom, top, 30))
        # A pyramid frustum: every face planar, not a ruled B-spline.
        self.assertTrue(all(face.geomType() == "PLANE" for face in result.shape.Faces()))

    def test_polygon_orientation_follows_the_first_perpendicular(self) -> None:
        """One corner along +X for Z, along +Y for X, along +Z for Y."""
        cases = {
            "+Z": (0, 1.0),  # corner on +X
            "-Z": (0, 1.0),
            "+X": (1, 1.0),  # corner on +Y
            "-X": (1, 1.0),
            "+Y": (2, 1.0),  # corner on +Z
            "-Y": (2, 1.0),
        }
        for axis, (index, _) in cases.items():
            with self.subTest(axis=axis):
                # A pentagon is not symmetric under a half turn, so the corner
                # direction is observable from the bounding box: R on the
                # corner side, R*cos(36 deg) on the other.
                result = self.build(doc([cylinder(diameter=20, height=5, sides=5, axis=axis)]))
                box = result.bounding_box()
                high = (box.maximum.x, box.maximum.y, box.maximum.z)[index]
                low = (box.minimum.x, box.minimum.y, box.minimum.z)[index]
                self.assertAlmostEqual(high, 10.0, delta=TOLERANCE_MM)
                self.assertAlmostEqual(low, -10.0 * math.cos(math.pi / 5), delta=TOLERANCE_MM)

    def test_a_polygon_at_a_position_on_a_negative_axis(self) -> None:
        result = self.build(
            doc([cylinder(diameter=20, height=30, sides=6, axis="-Y", position={"x": 1, "y": 2, "z": 3})])
        )
        box = result.bounding_box()
        self.assertAlmostEqual(box.maximum.y, 2.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.minimum.y, -28.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.maximum.z, 13.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.minimum.z, -7.0, delta=TOLERANCE_MM)
        self.assertVolume(result, 3 * math.sqrt(3) / 2 * 100 * 30)

    def test_a_hex_nut_is_a_polygon_with_a_hole(self) -> None:
        result = self.build(
            doc(
                [
                    cylinder(id="nut", diameter=20, height=8, sides=6),
                    {
                        "id": "bore",
                        "type": "through_hole",
                        "target": "nut",
                        "diameter": 10,
                        "position": {"x": 0, "y": 0, "z": 0},
                    },
                ]
            )
        )
        self.assertVolume(result, (3 * math.sqrt(3) / 2 * 100 - math.pi * 25) * 8)

    def test_an_untapered_round_cylinder_is_built_as_before(self) -> None:
        """Defaults take the 1.1.1 path: the same B-rep as an old document."""
        old = self.build(doc([cylinder()], version="1.1.1")).shape
        new = self.build(doc([cylinder(top_diameter=20, sides=0)])).shape
        self.assertAlmostEqual(old.Volume(), new.Volume(), delta=1e-12)
        self.assertEqual(
            [face.geomType() for face in old.Faces()],
            [face.geomType() for face in new.Faces()],
        )
        self.assertEqual(len(old.Edges()), len(new.Edges()))


class TestEdgesOfNewSolids(SchemaTestCase):
    def test_axis_parallel_z_picks_the_vertical_corners_of_a_prism(self) -> None:
        shape = self.build(doc([cylinder(diameter=20, height=30, sides=6)])).shape
        vertical = select_edges(shape, EdgeSelector("axis_parallel", "Z"))
        self.assertEqual(len(vertical), 6)
        for edge in vertical:
            start, end = edge.startPoint(), edge.endPoint()
            self.assertAlmostEqual(abs(end.z - start.z), 30.0, delta=TOLERANCE_MM)
            self.assertAlmostEqual(math.hypot(start.x, start.y), 10.0, delta=TOLERANCE_MM)
        self.assertEqual(len(select_edges(shape, EdgeSelector("all"))), 18)

    def test_a_fillet_on_the_vertical_corners_builds(self) -> None:
        sides, circumradius, height, radius = 6, 10.0, 30.0, 1.5
        result = self.build(
            doc(
                [
                    cylinder(diameter=2 * circumradius, height=height, sides=sides),
                    {
                        "id": "round",
                        "type": "fillet",
                        "target": "c",
                        "radius": radius,
                        "edges": {"select": "axis_parallel", "axis": "Z"},
                    },
                ]
            )
        )
        interior = math.pi * (sides - 2) / sides
        # Each rounded corner removes r^2 * (cot(a/2) - (pi - a)/2).
        removed = sides * radius**2 * (1 / math.tan(interior / 2) - (math.pi - interior) / 2)
        self.assertVolume(result, (regular_polygon_area(sides, circumradius) - removed) * height)
        self.assertEqual(result.solid_count(), 1)

    def test_a_tapered_polygon_has_no_vertical_edges(self) -> None:
        document = doc(
            [
                cylinder(diameter=20, top_diameter=10, height=30, sides=6),
                {
                    "id": "round",
                    "type": "fillet",
                    "target": "c",
                    "radius": 1,
                    "edges": {"select": "axis_parallel", "axis": "Z"},
                },
            ]
        )
        self.assertIn("E4", self.build_error(document))

    def test_a_cone_never_selects_its_seam_or_apex(self) -> None:
        shape = self.build(doc([cylinder(top_diameter=0)])).shape
        kinds = [edge_kind(shape, edge) for edge in shape.Edges()]
        self.assertIn(SEAM, kinds)
        selected = select_edges(shape, EdgeSelector("all"))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].geomType(), "CIRCLE")
        self.assertAlmostEqual(selected[0].radius(), 10.0, delta=TOLERANCE_MM)
        for axis in ("X", "Y", "Z"):
            self.assertEqual(select_edges(shape, EdgeSelector("axis_parallel", axis)), ())

    def test_a_frustum_selects_its_two_rims_and_rounds_them(self) -> None:
        shape = self.build(doc([cylinder(top_diameter=10)])).shape
        selected = select_edges(shape, EdgeSelector("all"))
        self.assertEqual(sorted(round(edge.radius(), 6) for edge in selected), [5.0, 10.0])
        result = self.build(
            doc(
                [
                    cylinder(top_diameter=10),
                    {"id": "r", "type": "fillet", "target": "c", "radius": 1, "edges": {"select": "all"}},
                ]
            )
        )
        self.assertEqual(result.solid_count(), 1)
        self.assertLess(result.volume(), math.pi * 30 / 3 * (100 + 25 + 50))


# --- the engine: patterns ----------------------------------------------------


class TestPatterns(SchemaTestCase):
    def test_a_six_hole_bolt_circle(self) -> None:
        result = self.build(
            doc(
                [
                    cylinder(id="disk", diameter=100, height=10),
                    cylinder(id="bolt", diameter=8, height=20, position={"x": 40, "y": 0, "z": -5}),
                    {
                        "id": "holes",
                        "type": "subtract",
                        "target": "disk",
                        "tools": ["bolt"],
                        "pattern_count": 6,
                        "pattern_angle": 60,
                    },
                ]
            )
        )
        self.assertVolume(result, math.pi * 50**2 * 10 - 6 * math.pi * 4**2 * 10)
        self.assertEqual(result.solid_count(), 1)
        for k in range(6):
            theta = math.radians(60 * k)
            with self.subTest(hole=k):
                self.assertInside(result, (40 * math.cos(theta), 40 * math.sin(theta), 5), False)
            mid = math.radians(60 * k + 30)
            self.assertInside(result, (40 * math.cos(mid), 40 * math.sin(mid), 5), True)
        cylindrical = [face for face in result.shape.Faces() if face.geomType() == "CYLINDER"]
        self.assertEqual(len(cylindrical), 7)  # the rim and six bores

    def test_six_arms_about_z(self) -> None:
        hub_radius, arm_length, half_width, thickness = 10.0, 55.0, 3.0, 4.0
        result = self.build(
            doc(
                [
                    cylinder(id="hub", diameter=2 * hub_radius, height=10),
                    {
                        "id": "arm",
                        "type": "box",
                        "size": {"x": arm_length - 5, "y": 2 * half_width, "z": thickness},
                        "position": {"x": 5, "y": -half_width, "z": 3},
                    },
                    {
                        "id": "arms",
                        "type": "union",
                        "target": "hub",
                        "tools": ["arm"],
                        "pattern_count": 6,
                        "pattern_angle": 60,
                    },
                ]
            )
        )
        # Each arm adds the part of it outside the hub: for |y| <= w,
        # x from sqrt(r^2 - y^2) to L.
        inside = half_width * math.sqrt(hub_radius**2 - half_width**2) + hub_radius**2 * math.asin(
            half_width / hub_radius
        )
        per_arm = (2 * half_width * arm_length - inside) * thickness
        self.assertVolume(result, math.pi * hub_radius**2 * 10 + 6 * per_arm)
        self.assertEqual(result.solid_count(), 1)
        box = result.bounding_box()
        self.assertAlmostEqual(box.maximum.x, arm_length, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.minimum.x, -arm_length, delta=TOLERANCE_MM)
        tip = arm_length * math.sin(math.radians(60)) + half_width * math.cos(math.radians(60))
        self.assertAlmostEqual(box.maximum.y, tip, delta=TOLERANCE_MM)

    def test_a_single_30_degree_turn(self) -> None:
        result = self.build(
            doc(
                [
                    {
                        "id": "base",
                        "type": "box",
                        "size": {"x": 100, "y": 100, "z": 5},
                        "position": {"x": -50, "y": -50, "z": 0},
                    },
                    {
                        "id": "rib",
                        "type": "box",
                        "size": {"x": 40, "y": 4, "z": 10},
                        "position": {"x": 0, "y": -2, "z": 5},
                    },
                    {
                        "id": "turned",
                        "type": "union",
                        "target": "base",
                        "tools": ["rib"],
                        "pattern_count": 1,
                        "pattern_angle": 30,
                    },
                ]
            )
        )
        self.assertVolume(result, 100 * 100 * 5 + 40 * 4 * 10)
        # Counter-clockwise seen from +Z: the rib now points 30 degrees above +X.
        theta = math.radians(30)
        self.assertInside(result, (30 * math.cos(theta), 30 * math.sin(theta), 10), True)
        self.assertInside(result, (30, 0, 10), False)
        self.assertInside(result, (30 * math.cos(theta), -30 * math.sin(theta), 10), False)

    def test_the_right_hand_rule_on_every_axis(self) -> None:
        """A quarter turn takes +X to +Y about Z, +Y to +Z about X, +Z to +X about Y."""
        cases = {
            "Z": ((20, 0, 0), (0, 20, 0)),
            "X": ((0, 20, 0), (0, 0, 20)),
            "Y": ((0, 0, 20), (20, 0, 0)),
        }
        for axis, (start, end) in cases.items():
            with self.subTest(axis=axis):
                stub = {
                    "id": "stub",
                    "type": "box",
                    "size": {"x": 4, "y": 4, "z": 4},
                    "position": {key: value - 2 for key, value in zip("xyz", start)},
                }
                # A bar through the origin along the end direction, ending
                # where the turned stub begins: they share a face. The
                # unturned stub would be apart from it (E3).
                bar = {
                    "id": "bar",
                    "type": "box",
                    "size": {key: (36 if value else 4) for key, value in zip("xyz", end)},
                    "position": {key: (-18 if value else -2) for key, value in zip("xyz", end)},
                }
                result = self.build(
                    doc(
                        [
                            bar,
                            stub,
                            {
                                "id": "turn",
                                "type": "union",
                                "target": "bar",
                                "tools": ["stub"],
                                "pattern_angle": 90,
                                "pattern_axis": axis,
                            },
                        ]
                    )
                )
                self.assertInside(result, end, True)
                self.assertInside(result, start, False)
                self.assertVolume(result, 36 * 4 * 4 + 4 * 4 * 4)

    def test_a_quarter_turn_is_exact(self) -> None:
        """No 6e-17 skew: a box turned 90 degrees stays exactly axis-aligned."""
        result = self.build(
            doc(
                [
                    {"id": "core", "type": "box", "size": {"x": 10, "y": 10, "z": 10},
                     "position": {"x": -5, "y": -5, "z": 0}},
                    {"id": "fin", "type": "box", "size": {"x": 20, "y": 2, "z": 10},
                     "position": {"x": 0, "y": -1, "z": 0}},
                    {"id": "turn", "type": "union", "target": "core", "tools": ["fin"],
                     "pattern_count": 4, "pattern_angle": 90},
                ]
            )
        )
        for edge in result.shape.Edges():
            start, end = edge.startPoint(), edge.endPoint()
            delta = [abs(end.x - start.x), abs(end.y - start.y), abs(end.z - start.z)]
            # Every edge runs along exactly one axis.
            self.assertEqual(sum(1 for d in delta if d > 0.0), 1, msg=str(delta))
        self.assertVolume(result, 10 * 10 * 10 + 4 * (15 * 2 * 10))
        # The outline is a cross on a square: the core's 4 corners, and per fin
        # 2 tips and 2 re-entrant corners -- 20 vertical edges.
        self.assertEqual(len(select_edges(result.shape, EdgeSelector("axis_parallel", "Z"))), 20)

    def test_a_linear_pattern_of_four_slots(self) -> None:
        result = self.build(
            plate_with_tool("subtract", pattern_count=4, pattern_axis="X", pattern_step=20)
        )
        self.assertVolume(result, 100 * 30 * 5 - 4 * (5 * 20 * 5))
        # The tool's own place is one step before the first copy: uncut.
        self.assertInside(result, (2.5, 15, 2.5), True)
        for k in range(1, 5):
            with self.subTest(slot=k):
                self.assertInside(result, (20 * k + 2.5, 15, 2.5), False)
                self.assertInside(result, (20 * k + 12.5, 15, 2.5), True)

    def test_a_negative_step_runs_toward_the_negative_end(self) -> None:
        document = plate_with_tool("subtract", pattern_count=2, pattern_axis="X", pattern_step=-30)
        document["features"][1]["position"]["x"] = 90
        result = self.build(document)
        self.assertVolume(result, 100 * 30 * 5 - 2 * (5 * 20 * 5))
        self.assertInside(result, (62.5, 15, 2.5), False)
        self.assertInside(result, (32.5, 15, 2.5), False)
        self.assertInside(result, (92.5, 15, 2.5), True)

    def test_copies_are_applied_tool_by_tool(self) -> None:
        """Two tools, each patterned: the same as listing all copies."""
        result = self.build(
            doc(
                [
                    cylinder(id="disk", diameter=100, height=10),
                    cylinder(id="outer", diameter=8, height=20, position={"x": 40, "y": 0, "z": -5}),
                    cylinder(id="inner", diameter=4, height=20, position={"x": 20, "y": 0, "z": -5}),
                    {"id": "holes", "type": "subtract", "target": "disk",
                     "tools": ["outer", "inner"], "pattern_count": 4, "pattern_angle": 90},
                ]
            )
        )
        self.assertVolume(
            result, math.pi * 2500 * 10 - 4 * math.pi * 16 * 10 - 4 * math.pi * 4 * 10
        )

    def test_a_patterned_polygon_tool(self) -> None:
        """Hexagonal pockets on a circle: taper, polygon and pattern together."""
        hexagon = 3 * math.sqrt(3) / 2 * 3**2
        result = self.build(
            doc(
                [
                    cylinder(id="disk", diameter=100, height=10),
                    cylinder(id="pocket", diameter=6, height=20, sides=6,
                             position={"x": 30, "y": 0, "z": -5}),
                    {"id": "cut", "type": "subtract", "target": "disk", "tools": ["pocket"],
                     "pattern_count": 5, "pattern_angle": 72},
                ]
            )
        )
        self.assertVolume(result, math.pi * 2500 * 10 - 5 * hexagon * 10)

    def test_copies_that_coincide_on_a_symmetric_tool_are_refused(self) -> None:
        """Statically distinct copies of a tool on the pattern axis coincide
        in space: the second removes nothing, which is never a silent no-op."""
        message = self.build_error(
            doc(
                [
                    cylinder(id="disk", diameter=100, height=10),
                    cylinder(id="bore", diameter=8, height=20, position={"x": 0, "y": 0, "z": -5}),
                    {"id": "cut", "type": "subtract", "target": "disk", "tools": ["bore"],
                     "pattern_count": 2, "pattern_angle": 60},
                ]
            )
        )
        self.assertIn("(copy 2 of 2)", message)
        self.assertIn("remove no material", message)

    def test_copies_that_do_not_touch_the_target_are_e3(self) -> None:
        message = self.build_error(
            doc(
                [
                    cylinder(id="hub", diameter=20, height=10),
                    {"id": "arm", "type": "box", "size": {"x": 10, "y": 6, "z": 4},
                     "position": {"x": 30, "y": -3, "z": 3}},
                    {"id": "arms", "type": "union", "target": "hub", "tools": ["arm"],
                     "pattern_count": 3, "pattern_angle": 120},
                ]
            )
        )
        self.assertIn("rule E3", message)
        for k in (1, 2, 3):
            self.assertIn(f"'arm' (copy {k} of 3)", message)

    def test_a_pattern_that_splits_the_body_is_e3(self) -> None:
        document = plate_with_tool("subtract", pattern_count=2, pattern_axis="X", pattern_step=30)
        document["features"][1]["size"]["y"] = 40
        document["features"][1]["position"]["y"] = -5
        self.assertIn("rule E3", self.build_error(document))

    def test_an_unpatterned_operation_builds_exactly_as_in_1_1_1(self) -> None:
        old = self.build(hub_document("1.1.1"))
        new = self.build(hub_document("1.2.0"))
        self.assertAlmostEqual(old.volume(), new.volume(), delta=1e-12)
        self.assertEqual(len(old.shape.Faces()), len(new.shape.Faces()))
        self.assertEqual(len(old.shape.Edges()), len(new.shape.Edges()))

    def test_unpatterned_messages_are_unchanged(self) -> None:
        message = self.build_error(
            doc(
                [
                    cylinder(id="hub", diameter=20, height=10),
                    {"id": "arm", "type": "box", "size": {"x": 10, "y": 6, "z": 4},
                     "position": {"x": 30, "y": -3, "z": 3}},
                    {"id": "join", "type": "union", "target": "hub", "tools": ["arm"]},
                ]
            )
        )
        self.assertIn("tool 'arm' (tools[0]) is not connected to target 'hub'", message)
        self.assertNotIn("copy", message)

    def test_the_build_is_deterministic(self) -> None:
        document = plate_with_tool("union", pattern_count=3, pattern_axis="X", pattern_step=30)
        first = self.build(copy.deepcopy(document))
        second = self.build(copy.deepcopy(document))
        self.assertAlmostEqual(first.volume(), second.volume(), delta=1e-12)
        self.assertEqual(len(first.shape.Edges()), len(second.shape.Edges()))


if __name__ == "__main__":
    unittest.main()
