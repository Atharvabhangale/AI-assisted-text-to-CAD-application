"""Mutation test: every Stage 71 invariant must go RED when it is undone.

Each mutation is a plausible simplification someone could make later -- most
of them are the shape of a bug this project has already had once.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
SRC = ROOT / "apps/api/src/cad_experimental"
PLAN, EXEC = SRC / "plan.py", SRC / "executor.py"
BUILD, HIST = SRC / "build.py", SRC / "history.py"
VALID = SRC / "validation.py"

MUTATIONS = [
  ("put `part` into the geometry vocabulary (moves every fingerprint)",
   PLAN, "DECLARATION_TYPES: Tuple[str, ...] = (PART,)",
         "DECLARATION_TYPES: Tuple[str, ...] = (PART,)\nOPERATION_TYPES = OPERATION_TYPES + (PART,)",
   ["test_no_schema_fingerprint_moved", "test_part_is_not_in_the_geometry_vocabulary"]),

  ("let ANY number of live bodies through (drop the gate entirely)",
   EXEC, "        if len(bodies) > 1 and undeclared:",
         "        if False and len(bodies) > 1 and undeclared:",
   ["test_undeclared_bodies_still_fail"]),

  ("INFER the declared set from the geometry instead of the plan",
   EXEC, '        declared = tuple(getattr(history, "declared_bodies", ()) or ())',
         "        declared = tuple(body.id for body in bodies)",
   ["test_undeclared_bodies_still_fail"]),

  ("widen `result.part` to `bodies[0]` (the Stage 62 bug by another name)",
   EXEC, "        return self.bodies[0].id if len(self.bodies) == 1 else None",
         "        return self.bodies[0].id if self.bodies else None",
   ["test_part_stays_the_single_body_or_none"]),

  ("render only the first body",
   BUILD, "    for body in result.bodies:\n        shape = result.shapes.get(body.id)",
          "    for body in result.bodies[:1]:\n        shape = result.shapes.get(body.id)",
   ["test_one_mesh_per_body_each_naming_its_body"]),

  ("let a declaration join its body's feature history",
   HIST, "        if kind == PART:\n            target = getattr(operation, \"target\", None)",
         "        if kind == PART and bodies:\n            bodies[-1][\"features\"].append(operation.id)\n            target = getattr(operation, \"target\", None)",
   ["test_a_declaration_is_nobody_s_feature"]),

  ("stop reporting an undeclared body left standing (P34's first half)",
   VALID, "    undeclared = tuple(name for name in live if name not in declared)",
          "    undeclared = ()",
   ["test_an_undeclared_body_left_standing_is_P34"]),

  ("allow a body to be declared twice (P33)",
   VALID, "            if operation.target in declared:",
          "            if False and operation.target in declared:",
   ["test_declaring_a_body_twice_is_P33"]),
]

def run():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_multi_body"],
                       cwd=ROOT / "apps/api/tests_experimental",
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

code, out = run()
assert code == 0, "the suite must be green before mutating\n" + out[-4000:]
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
assert code == 0, "the suite must be green again after restoring\n" + out[-4000:]
print(f"\nrestored: GREEN.  {len(MUTATIONS) - bad}/{len(MUTATIONS)} mutations caught")
sys.exit(1 if bad else 0)
