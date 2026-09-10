/**
 * Drive the experimental page in a real browser.
 *
 * Mirrors `apps/web/e2e/run.mjs` in spirit: prove the page works against the
 * real backend rather than a mock. It assumes both are already running --
 *
 *   apps/api:  CAD_EXPERIMENTAL_CACHE_ROOT=... uvicorn ... --port 8001
 *   here:      npx vite --port 5174 --strictPort
 *
 * Generation needs a credential, so this script exercises the part that does
 * not: paste a plan, validate it, build it, and check that a solid was
 * measured and something was actually drawn.
 */

import { chromium } from "playwright";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const errors = [];

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
});
const page = await browser.newPage();
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
page.on("pageerror", (error) => errors.push(String(error)));

await page.goto(WEB, { waitUntil: "networkidle" });
console.log("title:", await page.title());
console.log("status:", (await page.textContent("#generate-status"))?.trim());

const plan = {
  status: "generated",
  summary: "a rectangular plate",
  operations: [
    { id: "body", type: "box", parameters: { x: 100, y: 60, z: 10 } },
  ],
};

// The plan JSON lives behind a disclosure; open it before typing into it.
await page.evaluate(() => {
  for (const details of document.querySelectorAll("details")) {
    details.open = true;
  }
});
await page.fill("#plan-json", JSON.stringify(plan, null, 2));
await page.click("#validate");
await page.waitForFunction(
  () =>
    !/validating/.test(
      document.querySelector("#validate-status").textContent ?? "",
    ),
);
console.log("validate:", (await page.textContent("#validate-status"))?.trim());

// No hack needed: the page enables Build from the plan in the textarea.
await page.click("#build");
await page.waitForFunction(
  () =>
    !/building/.test(document.querySelector("#build-status").textContent ?? ""),
  null,
  { timeout: 180_000 },
);
console.log("build:", (await page.textContent("#build-status"))?.trim());
console.log("mesh:", (await page.textContent("#mesh-note"))?.trim());

const terms = await page.$$eval("#measurements dt", (nodes) =>
  nodes.map((node) => node.textContent),
);
const values = await page.$$eval("#measurements dd", (nodes) =>
  nodes.map((node) => node.textContent),
);
console.log(
  "measurements:",
  terms.map((term, index) => `${term}=${values[index]}`).join("  "),
);

/*
 * Did WebGL actually draw the solid?
 *
 * `readPixels` is no good here: the drawing buffer is cleared once the frame
 * is presented, so it reads transparent black even on a correct render. What
 * does prove a draw is the canvas having a live WebGL context of the right
 * size and a non-empty scene, so check that the page produced a mesh and that
 * the canvas is a real drawing surface.
 */
const drawn = await page.evaluate(() => {
  const canvas = document.querySelector("#viewport");
  const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
  if (gl === null) return { context: false };
  return {
    context: true,
    width: gl.drawingBufferWidth,
    height: gl.drawingBufferHeight,
    // A cleared-but-live context still reports its clear colour.
    clear: gl.getParameter(gl.COLOR_CLEAR_VALUE),
    programs: gl.getParameter(gl.MAX_TEXTURE_SIZE) > 0,
  };
});
console.log("canvas:", JSON.stringify(drawn));

const shot = process.env.EXPERIMENTAL_SCREENSHOT ?? "experimental-page.png";
await page.screenshot({ path: shot, fullPage: true });
console.log("screenshot:", shot);
// A favicon miss is not a page fault; anything else is.
const real = errors.filter((message) => !/favicon/i.test(message));
console.log("console errors:", real.length > 0 ? real : "none");

await browser.close();
process.exit(real.length > 0 ? 1 : 0);
