"""Mutation test: every Stage 73/74 guard must go RED when it is undone.

Each mutation is a plausible simplification. Most are the shape of "just pick
one" or "the file was written, so it worked" -- the two guesses this pair of
stages exists to refuse.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
SRC = ROOT / "apps/api/src/cad_experimental"
Q, CB = SRC / "questions.py", SRC / "cad_backend.py"
REF = SRC / "body_reference.py"

MEASURE = "test_body_measurement"
EXPORT = "test_step_assembly"

MUTATIONS = [
  # --- per-body measurement ------------------------------------------------
  ("answer about the first body when the question names none",
   Q, '''    choice = resolve_body(text, plan, verb="measure")
    if choice.reason is not None:
        raise QuestionRefused(choice.reason)''',
      '''    choice = resolve_body(text, plan, verb="measure")
    if choice.reason is not None:
        return Scope(plan=plan or {},
                     measurement=list(per_body.values())[0],
                     label=list(per_body)[0])''',
   MEASURE, ["test_the_volume_of_a_two_body_part_is_refused",
             "test_the_refusal_says_measure_not_change",
             "test_naming_two_bodies_at_once_is_refused"]),

  ("let an ambiguous question DECLINE, so the model answers it",
   Q, "        raise QuestionRefused(choice.reason)",
      "        return Scope(plan=plan or {}, measurement={})",
   MEASURE, ["test_the_volume_of_a_two_body_part_is_refused",
             "test_refusing_is_not_declining"]),

  ("scope the words but not the MEASUREMENT",
   Q, '''    return Scope(plan=_body_operations(plan or {}, choice.body),
                 measurement=per_body[choice.body],
                 label=choice.body)''',
      '''    return Scope(plan=_body_operations(plan or {}, choice.body),
                 measurement=dict(measured),
                 label=choice.body)''',
   MEASURE, ["test_a_named_body_size_is_that_body_not_the_pair",
             "test_the_volume_of_a_named_body_is_that_body",
             "test_the_other_body_answers_differently"]),

  ("scope the measurement but not the PLAN",
   Q, "    return Scope(plan=_body_operations(plan or {}, choice.body),",
      "    return Scope(plan=plan or {},",
   MEASURE, ["test_the_plan_is_scoped_too_not_only_the_measurement"]),

  ("call the combined envelope MEASURED",
   Q, '''        if name in _FROM_ENVELOPE:
            provenance = ASSUMED''',
      '''        if name in _FROM_ENVELOPE:
            provenance = MEASURED''',
   MEASURE, ["test_the_combined_envelope_is_ASSUMED",
             "test_the_aggregate_CENTRE_is_ASSUMED_too"]),

  ("call a summed total MEASURED",
   Q, '''        elif provenance == MEASURED:
            provenance = CALCULATED
            source = "summed over every body's own measurement"''',
      '''        elif provenance == MEASURED:
            pass''',
   MEASURE, ["test_the_total_volume_is_the_sum_and_says_it_is_calculated",
             "test_the_total_volume_is_never_reported_as_measured",
             "test_a_count_over_two_bodies_does_not_say_the_solid"]),

  # Only ONE test catches this, and the reason is worth recording: with the
  # gate removed, "make the cube wider" still resolves -- it names a body --
  # so it declines exactly as before. What breaks is "undo that", which names
  # none and would be refused as an ambiguous QUESTION although it is not a
  # question at all. The narrower expectation is the true one.
  ("refuse a NON-question too, taking edits from the readers",
   Q, "    if not _ASKS.search(lowered):\n        return None",
      "    if not _ASKS.search(lowered):\n        pass",
   MEASURE, ["test_a_non_question_is_never_refused"]),

  ("give a question the EDIT's refusal wording again",
   Q, '    choice = resolve_body(text, plan, verb="measure")',
      '    choice = resolve_body(text, plan)',
   MEASURE, ["test_the_refusal_says_measure_not_change"]),

  ("answer from another body when this one was not measured",
   Q, '''    if choice.body is None or choice.body not in per_body:''',
      '''    if choice.body is None:''',
   MEASURE, ["test_a_body_the_build_did_not_measure_is_refused"]),

  # --- STEP assembly -------------------------------------------------------
  ("trust the writer: stop counting the solids it wrote",
   CB, "    if len(written) != len(ordered):",
       "    if False:",
   EXPORT, ["test_a_wrong_solid_count_fails_the_export"]),

  ("check the geometry but not the body NAMES",
   CB, "    missing = [name for name, _ in ordered if name not in written_text]",
       "    missing = []",
   EXPORT, ["test_an_id_that_did_not_reach_the_file_fails_the_export"]),

  ("let two bodies share one id",
   CB, "    if len(set(names)) != len(names):",
       "    if False:",
   EXPORT, ["test_two_bodies_cannot_share_an_id"]),

  ("export only the first body",
   CB, "    ordered = tuple((str(name), shape) for name, shape in bodies)",
       "    ordered = tuple((str(name), shape) for name, shape in bodies)[:1]",
   EXPORT, ["test_two_bodies_are_written_and_read_back_as_two_solids",
            "test_no_body_is_dropped",
            "test_three_slabs_read_back_as_three_solids"]),

  ("accept an empty body list and write an empty assembly",
   CB, "    if not ordered:",
       "    if False:",
   EXPORT, ["test_an_empty_body_list_is_refused"]),

  ("accept a body with no shape",
   CB, "        if shape is None:",
       "        if False:",
   EXPORT, ["test_a_body_with_no_shape_is_refused"]),

  # --- the one that would undo Stage 71 as well ----------------------------
  ("restore the resolver's first-body fallback",
   REF, '''    listed = ", ".join(repr(name) for name in live)
    return BodyChoice(''',
        '''    return BodyChoice(body=live[0], named=False, live=live)
    listed = ", ".join(repr(name) for name in live)
    return BodyChoice(''',
   MEASURE, ["test_the_volume_of_a_two_body_part_is_refused"]),
]


def run(module):
    r = subprocess.run([sys.executable, "-m", "unittest", module],
                       cwd=ROOT / "apps/api/tests_experimental",
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


for module in (MEASURE, EXPORT):
    code, out = run(module)
    assert code == 0, f"{module} must be green before mutating\n" + out[-4000:]
print(f"baseline: GREEN ({MEASURE}, {EXPORT})\n")

bad = 0
for name, path, old, new, module, expect in MUTATIONS:
    backup = path.read_text(encoding="utf-8")
    assert backup.count(old) == 1, f"{name}: anchor appears {backup.count(old)} times"
    path.write_text(backup.replace(old, new), encoding="utf-8")
    try:
        code, out = run(module)
    finally:
        path.write_text(backup, encoding="utf-8")
    failed = {ln.split(" ")[1] for ln in out.splitlines()
              if ln.startswith(("FAIL: ", "ERROR: "))}
    ok = code != 0 and all(e in failed for e in expect)
    print(f"  [{'RED  ' if ok else 'MISS '}] {name}")
    if not ok:
        bad += 1
        print(f"        expected {expect}, got {sorted(failed)} (exit {code})")

for module in (MEASURE, EXPORT):
    code, out = run(module)
    assert code == 0, f"{module} must be green again after restoring\n" + out[-4000:]
print(f"\nrestored: GREEN.  {len(MUTATIONS) - bad}/{len(MUTATIONS)} mutations caught")
sys.exit(1 if bad else 0)
