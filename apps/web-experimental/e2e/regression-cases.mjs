/**
 * The real browser path, driven end to end against a real model.
 *
 * Exists because "Build failed" appeared on the page for plans the backend
 * had built correctly, and only the page could show that: the API answered
 * 200 throughout. Every request and response on the wire is captured here so
 * the claim "the backend succeeded and the page said otherwise" is evidence
 * rather than an assertion.
 *
 * Assumes both are already running -- backend 8001, this page on 5174 --
 * and that a real credential is configured. It uses no fixture.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";

const CASES = [
  {
    name: "CASE A — plate + hole + straight-Z corner fillet",
    text:
      "Create a 100 x 60 x 10 mm mounting plate with a centered 20 mm " +
      "through hole, then fillet the four external vertical corner edges " +
      "with a 2 mm radius.",
    expectSelector: { select: "straight", axis: "Z" },
  },
  {
    name: "CASE B — plate + hole + circular top rim chamfer",
    text:
      "Create a 100 x 60 x 10 mm mounting plate with a 20 mm hole through " +
      "the center, then chamfer the top circular rim by 1 mm.",
    expectSelector: { select: "circular", axis: "Z", position: "top" },
  },
  { name: "CASE C — simple box", text: "Create a 40 mm cube.", expectSelector: null },
  {
    name: "CASE D — simple plate + hole",
    text:
      "Create a 100 x 60 x 10 mm plate with a 20 mm hole through its centre.",
    expectSelector: null,
  },
];

/**
 * Which engine the page must actually have built on, when asserted.
 *
 * Set to `freecad` and every build in the run has to report that backend --
 * a run that quietly produced CadQuery geometry fails here rather than
 * looking like a success.
 */
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "";
const ONLY = (process.env.ONLY_CASES ?? "").trim();

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
});
const page = await browser.newPage();

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

// Capture the exact wire traffic for the three experimental routes.
const wire = [];
page.on("request", (r) => {
  if (r.url().includes("/experimental/")) {
    wire.push({ dir: ">>", url: r.url(), body: r.postData() });
  }
});
page.on("response", async (r) => {
  if (r.url().includes("/experimental/")) {
    let body = null;
    try {
      body = await r.text();
    } catch {
      /* body already consumed */
    }
    wire.push({ dir: "<<", url: r.url(), status: r.status(), body });
  }
});

const text = async (sel) => (await page.textContent(sel))?.trim();
let failures = 0;

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => {
  for (const d of document.querySelectorAll("details")) d.open = true;
});

for (const testCase of CASES) {
  if (ONLY !== "" && !ONLY.split(",").some((k) => testCase.name.includes(k))) {
    continue;
  }
  console.log("\n" + "=".repeat(74));
  console.log(testCase.name);
  wire.length = 0;

  // --- generate, through the page's own button -----------------------------
  //
  // Wait on the page's OWN in-progress wording ("asking the model…"), not a
  // guess at it. A predicate that matches nothing returns true immediately
  // and reads the previous case's DOM, which is a test that always passes
  // while proving nothing -- this driver did exactly that once already.
  await page.fill("#description", testCase.text);
  await page.click("#generate");
  await page.waitForFunction(
    () => /asking the model/i.test(
      document.querySelector("#generate-status")?.textContent ?? ""),
    null,
    { timeout: 30000 },
  );
  await page.waitForFunction(
    () => !/asking the model/i.test(
      document.querySelector("#generate-status")?.textContent ?? ""),
    null,
    { timeout: 180000 },
  );
  console.log("  generate status :", await text("#generate-status"));

  const planJson = await page.inputValue("#plan-json");
  let plan = null;
  try {
    plan = JSON.parse(planJson);
  } catch {
    console.log("  !! the page did not put a parseable plan in #plan-json");
  }
  if (plan) {
    console.log(
      "  operations      :",
      (plan.operations ?? []).map((o) => `${o.id}:${o.type}`).join(", "),
    );
    for (const op of plan.operations ?? []) {
      const edges = op.parameters?.edges;
      if (edges) console.log("  selector        :", JSON.stringify(edges));
      if (edges && testCase.expectSelector) {
        for (const [k, v] of Object.entries(testCase.expectSelector)) {
          if (edges[k] !== v) {
            console.log(`  !! selector.${k} = ${edges[k]}, expected ${v}`);
            failures++;
          }
        }
      }
    }
  }

  // --- validate ------------------------------------------------------------
  await page.click("#validate");
  await page.waitForFunction(
    () => {
      const s = document.querySelector("#validate-status")?.textContent ?? "";
      return s !== "" && !/validating/i.test(s);
    },
    null,
    { timeout: 60000 },
  );
  console.log("  validate status :", await text("#validate-status"));

  // --- build ---------------------------------------------------------------
  await page.click("#build");
  await page.waitForFunction(
    () => /building/i.test(
      document.querySelector("#build-status")?.textContent ?? ""),
    null,
    { timeout: 30000 },
  );
  await page.waitForFunction(
    () => !/building/i.test(
      document.querySelector("#build-status")?.textContent ?? ""),
    null,
    { timeout: 180000 },
  );
  const buildStatus = await text("#build-status");
  console.log("  BUILD STATUS    :", buildStatus);
  if (buildStatus !== "built") {
    console.log("  !! expected 'built'");
    failures++;
  }

  const measured = await page.$$eval("#measurements dt, #measurements dd",
    (nodes) => nodes.map((n) => n.textContent?.trim() ?? ""));
  const pairs = [];
  for (let i = 0; i < measured.length; i += 2) {
    pairs.push(`${measured[i]}=${measured[i + 1]}`);
  }
  console.log("  measurements    :", pairs.join("  "));
  const meshNote = await text("#mesh-note");
  console.log("  mesh note       :", meshNote);

  // The build-plan response is the authority on which engine ran and
  // whether a mesh came back -- not the page, which could in principle
  // render a stale model. Read it off the wire.
  const buildReply = wire.find(
    (e) => e.dir === "<<" && e.url.includes("/build-plan"));
  let reply = null;
  try {
    reply = buildReply ? JSON.parse(buildReply.body ?? "null") : null;
  } catch {
    reply = null;
  }
  if (reply) {
    const mesh = reply.render ?? null;
    console.log(
      `  backend         : ${reply.backend}   path: ${reply.execution_path}` +
        `   render: ${mesh ? `${mesh.triangles?.length ?? 0} triangles` : "ABSENT"}`,
    );
    if (EXPECT_BACKEND !== "" && reply.backend !== EXPECT_BACKEND) {
      console.log(`  !! backend was ${reply.backend}, expected ${EXPECT_BACKEND}`);
      failures++;
    }
    if (!mesh || (mesh.triangles?.length ?? 0) === 0) {
      console.log("  !! no non-empty render model came back");
      failures++;
    }
  } else {
    console.log("  !! no build-plan response captured");
    failures++;
  }
  if (/no mesh to draw|no render model/.test(meshNote ?? "")) {
    console.log("  !! the page reported it had nothing to draw");
    failures++;
  }

  // --- the wire, which is the evidence ------------------------------------
  for (const entry of wire) {
    const route = entry.url.split("/experimental/")[1];
    if (entry.dir === ">>") {
      console.log(`    >> POST ${route}  body=${(entry.body ?? "").slice(0, 160)}`);
    } else {
      console.log(
        `    << ${entry.status} ${route}  ${(entry.body ?? "").slice(0, 240)}`,
      );
    }
  }
}

console.log("\n" + "=".repeat(74));
console.log("console errors :", consoleErrors.length ? consoleErrors : "none");
console.log("case failures  :", failures);
await browser.close();
process.exit(failures === 0 && consoleErrors.length === 0 ? 0 : 1);
