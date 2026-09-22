/**
 * Two independent bodies, driven through the page.
 *
 *   "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two
 *    separate bodies."
 *
 * What this proves that a unit test cannot: the sentence reaches the kernel
 * through the real browser, the real HTTP session and the real backend, and
 * the viewport draws TWO bodies -- with no model configured, so the
 * provider-neutral reader is the only route open.
 *
 * The thing it is really looking for is the failure this whole stage exists
 * to prevent: a two-body part arriving on screen as one body, with the page
 * reporting a successful build. So it checks the body COUNT everywhere it
 * can be checked -- the payload, the meshes, the note under the viewport --
 * and it checks that nothing was fused, by arithmetic on the two volumes.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "freecad";

const REQUEST =
  "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two " +
  "separate bodies.";

/* The two bodies the request describes, as arithmetic on its own numbers. */
const CUBE_VOLUME = 40 ** 3;
const PIN_VOLUME = Math.PI * 10 ** 2 * 30;

let failures = 0;
const fail = (why) => {
  console.log("    !! " + why);
  failures++;
};
const ok = (what) => console.log("    ok " + what);

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
  try {
    entry.body = JSON.parse(await r.text());
  } catch {
    /* not JSON */
  }
  responses.push(entry);
});

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });

console.log("\n  the two-body request");
console.log("    " + REQUEST);

await page.fill("#prompt", REQUEST);
await page.click("#send");
await page.waitForFunction(
  () => !document.querySelector("#send")?.disabled,
  null,
  { timeout: 300_000 },
);
await page.waitForTimeout(600);

/* --- what the backend said ------------------------------------------- */
const message = [...responses]
  .reverse()
  .find((r) => r.url.includes("/session/message"));
if (!message) fail("no /session/message response was seen");
const body = message?.body ?? {};
console.log("    status      : " + body.status);
console.log("    interpreted : " + (body.interpreted_by?.source ?? "provider"));

if (message?.status !== 200) fail(`session returned HTTP ${message?.status}`);
else ok("session answered 200");

if (body.status !== "built") fail(`status was ${body.status}, not "built"`);
else ok("the part was built");

if (body.interpreted_by?.source !== "deterministic") {
  fail("this run must use the provider-neutral reader, not a model");
} else ok("read by the provider-neutral reader, with no model configured");

/* --- two bodies, and they are declared -------------------------------- */
const bodies = body.bodies ?? [];
console.log("    bodies      : " + bodies.map((b) => b.body_id).join(", "));
if (bodies.length !== 2) fail(`${bodies.length} bodies, expected 2`);
else ok("the payload carries two bodies");

if (String(body.declared_bodies ?? []) !== "cube,cylinder") {
  fail(`declared_bodies ${body.declared_bodies}, expected cube,cylinder`);
} else ok("both bodies are declared in the plan");

if (!bodies.every((b) => b.declared)) fail("a body is not declared");
else ok("every body reports itself declared");

/* Nothing fused: two solids, and the volumes are the two closed forms. */
const byId = Object.fromEntries(bodies.map((b) => [b.body_id, b]));
for (const [id, expected] of [["cube", CUBE_VOLUME], ["cylinder", PIN_VOLUME]]) {
  const m = byId[id]?.measurement ?? {};
  if (m.solid_count !== 1) fail(`${id}: solid_count ${m.solid_count}, expected 1`);
  else ok(`${id}: one solid`);
  if (Math.abs((m.volume ?? 0) - expected) > 1e-6) {
    fail(`${id}: volume ${m.volume}, expected ${expected}`);
  } else ok(`${id}: volume ${m.volume} matches the closed form`);
}

/* The part-level measurement must NOT be one body's, silently. */
if (Object.keys(body.measurement ?? {}).length !== 0) {
  fail("a part-level measurement was reported for a two-body part: "
       + JSON.stringify(body.measurement));
} else ok("no single part-level measurement is claimed");

if (body.render !== undefined) {
  fail("a single merged render model was sent for a two-body part");
} else ok("no merged mesh was sent");

if (!bodies.every((b) => b.render)) fail("a body arrived without a mesh");
else ok("each body carries its own mesh");

const backend = body.backend;
if (backend && backend !== EXPECT_BACKEND) {
  fail(`backend ${backend}, expected ${EXPECT_BACKEND}`);
} else ok(`backend ${backend ?? EXPECT_BACKEND}`);

/* --- what the page shows ---------------------------------------------- */
const mesh = (await page.textContent("#mesh-note"))?.trim();
console.log("    mesh note   : " + JSON.stringify(mesh));
if (!mesh) fail("the viewport reported no mesh");
else ok("the viewport reported a mesh");
for (const id of ["cube", "cylinder"]) {
  if (!mesh?.includes(id)) fail(`the mesh note does not name ${id}`);
  else ok(`the mesh note names ${id}`);
}

const canvas = await page.evaluate(() => {
  const el = document.querySelector("#viewport");
  const gl = el?.getContext("webgl2") ?? el?.getContext("webgl");
  if (!gl) return { context: false };
  return {
    context: true,
    width: gl.drawingBufferWidth,
    height: gl.drawingBufferHeight,
  };
});
console.log("    canvas      : " + JSON.stringify(canvas));
if (!canvas.context || canvas.width <= 0 || canvas.height <= 0) {
  fail("the viewport has no live WebGL surface");
} else ok("the viewport has a live WebGL surface");

/* --- nothing broke ----------------------------------------------------- */
const failed = responses.filter((r) => r.status >= 400);
if (failed.length > 0) {
  fail("failed requests: " + failed.map((r) => `${r.status} ${r.url}`).join(", "));
} else ok("no failed requests");

const real = consoleErrors.filter((t) => !/favicon/i.test(t));
if (real.length > 0) fail("console errors: " + real.join(" | "));
else ok("no console errors");

const shot = process.env.EXPERIMENTAL_SCREENSHOT ?? "multibody-page.png";
await page.screenshot({ path: shot, fullPage: true });
console.log("    screenshot  : " + shot);

await browser.close();
console.log(failures === 0 ? "\n  MULTI-BODY E2E: PASS\n"
                           : `\n  MULTI-BODY E2E: ${failures} FAILURE(S)\n`);
process.exit(failures === 0 ? 0 : 1);
