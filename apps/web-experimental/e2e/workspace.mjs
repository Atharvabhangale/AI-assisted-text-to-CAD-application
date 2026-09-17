/**
 * Drive the CAD workspace the way a person does: type into the Copilot and
 * press Run.
 *
 * Nothing is posted to the API directly. The whole point is that the product
 * shell performs the real flow -- generation, validation, build, render --
 * so this touches only the composer and then reads what the page shows.
 *
 * Assumes the API is running on 8001 and the page on 5174.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "";
const REQUEST =
  process.env.REQUEST ??
  "Create a 100 x 60 x 10 mm plate with an 8 mm through hole at x=10, y=10, " +
    "then fillet the four vertical edges by 2 mm.";

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
});
const page = await browser.newPage();

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const wire = [];
page.on("response", async (r) => {
  if (!r.url().includes("/experimental/")) return;
  let body = null;
  try { body = await r.text(); } catch { /* consumed */ }
  wire.push({ url: r.url(), status: r.status(), body });
});

const text = async (sel) => (await page.textContent(sel))?.trim();
let failures = 0;
const fail = (why) => { console.log("  !! " + why); failures++; };

await page.goto(WEB, { waitUntil: "networkidle" });
console.log("title           :", await page.title());
console.log("health          :", await text("#health-label"));

// The product surfaces exist and Copilot is the default.
const surfaces = await page.$$eval(".rail-item", (n) => n.map((b) => b.dataset.surface));
console.log("surfaces        :", surfaces.join(", "));
for (const expected of ["copilot","model","parts","drawings","macros","engineering","finder","settings"]) {
  if (!surfaces.includes(expected)) fail(`missing surface: ${expected}`);
}

// No fixture UX in the primary product flow. Checked against what is
// actually RENDERED -- `textContent` also returns text inside hidden panels,
// and developer fixtures living (folded away) under Settings is exactly
// where they belong, not a failure.
const visible = await page.evaluate(() => document.body.innerText);
if (/LOCAL_DEVELOPMENT_PLAN/.test(visible)) {
  fail("LOCAL_DEVELOPMENT_PLAN is visible in the primary UI");
}
if (/fixture/i.test(visible)) {
  fail("fixture UX is visible in the primary product flow");
}

console.log("\nrequest         :", REQUEST);
await page.fill("#prompt", REQUEST);
await page.click("#send");

// Wait for the run to finish: the Run button is re-enabled at the end.
await page.waitForFunction(
  () => !document.querySelector("#send")?.disabled,
  null,
  { timeout: 240000 },
);

const messages = await page.$$eval(".msg", (nodes) =>
  nodes.map((n) => ({
    who: n.querySelector(".msg-who")?.textContent?.trim(),
    body: n.querySelector(".msg-body")?.textContent?.trim(),
    error: n.classList.contains("msg-error"),
    facts: Array.from(n.querySelectorAll(".msg-facts dt")).map((dt, i) => [
      dt.textContent?.trim(),
      n.querySelectorAll(".msg-facts dd")[i]?.textContent?.trim(),
    ]),
  })),
);
const last = messages[messages.length - 1];
console.log("\n--- conversation ---");
for (const m of messages) {
  console.log(`  [${m.who}${m.error ? " ERROR" : ""}] ${(m.body ?? "").replace(/\n/g, "\n      ")}`);
  for (const [k, v] of m.facts) console.log(`        ${k}: ${v}`);
}
if (last.error) fail("the copilot's final message is an error");
if (messages.length < 3) fail("the conversation did not record the exchange");

console.log("\n--- status strip ---");
const stat = async (id) => (await text(id)) ?? "";
const backend = await stat("#stat-backend");
const path = await stat("#stat-path");
const build = await stat("#stat-build");
console.log("  backend       :", backend);
console.log("  model         :", await stat("#stat-model"));
console.log("  path          :", path);
console.log("  build         :", build);
console.log("  last operation:", await stat("#stat-last"));
console.log("  measurements  :", await stat("#stat-measure"));
console.log("  mesh          :", await text("#mesh-note"));

if (EXPECT_BACKEND && backend !== EXPECT_BACKEND) fail(`backend is ${backend}, expected ${EXPECT_BACKEND}`);
if (build !== "built") fail(`build status is "${build}"`);
if (path !== "graph_executor") fail(`execution path is "${path}"`);

// The viewport must actually be drawing.
const empty = await page.$eval("#viewport-empty", (n) => n.hidden);
if (!empty) fail("the empty-viewport overlay is still showing");
const drew = await page.$eval("#viewport", (c) => {
  const gl = c.getContext("webgl2") ?? c.getContext("webgl");
  return { context: Boolean(gl), width: c.width, height: c.height };
});
console.log("  canvas        :", JSON.stringify(drew));
if (!drew.context || drew.width === 0) fail("the viewport has no live WebGL canvas");

// The real generation call must have happened.
const generated = wire.find((w) => w.url.includes("/generate-plan"));
if (!generated || generated.status !== 200) fail("no successful generate-plan call");
else {
  const meta = JSON.parse(generated.body).metadata ?? {};
  console.log("\n--- generation ---");
  console.log("  provider      :", meta.provider);
  console.log("  model         :", meta.model);
  console.log("  schema        :", meta.plan_schema);
  if (meta.provider !== "anthropic") fail(`provider was ${meta.provider}`);
}
const built = wire.find((w) => w.url.includes("/build-plan"));
if (built) {
  const reply = JSON.parse(built.body);
  console.log("  build backend :", reply.backend, "| path:", reply.execution_path,
              "| render:", reply.render ? `${reply.render.triangles.length} triangles` : "ABSENT");
  if (!reply.render) fail("no render model came back");
  if (EXPECT_BACKEND && reply.backend !== EXPECT_BACKEND) fail("build ran on the wrong backend");
}

await page.screenshot({ path: process.env.SHOT ?? "workspace.png", fullPage: false });

console.log("\nconsole errors  :", consoleErrors.length ? consoleErrors : "none");
console.log("failures        :", failures);
await browser.close();
process.exit(failures === 0 && consoleErrors.length === 0 ? 0 : 1);
