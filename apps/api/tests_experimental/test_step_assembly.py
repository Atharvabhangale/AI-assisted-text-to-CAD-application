"""A real multi-body STEP: every body, none fused, each under its own id.

Stage 74, step 5 of `docs/multi-body-design.md`. Until now `/session/export`
refused a multi-body part by name, because a STEP holding one solid of a
two-body part is a lie about the geometry and there was nothing better to
write. STEP represents several solids natively, so there is.

THE TWO FAILURES THIS FILE EXISTS TO CATCH, both measured rather than
imagined.

1. **A file that exists is not an export.** FreeCAD's `Part.export`, handed
   raw `Part` shapes, leaves a well-formed 1.6 kB STEP that reads back as
   ZERO solids. It exists, it is non-empty, it parses. Every check short of
   counting solids passes on it. That is why the writer verifies by reading
   the file back and counting, and why `TheWriterVerifiesWhatItWroteTests`
   asserts the count rather than the bytes.

2. **A geometrically perfect assembly can still have lost every name.**
   Handed a compound, BOTH engines write a two-solid STEP whose bodies are
   called `Open CASCADE STEP translator 7.9 1.1` and `1.2`. Solid count,
   volumes, face and edge totals all match. The identity that this whole
   multi-body slice is about is simply gone, and no count can see it. So the
   verification checks the NAMES too, and `TheBodyIdsSurviveTests` is what
   would catch a change that stopped it.

What is deliberately NOT claimed. The name check proves each id REACHED the
file, by reading the STEP as the text it is. It does not prove which solid
carries which name -- that needs a per-engine assembly reader, and the two
engines' readers differ. The exact boundary is written down in
`docs/multi-body-step3/README.md` rather than papered over with a parity
claim neither engine supports.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from cad_experimental.cad_backend import (
    BackendOperationError,
    BackendUnavailable,
    ordered_bodies,
    resolve_backend,
    verify_assembly,
)
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan_text
from cad_experimental.validation import validate_plan

PART = "part"

CUBE_SIDE = 40.0
PIN_DIAMETER, PIN_HEIGHT = 20.0, 30.0
GAP = 10.0

CUBE_VOLUME = CUBE_SIDE ** 3
PIN_VOLUME = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_HEIGHT
TOLERANCE = 1e-6


def cube(identifier: str = "cube", side: float = CUBE_SIDE) -> dict:
    return {"id": identifier, "type": "box",
            "parameters": {"x": side, "y": side, "z": side}}


def pin(identifier: str = "pin") -> dict:
    return {"id": identifier, "type": "cylinder",
            "parameters": {"diameter": PIN_DIAMETER, "height": PIN_HEIGHT,
                           "position": {"x": CUBE_SIDE + GAP
                                             + PIN_DIAMETER / 2.0,
                                        "y": CUBE_SIDE / 2.0, "z": 0}}}


def slab(identifier: str, x: float, y: float, z: float, at: float) -> dict:
    return {"id": identifier, "type": "box",
            "parameters": {"x": x, "y": y, "z": z,
                           "position": {"x": at, "y": 0.0, "z": 0.0}}}


def declare(identifier: str, target: str) -> dict:
    return {"id": identifier, "type": PART, "target": target}


def parsed(*operations: dict):
    return parse_plan_text(json.dumps(
        {"status": "generated", "summary": "bodies",
         "operations": list(operations)}))


def engines():
    """Every engine actually available here, so parity is real or skipped."""
    found = []
    for name in ("cadquery", "freecad"):
        try:
            engine = resolve_backend(name)
        except BackendUnavailable:  # pragma: no cover - environment
            continue
        if engine.available():
            found.append(engine)
    return found


def one_engine():
    found = engines()
    if not found:  # pragma: no cover - environment
        raise unittest.SkipTest("no CAD backend is available here")
    return found[0]


def shapes_of(engine, *operations: dict):
    """Build a plan and hand back the ordered (body_id, shape) pairs."""
    plan = parsed(*operations)
    verdict = validate_plan(plan)
    assert verdict.valid, [(p.code, p.message) for p in verdict.problems]
    result = execute_plan(plan, backend=engine)
    assert result.succeeded, result.failure
    return [(body.id, result.shapes.get(body.id)) for body in result.bodies]


class ACubeAndACylinderRoundTripTests(unittest.TestCase):
    """Case 1 of the brief: the canonical two-body example, on every engine.

    The gate `docs/multi-body-design.md` sets for step 5 is exactly this --
    "a round-trip read of the STEP returns two solids" -- so the assertion is
    the read, never the write.
    """

    def test_two_bodies_are_written_and_read_back_as_two_solids(self) -> None:
        checked = 0
        for engine in engines():
            with self.subTest(engine.name):
                bodies = shapes_of(engine, cube(), pin(),
                                   declare("d1", "cube"), declare("d2", "pin"))
                self.assertEqual([name for name, _ in bodies],
                                 ["cube", "pin"])
                with tempfile.TemporaryDirectory() as folder:
                    target = Path(folder) / "assembly.step"
                    engine.export_step_assembly(bodies, target)
                    back = engine.read_step_solids(target)
                    self.assertEqual(len(back), 2)
                    volumes = sorted(engine.measure(s).volume for s in back)
                    self.assertAlmostEqual(volumes[0], PIN_VOLUME,
                                           delta=TOLERANCE)
                    self.assertAlmostEqual(volumes[1], CUBE_VOLUME,
                                           delta=TOLERANCE)
                checked += 1
        if not checked:  # pragma: no cover - environment
            raise unittest.SkipTest("no CAD backend is available here")
        # The loop must have exercised a real engine. Without this a change
        # that made `engines()` return nothing would leave every assertion
        # here unrun and the test green.
        self.assertGreaterEqual(checked, 1)

    def test_nothing_is_fused(self) -> None:
        """The volumes must stay SEPARATE, not add up into one solid.

        A writer that fused would produce one solid of 73424.778 -- a
        perfectly valid STEP of a part that was never asked for.
        """
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "assembly.step"
            engine.export_step_assembly(bodies, target)
            back = engine.read_step_solids(target)
        self.assertEqual(len(back), 2)
        for solid in back:
            self.assertEqual(engine.measure(solid).solid_count, 1)

    def test_no_body_is_dropped(self) -> None:
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "assembly.step"
            engine.export_step_assembly(bodies, target)
            total = sum(engine.measure(s).volume
                        for s in engine.read_step_solids(target))
        self.assertAlmostEqual(total, CUBE_VOLUME + PIN_VOLUME,
                               delta=TOLERANCE)


class TwoArbitraryBodiesRoundTripTests(unittest.TestCase):
    """Case 2 of the brief: two non-intersecting bodies that are not the pair.

    A different shape and a different count, so nothing here can be passing
    on a constant that happens to match the canonical example.
    """

    def test_three_slabs_read_back_as_three_solids(self) -> None:
        engine = one_engine()
        bodies = shapes_of(
            engine,
            slab("a", 10.0, 20.0, 30.0, 0.0),
            slab("b", 5.0, 5.0, 5.0, 50.0),
            slab("c", 12.0, 3.0, 7.0, 100.0),
            declare("d1", "a"), declare("d2", "b"), declare("d3", "c"))
        expected = sorted([10.0 * 20 * 30, 5.0 ** 3, 12.0 * 3 * 7])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "three.step"
            engine.export_step_assembly(bodies, target)
            back = engine.read_step_solids(target)
        self.assertEqual(len(back), 3)
        volumes = sorted(engine.measure(s).volume for s in back)
        for measured, closed_form in zip(volumes, expected):
            self.assertAlmostEqual(measured, closed_form, delta=TOLERANCE)


class TheBodyIdsSurviveTests(unittest.TestCase):
    """Case 3 of the brief: identity, which no count can check.

    This is the failure that looks like success. Both engines will happily
    write a geometrically perfect two-solid STEP whose bodies are anonymous;
    every volume matches and the identity is gone.
    """

    def test_each_body_id_reaches_the_file(self) -> None:
        checked = 0
        for engine in engines():
            with self.subTest(engine.name):
                bodies = shapes_of(engine, cube("housing"), pin("spigot"),
                                   declare("d1", "housing"),
                                   declare("d2", "spigot"))
                with tempfile.TemporaryDirectory() as folder:
                    target = Path(folder) / "named.step"
                    engine.export_step_assembly(bodies, target)
                    written = target.read_text(errors="ignore")
                self.assertIn("housing", written)
                self.assertIn("spigot", written)
                checked += 1
        if not checked:  # pragma: no cover - environment
            raise unittest.SkipTest("no CAD backend is available here")
        self.assertGreaterEqual(checked, 1)

    def test_an_id_that_did_not_reach_the_file_fails_the_export(self) -> None:
        """The guard itself, driven directly.

        `verify_assembly` is handed a file that genuinely has the right
        number of solids and genuinely does not carry the name. That is the
        anonymous-compound failure, reproduced without having to make an
        engine produce it.
        """
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "ok.step"
            engine.export_step_assembly(bodies, target)
            # The same file, verified against ids it does not contain.
            renamed = [("widget", bodies[0][1]), ("gubbins", bodies[1][1])]
            with self.assertRaises(BackendOperationError) as caught:
                verify_assembly(engine, target, renamed)
        message = str(caught.exception)
        self.assertIn("widget", message)
        self.assertIn("told apart", message)


class TheWriterVerifiesWhatItWroteTests(unittest.TestCase):
    """A file that exists is not an export, and a count is how you know."""

    def test_a_wrong_solid_count_fails_the_export(self) -> None:
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "two.step"
            engine.export_step_assembly(bodies, target)
            # Two solids in the file, three bodies claimed.
            claimed = list(bodies) + [("third", bodies[0][1])]
            with self.assertRaises(BackendOperationError) as caught:
                verify_assembly(engine, target, claimed)
        self.assertIn("reads back as", str(caught.exception))

    def test_an_empty_body_list_is_refused(self) -> None:
        with self.assertRaises(BackendOperationError) as caught:
            ordered_bodies([])
        self.assertIn("at least one body", str(caught.exception))

    def test_two_bodies_cannot_share_an_id(self) -> None:
        """Case 4 of the brief: a malformed multi-body case.

        Two solids under one name is not an assembly whose bodies can be told
        apart, and the file would assert that they are the same body.
        """
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        clashing = [("same", bodies[0][1]), ("same", bodies[1][1])]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(BackendOperationError) as caught:
                engine.export_step_assembly(clashing,
                                            Path(folder) / "clash.step")
        self.assertIn("cannot share an id", str(caught.exception))

    def test_a_body_with_no_shape_is_refused(self) -> None:
        with self.assertRaises(BackendOperationError) as caught:
            ordered_bodies([("ghost", None)])
        self.assertIn("ghost", str(caught.exception))

    def test_the_format_is_never_silently_switched(self) -> None:
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(BackendOperationError):
                engine.export_step_assembly(bodies,
                                            Path(folder) / "part.iges")

    def test_the_caller_order_is_the_file_order(self) -> None:
        """Deterministic, so two runs of one plan write the same file."""
        engine = one_engine()
        bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        self.assertEqual([n for n, _ in ordered_bodies(bodies)],
                         ["cube", "pin"])
        reversed_pairs = list(reversed(bodies))
        self.assertEqual([n for n, _ in ordered_bodies(reversed_pairs)],
                         ["pin", "cube"])


class ASingleBodyExportIsUnchangedTests(unittest.TestCase):
    """Case 5 of the brief: the regression that matters most.

    The product's single-body STEP route still calls `export_step`, exactly
    as it did before Stage 74. This asserts the assembly writer AGREES with
    it on a one-body part, so the two cannot drift apart while the product
    keeps using the proven one.
    """

    def test_one_body_through_the_assembly_writer_is_one_solid(self) -> None:
        engine = one_engine()
        bodies = shapes_of(engine, cube("only"))
        self.assertEqual(len(bodies), 1)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "one.step"
            engine.export_step_assembly(bodies, target)
            back = engine.read_step_solids(target)
        self.assertEqual(len(back), 1)
        self.assertAlmostEqual(engine.measure(back[0]).volume, CUBE_VOLUME,
                               delta=TOLERANCE)

    def test_both_writers_agree_on_a_one_body_part(self) -> None:
        engine = one_engine()
        bodies = shapes_of(engine, cube("only"))
        with tempfile.TemporaryDirectory() as folder:
            plain = Path(folder) / "plain.step"
            assembled = Path(folder) / "assembled.step"
            engine.export_step(bodies[0][1], plain)
            engine.export_step_assembly(bodies, assembled)
            one = engine.read_step_solids(plain)
            other = engine.read_step_solids(assembled)
        self.assertEqual(len(one), len(other), 1)
        self.assertAlmostEqual(engine.measure(one[0]).volume,
                               engine.measure(other[0]).volume,
                               delta=TOLERANCE)

    def test_read_step_solids_handles_a_single_solid_file(self) -> None:
        """A one-solid STEP must report ONE, not zero.

        A reader that only explored compounds would report zero solids for
        every single-body export, which would make the verification fail on
        the case that has always worked.
        """
        engine = one_engine()
        bodies = shapes_of(engine, cube("only"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "plain.step"
            engine.export_step(bodies[0][1], target)
            self.assertEqual(len(engine.read_step_solids(target)), 1)


class TheTwoEnginesAgreeTests(unittest.TestCase):
    """Where the engines agree, and where the boundary honestly is."""

    def test_both_engines_write_a_file_the_other_can_read(self) -> None:
        """Cross-read, because a STEP only one engine understands is not one."""
        found = engines()
        if len(found) < 2:  # pragma: no cover - environment
            raise unittest.SkipTest("both engines are needed for parity")
        first, second = found
        bodies = shapes_of(first, cube(), pin(), declare("d1", "cube"),
                           declare("d2", "pin"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "cross.step"
            first.export_step_assembly(bodies, target)
            mine = sorted(first.measure(s).volume
                          for s in first.read_step_solids(target))
            theirs = sorted(second.measure(s).volume
                            for s in second.read_step_solids(target))
        self.assertEqual(len(mine), 2)
        self.assertEqual(len(theirs), 2)
        for a, b in zip(mine, theirs):
            self.assertAlmostEqual(a, b, delta=TOLERANCE)

    def test_both_engines_produce_the_same_solid_count_and_volumes(self) -> None:
        found = engines()
        if len(found) < 2:  # pragma: no cover - environment
            raise unittest.SkipTest("both engines are needed for parity")
        readings = []
        for engine in found:
            bodies = shapes_of(engine, cube(), pin(), declare("d1", "cube"),
                               declare("d2", "pin"))
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "asm.step"
                engine.export_step_assembly(bodies, target)
                readings.append(sorted(engine.measure(s).volume
                                       for s in engine.read_step_solids(target)))
        self.assertEqual(len(readings[0]), len(readings[1]))
        for a, b in zip(*readings):
            self.assertAlmostEqual(a, b, delta=TOLERANCE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
