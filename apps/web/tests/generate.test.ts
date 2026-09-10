/**
 * The text-to-CAD flow: type a sentence, press one button, see the model.
 *
 * Every `/generate` response here was **captured from the real provider** and
 * is replayed through a stub transport, so these tests assert how the page
 * behaves for each shape the backend really produces. They spend no quota and
 * need no credential.
 *
 * What is asserted throughout: the page **interprets nothing**. It sends the
 * description verbatim, shows the document the backend validated, and hands
 * that same document back to be built without editing it.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { ApiClient } from "../src/api";
import { createApp, type App, type Elements } from "../src/app";
import { describeIntent } from "../src/design-intent";
import {
  clarificationNeeded,
  generatedCylinder,
  generatedPlate,
  generatedPlateBuild,
  generatedPlateRender,
  installPage,
  invalidModelOutput,
  recordingViewer,
  resultRows,
  stubFetch,
  unsupportedRequest,
  type RecordedCall,
  type RecordingViewer,
  type Stub,
} from "./harness";

const PLATE_TEXT =
  "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm thick.";

let elements: Elements;
let viewer: RecordingViewer;

beforeEach(() => {
  elements = installPage();
  viewer = recordingViewer();
});

/** An app wired to a stub transport, with the description already typed. */
function appWith(
  routes: Record<string, unknown>,
  text: string = PLATE_TEXT,
): { app: App; stub: Stub } {
  const stub = stubFetch({ routes });
  const app = createApp({
    elements,
    client: new ApiClient({ fetch: stub.fetch }),
    viewer: () => viewer,
  });
  elements.descriptionInput.value = text;
  return { app, stub };
}

/** The whole happy path: generate, then build, then draw. */
function happyPath(): { app: App; stub: Stub } {
  return appWith({
    "/generate": generatedPlate(),
    "/builds/": generatedPlateRender(),
    "/build": generatedPlateBuild(),
  });
}

describe("the page offers text input as its primary control", () => {
  it("renders the description box and the Generate CAD button", () => {
    expect(elements.descriptionInput).toBeTruthy();
    expect(elements.generateButton).toBeTruthy();
    expect(elements.generateButton.textContent).toContain("Generate CAD");
    expect(elements.descriptionInput.placeholder).toContain("plate");
  });

  it("keeps the advanced CAD JSON mode available", () => {
    expect(elements.documentInput).toBeTruthy();
    expect(elements.buildButton).toBeTruthy();
    expect(elements.validateButton).toBeTruthy();
  });

  it("starts idle", () => {
    const { app } = happyPath();
    expect(app.state()).toBe("idle");
  });
});

describe("an empty description is refused before any request", () => {
  it("makes no call and stays idle", async () => {
    for (const text of ["", "   ", "\n\t "]) {
      const { app, stub } = appWith({ "/generate": generatedPlate() }, text);
      await app.generate();
      expect(stub.calls, `text=${JSON.stringify(text)}`).toHaveLength(0);
      expect(app.state()).toBe("idle");
      expect(elements.status.textContent).toContain("Describe the part");
    }
  });
});

describe("the successful flow is one action", () => {
  it("sends the description verbatim to /generate", async () => {
    const { app, stub } = happyPath();
    await app.generate();
    const generate = stub.calls.find((call) => call.url.includes("/generate"));
    expect(generate).toBeDefined();
    expect(generate?.method).toBe("POST");
    expect(generate?.body).toEqual({ text: PLATE_TEXT });
  });

  it("builds automatically, with no second click", async () => {
    const { app, stub } = happyPath();
    await app.generate();
    const paths = stub.calls.map((call) => call.url);
    expect(paths.some((path) => path.includes("/generate"))).toBe(true);
    expect(paths.some((path) => path.endsWith("/build"))).toBe(true);
    expect(paths.some((path) => path.includes("/render"))).toBe(true);
    expect(app.state()).toBe("success");
  });

  it("sends the generated document to /build unchanged", async () => {
    const { app, stub } = happyPath();
    await app.generate();
    const build = stub.calls.find((call) => call.url.endsWith("/build"));
    const sent = (build?.body as { document: unknown }).document;
    // Byte-for-byte what the backend returned: not repaired, not completed,
    // not reordered, not re-defaulted by the page.
    expect(sent).toEqual(generatedPlate().document);
  });

  it("asks for the outputs the viewer and the export buttons need", async () => {
    const { app, stub } = happyPath();
    await app.generate();
    const build = stub.calls.find((call) => call.url.endsWith("/build"));
    expect((build?.body as { outputs: string[] }).outputs).toEqual([
      "geometry",
      "step",
      "iges",
      "stl",
      "render",
    ]);
  });

  it("shows the render model the backend produced", async () => {
    const { app } = happyPath();
    await app.generate();
    expect(viewer.shown).toHaveLength(1);
    expect(viewer.shown[0]).toEqual(generatedPlateRender());
    expect(app.lastModel()).toEqual(generatedPlateRender());
    expect(elements.fitButton.disabled).toBe(false);
  });

  it("shows the measurements the backend sent", async () => {
    const { app } = happyPath();
    await app.generate();
    const rows = resultRows(elements);
    expect(rows["Status"]).toBe("succeeded");
    expect(rows["Dimensions"]).toBe("100 x 60 x 10 mm");
    expect(rows["Solids"]).toBe("1");
    expect(rows["Volume"]).toBe("60000 mm3");
    expect(app.lastBuild()?.build_key).toBe(generatedPlateBuild().build_key);
  });

  it("enables STEP, IGES and STL with the backend's own logical ids", async () => {
    const { app } = happyPath();
    await app.generate();
    const artifacts = generatedPlateBuild().artifacts;
    for (const kind of ["step", "iges", "stl"] as const) {
      const button = elements.exportButtons[kind];
      const record = artifacts.find((item) => item.kind === kind);
      expect(button.hidden, kind).toBe(false);
      expect(button.dataset.logicalId, kind).toBe(record?.logical_id);
    }
  });

  it("reports a cache hit honestly", async () => {
    const cached = { ...generatedPlateBuild(), cache_hit: true };
    const { app } = appWith({
      "/generate": generatedPlate(),
      "/builds/": generatedPlateRender(),
      "/build": cached,
    });
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.status.textContent).toContain("cache");
    expect(resultRows(elements)["Source"]).toBe("cache");
  });
});

describe("design intent shows what the model decided", () => {
  it("lists the box's dimensions from the generated document", async () => {
    const { app } = happyPath();
    await app.generate();
    expect(elements.designIntent.hidden).toBe(false);
    const text = elements.designIntent.textContent ?? "";
    expect(text).toContain("Box");
    expect(text).toContain("Length (X)");
    expect(text).toContain("100 mm");
    expect(text).toContain("60 mm");
    expect(text).toContain("10 mm");
  });

  it("shows the model's own summary sentence", async () => {
    const { app } = happyPath();
    await app.generate();
    expect(elements.intentSummary.textContent).toBe(generatedPlate().summary);
  });

  it("describes a cylinder by diameter, height and axis", () => {
    const intent = describeIntent(generatedCylinder().document);
    expect(intent).not.toBeNull();
    const rows = intent!.features[0].rows;
    const byLabel = Object.fromEntries(
      rows.map((row) => [row.label, row.value]),
    );
    expect(intent!.features[0].heading).toContain("Cylinder");
    expect(byLabel["Diameter"]).toBe("20 mm");
    expect(byLabel["Height"]).toBe("50 mm");
    expect(byLabel["Axis"]).toBe("+Z");
    expect(byLabel["Base position"]).toBe("0, 0, 0 mm");
  });

  it("reports features in document order, because order is semantic", () => {
    const intent = describeIntent({
      units: "mm",
      features: [
        { id: "plate", type: "box", size: { x: 1, y: 2, z: 3 } },
        {
          id: "hole",
          type: "through_hole",
          target: "plate",
          diameter: 4,
          position: { x: 0, y: 0, z: 0 },
          axis: "+Z",
        },
      ],
    });
    expect(intent!.features.map((feature) => feature.heading)).toEqual([
      "Box — plate",
      "Through hole — hole",
    ]);
  });

  it("omits a field the document does not carry rather than inventing one", () => {
    const intent = describeIntent({
      units: "mm",
      features: [{ id: "b", type: "box", size: { x: 1, y: 2, z: 3 } }],
    });
    const labels = intent!.features[0].rows.map((row) => row.label);
    expect(labels).toEqual(["Length (X)", "Width (Y)", "Height (Z)"]);
    expect(labels).not.toContain("Position");
  });

  it("is cleared when a later generation fails", async () => {
    const { app } = happyPath();
    await app.generate();
    expect(elements.designIntent.hidden).toBe(false);
    const second = appWith({ "/generate": unsupportedRequest() });
    await second.app.generate();
    expect(elements.designIntent.hidden).toBe(true);
    expect(elements.intentSummary.textContent).toBe("");
  });
});

describe("the three non-building answers stop cleanly", () => {
  it("shows a clarification question and builds nothing", async () => {
    const { app, stub } = appWith({ "/generate": clarificationNeeded() });
    await app.generate();
    expect(app.state()).toBe("needs-clarification");
    expect(stub.calls.every((call) => !call.url.endsWith("/build"))).toBe(true);
    expect(elements.clarify.hidden).toBe(false);
    expect(elements.questions.textContent).toContain("unit");
    expect(viewer.shown).toHaveLength(0);
  });

  it("shows an unsupported refusal as a message, not an error dump", async () => {
    const { app, stub } = appWith({ "/generate": unsupportedRequest() });
    await app.generate();
    expect(app.state()).toBe("unsupported");
    expect(stub.calls.every((call) => !call.url.endsWith("/build"))).toBe(true);
    expect(elements.status.textContent).toBe(unsupportedRequest().message);
    expect(elements.errors.textContent).toContain("fillet");
    expect(elements.clarify.hidden).toBe(true);
  });

  it("reports an unusable model answer without building it", async () => {
    const { app, stub } = appWith({ "/generate": invalidModelOutput() });
    await app.generate();
    expect(app.state()).toBe("model-error");
    expect(stub.calls.every((call) => !call.url.endsWith("/build"))).toBe(true);
    expect(elements.status.textContent).toBe(invalidModelOutput().message);
    expect(viewer.shown).toHaveLength(0);
    expect(app.lastBuild()).toBeNull();
  });

  it("reports an unreachable provider as a service problem", async () => {
    const { app } = appWith({
      "/generate": {
        outcome: "model_error",
        message: "the interpretation service is unavailable",
        document: null,
        document_hash: null,
        summary: null,
        questions: [],
        issues: [],
        rule_codes: [],
        error_kind: "not_configured",
      },
    });
    await app.generate();
    expect(app.state()).toBe("model-error");
    expect(elements.exportButtons.step.hidden).toBe(true);
  });

  it("reports a transport failure as a network problem", async () => {
    const { app } = appWith({ "/generate": new Error("offline") });
    await app.generate();
    expect(app.state()).toBe("network-error");
  });
});

describe("the clarification follow-up is one fresh request", () => {
  it("appends the answer and generates again", async () => {
    const stub = stubFetch({
      routes: {
        "/generate": (call: RecordedCall) =>
          ((call.body as { text: string }).text.includes("millimetres")
            ? generatedPlate()
            : clarificationNeeded()),
        "/builds/": generatedPlateRender(),
        "/build": generatedPlateBuild(),
      },
    });
    const app = createApp({
      elements,
      client: new ApiClient({ fetch: stub.fetch }),
      viewer: () => viewer,
    });
    elements.descriptionInput.value = "Create a plate 100 by 60 by 10.";
    await app.generate();
    expect(app.state()).toBe("needs-clarification");

    elements.clarifyInput.value = "millimetres";
    await app.clarify();

    const texts = stub.calls
      .filter((call) => call.url.includes("/generate"))
      .map((call) => (call.body as { text: string }).text);
    expect(texts).toHaveLength(2);
    // A single self-contained description, not a conversation: the second
    // request carries the whole thing, and no history field is sent.
    expect(texts[1]).toBe("Create a plate 100 by 60 by 10. millimetres");
    expect(Object.keys(stub.calls[0].body as object)).toEqual(["text"]);
    expect(app.state()).toBe("success");
    expect(viewer.shown).toHaveLength(1);
  });

  it("does nothing when the answer is blank", async () => {
    const { app, stub } = appWith({ "/generate": clarificationNeeded() });
    await app.generate();
    const before = stub.calls.length;
    elements.clarifyInput.value = "   ";
    await app.clarify();
    expect(stub.calls).toHaveLength(before);
  });
});

describe("the advanced CAD JSON mode still works", () => {
  it("builds a pasted document without generating", async () => {
    const { app, stub } = appWith({
      "/builds/": generatedPlateRender(),
      "/build": generatedPlateBuild(),
    });
    elements.documentInput.value = JSON.stringify(generatedPlate().document);
    await app.build();
    expect(app.state()).toBe("success");
    expect(stub.calls.some((call) => call.url.includes("/generate"))).toBe(
      false,
    );
  });

  it("is filled in by a successful generation, for inspection", async () => {
    const { app } = happyPath();
    await app.generate();
    expect(JSON.parse(elements.documentInput.value)).toEqual(
      generatedPlate().document,
    );
  });

  it("rejects invalid JSON without calling the API", async () => {
    const { app, stub } = appWith({ "/build": generatedPlateBuild() });
    elements.documentInput.value = "{not json";
    await app.build();
    expect(app.state()).toBe("validation-error");
    expect(stub.calls).toHaveLength(0);
  });
});

describe("the browser never sees a credential", () => {
  it("sends only the description, and no auth header", async () => {
    const { app, stub } = happyPath();
    await app.generate();
    for (const call of stub.calls) {
      const body = JSON.stringify(call.body ?? {});
      for (const forbidden of ["api_key", "apiKey", "key", "token", "Bearer"]) {
        expect(body.toLowerCase(), forbidden).not.toContain(
          forbidden.toLowerCase(),
        );
      }
    }
  });

  it("shows no filesystem path or provider diagnostic on any outcome", async () => {
    for (const answer of [
      generatedPlate(),
      clarificationNeeded(),
      unsupportedRequest(),
      invalidModelOutput(),
    ]) {
      elements = installPage();
      const { app } = appWith({
        "/generate": answer,
        "/builds/": generatedPlateRender(),
        "/build": generatedPlateBuild(),
      });
      await app.generate();
      const visible = [
        elements.status.textContent,
        elements.errors.textContent,
        elements.designIntent.textContent,
        elements.intentSummary.textContent,
        elements.questions.textContent,
      ].join("\n");
      for (const forbidden of [
        "Traceback",
        "/tmp",
        "C:\\",
        "site-packages",
        "cadquery",
        "GEMINI",
        "api_key",
      ]) {
        expect(visible, forbidden).not.toContain(forbidden);
      }
    }
  });
});

describe("progress is described, never faked", () => {
  it("says what it is doing, with no percentage", async () => {
    const seen: string[] = [];
    const stub = stubFetch({
      routes: {
        "/generate": () => {
          seen.push(elements.status.textContent ?? "");
          return generatedPlate();
        },
        "/builds/": generatedPlateRender(),
        "/build": () => {
          seen.push(elements.status.textContent ?? "");
          return generatedPlateBuild();
        },
      },
    });
    const app = createApp({
      elements,
      client: new ApiClient({ fetch: stub.fetch }),
      viewer: () => viewer,
    });
    elements.descriptionInput.value = PLATE_TEXT;
    await app.generate();
    expect(seen[0]).toContain("Generating CAD");
    expect(seen[1]).toContain("Building geometry");
    expect(elements.status.textContent).toContain("Ready");
    for (const message of [...seen, elements.status.textContent ?? ""]) {
      expect(message).not.toMatch(/\d+\s*%/);
    }
  });

  it("disables the buttons while working and re-enables them after", async () => {
    let duringGenerate = false;
    const stub = stubFetch({
      routes: {
        "/generate": () => {
          duringGenerate = elements.generateButton.disabled;
          return generatedPlate();
        },
        "/builds/": generatedPlateRender(),
        "/build": generatedPlateBuild(),
      },
    });
    const app = createApp({
      elements,
      client: new ApiClient({ fetch: stub.fetch }),
      viewer: () => viewer,
    });
    elements.descriptionInput.value = PLATE_TEXT;
    await app.generate();
    expect(duringGenerate).toBe(true);
    expect(elements.generateButton.disabled).toBe(false);
  });
});
