"""Mutation test: every criterion in `evaluate75.py` must go RED when it stops.

A guard that no wrong answer can trip is worse than no guard, because it
reports a rate. So each mutant below disables exactly one criterion and the
focused suite is re-run against it; a mutant that SURVIVES means nothing in
the suite depends on that criterion, which is a gap in the tests, not noise.

**Result on the committed tree: 18/18 killed.** Two survived the first sweep
and were real gaps, both now closed:

* `total_volume_only` -- two disjoint solids fused have *exactly* the total
  volume of the two apart, so a grader that adds them up scores the single
  most important multi-body failure as a pass. Killed by
  `test_the_right_total_split_between_the_wrong_bodies_fails`.
* `accept_a_guess_as_a_refusal` -- every other signal a refusal case reads
  looks like a clean refusal when a guess produces an invalid plan. Killed by
  `test_a_guess_that_happened_not_to_build_is_still_not_a_refusal`.

`evaluate75.py` is restored from memory at the end, including on a crash, and
the run asserts byte-identity before it exits. Nothing here calls a model.

    cd <repo>/apps/api
    PYTHONPATH=../../packages/cad-core/src:src:tests_experimental \
      python3 ../../docs/evaluation-baselines/stage75-multibody/mutation_test.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TARGET = HERE / "evaluate75.py"
TESTS = ROOT / "apps/api/tests_experimental"
MODULE = "test_stage75_multibody_evaluator"

TRUTH = HERE / "ground_truth75.py"

ORIGINAL = TARGET.read_text(encoding="utf-8")
TRUTH_ORIGINAL = TRUTH.read_text(encoding="utf-8")

# Multi-line anchors, named so the MUTANTS table stays readable.
OBSERVER_QUESTIONS = (
    '        "questions": (\n'
    '            list(getattr(plan, "questions", ()) or ()) if plan is not None\n'
    "            else None\n"
    "        ),"
)
QUESTION_ADDRESSED = (
    '            checks["question_addressed_the_request"] = all(\n'
    "                token.lower() in said\n"
    '                for token in truth["refusal_question_must_mention"]\n'
    "            )"
)
BORE_FITS = (
    '        checks["bore_fits_the_body"] = bool(drilled) and all(\n'
    "            sum(1 for axis in range(3)\n"
    '                if (b["maximum"][axis] - b["minimum"][axis]) > bore) >= 2\n'
    "            for b in drilled\n"
    "        )"
)

#: One mutant lives in the corpus, not the evaluator: the guard that stops a
#: retired case being scored again. Applied separately because it patches a
#: different file.
RETIRED_MUTANT = (
    "score_a_retired_case_again",
    (
        "    if case.retired:\n"
        "        raise ValueError(\n"
        '            f"{case_name} is RETIRED and must not be scored again: "\n'
        '            f"{case.retired}"\n'
        "        )"
    ),
    "    if False:\n        raise ValueError(case_name)",
)

#: (name, the text to replace, what to replace it with). Each disables ONE
#: criterion; none of them is a plausible refactor, which is the point -- a
#: mutant is a defect deliberately reintroduced.
MUTANTS = [
    ("total_volume_only",
     '''    if truth["volumes"] is not None:
        checks["volumes"] = _match_multiset(
            [b["volume"] for b in bodies], truth["volumes"]
        )''',
     '''    if truth["volumes"] is not None:
        checks["volumes"] = _close(
            sum(b["volume"] for b in bodies), sum(truth["volumes"])
        )'''),
    ("grade_the_first_body_only",
     '''    if truth["volumes"] is not None:
        checks["volumes"] = _match_multiset(
            [b["volume"] for b in bodies], truth["volumes"]
        )''',
     '''    if truth["volumes"] is not None:
        checks["volumes"] = bool(bodies) and _close(
            bodies[0]["volume"], truth["volumes"][0]
        )'''),
    ("ignore_body_count",
     '    checks["body_count"] = observation["body_count"] == truth["bodies"]',
     '    checks["body_count"] = True'),
    ("ignore_declaration",
     '''        checks["declared_every_body"] = (
            len(declared) == truth["bodies"]
            and len(set(declared)) == len(declared)
            and {b["id"] for b in bodies} == set(declared)
        )''',
     '        checks["declared_every_body"] = True'),
    ("ignore_spurious_declaration",
     '        checks["declared_nothing_spurious"] = len(declared) == 0',
     '        checks["declared_nothing_spurious"] = True'),
    ("ignore_duplicate_declaration",
     (
         "    if len(declared) != len(set(declared)):\n"
         "        codes.append(G.B_EXTRA_BODY)"
     ),
     "    if False:\n        codes.append(G.B_EXTRA_BODY)"),
    ("ignore_body_ids",
     '''        checks["body_ids"] = (
            {b["id"] for b in bodies} == set(truth["body_ids"])
        )''',
     '        checks["body_ids"] = True'),
    ("ignore_disjointness",
     '''        checks["bodies_disjoint"] = not any(
            _overlaps(a, b)
            for i, a in enumerate(bodies) for b in bodies[i + 1:]
        )''',
     '        checks["bodies_disjoint"] = True'),
    ("ignore_edited_body",
     '''        checks["edited_body"] = any(
            _close(v, truth["edited_body_volume"]) for v in vols
        )''',
     '        checks["edited_body"] = True'),
    ("ignore_edit_isolation",
     '''        checks["other_body_untouched"] = any(
            _close(v, truth["unchanged_body_volume"]) for v in vols
        )''',
     '        checks["other_body_untouched"] = True'),
    ("ignore_untouched_topology",
     '        checks["untouched_body_intact"] = want in faces',
     '        checks["untouched_body_intact"] = True'),
    ("ignore_prismatic",
     '''        checks["both_bodies_prismatic"] = all(
            b["face_count"] == G.BOX_FACES for b in bodies
        )''',
     '        checks["both_bodies_prismatic"] = True'),
    ("ignore_extents",
     '''        checks["stated_extents_present"] = all(
            any(any(math.isclose(e, w, rel_tol=1e-9) for e in triple)
                for triple in extents)
            for w in wanted
        )''',
     '        checks["stated_extents_present"] = True'),
    ("ignore_refusal_naming",
     '        checks["named_the_bodies"] = named',
     '        checks["named_the_bodies"] = True'),
    ("accept_a_guess_as_a_refusal",
     '''        declined = observation["outcome_declared"] in (
            "needs_clarification", "unsupported",
        )''',
     '        declined = True'),
    ("call_every_shortfall_a_fusion",
     '''        codes.append(
            G.H_UNWANTED_FUSION if "union" in observation["operation_types"]
            else G.A_MISSING_BODY
        )''',
     '        codes.append(G.H_UNWANTED_FUSION)'),
    ("any_check_passing_is_success",
     '        "strict_success": all(applicable),',
     '        "strict_success": any(applicable),'),
    ("tolerance_is_one_percent",
     '    return abs(measured - truth) / abs(truth) <= G.VOLUME_TOLERANCE',
     '    return abs(measured - truth) / abs(truth) <= 0.5'),
    # ---------------------------------------------------- Phase B guards
    #
    # The first three reinstate the Phase A observer bug in its three
    # separate forms. Each is what the code actually did, not an invented
    # defect, and each must be caught.
    ("phase_a_read_questions_off_the_result",
     OBSERVER_QUESTIONS,
     '        "questions": list(getattr(generation, "questions", ()) or ()),'),
    ("empty_questions_indistinguishable_from_no_plan",
     OBSERVER_QUESTIONS,
     '        "questions": list(getattr(plan, "questions", ()) or ()),'),
    ("phase_a_merged_the_systems_words_into_the_models",
     '    for key in ("summary", "plan_reason"):',
     '    for key in ("summary", "plan_reason", "system_error"):'),
    ("ignore_the_question_field",
     '        checks["asked_a_question"] = bool(observation["questions"])',
     '        checks["asked_a_question"] = True'),
    ("let_a_clarification_carry_operations",
     '        checks["emitted_no_operations"] = observation["operation_count"] == 0',
     '        checks["emitted_no_operations"] = True'),
    ("ignore_whether_the_question_addressed_the_request",
     QUESTION_ADDRESSED,
     '            checks["question_addressed_the_request"] = True'),
    ("ignore_bore_coherence",
     BORE_FITS,
     '        checks["bore_fits_the_body"] = True'),
    ("call_a_failed_build_an_unwanted_fusion",
     '    if not observation["execution_succeeded"]:',
     '    if False:'),
]



#: ABSOLUTE, and built here rather than inherited. The caller's PYTHONPATH is
#: relative to `apps/api`, and the child runs in `tests_experimental`, so
#: inheriting it silently loses every path -- the same class of trap as the
#: separator one in CLAUDE.md section 15, and it cost a run here.
CHILD_PATH = os.pathsep.join(str(p) for p in (
    ROOT / "packages/cad-core/src", ROOT / "apps/api/src", TESTS,
))


def suite() -> tuple:
    environment = dict(os.environ, PYTHONPATH=CHILD_PATH)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", MODULE],
        cwd=TESTS, capture_output=True, text=True, env=environment,
    )
    return result.returncode, result.stdout + result.stderr


def main() -> int:
    code, out = suite()
    if code != 0:
        print("the suite must be GREEN before mutating\n" + out)
        return 1
    print("baseline: GREEN\n")

    killed, survived = 0, []
    try:
        for name, old, new in MUTANTS:
            if ORIGINAL.count(old) != 1:
                survived.append(f"{name} (anchor matched "
                                f"{ORIGINAL.count(old)} times -- STALE)")
                print(f"  STALE     {name}")
                continue
            TARGET.write_text(ORIGINAL.replace(old, new), encoding="utf-8")
            code, _ = suite()
            if code == 0:
                survived.append(name)
                print(f"  SURVIVED  {name}")
            else:
                killed += 1
                print(f"  killed    {name}")
        name, old, new = RETIRED_MUTANT
        if TRUTH_ORIGINAL.count(old) != 1:
            survived.append(f"{name} (anchor STALE)")
            print(f"  STALE     {name}")
        else:
            TRUTH.write_text(TRUTH_ORIGINAL.replace(old, new), encoding="utf-8")
            code, _ = suite()
            if code == 0:
                survived.append(name)
                print(f"  SURVIVED  {name}")
            else:
                killed += 1
                print(f"  killed    {name}")
    finally:
        TARGET.write_text(ORIGINAL, encoding="utf-8")
        TRUTH.write_text(TRUTH_ORIGINAL, encoding="utf-8")

    assert TARGET.read_text(encoding="utf-8") == ORIGINAL, \
        "evaluate75.py was not restored"
    assert TRUTH.read_text(encoding="utf-8") == TRUTH_ORIGINAL, \
        "ground_truth75.py was not restored"
    print(f"\n{killed}/{len(MUTANTS) + 1} killed; both modules restored "
          f"byte-identical")
    if survived:
        print("SURVIVORS (a gap in the tests, not noise):")
        for name in survived:
            print(f"  {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
