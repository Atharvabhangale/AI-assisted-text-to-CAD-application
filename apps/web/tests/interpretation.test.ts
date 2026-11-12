/**
 * What the interpreter did on the way to an answer: its assumptions, and how
 * many model calls it took.
 *
 * `assumptions` and `attempts` are two fields `POST /generate` adds to its
 * answer. **They are not in any captured fixture**: the backend that produces
 * them was being written alongside this page, so the values below are
 * authored to the agreed contract and layered over the captured answers. They
 * are evidence that the page handles the *shape*; they say nothing about what
 * a model really assumes or how often it is re-asked. Replace them with
 * captured answers once the backend can be recorded.
 *
 * What is asserted throughout: the page shows the backend's strings verbatim,
 * hides nothing, and never asks again on its own -- one click is exactly one
 * `POST /generate`, because every one of them is a paid model call.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { ApiClient } from "../src/api";
import type { GenerateResponse, GenerationAttempt } from "../src/api";
import {
  attemptNote,
  createApp,
  describeAttempt,
  wireExamples,
  type App,
  type Elements,
} from "../src/app";
import { describeIntent } from "../src/design-intent";
import {
  clarificationNeeded,
  generatedPlate,
  generatedPlateBuild,
  generatedPlateRender,
  installPage,
  invalidModelOutput,
  recordingViewer,
  stubFetch,
  type RecordingViewer,
  type Stub,
} from "./harness";

let elements: Elements;
let viewer: RecordingViewer;

beforeEach(() => {
  elements = installPage();
  viewer = recordingViewer();
});

/** An app that answers `/generate` with one given response. */
function appAnswering(answer: unknown): { app: App; stub: Stub } {
  const stub = stubFetch({
    routes: {
      "/generate": answer,
      "/builds/": generatedPlateRender(),
      "/build": generatedPlateBuild(),
    },
  });
  const app = createApp({
    elements,
    client: new ApiClient({ fetch: stub.fetch }),
    viewer: () => viewer,
  });
  elements.descriptionInput.value = "Gaming mouse";
  return { app, stub };
}

function generateCalls(stub: Stub): number {
  return stub.calls.filter((call) => call.url.includes("/generate")).length;
}

function listItems(list: HTMLElement): string[] {
  return [...list.children].map((item) => item.textContent ?? "");
}

const ASSUMPTIONS = [
  "Units were not stated; millimetres were assumed.",
  "Overall size was not stated; 120 x 65 x 38 mm was chosen as typical.",
];

const REJECTED_GEOMETRY: GenerationAttempt = {
  number: 1,
  accepted: false,
  stage: "geometry",
  rule_codes: ["E5"],
  issues: ["the fillet radius 12 is too large for the edges it selects"],
};

const REJECTED_VALIDATION: GenerationAttempt = {
  number: 1,
  accepted: false,
  stage: "validation",
  rule_codes: ["S9"],
  issues: ["the part ends with 2 solids; it must end with exactly one"],
};

const REJECTED_ANSWER: GenerationAttempt = {
  number: 1,
  accepted: false,
  stage: "answer",
  rule_codes: [],
  issues: ["the answer carried no CAD document"],
};

function accepted(number: number): GenerationAttempt {
  return { number, accepted: true, stage: null, rule_codes: [], issues: [] };
}

function numbered(
  attempt: GenerationAttempt,
  number: number,
): GenerationAttempt {
  return { ...attempt, number };
}

/** The captured plate answer, with the two new fields layered on. */
function plateWith(extra: Partial<GenerateResponse>): GenerateResponse {
  return { ...generatedPlate(), ...extra };
}

/** The captured failure, with attempts layered on. */
function failureWith(extra: Partial<GenerateResponse>): GenerateResponse {
  return { ...invalidModelOutput(), ...extra };
}

describe("an older backend's answer, with neither field, still works", () => {
  it("builds a captured answer that has no assumptions and no attempts", async () => {
    const answer = generatedPlate();
    expect(answer).not.toHaveProperty("assumptions");
    expect(answer).not.toHaveProperty("attempts");
    const { app } = appAnswering(answer);
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.assumptions.hidden).toBe(true);
    expect(elements.attemptNote.hidden).toBe(true);
    expect(elements.attemptDetails.hidden).toBe(true);
  });

  it("reports a captured failure that has no attempts as it always did", async () => {
    const { app } = appAnswering(invalidModelOutput());
    await app.generate();
    expect(app.state()).toBe("model-error");
    expect(elements.status.textContent).toBe(invalidModelOutput().message);
    expect(listItems(elements.errors)).toEqual(invalidModelOutput().issues);
    expect(elements.attemptNote.hidden).toBe(true);
    expect(elements.attemptDetails.hidden).toBe(true);
  });

  it("reads a null or malformed field as an empty list", async () => {
    for (const odd of [null, "none", 3, {}]) {
      elements = installPage();
      const { app } = appAnswering({
        ...generatedPlate(),
        assumptions: odd,
        attempts: odd,
      });
      await app.generate();
      expect(app.state(), JSON.stringify(odd)).toBe("success");
      expect(elements.assumptions.hidden).toBe(true);
      expect(elements.attemptNote.hidden).toBe(true);
    }
  });

  it("tolerates an attempt that omits its lists", async () => {
    const bare = { number: 1, accepted: false, stage: "validation" };
    const { app } = appAnswering(
      plateWith({ attempts: [bare as GenerationAttempt, accepted(2)] }),
    );
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.attemptNote.textContent).toBe(
      "Accepted on attempt 2 of 2 — attempt 1 was rejected by the " +
        "document validator.",
    );
  });
});

describe("assumptions are shown, never silent", () => {
  it("labels the list in the design-intent area", () => {
    expect(elements.assumptions.textContent).toContain("Assumptions made");
    expect(
      elements.assumptions.compareDocumentPosition(elements.designIntent) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(elements.assumptions.parentElement).toBe(
      elements.designIntent.parentElement,
    );
  });

  it("lists every assumption verbatim, in the backend's order", async () => {
    const { app } = appAnswering(plateWith({ assumptions: ASSUMPTIONS }));
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.assumptions.hidden).toBe(false);
    expect(listItems(elements.assumptionList)).toEqual(ASSUMPTIONS);
  });

  it("stays hidden when the backend made none", async () => {
    const { app } = appAnswering(plateWith({ assumptions: [] }));
    await app.generate();
    expect(elements.assumptions.hidden).toBe(true);
    expect(elements.assumptionList.children).toHaveLength(0);
  });

  it("shows the strings as text, not as markup", async () => {
    const { app } = appAnswering(
      plateWith({ assumptions: ["<b>bold</b> <img src=x>"] }),
    );
    await app.generate();
    expect(elements.assumptionList.querySelector("b, img")).toBeNull();
    expect(listItems(elements.assumptionList)).toEqual([
      "<b>bold</b> <img src=x>",
    ]);
  });

  it("does not change the document that is built", async () => {
    const { app, stub } = appAnswering(plateWith({ assumptions: ASSUMPTIONS }));
    await app.generate();
    const build = stub.calls.find((call) => call.url.endsWith("/build"));
    expect((build?.body as { document: unknown }).document).toEqual(
      generatedPlate().document,
    );
  });

  it("is cleared by the next generation", async () => {
    const first = appAnswering(plateWith({ assumptions: ASSUMPTIONS }));
    await first.app.generate();
    expect(elements.assumptions.hidden).toBe(false);
    const second = appAnswering(generatedPlate());
    await second.app.generate();
    expect(elements.assumptions.hidden).toBe(true);
    expect(elements.assumptionList.children).toHaveLength(0);
  });
});

describe("a part that took more than one attempt says so", () => {
  it("says nothing when the first answer was accepted", async () => {
    const { app } = appAnswering(plateWith({ attempts: [accepted(1)] }));
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.attemptNote.hidden).toBe(true);
    expect(elements.attemptDetails.hidden).toBe(true);
  });

  it("names the accepted attempt and what the earlier one was rejected for", async () => {
    const { app } = appAnswering(
      plateWith({ attempts: [REJECTED_GEOMETRY, accepted(2)] }),
    );
    await app.generate();
    expect(app.state()).toBe("success");
    expect(elements.attemptNote.hidden).toBe(false);
    expect(elements.attemptNote.textContent).toBe(
      "Accepted on attempt 2 of 2 — attempt 1 was rejected by the " +
        "geometry check (E5).",
    );
  });

  it("keeps the rejected attempt's reasons one click away", async () => {
    const { app } = appAnswering(
      plateWith({ attempts: [REJECTED_GEOMETRY, accepted(2)] }),
    );
    await app.generate();
    expect(elements.attemptDetails.hidden).toBe(false);
    expect(elements.attemptDetails.open).toBe(false);
    expect(elements.attemptDetails.querySelector("summary")).not.toBeNull();
    const entries = [...elements.attemptList.children];
    expect(entries).toHaveLength(2);
    expect(entries[0].textContent).toContain(
      "Attempt 1 — rejected by the geometry check (E5)",
    );
    expect(entries[0].textContent).toContain(REJECTED_GEOMETRY.issues[0]);
    expect(entries[1].textContent).toBe("Attempt 2 — accepted");
    // The part was accepted, so the rejected reasons are not errors.
    expect(elements.errors.hidden).toBe(true);
  });

  it("counts the rejected answers when there were several", async () => {
    const { app } = appAnswering(
      plateWith({
        attempts: [
          REJECTED_VALIDATION,
          numbered(REJECTED_GEOMETRY, 2),
          accepted(3),
        ],
      }),
    );
    await app.generate();
    expect(elements.attemptNote.textContent).toBe(
      "Accepted on attempt 3 of 3 — 2 earlier answers were rejected.",
    );
    const text = elements.attemptList.textContent ?? "";
    expect(text).toContain("rejected by the document validator (S9)");
    expect(text).toContain(REJECTED_VALIDATION.issues[0]);
    expect(text).toContain("rejected by the geometry check (E5)");
    expect(text).toContain(REJECTED_GEOMETRY.issues[0]);
  });

  it("still builds the accepted document, once, unchanged", async () => {
    const { app, stub } = appAnswering(
      plateWith({ attempts: [REJECTED_GEOMETRY, accepted(2)] }),
    );
    await app.generate();
    const builds = stub.calls.filter((call) => call.url.endsWith("/build"));
    expect(builds).toHaveLength(1);
    expect((builds[0].body as { document: unknown }).document).toEqual(
      generatedPlate().document,
    );
    expect(viewer.shown).toHaveLength(1);
  });

  it("is cleared by the next generation", async () => {
    const first = appAnswering(
      plateWith({ attempts: [REJECTED_GEOMETRY, accepted(2)] }),
    );
    await first.app.generate();
    expect(elements.attemptNote.hidden).toBe(false);
    const second = appAnswering(generatedPlate());
    await second.app.generate();
    expect(elements.attemptNote.hidden).toBe(true);
    expect(elements.attemptNote.textContent).toBe("");
    expect(elements.attemptList.children).toHaveLength(0);
  });
});

describe("a failure after every attempt shows what each was rejected for", () => {
  const ATTEMPTS = [
    REJECTED_ANSWER,
    numbered(REJECTED_VALIDATION, 2),
    numbered(REJECTED_GEOMETRY, 3),
  ];

  it("lists each attempt, its stage, its rule codes and its reasons", async () => {
    const { app } = appAnswering(failureWith({ attempts: ATTEMPTS }));
    await app.generate();
    expect(app.state()).toBe("model-error");
    expect(elements.status.textContent).toBe(invalidModelOutput().message);
    expect(elements.attemptNote.textContent).toBe(
      "None of the 3 attempts was accepted.",
    );
    expect(elements.attemptDetails.hidden).toBe(false);
    expect(elements.attemptDetails.open).toBe(true);
    const entries = [...elements.attemptList.children].map(
      (entry) => entry.textContent ?? "",
    );
    expect(entries).toHaveLength(3);
    expect(entries[0]).toContain("Attempt 1 — rejected as an unusable answer");
    expect(entries[0]).toContain(REJECTED_ANSWER.issues[0]);
    expect(entries[1]).toContain(
      "Attempt 2 — rejected by the document validator (S9)",
    );
    expect(entries[1]).toContain(REJECTED_VALIDATION.issues[0]);
    expect(entries[2]).toContain(
      "Attempt 3 — rejected by the geometry check (E5)",
    );
    expect(entries[2]).toContain(REJECTED_GEOMETRY.issues[0]);
  });

  it("builds nothing and draws nothing", async () => {
    const { app, stub } = appAnswering(failureWith({ attempts: ATTEMPTS }));
    await app.generate();
    expect(stub.calls.some((call) => call.url.endsWith("/build"))).toBe(false);
    expect(viewer.shown).toHaveLength(0);
    expect(app.lastBuild()).toBeNull();
  });

  it("words a single failed attempt in the singular", async () => {
    const { app } = appAnswering(
      failureWith({ attempts: [REJECTED_VALIDATION] }),
    );
    await app.generate();
    expect(elements.attemptNote.textContent).toBe(
      "The one attempt made was not accepted.",
    );
    expect(elements.attemptDetails.open).toBe(true);
    expect(elements.attemptList.textContent).toContain(
      REJECTED_VALIDATION.issues[0],
    );
  });

  it("does not repeat a reason already listed under its attempt", async () => {
    const repeated = REJECTED_GEOMETRY.issues[0];
    const { app } = appAnswering(
      failureWith({ attempts: ATTEMPTS, issues: [repeated] }),
    );
    await app.generate();
    expect(elements.errors.hidden).toBe(true);
    expect(elements.attemptList.textContent).toContain(repeated);
  });

  it("still shows a reason no attempt carries", async () => {
    const extra = "the interpreter gave up after three attempts";
    const { app } = appAnswering(
      failureWith({
        attempts: ATTEMPTS,
        issues: [REJECTED_GEOMETRY.issues[0], extra],
      }),
    );
    await app.generate();
    expect(elements.errors.hidden).toBe(false);
    expect(listItems(elements.errors)).toEqual([extra]);
  });

  it("shows no attempt list when the model was never reached", async () => {
    const { app } = appAnswering({
      outcome: "model_error",
      message: "the interpretation service is unavailable",
      document: null,
      document_hash: null,
      summary: null,
      questions: [],
      issues: [],
      rule_codes: [],
      error_kind: "not_configured",
      assumptions: [],
      attempts: [],
    });
    await app.generate();
    expect(app.state()).toBe("model-error");
    expect(elements.attemptNote.hidden).toBe(true);
    expect(elements.attemptDetails.hidden).toBe(true);
  });
});

describe("a question that came after a rejected answer keeps both visible", () => {
  const asked: GenerationAttempt = {
    number: 2,
    accepted: false,
    stage: null,
    rule_codes: [],
    issues: [],
  };

  it("says how many attempts the answer took", async () => {
    const { app } = appAnswering({
      ...clarificationNeeded(),
      attempts: [REJECTED_VALIDATION, asked],
    });
    await app.generate();
    expect(app.state()).toBe("needs-clarification");
    expect(elements.clarify.hidden).toBe(false);
    expect(elements.attemptNote.textContent).toBe(
      "Answered after 2 attempts — attempt 1 was rejected by the " +
        "document validator (S9).",
    );
    expect(elements.attemptDetails.open).toBe(false);
    expect(elements.attemptList.children[1].textContent).toBe(
      "Attempt 2 — answered without a CAD document",
    );
  });

  it("keeps every reason in plain view while the list is collapsed", async () => {
    const reason = REJECTED_VALIDATION.issues[0];
    const { app } = appAnswering({
      ...clarificationNeeded(),
      issues: [reason],
      attempts: [REJECTED_VALIDATION, asked],
    });
    await app.generate();
    expect(elements.attemptDetails.open).toBe(false);
    expect(listItems(elements.errors)).toEqual([reason]);
  });
});

describe("the page never asks again on its own", () => {
  it("makes exactly one /generate request per click, whatever comes back", async () => {
    const answers: unknown[] = [
      generatedPlate(),
      plateWith({ attempts: [REJECTED_GEOMETRY, accepted(2)] }),
      failureWith({ attempts: [REJECTED_ANSWER] }),
      failureWith({
        attempts: [
          REJECTED_ANSWER,
          numbered(REJECTED_VALIDATION, 2),
          numbered(REJECTED_GEOMETRY, 3),
        ],
      }),
      invalidModelOutput(),
      new Error("offline"),
    ];
    for (const answer of answers) {
      elements = installPage();
      const { app, stub } = appAnswering(answer);
      await app.generate();
      // Long enough for any timer-driven second request to have fired.
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(generateCalls(stub)).toBe(1);
    }
  });
});

describe("the wording of one attempt", () => {
  it("uses the backend's stage and rule codes", () => {
    expect(describeAttempt(accepted(1))).toBe("accepted");
    expect(describeAttempt(REJECTED_GEOMETRY)).toBe(
      "rejected by the geometry check (E5)",
    );
    expect(describeAttempt(REJECTED_VALIDATION)).toBe(
      "rejected by the document validator (S9)",
    );
    expect(describeAttempt(REJECTED_ANSWER)).toBe(
      "rejected as an unusable answer",
    );
    expect(
      describeAttempt({ ...REJECTED_VALIDATION, rule_codes: ["S6", "S9"] }),
    ).toBe("rejected by the document validator (S6, S9)");
  });

  it("does not guess at a stage it has never heard of", () => {
    const unknown = {
      ...REJECTED_GEOMETRY,
      stage: "export",
    } as unknown as GenerationAttempt;
    expect(describeAttempt(unknown)).toBe("rejected (E5)");
  });

  it("has no note for an answer with nothing rejected", () => {
    expect(attemptNote(generatedPlate())).toBeNull();
    expect(attemptNote(plateWith({ attempts: [] }))).toBeNull();
    expect(attemptNote(plateWith({ attempts: [accepted(1)] }))).toBeNull();
  });
});

describe("the design intent reads a union", () => {
  const BRACKET = {
    schema_version: "1.0.0",
    units: "mm",
    name: "l-bracket",
    features: [
      { id: "base", type: "box", size: { x: 60, y: 30, z: 5 } },
      {
        id: "upright",
        type: "box",
        size: { x: 5, y: 30, z: 40 },
        position: { x: 0, y: 0, z: 0 },
      },
      { id: "bracket", type: "union", target: "base", tools: ["upright"] },
    ],
  };

  it("describes it as joining the tools onto the target", () => {
    const intent = describeIntent(BRACKET);
    const union = intent!.features[2];
    expect(union.heading).toBe("Union — bracket");
    expect(union.rows).toEqual([
      { label: "Join", value: "upright" },
      { label: "Onto", value: "base" },
    ]);
  });

  it("names every tool, in the document's order", () => {
    const intent = describeIntent({
      units: "mm",
      features: [
        {
          id: "body",
          type: "union",
          target: "cup",
          tools: ["handle", "foot", "lip"],
        },
      ],
    });
    expect(intent!.features[0].rows).toEqual([
      { label: "Join", value: "handle, foot, lip" },
      { label: "Onto", value: "cup" },
    ]);
  });

  it("omits what the document does not carry", () => {
    const intent = describeIntent({
      units: "mm",
      features: [{ id: "body", type: "union", tools: [] }],
    });
    expect(intent!.features[0].heading).toBe("Union — body");
    expect(intent!.features[0].rows).toEqual([]);
  });

  it("appears in the panel, in document order, and is built unchanged", async () => {
    const { app, stub } = appAnswering(plateWith({ document: BRACKET }));
    await app.generate();
    const headings = [...elements.designIntent.querySelectorAll("h3")].map(
      (heading) => heading.textContent,
    );
    expect(headings).toEqual([
      "Box — base",
      "Box — upright",
      "Union — bracket",
    ]);
    const text = elements.designIntent.textContent ?? "";
    expect(text).toContain("Join");
    expect(text).toContain("Onto");
    const build = stub.calls.find((call) => call.url.endsWith("/build"));
    expect((build?.body as { document: unknown }).document).toEqual(BRACKET);
  });
});

describe("the example chips fill the description and submit nothing", () => {
  function chips(): HTMLButtonElement[] {
    return [
      ...elements.examples.querySelectorAll<HTMLButtonElement>(
        "button[data-example]",
      ),
    ];
  }

  function wired(): { app: App; stub: Stub } {
    const harness = appAnswering(generatedPlate());
    elements.descriptionInput.value = "";
    wireExamples(elements);
    return harness;
  }

  it("offers a handful of examples, each a plain button", () => {
    const all = chips();
    expect(all.length).toBeGreaterThanOrEqual(4);
    for (const chip of all) {
      expect(chip.type).toBe("button");
      expect((chip.textContent ?? "").trim()).not.toBe("");
    }
    const labels = all.map((chip) => chip.textContent ?? "");
    expect(labels).toContain("Gaming mouse");
    expect(labels.some((label) => label.startsWith("L-bracket"))).toBe(true);
    expect(labels.some((label) => label.includes("with a handle"))).toBe(true);
  });

  it("sits under the description box and above the Generate button", () => {
    const after = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(
      elements.descriptionInput.compareDocumentPosition(elements.examples) &
        after,
    ).toBeTruthy();
    expect(
      elements.examples.compareDocumentPosition(elements.generateButton) &
        after,
    ).toBeTruthy();
  });

  it("tells the user that an example does not generate", () => {
    const help = document.getElementById(
      elements.examples.getAttribute("aria-describedby") ?? "",
    );
    expect(help?.textContent).toContain("only fills in the box");
    expect(help?.textContent).toContain("Generate CAD");
  });

  it("puts the chip's own text in the description box", () => {
    wired();
    for (const chip of chips()) {
      chip.click();
      expect(elements.descriptionInput.value).toBe(chip.textContent);
    }
  });

  it("replaces what was there instead of appending to it", () => {
    wired();
    elements.descriptionInput.value = "something typed earlier";
    const [first, second] = chips();
    first.click();
    second.click();
    expect(elements.descriptionInput.value).toBe(second.textContent);
  });

  it("makes no request of any kind, and leaves the page idle", async () => {
    const { app, stub } = wired();
    for (const chip of chips()) {
      chip.click();
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stub.calls).toHaveLength(0);
    expect(app.state()).toBe("idle");
    expect(viewer.shown).toHaveLength(0);
    expect(elements.designIntent.hidden).toBe(true);
  });

  it("ignores a click that lands between the chips", async () => {
    const { stub } = wired();
    elements.descriptionInput.value = "kept";
    elements.examples.click();
    expect(elements.descriptionInput.value).toBe("kept");
    expect(stub.calls).toHaveLength(0);
  });

  it("generates only when Generate CAD is used, and then exactly once", async () => {
    const { app, stub } = wired();
    const chip = chips()[0];
    chip.click();
    expect(stub.calls).toHaveLength(0);
    await app.generate();
    const generate = stub.calls.filter((call) =>
      call.url.includes("/generate"),
    );
    expect(generate).toHaveLength(1);
    expect(generate[0].body).toEqual({ text: chip.textContent });
  });
});
