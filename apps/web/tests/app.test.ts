/**
 * The browser application: loading, building, rendering, failing and exporting.
 *
 * Every assertion is about application state, the DOM, or the real
 * `RenderModel` data. Nothing here compares pixels.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { ApiClient, REQUESTED_OUTPUTS } from "../src/api";
import {
  collectElements,
  createApp,
  wireExports,
  type Elements,
} from "../src/app";
import { SECTION_D_DOCUMENT } from "../src/example";
import {
  cachedBuild,
  geometryFailure,
  installPage,
  recordingViewer,
  resultRows,
  sectionDDocument,
  sectionDRender,
  stubFetch,
  successBuild,
  validationFailure,
  visibleText,
  type RecordingViewer,
} from "./harness";

const BUILD_KEY =
  "a6cd6fa167f396db95f7bb618a98ef9867debaecc014846264b591ac30e2ca21";
const DOCUMENT_HASH =
  "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc";
const BASE = "/api";

interface Harness {
  readonly elements: Elements;
  readonly viewer: RecordingViewer;
  readonly calls: ReturnType<typeof stubFetch>["calls"];
  readonly client: ApiClient;
  readonly app: ReturnType<typeof createApp>;
}

function harness(
  routes: Record<string, unknown | ((call: never) => unknown)>,
): Harness {
  const elements = installPage();
  const viewer = recordingViewer();
  const stub = stubFetch({ routes: routes as never });
  const client = new ApiClient({ base: BASE, fetch: stub.fetch });
  const app = createApp({ elements, client, viewer: () => viewer });
  return { elements, viewer, calls: stub.calls, client, app };
}

function successRoutes(build = successBuild()) {
  return { "/build": build, "/render": sectionDRender() };
}

describe("the application loads", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("finds every element the page promises", () => {
    const elements = installPage();
    expect(elements.documentInput.tagName).toBe("TEXTAREA");
    expect(elements.buildButton.tagName).toBe("BUTTON");
    expect(elements.status.getAttribute("role")).toBe("status");
    expect(elements.status.getAttribute("aria-live")).toBe("polite");
  });

  it("starts idle, with nothing shown and no exports offered", () => {
    const { elements, app } = harness({});
    expect(app.state()).toBe("idle");
    expect(elements.status.dataset.state).toBe("idle");
    expect(elements.result.hidden).toBe(true);
    expect(elements.errors.hidden).toBe(true);
    expect(elements.fitButton.disabled).toBe(true);
    for (const kind of ["step", "iges", "stl"] as const) {
      expect(elements.exportButtons[kind].hidden).toBe(true);
    }
    expect(app.lastBuild()).toBeNull();
    expect(app.lastModel()).toBeNull();
  });

  it("throws a clear error if the page is missing an element", () => {
    installPage();
    document.getElementById("build-button")?.remove();
    expect(() => collectElements(document)).toThrow(/#build-button/);
  });
});

describe("the JSON example loads", () => {
  it("puts the specification's Section D document in the textarea", () => {
    const { elements, app } = harness({});
    app.loadExample();
    const parsed = JSON.parse(elements.documentInput.value) as {
      name: string;
      features: unknown[];
    };
    expect(parsed).toEqual(SECTION_D_DOCUMENT);
    expect(parsed.name).toBe("plate-100x60x10-4holes");
    expect(parsed.features).toHaveLength(5);
    expect(app.state()).toBe("idle");
  });

  it("is byte-for-byte the document the backend accepted", () => {
    // The fixture is the document that was actually posted when the other
    // fixtures were captured, so the preloaded example cannot drift into
    // something the validator would reject.
    expect(SECTION_D_DOCUMENT).toEqual(sectionDDocument());
  });
});

describe("the build button sends the expected request", () => {
  it("posts the parsed document and every output the viewer needs", async () => {
    const { app, calls } = harness(successRoutes());
    app.loadExample();
    await app.build();

    const build = calls.find((call) => call.url.endsWith("/build"));
    expect(build).toBeDefined();
    expect(build?.method).toBe("POST");
    expect(build?.url).toBe(`${BASE}/build`);
    expect(build?.body).toEqual({
      document: SECTION_D_DOCUMENT,
      outputs: [...REQUESTED_OUTPUTS],
    });
  });

  it("fetches the render model by build key, not by path or filename", async () => {
    const { app, calls } = harness(successRoutes());
    app.loadExample();
    await app.build();

    const render = calls.find((call) => call.url.includes("/render"));
    expect(render?.method).toBe("GET");
    expect(render?.url).toBe(`${BASE}/builds/${BUILD_KEY}/render`);
    expect(render?.body).toBeUndefined();
  });

  it("refuses to send a document that is not JSON", async () => {
    const { elements, app, calls } = harness(successRoutes());
    elements.documentInput.value = "{not json";
    await app.build();
    expect(app.state()).toBe("validation-error");
    expect(calls).toHaveLength(0);
  });
});

describe("a successful build response is rendered", () => {
  it("reaches the success state and draws the model the backend sent", async () => {
    const { app, viewer } = harness(successRoutes());
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("success");
    expect(viewer.shown).toHaveLength(1);
    expect(viewer.shown[0]).toEqual(sectionDRender());
    expect(app.lastModel()?.vertices).toHaveLength(2048);
    expect(app.lastModel()?.triangles).toHaveLength(2044);
  });

  it("shows the result and enables fit-to-view", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();

    expect(elements.result.hidden).toBe(false);
    expect(elements.errors.hidden).toBe(true);
    expect(elements.fitButton.disabled).toBe(false);
    expect(resultRows(elements).Status).toBe("succeeded");
  });
});

describe("a cache-hit result renders", () => {
  it("draws the same geometry and says the result came from cache", async () => {
    const { elements, app, viewer } = harness(successRoutes(cachedBuild()));
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("success");
    expect(app.lastBuild()?.cache_hit).toBe(true);
    expect(app.lastBuild()?.execution_id).toBeNull();
    expect(viewer.shown).toHaveLength(1);
    expect(resultRows(elements).Source).toBe("cache");
    expect(elements.status.textContent).toMatch(/cache/i);
  });

  it("gives a cache hit the same build key and measurements as the cold build", () => {
    const cold = successBuild();
    const warm = cachedBuild();
    expect(warm.build_key).toBe(cold.build_key);
    expect(warm.document_hash).toBe(cold.document_hash);
    const geometry = (response: typeof cold) =>
      response.artifacts.find((item) => item.kind === "geometry")?.measurements;
    expect(geometry(warm)).toEqual(geometry(cold));
  });
});

describe("a validation error is displayed", () => {
  it("shows the rule code and the validator's own message", async () => {
    const { elements, app } = harness({ "/validate": validationFailure() });
    app.loadExample();
    await app.validate();

    expect(app.state()).toBe("validation-error");
    expect(elements.errors.hidden).toBe(false);
    const text = elements.errors.textContent ?? "";
    expect(text).toContain("S10");
    expect(text).toContain("size component 'x' must be > 0");
    expect(text).toContain("features[0].size.x");
    expect(elements.result.hidden).toBe(true);
  });

  it("reports a valid document as valid", async () => {
    const { elements, app } = harness({
      "/validate": {
        valid: true,
        document_hash: DOCUMENT_HASH,
        name: "plate-100x60x10-4holes",
        feature_count: 5,
        document: SECTION_D_DOCUMENT,
        error: null,
      },
    });
    app.loadExample();
    await app.validate();

    expect(app.state()).toBe("success");
    expect(elements.status.textContent).toContain("plate-100x60x10-4holes");
    expect(elements.errors.hidden).toBe(true);
  });
});

describe("a build error is displayed", () => {
  it("distinguishes a geometry failure from a document failure", async () => {
    const { elements, app, viewer } = harness({ "/build": geometryFailure() });
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("build-error");
    expect(elements.status.dataset.state).toBe("build-error");
    expect(elements.status.textContent).toContain("does not intersect");
    expect(elements.errors.textContent).toContain("E1");
    expect(elements.result.hidden).toBe(true);
    expect(viewer.shown).toHaveLength(0);
    expect(app.lastBuild()).toBeNull();
  });

  it("maps each contract failure classification to its own state", async () => {
    const cases: ReadonlyArray<readonly [string, string]> = [
      ["invalid_document", "validation-error"],
      ["malformed_document", "validation-error"],
      ["invalid_request", "validation-error"],
      ["geometry_failed", "build-error"],
      ["output_failed", "output-error"],
      ["execution_failed", "execution-error"],
      ["internal_error", "execution-error"],
    ];
    for (const [failure, expected] of cases) {
      const { app } = harness({
        "/build": {
          status: "failed",
          succeeded: false,
          document_hash: null,
          build_key: null,
          execution_id: null,
          cache_hit: false,
          outputs: [],
          artifacts: [],
          error: {
            failure,
            message: "the build did not succeed",
            output: null,
            rule_codes: [],
            validation_errors: [],
          },
        },
      });
      app.loadExample();
      await app.build();
      expect(app.state(), failure).toBe(expected);
    }
  });
});

describe("a network error is displayed", () => {
  it("reports an unreachable API without crashing", async () => {
    const { elements, app } = harness({
      "/build": new TypeError("Failed to fetch"),
    });
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("network-error");
    expect(elements.status.dataset.state).toBe("network-error");
    expect(elements.status.textContent).toBe("the API could not be reached");
    expect(elements.status.textContent).not.toContain("Failed to fetch");
    expect(elements.buildButton.disabled).toBe(false);
  });

  it("reports a render request that fails after a successful build", async () => {
    const { elements, app } = harness({
      "/build": successBuild(),
      "/render": new TypeError("Failed to fetch"),
    });
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("network-error");
    // The build itself did land, so its result stays on the page.
    expect(elements.result.hidden).toBe(false);
  });

  it("reports a render payload this viewer cannot draw as an output error", async () => {
    const model = { ...sectionDRender(), format_version: "9.9.9" };
    const { app } = harness({ "/build": successBuild(), "/render": model });
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("output-error");
    expect(app.lastModel()).toBeNull();
  });
});

describe("the build identity is shown", () => {
  it("shows the build key exactly as the backend returned it", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    expect(resultRows(elements)["Build key"]).toBe(BUILD_KEY);
  });

  it("shows the document hash exactly as the backend returned it", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    expect(resultRows(elements)["Document hash"]).toBe(DOCUMENT_HASH);
  });
});

describe("the measurements shown are the backend's own", () => {
  it("shows the dimensions from the geometry artifact's bounding box", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    expect(resultRows(elements).Dimensions).toBe("100 x 60 x 10 mm");
  });

  it("shows the volume the kernel measured, unmodified", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();

    const measurements = successBuild().artifacts.find(
      (item) => item.kind === "geometry",
    )?.measurements as { volume_mm3: number };
    expect(resultRows(elements).Volume).toBe(`${measurements.volume_mm3} mm3`);
    // The plate is one solid with four holes through it.
    expect(resultRows(elements).Solids).toBe("1");
    expect(resultRows(elements).Faces).toBe("10");
  });

  it("shows the triangle count from the render artifact", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    expect(resultRows(elements).Triangles).toBe("2044");
    expect(elements.geometrySummary.textContent).toContain("2044 triangles");
  });

  it("shows no dimension row when the backend sent no geometry measurements", async () => {
    const response = successBuild();
    const artifacts = response.artifacts.filter(
      (item) => item.kind !== "geometry",
    );
    const { elements, app } = harness(
      successRoutes({ ...response, artifacts }),
    );
    app.loadExample();
    await app.build();

    const rows = resultRows(elements);
    expect(rows.Dimensions).toBeUndefined();
    expect(rows.Volume).toBeUndefined();
    expect(rows["Build key"]).toBe(BUILD_KEY);
  });
});

describe("export buttons follow the artifacts the build published", () => {
  it("appears only for outputs the build actually produced", async () => {
    const response = successBuild();
    const artifacts = response.artifacts.filter((item) => item.kind !== "iges");
    const { elements, app } = harness(
      successRoutes({ ...response, artifacts, outputs: ["geometry", "step", "stl", "render"] }),
    );
    app.loadExample();
    await app.build();

    expect(elements.exportButtons.step.hidden).toBe(false);
    expect(elements.exportButtons.stl.hidden).toBe(false);
    expect(elements.exportButtons.iges.hidden).toBe(true);
    expect(elements.exportButtons.iges.dataset.logicalId).toBeUndefined();
  });

  it("hides every export button again when a later build fails", async () => {
    const elements = installPage();
    const viewer = recordingViewer();
    let response: unknown = successBuild();
    const stub = stubFetch({
      routes: { "/build": () => response, "/render": sectionDRender() },
    });
    const client = new ApiClient({ base: BASE, fetch: stub.fetch });
    const app = createApp({ elements, client, viewer: () => viewer });

    app.loadExample();
    await app.build();
    expect(elements.exportButtons.step.hidden).toBe(false);

    response = geometryFailure();
    await app.build();
    expect(elements.exportButtons.step.hidden).toBe(true);
    expect(elements.exportButtons.iges.hidden).toBe(true);
    expect(elements.exportButtons.stl.hidden).toBe(true);
  });

  for (const kind of ["step", "iges", "stl"] as const) {
    it(`downloads ${kind.toUpperCase()} by artifact id`, async () => {
      const { elements, app, client } = harness(successRoutes());
      const downloads: string[] = [];
      wireExports(elements, client, (url) => downloads.push(url));

      app.loadExample();
      await app.build();
      elements.exportButtons[kind].click();

      const record = successBuild().artifacts.find(
        (item) => item.kind === kind,
      );
      expect(record?.logical_id).toBe(`${BUILD_KEY}:${kind}`);
      expect(elements.exportButtons[kind].dataset.logicalId).toBe(
        record?.logical_id,
      );
      expect(downloads).toEqual([
        `${BASE}/artifacts/${encodeURIComponent(record!.logical_id)}`,
      ]);
      // The URL carries an identity, not a location.
      expect(downloads[0]).toContain(BUILD_KEY);
      expect(downloads[0]).not.toMatch(/\.(step|stp|igs|iges|stl)/);
    });
  }

  it("does nothing when an export button carries no artifact id", () => {
    const { elements, client } = harness({});
    const downloads: string[] = [];
    wireExports(elements, client, (url) => downloads.push(url));
    elements.exportButtons.step.click();
    expect(downloads).toEqual([]);
  });
});

describe("nothing internal reaches the page", () => {
  const FORBIDDEN = [
    "/tmp",
    "/home/",
    "/var/",
    "C:\\",
    "Traceback",
    "traceback",
    "most recent call last",
    "File \"",
    ".py",
    "cadquery",
    "CadQuery",
    "OCP",
    "TopoDS",
    "Workplane",
    "staging",
    "manifest.json",
    "exit code",
    "PYTHONPATH",
    "CAD_API_CACHE_ROOT",
  ];

  it("shows no filesystem path or internal detail after a success", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    const text = visibleText(elements);
    for (const token of FORBIDDEN) {
      expect(text, token).not.toContain(token);
    }
  });

  it("shows no traceback or path after a geometry failure", async () => {
    const { elements, app } = harness({ "/build": geometryFailure() });
    app.loadExample();
    await app.build();
    const text = visibleText(elements);
    for (const token of FORBIDDEN) {
      expect(text, token).not.toContain(token);
    }
  });

  it("shows no traceback or path after a validation failure", async () => {
    const { elements, app } = harness({ "/validate": validationFailure() });
    app.loadExample();
    await app.validate();
    const text = visibleText(elements);
    for (const token of FORBIDDEN) {
      expect(text, token).not.toContain(token);
    }
  });

  it("does not print the underlying transport exception on a network error", async () => {
    const { elements, app } = harness({
      "/build": new TypeError("connect ECONNREFUSED 127.0.0.1:8000"),
    });
    app.loadExample();
    await app.build();
    const text = visibleText(elements);
    expect(text).not.toContain("ECONNREFUSED");
    expect(text).not.toContain("127.0.0.1");
    for (const token of FORBIDDEN) {
      expect(text, token).not.toContain(token);
    }
  });

  it("never shows the cache root or any artifact path from the response", async () => {
    const { elements, app } = harness(successRoutes());
    app.loadExample();
    await app.build();
    // The transport contract carries no path at all; assert both ends.
    const serialized = JSON.stringify(successBuild());
    expect(serialized).not.toContain("\"path\"");
    expect(visibleText(elements)).not.toContain("path");
  });
});

describe("the page stays usable while a build runs", () => {
  it("disables and re-enables the buttons around a build", async () => {
    const elements = installPage();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const stub = stubFetch({
      routes: {
        "/build": () => successBuild(),
        "/render": () => sectionDRender(),
      },
    });
    const slowFetch: typeof globalThis.fetch = async (input, init) => {
      await gate;
      return stub.fetch(input, init);
    };
    const client = new ApiClient({ base: BASE, fetch: slowFetch });
    const app = createApp({
      elements,
      client,
      viewer: () => recordingViewer(),
    });
    app.loadExample();

    const running = app.build();
    expect(app.state()).toBe("building");
    expect(elements.buildButton.disabled).toBe(true);
    expect(elements.status.dataset.state).toBe("building");

    release();
    await running;
    expect(elements.buildButton.disabled).toBe(false);
    expect(app.state()).toBe("success");
  });

  it("ignores a second build while one is in flight", async () => {
    const elements = installPage();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const stub = stubFetch({
      routes: { "/build": () => successBuild(), "/render": () => sectionDRender() },
    });
    const slowFetch: typeof globalThis.fetch = async (input, init) => {
      await gate;
      return stub.fetch(input, init);
    };
    const client = new ApiClient({ base: BASE, fetch: slowFetch });
    const app = createApp({ elements, client, viewer: () => recordingViewer() });
    app.loadExample();

    const first = app.build();
    await app.build();
    release();
    await first;
    expect(stub.calls.filter((call) => call.url.endsWith("/build"))).toHaveLength(
      1,
    );
  });
});

describe("the viewer is optional", () => {
  it("still shows the result and the summary with no viewport at all", async () => {
    const elements = installPage();
    const stub = stubFetch({ routes: successRoutes() });
    const client = new ApiClient({ base: BASE, fetch: stub.fetch });
    const app = createApp({ elements, client, viewer: () => null });
    app.loadExample();
    await app.build();

    expect(app.state()).toBe("success");
    expect(elements.result.hidden).toBe(false);
    expect(elements.geometrySummary.textContent).toContain("2048 vertices");
    // Nothing to fit without a viewport.
    expect(elements.fitButton.disabled).toBe(true);
  });
});
