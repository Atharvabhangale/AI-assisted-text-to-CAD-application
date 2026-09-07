"""Unit tests for FeatureScript generation of a single V1 box.

These tests require no Onshape account, no network connection and no browser:
they compare generated source text against expectations, and never execute it.
"""

from __future__ import annotations

import ast
import json
import re
import socket
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_core import validate
from cad_core.featurescript import (
    FEATURESCRIPT_VERSION,
    STANDARD_LIBRARY_PATH,
    UnsupportedPartError,
    generate_featurescript,
)
from cad_core.model import Box, Cylinder, Part, Position, Size

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "cad_core" / "featurescript.py"


def box_document(**overrides: Any) -> Dict[str, Any]:
    """A single-box specification document."""
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
    }
    feature.update(overrides.pop("feature", {}))
    document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "test-part",
        "features": [feature],
    }
    document.update(overrides)
    return document


class FeatureScriptTestCase(unittest.TestCase):
    def part_from(self, document: Dict[str, Any]) -> Part:
        """Build a typed Part the way the pipeline does: through validate()."""
        result = validate(document)
        self.assertTrue(
            result.valid, msg=f"fixture is not valid: {[str(e) for e in result.errors]}"
        )
        assert result.part is not None
        return result.part

    def generate(self, document: Dict[str, Any]) -> str:
        return generate_featurescript(self.part_from(document))


# --- the two required box cases, each asserted end to end -----------------


class TestRequiredCases(FeatureScriptTestCase):
    """The two Stage 3A cases, each verifying the full contract in one place."""

    def _assert_contract(self, source: object, expected_values: tuple) -> None:
        self.assertIsInstance(source, str)
        assert isinstance(source, str)
        self.assertNotEqual(source.strip(), "")
        self.assertIn(f"FeatureScript {FEATURESCRIPT_VERSION};", source)
        self.assertIn(
            f'import(path : "{STANDARD_LIBRARY_PATH}", version : "{FEATURESCRIPT_VERSION}.0");',
            source,
        )
        self.assertIn("defineFeature", source)
        self.assertIn("fCuboid", source)
        self.assertIn("corner1", source)
        self.assertIn("corner2", source)
        for value in expected_values:
            with self.subTest(value=value):
                self.assertIn(value, source)

    def test_origin_box(self) -> None:
        """size = (100, 60, 10), position = (0, 0, 0)."""
        document = box_document(feature={"position": {"x": 0, "y": 0, "z": 0}})
        source = self.generate(document)
        self._assert_contract(
            source,
            (
                '"corner1" : vector(0, 0, 0) * millimeter,',
                '"corner2" : vector(100, 60, 10) * millimeter',
            ),
        )
        self.assertEqual(source, self.generate(document))

    def test_offset_box(self) -> None:
        """size = (100, 60, 10), position = (10, 20, 30)."""
        document = box_document(feature={"position": {"x": 10, "y": 20, "z": 30}})
        source = self.generate(document)
        self._assert_contract(
            source,
            (
                '"corner1" : vector(10, 20, 30) * millimeter,',
                '"corner2" : vector(110, 80, 40) * millimeter',
            ),
        )
        self.assertEqual(source, self.generate(document))

    def test_both_cases_are_byte_for_byte_stable(self) -> None:
        for position in ({"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 20, "z": 30}):
            with self.subTest(position=position):
                part = self.part_from(box_document(feature={"position": position}))
                first = generate_featurescript(part)
                self.assertEqual(
                    {generate_featurescript(part).encode("utf-8") for _ in range(10)},
                    {first.encode("utf-8")},
                )


# --- generation for the supported subset ------------------------------------


class TestBoxGeneration(FeatureScriptTestCase):
    def test_box_at_origin(self) -> None:
        source = self.generate(box_document())
        self.assertIn("vector(0, 0, 0) * millimeter", source)
        self.assertIn("vector(100, 60, 10) * millimeter", source)
        self.assertIn('fCuboid(context, id + "box", {', source)

    def test_box_at_origin_with_explicit_position(self) -> None:
        """An explicit origin position generates the same source as an omitted one."""
        implicit = self.generate(box_document())
        explicit = self.generate(box_document(feature={"position": {"x": 0, "y": 0, "z": 0}}))
        self.assertEqual(implicit, explicit)

    def test_box_positioned_at_10_20_30(self) -> None:
        source = self.generate(box_document(feature={"position": {"x": 10, "y": 20, "z": 30}}))
        # position is the minimum corner; the far corner is position + size
        self.assertIn('"corner1" : vector(10, 20, 30) * millimeter,', source)
        self.assertIn('"corner2" : vector(110, 80, 40) * millimeter', source)

    def test_placement_is_absolute_not_centred(self) -> None:
        """The box must not be centred on its position."""
        source = self.generate(box_document(feature={"position": {"x": 10, "y": 20, "z": 30}}))
        self.assertNotIn("vector(-40", source)  # would appear if centred on x
        self.assertNotIn("vector(60, 50, 35)", source)  # the centred far corner
        self.assertIn("vector(10, 20, 30)", source)

    def test_negative_position(self) -> None:
        source = self.generate(box_document(feature={"position": {"x": -50, "y": -30, "z": -5}}))
        self.assertIn('"corner1" : vector(-50, -30, -5) * millimeter,', source)
        self.assertIn('"corner2" : vector(50, 30, 5) * millimeter', source)

    def test_fractional_values_keep_precision_without_float_noise(self) -> None:
        source = self.generate(
            box_document(
                feature={
                    "size": {"x": 12.5, "y": 0.25, "z": 3},
                    "position": {"x": 1.5, "y": 0, "z": -0.75},
                }
            )
        )
        self.assertIn('"corner1" : vector(1.5, 0, -0.75) * millimeter,', source)
        self.assertIn('"corner2" : vector(14, 0.25, 2.25) * millimeter', source)
        self.assertNotIn(".0,", source)
        self.assertNotIn("0000", source)

    def test_integral_lengths_are_not_written_as_floats(self) -> None:
        source = self.generate(box_document())
        self.assertIn("vector(100, 60, 10)", source)
        self.assertNotIn("100.0", source)

    def test_generated_source_reports_the_specification_it_came_from(self) -> None:
        source = self.generate(box_document(name="bracket-plate"))
        self.assertIn("Generated by cad-core from a V1 CAD specification.", source)
        self.assertIn("docs/cad-specification.md, schema version 1.0.0", source)
        self.assertIn("bracket-plate", source)
        self.assertIn("'plate' (box)", source)
        self.assertIn("mm -> millimeter", source)

    def test_generated_source_states_onshape_execution_is_not_implemented(self) -> None:
        self.assertIn("Onshape execution is not implemented", self.generate(box_document()))


# --- structural declarations and shape of the output -----------------------


class TestGeneratedStructure(FeatureScriptTestCase):
    def test_output_is_not_empty(self) -> None:
        source = self.generate(box_document())
        self.assertTrue(source.strip())
        self.assertGreater(len(source.splitlines()), 10)

    def test_version_declaration_is_the_first_line(self) -> None:
        source = self.generate(box_document())
        self.assertEqual(source.splitlines()[0], f"FeatureScript {FEATURESCRIPT_VERSION};")

    def test_import_declaration_matches_the_version(self) -> None:
        source = self.generate(box_document())
        self.assertEqual(
            source.splitlines()[1],
            f'import(path : "{STANDARD_LIBRARY_PATH}", version : "{FEATURESCRIPT_VERSION}.0");',
        )

    def test_required_structural_declarations_are_present(self) -> None:
        source = self.generate(box_document())
        for fragment in (
            "FeatureScript ",
            "import(path : ",
            'annotation { "Feature Type Name" : ',
            "export const cadCoreBox = defineFeature(",
            "function(context is Context, id is Id, definition is map)",
            "precondition",
            "fCuboid(",
            '"corner1" : ',
            '"corner2" : ',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_output_ends_with_a_single_newline(self) -> None:
        source = self.generate(box_document())
        self.assertTrue(source.endswith("});\n"))
        self.assertFalse(source.endswith("\n\n"))

    def test_braces_are_balanced(self) -> None:
        source = self.generate(box_document())
        self.assertEqual(source.count("{"), source.count("}"))
        self.assertEqual(source.count("("), source.count(")"))

    def test_free_text_cannot_break_out_of_a_comment(self) -> None:
        """A part name is free text; it must not corrupt the generated source.

        The injected text may still appear, but only inside a comment line.
        """
        injected = 'export const x = 1; // "quoted"'
        source = self.generate(box_document(name=f"evil\n{injected}\r\ttail"))

        carrying = [line for line in source.splitlines() if "evil" in line]
        self.assertEqual(len(carrying), 1, msg="the name must stay on one line")
        self.assertTrue(carrying[0].startswith("// "), msg=f"escaped: {carrying[0]!r}")
        self.assertIn(injected, carrying[0])

        # Nothing outside a comment gained a declaration.
        code_lines = [
            line for line in source.splitlines() if not line.lstrip().startswith("//")
        ]
        self.assertEqual(
            [line for line in code_lines if line.lstrip().startswith("export const")],
            ["export const cadCoreBox = defineFeature("
             "function(context is Context, id is Id, definition is map)"],
        )
        for line in source.splitlines():
            self.assertNotIn("\r", line)
            self.assertNotIn("\t", line)


# --- constructs verified against the standard library source ---------------


class TestVerifiedConstructs(FeatureScriptTestCase):
    """Pin the generated source to FeatureScript constructs read from the
    Onshape standard library source at version 2960.

    Each assertion corresponds to a citation in the module docstring of
    ``cad_core.featurescript``.
    """

    def test_version_declaration_uses_the_bare_number(self) -> None:
        """geometry.fs:1 -- ``FeatureScript 2960;``"""
        self.assertEqual(self.generate(box_document()).splitlines()[0], "FeatureScript 2960;")

    def test_import_version_appends_dot_zero(self) -> None:
        """geometry.fs:17 -- ``version : "2960.0"``"""
        self.assertEqual(
            self.generate(box_document()).splitlines()[1],
            'import(path : "onshape/std/geometry.fs", version : "2960.0");',
        )

    def test_precondition_block_is_empty(self) -> None:
        """feature.fs ``dummyFeature`` and context.fs ``precondition {}``.

        Comments are permitted inside it; statements are not, because the
        feature takes no parameters.
        """
        source = self.generate(box_document())
        lines = source.splitlines()
        opening = lines.index("    precondition") + 1
        self.assertEqual(lines[opening], "    {")
        closing = lines.index("    }", opening)
        for line in lines[opening + 1 : closing]:
            self.assertTrue(line.strip().startswith("//"), msg=f"statement in precondition: {line!r}")

    def test_one_argument_define_feature_form(self) -> None:
        """feature.fs:120 -- the trailing defaults map is optional."""
        source = self.generate(box_document())
        self.assertTrue(source.rstrip().endswith("});"))
        self.assertNotIn("}, {});", source)

    def test_fcuboid_is_called_with_the_documented_field_names(self) -> None:
        """primitives.fs:101-118 -- ``corner1`` and ``corner2``."""
        source = self.generate(box_document())
        self.assertIn('fCuboid(context, id + "box", {', source)
        self.assertIn('"corner1" : ', source)
        self.assertIn('"corner2" : ', source)
        self.assertNotIn("firstCorner", source)
        self.assertNotIn("sideLength", source)

    def test_lengths_use_the_millimeter_unit_constant(self) -> None:
        """units.fs:157 -- ``export const millimeter = 0.001 * meter;``"""
        source = self.generate(box_document())
        self.assertEqual(source.count("* millimeter"), 2)
        for token in ("* inch", "* meter", "* centimeter"):
            self.assertNotIn(token, source)

    def test_corners_differ_on_every_axis(self) -> None:
        """fCuboid's precondition requires corner1[dim] != corner2[dim].

        Specification rule S10 (size components > 0) guarantees this.
        """
        source = self.generate(box_document(feature={"position": {"x": 10, "y": 20, "z": 30}}))
        corner1 = _corner_values(source, "corner1")
        corner2 = _corner_values(source, "corner2")
        for axis, (low, high) in enumerate(zip(corner1, corner2)):
            with self.subTest(axis="xyz"[axis]):
                self.assertNotEqual(low, high)

    def test_no_construct_outside_the_verified_set(self) -> None:
        """Nothing is emitted that was not read from the library source."""
        source = self.generate(box_document())
        identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _code_only(source)))
        verified = {
            "FeatureScript", "import", "path", "onshape", "std", "geometry", "fs", "version",
            "annotation", "Feature", "Type", "Name", "cad", "core", "box", "plate",
            "export", "const", "cadCoreBox", "defineFeature", "function", "context",
            "is", "Context", "id", "Id", "definition", "map", "precondition",
            "fCuboid", "corner1", "corner2", "vector", "millimeter",
        }
        self.assertEqual(identifiers - verified, set())


def _code_only(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )


def _corner_values(source: str, corner: str) -> list:
    match = re.search(rf'"{corner}" : vector\(([^)]*)\)', source)
    assert match is not None, f"{corner} not found"
    return [float(part) for part in match.group(1).split(",")]


# --- determinism ------------------------------------------------------------


class TestDeterminism(FeatureScriptTestCase):
    def test_repeated_calls_are_identical(self) -> None:
        part = self.part_from(box_document(feature={"position": {"x": 10, "y": 20, "z": 30}}))
        outputs = {generate_featurescript(part) for _ in range(25)}
        self.assertEqual(len(outputs), 1)

    def test_equal_parts_from_separate_validations_agree(self) -> None:
        document = box_document(feature={"position": {"x": 10, "y": 20, "z": 30}})
        self.assertEqual(self.generate(document), self.generate(document))

    def test_no_nondeterministic_content(self) -> None:
        """No timestamps, generated ids, hostnames or filesystem paths."""
        import getpass
        import os
        import platform
        import sys

        source = self.generate(box_document())
        for marker in (
            str(os.getpid()),
            platform.node(),
            getpass.getuser(),
            sys.prefix,
            str(Path.cwd()),
            str(Path.home()),
        ):
            if marker:
                with self.subTest(marker=marker):
                    self.assertNotIn(marker, source)
        for word in ("uuid", "UUID", "generated at", "Timestamp", "20250", "20260"):
            with self.subTest(word=word):
                self.assertNotIn(word, source)

    def test_directly_constructed_part_matches_the_validated_one(self) -> None:
        validated = self.part_from(box_document(feature={"position": {"x": 10, "y": 20, "z": 30}}))
        constructed = Part(
            schema_version="1.0.0",
            units="mm",
            name="test-part",
            features=(
                Box(id="plate", size=Size(100.0, 60.0, 10.0), position=Position(10.0, 20.0, 30.0)),
            ),
        )
        self.assertEqual(generate_featurescript(validated), generate_featurescript(constructed))


# --- the supported-subset boundary ------------------------------------------


class TestUnsupportedInput(FeatureScriptTestCase):
    def test_cylinder_is_rejected(self) -> None:
        document = box_document(
            feature={"type": "cylinder", "diameter": 8, "height": 20, "size": None}
        )
        del document["features"][0]["size"]
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(self.part_from(document))
        self.assertIn("cylinder", str(caught.exception))

    def test_through_hole_is_rejected(self) -> None:
        """A box plus a through-hole is a two-feature history and unsupported."""
        document = box_document()
        document["features"].append(
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            }
        )
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(self.part_from(document))
        self.assertIn("exactly one feature", str(caught.exception))

    def test_through_hole_alone_is_rejected_by_feature_type(self) -> None:
        """Constructed directly, since a lone through-hole is not a valid part."""
        from cad_core.model import ThroughHole

        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                ThroughHole(
                    id="hole", target="plate", diameter=8.0, position=Position(10.0, 10.0, 0.0)
                ),
            ),
        )
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(part)
        self.assertIn("through_hole", str(caught.exception))

    def test_multiple_features_are_rejected(self) -> None:
        document = box_document()
        document["features"].append(
            {"id": "pin", "type": "cylinder", "diameter": 8, "height": 20}
        )
        document["features"].append(
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["pin"]}
        )
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(self.part_from(document))
        self.assertIn("exactly one feature", str(caught.exception))
        self.assertIn("3", str(caught.exception))

    def test_two_boxes_are_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="a", size=Size(1.0, 1.0, 1.0)),
                Box(id="b", size=Size(1.0, 1.0, 1.0)),
            ),
        )
        with self.assertRaises(UnsupportedPartError):
            generate_featurescript(part)

    def test_empty_feature_history_is_rejected(self) -> None:
        part = Part(schema_version="1.0.0", units="mm", name="p", features=())
        with self.assertRaises(UnsupportedPartError):
            generate_featurescript(part)

    def test_unsupported_units_are_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="in",
            name="p",
            features=(Box(id="a", size=Size(1.0, 1.0, 1.0)),),
        )
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(part)
        self.assertIn("'in'", str(caught.exception))

    def test_feature_id_outside_the_specification_pattern_is_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(Box(id='bad" id', size=Size(1.0, 1.0, 1.0)),),
        )
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(part)
        self.assertIn("pattern", str(caught.exception))

    def test_unsupported_input_produces_no_output(self) -> None:
        """Failure is explicit: nothing partial is returned."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(Cylinder(id="c", diameter=8.0, height=20.0),),
        )
        with self.assertRaises(UnsupportedPartError):
            generate_featurescript(part)


# --- the Stage 2 example plate is out of scope for Stage 3A ---------------


class TestStage2ExamplePlateIsRejected(FeatureScriptTestCase):
    """The Section D example plate must NOT be generated by this stage.

    It is a valid V1 part, but it carries four ``through_hole`` features, which
    Stage 3A does not support. It must be rejected rather than silently
    generating only its box.
    """

    SPECIFICATION_PATH = Path(__file__).resolve().parents[3] / "docs" / "cad-specification.md"

    @classmethod
    def load_example(cls) -> Dict[str, Any]:
        text = cls.SPECIFICATION_PATH.read_text(encoding="utf-8")
        section = re.search(
            r"^## D\. Example specification$(.*?)^## E\.", text, re.MULTILINE | re.DOTALL
        )
        assert section is not None, f"Section D not found in {cls.SPECIFICATION_PATH}"
        block = re.search(r"^```json$\n(.*?)^```$", section.group(1), re.MULTILINE | re.DOTALL)
        assert block is not None, f"no JSON example in Section D of {cls.SPECIFICATION_PATH}"
        return json.loads(block.group(1))

    def test_example_plate_is_a_valid_v1_part(self) -> None:
        """Establish that rejection is about scope, not about validity."""
        part = self.part_from(self.load_example())
        self.assertEqual(len(part.features), 5)
        self.assertEqual(
            [feature.TYPE for feature in part.features],
            ["box", "through_hole", "through_hole", "through_hole", "through_hole"],
        )

    def test_example_plate_is_rejected_by_the_generator(self) -> None:
        part = self.part_from(self.load_example())
        with self.assertRaises(UnsupportedPartError) as caught:
            generate_featurescript(part)
        self.assertIn("exactly one feature", str(caught.exception))
        self.assertIn("5", str(caught.exception))

    def test_rejection_produces_no_partial_source(self) -> None:
        """No box-only source is emitted for a part whose holes cannot be made."""
        part = self.part_from(self.load_example())
        try:
            generate_featurescript(part)
        except UnsupportedPartError:
            pass
        else:  # pragma: no cover - the assertion above already guards this
            self.fail("the example plate must be rejected")


# --- the typed Part boundary ------------------------------------------------


class TestTypedPartBoundary(unittest.TestCase):
    def test_raw_dictionary_cannot_bypass_the_boundary(self) -> None:
        document = box_document()
        with self.assertRaises(TypeError) as caught:
            generate_featurescript(document)  # type: ignore[arg-type]
        self.assertIn("Part", str(caught.exception))

    def test_json_text_cannot_bypass_the_boundary(self) -> None:
        with self.assertRaises(TypeError):
            generate_featurescript(json.dumps(box_document()))  # type: ignore[arg-type]

    def test_other_types_cannot_bypass_the_boundary(self) -> None:
        for value in (None, 0, [], (), {"features": []}, object(), Box(id="b", size=Size(1, 1, 1))):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    generate_featurescript(value)  # type: ignore[arg-type]

    def test_an_invalid_document_never_reaches_the_generator(self) -> None:
        """The pipeline's only route to a Part is a successful validation."""
        result = validate(box_document(feature={"size": {"x": -1, "y": 60, "z": 10}}))
        self.assertFalse(result.valid)
        self.assertIsNone(result.part)
        with self.assertRaises(TypeError):
            generate_featurescript(result.part)  # type: ignore[arg-type]

    def test_validation_result_is_not_accepted_in_place_of_a_part(self) -> None:
        result = validate(box_document())
        with self.assertRaises(TypeError):
            generate_featurescript(result)  # type: ignore[arg-type]


# --- no Onshape account, network or browser --------------------------------


class TestNoExternalDependencies(FeatureScriptTestCase):
    def test_generation_works_with_networking_disabled(self) -> None:
        """Generation must not open a socket."""

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the generator must not use the network")

        original_socket = socket.socket
        original_connection = socket.create_connection
        socket.socket = refuse  # type: ignore[assignment]
        socket.create_connection = refuse  # type: ignore[assignment]
        try:
            source = self.generate(box_document())
        finally:
            socket.socket = original_socket  # type: ignore[assignment]
            socket.create_connection = original_connection  # type: ignore[assignment]
        self.assertIn("fCuboid(", source)

    def test_module_imports_nothing_network_or_browser_related(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {
            "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx",
            "webbrowser", "selenium", "playwright", "subprocess", "asyncio",
        }
        self.assertEqual(imported & forbidden, set())

    def test_generator_does_not_execute_the_generated_source(self) -> None:
        """No dynamic evaluation anywhere in the module.

        Checked on the parsed tree, so a legitimate ``re.compile`` is not
        mistaken for a call to the ``compile`` builtin.
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(called & {"eval", "exec", "compile", "__import__", "open"}, set())

    def test_no_credentials_or_account_configuration_is_read(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for marker in ("environ", "getenv", "api_key", "apikey", "access_key", "secret", "token"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
