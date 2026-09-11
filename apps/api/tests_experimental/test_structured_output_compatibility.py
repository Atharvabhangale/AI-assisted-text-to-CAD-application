"""Stage 41: the schema the provider will actually compile.

Stage 40 measured that the operation plan could not use Anthropic structured
output at all: 31 optional properties against a limit of 24. Stage 41
restructured the schema and measured the provider's real constraints, which
turned out to be four rules acting at once rather than the single documented
one:

===========================================  ==========================
rule                                          measured
===========================================  ==========================
optional properties, whole document           at most 24
optional properties, any single object        at most 14
compiled grammar size                         a ceiling these tests
                                              approximate by branch and
                                              property budget
``oneOf``                                     rejected; ``anyOf`` accepted
``additionalProperties: false``               required on every object
``exclusiveMinimum``/``maximum``/``maxItems`` rejected
``minItems``                                  only 0 or 1
unused ``$defs``                              still cost grammar budget
===========================================  ==========================

These tests encode those rules against the **actual schema objects**, not
against a count typed into a docstring, so the schema cannot regress past
them silently. They make no network call; the live acceptance check is a
separate, explicitly-gated probe.
"""

from __future__ import annotations

import json
import unittest

from cad_ai.specification import response_schema

from cad_experimental.plan import (
    EXECUTABLE_TYPES,
    OPERATION_TYPES,
    plan_schema,
    provider_schema,
)

#: The documented whole-document ceiling, and the one Stage 40 hit.
OPTIONAL_LIMIT = 24

#: Measured separately: a single object with more than this many optional
#: properties is refused as "too complex" regardless of the document total.
PER_OBJECT_OPTIONAL_LIMIT = 14

#: JSON-Schema keywords the structured-output validator rejects outright.
REJECTED_KEYWORDS = (
    "exclusiveMinimum", "exclusiveMaximum", "maximum", "minimum",
    "maxItems", "maxLength", "minLength", "multipleOf", "oneOf",
)


def walk(node, path=""):
    """Every (path, object-schema) pair in a schema document."""
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(
            node.get("properties"), dict
        ):
            yield path, node
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def optional_properties(schema):
    found = []
    for path, node in walk(schema):
        required = set(node.get("required") or ())
        for name in node["properties"]:
            if name not in required:
                found.append(f"{path}.{name}")
    return found


def keywords(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            keywords(value, found)
    elif isinstance(node, list):
        for value in node:
            keywords(value, found)
    return found


def referenced_defs(schema):
    """Every ``$defs`` name reachable from the document body."""
    defs = schema.get("$defs") or {}

    def refs(node, acc):
        if isinstance(node, dict):
            target = node.get("$ref")
            if isinstance(target, str) and target.startswith("#/$defs/"):
                acc.add(target.rsplit("/", 1)[-1])
            for value in node.values():
                refs(value, acc)
        elif isinstance(node, list):
            for value in node:
                refs(value, acc)
        return acc

    seen = set()
    frontier = refs({k: v for k, v in schema.items() if k != "$defs"}, set())
    while frontier:
        name = frontier.pop()
        if name in seen or name not in defs:
            continue
        seen.add(name)
        frontier |= refs(defs[name], set()) - seen
    return seen


class OptionalPropertyTests(unittest.TestCase):
    """The rule Stage 40 tripped over, now enforced in both directions."""

    def test_the_plan_schema_is_within_the_document_limit(self):
        count = len(optional_properties(plan_schema()))
        self.assertLessEqual(count, OPTIONAL_LIMIT, optional_properties(plan_schema()))

    def test_the_plan_schema_has_real_headroom(self):
        """Not merely at the limit -- comfortably inside it."""
        self.assertLessEqual(len(optional_properties(plan_schema())), 15)

    def test_the_provider_schema_is_within_the_document_limit(self):
        self.assertLessEqual(
            len(optional_properties(provider_schema())), OPTIONAL_LIMIT
        )

    def test_no_single_object_exceeds_the_per_object_limit(self):
        """The old flat `parameters` object alone had fifteen optionals."""
        for schema in (plan_schema(), provider_schema()):
            for path, node in walk(schema):
                required = set(node.get("required") or ())
                optional = [
                    n for n in node["properties"] if n not in required
                ]
                with self.subTest(path=path or "<root>"):
                    self.assertLessEqual(
                        len(optional), PER_OBJECT_OPTIONAL_LIMIT, optional
                    )

    def test_it_is_a_large_improvement_on_the_measured_stage_40_count(self):
        """Stage 40 measured 31. Regressing near that must fail here."""
        self.assertLess(len(optional_properties(plan_schema())), 31 - 10)

    def test_v1_still_passes_its_own_compatibility_check(self):
        """The production schema is untouched and still within the limit."""
        count = len(optional_properties(response_schema()))
        self.assertLessEqual(count, OPTIONAL_LIMIT)
        self.assertEqual(count, 8)


class KeywordTests(unittest.TestCase):
    def test_neither_schema_uses_a_rejected_keyword(self):
        for name, schema in (("plan", plan_schema()),
                             ("provider", provider_schema())):
            used = keywords(schema)
            for keyword in REJECTED_KEYWORDS:
                with self.subTest(schema=name, keyword=keyword):
                    self.assertNotIn(keyword, used)

    def test_min_items_is_only_zero_or_one(self):
        for name, schema in (("plan", plan_schema()),
                             ("provider", provider_schema())):
            for node in json.loads(json.dumps(schema)).get("$defs", {}).values():
                pass
            def check(node, path=""):
                if isinstance(node, dict):
                    if "minItems" in node:
                        with self.subTest(schema=name, path=path):
                            self.assertIn(node["minItems"], (0, 1))
                    for key, value in node.items():
                        check(value, f"{path}.{key}")
                elif isinstance(node, list):
                    for i, value in enumerate(node):
                        check(value, f"{path}[{i}]")
            check(schema)

    def test_every_object_closes_additional_properties(self):
        """The API requires it explicitly on every object."""
        for name, schema in (("plan", plan_schema()),
                             ("provider", provider_schema())):
            for path, node in walk(schema):
                with self.subTest(schema=name, path=path or "<root>"):
                    self.assertIs(node.get("additionalProperties"), False)

    def test_unions_use_any_of(self):
        schema = plan_schema()
        self.assertIn(
            "anyOf", schema["properties"]["operations"]["items"]
        )


class DefinitionTests(unittest.TestCase):
    """Unused `$defs` still cost grammar budget -- measured, not assumed."""

    def test_the_plan_schema_carries_no_unused_definition(self):
        schema = plan_schema()
        self.assertEqual(set(schema.get("$defs") or {}), referenced_defs(schema))

    def test_the_provider_schema_carries_no_unused_definition(self):
        schema = provider_schema()
        self.assertEqual(set(schema.get("$defs") or {}), referenced_defs(schema))

    def test_the_provider_schema_drops_the_sketch_definitions(self):
        """The proof that pruning happens: it has fewer defs than the full
        schema, because no branch references the sketch's geometry."""
        self.assertLess(
            len(provider_schema().get("$defs") or {}),
            len(plan_schema().get("$defs") or {}),
        )


class CoverageTests(unittest.TestCase):
    """What each schema covers, so a silent narrowing fails here."""

    def kinds(self, schema):
        return [
            b["properties"]["type"]["const"]
            for b in schema["properties"]["operations"]["items"]["anyOf"]
        ]

    def test_the_plan_schema_still_describes_all_nine_operations(self):
        self.assertEqual(self.kinds(plan_schema()), list(OPERATION_TYPES))

    def test_the_provider_schema_covers_exactly_the_executable_subset(self):
        self.assertEqual(self.kinds(provider_schema()), list(EXECUTABLE_TYPES))

    def test_the_provider_subset_is_a_subset_and_not_a_different_shape(self):
        """Each shared branch must be identical in both schemas."""
        full = {b["properties"]["type"]["const"]: b
                for b in plan_schema()["properties"]["operations"]["items"]["anyOf"]}
        subset = {b["properties"]["type"]["const"]: b
                  for b in provider_schema()["properties"]["operations"]["items"]["anyOf"]}
        self.assertTrue(set(subset) < set(full))
        for kind, branch in subset.items():
            with self.subTest(kind=kind):
                self.assertEqual(branch, full[kind])

    def test_the_omitted_operations_are_exactly_the_unbuildable_ones(self):
        missing = set(OPERATION_TYPES) - set(EXECUTABLE_TYPES)
        self.assertEqual(missing, {"sketch", "extrude", "revolve"})
        self.assertEqual(
            missing, set(OPERATION_TYPES) - set(self.kinds(provider_schema()))
        )

    def test_the_parser_still_accepts_every_one_of_the_nine(self):
        """The narrowing is the provider's, not the language's."""
        from cad_experimental.parser import parse_plan

        payload = {
            "status": "generated", "summary": "a profile",
            "operations": [
                {"id": "profile", "type": "sketch", "parameters": {
                    "plane": "XY",
                    "geometry": [{"id": "r1", "type": "rectangle",
                                  "corner": {"x": 0, "y": 0},
                                  "width": 100, "height": 60}]}},
                {"id": "body", "type": "extrude", "target": "profile",
                 "parameters": {"distance": 10}},
            ],
        }
        plan = parse_plan(payload)
        self.assertEqual([o.TYPE for o in plan.operations],
                         ["sketch", "extrude"])


class GenerationUsesTheProviderSchemaTests(unittest.TestCase):
    def test_the_service_requests_the_compatible_schema(self):
        import pathlib

        import cad_experimental.generation as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("provider_schema()", source)
        self.assertNotIn("plan_schema()", source)


if __name__ == "__main__":
    unittest.main()
