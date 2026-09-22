"""Mutation test: every Stage 72 guard must go RED when it is undone.

Each mutation is a plausible simplification, and most are the shape of "just
pick one" -- the guess this whole slice exists to refuse.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/home/user/AI-assisted-text-to-CAD-application")
SRC = ROOT / "apps/api/src/cad_experimental"
REF, NORM, EXEC = SRC / "body_reference.py", SRC / "normalize.py", SRC / "executor.py"

MUTATIONS = [
  ("fall back to the first body when the request names none",
   REF, '''    listed = ", ".join(repr(name) for name in live)
    return BodyChoice(''',
        '''    return BodyChoice(body=live[0], named=False, live=live)
    listed = ", ".join(repr(name) for name in live)
    return BodyChoice(''',
   ["test_several_bodies_and_no_name_refuses_and_lists_them",
    "test_an_edit_naming_no_body_is_refused_not_declined"]),

  ("pick the first when the request names two bodies",
   REF, "    if len(mentioned) > 1:",
        "    if len(mentioned) > 1 and False:",
   ["test_two_names_at_once_refuses",
    "test_an_edit_naming_two_bodies_is_refused"]),

  ("let a consumed body resolve as though it were live",
   REF, "        if chosen in live:", "        if True:",
   ["test_a_consumed_body_says_what_took_it"]),

  ("stop pruning an id contained in a longer one",
   REF, "    mentioned = _longest_distinct(found) if found else ()",
        "    mentioned = tuple(found)",
   ["test_an_id_inside_a_longer_id_is_settled_by_the_text"]),

  ("take the envelope over EVERY body again",
   NORM, "        if pieces is not None and op.get(\"id\") not in pieces:\n            continue",
         "        if False:\n            continue",
   ["test_the_bore_lands_in_THAT_body_not_between_them"]),

  ("decline instead of refusing when the body is ambiguous",
   NORM, '''    choice = resolve_body(text, plan)
    if choice.reason is not None:
        raise ReadingError(choice.reason)''',
         '''    choice = resolve_body(text, plan)
    if choice.reason is not None:
        raise _Decline(choice.reason)''',
   ["test_an_edit_naming_no_body_is_refused_not_declined"]),

  ("treat a bare adjective as a relative change again",
   NORM, "    if absolute or not comparative:", "    if absolute:",
   ["test_a_bare_adjective_states_the_finished_size"]),

  ("stop naming the body on a failed selection",
   EXEC, 'resolution.code, f"on {body!r}: {resolution.message}",',
         'resolution.code, resolution.message,',
   ["test_R1_says_which_body"]),
]

def run():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_body_targeting"],
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
