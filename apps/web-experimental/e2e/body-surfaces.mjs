/**
 * The product surfaces of a two-body part, driven through the real page.
 *
 * Stage 73/74. Stage 71 made two bodies build and draw; Stage 72 made them
 * editable by name. This is the rest of the product:
 *
 *   1. create two bodies
 *   2. ask a per-body measurement question      -> answered, about THAT body
 *   3. ask a question that names no body        -> REFUSED, not guessed
 *   4. ask for a total                          -> answered, and CALCULATED
 *   5. engineering, per body                    -> attributed, not merged
 *   6. a drawing of one body                    -> a real sheet, named
 *   7. a drawing of the whole part              -> REFUSED by name
 *   8. export STEP                              -> both bodies in one file
 *
 * Steps 3 and 7 are the ones that need the product to prove them. A unit
 * test can show a function raising; only this can show a refusal arriving as
 * an ANSWER, in front of a person, with the part left where it was.
 *
 * Step 8 is verified by BYTES, not by the download succeeding: the response
 * is re-read here and its solid count checked, because a STEP that quietly
 * dropped a body is a perfectly valid file.
 *
 * No model is configured, so the provider-neutral reader is the only route
 * open. Deterministic success is architecture evidence and says nothing
 * whatever about model quality.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
// The page's own route to the backend, not the backend's address. Fetching
// 127.0.0.1:8001 directly from a page served on 5174 is cross-origin and the
// browser blocks it -- and the product has no CORS policy, deliberately. Going
// through the same `/api` proxy the application uses means these steps
// exercise exactly the path a person's browser takes.
const API = process.env.EXPERIMENTAL_API ?? "/api";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "freecad";

const CREATE =
  "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two " +
  "separate bodies.";

const CUBE = 40 ** 3;
const PIN = Math.PI * 10 ** 2 * 30;

let failures = 0;
const fail = (why) => { console.log("    !! " + why); failures++; };
const ok = (what) => console.log("    ok " + what);
const near = (a, b, tol = 1e-4) => Math.abs((a ?? NaN) - b) <= tol;

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

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });

async function say(text) {
  const before = responses.length;
  await page.fill("#prompt", text);
  await page.click("#send");
  await page.waitForFunction(
    () => !document.querySelector("#send")?.disabled, null,
    { timeout: 300_000 });
  await page.waitForTimeout(500);
  const turn = responses.slice(before)
    .reverse().find((r) => r.url.includes("/session/message"));
  if (!turn) fail("no /session/message response for: " + text);
  return turn ?? { status: 0, body: {} };
}

const volumes = (body) => Object.fromEntries(
  (body.bodies ?? []).map((b) => [b.body_id, b.measurement?.volume]));

/* --- 1. create ---------------------------------------------------------- */
console.log("\n  1. " + CREATE);
let turn = await say(CREATE);
const v = volumes(turn.body);
console.log("     bodies: " + JSON.stringify(v));
if (turn.body.status !== "built") fail(`status ${turn.body.status}`);
else ok("two bodies built");
if (turn.body.interpreted_by?.source !== "deterministic") {
  fail("this run must use the provider-neutral reader, not a model");
} else ok("read by the provider-neutral reader, no model configured");
if (!near(v.cube, CUBE) || !near(v.cylinder, PIN)) {
  fail(`volumes ${JSON.stringify(v)}`);
} else ok("both bodies match their closed forms");
if (turn.body.backend !== EXPECT_BACKEND) fail(`backend ${turn.body.backend}`);
else ok(`backend ${turn.body.backend}`);

const sessionId = turn.body.session_id;

/* --- the panel must not report one body's numbers as the part's --------- */
const panel = await page.evaluate(() => {
  const dl = document.querySelector("#measurements");
  return dl ? dl.textContent.replace(/\s+/g, " ").trim() : "";
});
console.log("     measurements panel: " + panel.slice(0, 160));
if (!/cube/.test(panel) || !/cylinder/.test(panel)) {
  fail("the measurements panel does not name both bodies");
} else ok("the panel lists each body by name");
if (/^solids/.test(panel)) {
  fail("the panel shows one body's numbers as the part's");
} else ok("no part-level measurement is claimed");

/* --- 2. a question about ONE body -------------------------------------- */
console.log("\n  2. What is the volume of the cylinder?");
turn = await say("What is the volume of the cylinder?");
console.log("     status: " + turn.body.status);
console.log("     reply : " + (turn.body.reply ?? "").slice(0, 160));
if (turn.body.status !== "answered") fail(`status ${turn.body.status}`);
else ok("answered from evidence");
if (!/cylinder:/.test(turn.body.reply ?? "")) {
  fail("the answer does not say which body it is about");
} else ok("the answer names the body");
const said = turn.body.reply ?? "";
if (!new RegExp(PIN.toFixed(3).slice(0, 7)).test(said)) {
  fail(`the answer is not the cylinder's volume: ${said}`);
} else ok(`the cylinder's own volume, ${PIN.toFixed(3)}`);
if (new RegExp(String(CUBE)).test(said)) fail("that is the cube's volume");
if (turn.body.evidence?.provenance !== "measured") {
  fail(`provenance ${turn.body.evidence?.provenance}`);
} else ok("provenance MEASURED");

/* --- 3. a question that names NO body must be refused ------------------ */
console.log("\n  3. What is the volume?   (names no body)");
turn = await say("What is the volume?");
console.log("     status: " + turn.body.status);
console.log("     reply : " + (turn.body.reply ?? "").slice(0, 160));
if (turn.body.status !== "refused") {
  fail(`an ambiguous question was ${turn.body.status}; it must be refused`);
} else ok("refused");
if (!/separate bodies/.test(turn.body.reply ?? "")) {
  fail("the refusal does not name the bodies");
} else ok("the refusal names both bodies and asks which");
if (/to change/.test(turn.body.reply ?? "")) {
  fail("the refusal tells someone asking a question to name what to CHANGE");
} else ok("the refusal is phrased for a question, not an edit");

/* --- 4. an aggregate IS answerable, and says it is calculated ---------- */
console.log("\n  4. What is the total volume?");
turn = await say("What is the total volume?");
console.log("     status: " + turn.body.status);
console.log("     reply : " + (turn.body.reply ?? "").slice(0, 160));
if (turn.body.status !== "answered") fail(`status ${turn.body.status}`);
else ok("answered");
if (!near(
  Number(((turn.body.reply ?? "").match(/Volume ([\d.]+)/) ?? [])[1]),
  CUBE + PIN, 1e-2)) {
  fail(`the total is not ${(CUBE + PIN).toFixed(3)}`);
} else ok(`total ${(CUBE + PIN).toFixed(3)} mm3`);
if (turn.body.evidence?.provenance !== "calculated") {
  fail(`a summed total is reported as ${turn.body.evidence?.provenance}`);
} else ok("provenance CALCULATED, not MEASURED");

/* --- 5. engineering, per body ------------------------------------------ */
console.log("\n  5. engineering, with no question");
let reply = await page.evaluate(async ([api, id]) => {
  const r = await fetch(api + "/experimental/session/engineering", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: id }),
  });
  return { status: r.status, body: await r.json() };
}, [API, sessionId]);
console.log("     per_body: " + reply.body.per_body);
if (reply.body.per_body !== true) {
  fail("engineering merged the bodies into one report");
} else ok("engineering reports each body separately");
if (!reply.body.bodies?.cube || !reply.body.bodies?.cylinder) {
  fail("engineering does not report both bodies");
} else ok("both bodies have their own findings");

/* --- 6. a drawing of ONE body is a real sheet -------------------------- */
console.log("\n  6. drawing of the cube");
reply = await page.evaluate(async ([api, id]) => {
  const r = await fetch(api + "/experimental/session/drawing", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: id, body: "cube" }),
  });
  return { status: r.status, body: await r.json() };
}, [API, sessionId]);
console.log("     status: " + reply.status + "  body: " + reply.body.body);
if (reply.status !== 200) fail(`a per-body drawing failed: ${JSON.stringify(reply.body).slice(0, 200)}`);
else ok("a drawing of one body is produced");
if (reply.body.body !== "cube") fail("the sheet does not say which body it is of");
else ok("the sheet names its body");
if (!(reply.body.drawing?.svg ?? "").includes("<svg")) fail("no SVG in the sheet");
else ok("the sheet carries a real SVG");

/* --- 7. a drawing of the WHOLE part is refused by name ----------------- */
console.log("\n  7. drawing of the whole part   (names no body)");
reply = await page.evaluate(async ([api, id]) => {
  const r = await fetch(api + "/experimental/session/drawing", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: id }),
  });
  return { status: r.status, body: await r.json() };
}, [API, sessionId]);
console.log("     status: " + reply.status);
console.log("     error : " + (reply.body.error ?? "").slice(0, 160));
if (reply.status === 200) fail("an assembly drawing was produced; none exists");
else ok(`refused -- HTTP ${reply.status}`);
if (reply.body.capability !== "assembly_drawing") {
  fail("the refusal does not name the missing capability");
} else ok("the refusal names the capability: assembly_drawing");
if (!/parts list|item number/.test(reply.body.error ?? "")) {
  fail("the refusal does not say WHY");
} else ok("the refusal says what an assembly drawing would need");

/* --- 8. export STEP: both bodies, in one file, verified ---------------- */
console.log("\n  8. export STEP");
const exported = await page.evaluate(async ([api, id]) => {
  const r = await fetch(api + "/experimental/session/export", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: id, format: "step" }),
  });
  const text = await r.text();
  return {
    status: r.status,
    bodies: r.headers.get("x-cad-bodies"),
    backend: r.headers.get("x-cad-backend"),
    bytes: text.length,
    solids: (text.match(/MANIFOLD_SOLID_BREP/g) ?? []).length,
    hasCube: text.includes("cube"),
    hasCylinder: text.includes("cylinder"),
  };
}, [API, sessionId]);
console.log("     " + JSON.stringify(exported));
if (exported.status !== 200) fail(`the export failed: HTTP ${exported.status}`);
else ok("a multi-body STEP is exported, not refused");
if (exported.bodies !== "cube, cylinder") {
  fail(`the response does not name both bodies: ${exported.bodies}`);
} else ok("the response names both bodies");
if (exported.solids !== 2) {
  fail(`the STEP holds ${exported.solids} solids, expected 2`);
} else ok("the STEP file holds TWO solids -- nothing fused, nothing dropped");
if (!exported.hasCube || !exported.hasCylinder) {
  fail("the body ids did not reach the file");
} else ok("both body ids are in the file");

/* --- nothing broke ------------------------------------------------------ */
//
// Step 7 provokes a 501 ON PURPOSE -- it is the assembly-drawing refusal this
// stage exists to make visible -- so a blanket "no 5xx" check would report
// the thing being tested as a failure. It is excluded BY NAME and its
// presence asserted, rather than by widening the check to "no 5xx except
// 501", which would also hide a 501 from anywhere else.
const expectedRefusal = (r) =>
  r.status === 501 && r.url.includes("/session/drawing");
const refusals = responses.filter(expectedRefusal);
if (refusals.length !== 1) {
  fail(`expected exactly one assembly-drawing refusal, saw ${refusals.length}`);
} else ok("the one 501 is the assembly-drawing refusal step 7 asked for");

const failed = responses.filter((r) => r.status >= 500 && !expectedRefusal(r));
if (failed.length > 0) {
  fail("server errors: " + failed.map((r) => `${r.status} ${r.url}`).join(", "));
} else ok("no unexpected server errors");

// The browser logs a console error for any non-2xx fetch, so the deliberate
// refusal shows up here too. Dropped for the same reason and no other.
const real = consoleErrors.filter(
  (t) => !/favicon/i.test(t) && !/\b501\b/.test(t));
if (real.length > 0) fail("console errors: " + real.join(" | "));
else ok("no console errors beyond the deliberate refusal");

const shot = process.env.EXPERIMENTAL_SCREENSHOT ?? "body-surfaces-page.png";
await page.screenshot({ path: shot, fullPage: true });
console.log("\n    screenshot  : " + shot);

await browser.close();
console.log(failures === 0 ? "\n  BODY-SURFACES E2E: PASS\n"
                           : `\n  BODY-SURFACES E2E: ${failures} FAILURE(S)\n`);
process.exit(failures === 0 ? 0 : 1);
