"""Mutation test: every Phase D guard must go RED when its defect returns.

Stage 69's and Stage 70's harnesses are the pattern and this follows them.
A mutant is a five-tuple -- description, file, old, new, the tests that MUST
fail -- so a mutant that merely breaks something is not counted as caught;
it has to break the guard whose name is the claim.

    cd /home/user/AI-assisted-text-to-CAD-application/apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    python3 ../../docs/evaluation-baselines/stage75-multibody/phase-d-r2/\
        mutation_test_d.py

Every file is restored in a `finally`, and the suite is re-run at the end to
prove it: a sweep that leaves a mutated prompt behind would be worse than no
sweep at all.
"""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
PROMPT = ROOT / "apps/api/src/cad_experimental/prompt.py"
TEST = ROOT / "apps/api/tests_experimental/test_missing_body_reference.py"
STAGE75 = ROOT / "docs/evaluation-baselines/stage75-multibody"
TRUTH = STAGE75 / "ground_truth75.py"
GRADER = STAGE75 / "evaluate75.py"
CLASSIFY = STAGE75 / "phase-d-r2/classify_d.py"
VARIANTS = STAGE75 / "phase-d-r2/variants_d.py"
RULE = STAGE75 / "phase-d-r2/decision_rule_d.py"

CONTRAST = 'A request that DOES name one is a different question. "Make the flange 5 mm\nwider", with `plate` and `post` standing, names a body this part does not\nhave. Say that `flange` matches nothing, and then list the bodies there are.\nDo not answer it as though it had named neither.'
TAIL = '"Make it 10 mm taller" with a plate and a post standing names neither, and\nguessing is worse than asking.'

#: The envelope arm D3 sent -- measured at 0/32 in this very section. Brace
#: doubled, because the prompt is one big f-string.
ENVELOPE = (
    "A name that matches no body is not the same question. `flange` is not "
    "one of\nthis part's bodies, so say that, and list the bodies there "
    "are:\n\n  {{\"status\": \"needs_clarification\",\n   \"summary\": "
    "\"this part has no body called `flange`\",\n   \"questions\": "
    "[\"There is no body called `flange`.\"],\n   \"operations\": []}}")

MUTATIONS = [
  ("delete the contrast entirely (the committed prompt before Phase D, R2 0/32)",
   PROMPT, "\n\n" + CONTRAST, "",
   ["test_the_section_shows_TWO_worked_requests"]),

  ("state the contrast BEFORE the pronoun rule (Stage 70's B3 shape)",
   PROMPT, TAIL + "\n\n" + CONTRAST, CONTRAST + "\n\n" + TAIL,
   ["test_the_pronoun_rule_comes_FIRST"]),

  ("make the worked missing name a body the section declares",
   PROMPT, "Say that `flange` matches nothing",
           "Say that `plate` matches nothing",
   ["test_the_section_shows_TWO_worked_requests"]),

  ("teach to the test: use the corpus's own noun",
   PROMPT, CONTRAST, CONTRAST.replace("flange", "bracket"),
   ["test_the_contrast_does_not_teach_to_the_test"]),

  ("stop listing the bodies that DO exist",
   PROMPT, 'wider", with `plate` and `post` standing, names a body this part does not',
           'wider" names a body this part does not',
   ["test_the_contrast_lists_the_bodies_that_DO_exist"]),

  ("replace the prose with arm D3's reply envelope (measured 0/32)",
   PROMPT, CONTRAST, ENVELOPE,
   ["test_the_contrast_is_PROSE_not_a_second_reply_envelope"]),

  # The generic reply's own summary contains the word "body", so pinning
  # ("body",) on R1 leaves `test_the_same_reply_is_right...` passing -- the
  # criterion is met by accident. The guard that exists for THIS defect is
  # the one that names it, and it is the one required here.
  ("pin the user's noun on R1 as well, so the control stops being one",
   TRUTH,
   '        "R1", REFUSAL, R1_TEXT, bodies=None, declaration_required=False,\n'
   '        refusal_must_name=FIXTURE_BODIES, operations_permitted=False,',
   '        "R1", REFUSAL, R1_TEXT, bodies=None, declaration_required=False,\n'
   '        refusal_must_name=FIXTURE_BODIES, operations_permitted=False,\n'
   '        refusal_question_must_mention=("body",),',
   ["test_only_R2_pins_the_users_own_noun"]),

  ("let a clarification pass without addressing the request",
   GRADER,
   '        if truth["refusal_question_must_mention"]:\n'
   '            checks["question_addressed_the_request"] = all(\n'
   '                token.lower() in said\n'
   '                for token in truth["refusal_question_must_mention"]\n'
   '            )',
   '        if truth["refusal_question_must_mention"]:\n'
   '            checks["question_addressed_the_request"] = True',
   ["test_the_same_reply_is_right_for_R1_and_wrong_for_R2"]),

  ("default the model's operation count to 0 when the key is absent",
   CLASSIFY,
   '    written = observation.get("model_operation_count")\n'
   '    if written is not None:\n'
   '        return written\n'
   '    written = EV._operations_the_model_wrote(observation.get("raw_text"))\n'
   '    if written is not None:\n'
   '        return written\n'
   '    return observation.get("operation_count", 0)',
   '    return observation.get("model_operation_count", 0)',
   ["test_the_PHASE_B_record_re_reads_as_five_E_rows"]),

  ("call a provider failure the model's own behaviour",
   CLASSIFY,
   '    if EV.outcome_label(observation) == G.PROVIDER_ERROR:\n        return "F"\n',
   '    if observation.get("outcome_declared") in (None, "invalid_model_output"):\n'
   '        return "F"\n',
   ["test_a_provider_failure_is_not_the_models_behaviour"]),

  ("drop the operations class entirely, so an E row reads as F",
   CLASSIFY,
   '    # E -- and it MUST be first. See the module docstring.\n'
   '    if operations_written(observation) > 0:\n'
   '        return "E"\n',
   '',
   ["test_operations_beat_the_invalid_output_class",
    "test_the_PHASE_B_record_re_reads_as_five_E_rows"]),

  ("let a refusal that BUILT something still read as correct",
   CLASSIFY,
   '    if observation.get("body_count"):\n        return "F"\n', '',
   ["test_a_refusal_that_BUILT_something_is_not_correct"]),

  ("apply the refusal taxonomy to a creation case again",
   CLASSIFY, '    if truth["group"] != G.REFUSAL:', '    if False:',
   ["test_the_taxonomy_refuses_a_CREATION_case"]),

  ("drop the zero classes from the distribution",
   CLASSIFY, '    counts = {letter: 0 for letter in CLASS_ORDER}',
             '    counts = {}',
   ["test_every_class_is_in_the_distribution"]),

  ("let a missing creation control be treated as passed",
   RULE,
   '        elif key in ("siblings", "creation", "single_body") and not (\n'
   '                arm.get(key) and baseline.get(key)):',
   '        elif False:',
   ["test_an_EMPTY_creation_mapping_REJECTS"]),

  ("compare wrong-body code COUNTS again instead of rates",
   RULE,
   '            if _rate(arm_count, arm_calls) > _rate(base_count, base_calls):',
   '            if arm_count > base_count:',
   ["test_identity_codes_are_compared_PER_CALL"]),

  ("give `exploratory` a default again",
   RULE, "           *, exploratory: bool) -> Dict[str, Any]:",
         "           *, exploratory: bool = False) -> Dict[str, Any]:",
   ["test_exploratory_has_no_default"]),

  ("make D4's idempotency guard wrap-sensitive again",
   VARIANTS,
   '    if _normalised(CONTRAST_MARKER) in _normalised(base):',
   '    if CONTRAST.strip().split("\\n")[0] in base:',
   ["test_the_D4_guard_survives_a_REFLOW"]),

  ("let D3 append different text from D1",
   VARIANTS,
   '        BODIES_TAIL + _EXAMPLE.format(question=QUESTION_NAMES_THE_MISS))',
   '        BODIES_TAIL + _EXAMPLE.format(question=QUESTION_WITHOUT_THE_MISS))',
   ["test_D1_and_D3_append_BYTE_IDENTICAL_text"]),

  ("let the adopted arm append its paragraph a second time",
   VARIANTS,
   '    if _normalised(CONTRAST_MARKER) in _normalised(base):\n        return base\n',
   '',
   ["test_the_adopted_arm_now_renders_the_committed_prompt"]),

  ("assert the WRONG paragraph order",
   TEST, "        self.assertLess(\n            pronoun, where,",
         "        self.assertGreater(\n            pronoun, where,",
   ["test_the_pronoun_rule_comes_FIRST"]),

  ("assert the envelope IS in the contrast",
   TEST, "            self.assertNotIn(envelope, contrast)",
         "            self.assertIn(envelope, contrast)",
   ["test_the_contrast_is_PROSE_not_a_second_reply_envelope"]),

  ("claim the smuggled-operations row is class F",
   TEST, '        self.assertEqual(CD.classify(attempt), "E")',
         '        self.assertEqual(CD.classify(attempt), "F")',
   ["test_operations_beat_the_invalid_output_class"]),
]


#: An ABSOLUTE PYTHONPATH, built here rather than inherited.
#: Stage 69's and Stage 70's drivers pass no `env` and run the child from a
#: different directory, so a relative `PYTHONPATH` exported by the caller
#: resolves against the wrong root and every mutant reads as caught -- the
#: suite fails to import, so it "fails" whatever was mutated.
ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join(str(p) for p in (
    ROOT / "packages/cad-core/src",
    ROOT / "apps/api/src",
    ROOT / "apps/api/tests_experimental",
))


def run():
    r = subprocess.run([sys.executable, "-m", "unittest",
                        "test_missing_body_reference"],
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
print(f"\nrestored: GREEN.  {len(MUTATIONS) - bad}/{len(MUTATIONS)} mutations caught")
sys.exit(1 if bad else 0)
