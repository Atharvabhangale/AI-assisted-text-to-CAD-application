/**
 * The package boundary: the frontend is a client, not the CAD engine.
 *
 * These are source-level assertions. They exist because the architectural
 * rule -- geometry, validation, measurement and identity belong to the
 * backend -- is not something a behavioural test can catch being broken
 * quietly: a frontend that recomputed a volume would still show a number.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { pageSource, sourceOf } from "./harness";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "..", "src");

const SOURCES = readdirSync(SRC)
  .filter((name) => name.endsWith(".ts"))
  .sort();

function read(name: string): string {
  return readFileSync(join(SRC, name), "utf8");
}

/** Every import specifier the frontend uses. */
function importsOf(text: string): string[] {
  return [...text.matchAll(/from\s+"([^"]+)"/g)].map((match) => match[1]);
}

describe("the frontend's dependencies", () => {
  it("has the source files the stage describes and no others", () => {
    expect(SOURCES).toEqual([
      "api.ts",
      "app.ts",
      "example.ts",
      "main.ts",
      "render-model.ts",
      "viewer.ts",
    ]);
  });

  it("imports only Three.js and its own modules", () => {
    const allowed = new Set([
      "three",
      "three/addons/controls/OrbitControls.js",
      "./api",
      "./app",
      "./example",
      "./render-model",
      "./viewer",
    ]);
    for (const name of SOURCES) {
      for (const specifier of importsOf(read(name))) {
        expect(allowed.has(specifier), `${name} imports ${specifier}`).toBe(
          true,
        );
      }
    }
  });

  it("declares no CAD, kernel, AI, database or auth dependency", () => {
    const manifest = JSON.parse(
      readFileSync(join(HERE, "..", "package.json"), "utf8"),
    ) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
    };
    expect(Object.keys(manifest.dependencies)).toEqual(["three"]);
    const all = [
      ...Object.keys(manifest.dependencies),
      ...Object.keys(manifest.devDependencies),
    ].join(" ");
    for (const forbidden of [
      "opencascade",
      "occt",
      "cadquery",
      "jscad",
      "csg",
      "openai",
      "anthropic",
      "langchain",
      "socket.io",
      "firebase",
      "supabase",
      "sqlite",
      "dexie",
      "auth",
      "jsonwebtoken",
    ]) {
      expect(all, forbidden).not.toContain(forbidden);
    }
  });

  it("never touches the network directly outside the API client", () => {
    for (const name of SOURCES) {
      if (name === "api.ts") {
        continue;
      }
      const text = read(name);
      expect(text, name).not.toContain("fetch(");
      expect(text, name).not.toContain("XMLHttpRequest");
      expect(text, name).not.toContain("WebSocket");
      expect(text, name).not.toContain("EventSource");
    }
  });
});

describe("no CAD business logic exists in the frontend", () => {
  it("implements no V1 validation rule", () => {
    for (const name of SOURCES) {
      const text = read(name);
      // A quoted rule code would mean the browser is judging documents.
      expect(text.match(/["'](?:S\d{1,2}|E\d)["']/), name).toBeNull();
      // `feature_id`, `field_path` and `feature_index` are *contract fields*
      // the frontend relays; what must not appear is a rule's own text.
      for (const forbidden of [
        "must be > 0",
        "schema_version",
        "features[",
        "rule S",
        "violates",
      ]) {
        if (name === "example.ts") {
          continue; // the preloaded document is input data, checked below
        }
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("builds no geometry of its own", () => {
    for (const name of SOURCES) {
      const text = read(name);
      for (const forbidden of [
        "BoxGeometry",
        "CylinderGeometry",
        "SphereGeometry",
        "ExtrudeGeometry",
        "LatheGeometry",
        "ShapeGeometry",
        "TorusGeometry",
        "computeVertexNormals",
        "mergeVertices",
        "toNonIndexed",
        "tessellate(",
        "triangulate(",
      ]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("reconstructs nothing from STEP, IGES, STL or the CAD document", () => {
    for (const name of SOURCES) {
      const text = read(name);
      for (const forbidden of [
        "STLLoader",
        "STEPLoader",
        "OBJLoader",
        "GLTFLoader",
        "ThreeMFLoader",
        "parseStl",
        "parseStep",
        "loadGeometry",
      ]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
    // The download buttons hand the browser a URL; they never read bytes.
    expect(read("app.ts")).not.toContain("arrayBuffer");
    expect(read("api.ts")).not.toContain("arrayBuffer");
    expect(read("api.ts")).not.toContain("blob(");
  });

  it("performs no boolean or feature operation", () => {
    for (const name of SOURCES) {
      const text = read(name);
      for (const forbidden of [
        "through_hole",
        "diameter",
        "fillet(",
        "chamfer(",
        "subtract(",
        ".cut(",
        ".union(",
        "extrude(",
      ]) {
        if (name === "example.ts") {
          continue; // the preloaded document is input data, checked below
        }
        expect(text.toLowerCase(), `${name}: ${forbidden}`).not.toContain(
          forbidden,
        );
      }
    }
  });

  it("computes no engineering number in the UI layer", () => {
    // Every dimension, volume, solid and triangle count on the page is read
    // from a field the backend sent, so the UI needs no arithmetic at all.
    for (const name of ["app.ts", "api.ts", "example.ts"]) {
      const text = read(name);
      expect(text, `${name}: Math`).not.toContain("Math.");
      for (const forbidden of ["* 2", "/ 2", "** 2", "Number(", "parseFloat"]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
    // The one place a measurement name appears is where it is *read*.
    const app = read("app.ts");
    for (const [name, expression] of [
      ["volume", 'measurement("volume_mm3")'],
      ["bounding box", 'measurement("bounding_box")'],
      ["solids", 'measurement("solid_count")'],
      ["faces", 'measurement("face_count")'],
      ["triangles", 'measurements?.["triangle_count"]'],
    ] as const) {
      expect(app, name).toContain(expression);
    }
    expect(app).toContain("Read a measurement the backend supplied");
  });

  it("hashes nothing: identity comes from the backend", () => {
    for (const name of SOURCES) {
      const text = read(name);
      for (const forbidden of [
        "createHash",
        "sha256",
        "SHA-256",
        "node:crypto",
        "crypto.subtle",
        "digest(",
      ]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("keeps the example a plain data literal", () => {
    const text = read("example.ts");
    expect(text).not.toContain("function");
    expect(text).not.toContain("=>");
    expect(importsOf(text)).toEqual([]);
    expect(text).toContain("as const");
  });

  it("names no artifact filename, extension or filesystem path", () => {
    for (const name of SOURCES) {
      const text = read(name);
      for (const forbidden of [
        ".step",
        ".stp",
        ".iges",
        ".igs",
        ".stl",
        "/tmp",
        "entries/",
        "staging/",
        "manifest.json",
        "artifacts/<",
      ]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("uses the backend's logical id as the only artifact reference", () => {
    const api = read("api.ts");
    expect(api).toContain("/artifacts/${encodeURIComponent(logicalId)}");
    const app = read("app.ts");
    expect(app).toContain("record.logical_id");
    expect(app).not.toContain("checksum");
  });

  it("retrieves the render model by build key and by nothing else", () => {
    const api = read("api.ts");
    expect(api).toContain("/builds/${encodeURIComponent(buildKey)}/render");
    expect(api).not.toContain("/documents/");
    // `execution_id` is a field the response carries; it is never a lookup
    // identity, so it must not appear inside any request path.
    const paths = [...api.matchAll(/`(\/[^`]*)`/g)].map((match) => match[1]);
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path, path).not.toContain("execution");
      expect(path, path).not.toContain("..");
    }
  });
});

describe("nothing environment-specific is hard-coded", () => {
  it("names no origin, domain, port or IP address in the sources", () => {
    for (const name of SOURCES) {
      const text = read(name);
      expect(text, `${name}: scheme`).not.toMatch(/https?:\/\//);
      expect(text, `${name}: IPv4`).not.toMatch(/\b\d{1,3}(?:\.\d{1,3}){3}\b/);
      expect(text, `${name}: localhost`).not.toContain("localhost");
    }
  });

  it("addresses the API by a same-origin path, overridable at build time", () => {
    const api = read("api.ts");
    expect(api).toContain('?? "/api"');
    expect(api).toContain("VITE_API_BASE");
  });

  it("carries no credential, key or token", () => {
    for (const name of SOURCES) {
      const text = read(name).toLowerCase();
      for (const forbidden of [
        "api_key",
        "apikey",
        "secret",
        "password",
        "bearer",
        "authorization",
        "access_token",
        "client_secret",
      ]) {
        expect(text, `${name}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("reaches the backend without CORS, through the dev server's proxy", () => {
    const config = readFileSync(join(HERE, "..", "vite.config.ts"), "utf8");
    // Same origin from the browser's point of view, so the backend needs no
    // CORS headers -- and Stage 22 deliberately did not add any.
    expect(config).toContain("proxy");
    expect(config).toContain("CAD_API_ORIGIN");
    expect(config).toContain("/api");
  });
});

describe("the stage's exclusions hold", () => {
  it("contains no natural-language, LLM, MCP or Onshape code", () => {
    const everything = [
      ...SOURCES.map((name) => read(name)),
      pageSource(),
    ]
      .join("\n")
      .toLowerCase();
    for (const forbidden of [
      "openai",
      "anthropic",
      "gpt-",
      "completion",
      "system prompt",
      "mcp",
      "onshape",
      "featurescript",
      "natural language",
      "describe your part",
    ]) {
      expect(everything, forbidden).not.toContain(forbidden);
    }
  });

  it("contains no authentication, account, database or collaboration code", () => {
    const everything = [...SOURCES.map((name) => read(name)), pageSource()]
      .join("\n")
      .toLowerCase();
    for (const forbidden of [
      "signin",
      "sign in",
      "log in",
      "logout",
      "session",
      "cookie",
      "localstorage",
      "sessionstorage",
      "indexeddb",
      "websocket",
      "presence",
      "collaborat",
    ]) {
      expect(everything, forbidden).not.toContain(forbidden);
    }
  });

  it("is a viewer, not an editor: the only editable field is the document", () => {
    const html = pageSource();
    const editable = [...html.matchAll(/<(textarea|input|select)\b/g)].map(
      (match) => match[1],
    );
    expect(editable).toEqual(["textarea"]);
    expect(html).not.toContain("contenteditable");
    // Every control is a button that does one of the five documented things.
    const buttons = [...html.matchAll(/<button[^>]*id="([^"]+)"/g)].map(
      (match) => match[1],
    );
    expect(buttons.sort()).toEqual([
      "build-button",
      "export-iges",
      "export-step",
      "export-stl",
      "fit-button",
      "load-example",
      "validate-button",
    ]);
  });
});

describe("the page is honest about what validates the document", () => {
  it("says the backend validates and the page does not", () => {
    expect(pageSource()).toContain("The backend validates it; this page does");
  });

  it("labels its inputs and announces status changes", () => {
    const html = pageSource();
    expect(html).toContain('for="document-input"');
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-live="polite"');
  });

  it("keeps the viewer's WebGL code out of the pure conversion module", () => {
    const model = sourceOf("render-model.ts");
    // It imports nothing at all -- not Three.js, not the DOM.
    expect(importsOf(model)).toEqual([]);
    expect(model).not.toContain('from "three"');
    expect(model).not.toContain("BufferGeometry");
    expect(model).not.toContain("document.");
    expect(model).not.toContain("window.");
  });
});
