"""Does Stage 76's observer actually bite? Break one guard, expect a failure.

    python3 mutation_test_76.py            # the whole sweep
    python3 mutation_test_76.py --list     # what it would do, changing nothing

A test suite that passes is not evidence that its guards work; it is
evidence that the code passes them. The only way to find out whether a
guard bites is to break the thing it guards and watch. Stage 75's sweep
found two survivors on its first run and both were real gaps, not noise --
a grader that compares TOTAL volume instead of per-body volumes, and one
that stops asking whether the model declined.

Each mutant below disables exactly ONE guard and names the test that must
fail. A mutant nothing catches is a guard that does not exist.

**No file is left modified.** Every edit is applied to a copy of the
original text, the suite is run, and the original is restored in a
`finally`. The driver refuses to apply an edit whose `old` text is not
present exactly once, so a mutant that silently no-ops -- which happened in
Phase D, where an anchor comment had been reworded -- fails loudly instead
of surviving.
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
SUITE = "test_stage76_observation"

OBSERVE = HERE / "observe76.py"
EVALUATE = HERE / "evaluate76.py"
TRUTH = HERE / "ground_truth76.py"
TESTFILE = TESTS / f"{SUITE}.py"
BROWSER = HERE / "browser76.py"

#: (description, file, old, new, tests that must fail)
Mutant = Tuple[str, Path, str, str, Tuple[str, ...]]

MUTANTS: List[Mutant] = [
    # ---------------------------------------------------- the observer
    (
        "the part measurement is read as the FIRST body",
        OBSERVE,
        '''    only = getattr(execution, "part", None)
    if only is None:
        return {}
    return bodies_by_id(execution).get(str(only), {})''',
        '''    rows = bodies_by_id(execution)
    return next(iter(rows.values()), {})''',
        ("test_the_part_measurement_comes_from_the_canonical_part",
         "test_T1_the_first_body_is_not_the_part"),
    ),
    (
        "the body an answer is about is inferred from its VALUE",
        OBSERVE,
        '''        row["about"] = label if (label and not scope.aggregate) else None''',
        '''        row["about"] = next(
            (body for body, measured in per_body.items()
             if abs(float(measured.get("volume") or 0.0)
                    - (_first_number(found.text, label) or -1.0)) < 1.0),
            None) if (label and not scope.aggregate) else None''',
        ("test_the_body_is_read_from_the_scope_never_from_the_value",),
    ),
    (
        "the label is not stripped before the number is read "
        "(the defect the offline gate caught)",
        OBSERVE,
        '''    if label and sentence.startswith(f"{label}: "):
        sentence = sentence[len(label) + 2:]''',
        '''    pass''',
        ("test_every_case_passes_on_every_available_engine",
         "test_the_trap_fixtures_are_one_edit_from_a_real_observation"),
    ),
    (
        "body names are found by SUBSTRING, as the product does",
        OBSERVE,
        '''        present = set(products)
        row["names_found"] = [n for n, _ in ordered if n in present]
        row["names_missing"] = [n for n, _ in ordered if n not in present]''',
        '''        row["names_found"] = [n for n, _ in ordered if n in text]
        row["names_missing"] = [n for n, _ in ordered if n not in text]''',
        ("test_a_body_name_is_looked_for_as_a_product_not_a_substring",),
    ),
    (
        "the answer is never checked for carrying its body's name",
        OBSERVE,
        '''        row["label_is_prefix"] = (
            found.text.startswith(f"{label}: ") if label else None
        )''',
        '''        row["label_is_prefix"] = True if label else None''',
        ("test_an_answer_that_omits_its_body_is_recorded_as_omitting_it",),
    ),
    (
        "a DETERMINISTIC run may call itself MODEL_GENERATED",
        OBSERVE,
        '''    if source == G.SOURCE_MODEL_GENERATED and model_output is None:''',
        '''    if False:''',
        ("test_model_generated_requires_model_output",),
    ),
    (
        "a refusal is recorded as a decline, so it falls through to a model",
        OBSERVE,
        '''            row["outcome"] = G.REFUSED
            row["said"] = str(refusal)''',
        '''            row["outcome"] = G.DECLINED
            row["said"] = str(refusal)''',
        ("test_every_case_passes_on_every_available_engine",),
    ),
    # ----------------------------------------------------- the grader
    (
        "per-body volumes are compared as a TOTAL",
        EVALUATE,
        '''        "volumes": _match_multiset(volumes, truth["volumes"],
                                   truth["volume_tolerance"]),''',
        '''        "volumes": _close(sum(volumes), sum(truth["volumes"]),
                          truth["volume_tolerance"]),''',
        ("test_T4b_a_right_geometry_total_is_not_a_right_split",),
    ),
    (
        "which body an answer was about is not checked",
        EVALUATE,
        '''            checks["about"] = row.get("about") == want["about"]''',
        '''            checks["about"] = True''',
        ("test_T2b_swapped_labels_are_caught",
         "test_T5_identical_dimensions_are_told_apart_by_id_alone"),
    ),
    (
        "a probe that was never asked counts as absent, not failed",
        EVALUATE,
        '''        checks: Dict[str, Optional[bool]] = {"asked": row is not None}
        if row is None:''',
        '''        checks: Dict[str, Optional[bool]] = {"asked": True}
        if row is None:''',
        ("test_a_missing_probe_is_a_failure_not_an_absence",),
    ),
    (
        "an export succeeds because a file exists",
        EVALUATE,
        '''    if not row.get("readable") or row.get("solids_read") is None:
        return G.LEVEL_B
    if int(row["solids_read"]) != int(truth["export_solids"]):''',
        '''    return G.LEVEL_F
    if int(row["solids_read"]) != int(truth["export_solids"]):''',
        ("test_T9_a_file_that_exists_is_not_an_export",
         "test_each_rung_is_reached_by_its_own_observation"),
    ),
    (
        "the names rung is skipped: anonymous bodies pass",
        EVALUATE,
        '''    found = set(row.get("names_found") or ())
    if any(name not in found for name in truth["export_names"]):
        return G.LEVEL_D''',
        '''    found = set(row.get("names_found") or ())''',
        ("test_T10_identity_is_never_claimed_from_a_substring",
         "test_each_rung_is_reached_by_its_own_observation"),
    ),
    (
        "the geometry rung is skipped: a wrong solid passes",
        EVALUATE,
        '''    if not _match_multiset(row.get("volumes_read"), truth["export_volumes"],
                           truth["volume_tolerance"]):
        return G.LEVEL_E''',
        '''    pass''',
        ("test_each_rung_is_reached_by_its_own_observation",),
    ),
    (
        "a fused export is not distinguished from an assembly",
        EVALUATE,
        '''    if int(row["solids_read"]) != int(truth["export_solids"]):''',
        '''    if int(row["solids_read"]) < 1:''',
        ("test_a_fused_export_is_not_a_success",
         "test_each_rung_is_reached_by_its_own_observation"),
    ),
    (
        "a refusal need not name the bodies",
        EVALUATE,
        '''            checks["named"] = all(
                str(body).lower() in said for body in want["must_name"]
            )''',
        '''            checks["named"] = True''',
        ("test_T8_a_refusal_must_name_every_body",),
    ),
    (
        "provenance is not checked, so a sum may call itself MEASURED",
        EVALUATE,
        '''            checks["provenance"] = row.get("provenance") == want["provenance"]''',
        '''            checks["provenance"] = True''',
        ("test_an_aggregate_may_not_claim_to_be_measured",),
    ),
    (
        "the writer is not checked, so an assembly may write a one-body part",
        EVALUATE,
        '''        writer_right = (
            (row.get("writer") == "single") == bool(truth["single_body_writer"])
        )''',
        '''        writer_right = True''',
        ("test_the_writer_is_pinned_per_case",),
    ),
    (
        "the summary produces a combined rate",
        EVALUATE,
        '''        "combined_rate": None,''',
        '''        "combined_rate": 1.0,''',
        ("test_the_summary_counts_live_results_and_refuses_a_combined_rate",),
    ),
    (
        "an ANSWERED outcome is accepted where a REFUSAL was required",
        EVALUATE,
        '''        checks["outcome"] = got == want["outcome"]''',
        '''        checks["outcome"] = got in (G.ANSWERED, G.REFUSED, G.DECLINED)''',
        ("test_T6_answering_an_ambiguous_question_fails",
         "test_T7_declining_an_ambiguous_question_fails"),
    ),
    # ------------------------------------------------------ the truth
    (
        "the narrowed probe view leaks the expected outcome",
        TRUTH,
        '''        {"name": p.name, "kind": p.kind, "text": p.text} for p in case.probes''',
        '''        {"name": p.name, "kind": p.kind, "text": p.text,
         "outcome": p.outcome} for p in case.probes''',
        ("test_probes_to_ask_carries_no_expectation",),
    ),
    (
        "a case may expect body names from a writer that writes none",
        TRUTH,
        '''        if single_body_writer and export_identity != IDENTITY_NOT_WRITTEN:''',
        '''        if False:''',
        ("test_a_case_may_not_expect_names_from_a_writer_that_writes_none",),
    ),
    (
        "the truth module imports the product it judges",
        TRUTH,
        '''import math
from typing import Final, Mapping, Optional, Tuple''',
        '''import math
from typing import Final, Mapping, Optional, Tuple

from cad_experimental import questions  # noqa: F401''',
        ("test_the_truth_module_imports_nothing_it_judges",),
    ),
    # ------------------------------------------- the tests themselves
    #
    # Stage 75 Phase E lost two mutants on its first sweep and both were
    # weak TESTS rather than weak guards. These break a test's own premise,
    # so a test that proves nothing is visible as one that cannot fail.
    (
        "the ten-trap set is silently reduced to nine",
        TESTFILE,
        '''    "T10 identity claimed from a substring or from translator metadata",
)''',
        ''')''',
        ("test_there_are_ten_and_each_has_a_test",),
    ),
    (
        "the corpus quietly loses its only THREE-body case",
        TRUTH,
        '''        "X2", MULTI,''',
        '''        "X2", MULTI, retired="mutant",''',
        ("test_the_multi_body_cases_really_had_several_bodies",),
    ),
    # ------------------------------------------------ the browser layer
    (
        "any text before a colon is taken for a body id",
        BROWSER,
        '''    known = _labels(bodies or reply.get("bodies") or ())''',
        '''    known = frozenset([label]) if label else frozenset()''',
        ("test_a_prefix_that_is_not_a_body_never_becomes_one",),
    ),
    (
        "a refusal on the route is recorded as an ordinary answer",
        BROWSER,
        '''    if status == "refused":
        row["outcome"] = G.REFUSED''',
        '''    if status == "refused":
        row["outcome"] = G.DECLINED''',
        ("test_the_route_grades_against_the_same_truth",),
    ),
    (
        "an unmeasured export rung is recorded as an EMPTY one",
        BROWSER,
        '''        "volumes_read": None,''',
        '''        "volumes_read": [],''',
        ("test_a_browser_export_stops_at_the_names_rung_and_says_why",),
    ),
    (
        "the body ids are taken from the x-cad-bodies HEADER",
        BROWSER,
        '''        "names_found": [name for name in asked if name in present],''',
        '''        "names_found": [n.strip() for n
                        in (headers.get("x-cad-bodies") or "").split(",")
                        if n.strip()],''',
        ("test_the_export_header_is_recorded_and_never_consulted",),
    ),
]


def _environment() -> dict:
    env = dict(os.environ)
    # ABSOLUTE, and built here rather than inherited: a relative PYTHONPATH
    # resolves against the child's working directory, and Stage 75 Phase E
    # lost a sweep to exactly that.
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
        cwd=str(TESTS), env=_environment(),
        capture_output=True, text=True,
    )
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
