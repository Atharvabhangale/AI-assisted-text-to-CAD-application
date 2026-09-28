"""Mutation test: every Stage 78 guard must go RED when its defect returns.

A guard no wrong answer can trip is worse than no guard, because it reports
a rate. Each mutant below disables exactly one criterion and the focused
suite is re-run against it; a mutant that SURVIVES means nothing in the
suite depends on that criterion, which is a gap in the tests and not noise.

Two of these are not invented. They are the defects this stage's own
instrument actually had, caught by the offline re-grade before any live
call was spent:

* `p_code_is_fatal` -- `evaluate78` read Stage 77's informational
  `P:export_identity_unproven` as a failure, which scored every one of
  CR-06's 32 calls as a loss;
* `flag_any_shared_coordinate` -- it flagged an axis merely SHARING a
  coordinate with a neighbour it stood clear of, which scored five correct
  Stage 77 calls as failures.

Both are mutants now so they cannot come back quietly.

Every mutated file is restored from memory at the end, including on a
crash, and byte-identity is asserted before the run exits.

    cd <repo>/docs/evaluation-baselines/stage78-axis-extent
    PYTHONPATH=. python3 mutation_test_78.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
TESTS = REPO / "apps" / "api" / "tests_experimental"
MODULE = "test_stage78_axis_extent"

EVAL = HERE / "evaluate78.py"
TRUTH = HERE / "ground_truth78.py"
RULE = HERE / "decision_rule_78.py"
REGRADE = HERE / "regrade78.py"
VARIANTS = HERE / "variants78.py"

ORIGINALS = {p: p.read_text(encoding="utf-8")
             for p in (EVAL, TRUTH, RULE, VARIANTS, REGRADE)}

#: ABSOLUTE, and built here. The caller's PYTHONPATH is relative to
#: wherever they stood, and the child runs in `tests_experimental`.
CHILD_PATH = os.pathsep.join(str(p) for p in (
    REPO / "packages" / "cad-core" / "src",
    REPO / "apps" / "api" / "src",
    TESTS, HERE,
))

# (name, file, find, replace) -- each disables ONE criterion.
MUTANTS = [
    # --- the two real defects ------------------------------------------
    ("p_code_is_fatal", REGRADE,
     '    return not any(code in G77.FAILURE_CODES for code in turn["codes"])',
     '    return not turn["codes"]'),
    ("flag_any_shared_coordinate", EVAL,
     '            if not overlaps(cylinder, other):\n                continue',
     '            if False:\n                continue'),

    # --- the axis/extent distinction itself -----------------------------
    ("min_extent_counts_as_the_axis", TRUTH,
     '    if close(observed, axis):\n        return AT_AXIS',
     '    if close(observed, axis) or close(observed, minimum):\n        return AT_AXIS'),
    ("max_extent_counts_as_the_axis", TRUTH,
     '    if close(observed, minimum):\n        return AT_MIN_EXTENT',
     '    if close(observed, minimum) or close(observed, maximum):\n        return AT_MIN_EXTENT'),
    ("a_missing_coordinate_is_the_axis", TRUTH,
     '    if observed is None:\n        return AT_OTHER',
     '    if observed is None:\n        return AT_AXIS'),

    # --- only one axis checked -------------------------------------------
    ("only_x_is_checked", EVAL,
     '        "bore_y_on_axis": seen["bore_y_is"] == G78.AT_AXIS,',
     '        "bore_y_on_axis": True,'),
    ("only_y_is_checked", EVAL,
     '        "bore_x_on_axis": seen["bore_x_is"] == G78.AT_AXIS,',
     '        "bore_x_on_axis": True,'),

    # --- the other ED-02 criteria ----------------------------------------
    ("body_may_move_instead", EVAL,
     '        checks["body_did_not_move"] = all(',
     '        checks["body_did_not_move"] = True or all('),
    ("target_is_not_checked", EVAL,
     '        "bore_targets_the_pin": seen["bore_target"] == truth["feature_body"],',
     '        "bore_targets_the_pin": True,'),
    ("direction_is_not_checked", EVAL,
     '        "bore_runs_along_the_body_axis":\n            seen["bore_axis"] == truth["feature_axis_direction"],',
     '        "bore_runs_along_the_body_axis": True,'),
    ("a_missing_bore_is_fine", EVAL,
     '        "bore_present": bool(seen["found_a_bore"]),',
     '        "bore_present": True,'),

    # --- CR-06 -----------------------------------------------------------
    ("overlap_is_not_checked", EVAL,
     '        "bodies_disjoint": not seen["interpenetrating_pairs"],',
     '        "bodies_disjoint": True,'),
    ("body_count_is_not_checked", EVAL,
     '        "three_solids": seen["body_count"] == G78.axis_truth("CR-06")["bodies"],',
     '        "three_solids": True,'),

    # --- the layering: Stage 78 must be STRICTLY stronger than Stage 77 ---
    ("stage77_failure_can_pass", EVAL,
     '        "stage78_strict": bool(stage77_strict and graded["axis_ok"]),',
     '        "stage78_strict": bool(graded["axis_ok"]),'),
    ("any_check_passing_is_enough", EVAL,
     '        "axis_ok": all(checks.values()),\n    }\n\n\n# --------------------------------------------------------------- CR-06',
     '        "axis_ok": any(checks.values()),\n    }\n\n\n# --------------------------------------------------------------- CR-06'),

    # --- the truth must not come from the output --------------------------
    # A REAL "truth from the output" mutant: take the landmarks from the
    # cylinder the MODEL wrote instead of from the corpus. Any bore that
    # matches the model's own cylinder then passes -- including one that
    # moved the body -- which is precisely Stage 67's defect.
    ("truth_read_from_the_plan", EVAL,
     '    for axis_letter in ("x", "y"):\n        marks = landmarks[axis_letter]',
     '    for axis_letter in ("x", "y"):\n        marks = dict(landmarks[axis_letter])\n'
     '        if cylinder is not None:\n'
     '            marks["axis"] = _position(cylinder).get(axis_letter, marks["axis"])'),

    # --- the decision rule ------------------------------------------------
    ("confirmation_n_back_to_32", RULE,
     'MIN_CONFIRMATION_CALLS_PER_CASE: Final[int] = 128',
     'MIN_CONFIRMATION_CALLS_PER_CASE: Final[int] = 32'),
    ("exploration_may_adopt", RULE,
     '    if not is_confirmation:',
     '    if False:'),
    ("mechanism_need_not_fall", RULE,
     '               and c["mechanism_fell_enough"]]',
     '               ]'),
    ("regressions_are_ignored", RULE,
     '    if regressions:\n        reasons.append(',
     '    if False:\n        reasons.append('),
    ("a_tiny_improvement_adopts", RULE,
     'MIN_ABSOLUTE_IMPROVEMENT: Final[float] = 0.125',
     'MIN_ABSOLUTE_IMPROVEMENT: Final[float] = 0.0'),

    # --- the arms ---------------------------------------------------------
    ("order_control_is_not_a_reordering", VARIANTS,
     "same two numbers, always.\n\n  {\"id\": \"shaft\"",
     "same two numbers, always. Extra words here.\n\n  {\"id\": \"shaft\""),
]


def suite() -> tuple:
    environment = dict(os.environ, PYTHONPATH=CHILD_PATH)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", MODULE],
        cwd=TESTS, capture_output=True, text=True, env=environment)
    return result.returncode, result.stdout + result.stderr


def main() -> int:
    code, out = suite()
    if code != 0:
        print("the suite must be GREEN before mutating\n" + out[-3000:])
        return 1
    print("baseline: GREEN\n")

    killed, survived = 0, []
    try:
        for name, path, find, replace in MUTANTS:
            source = ORIGINALS[path]
            if source.count(find) != 1:
                survived.append(f"{name} (anchor matched "
                                f"{source.count(find)} times -- STALE)")
                print(f"  STALE     {name}")
                continue
            path.write_text(source.replace(find, replace), encoding="utf-8")
            code, _ = suite()
            path.write_text(source, encoding="utf-8")
            if code == 0:
                survived.append(name)
                print(f"  SURVIVED  {name}")
            else:
                killed += 1
                print(f"  killed    {name}")
    finally:
        for path, source in ORIGINALS.items():
            path.write_text(source, encoding="utf-8")

    for path, source in ORIGINALS.items():
        assert path.read_text(encoding="utf-8") == source, \
            f"{path.name} was not restored"

    print(f"\n{killed}/{len(MUTANTS)} killed; every module restored "
          f"byte-identical")
    if survived:
        print("SURVIVORS (a gap in the tests, not noise):")
        for name in survived:
            print(f"  {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
