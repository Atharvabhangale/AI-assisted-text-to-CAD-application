"""Mutation test: every Phase E instrument proof must go RED on its defect.

Phase E's conclusion is a statement about a residual, and a residual is only
worth stating if the checks that found it read what they claim to. These
mutants reinstate, one at a time, each way a check could stop doing that --
including the two Phase A actually made -- and require the proof that names
the defect to fail.

Stage 69's and Stage 70's harness is the pattern: a mutant is a five-tuple
and it names WHICH tests must fail, so a mutant that merely breaks something
is not counted as caught.

    cd /home/user/AI-assisted-text-to-CAD-application/apps/api
    python3 ../../docs/evaluation-baselines/stage75-multibody/phase-e-r2-tail/\
        mutation_test_e.py
"""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
STAGE75 = ROOT / "docs/evaluation-baselines/stage75-multibody"
GRADER = STAGE75 / "evaluate75.py"
TRUTH = STAGE75 / "ground_truth75.py"
GENERATION = ROOT / "apps/api/src/cad_experimental/generation.py"
TEST = ROOT / "apps/api/tests_experimental/test_r2_instrument.py"

#: Absolute, and built here. The child runs from a different directory, so a
#: relative PYTHONPATH exported by the caller would resolve against the wrong
#: root -- every mutant would then read as caught because the suite fails to
#: import at all.
ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join(str(p) for p in (
    ROOT / "packages/cad-core/src",
    ROOT / "apps/api/src",
    ROOT / "apps/api/tests_experimental",
))

MUTATIONS = [
  ("let the SYSTEM's sentence count as the model's words (Phase A's bug)",
   GRADER,
   '    parts.extend(observation.get("questions") or ())',
   '    parts.extend(observation.get("questions") or ())\n'
   '    if observation.get("system_error"):\n'
   '        parts.append(str(observation["system_error"]))',
   ["test_the_noun_check_cannot_be_satisfied_by_the_SYSTEM",
    "test_the_naming_check_cannot_be_satisfied_by_the_SYSTEM",
    "test_model_words_never_reach_for_the_system_field"]),

  ("let the noun check pass unconditionally",
   GRADER,
   '            checks["question_addressed_the_request"] = all(\n'
   '                token.lower() in said\n'
   '                for token in truth["refusal_question_must_mention"]\n'
   '            )',
   '            checks["question_addressed_the_request"] = True',
   ["test_the_noun_check_cannot_be_satisfied_by_the_SYSTEM",
    "test_the_noun_check_partitions_the_sample_exactly"]),

  ("read the noun only out of the summary",
   GRADER,
   '        said = _model_words(observation)',
   '        said = str(observation.get("summary") or "").lower()',
   ["test_the_noun_check_reads_the_QUESTION_as_well_as_the_summary"]),

  ("accept a generated plan as a refusal",
   GRADER,
   '        declined = observation["outcome_declared"] in (\n'
   '            "needs_clarification", "unsupported",\n'
   '        )',
   '        declined = observation["outcome_declared"] is not None',
   ["test_a_plan_instead_of_a_question_fails"]),

  ("let naming ONE body count as naming the bodies",
   GRADER,
   '        named = all(body.lower() in said for body in wanted)',
   '        named = any(body.lower() in said for body in wanted)',
   ["test_naming_only_one_body_fails"]),

  ("stop checking that a refusal built nothing",
   GRADER,
   '        checks["built_nothing"] = observation["body_count"] == 0',
   '        checks["built_nothing"] = True',
   ["test_building_geometry_fails"]),

  ("count operations from the PARSED plan again",
   GRADER,
   '        written = observation.get("model_operation_count",\n'
   '                                  observation["operation_count"])',
   '        written = observation["operation_count"]',
   ["test_carrying_operations_fails"]),

  ("let an unrecognised outcome be MODEL_GENERATED again",
   GRADER,
   '    if declared in ("needs_clarification", "unsupported"):\n'
   '        return G.REFUSED\n'
   '    return G.PROVIDER_ERROR',
   '    if declared in ("needs_clarification", "unsupported"):\n'
   '        return G.REFUSED\n'
   '    return G.MODEL_GENERATED',
   ["test_the_label_fails_closed"]),

  ("widen what counts as a success to any live-ish label",
   TRUTH,
   'COUNTS_AS_SUCCESS: Final[Tuple[str, ...]] = (MODEL_GENERATED,)',
   'COUNTS_AS_SUCCESS: Final[Tuple[str, ...]] = (MODEL_GENERATED, FALLBACK)',
   ["test_only_one_label_counts_as_success"]),

  ("give the generation layer a deterministic route",
   GENERATION,
   'from .parser import PlanParseError, parse_plan_text',
   'from .parser import PlanParseError, parse_plan_text\nfrom . import normalize  # noqa: F401',
   ["test_the_generation_layer_has_no_deterministic_route"]),

  ("assert the crossed reply PASSES the noun check",
   TEST,
   '        self.assertIs(crossed["checks"]["question_addressed_the_request"],\n'
   '                      False)',
   '        self.assertIs(crossed["checks"]["question_addressed_the_request"],\n'
   '                      True)',
   ["test_the_noun_check_cannot_be_satisfied_by_the_SYSTEM"]),

  ("let the recorded sample be read as 47 attempts",
   TEST,
   '        self.assertEqual(len(self.rows), 48)',
   '        self.assertEqual(len(self.rows), 47)',
   ["test_the_sample_is_what_it_says_it_is"]),
]


def run():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_r2_instrument"],
                       cwd=ROOT / "apps/api/tests_experimental",
                       env=ENV, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


code, out = run()
assert code == 0, "the suite must be green before mutating\n" + out
print("baseline: GREEN\n")
bad = 0
for name, path, old, new, expect in MUTATIONS:
    backup = path.read_text(encoding="utf-8")
    assert backup.count(old) == 1, (
        f"{name}: anchor appears {backup.count(old)} times")
    path.write_text(backup.replace(old, new), encoding="utf-8")
    try:
        code, out = run()
    finally:
        path.write_text(backup, encoding="utf-8")
    failed = {ln.split(" ")[1] for ln in out.splitlines()
              if ln.startswith(("FAIL: ", "ERROR: "))}
    ok = code != 0 and all(e in failed for e in expect)
    print(f"  [{'RED  ' if ok else 'MISS '}] {name}")
    if not ok:
        bad += 1
        print(f"        expected {expect}, got {sorted(failed)} (exit {code})")
code, out = run()
assert code == 0, "the suite must be green again after restoring\n" + out
print(f"\nrestored: GREEN.  {len(MUTATIONS) - bad}/{len(MUTATIONS)} caught")
sys.exit(1 if bad else 0)
