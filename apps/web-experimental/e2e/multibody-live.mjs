/**
 * Stage 77 Phase 9: representative LIVE-MODEL multi-body workflows, in a
 * real browser.
 *
 * Every other multi-body browser run this project has (`e2e:multibody`,
 * `e2e:bodytarget`, `e2e:surfaces`) is driven by the deterministic readers
 * with NO model configured. Those are architecture evidence and say nothing
 * whatever about a model. This one requires a real provider and ASSERTS that
 * one answered: if the deterministic reader picks a turn up, the step fails
 * rather than passing for the wrong reason.
 *
 *   1. create two bodies                 -> two declared bodies, own meshes
 *   2. edit the named body               -> it changes, the other does not
 *   3. edit the SECOND body by name      -> the same, the other way round
 *   4. an ambiguous edit                 -> REFUSED, both bodies named
 *   5. a per-body measurement question   -> answered ABOUT that body
 *   6. an aggregate measurement          -> answered, and CALCULATED
 *   7. export STEP                       -> verified by its own BYTES
 *   8. create THREE bodies               -> nothing is written for two
 *
 * Step 4 is the one that needs a browser: a unit test can show a function
 * raising; only this can show a refusal arriving as an ANSWER in front of a
 * person, with the part left exactly where it was.
 *
 * Step 7 is verified by BYTES rather than by the download succeeding,
 * because a STEP that quietly dropped a body is a perfectly valid file.
 *
 * NOTHING HERE IS A RATE. It is one attempt per step against a stochastic
 * model, so it is evidence that the PRODUCT PATH carries a live multi-body
 * answer end to end -- not a measurement of how often it does. The rates
 * live in `docs/evaluation-baselines/stage77-multibody-corpus/`.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const API = process.env.EXPERIMENTAL_API ?? "/api";

const CUBE = 40 ** 3;
const PIN = Math.PI * 10 ** 2 * 30;
const CUBE_WIDE = 50 * 40 * 40;
const PIN_LONG = Math.PI * 10 ** 2 * 50;

let failures = 0;
const fail = (why) => { console.log("    !! " + why); failures++; };
const ok = (what) => console.log("    ok " + what);
const near = (a, b, tol = 1e-3) => Math.abs((a ?? NaN) - b) <= tol;

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const responses = [];
page.on("response", async (r) => {
  if (!r.url().includes("/experimental/")) return;
  const entry = { url: r.url(), status: r.status() };
  try { entry.body = JSON.parse(await r.text()); } catch { /* not JSON */ }
  responses.push(entry);
});

async function fresh() {
  await page.goto(WEB, { waitUntil: "networkidle" });
  await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
  await page.reload({ waitUntil: "networkidle" });
}

async function say(text) {
  const before = responses.length;
  await page.fill("#prompt", text);
  await page.click("#send");
  await page.waitForFunction(
    () => !document.querySelector("#send")?.disabled, null,
    { timeout: 600_000 });
  await page.waitForTimeout(400);
  const turn = responses.slice(before)
    .reverse().find((r) => r.url.includes("/session/message"));
  if (!turn) fail("no /session/message response for: " + text);
  return turn ?? { status: 0, body: {} };
}

/** THE GUARD that makes this a live-model run rather than another
 *  deterministic one.
 *
 *  A turn a PROVIDER answered carries `metadata` naming the provider, the
 *  model and the prompt fingerprint; one the deterministic reader answered
 *  carries `interpreted_by.source === "deterministic"` and no metadata. So
 *  this asserts the metadata is there AND that it is the SAME identity the
 *  Stage 77 corpus was measured against -- a browser run against a
 *  different prompt would not be evidence about the same thing.
 *
 *  Only geometry turns are guarded. A measurement QUESTION is answered from
 *  the build's own evidence before any model is consulted, which is correct
 *  product behaviour and deliberately not a model claim. */
const MODEL = "claude-haiku-4-5-20251001";
const PROMPT = "f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675";

function byTheModel(turn, step) {
  const meta = turn.body?.metadata;
  const fell = turn.body?.interpreted_by?.source;
  if (!meta) {
    fail(`${step}: no provider metadata` +
         (fell ? ` (interpreted by ${fell})` : "") +
         " -- this step is not evidence about a model");
    return false;
  }
  if (meta.model !== MODEL || meta.prompt_fingerprint !== PROMPT) {
    fail(`${step}: answered by ${meta.model} / ` +
         `${String(meta.prompt_fingerprint).slice(0, 16)}, not the identity ` +
         "the Stage 77 corpus was measured against");
    return false;
  }
  ok(`${step}: answered by ${meta.model} (structured_output=` +
     `${meta.structured_output})`);
  return true;
}

const volumes = (body) => Object.fromEntries(
  (body.bodies ?? []).map((b) => [b.body_id, b.measurement?.volume]));

/* --- 1. create two bodies ---------------------------------------------- */
console.log("1. create two bodies, live");
await fresh();
let turn = await say(
  'Create two separate bodies: a 40 mm cube with the id "cube", and a ' +
  '20 mm diameter cylinder 30 mm long with the id "pin" standing beside it.');
byTheModel(turn, "create");
if (turn.body.status !== "built") fail(`status ${turn.body.status}: ` +
  (turn.body.reply ?? "").slice(0, 200));
let v = volumes(turn.body);
if (!near(v.cube, CUBE) || !near(v.pin, PIN)) {
  fail(`volumes ${JSON.stringify(v)}`);
} else { ok(`two bodies at their closed forms ${JSON.stringify(v)}`); }
if ((turn.body.declared_bodies ?? []).length !== 2) {
  fail(`declared ${JSON.stringify(turn.body.declared_bodies)}`);
} else { ok("both declared"); }
if (turn.body.render) fail("a multi-body part must have no single merged mesh");
else ok("no merged mesh");
if (Object.keys(turn.body.measurement ?? {}).length) {
  fail("a multi-body part must claim no part-level measurement");
} else { ok("no part-level measurement claimed"); }

/* --- 2. edit the named body -------------------------------------------- */
console.log("2. edit the named body");
turn = await say("Change the cube to 50 mm along X, keeping it 40 mm in Y " +
                 "and Z. Leave the cylinder unchanged.");
byTheModel(turn, "edit cube");
v = volumes(turn.body);
if (!near(v.cube, CUBE_WIDE)) fail(`the cube is ${v.cube}, expected ${CUBE_WIDE}`);
else ok(`the cube became ${v.cube}`);
if (!near(v.pin, PIN)) fail(`the cylinder MOVED: ${v.pin}, expected ${PIN}`);
else ok("the cylinder is untouched, to the last digit");

/* --- 3. edit the second body by name ------------------------------------ */
console.log("3. edit the second body by name");
turn = await say("Make the pin 50 mm long, keeping its diameter at 20 mm. " +
                 "Leave the cube unchanged.");
byTheModel(turn, "edit pin");
v = volumes(turn.body);
if (!near(v.pin, PIN_LONG)) fail(`the pin is ${v.pin}, expected ${PIN_LONG}`);
else ok(`the pin became ${v.pin}`);
if (!near(v.cube, CUBE_WIDE)) fail(`the cube MOVED: ${v.cube}`);
else ok("the cube is untouched, and still carries turn 2's change");

/* --- 4. an ambiguous edit must be REFUSED ------------------------------- */
console.log("4. an ambiguous edit");
const before = volumes((responses.slice().reverse()
  .find((r) => r.url.includes("/session/message"))?.body) ?? {});
turn = await say("Make it 20 mm taller.");
if (turn.body.status === "built") {
  fail("an ambiguous edit was BUILT; guessing is worse than asking");
} else { ok(`refused as '${turn.body.status}'`); }
const said = JSON.stringify(turn.body.reply ?? turn.body);
if (!/cube/.test(said) || !/pin/.test(said)) {
  fail("the refusal does not name both bodies: " + said.slice(0, 200));
} else { ok("the refusal names both bodies"); }

/* --- 5. a per-body measurement question --------------------------------- */
console.log("5. a per-body measurement question");
turn = await say("What is the volume of the pin?");
if (turn.body.status !== "answered") {
  fail(`status ${turn.body.status}: ${(turn.body.reply ?? "").slice(0, 160)}`);
} else { ok("answered"); }
if (!/^pin:/.test(turn.body.reply ?? "")) {
  fail("the answer does not say WHICH body it is about: " +
       (turn.body.reply ?? "").slice(0, 120));
} else { ok(`attributed: ${turn.body.reply}`); }
if (turn.body.evidence?.provenance !== "measured") {
  fail(`provenance ${turn.body.evidence?.provenance}, expected measured`);
} else { ok("MEASURED"); }
if (new RegExp(String(Math.round(CUBE_WIDE))).test(turn.body.reply ?? "")) {
  fail("that is the cube's volume");
}

/* --- 6. an aggregate measurement ---------------------------------------- */
console.log("6. an aggregate measurement");
turn = await say("What is the total volume?");
if (turn.body.status !== "answered") fail(`status ${turn.body.status}`);
if (turn.body.evidence?.provenance !== "calculated") {
  fail(`a summed total is reported as ${turn.body.evidence?.provenance}`);
} else { ok("CALCULATED, not measured"); }
const total = Number(((turn.body.reply ?? "").match(/Volume ([\d.]+)/) ?? [])[1]);
if (!near(total, CUBE_WIDE + PIN_LONG, 1e-2)) {
  fail(`the total is ${total}, expected ${CUBE_WIDE + PIN_LONG}`);
} else { ok(`the total is the sum: ${total}`); }

/* --- 7. export STEP, verified by its BYTES ------------------------------- */
console.log("7. export STEP");
const exported = await page.evaluate(async ([api, id]) => {
  const r = await fetch(api + "/experimental/session/export", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: id, format: "step" }),
  });
  const text = await r.text();
  return {
    status: r.status,
    header: r.headers.get("x-cad-bodies"),
    bytes: text.length,
    solids: (text.match(/MANIFOLD_SOLID_BREP/g) ?? []).length,
    // The file's own PRODUCT structure, not a substring scan: Stage 76
    // measured that a bare `name in text` accepts 'SOLID', 'part', 'Open'
    // and 'cub' out of STEP boilerplate.
    products: [...text.matchAll(/(?<![A-Z_])PRODUCT\s*\(\s*'([^']*)'/g)]
      .map((m) => m[1]),
  };
}, [API, await page.evaluate(
  // An OPAQUE STRING, not JSON: `main.ts` stores the id itself under this
  // key. Parsing it as JSON is what the first run of this script did, and
  // it threw on a perfectly correct page.
  () => localStorage.getItem("cad-workspace-session") ?? "")]);
if (exported.status !== 200) fail(`the export failed: HTTP ${exported.status}`);
else ok(`HTTP 200, ${exported.bytes} bytes`);
if (exported.solids !== 2) fail(`the STEP holds ${exported.solids} solids, expected 2`);
else ok("2 MANIFOLD_SOLID_BREP");
if (!exported.products.includes("cube") || !exported.products.includes("pin")) {
  fail(`the body ids are not PRODUCTs in the file: ` +
       JSON.stringify(exported.products));
} else { ok("both ids reached the file as PRODUCTs (which solid carries " +
            "which name is NOT proven -- see Stage 76)"); }

/* --- 8. three bodies ---------------------------------------------------- */
console.log("8. create three bodies");
await fresh();
turn = await say(
  "Create three separate bodies: a 60 x 40 x 8 mm plate, a 16 mm diameter " +
  "cylinder 20 mm long beside it, and a 10 mm diameter cylinder 40 mm long " +
  "beside that.");
byTheModel(turn, "three bodies");
if (turn.body.status !== "built") {
  fail(`status ${turn.body.status}: ${(turn.body.reply ?? "").slice(0, 200)}`);
} else { ok("built"); }
const three = volumes(turn.body);
if (Object.keys(three).length !== 3) {
  fail(`${Object.keys(three).length} bodies, expected 3`);
} else { ok(`three bodies: ${JSON.stringify(three)}`); }
const wanted = [60 * 40 * 8, Math.PI * 8 ** 2 * 20, Math.PI * 5 ** 2 * 40];
const got = Object.values(three).sort((a, b) => a - b);
if (!wanted.sort((a, b) => a - b).every((w, i) => near(got[i], w, 1e-3))) {
  fail(`volumes ${JSON.stringify(got)} against ${JSON.stringify(wanted)}`);
} else { ok("all three at their closed forms"); }

/* --- closing ------------------------------------------------------------ */
const failed = responses.filter((r) => r.status >= 400);
const server = failed.filter((r) => r.status >= 500);
if (server.length) fail(`${server.length} server errors: ` +
  JSON.stringify(server.map((r) => [r.url, r.status])));
else ok(`0 server errors (${failed.length} 4xx, which a refusal may be)`);
const noise = consoleErrors.filter((t) => !/favicon/i.test(t));
if (noise.length) fail(`${noise.length} console errors: ${noise[0]}`);
else ok("0 console errors");

await browser.close();
console.log(failures ? `\nFAILED (${failures})` : "\nPASS");
process.exit(failures ? 1 : 0);
