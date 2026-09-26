# The broader multi-body corpus — designed, and deliberately not run

**Nothing in this file has been measured.** It is the design Stage 75 Phase E
was asked to produce once the R2 decision was complete, written against the
code as it stands, so the next stage builds an instrument rather than
inventing one while it runs.

---

## 1 — why the current numbers do not already answer it

Stage 75's multi-body corpus is **nine cases**: six creation and three
refusal, all against one two-body shape. Its creation rate on the committed
prompt is 47/48, and that number must not be read as general multi-body
reliability, for three reasons that are facts about the corpus rather than
opinions about the model:

- **Four of the six creation cases are the same part.** M1, M2, M3 and M5
  all build a cube and a cylinder side by side; N2 fuses them and N3 edits
  one. A corpus that varies the sentence but not the geometry measures
  comprehension of one shape.
- **Every case declares at most two bodies.** `MAX_BODIES` is 8 and nothing
  above two has been asked of a model even once.
- **No creation case exercises a downstream surface.** Measurement, drawing
  and export are covered deterministically (Stages 73–74) and by a model
  never.

And the refusal side is three cases against one fixture, of which **one —
R2 — is at 70.8 %** with a cause that four arms across two phases have not
removed.

## 2 — the nine dimensions, and what each one has to pin

Creation and refusal are scored **separately and never pooled**: a refusal
rate and a build rate are different quantities, and mixing them is how a
model that refuses everything scores well. Phase B's record says so and its
`summarise()` refuses to produce a combined number.

| # | dimension | what a case must pin | what makes it hard to fake |
|---|---|---|---|
| 1 | **independent body creation** | N bodies for N in {2, 3, 5}, each declared exactly once, per-body closed-form volumes, disjoint | the volumes are per body and compared as an unordered multiset; a total would pass on a fused part |
| 2 | **named body creation** | the ids the request asks for survive into `declared_bodies` | an id the model invents is a different part, even at the right volume |
| 3 | **body-targeted edits** | the named body changes, every other body is bit-identical in volume, faces, edges and solids | Stage 71 measured isolation on the kernel; a model case must too |
| 4 | **holes / features on named bodies** | the bore lands in the named body, its axis and centre are right, and the other body has no new faces | Stage 69's bore-centre rule applies per body here |
| 5 | **ambiguous body references** | REFUSE, name every live body, ask, emit nothing | R1 and R3 generalised past two bodies |
| 6 | **missing body references** | REFUSE, say the name matched nothing, name the live bodies, ask, emit nothing | R2 generalised; **carry its 70.8 % forward as a known floor, not as a fresh unknown** |
| 7 | **explicit fusion** | ONE body, no `part` declaration, the closed form of the overlap | N2's shape; the control that catches a multi-body prompt over-declaring |
| 8 | **body measurement** | a question naming a body answers from THAT body; naming none of several REFUSES; a total says it is `CALCULATED` | Stage 73's semantics, asked of a model for the first time |
| 9 | **export** | a STEP assembly with one solid per body, every id present in the file | Stage 74's `verify_assembly` reads the file back; a count alone cannot see anonymous products |

## 3 — the rules the corpus must be built under

These are not new. They are what Stages 67, 68 and Phase A–E each paid to
learn, and every one of them cost a stage's headline number when it was
missed.

- **Ground truth takes a case NAME and nothing else.** Stage 67 derived its
  expected thickness from the model's own plan and graded parts against
  their own answer; `ground_truth75.expected()` has no parameter a plan can
  enter by, and the replacement must not either.
- **Never edit an expectation after seeing a score.** A case whose
  expectation turns out to be wrong is RETIRED verbatim with a note, the way
  M4, M6, M7, M8 and N1 are, and replaced by a new case with a new name.
- **The request is a variable of the experiment.** Stage 68's original and
  explicit golden requests describe the same part and score 0/8 and 7/8;
  editing a request makes a NEW case, never an edited one.
- **Grade every body, never `bodies[0]`.** Stage 75's evaluator compares an
  unordered multiset and an AST test pins that it never indexes by position,
  because two disjoint solids fused have exactly the total of the two apart.
- **A refusal case starts from a DETERMINISTIC fixture.** Phase A's refusal
  cases would otherwise record a setup failure as a refusal failure.
- **Retire nothing silently, and keep what was measured in error.** Phase D
  kept the wrong taxonomy letters in its creation files under
  `classes_recorded_in_error`.

## 4 — the instrument, and what can be reused

`arena75`, `evaluate75` and `ground_truth75` generalise to dimensions 1–7
with a wider corpus and no structural change: the observer already records
per-body measurements, declared ids and the raw answer, and the grader
already scores an unordered multiset.

Dimensions **8 and 9 need a second observer**, because they are not
properties of a plan. A measurement question goes through
`questions.answer` and an export through `/session/export`, and neither is
visible in a `PlanGenerationResult`. Building that observer is the first
piece of work the broader corpus needs, and it should be built and
mutation-tested **before** any live call, exactly as Stage 75's was.

## 5 — what would have to be true to start

1. **R2's floor is carried, not re-discovered.** 70.8 % on the committed
   prompt, with the cause recorded here. A dimension-6 case that scores near
   it is reproducing a known number, not finding a new problem.
2. **A per-body observer for measurement and export exists and is
   mutation-tested.** Without it dimensions 8 and 9 cannot be graded at all,
   and a corpus that quietly drops two of its nine dimensions is a corpus
   that measures seven.
3. **The identity is frozen and recorded first**, as
   `arena75.COMMITTED` does: model, prompt version and fingerprint, schema
   name, inlined size and fingerprint. Every number this design produces is
   meaningless against a different one.
4. **The two rates are reported apart from the first run onwards.** Not
   split later — `summarise()` must refuse to produce a combined number, as
   it does today.

Until (2) exists, the honest scope of a broader corpus is dimensions 1–7,
and it should say so rather than quietly covering what it can reach.
