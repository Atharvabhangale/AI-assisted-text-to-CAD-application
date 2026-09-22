"""Mutation test: every Stage 70 guard must go RED when its defect returns."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
PROMPT = ROOT / "apps/api/src/cad_experimental/prompt.py"
TEST = ROOT / "apps/api/tests_experimental/test_union_section_shape.py"

NOUNS = ("that piece for the finished product: `shell`, `box`, `enclosure` and\n"
         "`assembly` all read like names")
MANDATE = "Name the union itself `fuse` -- that exact word, always."

MUTATIONS = [
  ("delete the forbidden-noun sentence (arm B1's edit, P11 7/96)",
   PROMPT, NOUNS, "that piece for the finished product. Those all read like names",
   ["test_the_forbidden_product_nouns_are_still_named"]),
  ("rename the mandated union id away from the verb",
   PROMPT, MANDATE, "Name the union itself `assembly` -- that exact word, always.",
   ["test_the_mandate_names_a_verb"]),
  ("drop the reason from the mandate (B1's lesson: the reason carries it)",
   PROMPT, "there is no solid called `fuse`", "no solid is called that",
   ["test_the_mandate_explains_why_rather_than_only_commanding"]),
  ("let a consumed tool collapse into P11",
   TEST, 'self.assertEqual(sorted({p.code for p in verdict.problems}), ["P12"])',
   'self.assertEqual(sorted({p.code for p in verdict.problems}), ["P11"])',
   ["test_a_consumed_tool_is_P12_and_not_P11"]),
  ("assert the WRONG paragraph order (arm B3, p = 0.0001)",
   TEST, "        self.assertLess(\n            naming, mandate,",
         "        self.assertGreater(\n            naming, mandate,",
   ["test_the_naming_rule_comes_BEFORE_the_mandate"]),
  ("let the union's id decide whether targeting it is legal",
   TEST, '    UNION_IDS = ("fuse", "enclosure", "shell", "assembly", "join_1")',
         '    UNION_IDS = ()',
   ["test_targeting_the_union_is_P11_whatever_it_is_named"]),
]

def run():
    r = subprocess.run([sys.executable, "-m", "unittest",
                        "test_union_section_shape"],
                       cwd=ROOT / "apps/api/tests_experimental",
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

code, out = run()
assert code == 0, "the suite must be green before mutating\n" + out
print("baseline: GREEN\n")
bad = 0
for name, path, old, new, expect in MUTATIONS:
    backup = path.read_text(encoding="utf-8")
    assert backup.count(old) == 1, f"{name}: anchor appears {backup.count(old)} times"
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
