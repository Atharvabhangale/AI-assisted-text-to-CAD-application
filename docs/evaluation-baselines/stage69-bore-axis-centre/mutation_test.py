"""Mutation test: every Stage 69 guard must go RED when its defect returns."""
import pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
PROMPT = ROOT / "apps/api/src/cad_experimental/prompt.py"
TEST = ROOT / "apps/api/tests_experimental/test_bore_axis_centre.py"

EXAMPLE = ('            Through the centre of a 60 by 30 by 30 part, the three\n'
           '            bores are written:\n'
           '              +Z -- {{"x": 30, "y": 15, "z": 0}}\n'
           '              +Y -- {{"x": 30, "y": 0,  "z": 15}}\n'
           '              +X -- {{"x": 0,  "y": 15, "z": 15}}\n'
           '            Only the +Z bore has z at 0. The other two carry z at\n'
           '            the middle of the height, because for them z is one of\n'
           '            the two components that decide where the hole is.\n')

MUTATIONS = [
  ("remove the worked example entirely (the pre-Stage-69 prompt)",
   PROMPT, EXAMPLE, "",
   ["test_the_prompt_works_a_bore_position_example",
    "test_not_every_shown_bore_position_zeroes_z"]),
  ("zero z on the example's +Y and +X bores (teach the defect)",
   PROMPT,
   '              +Y -- {{"x": 30, "y": 0,  "z": 15}}\n'
   '              +X -- {{"x": 0,  "y": 15, "z": 15}}\n',
   '              +Y -- {{"x": 30, "y": 0,  "z": 0}}\n'
   '              +X -- {{"x": 0,  "y": 15, "z": 0}}\n',
   ["test_the_examples_arithmetic_is_right",
    "test_not_every_shown_bore_position_zeroes_z"]),
  ("delete the stated rule, keeping the example",
   PROMPT, "            0 is only ever safe for the component along the axis.\n", "",
   ["test_the_rule_is_stated_as_well_as_shown"]),
  ("make centred_triple zero z on every axis (the measured defect)",
   TEST, "    return tuple(along if j == i else CENTRE[j] for j in range(3))",
   "    return tuple(0.0 if j in (i, 2) else CENTRE[j] for j in range(3))",
   ["test_the_centred_part_is_the_canonical_enclosure"]),
  ("make the defective triples equal the correct ones",
   TEST, "Z_ZEROED = {axis: tuple(0.0 if j == 2 else v for j, v in enumerate(triple))\n"
         "            for axis, triple in ALL_CENTRED.items()}",
   "Z_ZEROED = dict(ALL_CENTRED)",
   ["test_zeroing_z_on_every_bore_is_a_different_part"]),
  ("let an off-centre bore be graded by the kernel alone",
   TEST, '        off["Z"] = (17.0, CENTRE[1], 0.0)',
   '        off["Z"] = (7.0, CENTRE[1], 0.0)',
   ["test_an_off_centre_bore_can_measure_identically"]),
]

def run():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_bore_axis_centre"],
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
