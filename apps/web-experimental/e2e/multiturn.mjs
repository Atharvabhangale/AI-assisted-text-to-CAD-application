/**
 * The multi-turn copilot, driven as a person drives it.
 *
 * Every step goes through the page's own composer and Undo/New part buttons.
 * Nothing is posted to the API directly, because the thing under test is
 * whether the CONVERSATION modifies the part -- not whether the endpoint can.
 *
 * Each turn is a real Anthropic call, so this is slow and costs quota.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "";

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: process.env.HEADED === "1" ? false : true,
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const turns = [];
page.on("response", async (r) => {
  if (!r.url().includes("/experimental/session/")) return;
  try {
    turns.push({ url: r.url().split("/session/")[1], body: JSON.parse(await r.text()) });
  } catch { /* non-JSON */ }
});

let failures = 0;
const fail = (why) => { console.log("    !! " + why); failures++; };
const text = async (sel) => (await page.textContent(sel))?.trim();
const lastTurn = (kind) => [...turns].reverse().find((t) => t.url.startsWith(kind));

await page.goto(WEB, { waitUntil: "networkidle" });
// A fresh session per run, so a previous run's part cannot leak into this one.
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(700);

async function send(request) {
  await page.fill("#prompt", request);
  await page.click("#send");
  await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
    null, { timeout: 300000 });
  await page.waitForTimeout(400);
}

async function report(label, { expectBuilt = true } = {}) {
  const reply = lastTurn("message") ?? lastTurn("undo");
  const status = reply?.body?.status;
  const measure = await text("#stat-measure");
  console.log(`    status   : ${status}`);
  console.log(`    assistant: ${(reply?.body?.reply ?? "").slice(0, 150)}`);
  console.log(`    backend  : ${await text("#stat-backend")}   path: ${await text("#stat-path")}`);
  console.log(`    measure  : ${measure}`);
  console.log(`    plan ops : ${(reply?.body?.plan?.operations ?? []).map((o) => `${o.id}:${o.type}`).join(", ") || "—"}`);
  if (expectBuilt) {
    if (status !== "built") fail(`${label}: status was ${status}, expected built`);
    if (!reply?.body?.render) fail(`${label}: no render model`);
    if (EXPECT_BACKEND && reply?.body?.backend !== EXPECT_BACKEND) {
      fail(`${label}: backend was ${reply?.body?.backend}`);
    }
  }
  return { reply: reply?.body, measure };
}

const box = (t) => t?.body?.plan?.operations?.find((o) => o.type === "box");
const holes = (t) => (t?.body?.plan?.operations ?? []).filter((o) => o.type === "through_hole");
const fillets = (t) => (t?.body?.plan?.operations ?? []).filter((o) => o.type === "fillet");

console.log("STEP 1 — create");
await send("Create a 100 x 60 x 10 mm plate with an 8 mm through hole at x=10, y=10.");
let r1 = await report("step 1");
if (r1.reply?.editing !== false) fail("step 1 should not be an edit");
const width1 = box(lastTurn("message"))?.parameters?.x;
console.log(`    plate x  : ${width1}`);

console.log("\nSTEP 2 — modify an existing dimension");
await send("Make the plate 120 mm wide.");
let r2 = await report("step 2");
if (r2.reply?.editing !== true) fail("step 2 should be an edit, not a fresh part");
const width2 = box(lastTurn("message"))?.parameters?.x;
console.log(`    plate x  : ${width1} -> ${width2}`);
if (width2 !== 120) fail(`plate width is ${width2}, expected 120`);
if (holes(lastTurn("message")).length < 1) fail("the existing hole was lost");

console.log("\nSTEP 3 — refer to existing geometry");
// "the opposite side" is genuinely ambiguous on a rectangular plate, and
// asking rather than guessing is the required behaviour -- so either answer
// is acceptable here. If it asks, we answer it and then the holes must
// appear: that is the capability under test either way.
await send("Add two more 8 mm holes on the opposite side.");
let r3 = await report("step 3", { expectBuilt: false });
if (r3.reply?.status === "needs_clarification") {
  console.log("    (asked for clarification, which is correct -- answering it)");
  if (r3.reply?.render) fail("a clarification built geometry");
  await send("Put them at x=110, y=10 and x=110, y=50.");
  r3 = await report("step 3 (after clarifying)");
} else if (r3.reply?.status !== "built") {
  fail(`step 3: status was ${r3.reply?.status}`);
}
const holeCount = holes(lastTurn("message")).length;
console.log(`    holes    : ${holeCount}`);
if (holeCount < 3) fail(`expected at least 3 holes, found ${holeCount}`);

console.log("\nSTEP 4 — modify features");
await send("Fillet the four outside vertical edges by 2 mm.");
let r4 = await report("step 4");
const filletCount = fillets(lastTurn("message")).length;
console.log(`    fillets  : ${filletCount}`);
if (filletCount < 1) fail("no fillet in the plan");
const measureWithFillet = r4.measure;

console.log("\nSTEP 5 — undo");
await page.click("#undo");
await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
  null, { timeout: 300000 });
await page.waitForTimeout(500);
const undone = lastTurn("undo");
console.log(`    status   : ${undone?.body?.status}`);
console.log(`    assistant: ${(undone?.body?.reply ?? "").slice(0, 140)}`);
console.log(`    measure  : ${await text("#stat-measure")}`);
if (undone?.body?.status !== "built") fail("undo did not rebuild a model");
if ((undone?.body?.plan?.operations ?? []).some((o) => o.type === "fillet")) {
  fail("undo did not remove the fillet");
}
if ((await text("#stat-measure")) === measureWithFillet) {
  fail("the measurements did not change after undo");
}

console.log("\nSTEP 6 — modify after undo");
await send("Make the plate 12 mm thick.");
let r6 = await report("step 6");
const thickness = box(lastTurn("message"))?.parameters?.z;
console.log(`    plate z  : ${thickness}`);
if (thickness !== 12) fail(`thickness is ${thickness}, expected 12`);

console.log("\nSTEP 7 — an ambiguous request must ask, not guess");
const beforeAmbiguous = await text("#stat-measure");
await send("Move the hole.");
const ambiguous = lastTurn("message");
console.log(`    status   : ${ambiguous?.body?.status}`);
console.log(`    assistant: ${(ambiguous?.body?.reply ?? "").slice(0, 180)}`);
if (!["needs_clarification", "unsupported"].includes(ambiguous?.body?.status)) {
  fail(`ambiguous request produced ${ambiguous?.body?.status}`);
}
if (ambiguous?.body?.render) fail("an ambiguous request built geometry");
if ((await text("#stat-measure")) !== beforeAmbiguous) {
  fail("the model changed on an ambiguous request");
}

console.log("\nSTEP 8 — an unsupported request must not destroy the part");
await send("Turn it into a helical involute gear with 24 teeth.");
const unsupported = lastTurn("message");
console.log(`    status   : ${unsupported?.body?.status}`);
console.log(`    assistant: ${(unsupported?.body?.reply ?? "").slice(0, 180)}`);
console.log(`    measure  : ${await text("#stat-measure")}`);
if (unsupported?.body?.status === "built") fail("an involute gear was somehow built");
if ((await text("#stat-measure")) !== beforeAmbiguous) {
  fail("the unsupported request changed the model");
}
if (await page.$eval("#viewport-empty", (n) => !n.hidden)) {
  fail("the viewport went blank after an unsupported request");
}
if (unsupported?.body?.session?.has_model !== true) fail("the session lost its model");

console.log("\nSTEP 9 — the conversation reflects what happened");
const messages = await page.$$eval(".msg .msg-body", (n) => n.map((b) => b.textContent.trim()));
console.log(`    messages : ${messages.length}`);
if (messages.length < 14) fail("the conversation did not record every turn");

await page.screenshot({ path: process.env.SHOT ?? "multiturn.png" });
console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log("failures      :", failures);
await browser.close();
process.exit(failures === 0 && consoleErrors.length === 0 ? 0 : 1);
