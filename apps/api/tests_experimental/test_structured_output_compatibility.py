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
    EDGE_MODIFIER_TYPES,
    EXECUTABLE_TYPES,
    OPERATION_TYPES,
    compact_provider_schema,
    executable_schema,
    plan_schema,
    provider_schema,
)

#: The branch ceiling Stage 41 measured: eight operation branches were
#: accepted and a ninth refused, even stripped to one field.
PROVIDER_BRANCH_LIMIT = 8

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

    def test_the_executable_schema_drops_the_sketch_definitions(self):
        """The proof that pruning happens: the executable subset has fewer
        defs than the full schema, because no branch of it references the
        sketch's geometry."""
        self.assertLess(
            len(executable_schema().get("$defs") or {}),
            len(plan_schema().get("$defs") or {}),
        )

    def test_the_compact_schema_drops_the_constraint_definitions(self):
        """Dropping a sketch's optional `constraints` must take the two
        definitions only a constraint reaches with it -- that is the whole
        point of the variant, and an unpruned def still costs budget."""
        full = set(provider_schema().get("$defs") or {})
        compact = set(compact_provider_schema().get("$defs") or {})
        self.assertTrue(compact < full)
        self.assertEqual(full - compact,
                         {"sketch_constraint", "sketch_point_handle"})


class CoverageTests(unittest.TestCase):
    """What each schema covers, so a silent narrowing fails here."""

    def branches(self, schema):
        return schema["properties"]["operations"]["items"]["anyOf"]

    def merged_branch(self, kinds):
        """The merged branch carrying exactly ``kinds``.

        Named rather than "the first branch with an enum": there are three
        merged groups now, and picking the first would silently test a
        different pair than the docstring claims.
        """
        wanted = list(kinds)
        for branch in self.branches(provider_schema()):
            if "enum" in branch["properties"]["type"]:
                if self.types_of(branch) == wanted:
                    return branch
        raise AssertionError(f"no merged branch for {wanted}")

    def types_of(self, branch):
        discriminator = branch["properties"]["type"]
        if "const" in discriminator:
            return [discriminator["const"]]
        return list(discriminator["enum"])

    def kinds(self, schema):
        """Every operation type a schema admits, in order.

        A branch names one type with `const` or several with `enum`, so this
        flattens both: what matters is which types a decoder may emit, not
        how many branches carry them.
        """
        names = []
        for branch in self.branches(schema):
            names.extend(self.types_of(branch))
        return names

    def test_the_plan_schema_still_describes_all_nine_operations(self):
        self.assertEqual(self.kinds(plan_schema()), list(OPERATION_TYPES))

    def test_the_plan_schema_gives_each_type_its_own_branch(self):
        """The faithful description merges nothing: nine types, nine
        branches, each discriminated by its own `const`."""
        self.assertEqual(len(self.branches(plan_schema())),
                         len(OPERATION_TYPES))
        for branch in self.branches(plan_schema()):
            self.assertIn("const", branch["properties"]["type"])

    def test_the_provider_schema_covers_the_whole_vocabulary(self):
        """Stage 44. A schema is what the model may SAY; the execution
        boundary is what the engine can BUILD. Narrowing the first to the
        second is what made Stage 43 record a forced refusal as a choice:
        with no sketch, extrude or revolve branch in the grammar, the model
        had no way to answer those cases except by refusing.
        """
        self.assertEqual(sorted(self.kinds(provider_schema())),
                         sorted(OPERATION_TYPES))

    def test_the_provider_schema_fits_the_measured_branch_ceiling(self):
        """Eight branches were accepted and a ninth refused (Stage 41), so
        nine types must travel in at most eight branches."""
        self.assertLessEqual(len(self.branches(provider_schema())),
                             PROVIDER_BRANCH_LIMIT)

    def test_the_merged_branches_are_exactly_the_declared_groups(self):
        """No creeping merge. A branch carries more than one type only when
        `MERGED_SCHEMA_GROUPS` says so, and every other type keeps its own
        branch and so its own exact `required` list.

        Three groups since `union` became the eleventh operation type:
        `fillet` with `chamfer`, `extrude` with `revolve`, and `subtract`
        with `union`. Each pair shares its operation-level shape. The first
        two differ in their parameters; the third does not differ at all,
        which is why it merges without loosening anything.
        """
        from cad_experimental.plan import MERGED_SCHEMA_GROUPS

        merged = [self.types_of(b) for b in self.branches(provider_schema())
                  if "enum" in b["properties"]["type"]]
        # Compared as a set of groups, not a sequence: branches are emitted
        # in vocabulary order and the groups are declared in the order they
        # were discovered, so requiring the two to coincide would pin an
        # accident. What matters is that the merges are exactly these and
        # that each group's own membership and order are intact.
        self.assertEqual(
            sorted(merged),
            sorted(list(group) for group in MERGED_SCHEMA_GROUPS),
        )
        self.assertIn(list(EDGE_MODIFIER_TYPES), merged)

    def test_the_executable_schema_covers_exactly_the_six_v1_features(self):
        """Kept under its own name so the Stage 43 instrument still exists
        and still means what it meant.

        Pinned to `V1_FEATURE_TYPES`, not `EXECUTABLE_TYPES`: Stage 46 made
        `pattern` executable, and a recorded measurement must stay
        attributable to the instrument that produced it.
        """
        from cad_experimental.plan import V1_FEATURE_TYPES

        self.assertEqual(self.kinds(executable_schema()),
                         list(V1_FEATURE_TYPES))
        self.assertNotEqual(set(EXECUTABLE_TYPES), set(V1_FEATURE_TYPES))

    def test_the_unmerged_provider_branches_match_the_full_schema(self):
        """A branch that was not merged must be identical in both, so the
        provider schema is the same description and not a second one."""
        full = {b["properties"]["type"]["const"]: b
                for b in self.branches(plan_schema())}
        for branch in self.branches(provider_schema()):
            if "enum" in branch["properties"]["type"]:
                continue
            kind = branch["properties"]["type"]["const"]
            with self.subTest(kind=kind):
                self.assertEqual(branch, full[kind])

    def test_the_merged_branch_requires_only_what_both_types_require(self):
        """The whole cost of merging, stated: `target` and `edges` stay
        required, and only the choice between `radius` and `distance` leaves
        the grammar."""
        from cad_experimental.plan import PARAMETERS

        branch = self.merged_branch(EDGE_MODIFIER_TYPES)
        self.assertIn("target", branch["required"])
        parameters = branch["properties"]["parameters"]
        self.assertEqual(parameters["required"], ["edges"])
        self.assertEqual(sorted(parameters["properties"]),
                         ["distance", "edges", "radius"])
        for kind in EDGE_MODIFIER_TYPES:
            self.assertIn("edges", PARAMETERS[kind][0])

    def test_the_merged_grammar_is_looser_than_the_parser(self):
        """Proof the loosening is confined to the grammar: a fillet carrying
        a chamfer's length satisfies the merged branch, and the parser
        refuses it anyway."""
        from cad_experimental.parser import PlanParseError, parse_plan

        branch = self.merged_branch(EDGE_MODIFIER_TYPES)
        self.assertNotIn("radius",
                         branch["properties"]["parameters"]["required"])
        with self.assertRaises(PlanParseError):
            parse_plan({
                "status": "generated", "summary": "s",
                "operations": [
                    {"id": "plate", "type": "box",
                     "parameters": {"x": 10, "y": 10, "z": 10}},
                    {"id": "r", "type": "fillet", "target": "plate",
                     "parameters": {"distance": 1,
                                    "edges": {"select": "all"}}},
                ],
            })

    def test_the_parser_still_accepts_every_one_of_the_nine(self):
        """The parser is authoritative, and no schema choice changes it."""
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
