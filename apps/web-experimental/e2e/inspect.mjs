/**
 * A real-Chrome UX inspection pass.
 *
 * Drives the installed Chrome (not bundled Chromium, not headless) so what is
 * photographed is what a person sees: real compositor, real fonts, real WebGL.
 * It captures the states a reviewer needs to LOOK at, and reports console and
 * network faults that DOM assertions do not surface.
 *
 * It asserts very little on purpose. Its output is screenshots plus facts;
 * the judgement about hierarchy and spacing is made by looking at them.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const WEB = process.env.EXPERIMENTAL_WEB ?? "http://127.0.0.1:5174/";
const OUT = process.env.SHOT_DIR ?? "shots";
const REQUEST =
  "Create a 100 x 60 x 10 mm plate with an 8 mm through hole at x=10, y=10, " +
  "then fillet the four vertical edges by 2 mm.";
const UNSUPPORTED =
  "Create a spur gear with 24 teeth, module 2, and a keyway broached into the bore.";

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: process.env.HEADED === "1" ? false : true,
  args: ["--force-color-profile=srgb"],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

const consoleMessages = [];
const failedRequests = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "warning") {
    consoleMessages.push(`${m.type()}: ${m.text()}`);
  }
});
page.on("pageerror", (e) => consoleMessages.push(`pageerror: ${String(e)}`));
page.on("requestfailed", (r) =>
  failedRequests.push(`${r.method()} ${r.url()} — ${r.failure()?.errorText}`));
page.on("response", (r) => {
  if (r.status() >= 400) failedRequests.push(`HTTP ${r.status()} ${r.url()}`);
});

const shot = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log(`  shot: ${OUT}/${name}.png`);
};
const text = async (sel) => (await page.textContent(sel))?.trim();

console.log("=== 1. first load ===");
await page.goto(WEB, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
console.log("  title :", await page.title());
console.log("  health:", await text("#health-label"));
await shot("01-first-load");

// Which panels are actually rendered, by measured box, not by attribute.
const visiblePanels = await page.$$eval(
  ".copilot, .panel",
  (nodes) => nodes.filter((n) => n.getBoundingClientRect().width > 0)
                 .map((n) => n.id),
);
console.log("  visible panels:", visiblePanels.join(", ") || "(none)");

console.log("\n=== 2. every navigation surface ===");
const surfaces = await page.$$eval(".rail-item", (n) =>
  n.map((b) => b.dataset.surface));
for (const surface of surfaces) {
  await page.click(`.rail-item[data-surface="${surface}"]`);
  await page.waitForTimeout(220);
  const active = await page.$$eval(".rail-item.is-active", (n) =>
    n.map((b) => b.dataset.surface));
  const shown = await page.$$eval(
    ".copilot, .panel",
    (nodes) => nodes.filter((n) => n.getBoundingClientRect().width > 0)
                    .map((n) => n.id),
  );
  const ok = active.length === 1 && active[0] === surface && shown.length === 1;
  console.log(`  ${surface.padEnd(12)} active=[${active}] visible=[${shown}] ${ok ? "ok" : "!! WRONG"}`);
  await shot(`02-surface-${surface}`);
}

await page.click('.rail-item[data-surface="copilot"]');
await page.waitForTimeout(200);

console.log("\n=== 3. the copilot flow ===");
await page.fill("#prompt", REQUEST);
await shot("03-composed");
await page.click("#send");
await page.waitForTimeout(1400);
await shot("04-in-progress");
await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
  null, { timeout: 240000 });
await page.waitForTimeout(600);
await shot("05-built");
console.log("  backend  :", await text("#stat-backend"));
console.log("  path     :", await text("#stat-path"));
console.log("  build    :", await text("#stat-build"));
console.log("  measure  :", await text("#stat-measure"));
console.log("  mesh     :", await text("#mesh-note"));

console.log("\n=== 4. technical details disclosure ===");
await page.click("#details-toggle");
await page.waitForTimeout(350);
await shot("06-details-open");
const detailsShown = await page.$eval("#details", (n) =>
  n.getBoundingClientRect().height > 0);
console.log("  details visible:", detailsShown);
await page.click("#details-toggle");
await page.waitForTimeout(300);

console.log("\n=== 5. viewport interaction ===");
const box = await page.$eval("#viewport", (c) => {
  const r = c.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});
const cx = box.x + box.w / 2;
const cy = box.y + box.h / 2;
const pixels = async (label) => {
  const data = await page.$eval("#viewport", (c) => {
    const gl = c.getContext("webgl2") ?? c.getContext("webgl");
    if (!gl) return null;
    const buf = new Uint8Array(4 * 40 * 40);
    gl.readPixels(Math.floor(gl.drawingBufferWidth / 2) - 20,
                  Math.floor(gl.drawingBufferHeight / 2) - 20,
                  40, 40, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i += 4) sum += buf[i] + buf[i + 1] + buf[i + 2];
    return sum;
  });
  console.log(`  centre luminance after ${label}: ${data}`);
  return data;
};
await pixels("build");
// orbit
await page.mouse.move(cx, cy);
await page.mouse.down();
await page.mouse.move(cx + 170, cy + 90, { steps: 22 });
await page.mouse.up();
await page.waitForTimeout(400);
await shot("07-orbited");
// zoom
await page.mouse.move(cx, cy);
await page.mouse.wheel(0, -420);
await page.waitForTimeout(400);
await shot("08-zoomed");
// pan (right-drag is OrbitControls' default pan)
await page.mouse.move(cx, cy);
await page.mouse.down({ button: "right" });
await page.mouse.move(cx - 120, cy - 60, { steps: 16 });
await page.mouse.up({ button: "right" });
await page.waitForTimeout(400);
await shot("09-panned");
// fit
await page.click("#fit-view");
await page.waitForTimeout(600);
await shot("10-refit");
await pixels("orbit/zoom/pan/fit");

console.log("\n=== 6. an unsupported request ===");
await page.fill("#prompt", UNSUPPORTED);
await page.click("#send");
await page.waitForFunction(() => !document.querySelector("#send")?.disabled,
  null, { timeout: 240000 });
await page.waitForTimeout(500);
await shot("11-unsupported");
const lastMessage = await page.$$eval(".msg", (nodes) => {
  const n = nodes[nodes.length - 1];
  return { body: n.querySelector(".msg-body")?.textContent?.trim(),
           error: n.classList.contains("msg-error") };
});
console.log("  reply  :", (lastMessage.body ?? "").slice(0, 220));
console.log("  styled as error:", lastMessage.error);
console.log("  model still shown:", await text("#stat-build"));

console.log("\n=== 7. responsive ===");
for (const [w, h, name] of [[1280, 800, "12-1280"], [1100, 760, "13-1100"]]) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(500);
  await shot(name);
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth);
  console.log(`  ${w}x${h} horizontal overflow: ${overflow}`);
}
await page.setViewportSize({ width: 1600, height: 900 });

console.log("\n=== 8. reload ===");
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(900);
await shot("14-after-reload");
console.log("  health :", await text("#health-label"));
console.log("  backend:", await text("#stat-backend"));
console.log("  build  :", await text("#stat-build"));
console.log("  thread :", (await page.$$(".msg")).length, "message(s)");

console.log("\n=== console / network ===");
console.log("  console errors+warnings:", consoleMessages.length ? consoleMessages : "none");
console.log("  failed requests        :", failedRequests.length ? failedRequests : "none");

await browser.close();
