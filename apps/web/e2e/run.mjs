/**
 * The end-to-end test: the real stack, in a real browser.
 *
 * Nothing is stubbed. This script
 *
 *  1. starts the real backend (`uvicorn` on `cad_api.app:create_app`) against
 *     a fresh temporary cache root,
 *  2. starts the real Vite dev server, which proxies `/api` to it, so the
 *     browser sees one origin and no CORS is involved,
 *  3. drives Chromium through the page: load the example, build, inspect the
 *     result, fit the view, download STEP, IGES and STL,
 *  4. asserts against **application state, the DOM and the real RenderModel**
 *     -- and against the backend's own numbers, fetched independently.
 *
 * No screenshot is compared. The one visual check that is meaningful without
 * pixels is made structurally: the four holes are asserted present in the
 * geometry the browser drew, by counting the mesh's cylindrical walls.
 *
 * Usage: `node e2e/run.mjs` from `apps/web` (see `docs/web-application.md`).
 */

import { spawn } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, "..");
const REPO_ROOT = resolve(WEB_ROOT, "..", "..");

const API_HOST = "127.0.0.1";
const API_PORT = Number(process.env.CAD_E2E_API_PORT ?? 8123);
const WEB_PORT = Number(process.env.CAD_E2E_WEB_PORT ?? 5199);
const API_ORIGIN = `http://${API_HOST}:${API_PORT}`;
const WEB_ORIGIN = `http://${API_HOST}:${WEB_PORT}`;

/** Section D's identity, pinned since Stage 15. */
const SECTION_D_HASH =
  "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc";
const PLATE_SIZE = [100, 60, 10];
const HOLE_DIAMETER = 8;
const HOLE_COUNT = 4;
/** Millimetres. Tessellated geometry is never compared for exact equality. */
const TOLERANCE_MM = 1e-6;
/** The chord error the tessellator was configured with, plus a margin. */
const MESH_TOLERANCE_MM = 0.2;

const children = [];
let workspace = null;
let failures = 0;
let checks = 0;

function check(name, condition, detail = "") {
  checks += 1;
  if (condition) {
    process.stdout.write(`  ok   ${name}\n`);
  } else {
    failures += 1;
    process.stdout.write(`  FAIL ${name}${detail ? ` -- ${detail}` : ""}\n`);
  }
}

function equal(name, actual, expected) {
  check(
    name,
    actual === expected,
    `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
  );
}

function close(name, actual, expected, tolerance) {
  check(
    name,
    Math.abs(actual - expected) <= tolerance,
    `expected ${expected} +/- ${tolerance}, got ${actual}`,
  );
}

function sleep(milliseconds) {
  return new Promise((done) => setTimeout(done, milliseconds));
}

async function waitFor(what, probe, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      if (await probe()) {
        return;
      }
    } catch (cause) {
      last = String(cause);
    }
    await sleep(250);
  }
  throw new Error(`${what} did not become ready. ${last}`);
}

function start(name, command, args, options) {
  const child = spawn(command, args, {
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  children.push({ name, child });
  const log = (stream) => (data) => {
    if (process.env.CAD_E2E_VERBOSE) {
      process.stdout.write(`[${name}:${stream}] ${data}`);
    }
  };
  child.stdout.on("data", log("out"));
  child.stderr.on("data", log("err"));
  child.on("exit", (code) => {
    if (process.env.CAD_E2E_VERBOSE) {
      process.stdout.write(`[${name}] exited with ${code}\n`);
    }
  });
  return child;
}

function shutdown() {
  for (const { child } of children.reverse()) {
    try {
      child.kill("SIGTERM");
    } catch {
      // already gone
    }
  }
  if (workspace !== null) {
    rmSync(workspace, { recursive: true, force: true });
  }
}

async function main() {
  workspace = mkdtempSync(join(tmpdir(), "cad-e2e-"));
  const cacheRoot = join(workspace, "cache");
  const downloads = join(workspace, "downloads");
  // The backend never invents a cache location: it refuses to start unless
  // `CAD_API_CACHE_ROOT` names a directory that already exists.
  mkdirSync(cacheRoot, { recursive: true });
  mkdirSync(downloads, { recursive: true });

  process.stdout.write("starting the backend\n");
  start(
    "api",
    "python3",
    [
      "-m",
      "uvicorn",
      "--factory",
      "cad_api.app:app_from_environment",
      "--host",
      API_HOST,
      "--port",
      String(API_PORT),
      "--log-level",
      "warning",
    ],
    {
      cwd: join(REPO_ROOT, "apps", "api"),
      env: {
        ...process.env,
        // The backend invents no cache path: it is configured, as Stage 22
        // requires, and here it is a throwaway directory.
        CAD_API_CACHE_ROOT: cacheRoot,
        PYTHONPATH: [
          join(REPO_ROOT, "packages", "cad-core", "src"),
          join(REPO_ROOT, "apps", "api", "src"),
        ].join(":"),
      },
    },
  );
  await waitFor("the backend", async () => {
    const response = await fetch(`${API_ORIGIN}/health`);
    return response.ok;
  });

  process.stdout.write("starting the dev server\n");
  start(
    "web",
    process.execPath,
    [join(WEB_ROOT, "node_modules", "vite", "bin", "vite.js"), "--port", String(WEB_PORT), "--strictPort"],
    {
      cwd: WEB_ROOT,
      env: { ...process.env, CAD_API_ORIGIN: API_ORIGIN },
    },
  );
  await waitFor("the dev server", async () => {
    const response = await fetch(WEB_ORIGIN);
    return response.ok;
  });

  // The proxy is what makes the page same-origin with the API. Prove it.
  const proxied = await fetch(`${WEB_ORIGIN}/api/health`);
  check("the dev server proxies /api to the backend", proxied.ok);
  check(
    "the backend sends no CORS header (none was added)",
    proxied.headers.get("access-control-allow-origin") === null,
  );

  process.stdout.write("launching the browser\n");
  const browser = await chromium.launch();
  const context = await browser.newContext({
    acceptDownloads: true,
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();

  // An uncaught exception is always a bug. A console "error" that is just
  // Chromium logging a non-2xx response is not: the invalid-document step
  // below deliberately provokes a 422, and the page handles it.
  const pageErrors = [];
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() !== "error") {
      return;
    }
    const text = message.text();
    if (/Failed to load resource/.test(text)) {
      return;
    }
    consoleErrors.push(text);
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  await page.goto(WEB_ORIGIN, { waitUntil: "load" });
  await page.waitForFunction(() => "__cadApp" in globalThis);

  // --- 1. the page loads, with the example already in it ------------------

  equal(
    "the page starts idle",
    await page.evaluate(() => globalThis.__cadApp.state()),
    "idle",
  );
  const loaded = await page.evaluate(() =>
    JSON.parse(document.querySelector("#document-input").value),
  );
  equal("the example is Section D", loaded.name, "plate-100x60x10-4holes");
  equal("the example has five features", loaded.features.length, 5);

  // --- 2. the build ------------------------------------------------------

  process.stdout.write("building\n");
  await page.click("#build-button");
  await page.waitForFunction(
    () => globalThis.__cadApp.state() !== "building",
    null,
    { timeout: 180000 },
  );

  const state = await page.evaluate(() => globalThis.__cadApp.state());
  equal("the build succeeded", state, "success");
  if (state !== "success") {
    const message = await page.textContent("#status");
    process.stdout.write(`  status line: ${message}\n`);
  }

  const build = await page.evaluate(() => globalThis.__cadApp.lastBuild());
  equal("the document hash is Section D's", build.document_hash, SECTION_D_HASH);
  check(
    "the build key is 64 lowercase hex characters",
    /^[0-9a-f]{64}$/.test(build.build_key),
    build.build_key,
  );
  equal("the build was not a cache hit", build.cache_hit, false);
  equal(
    "all five outputs came back",
    build.outputs.join(","),
    "geometry,step,iges,stl,render",
  );

  const geometry = build.artifacts.find((item) => item.kind === "geometry");
  const measured = geometry.measurements;

  // --- 3. one solid, 100 x 60 x 10 mm ------------------------------------

  equal("the result is one solid", measured.solid_count, 1);
  equal("the result is a solid", measured.is_solid, true);
  for (const [index, axis] of ["x", "y", "z"].entries()) {
    close(
      `the plate measures ${PLATE_SIZE[index]} mm in ${axis}`,
      measured.bounding_box.size[axis],
      PLATE_SIZE[index],
      TOLERANCE_MM,
    );
  }

  // --- 4. the volume matches the backend, and the four holes are real ----

  const nominal = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2];
  const holes =
    HOLE_COUNT * Math.PI * (HOLE_DIAMETER / 2) ** 2 * PLATE_SIZE[2];
  close(
    "the volume is the plate less four through-holes",
    measured.volume_mm3,
    nominal - holes,
    1e-6 * nominal,
  );
  const shownVolume = await page.evaluate(() => {
    const terms = [...document.querySelectorAll("#result dt")];
    const index = terms.findIndex((term) => term.textContent === "Volume");
    return document.querySelectorAll("#result dd")[index].textContent;
  });
  equal(
    "the page shows the backend's volume verbatim",
    shownVolume,
    `${measured.volume_mm3} mm3`,
  );

  // The same numbers, fetched from the backend independently of the browser.
  const direct = await (
    await fetch(`${API_ORIGIN}/builds/${build.build_key}`)
  ).json();
  const directGeometry = direct.artifacts.find(
    (item) => item.kind === "geometry",
  );
  equal(
    "the browser and a direct retrieval agree on the volume",
    directGeometry.measurements.volume_mm3,
    measured.volume_mm3,
  );

  // --- 5. the render model loaded, and holds four cylindrical walls ------

  const model = await page.evaluate(() => globalThis.__cadApp.lastModel());
  check("the render model loaded", model !== null);
  equal("the render model is version 1.0.0", model.format_version, "1.0.0");
  equal("the render model is in millimetres", model.units, "mm");
  equal(
    "the render model is right-handed Z-up",
    model.coordinate_system,
    "right_handed_z_up",
  );
  equal(
    "there is one normal per vertex",
    model.normals.length,
    model.vertices.length,
  );
  check("the mesh has triangles", model.triangles.length > 0);
  for (const [index, expected] of PLATE_SIZE.entries()) {
    close(
      `the render bounds span ${expected} mm on axis ${index}`,
      model.bounds.size[index],
      expected,
      TOLERANCE_MM,
    );
  }

  // Four holes, structurally: cluster the mesh's radial wall vertices by the
  // axis each one circles. On a through-hole the solid's outward normal points
  // *into* the void, so stepping one radius **along** the normal lands on the
  // centreline -- and four distinct centrelines, at the corner offsets Section
  // D specifies, is the geometry having four holes. The plate's own four flat
  // sides are excluded by their axis-aligned normals; a handful of seam
  // vertices carry a blended normal and are outvoted by the count threshold.
  const centres = await page.evaluate(
    ({ radius, tolerance, minimumVotes }) => {
      const model = globalThis.__cadApp.lastModel();
      const found = [];
      for (let index = 0; index < model.vertices.length; index += 1) {
        const [x, y] = model.vertices[index];
        const [nx, ny, nz] = model.normals[index];
        if (Math.abs(nz) > 1e-6) {
          continue; // a flat top or bottom face
        }
        if (Math.abs(Math.abs(nx) - 1) < 1e-9 || Math.abs(Math.abs(ny) - 1) < 1e-9) {
          continue; // one of the plate's four flat sides
        }
        const cx = x + nx * radius;
        const cy = y + ny * radius;
        const near = found.find(
          (candidate) =>
            Math.abs(candidate.x - cx) < tolerance &&
            Math.abs(candidate.y - cy) < tolerance,
        );
        if (near === undefined) {
          found.push({ x: cx, y: cy, votes: 1 });
        } else {
          // A running mean, so the cluster is not pinned to its first vertex.
          near.x += (cx - near.x) / (near.votes + 1);
          near.y += (cy - near.y) / (near.votes + 1);
          near.votes += 1;
        }
      }
      return found.filter((candidate) => candidate.votes >= minimumVotes);
    },
    { radius: HOLE_DIAMETER / 2, tolerance: 0.5, minimumVotes: 20 },
  );
  equal(
    "the drawn mesh contains four cylindrical walls",
    centres.length,
    HOLE_COUNT,
  );
  const expectedCentres = [
    [10, 10],
    [90, 10],
    [10, 50],
    [90, 50],
  ];
  for (const [x, y] of expectedCentres) {
    check(
      `a hole is centred at (${x}, ${y}) mm`,
      centres.some(
        (centre) =>
          Math.abs(centre.x - x) <= MESH_TOLERANCE_MM &&
          Math.abs(centre.y - y) <= MESH_TOLERANCE_MM,
      ),
      JSON.stringify(
        centres.map((c) => [c.x.toFixed(3), c.y.toFixed(3), c.votes]),
      ),
    );
  }

  // Per-face vertices survived the round trip through the real transport.
  const distinct = await page.evaluate(() => {
    const model = globalThis.__cadApp.lastModel();
    return new Set(model.vertices.map((vertex) => vertex.join(","))).size;
  });
  check(
    "per-face vertices are preserved",
    distinct < model.vertices.length,
    `${distinct} distinct of ${model.vertices.length}`,
  );

  // --- 6. the WebGL viewport actually drew it ----------------------------

  const drawn = await page.evaluate(() => {
    const canvas = document.querySelector("#viewer-canvas");
    const context =
      canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    return {
      hasContext: context !== null,
      width: canvas.width,
      height: canvas.height,
      summary: document.querySelector("#geometry-summary").textContent,
    };
  });
  check("the canvas has a WebGL context", drawn.hasContext);
  check("the canvas has a size", drawn.width > 0 && drawn.height > 0);
  check(
    "the viewport is described by the backend's counts",
    drawn.summary.includes(`${model.triangles.length} triangles`),
    drawn.summary,
  );
  equal(
    "fit-to-view is available",
    await page.isDisabled("#fit-button"),
    false,
  );
  await page.click("#fit-button");
  await sleep(300);
  equal(
    "fitting the view breaks nothing",
    await page.evaluate(() => globalThis.__cadApp.state()),
    "success",
  );

  // --- 7. the exports -----------------------------------------------------

  for (const kind of ["step", "iges", "stl"]) {
    const button = `#export-${kind}`;
    equal(`the ${kind.toUpperCase()} button is offered`, await page.isVisible(button), true);
    const logicalId = await page.getAttribute(button, "data-logical-id");
    equal(
      `the ${kind.toUpperCase()} button carries the artifact id`,
      logicalId,
      `${build.build_key}:${kind}`,
    );

    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 60000 }),
      page.click(button),
    ]);
    const target = join(downloads, `${kind}.bin`);
    await download.saveAs(target);
    const size = statSync(target).size;
    const record = build.artifacts.find((item) => item.kind === kind);
    equal(
      `the downloaded ${kind.toUpperCase()} is the size the backend published`,
      size,
      record.size_bytes,
    );
    check(
      `the ${kind.toUpperCase()} download URL is the artifact endpoint`,
      download.url() ===
        `${WEB_ORIGIN}/api/artifacts/${encodeURIComponent(record.logical_id)}`,
      download.url(),
    );
    const head = readFileSync(target).subarray(0, 80).toString("latin1");
    if (kind === "step") {
      check("the STEP file is a STEP file", head.startsWith("ISO-10303-21"));
    } else if (kind === "iges") {
      check("the IGES file is an IGES file", /S\s*0*1/.test(head.slice(70)));
    } else {
      check(
        "the STL file is binary STL of the right length",
        size === 84 + 50 * readFileSync(target).readUInt32LE(80),
      );
    }
  }

  // --- 8. a second build is a cache hit, and renders the same thing ------

  process.stdout.write("rebuilding, for the cache\n");
  await page.click("#build-button");
  await page.waitForFunction(
    () => globalThis.__cadApp.state() !== "building",
    null,
    { timeout: 180000 },
  );
  const second = await page.evaluate(() => globalThis.__cadApp.lastBuild());
  equal(
    "the second build succeeded",
    await page.evaluate(() => globalThis.__cadApp.state()),
    "success",
  );
  equal("the second build was a cache hit", second.cache_hit, true);
  equal("the build key is unchanged", second.build_key, build.build_key);
  const secondModel = await page.evaluate(() =>
    globalThis.__cadApp.lastModel(),
  );
  equal(
    "the cached render model has the same vertex count",
    secondModel.vertices.length,
    model.vertices.length,
  );
  check(
    "the cached render model is identical",
    JSON.stringify(secondModel) === JSON.stringify(model),
  );

  // --- 9. a failure is shown, and nothing internal leaks -----------------

  process.stdout.write("building an invalid document\n");
  await page.evaluate(() => {
    const input = document.querySelector("#document-input");
    const parsed = JSON.parse(input.value);
    parsed.features[0].size.x = 0; // rule S10
    input.value = JSON.stringify(parsed, null, 2);
  });
  await page.click("#build-button");
  await page.waitForFunction(
    () => globalThis.__cadApp.state() !== "building",
    null,
    { timeout: 180000 },
  );
  equal(
    "an invalid document is a validation error",
    await page.evaluate(() => globalThis.__cadApp.state()),
    "validation-error",
  );
  const shown = await page.evaluate(() =>
    [
      document.querySelector("#status").textContent,
      document.querySelector("#errors").textContent,
      document.querySelector("#result").textContent,
    ].join("\n"),
  );
  check("the rule code is shown", shown.includes("S10"), shown);
  for (const token of [
    "Traceback",
    "/tmp",
    "/home/",
    "site-packages",
    ".py",
    "cadquery",
    "OCP",
    "entries",
    "manifest.json",
    "exit code",
  ]) {
    check(`the page does not show ${token}`, !shown.includes(token));
  }

  // --- 10. no console errors along the way -------------------------------

  check(
    "the page threw no uncaught exception",
    pageErrors.length === 0,
    pageErrors.join(" | "),
  );
  check(
    "the browser logged no error of its own",
    consoleErrors.length === 0,
    consoleErrors.join(" | "),
  );

  await context.close();
  await browser.close();
}

main()
  .then(() => {
    process.stdout.write(
      `\n${checks - failures}/${checks} checks passed\n`,
    );
    shutdown();
    process.exit(failures === 0 ? 0 : 1);
  })
  .catch((error) => {
    process.stdout.write(`\nend-to-end run failed: ${error}\n`);
    shutdown();
    process.exit(2);
  });
