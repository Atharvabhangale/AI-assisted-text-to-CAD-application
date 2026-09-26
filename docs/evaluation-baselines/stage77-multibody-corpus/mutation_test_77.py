"""Does Stage 77's corpus and grader actually bite? Break one guard, watch.

    python3 mutation_test_77.py            # the whole sweep
    python3 mutation_test_77.py --list     # what it would do, changing nothing

A suite that passes is evidence the code passes it, not that its guards
work. The only way to find out is to break the thing a guard protects.
Stage 75's first sweep found two survivors and both were real gaps; Stage
76's found six and every one was a weak TEST rather than a missing guard.

Each mutant disables exactly ONE guard and names the test that must fail. A
mutant nothing catches is a guard that does not exist.

**No file is left modified.** Every edit is applied to a copy of the
original text and restored in a `finally`, and the driver refuses an edit
whose anchor is not present exactly once -- a mutant that silently no-ops
survives by accident otherwise, which happened once in Stage 75 Phase D.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TESTS = REPO / "apps" / "api" / "tests_experimental"
SUITE = "test_stage77_corpus"

TRUTH = HERE / "ground_truth77.py"
EVAL = HERE / "evaluate77.py"
FIX = HERE / "fixtures77.py"
ARENA = HERE / "arena77.py"
TESTFILE = TESTS / f"{SUITE}.py"

Mutant = Tuple[str, Path, str, str, Tuple[str, ...]]

MUTANTS: List[Mutant] = [
    # ---------------------------------------------- grading by position
    (
        "the part is graded by its FIRST body",
        EVAL,
        '''    return {str(b["id"]): b for b in bodies}''',
        '''    return {str(b["id"]): b for b in bodies[:1]}''',
        ("test_the_control_passes", "test_identity_is_not_taken_from_order"),
    ),
    (
        "bodies are paired with expectations by ORDER",
        EVAL,
        '''    remaining = list(measured)
    for want in truth:
        hit = next((i for i, got in enumerate(remaining)
                    if _close(got, want, tolerance)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True''',
        '''    return all(_close(got, want, tolerance)
               for got, want in zip(measured, truth))''',
        ("test_identity_is_not_taken_from_order",),
    ),
    # ------------------------------------- total instead of per body
    (
        "per-body volumes are compared as a TOTAL",
        EVAL,
        '''        checks["volumes"] = _match_multiset(
            [b["volume"] for b in bodies], turn["volumes"], tolerance)''',
        '''        checks["volumes"] = _close(
            sum(b["volume"] for b in bodies), sum(turn["volumes"]),
            tolerance)''',
        ("test_a_right_total_at_the_right_count_is_not_a_right_split",),
    ),
    (
        "a body count that is too small is not noticed",
        EVAL,
        '''    checks["body_count"] = observation["body_count"] == turn["bodies"]''',
        '''    checks["body_count"] = observation["body_count"] <= turn["bodies"]''',
        ("test_a_missing_body_is_caught",),
    ),
    (
        "the per-ID volume pairing is not checked",
        EVAL,
        '''        checks["body_volumes"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["body_volumes"].items()
        )''',
        '''        checks["body_volumes"] = True''',
        ("test_a_named_case_catches_swapped_bodies",),
    ),
    (
        "disjointness is not checked",
        EVAL,
        '''        checks["bodies_disjoint"] = not any(
            _overlaps(a, b)
            for i, a in enumerate(bodies) for b in bodies[i + 1:])''',
        '''        checks["bodies_disjoint"] = True''',
        ("test_bodies_that_interpenetrate_are_caught",),
    ),
    (
        "the declaration is not checked",
        EVAL,
        '''        checks["declared_every_body"] = (
            len(declared) == turn["bodies"]
            and len(set(declared)) == len(declared)
            and set(by_id) == set(declared)
        )''',
        '''        checks["declared_every_body"] = True''',
        ("test_undeclared_bodies_are_caught",),
    ),
    (
        "placements are not checked, per id",
        EVAL,
        '''            checks["placements"] = all(
                body in by_id
                and _box_matches(by_id[body], low, high,
                                 case["placement_tolerance"])
                for body, (low, high) in places.items()
            )''',
        '''            checks["placements"] = True''',
        ("test_a_named_body_in_the_wrong_place_is_caught",),
    ),
    (
        "placements are not checked, as an unordered set",
        EVAL,
        '''            checks["placements"] = matched and not remaining''',
        '''            checks["placements"] = True''',
        ("test_the_right_bodies_in_the_wrong_place_are_caught",),
    ),
    # --------------------------------------------------- the edit half
    (
        "the OTHER bodies are not checked, so a cross-body edit passes",
        EVAL,
        '''        checks["other_bodies_untouched"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["unchanged"].items())''',
        '''        checks["other_bodies_untouched"] = True''',
        ("test_a_cross_body_edit_is_caught",),
    ),
    (
        "the edit target is not checked, so editing the wrong body passes",
        EVAL,
        '''        checks["edit_target_changed"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["changed"].items())''',
        '''        checks["edit_target_changed"] = True''',
        ("test_editing_the_wrong_body_is_caught",
         "test_doing_nothing_at_all_is_caught"),
    ),
    (
        "a renamed body is not noticed",
        EVAL,
        '''        checks["body_ids_preserved"] = (
            set(by_id) == set(G.FIXTURE_BODIES[case["fixture"]]))''',
        '''        checks["body_ids_preserved"] = True''',
        ("test_renaming_a_body_is_caught",),
    ),
    # ---------------------------------------------------- the refusal
    (
        "a refusal is not asked whether it refused",
        EVAL,
        '''    checks["refused"] = observation["outcome_declared"] in (
        "needs_clarification", "unsupported")''',
        '''    checks["refused"] = True''',
        ("test_a_refusal_that_did_not_refuse_is_caught",),
    ),
    (
        "a clarification need not ask anything",
        EVAL,
        '''    checks["asked_a_question"] = bool(observation["questions"])''',
        '''    checks["asked_a_question"] = True''',
        ("test_a_clarification_that_asked_nothing_is_caught",),
    ),
    (
        "a refusal need not name the bodies",
        EVAL,
        '''    checks["named_the_bodies"] = all(
        body.lower() in said for body in case["refusal_must_name"])''',
        '''    checks["named_the_bodies"] = True''',
        ("test_naming_one_body_is_not_an_answer",),
    ),
    (
        "the SYSTEM's own sentence satisfies the naming check",
        EVAL,
        '''    for key in ("summary", "plan_reason"):''',
        '''    for key in ("summary", "plan_reason", "system_error"):''',
        ("test_the_systems_own_sentence_cannot_satisfy_the_naming_check",),
    ),
    (
        "the operations count is read from the PARSED plan",
        EVAL,
        '''    written = observation.get("model_operation_count",
                              observation["operation_count"])
    checks["emitted_no_operations"] = (
        None if case["operations_permitted"] else written == 0)''',
        '''    checks["emitted_no_operations"] = (
        None if case["operations_permitted"]
        else observation["operation_count"] == 0)''',
        ("test_a_clarification_carrying_geometry_is_caught",),
    ),
    (
        "a refusal may build geometry",
        EVAL,
        '''    checks["built_nothing"] = observation["body_count"] == 0''',
        '''    checks["built_nothing"] = True''',
        ("test_refusing_in_words_while_building_is_caught",),
    ),
    (
        "the user's own noun need not be addressed",
        EVAL,
        '''        checks["addressed_the_request"] = all(
            token.lower() in said for token in case["refusal_must_mention"])''',
        '''        checks["addressed_the_request"] = True''',
        ("test_the_missing_noun_is_required_where_the_case_pins_it",),
    ),
    # ------------------------------------------------ what may score
    (
        "a DETERMINISTIC answer counts as a model success",
        EVAL,
        '''    checks["model_generated"] = (
        observation["source"] in G.COUNTS_AS_MODEL_EVIDENCE)''',
        '''    checks["model_generated"] = True''',
        ("test_a_deterministic_answer_is_never_a_model_success",
         "test_a_deterministic_reference_can_never_score"),
    ),
    (
        "outcome_label falls through instead of failing closed",
        EVAL,
        '''    source = observation.get("source")
    if source not in G.OUTCOMES:
        return G.PROVIDER_ERROR
    if source != G.MODEL_GENERATED:
        return source''',
        '''    source = observation.get("source")''',
        ("test_a_deterministic_answer_is_never_a_model_success",),
    ),
    # --------------------------------------- post-conditions vs blame
    (
        "a part that never built is recorded as a MEASUREMENT failure",
        EVAL,
        '''    if not observation["execution_succeeded"] or not observation["bodies"]:
        return {"verdict": NOT_ASSESSED,
                "why": "nothing was built, so there was nothing to measure"}''',
        '''    if not observation["execution_succeeded"] or not observation["bodies"]:
        return {"verdict": "assessed", "checks": {"built": False},
                "passed": False}''',
        ("test_a_part_that_did_not_build_is_not_a_measurement_failure",
         "test_not_assessed_is_counted_apart_from_failed"),
    ),
    (
        "the summary produces a combined rate",
        EVAL,
        '''        "combined_rate": None,''',
        '''        "combined_rate": 1.0,''',
        ("test_there_is_no_combined_rate",),
    ),
    # --------------------------------------------------- the corpus
    (
        "the corpus quietly loses its five-body case",
        TRUTH,
        '''        "CR-10", CREATION, family="five bodies",''',
        '''        "CR-10", CREATION, family="five bodies", retired="mutant",''',
        ("test_something_declares_more_than_two_bodies",),
    ),
    (
        "the corpus collapses back to one geometric family",
        TRUTH,
        '''        "CR-03", CREATION, family="cylinder+cylinder",''',
        '''        "CR-03", CREATION, family="box+cylinder",''',
        ("test_the_geometric_families_are_not_all_one_shape",),
    ),
    (
        "the two identical cubes are given different sizes",
        TRUTH,
        '''            body_volumes={"left": CUBE_30, "right": CUBE_30},''',
        '''            body_volumes={"left": CUBE_30, "right": CUBE_20},''',
        ("test_a_case_has_two_bodies_of_identical_dimension",),
    ),
    (
        "a creation case is allowed to start from a fixture",
        TRUTH,
        '''        if group == CREATION and fixture is not None:''',
        '''        if False:''',
        ("test_a_creation_case_may_not_carry_a_fixture",),
    ),
    (
        "a refusal case may permit operations",
        TRUTH,
        '''            if operations_permitted:''',
        '''            if False:''',
        ("test_a_refusal_may_not_permit_operations",),
    ),
    (
        "the informational export code is counted as a failure",
        TRUTH,
        '''INFORMATIONAL: Final[Tuple[str, ...]] = (P_EXPORT_IDENTITY_UNPROVEN,)''',
        '''INFORMATIONAL: Final[Tuple[str, ...]] = ()''',
        ("test_an_informational_code_is_not_a_failure",),
    ),
    (
        "the corpus's recorded identity drifts from the live route",
        TRUTH,
        '''PROMPT_CHARACTERS: Final[int] = 34036''',
        '''PROMPT_CHARACTERS: Final[int] = 33759''',
        ("test_the_identity_is_checked_against_the_live_route",),
    ),
    # ----------------------------------------------------- the fixture
    (
        "the fixture's pin moves back where a grown cube would hit it",
        FIX,
        '''        cylinder("pin", 20.0, 30.0, {"x": 100.0, "y": 20.0, "z": 0.0}),''',
        '''        cylinder("pin", 20.0, 30.0, {"x": 60.0, "y": 20.0, "z": 0.0}),''',
        ("test_the_fixture_survives_the_edits_it_is_used_for",),
    ),
    # ------------------------------------------------------- the arena
    (
        "the arena runs without --live",
        ARENA,
        '''    if not args.live:''',
        '''    if False:''',
        ("test_live_is_required",),
    ),
    (
        "the arena may overwrite an earlier baseline",
        ARENA,
        '''    for name in PROTECTED:
        if name in resolved.parts:''',
        '''    for name in []:
        if name in resolved.parts:''',
        ("test_the_arena_refuses_to_write_into_an_earlier_baseline",),
    ),
]


def _environment() -> dict:
    env = dict(os.environ)
    # ABSOLUTE, and built here rather than inherited: a relative PYTHONPATH
    # resolves against the child's working directory.
    env["PYTHONPATH"] = os.pathsep.join([
        str(REPO / "packages" / "cad-core" / "src"),
        str(REPO / "apps" / "api" / "src"),
        str(TESTS),
    ])
    return env


_FAILED = re.compile(r"^(?:FAIL|ERROR): (\w+)", re.M)


def _run() -> Tuple[int, set]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", SUITE],
        cwd=str(TESTS), env=_environment(), capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    return proc.returncode, set(_FAILED.findall(output))


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for index, (description, path, _, _, expect) in enumerate(MUTANTS, 1):
            print(f"{index:2}. [{path.name}] {description}")
            print(f"    must fail: {', '.join(expect)}")
        print(f"\n{len(MUTANTS)} mutants")
        return 0

    code, failed = _run()
    if code != 0:
        print("the suite is RED before any mutation; fix that first")
        print(sorted(failed))
        return 1
    print(f"baseline GREEN; {len(MUTANTS)} mutants\n")

    survivors = []
    for index, (description, path, old, new, expect) in enumerate(MUTANTS, 1):
        backup = path.read_text(encoding="utf-8")
        occurrences = backup.count(old)
        if occurrences != 1:
            print(f"{index:2}. SKIPPED (anchor appears {occurrences} times) "
                  f"-- {description}")
            survivors.append((index, description, "anchor"))
            continue
        try:
            path.write_text(backup.replace(old, new, 1), encoding="utf-8")
            code, failed = _run()
        finally:
            path.write_text(backup, encoding="utf-8")
        caught = code != 0 and all(name in failed for name in expect)
        print(f"{index:2}. {'CAUGHT ' if caught else 'SURVIVED'} "
              f"[{path.name}] {description}")
        if not caught:
            print(f"     expected {sorted(expect)}, got {sorted(failed)}")
            survivors.append((index, description, sorted(failed)))

    print(f"\n{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} caught")
    if survivors:
        print("SURVIVORS -- each is a guard that does not exist:")
        for index, description, got in survivors:
            print(f"  {index:2}. {description} -> {got}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
