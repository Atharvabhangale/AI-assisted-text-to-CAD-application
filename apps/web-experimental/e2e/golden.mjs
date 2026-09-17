/**
 * The golden workflow, driven through the page.
 *
 * Eight steps, real Anthropic, real FreeCAD. Proves the capabilities added
 * this milestone hold together in one continuous session rather than
 * individually.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "freecad";

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: process.env.HEADED === "1" ? false : true,
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const seen = [];
page.on("response", async (r) => {
  if (!r.url().includes("/experimental/session/")) return;
  const route = r.url().split("/session/")[1];
  if (route === "export") { seen.push({ route, status: r.status() }); return; }
  try { seen.push({ route, body: JSON.parse(await r.text()) }); } catch {}
});

let failures = 0;
const fail = (w) => { console.log("    !! " + w); failures++; };
const text = async (s) => (await page.textContent(s))?.trim();
const last = (r) => [...seen].reverse().find((s) => s.route.startsWith(r));

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(700);

async function send(request) {
  await page.fill("#prompt", request);
  await page.click("#send");
  await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
    null, { timeout: 300000 });
  await page.waitForTimeout(400);
  const turn = last("message");
  console.log(`    status   : ${turn?.body?.status}`);
  console.log(`    assistant: ${(turn?.body?.reply ?? "").slice(0, 170)}`);
  console.log(`    measure  : ${await text("#stat-measure")}`);
  return turn?.body;
}
const ops = (b) => (b?.plan?.operations ?? []).map((o) => `${o.id}:${o.type}`);
const op = (b, kind) => (b?.plan?.operations ?? []).filter((o) => o.type === kind);

console.log("1. create");
let r = await send("Create a 100 x 60 x 10 mm plate with an 8 mm through hole at x=10, y=10.");
if (r?.status !== "built") fail("step 1 did not build");
console.log(`    ops      : ${ops(r).join(", ")}`);

console.log("\n2. widen");
r = await send("Make it 120 mm wide.");
if (op(r, "box")[0]?.parameters?.x !== 120) fail("width is not 120");

console.log("\n3. change the hole diameter");
r = await send("Change the hole to 10 mm.");
const hole = op(r, "through_hole")[0];
if (hole?.parameters?.diameter !== 10) fail(`hole diameter is ${hole?.parameters?.diameter}`);
if (op(r, "box")[0]?.parameters?.x !== 120) fail("changing the hole changed the plate width");
console.log(`    unrelated geometry preserved: plate x = ${op(r, "box")[0]?.parameters?.x}`);

console.log("\n4. pattern");
r = await send("Pattern that hole 3 times along X with 30 mm spacing.");
const patterns = op(r, "pattern");
const holes = op(r, "through_hole");
console.log(`    ops      : ${ops(r).join(", ")}`);
console.log(`    pattern op: ${patterns.length}  |  through_holes: ${holes.length}`);
if (r?.status !== "built") fail("pattern step did not build");
if (patterns.length === 0 && holes.length < 3) fail("neither a pattern nor repeated holes appeared");

console.log("\n5. fillet");
r = await send("Fillet the four outside vertical edges by 2 mm.");
if (op(r, "fillet").length === 0) fail("no fillet");
const withFillet = await text("#stat-measure");

console.log("\n6. ask for measurements (must come from evidence, not the model)");
r = await send("What are the overall dimensions and volume?");
if (r?.status !== "answered") fail(`measurement query status was ${r?.status}`);
if (r?.from_evidence !== true) fail("the answer did not come from stored evidence");
if (r?.metadata) fail("a model call was made for a measurement question");

console.log("\n7. export STEP");
await page.click('.rail-item[data-surface="model"]');
await page.waitForTimeout(400);
const treeItems = await page.$$eval("#op-tree button .op-name", (n) => n.map((x) => x.textContent));
console.log(`    operation tree: ${treeItems.join(", ") || "(empty)"}`);
if (treeItems.length === 0) fail("the operation tree is empty");
const inspectPairs = await page.$$eval("#inspect dt, #inspect dd", (n) => n.map((x) => x.textContent?.trim()));
console.log(`    inspect: ${inspectPairs.slice(0, 8).join(" ")}`);
// click an operation -- the smallest useful history interaction
await page.click("#op-tree button");
await page.waitForTimeout(300);
const selected = await page.$$eval("#op-tree button.is-selected", (n) => n.length);
if (selected !== 1) fail("clicking an operation did not select exactly one");
const downloads = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
await page.click("#export-step");
const download = await downloads;
await page.waitForTimeout(800);
const exportNote = await text("#export-note");
console.log(`    export   : ${exportNote}`);
console.log(`    download : ${download ? await download.suggestedFilename() : "none"}`);
if (download) {
  const path = await download.path();
  const { statSync, readFileSync } = await import("node:fs");
  const size = statSync(path).size;
  const head = readFileSync(path, "latin1").slice(0, 120);
  console.log(`    bytes    : ${size}`);
  console.log(`    header   : ${head.split("\\n")[0]}`);
  if (size < 500) fail("the STEP file is suspiciously small");
  if (!head.startsWith("ISO-10303")) fail("the file is not a STEP file");
} else {
  fail("no STEP download arrived");
}

console.log("\n8. undo the fillet");
await page.click('.rail-item[data-surface="copilot"]');
await page.waitForTimeout(300);
await page.click("#undo");
await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
  null, { timeout: 300000 });
await page.waitForTimeout(500);
const undone = last("undo");
console.log(`    status   : ${undone?.body?.status}`);
console.log(`    measure  : ${await text("#stat-measure")}`);
if (undone?.body?.status !== "built") fail("undo did not rebuild");
if ((undone?.body?.plan?.operations ?? []).some((o) => o.type === "fillet")) fail("fillet survived undo");
if ((await text("#stat-measure")) === withFillet) fail("measurements unchanged after undo");

console.log(`\n    backend  : ${await text("#stat-backend")}   path: ${await text("#stat-path")}`);
if ((await text("#stat-backend")) !== EXPECT_BACKEND) fail("wrong backend");

await page.screenshot({ path: process.env.SHOT ?? "golden.png" });
console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log("failures      :", failures);
await browser.close();
process.exit(failures === 0 && consoleErrors.length === 0 ? 0 : 1);
