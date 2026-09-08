"""The document/geometry integration point.

`test_serialization.py` deliberately builds no geometry -- the canonical CAD
document is defined without reference to a kernel, and a test there asserts
the module cannot even load one. This file is the other half of that claim:
that a part recovered from a canonical document is the same part the geometry
engine accepts, so the document really is sufficient to rebuild everything
derived from it.

It needs CadQuery, which is why it is separate.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any, Dict, List

from cad_core import validate
from cad_core.local_cad import build_part
from cad_core.serialization import (
    part_from_json,
    part_hash,
    part_to_json,
    parts_equivalent,
    serialize_part,
)

VOLUME_TOLERANCE_MM3 = 1e-6
TOLERANCE_MM = 1e-6


def document(features: List[Dict[str, Any]], **overrides: Any) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate",
        "features": features,
    }
    doc.update(overrides)
    return doc


PLATE = {
    "id": "plate",
    "type": "box",
    "size": {"x": 100, "y": 60, "z": 10},
    "position": {"x": 0, "y": 0, "z": 0},
}


def section_d_document() -> Dict[str, Any]:
    features: List[Dict[str, Any]] = [dict(PLATE)]
    for index, (x, y) in enumerate(((10, 10), (90, 10), (10, 50), (90, 50)), 1):
        features.append(
            {
                "id": f"hole{index}",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": x, "y": y, "z": 0},
                "axis": "+Z",
            }
        )
    return document(features, name="plate-100x60x10-4holes")


class DocumentGeometryTestCase(unittest.TestCase):
    def part_from(self, doc: Dict[str, Any]):
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def corpus(self):
        return (
            ("box", document([dict(PLATE)])),
            ("section-d", section_d_document()),
            (
                "cylinder",
                document(
                    [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
                    name="pin",
                ),
            ),
            (
                "subtract",
                document(
                    [
                        dict(PLATE),
                        {
                            "id": "tool",
                            "type": "cylinder",
                            "diameter": 20,
                            "height": 20,
                            "position": {"x": 20, "y": 20, "z": -5},
                        },
                        {
                            "id": "cut",
                            "type": "subtract",
                            "target": "plate",
                            "tools": ["tool"],
                        },
                    ]
                ),
            ),
            (
                "fillet",
                document(
                    [
                        dict(PLATE),
                        {
                            "id": "round",
                            "type": "fillet",
                            "target": "plate",
                            "radius": 2,
                            "edges": {"select": "axis_parallel", "axis": "Z"},
                        },
                    ]
                ),
            ),
            (
                "chamfer",
                document(
                    [
                        dict(PLATE),
                        {
                            "id": "bevel",
                            "type": "chamfer",
                            "target": "plate",
                            "distance": 2,
                            "edges": {"select": "axis_parallel", "axis": "Z"},
                        },
                    ]
                ),
            ),
        )


class TestDeserializedPartsBuild(DocumentGeometryTestCase):
    def test_a_round_tripped_part_builds_identically(self) -> None:
        """The document is sufficient: same solid, feature for feature."""
        for name, doc in self.corpus():
            original = self.part_from(doc)
            recovered = part_from_json(part_to_json(original))
            with self.subTest(name=name):
                self.assertEqual(original, recovered)
                self.assertTrue(parts_equivalent(original, recovered))

                first = build_part(original)
                second = build_part(recovered)
                self.assertEqual(first.feature_id, second.feature_id)
                self.assertEqual(first.solid_count(), second.solid_count())
                self.assertAlmostEqual(
                    first.volume(), second.volume(), delta=VOLUME_TOLERANCE_MM3
                )
                self.assertEqual(
                    len(first.shape.Faces()), len(second.shape.Faces())
                )
                for axis in "xyz":
                    self.assertAlmostEqual(
                        getattr(first.bounding_box().minimum, axis),
                        getattr(second.bounding_box().minimum, axis),
                        delta=TOLERANCE_MM,
                    )

    def test_the_section_d_plate_builds_from_its_document(self) -> None:
        recovered = part_from_json(part_to_json(self.part_from(section_d_document())))
        result = build_part(recovered)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")
        expected = 100 * 60 * 10 - 4 * math.pi * 4.0**2 * 10
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_a_terse_document_builds_the_same_solid_as_its_canonical_form(self) -> None:
        """Materialising the defaults changes no geometry."""
        terse = document(
            [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
            name="pin",
        )
        terse_part = self.part_from(terse)
        canonical_part = part_from_json(part_to_json(terse_part))
        self.assertAlmostEqual(
            build_part(terse_part).volume(),
            build_part(canonical_part).volume(),
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_building_geometry_does_not_change_the_document_or_its_hash(self) -> None:
        """Derived artifacts never feed back into the authoritative document."""
        for name, doc in self.corpus():
            part = self.part_from(doc)
            before_text, before_hash = part_to_json(part), part_hash(part)
            built = build_part(part)
            self.assertTrue(built.is_solid())
            with self.subTest(name=name):
                self.assertEqual(part_to_json(part), before_text)
                self.assertEqual(part_hash(part), before_hash)

    def test_the_document_carries_nothing_the_engine_produced(self) -> None:
        """A built part serializes exactly as it did before building."""
        part = self.part_from(section_d_document())
        built = build_part(part)
        self.assertGreater(len(built.shape.Faces()), 0)
        emitted = serialize_part(part)
        self.assertEqual(set(emitted), {"schema_version", "units", "name", "features"})
        recovered = serialize_part(part_from_json(part_to_json(part)))
        self.assertEqual(
            json.dumps(emitted, sort_keys=True),
            json.dumps(recovered, sort_keys=True),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
