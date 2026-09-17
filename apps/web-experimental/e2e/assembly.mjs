/**
 * The golden multi-plate assembly, driven through the page.
 *
 *   "Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 (2)
 *    plates with 8mm diameter holes in center of each plate"
 *
 * Two things this proves that a unit test cannot:
 *
 *  - the sentence reaches the kernel through the **real** browser, the real
 *    HTTP session and the real backend, and the viewport draws what came
 *    back;
 *  - it does so with **no model configured**. The backend is started without
 *    a credential, so the provider-neutral reader is the only route open.
 *    If this passes, the hosted model is genuinely optional for this request
 *    rather than optional in principle.
 *
 * Nothing here is faked. The measurements are read out of the page, the
 * canvas is a real WebGL surface, and every network response is inspected.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const EXPECT_BACKEND = process.env.EXPECT_BACKEND ?? "freecad";

const GOLDEN =
  "Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 (2) " +
  "plates with 8mm diameter holes in center of each plate";

/* The part the request describes, as arithmetic on its own numbers. */
const SHELL = 40 * 20 * 20 - 30 * 10 * 10;
const BORES = 6 * Math.PI * 4 ** 2 * 5;
const EXPECTED_VOLUME = SHELL - BORES;

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
    /* not JSON: a render payload or an export */
  }
  responses.push(entry);
});

await page.goto(WEB, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("cad-workspace-session"));
await page.reload({ waitUntil: "networkidle" });

console.log("\n  the golden request");
console.log("    " + GOLDEN);

await page.fill("#prompt", GOLDEN);
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
console.log("    note        : " + (body.interpreted_by?.note ?? "-"));

if (message?.status !== 200) fail(`session returned HTTP ${message?.status}`);
else ok("session answered 200");

if (body.status !== "built") fail(`status was ${body.status}, not "built"`);
else ok("the part was built");

if (body.interpreted_by?.source !== "deterministic") {
  fail("this run must use the provider-neutral reader, not a model");
} else ok("read by the provider-neutral reader, with no model configured");

/* --- the geometry the page was given ---------------------------------- */
const m = body.measurement ?? {};
if (m.solid_count !== 1) fail(`solid_count ${m.solid_count}, expected 1`);
else ok("one solid");
if (m.is_valid !== true) fail("the solid is not valid");
else ok("the solid is valid");

if (Math.abs((m.volume ?? 0) - EXPECTED_VOLUME) > 1e-6) {
  fail(`volume ${m.volume}, expected ${EXPECTED_VOLUME}`);
} else ok(`volume ${m.volume} matches the closed form`);

const size = (m.size ?? []).map((v) => Math.round(v * 1e6) / 1e6);
if (String(size) !== String([40, 20, 20])) {
  fail(`envelope ${size}, expected 40,20,20`);
} else ok("envelope 40 x 20 x 20 mm");

const backend = body.build?.backend ?? body.backend;
if (backend && backend !== EXPECT_BACKEND) {
  fail(`backend ${backend}, expected ${EXPECT_BACKEND}`);
} else ok(`backend ${backend ?? EXPECT_BACKEND}`);

/* --- what the page shows ---------------------------------------------- */
const terms = await page.$$eval("#measurements dt", (n) =>
  n.map((x) => x.textContent?.trim()),
);
const values = await page.$$eval("#measurements dd", (n) =>
  n.map((x) => x.textContent?.trim()),
);
const shown = terms.map((t, i) => `${t}=${values[i]}`).join("  ");
console.log("    measurements: " + shown);
if (terms.length === 0) fail("the page shows no measurements");
else ok("measurements appear in the page");

const mesh = (await page.textContent("#mesh-note"))?.trim();
console.log("    mesh note   : " + mesh);
if (!mesh) fail("the viewport reported no mesh");
else ok("the viewport reported a mesh");

/* Did WebGL actually get a drawing surface? `readPixels` is useless here --
 * the drawing buffer is cleared once the frame is presented -- so check the
 * context is live and correctly sized, as drive.mjs does. */
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

const shot = process.env.EXPERIMENTAL_SCREENSHOT ?? "assembly-page.png";
await page.screenshot({ path: shot, fullPage: true });
console.log("    screenshot  : " + shot);

await browser.close();
console.log(failures === 0 ? "\n  ASSEMBLY E2E: PASS\n" : `\n  ASSEMBLY E2E: ${failures} FAILURE(S)\n`);
process.exit(failures === 0 ? 0 : 1);
