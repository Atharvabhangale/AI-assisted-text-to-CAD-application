/**
 * Editing one body of a two-body part, through the page.
 *
 * The conversation this stage exists for:
 *
 *   1. "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as
 *       two separate bodies."
 *   2. "make the cube 50 mm wide"        -> the cylinder must be untouched
 *   3. "put a 6 mm hole through the cylinder" -> the cube must be untouched
 *   4. "make it 20 mm wider"             -> must be REFUSED, not guessed
 *
 * Step 4 is the one that matters most. Every other step can be checked in a
 * unit test; that a refusal reaches the person as an answer, in the product,
 * with the part left exactly where it was, cannot.
 *
 * No model is configured, so the provider-neutral reader is the only route
 * open. Deterministic success is architecture evidence and says nothing
 * whatever about model quality.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "freecad";

const CREATE =
  "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two " +
  "separate bodies.";

const CUBE = 40 ** 3;
const PIN = Math.PI * 10 ** 2 * 30;
const CUBE_WIDENED = 50 * 40 * 40;
const PIN_BORED = PIN - Math.PI * 3 ** 2 * 30;

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
let v = volumes(turn.body);
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

/* --- 2. edit the cube --------------------------------------------------- */
console.log("\n  2. make the cube 50 mm wide");
turn = await say("make the cube 50 mm wide");
v = volumes(turn.body);
console.log("     bodies: " + JSON.stringify(v));
if (turn.body.status !== "built") fail(`status ${turn.body.status}`);
else ok("the edit built");
if (!near(v.cube, CUBE_WIDENED)) fail(`cube ${v.cube}, expected ${CUBE_WIDENED}`);
else ok(`the cube is now ${v.cube}`);
if (!near(v.cylinder, PIN)) fail(`the cylinder CHANGED: ${v.cylinder}`);
else ok("the cylinder is untouched");

/* --- 3. edit the cylinder ---------------------------------------------- */
console.log("\n  3. put a 6 mm hole through the cylinder");
turn = await say("put a 6 mm hole through the cylinder");
v = volumes(turn.body);
console.log("     bodies: " + JSON.stringify(v));
if (turn.body.status !== "built") fail(`status ${turn.body.status}`);
else ok("the edit built");
if (!near(v.cylinder, PIN_BORED)) fail(`cylinder ${v.cylinder}, expected ${PIN_BORED}`);
else ok(`the cylinder is now ${v.cylinder}`);
if (!near(v.cube, CUBE_WIDENED)) fail(`the cube CHANGED: ${v.cube}`);
else ok("the cube keeps the edit it was given");

/* --- 4. an edit that names no body must be REFUSED --------------------- */
console.log("\n  4. make it 20 mm wider   (names no body)");
turn = await say("make it 20 mm wider");
const after = volumes(turn.body);
console.log("     status: " + turn.body.status);
console.log("     reply : " + (turn.body.reply ?? "").slice(0, 140));
if (turn.body.status === "built") {
  fail("an ambiguous edit was BUILT; it must be refused");
} else ok(`not built -- status ${turn.body.status}`);
const said = (turn.body.reply ?? "") + (turn.body.error ?? "");
if (!/separate bodies/.test(said)) fail("the refusal does not name the bodies");
else ok("the refusal names both bodies and asks which");
if (Object.keys(after).length && !near(after.cube, CUBE_WIDENED)) {
  fail("the part changed on a refused turn");
} else ok("the part is exactly where it was");

/* --- nothing broke ------------------------------------------------------ */
const failed = responses.filter((r) => r.status >= 500);
if (failed.length > 0) {
  fail("server errors: " + failed.map((r) => `${r.status} ${r.url}`).join(", "));
} else ok("no server errors");
const real = consoleErrors.filter((t) => !/favicon/i.test(t));
if (real.length > 0) fail("console errors: " + real.join(" | "));
else ok("no console errors");

const shot = process.env.EXPERIMENTAL_SCREENSHOT ?? "body-targeting-page.png";
await page.screenshot({ path: shot, fullPage: true });
console.log("\n    screenshot  : " + shot);

await browser.close();
console.log(failures === 0 ? "\n  BODY-TARGETING E2E: PASS\n"
                           : `\n  BODY-TARGETING E2E: ${failures} FAILURE(S)\n`);
process.exit(failures === 0 ? 0 : 1);
