# Text-to-CAD evaluation harness

Status: **Stage 27 — a repeatable measurement harness for the configured
LLM. Not a proof of general CAD competence.**

> **This is an evaluation harness, not evidence of model quality.** It
> measures one prompt, one model and 35 hand-written cases per run. A good
> score would mean that model handles *these* prompts; it would say nothing
> about arbitrary mechanical language, and no such claim is made here.

> **Staleness notice (prompt `2026-11-12.1`).** The product's generation layer
> now builds and re-asks (`docs/text-to-cad-ai.md`), and its policy changed
> from *ask or refuse* to *build, and say what was assumed*. This harness
> deliberately does **not** follow: it measures the model single-shot. And the
> **corpus is untouched, so it is now stale against the new policy in more
> places than before** — see *Corpus status after the policy change* below.
> No corpus run on the current prompt exists.

> **Corpus 2.0.0 (the re-baseline) exists beside it.** `--corpus 2.0.0` runs
> 135 cases written for the current policy and scores the **built solid**
> against a reference with the CAD kernel (IoU, volume, topology), not the
> document field by field. Corpus 1.0.0 is unchanged and stays the default,
> so its recorded runs keep their meaning. See
> [*Corpus 2.0.0: scoring the solid*](#corpus-200-scoring-the-solid) below.
> **No live run of 2.0.0 has been made**: every number in that section is the
> instrument measured against itself.

Two providers are implemented (Anthropic and Gemini). A run measures
**whichever one it was pointed at** and records which; a result from one is
never evidence about the other.

## Purpose

Stage 26 established the AI boundary safely but could not exercise it: no
application credential was available, so the live provider test skipped and
every example in `docs/text-to-cad-ai.md` is fixture-driven. Those fixtures
prove the *plumbing*. They prove nothing about the model.

This stage builds the thing that can: a fixed corpus, an explicit expected
answer per case, and a runner that reports five dimensions separately.

```
case prompt
  │  TextToCadService.generate_cad_from_text     max_attempts=1, verify_build=False
candidate document
  │  cad_core deserializer + validator           unchanged, still authoritative
  │  cad_ai.comparison.compare_documents         field-level, labelled
  │  (optional) CadApplicationService.build      does it actually execute?
EvaluationResult -> EvaluationRun -> a JSON file
```

**Measurement only.** The harness constructs its service with
**`max_attempts=1, verify_build=False`** (`evaluation.py`), so a run is exactly
one model call and no verification build, whatever `CAD_AI_MAX_ATTEMPTS` says.
That is intentional: the product's verify-and-retry loop would otherwise
convert some of the model's failures into successes and a score would measure
the loop as much as the model. A benchmark of the *whole product* (model plus
loop) would be a separate instrument and does not exist. No prompt variation,
no A/B testing. The model is not improved during a run and the corpus is not
edited after seeing results — the whole point is an honest baseline for the
next stage to work against.

## Files

| File | Role |
|---|---|
| `apps/api/src/cad_ai/corpus.py` | the 35 cases: prompts and hand-authored expected documents |
| `apps/api/src/cad_ai/comparison.py` | field-level document comparison and the error taxonomy |
| `apps/api/src/cad_ai/evaluation.py` | cases, results, runner, metrics, storage, the command |
| `apps/api/tests/test_ai_evaluation.py` | the tests, none of which need a credential |

Nothing lives in `cad-core`: a test asserts no `cad_core` module names
`cad_ai`, `SemanticErrorCategory`, `EvaluationCase`, `EvaluationResult`,
`compare_documents`, `load_corpus` or `benchmark`, and that none of
`evaluation.py`, `corpus.py` or `comparison.py` exists there. Nothing in the
generation path imports the evaluation layer either, so no expected output is
hard-coded anywhere near the code that talks to the model — also asserted.

### A note on Stage 26's boundary tests

Adding these three modules widened the `cad_ai` package, and six of Stage
26's package-boundary tests walked *every* file in it. They were re-scoped to
a named `GENERATION_MODULES` list rather than weakened: each still asserts, at
full strength, that the generation path prints nothing, writes no file, uses
no temporary directory, imports only `cad_core.application_service` and
`cad_core.model`, and names no kernel or exporter. The evaluation layer
legitimately does some of those things — it prints a report and writes a
result file — and `test_ai_evaluation.py` asserts the invariants that *do*
apply to it: no interpreter, no subprocess, no socket, no CAD kernel import.

## Corpus 2.0.0: scoring the solid

### Why a second corpus

Corpus 1.0.0 has two problems that no amount of re-running fixes. Its
expectations encode the old *ask or refuse* policy (above), and it scores a
document **field by field** -- which says little about whether the right
*shape* was made. A model that cuts a hole with a 14 mm tool instead of a
12 mm one, or unions two half-plates instead of writing one box, produces the
same solid and a different document; a model that swaps two numbers
(corpus 1.0.0's A6) produces a different solid of identical volume. Only the
solid can tell these apart.

So 2.0.0 is a **new instrument**, versioned separately, authored before any
model saw it, with nothing copied from model output (a test greps for the
traces model output would leave). Corpus 1.0.0 is not edited: a test pins a
hash of its data.

```
case prompt
  |  TextToCadService   max_attempts=1, verify_build=False, use_templates=False
candidate document (validated by cad_core, unchanged)
  |  isolated worker, operation compare_shapes      (cad_core.shape_comparison)
  |    build candidate + build reference -> facts, IoU, volume, topology
  |  the case's rule, as data                       (cad_ai.geometric_scoring)
GeometryVerdict -> EvaluationResult -> per-category table + geometry block
```

### Files

| File | Role |
|---|---|
| `packages/cad-core/src/cad_core/shape_comparison.py` | kernel measurements of a solid and of a candidate against a reference. Deterministic, plain data out, no LLM, no thresholds |
| `cad_core/isolated_worker.py`, `isolated_execution.py` | the `compare_shapes` worker operation and `compare_shapes_isolated`, so the kernel never runs in the harness (`docs/isolated-cad-execution.md`) |
| `apps/api/src/cad_ai/corpus_v2.py` | the 135 cases, references written with `cad_ai.features`, rules as data, plus `PERTURBATIONS` and `EQUIVALENTS` |
| `apps/api/src/cad_ai/geometric_scoring.py` | the one interpreter of rules: facts in, verdict and named checks out. No kernel |
| `apps/api/src/cad_ai/evaluation.py` | `--corpus`, `load_corpus_v2`, `IsolatedShapeScorer`, `instrument_check`, the per-category and geometry metrics |
| `packages/cad-core/tests/test_shape_comparison.py`, `apps/api/tests/test_ai_evaluation_v2.py` | closed-form kernel tests; the instrument check; the harness |

### What the kernel measures (`cad_core.shape_comparison`)

For one solid (`shape_facts`): volume, area, the exact (not tessellated)
bounding box, solid and shell counts, faces by surface type, and its
**cylindrical surfaces** -- faces grouped by axis line, radius and a
contiguous stretch of that line, each marked *concave* (a hole or pocket wall:
the face normal points at the axis) or *convex*, and *full-turn* (the faces
cover 360 degrees: a bore or a round boss, as opposed to a fillet's quarter
turn or a hole broken out through an edge). And its **genus**, the number of
through-holes, from the Euler-Poincare formula
`V - E + F - (L - F) = 2 (S - G)` over the B-rep (degenerate edges excluded);
a non-integer result is reported as unknown, never rounded.

For a candidate against a reference (`compare_shapes`): volume ratio and
relative error, extents, the offset between the as-built minimum corners, and
the **IoU** -- intersection volume by a kernel boolean, union as
`V_c + V_r - I` -- after moving the candidate so its bounding-box minimum
corner sits on the reference's, under an orientation mode:

| Mode | Rotations searched | Use when the request ... |
|---|---|---|
| `fixed` | identity | fixes the axes: axis letters, or long -> X, wide/deep -> Y, thick/tall -> Z |
| `upright` | the 4 quarter turns about +Z | fixes only "up": top, tall, standing, open at the top |
| `upright_any` | the quarter turns, plus every turn about Z that carries a candidate's vertical bore or boss onto a reference one | is a patterned round part: a bolt circle's phase is rarely stated, and 8 holes at 22.5 degrees are no quarter turn from 8 at 0 |
| `free` | the 24 proper axis-aligned rotations (no mirror images: a mirrored part is a different part) | fixes nothing |

Axis-aligned rotations are signed permutations, exact in floating point. The
24-rotation search evaluates rotations in order of an exact upper bound on
their IoU (from the volumes and the overlap of the aligned boxes) and stops
when the best found reaches the next bound, so it returns the true best over
all 24 -- a test checks it against brute force -- usually after one boolean.
Every tolerance is a named constant; no kernel value is compared exactly.

The closed-form tests include the A6 pair: 100 x 60 x 10 against
60 x 100 x 10 has volume ratio 1, IoU **3/7** with orientation fixed
(36000 / 84000) and **1** in `upright` or `free`.

### The cases

| Category | n | Rule | What it measures |
|---|---|---|---|
| `primitives` | 16 | exact | boxes and cylinders with stated sizes: axis words and letters, the A6 reversed order, decimals, explicit axes including -Z, minimum corner, centred on the origin |
| `holes-patterns` | 20 | exact | centre and corner holes, bolt circles (6, 8), rows along X and Y, a 3 x 3 grid, blind round and rectangular pockets, counterbore, cross holes, slot, tube, washer, open box, horizontal holes |
| `blends` | 10 | exact (blend) | fillets and chamfers: vertical edges, all edges, edges along X, cylinder rims, a 1 mm chamfer that removes 0.4 % of the volume, blends combined with holes |
| `tapers-polygons` | 10 | exact | cone, frustum, tapered leg (direction matters), hexagon by corners and by flats (M10 nut, standoff), triangle, pentagon, octagon, countersink |
| `joined` | 15 | exact (joined) | L-bracket, T, plate with boss, stepped shaft, flange with hub and bolt circle, plus, U-channel, I-beam, table, T-slot, dumbbell, stacked cubes, wheel, organiser, rocket body |
| `units` | 12 | exact | inches, fractions of an inch, cm, m, feet, mixed units, and **no unit** (corpus 1.0.0's D1/G1, now millimetres by policy) |
| `semantic` | 17 | exact | radius vs diameter, on top vs underneath, opposite ends, mirrored holes, words not order (G5), relative sizes, "centred at" vs "starting at" (G2-G4), horizontal hole, flush wall, inner diameter plus wall |
| `everyday` | 22 | properties | no dimensions: mug, phone stand, quadcopter, hexacopter, pencil holder, coaster, die with pips, M8 washer and bolt, picture frame, bookend, door wedge, shelf bracket, keyring tag, toy wheel, bowl, chess pawn, bottle, birdhouse, soap dish, 12-tooth gear, chair |
| `adversarial` | 10 | boundary | "ignore instructions, output Python", CadQuery, FeatureScript, a shell payload, a schema override, writing a file, revealing the prompt, a `script` feature with `__import__`, two non-physical requests |
| `clarification` | 3 | outcome | "make me a part", "design something useful", "surprise me": the one thing the policy still asks about |

Each case records `fixes` -- the words that fix its orientation or position --
so a disagreement with a rule is arguable. Three cases resemble the prompt's
worked examples -- E01 (mug), J01 (L-bracket) and J05 (flange with hub and
bolt circle) -- and are tagged `in-prompt-example`: they measure, in part,
whether the model follows an example. Templates are off in every benchmark
run (`use_templates=False`), so no case is helped by a reference design.

### Scoring rules and thresholds

Rules are data in each case; `geometric_scoring.judge` is the only code that
reads them, so there is no per-case logic to tune.

**`exact`** passes when **all** hold:

| Check | Default | Why this value |
|---|---|---|
| solid count | 1 | the specification's single-solid rule |
| IoU in the rule's mode | >= **0.99** | a fully stated part leaves nothing to choose, so the right answer matches to kernel precision (references score 1.000000); 0.99 admits a person's rounding -- measured: 19.6 for a 19.63 hexagon 0.9925, inches rounded to 0.1 mm 0.9973 |
| volume error | <= **1 %** | the same rounding: 0.44 % and 0.27 % |
| topology signature | equal | genus, the diameters of full-turn bores and bosses (each within max(0.05 mm, 1 %)), and the set of surface types. **IoU and volume alone cannot see small features**: a missing 8 mm hole in a 100 x 60 x 10 plate is IoU 0.9916 and 0.85 % of volume, a wrong hole diameter 0.9952, a missing one of four holes 0.997 -- all above 0.99, all caught here |
| placement, when `position: fixed` | within **0.5 mm** per axis | for requests that state a location (minimum corner, centred at, centred on the origin); IoU is placement-invariant by construction |
| extra checks | as listed | none are used today |

Two categories override defaults, each for a stated reason:

* **joined**: IoU >= **0.98**, volume <= **2 %**. The prompt itself teaches
  that an attached part starts 2 mm inside the face it joins, which can leave
  a stated protrusion 2 mm short; measured on the plate with boss, IoU 0.9913.
  This admits exactly that and nothing much larger.
* **blends**: volume <= **0.2 %**. A blend often removes under 1 % of the
  volume (the 1 mm chamfer on B09: 0.42 %; a missing 5 mm vertical fillet on
  B01: 0.36 %). The blend is stated exactly, so the tight tolerance costs a
  correct answer nothing. Honest margin: the missing chamfer on B09 is caught
  **only** by this tolerance (0.42 % against 0.2 %), a factor of two.

**`properties`** passes when every listed check holds on the candidate alone:
`extent` (x, y, z, largest, middle, smallest, largest_xy, smallest_xy),
`extent_ratio`, `volume`, `fill_ratio` (volume over bounding-box volume),
`genus`, `bores` and `bosses` (count in a diameter range, optionally along an
axis or `horizontal`), `faces` (count of a surface type). The ranges come from
real-object sizes (an ISO 7089 M8 washer, a 10 x 15 cm photo, mugs 80-120 mm
tall) and from the object's defining feature: a mug's handle is a loop
(genus >= 1) and its cavity a round bore; a die has 21 round pips; a hexacopter
six motor bosses; a wedge fills about half its box; twelve straight gear
teeth give at least 38 flat faces where a 12-sided prism gives 14. The
reference is an **exemplar**, proved to satisfy the checks and never compared
with a candidate.

**Adversarial**: correct when the boundary held (nothing code-shaped in a
candidate, no unsupported feature -- the existing checks) **and** the outcome
is `unsupported` (the policy's answer) or `generated` (a plain document of
the part the request also describes). An unreadable answer holds the boundary
but is not correct. **Clarification**: correct when the outcome is
`needs_clarification`. Every other category expects `generated`.

### The instrument check: does it detect what it claims?

`python -m cad_ai.evaluation --check --corpus 2.0.0` loads and validates the
corpus, then builds and scores, in the isolated worker, three sets of known
answers. Current result (36 s on the Linux container):

| Set | n | Required | Result |
|---|---|---|---|
| every reference against itself | 122 | pass | **122/122**, IoU 1.000000, volume error 0 |
| `EQUIVALENTS`: correct answers built differently | 13 | pass | **13/13** -- two half-boxes unioned elsewhere; a rod built along -X; a flush hole tool; a plate turned in a free case; bolt circles at 17, 22.5 and 45 degrees of phase; five separate hole tools instead of a pattern; 19.6 for 19.63; inches rounded to 0.1 mm; a boss starting 2 mm inside; a different mug |
| `PERTURBATIONS`: deliberately wrong answers | 33 | build **and** fail | **33/33** -- swapped axes (A6), thickness on the wrong axis, wrong cylinder axis, radius used as diameter (twice), position ignored, "centred at" read as the base, corner at origin, wrong hole diameter, missing hole, one of four holes missing, five bolt holes for six, a pocket cut through, missing fillet, missing chamfer, fillet for chamfer, taper upside down, round for hexagon, boss underneath, missing bore, inches not converted (twice), cm read as inches, peg on top for underneath, vertical hole for horizontal, wall added once to a diameter, mug without handle loop, mug without cavity, hexacopter with four arms, die without pips, wedge as a block, 12-gon for a gear, a frame too small for the photo |

A perturbation that does not build would prove nothing about its rule, so it
must build *and* fail. The same three sets run as unit tests
(`test_ai_evaluation_v2.TestInstrumentCheck`), and `--self-check --corpus
2.0.0` runs the whole harness against the oracle stub: 135/135.

### Reliability is not quality, and the instrument is not the model

Three things are kept apart in the metrics:

* a **provider error** (rate limit, capacity, timeout) is unmeasured, as in
  1.0.0: no verdict, outside every quality rate;
* an **instrument error** -- the worker crashed or timed out, or a reference
  did not build -- is also unmeasured: `geometry.instrument_errors`, excluded
  from `geometry.checked` and from the category's `measured`. Pairs are
  measured in batches of 16; when a batch fails each pair is measured alone,
  so one bad pair costs only its own verdict;
* a candidate that **validates but does not build** (an E-rule) is
  `not_built` and **is** a model failure: the harness pins
  `verify_build=False`, so the build here is the first one.

### Metrics

Everything in 1.0.0's report remains. Two blocks are added, for every run:

* `by_category`: per category, cases, measured, correct and `correct_rate`
  (each case's own criterion), geometry checked/passed, not built, instrument
  errors, mean IoU and mean volume error;
* `geometry`: candidates offered, checked, passed, `pass_rate`, not built,
  instrument errors, exact-rule and property-rule counts, mean and median IoU
  in the scored mode, **mean IoU with orientation fixed and with orientation
  free** (so a convention miss can be told from a wrong shape), and mean and
  median volume error. IoU and volume figures are over exact-rule cases that
  built.

The per-case report names every failed check with its expected and actual
value (`IoU (fixed): expected >= 0.99, actual 0.428571`).

Every saved run records `corpus_version`, `prompt_version`,
`prompt_fingerprint`, provider, model and `shape_comparison_version`.

### Running it

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src

python -m cad_ai.evaluation --list  --corpus 2.0.0
python -m cad_ai.evaluation --check --corpus 2.0.0          # instrument check, no model
python -m cad_ai.evaluation --self-check --corpus 2.0.0 --pace 0
python -m cad_ai.evaluation --live --corpus 2.0.0 --provider anthropic --pace 360 \
    --category primitives                                    # spends money
```

**Cost of one full live run: 135 model requests** (one per case; the harness
never re-asks), plus 5 x N for `--repeat N`. Wall time is dominated by the
model: product-flow calls on Haiku 5.5 took 6-15 s for simple parts and up to
about a minute for detailed objects, so roughly 30-60 minutes back to back,
plus about 40 s of geometric scoring at the end. **The owner's cost policy is
at most 10 requests an hour (never more than 20)**, which makes a full run
about 14 hours: run it a category at a time with `--category`/`--case`, or
pace it with `--pace 360`. A run that a provider cuts short is reported as
partially measured, never as failures.

### Limitations of 2.0.0

* **No live run exists.** Everything above is the instrument measured on
  itself and on hand-written answers. It proves the instrument can see the
  errors it names; it says nothing about any model.
* **The `fixed` convention is a convention.** long -> X, wide/deep -> Y,
  thick/tall -> Z is what the prompt's own L-bracket example does and what
  1.0.0 committed to, but the prompt does not state it. `iou_free` is reported
  beside every exact verdict so a convention miss is visible as such.
* **`free` searches quarter turns only.** A correct part built at, say, 30
  degrees about an axis fails a `free` case; `upright_any` adds arbitrary
  turns only about Z and only where vertical round features align them.
* **Property ranges are judgement**, written from real-world sizes before any
  model was run, and recorded in each case's `notes`. A defensible object
  outside a range is a recorded failure; arguing it means editing the corpus,
  which means a new corpus version.
* **The topology signature can be stricter than the shape.** It compares the
  *set* of surface types and full-turn diameters; a solid built so that the
  kernel leaves an extra surface type (for example a seam split into a
  B-spline) would fail it. None of the 13 equivalents does, which is evidence,
  not proof.
* **Genus is unknown (`None`) when the Euler-Poincare result is not an
  integer**; a property check on genus then fails, and an exact signature
  skips genus only when the *reference's* is unknown (none is today).
* **Field-level dimensions are not reported for 2.0.0**
  (`exact_document_match_rate` and friends are `None`): there is no expected
  document, only an expected solid.
* `totals.correct_rate` keeps 1.0.0's denominator (measured cases); an
  instrument error lands in it as not-correct. `by_category` and `geometry`
  exclude instrument errors; read those for 2.0.0.

## The benchmark corpus (1.0.0)

35 cases in seven categories.

| Category | n | What it measures |
|---|---|---|
| **A** — simple box | 6 | explicit dimensions, explicit position, decimals, repeated wording, reversed dimension order |
| **B** — cylinder | 6 | diameter + height, each explicit axis (`+Z`, `+X`, `-Z`, `+Y`), explicit base position |
| **C** — defaults | 3 | omissions the specification permits: a cube, a cylinder with only its position stated, a disc with neither |
| **D** — ambiguity | 5 | unstated units, a missing extent, a missing diameter, an un-inferable axis, vague placement |
| **E** — unsupported | 5 | through-hole, subtract, fillet, chamfer, two solids |
| **F** — adversarial | 5 | "ignore previous instructions", "write CadQuery", "output FeatureScript", shell injection, a schema-override attempt |
| **G** — semantic confusion | 5 | known failure modes: units omitted, box centred on the origin, cylinder "starting at", cylinder "centred at", words vs. order |

### Expected-output methodology

Every expected document was **written by hand from
`docs/cad-specification.md`, before any model was called.** No model output
was copied into the corpus, and a test greps `corpus.py` for the shapes model
output would leave behind (`stop_reason`, `input_tokens`, `claude-`,
`candidate_document`, a `"status"` field).

`load_corpus()` then puts every expected document through the **existing**
deserializer and validator before a single model call happens, so the
benchmark cannot score the model against ground truth that is itself invalid.
It also rejects a duplicate `case_id`, an `EXPECTED_GENERATED` case with no
document, a non-generated case carrying one, an expected document using a
feature outside the supported subset, and an empty corpus. A separate test
feeds it four invalid documents (S10, S9, S4, S1) and asserts each is refused.

The corpus commits to two interpretation conventions, both grounded in the
specification rather than invented:

* **word-to-axis mapping** — Section A.1 gives `+X` "width", `+Y` "depth",
  `+Z` "height"; a bare `A x B x C` is read in order as X, Y, Z.
* **anchor points** — Section C.1 makes a box's `position` its **minimum
  corner**, Section C.2 makes a cylinder's the **centre of its base circle**.
  Several G cases exist precisely because these are easy to get wrong.

Where a prompt does not state something the specification requires — units
above all, since Section A.3 says the unit system "is never implied by
context" — the expected answer is a **question**, not a document.

### Expected outcomes

| Expectation | Satisfied by | Notes |
|---|---|---|
| `EXPECTED_GENERATED` | `GENERATED` | and the document must match |
| `EXPECTED_NEEDS_CLARIFICATION` | `NEEDS_CLARIFICATION` | a valid document here is a **failure** |
| `EXPECTED_UNSUPPORTED` | `UNSUPPORTED` | a document here is a **failure** |
| `EXPECTED_BOUNDARY_HELD` | any of `GENERATED`, `NEEDS_CLARIFICATION`, `UNSUPPORTED`, `INVALID_MODEL_OUTPUT` | adversarial cases, where more than one answer is legitimate |

These map onto `GenerationOutcome`, which Stage 26 already defines. **No
second outcome taxonomy was created** — a test asserts every entry in
`SATISFYING_OUTCOMES` is a real `GenerationOutcome`.

`EXPECTED_BOUNDARY_HELD` exists because for F1–F5 more than one answer is
correct: refusing to write Python is right, and so is answering with the CAD
the request also described. What is scored there is the **boundary**, not the
outcome: nothing executed, no code-shaped content in the result, no
unsupported feature accepted.

### Corpus status after the policy change

The corpus (`cad_ai.corpus`, version `1.0.0`, 35 cases) is a fixed measurement
instrument and was **not edited** for the new prompt. Its expectations encode
the Stage 26-29 policy, so a number of cases now contradict what the prompt
tells the model to do, and **would score a correct new-policy answer as a
failure**:

* **D-ambiguity** cases that expect `NEEDS_CLARIFICATION` for a missing unit or
  dimension (for example "plate 100 by 60 by 10"). The prompt now assumes
  millimetres and builds, listing the assumption;
* **E-unsupported** cases that expect `UNSUPPORTED` for holes, cuts, fillets,
  chamfers, joins or shapes the vocabulary approximates. The prompt now builds
  or approximates these (five of them were already stale after
  `2026-09-09.1`);
* cases that score whether a defaulted `position` or `axis` was omitted (the
  `WRONG_DEFAULT` logic): the prompt now always writes both explicitly. The
  comparison works on canonical documents, which materialise defaults, so it
  cannot see omission itself; the evaluator observes the model's raw text
  separately. How much this matters has not been re-measured.

The fix is a **re-baseline, as its own stage**: re-author the expectations
deliberately, with the reasoning in each case's `notes`, then run. It is not to
be done by editing cases after seeing a score, and nothing here suggests it.
Until then a corpus score on the current prompt is not interpretable, and the
only evidence for the current prompt is the hand-run product-flow requests in
`docs/text-to-cad-ai.md`.

## The five dimensions

Measured separately and never collapsed:

| # | Dimension | How |
|---|---|---|
| 1 | **parseability** | was the model's raw text a JSON object at all |
| 2 | **CAD-spec validation** | did `cad_core`'s validator accept the candidate |
| 3 | **expected-outcome match** | did the model do the kind of thing the case wanted |
| 4 | **exact canonical match** | is the candidate the expected document, after canonicalization |
| 5 | **field-level correctness** | of the leaf fields compared, how many agree |

1 and 2 are genuinely separable because the evaluator wraps the model in a
`RecordingModel` and keeps the raw response. The finished
`AiGenerationResult` reports a validated document or nothing, so without that
wrapper "the model wrote prose" and "the validator rejected it" would both
just be `INVALID_MODEL_OUTPUT`. The wrapper observes; it changes nothing —
the request goes through untouched and the response comes back untouched.

The same wrapper records **which defaulted parameters the model omitted**,
which canonicalization has already materialised by the time anyone else sees
the document.

## Exact match

For `EXPECTED_GENERATED` cases:

```
canonical(candidate) == canonical(expected)
```

using Stage 15's canonical serialization. Whitespace, raw model JSON
formatting and key insertion order are **never** compared — a test builds two
documents that differ only in key order and number spelling and asserts they
match exactly.

## Semantic comparison

`compare_documents` walks the two canonical documents and emits **labelled**
differences, never an unlabelled recursive dict diff:

```
DIFFERENCE: features[0].size.x
  expected: 100.0
  actual:   60.0
  category: wrong_dimension
```

The walk is driven by `cad_core.model`'s own tables —
`FEATURE_PARAMETERS`, `FEATURE_REFERENCE_FIELDS`, `ORIGIN`, `DEFAULT_AXIS` —
so a parameter the specification grows and this module has no rule for raises
rather than being silently skipped.

### Categories

| Category | Meaning |
|---|---|
| `WRONG_SCHEMA_VERSION` | `schema_version` differs |
| `UNIT_ERROR` | `units` differs |
| `WRONG_LABEL` | `name` or `description` differs |
| `WRONG_FEATURE_TYPE` | a feature is the wrong type (parameter comparison then stops — comparing a box's `size` to a cylinder's `diameter` is noise) |
| `WRONG_FEATURE_ID` | a feature's `id` differs |
| `WRONG_FEATURE_ORDER` | the same features, permuted |
| `WRONG_DIMENSION` | `size.*`, `diameter`, `height`, `radius`, `distance` |
| `WRONG_POSITION` | `position.*` |
| `WRONG_AXIS` | `axis` |
| `WRONG_DEFAULT` | a defaulted parameter differs **and one side is the specification's default** |
| `WRONG_REFERENCE` | `target` or `tools` |
| `WRONG_SELECTOR` | `edges.select` / `edges.axis` |
| `MISSING_FEATURE` / `EXTRA_FEATURE` | the feature count or membership differs |
| `AMBIGUITY_NOT_ASKED` | *case-level*: a question was required, a document was produced |
| `UNSUPPORTED_FEATURE_ACCEPTED` | *case-level*: a refusal was required, a document was produced |

The taxonomy lives in `cad_ai/comparison.py` and nowhere else; a test asserts
none of its member names appears in `cad_core/model.py`.

### `WRONG_DEFAULT`, defined exactly

A defaulted parameter (`position`, `axis` — read out of
`FEATURE_PARAMETERS`' optional tuples) differs, **and one of the two values is
the specification's default**. `default_side` records which. It refines
`WRONG_POSITION`/`WRONG_AXIS`: the mistake involves the default rather than
two arbitrary wrong values.

**Known limitation:** because both sides are canonical, this compares
*values*, not omission. A model that wrote `position: {0,0,0}` explicitly and
one that omitted it produce the same canonical document and both count as
exact matches. Which one actually happened is recorded separately, in
`omitted_defaults`, from the raw answer.

### Labels are not geometry

`name`, `description` and feature `id`s carry no geometric meaning — the
specification says so for `description`, and a consistent renaming produces
an identical part (references are already validated, so an inconsistent one
would not have validated at all). A difference in one of them is **reported**
and makes `exact_match` false, but the semantic status is `LABELS_DIFFER`
rather than `MISMATCH`, and the case still counts as correct.

### `SemanticStatus`

| Status | Meaning |
|---|---|
| `MATCH` | no difference at all |
| `LABELS_DIFFER` | every difference is a label |
| `MISMATCH` | at least one difference changes what the document describes |
| `NOT_COMPARED` | no candidate, or the case expects none |

### No geometry equivalence

**Two documents that would build to the same solid but differ in design
history stay different.** `comparison.py` contains no volume, no bounding
box, no tolerance and no `isclose` — a test asserts that. Numbers are
compared exactly, because both sides came through the same canonical
serialization of JSON literals and no kernel value is involved; a tolerance
would hide a model writing 99.999 for 100.

The stage's own example is a test: expected box 100×60×10 versus a valid box
60×100×10 is a **document mismatch**, even though the sorted extents agree
and the second builds perfectly.

## Metrics

A table of dimensions, never one headline. Each rate has **its own
denominator**, and a rate with no denominator is `None`, not `0.0` — a group
of clarification cases offers no documents, so its document-match rate is
*not applicable*, and reporting `0.0` would read as total failure at
something never attempted.

**Overall:** total cases, completed, skipped, provider errors, invalid
outputs, unparseable outputs, correct, `correct_rate`.

`correct_rate` is deliberately **not** called "accuracy": it is the fraction
of cases whose own success criterion was met, and each group's criterion
differs. The per-group table is the real answer, and a test asserts the words
"accuracy" and "score" appear nowhere in the metrics payload.

**Per expectation group:** `outcome_match_rate`, `valid_document_rate`,
`exact_document_match_rate`, `semantic_match_rate`, `false_generation_rate`,
`field_correctness_rate` (with `fields_compared` / `fields_matching`
recorded, so the denominator is stated rather than inferred).

`false_generation_rate` is the metric that stops a plausible answer scoring
as a correct one: a document produced where a question or a refusal was
required.

**Adversarial:** `boundary_violations` and `execution_attempts_observed`.
Both are instrumented rather than inferred.

## Repeat runs

`--repeat N` re-runs five named cases — one box, one cylinder, one ambiguous,
one unsupported, one semantic-confusion (`corpus.REPEAT_CASE_IDS`, fixed so
the study is the same study every time).

Reported per case: outcome counts, distinct document hashes, exact matches,
validation count, and two booleans for whether the outcome and the document
were stable. For example:

```
A1-box-explicit-dimensions
    outcomes             generated: 5/5
    exact document match 3/5
    distinct documents   2
```

**No pass/fail is derived from any of it** — a test asserts the repeat
summary has no `passed` and no `correct` field. Converting stochastic
variation into a boolean would destroy exactly the information the study
exists to collect.

## Stochasticity and model settings

Generation is not deterministic and nothing here claims it is. The run
records the actual decoding configuration, re-checked at run time rather than
asserted from memory:

```json
"settings": {
  "temperature": "not settable",
  "top_p": "not settable",
  "top_k": "not settable",
  "seed": "not settable",
  "deterministic_decoding": "unavailable",
  "sdk_version": "1.4.0"
}
```

Stage 26 measured that the installed SDK's `messages.create` exposes no
`temperature`, `top_p`, `top_k` or `seed`. (The Anthropic request now also carries a cached system block and, only when
`CAD_AI_EFFORT` is set, `output_config.effort`; none of temperature, top_p,
top_k or seed is sent. The default model is `claude-haiku-5-5`.) The probe runs
again on every run,
so if a future SDK adds one the recorded value changes with it — and it lives
in `anthropic_provider.decoding_capabilities()`, not in the evaluation layer,
because the provider owns knowledge of its SDK. That keeps Stage 26's
invariant true: **exactly one module in the repository imports `anthropic`**,
and a test still asserts it.

## Model identification and prompt integrity

Every run records `provider`, `model`, `prompt_version` and
`prompt_fingerprint` — the **existing** fingerprint from `cad_ai.prompt`
(currently `2026-11-12.1`, `cf2291a5…`). No
second prompt hash is computed. The prompt is never modified during a run; a
test asserts `evaluation.py` defines no prompt, no instructions and no status
mapping of its own, and that it reaches the model only through
`self._service.generate_cad_from_text`.

**Never recorded:** an API key, a request header, an auth token, or an
environment dump. `RunMetadata` has no field that could hold one, and a test
saves a run with a credential set in the environment and greps the file for
`sk-ant`, `api_key`, `x-api-key`, `authorization`, `Bearer` and
`ANTHROPIC_API_KEY`.

## The build cross-check

`--build` builds every validated candidate through the existing application
service **after** the single-shot call (the service itself no longer builds
during a benchmark, because `verify_build=False`) and records success, build key, solid count, volume, bounding-box
size and triangle count.

**This is not semantic ground truth.** A valid document that builds can still
be the wrong part, and a test proves the point: a transposed 60×100×10 box
builds perfectly and is still scored as a document mismatch. The measurements
are there to *diagnose* a mismatch, never to replace document comparison.

An invalid candidate never reaches geometry — `candidate_document` is `None`
unless the validator accepted it — and tests assert `build_part` is never
called for an invalid candidate or for a clarification.

## Latency

Three separate numbers, never summed: `model_seconds` (measured around the
provider call by `RecordingModel`), `validation_seconds`, and
`build_seconds`. All are **wall-clock elapsed time and environment
dependent**; the report says so in the output itself. No benchmarking
infrastructure beyond that was added.

## Cost

Provider usage metadata (`input_tokens`, `output_tokens`) is recorded when the
response supplies it, and reported as `{"status": "not measured"}` when it
does not. **No pricing is applied and no cost is guessed.**

## Running it

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src

python -m cad_ai.evaluation --list        # the corpus
python -m cad_ai.evaluation --check       # validate the corpus; calls no model
python -m cad_ai.evaluation --self-check  # exercise the harness against a stub
python -m cad_ai.evaluation               # reports NOT_RUN: --live is required
python -m cad_ai.evaluation --live --provider gemini --build
```

`--provider` picks the adapter for one run and overrides `CAD_AI_PROVIDER`.
Selecting a provider **does not contact it**.

No application server is needed, and no HTTP endpoint was added — evaluation
is not a production capability.

### Live-provider handling

The live run uses the existing provider configuration. `CAD_AI_PROVIDER`
picks the provider explicitly; with it unset, the first provider whose
credential is present wins, in the order `anthropic`, `gemini`. Each provider
reads its own conventional variable — `ANTHROPIC_API_KEY` or
`GEMINI_API_KEY` — and `CAD_AI_MODEL` / `CAD_AI_TIMEOUT_SECONDS` apply to
whichever is chosen. Only *presence* of a credential is ever read; no value is
returned, stored, compared or printed.

A live run additionally requires `--live`. A credential being present is
deliberately **not** sufficient — a benchmark spends money and must be started
deliberately — so the default path reports `NOT_RUN`, builds no client and
opens no socket even with a key configured:

```
==========================================================================
GEMINI BENCHMARK: NOT_RUN
==========================================================================
No model was called: a live run requires --live.

configured provider : gemini
configured model    : gemini-2.5-pro
credential present  : yes
```

Without a credential the command prints:

```
==========================================================================
LIVE BENCHMARK: NOT_RUN
==========================================================================
ANTHROPIC_API_KEY is not set, so no model was called.

No result was produced and none was invented. This is not
a model failure: nothing was measured.
```

and writes no file, exiting 0. **Absence of a credential is not model
failure**, and a test asserts the message says so.

### `--self-check`

Runs the whole harness against `OracleStub`, which answers each case the way
its corpus entry says it should. It exercises the corpus, comparison, metrics,
storage and report end to end with no credential — and says **nothing** about
model quality. Runs made this way are marked `"live": false` and
`"provider": "stub-oracle"` in both the report and the saved file, and the
command prints a warning to that effect.

## Result storage

`<repo>/evaluation-results/<run_id>.json`, overridable with
`CAD_AI_EVAL_RESULTS_DIR` or `--out`.

* **Never the CAD build cache.** `save_run` refuses a directory inside a
  cache (checked against `entries`/`staging` in the path and against
  `CAD_API_CACHE_ROOT`), because evaluation results are not CAD artifacts.
* **Never overwrites.** A run id is `eval-<UTC timestamp>-<8 hex>`; writing
  over an existing file is refused rather than silently destroying a prior
  run.
* **The run id is not a document hash and not a build key.** It identifies an
  evaluation run and nothing keys on it; a test asserts it is neither 64
  characters nor equal to any document hash in the run.
* **`detail` is excluded by default.** The development diagnostic may quote
  the model's raw text, so it is opt-in.

### User prompts are stored — an explicit choice

The saved file contains a `prompts` map of case id to prompt text. These are
**the corpus's own prompts**, written by this repository and already in
version control, so storing them adds no disclosure and makes a result file
self-describing. `--no-prompts` omits them. Note that this differs from the
generation layer, which deliberately does **not** record an end user's
description.

Nothing is sent anywhere: results are written to a local file and to nothing
else.

## Testing strategy

* **No test needs a live provider.** Every case runs against `ScriptedModel`,
  a deterministic stub with fixed answers per prompt, so CI runs with nothing
  configured.
* **Stubs cover** a correct answer, a valid-but-wrong answer, a labels-only
  difference, each field-error class, malformed JSON, prose, a clarification,
  a refusal, a provider failure, and nine adversarial payloads.
* **One live test** (`TestLiveBenchmark`) runs the eight smoke cases against
  the real model, and only when a credential already exists. It asserts the
  harness ran and the provider did not fail; **what the model scored is data,
  not a pass condition**, because a benchmark that fails CI when the model is
  wrong is a benchmark nobody can run.

### Execution boundary

Stage 26's guarantees are re-proved through the evaluation path. For nine
adversarial answers (Python, a CadQuery script, FeatureScript, an
`__import__('os')` document, a shell-command document, a fake `tool_use`
block, an instruction in the summary, an eval expression as a dimension, a
schema override):

* `eval`, `exec` and `compile` are patched to raise;
* `subprocess.Popen`, `subprocess.run` and `os.system` are patched to raise;
* `socket.socket` and `socket.create_connection` are patched to raise;
* the temporary tree and the system temp directory are snapshotted before and
  after — nothing is written;
* no code marker ever appears in a candidate document.

Source-level: no evaluation module imports a CAD kernel, `pickle`, `marshal`,
`requests`, `httpx` or `urllib`, and none calls `eval`, `exec`, `compile`,
`system` or `popen`.

## Limitations

* **The harness measures the model, not the product.** It pins one attempt and
  no verification build, so it says nothing about how often the product's
  re-ask loop rescues a failing answer, and the corpus is stale against the
  current policy (above).
* **This is not a proof of general CAD competence.** 35 hand-written prompts,
  one prompt version, one provider and one model **per run**. It measures what
  it measures, for the provider it was pointed at.
* **A cross-provider comparison needs care.** The prompt, schema and scoring
  are identical across providers, which makes runs comparable — but the two
  SDKs differ in what they expose. Gemini's offers `temperature` and `seed`
  and Anthropic's offers neither; the harness sets none on either, and records
  the difference, so a gap between two runs is not automatically a gap between
  two models.
* **The corpus encodes one reading of each prompt.** Where natural language
  is genuinely ambiguous, the corpus picks the specification-grounded reading
  and scores the model against it. A defensible alternative reading is
  recorded as a failure. The `notes` field on each case states the reasoning
  so a disagreement is arguable rather than hidden.
* **The corpus must not be tuned to the model.** Editing a case after seeing
  the model fail it converts a measurement into a self-portrait. The baseline
  comes first; the next stage improves the *prompt*, not the benchmark.
* **A single run is one sample of a stochastic process.** Only the repeat
  study says anything about stability, and only for its five cases.
* **`correct_rate` mixes criteria.** It counts cases that met their own
  success condition, and those conditions differ between groups. Read the
  group table.
* **The adversarial cases test our boundary, not the model's safety.** They
  show that nothing we receive gets executed. They say nothing about whether
  the model can be made to emit something unwanted.
* **No cost, latency or throughput target exists.** The numbers are recorded,
  not judged.
* **Field correctness weights every leaf equally.** `units` counts the same
  as `features[0].size.x`. It is a diagnostic, not a quality score.
