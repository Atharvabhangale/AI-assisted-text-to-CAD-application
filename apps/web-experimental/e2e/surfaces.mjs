/**
 * The whole workspace, one part, every surface -- driven through the page.
 *
 * Real Anthropic for the Copilot turns, real FreeCAD for the geometry, real
 * downloads for the exports.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: process.env.HEADED === "1" ? false : true,
  acceptDownloads: true,
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

let failures = 0;
const fail = (w) => { console.log("    !! " + w); failures++; };
const text = async (s) => (await page.textContent(s))?.trim();
const go = async (surface) => {
  await page.click(`.rail-item[data-surface="${surface}"]`);
  await page.waitForTimeout(350);
};

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(700);

async function ask(request) {
  await page.fill("#prompt", request);
  await page.click("#send");
  await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
    null, { timeout: 300000 });
  await page.waitForTimeout(600);
}

console.log("COPILOT — build the part");
// Two turns on purpose: the fillet has to be its own revision for "undo the
// fillet" to mean anything. Built in one turn there is nothing behind it,
// and Undo is correctly disabled.
await ask("Create a 120 x 60 x 10 mm plate with three 8 mm through holes.");
console.log("    after holes :", await text("#stat-measure"));
await ask("Fillet the four outside vertical edges by 2 mm.");
console.log("    build   :", await text("#stat-build"));
console.log("    backend :", await text("#stat-backend"));
console.log("    measure :", await text("#stat-measure"));
if ((await text("#stat-build")) !== "built") fail("the part did not build");
if ((await text("#stat-backend")) !== "freecad") fail("backend is not freecad");

console.log("\nMODEL — operation tree");
await go("model");
const tree = await page.$$eval("#op-tree button .op-name", (n) => n.map((x) => x.textContent));
console.log("    tree    :", tree.join(", "));
if (tree.length < 3) fail("the operation tree is too short");
await page.click("#op-tree button");
await page.waitForTimeout(250);
if ((await page.$$("#op-tree button.is-selected")).length !== 1) {
  fail("selecting an operation did not select exactly one");
}
const inspect = await page.$$eval("#inspect dt, #inspect dd", (n) => n.map((x) => x.textContent?.trim()));
console.log("    inspect :", inspect.slice(0, 10).join(" "));

console.log("\nENGINEERING");
await go("engineering");
await page.fill("#eng-question", "What is the volume and overall size?");
await page.click("#eng-ask");
await page.waitForTimeout(1200);
console.log("    note    :", await text("#eng-note"));
const findings = await page.$$eval("#eng-findings li", (n) => n.map((x) => x.innerText.replace(/\n/g, " | ")));
findings.slice(0, 4).forEach((f) => console.log("      " + f));
if (findings.length === 0) fail("engineering returned no findings");
const badges = await page.$$eval("#eng-findings .badge", (n) => n.map((x) => x.textContent));
if (!badges.includes("measured")) fail("nothing was labelled measured");

await page.fill("#eng-question", "How much material is removed by the holes?");
await page.click("#eng-ask");
await page.waitForTimeout(1200);
const calc = await page.$$eval("#eng-findings li", (n) => n.map((x) => x.innerText.replace(/\n/g, " | ")));
console.log("    removed :", calc[0] ?? "(none)");
if (!calc.some((c) => /calculated/i.test(c))) fail("no calculated finding");

console.log("\nDRAWINGS");
await go("drawings");
await page.click("#make-drawing");
await page.waitForTimeout(6000);
console.log("    note    :", await text("#drawing-note"));
const facts = await page.$$eval("#drawing-facts dt, #drawing-facts dd", (n) => n.map((x) => x.textContent?.trim()));
console.log("    facts   :", facts.join(" "));
const svgCount = await page.$$eval("#drawing-sheet svg polyline", (n) => n.length);
console.log("    polylines:", svgCount);
if (svgCount < 4) fail("the drawing has too few edges to be real");
const sheetText = await page.$eval("#drawing-sheet", (n) => n.textContent ?? "");
if (!/120/.test(sheetText)) fail("the drawing does not show a measured dimension");

console.log("\nPART FINDER");
await go("finder");
await page.fill("#finder-query", "Find an M8 socket head cap screw.");
await page.click("#finder-search");
await page.waitForTimeout(900);
const parts = await page.$$eval("#finder-results li", (n) => n.map((x) => x.innerText.replace(/\n/g, " | ")));
parts.slice(0, 3).forEach((p) => console.log("      " + p));
console.log("    note    :", (await text("#finder-note"))?.slice(0, 110));
if (parts.length === 0) fail("the catalogue returned nothing for M8");
if (!/M8/.test(parts.join(" "))) fail("results are not M8");

console.log("\nMACROS");
await go("macros");
await page.fill("#macro-name", "Manufacturing Package");
await page.fill("#macro-text", "export STEP and STL");
await page.click("#macro-create");
await page.waitForTimeout(900);
console.log("    created :", await text("#macro-note"));
const macros = await page.$$eval("#macro-list button", (n) => n.map((x) => x.innerText.replace(/\n/g, " ")));
console.log("    macros  :", macros.join(" / "));
if (macros.length === 0) fail("the macro was not stored");
await page.click("#macro-list button");
await page.waitForTimeout(9000);
const ran = await text("#macro-note");
console.log("    ran     :", ran);
if (!/bytes/.test(ran ?? "")) fail("the macro run produced no exported bytes");

console.log("\nEXPORT — real downloads");
await go("model");
for (const format of ["step", "stl"]) {
  const wait = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
  await page.click(`#export-${format}`);
  const file = await wait;
  await page.waitForTimeout(700);
  if (!file) { fail(`no ${format} download`); continue; }
  const { statSync, readFileSync } = await import("node:fs");
  const path = await file.path();
  const size = statSync(path).size;
  const head = readFileSync(path, "latin1").slice(0, 40);
  console.log(`    ${format.toUpperCase().padEnd(4)}: ${await file.suggestedFilename()}  ${size} bytes  head="${head.split("\n")[0].trim()}"`);
  if (size < 300) fail(`${format} file is too small`);
  if (format === "step" && !head.startsWith("ISO-10303")) fail("bad STEP header");
  if (format === "stl" && !(head.startsWith("solid") || size > 84)) fail("bad STL");
}

console.log("\nCOPILOT — undo the fillet");
await go("copilot");
const beforeUndo = await text("#stat-measure");
if (await page.$eval("#undo", (n) => n.disabled)) {
  fail("Undo is disabled although the fillet was its own revision");
}
await page.click("#undo");
await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
  null, { timeout: 300000 });
await page.waitForTimeout(600);
console.log("    measure :", await text("#stat-measure"));
console.log("    build   :", await text("#stat-build"));
if ((await text("#stat-measure")) === beforeUndo) fail("undo did not change the model");

await page.screenshot({ path: process.env.SHOT ?? "surfaces.png" });
console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log("failures      :", failures);
await browser.close();
process.exit(failures === 0 && consoleErrors.length === 0 ? 0 : 1);
