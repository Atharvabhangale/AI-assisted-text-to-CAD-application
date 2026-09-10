/**
 * The UI: state, DOM and the build flow. No CAD logic.
 *
 * Every engineering number on the page -- dimensions, solid count, volume,
 * triangle count -- is read from a field the backend sent. Nothing is
 * measured, validated or derived here, and a test asserts this file contains
 * no such arithmetic.
 */

import { ApiClient, NetworkError } from "./api";
import type {
  ArtifactRecord,
  BuildResponse,
  DownloadableKind,
  ErrorRecord,
  GenerateResponse,
  ValidateResponse,
} from "./api";
import { describeIntent, type IntentFeature } from "./design-intent";
import { SECTION_D_JSON } from "./example";
import {
  RenderModelError,
  assertRenderModel,
  describeMesh,
  type RenderModel,
} from "./render-model";

/** The states the status line can be in. Exactly these. */
export type AppState =
  | "idle"
  | "generating"
  | "needs-clarification"
  | "unsupported"
  | "model-error"
  | "building"
  | "success"
  | "validation-error"
  | "build-error"
  | "output-error"
  | "execution-error"
  | "network-error";

/**
 * Which state each AI outcome means.
 *
 * A mapping of the backend's own `outcome` values, so the page distinguishes
 * a clarification from a refusal from a model failure without inventing a
 * taxonomy. `generated` is absent on purpose: it is not a resting state, it
 * is the point at which the page goes on to build.
 */
export const OUTCOME_STATES: Readonly<Record<string, AppState>> = {
  needs_clarification: "needs-clarification",
  unsupported: "unsupported",
  model_error: "model-error",
  invalid_model_output: "model-error",
};

export function stateForOutcome(outcome: string | undefined): AppState {
  return OUTCOME_STATES[outcome ?? ""] ?? "model-error";
}

/**
 * Which state a backend failure classification means.
 *
 * A mapping of the transport contract's own `failure` values, so the page
 * distinguishes a document problem from a geometry problem from an export
 * problem from an infrastructure problem -- without inventing a taxonomy.
 */
export const FAILURE_STATES: Readonly<Record<string, AppState>> = {
  malformed_document: "validation-error",
  invalid_document: "validation-error",
  invalid_request: "validation-error",
  geometry_failed: "build-error",
  output_failed: "output-error",
  execution_failed: "execution-error",
  internal_error: "execution-error",
};

export function stateForFailure(failure: string | undefined): AppState {
  return FAILURE_STATES[failure ?? ""] ?? "execution-error";
}

/** The parts of the page this module drives. */
export interface Elements {
  readonly descriptionInput: HTMLTextAreaElement;
  readonly generateButton: HTMLButtonElement;
  readonly clarify: HTMLElement;
  readonly questions: HTMLElement;
  readonly clarifyInput: HTMLTextAreaElement;
  readonly clarifyButton: HTMLButtonElement;
  readonly intentSummary: HTMLElement;
  readonly designIntent: HTMLElement;
  readonly documentInput: HTMLTextAreaElement;
  readonly loadExample: HTMLButtonElement;
  readonly validateButton: HTMLButtonElement;
  readonly buildButton: HTMLButtonElement;
  readonly fitButton: HTMLButtonElement;
  readonly status: HTMLElement;
  readonly errors: HTMLElement;
  readonly result: HTMLElement;
  readonly geometrySummary: HTMLElement;
  readonly exportButtons: Readonly<Record<DownloadableKind, HTMLButtonElement>>;
}

/**
 * The element ids the page uses.
 *
 * Declared once, so `index.html` and this module cannot drift apart: the
 * tests collect the real page's markup through `collectElements` below.
 */
export const ELEMENT_IDS = {
  descriptionInput: "description-input",
  generateButton: "generate-button",
  clarify: "clarify",
  questions: "questions",
  clarifyInput: "clarify-input",
  clarifyButton: "clarify-button",
  intentSummary: "intent-summary",
  designIntent: "design-intent",
  documentInput: "document-input",
  loadExample: "load-example",
  validateButton: "validate-button",
  buildButton: "build-button",
  fitButton: "fit-button",
  status: "status",
  errors: "errors",
  result: "result",
  geometrySummary: "geometry-summary",
  viewerCanvas: "viewer-canvas",
  exportStep: "export-step",
  exportIges: "export-iges",
  exportStl: "export-stl",
} as const;

/** Find one required element, by the id above. */
export function requireElement<T extends HTMLElement>(
  root: ParentNode,
  id: string,
): T {
  const element = root.querySelector<T>(`#${id}`);
  if (element === null) {
    throw new Error(`the page is missing #${id}`);
  }
  return element;
}

/** Collect the page's elements. Used by the entry point and by the tests. */
export function collectElements(root: ParentNode): Elements {
  return {
    descriptionInput: requireElement<HTMLTextAreaElement>(
      root,
      ELEMENT_IDS.descriptionInput,
    ),
    generateButton: requireElement<HTMLButtonElement>(
      root,
      ELEMENT_IDS.generateButton,
    ),
    clarify: requireElement(root, ELEMENT_IDS.clarify),
    questions: requireElement(root, ELEMENT_IDS.questions),
    clarifyInput: requireElement<HTMLTextAreaElement>(
      root,
      ELEMENT_IDS.clarifyInput,
    ),
    clarifyButton: requireElement<HTMLButtonElement>(
      root,
      ELEMENT_IDS.clarifyButton,
    ),
    intentSummary: requireElement(root, ELEMENT_IDS.intentSummary),
    designIntent: requireElement(root, ELEMENT_IDS.designIntent),
    documentInput: requireElement<HTMLTextAreaElement>(
      root,
      ELEMENT_IDS.documentInput,
    ),
    loadExample: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.loadExample),
    validateButton: requireElement<HTMLButtonElement>(
      root,
      ELEMENT_IDS.validateButton,
    ),
    buildButton: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.buildButton),
    fitButton: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.fitButton),
    status: requireElement(root, ELEMENT_IDS.status),
    errors: requireElement(root, ELEMENT_IDS.errors),
    result: requireElement(root, ELEMENT_IDS.result),
    geometrySummary: requireElement(root, ELEMENT_IDS.geometrySummary),
    exportButtons: {
      step: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.exportStep),
      iges: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.exportIges),
      stl: requireElement<HTMLButtonElement>(root, ELEMENT_IDS.exportStl),
    },
  };
}

/** What the viewport must be able to do. Satisfied by `createViewer`. */
export interface ViewerPort {
  show(model: RenderModel): void;
  fit(): void;
}

export interface AppOptions {
  readonly elements: Elements;
  readonly client: ApiClient;
  /** Built lazily, so a page with no WebGL still validates and builds. */
  readonly viewer?: () => ViewerPort | null;
}

export interface App {
  /** The current state, for tests and for the status line. */
  state(): AppState;
  /** The whole product flow: interpret, then build, as one action. */
  generate(): Promise<void>;
  /** Re-run generation with the clarification answer appended. */
  clarify(): Promise<void>;
  loadExample(): void;
  validate(): Promise<void>;
  build(): Promise<void>;
  /** The last successful build, if any. */
  lastBuild(): BuildResponse | null;
  /** The last render model drawn, if any. */
  lastModel(): RenderModel | null;
  /** The last generation answer, if any. */
  lastGeneration(): GenerateResponse | null;
}

const DOWNLOADABLE: readonly DownloadableKind[] = ["step", "iges", "stl"];

export function createApp(options: AppOptions): App {
  const { elements, client } = options;

  let state: AppState = "idle";
  let busy = false;
  let build: BuildResponse | null = null;
  let model: RenderModel | null = null;
  let viewer: ViewerPort | null = null;
  let generation: GenerateResponse | null = null;

  function setState(next: AppState, message: string): void {
    state = next;
    elements.status.dataset.state = next;
    elements.status.textContent = message;
  }

  function clearErrors(): void {
    elements.errors.textContent = "";
    elements.errors.hidden = true;
  }

  /** Show the validator's own structured errors, as the backend reported them. */
  function showErrors(error: ErrorRecord | null): void {
    clearErrors();
    if (error === null) {
      return;
    }
    const items: string[] = [];
    for (const record of error.validation_errors) {
      const where = [
        record.feature_id ? `feature ${record.feature_id}` : "",
        record.field_path ? `at ${record.field_path}` : "",
      ]
        .filter(Boolean)
        .join(" ");
      items.push(`${record.rule}|${record.message}${where ? ` (${where})` : ""}`);
    }
    if (items.length === 0 && error.rule_codes.length > 0) {
      items.push(`${error.rule_codes.join(", ")}|${error.message}`);
    }
    if (items.length === 0) {
      return;
    }
    for (const item of items) {
      const [code, ...rest] = item.split("|");
      const entry = document.createElement("li");
      const label = document.createElement("code");
      label.textContent = code;
      entry.append(label, ` ${rest.join("|")}`);
      elements.errors.append(entry);
    }
    elements.errors.hidden = false;
  }

  function hideResult(): void {
    elements.result.textContent = "";
    elements.result.hidden = true;
    elements.geometrySummary.textContent = "";
    for (const kind of DOWNLOADABLE) {
      const button = elements.exportButtons[kind];
      button.hidden = true;
      // Drop the identifier too, not just the button: a stale logical id from
      // a previous build must not survive into the next one.
      delete button.dataset.logicalId;
    }
    elements.fitButton.disabled = true;
  }

  function clearIntent(): void {
    elements.designIntent.textContent = "";
    elements.designIntent.hidden = true;
    elements.intentSummary.textContent = "";
  }

  function clearClarify(): void {
    elements.questions.textContent = "";
    elements.clarify.hidden = true;
  }

  /**
   * Show what the model decided, read from the document the backend returned.
   *
   * Nothing here is inferred from the user's sentence: `describeIntent` reads
   * document fields and formats them, and a field the document does not carry
   * is simply not shown.
   */
  function showIntent(response: GenerateResponse): void {
    clearIntent();
    elements.intentSummary.textContent = response.summary ?? "";
    const intent = describeIntent(response.document);
    if (intent === null || intent.features.length === 0) {
      return;
    }
    for (const feature of intent.features as readonly IntentFeature[]) {
      const heading = document.createElement("h3");
      heading.textContent = feature.heading;
      const list = document.createElement("dl");
      for (const entry of feature.rows) {
        row(list, entry.label, entry.value);
      }
      elements.designIntent.append(heading, list);
    }
    elements.designIntent.hidden = false;
  }

  /** Show the model's clarification questions, and invite one answer. */
  function showQuestions(questions: readonly string[]): void {
    clearClarify();
    for (const question of questions) {
      const entry = document.createElement("li");
      entry.textContent = question;
      elements.questions.append(entry);
    }
    elements.clarifyInput.value = "";
    elements.clarify.hidden = questions.length === 0;
  }

  /** Show plain reasons the backend gave, as a list. Never a traceback. */
  function showIssues(issues: readonly string[]): void {
    clearErrors();
    for (const issue of issues) {
      const entry = document.createElement("li");
      entry.textContent = issue;
      elements.errors.append(entry);
    }
    elements.errors.hidden = issues.length === 0;
  }

  function parseDocument(): unknown {
    return JSON.parse(elements.documentInput.value) as unknown;
  }

  function artifact(kind: string): ArtifactRecord | undefined {
    return build?.artifacts.find((item) => item.kind === kind);
  }

  /** Read a measurement the backend supplied. Never compute one. */
  function measurement(name: string): unknown {
    return artifact("geometry")?.measurements?.[name];
  }

  function row(list: HTMLElement, label: string, value: string): void {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  }

  function showResult(response: BuildResponse): void {
    const list = elements.result;
    list.textContent = "";
    row(list, "Status", response.status);
    row(list, "Build key", response.build_key ?? "-");
    row(list, "Document hash", response.document_hash ?? "-");
    row(list, "Source", response.cache_hit ? "cache" : "built now");
    row(list, "Outputs", response.outputs.join(", "));

    const size = measurement("bounding_box") as
      | { size?: Record<string, number> }
      | undefined;
    const dimensions = size?.size;
    if (dimensions) {
      row(
        list,
        "Dimensions",
        `${dimensions.x} x ${dimensions.y} x ${dimensions.z} mm`,
      );
    }
    const solids = measurement("solid_count");
    if (solids !== undefined) {
      row(list, "Solids", String(solids));
    }
    const volume = measurement("volume_mm3");
    if (volume !== undefined) {
      row(list, "Volume", `${String(volume)} mm3`);
    }
    const faces = measurement("face_count");
    if (faces !== undefined) {
      row(list, "Faces", String(faces));
    }
    const render = artifact("render");
    const triangles = render?.measurements?.["triangle_count"];
    if (triangles !== undefined) {
      row(list, "Triangles", String(triangles));
    }
    list.hidden = false;

    for (const kind of DOWNLOADABLE) {
      const record = artifact(kind);
      const button = elements.exportButtons[kind];
      button.hidden = record === undefined;
      if (record !== undefined) {
        button.dataset.logicalId = record.logical_id;
      } else {
        delete button.dataset.logicalId;
      }
    }
  }

  function setBusy(next: boolean): void {
    busy = next;
    elements.buildButton.disabled = next;
    elements.validateButton.disabled = next;
    elements.loadExample.disabled = next;
    elements.generateButton.disabled = next;
    elements.clarifyButton.disabled = next;
  }

  async function draw(buildKey: string): Promise<void> {
    const payload = await client.renderModel(buildKey);
    model = assertRenderModel(payload);
    elements.geometrySummary.textContent = describeMesh(model);
    if (viewer === null && options.viewer) {
      viewer = options.viewer();
    }
    viewer?.show(model);
    elements.fitButton.disabled = viewer === null;
  }

  /**
   * Build one document and draw it. Shared by both entry points.
   *
   * The document is passed through untouched, whoever produced it: the AI
   * path and the JSON path reach the *same* `POST /build`, so there is one
   * build flow and the generated document gets no special treatment.
   *
   * Assumes the caller has already taken the busy flag.
   */
  async function runBuild(parsed: unknown): Promise<void> {
    setState("building", "Building geometry...");
    try {
      const response = await client.build(parsed);
      if (!response.succeeded) {
        build = null;
        showErrors(response.error);
        setState(
          stateForFailure(response.error?.failure),
          response.error?.message ?? "The build failed.",
        );
        return;
      }
      build = response;
      showResult(response);
      if (response.build_key !== null) {
        await draw(response.build_key);
      }
      setState(
        "success",
        response.cache_hit ? "Ready (served from cache)." : "Ready.",
      );
    } catch (cause) {
      if (cause instanceof RenderModelError) {
        setState("output-error", cause.message);
      } else {
        setState(
          "network-error",
          cause instanceof NetworkError
            ? cause.message
            : "The API could not be reached.",
        );
      }
    }
  }

  /**
   * The product flow: interpret a description, then build what came back.
   *
   * One action from the user's point of view. The page does not inspect,
   * repair, complete or second-guess the model's document -- it shows what
   * the backend validated and sends that same document to be built.
   */
  async function runGeneration(text: string): Promise<void> {
    if (busy) {
      return;
    }
    if (!text.trim()) {
      clearErrors();
      clearIntent();
      clearClarify();
      setState("idle", "Describe the part you want before generating.");
      return;
    }
    setBusy(true);
    clearErrors();
    clearIntent();
    clearClarify();
    hideResult();
    generation = null;
    setState("generating", "Generating CAD...");
    try {
      const response = await client.generate(text);
      generation = response;
      if (response.outcome !== "generated" || response.document === null) {
        showIssues(response.issues);
        if (response.outcome === "needs_clarification") {
          showQuestions(response.questions);
        }
        setState(stateForOutcome(response.outcome), response.message);
        return;
      }
      showIntent(response);
      // Mirror the generated document into the advanced view, so what was
      // built is inspectable and re-buildable without retyping it.
      elements.documentInput.value = JSON.stringify(response.document, null, 2);
      await runBuild(response.document);
    } catch (cause) {
      setState(
        "network-error",
        cause instanceof NetworkError
          ? cause.message
          : "The API could not be reached.",
      );
    } finally {
      setBusy(false);
    }
  }

  return {
    state: () => state,
    lastBuild: () => build,
    lastModel: () => model,
    lastGeneration: () => generation,

    async generate(): Promise<void> {
      await runGeneration(elements.descriptionInput.value);
    },

    /**
     * Answer a clarification and try again.
     *
     * The answer is appended to the original description and sent as **one
     * fresh request**. There is no conversation, no history and no server-side
     * state: the model sees a single self-contained description, and the
     * document that comes back is validated exactly like any other.
     */
    async clarify(): Promise<void> {
      const answer = elements.clarifyInput.value.trim();
      if (!answer) {
        return;
      }
      const combined = `${elements.descriptionInput.value.trim()} ${answer}`;
      elements.descriptionInput.value = combined;
      await runGeneration(combined);
    },

    loadExample(): void {
      elements.documentInput.value = SECTION_D_JSON;
      clearErrors();
      clearIntent();
      clearClarify();
      hideResult();
      setState(
        "idle",
        "Describe a part and press Generate CAD, or press Build to use the " +
          "example CAD JSON.",
      );
    },

    async validate(): Promise<void> {
      if (busy) {
        return;
      }
      let parsed: unknown;
      try {
        parsed = parseDocument();
      } catch {
        clearErrors();
        setState("validation-error", "The document is not valid JSON.");
        return;
      }
      setBusy(true);
      setState("building", "Validating...");
      try {
        const response: ValidateResponse = await client.validate(parsed);
        if (response.valid) {
          clearErrors();
          setState(
            "success",
            `Valid: ${response.name ?? "document"}, ` +
              `${String(response.feature_count ?? 0)} feature(s).`,
          );
        } else {
          showErrors(response.error);
          setState(
            "validation-error",
            response.error?.message ?? "The document is not valid.",
          );
        }
      } catch (cause) {
        clearErrors();
        setState(
          "network-error",
          cause instanceof NetworkError
            ? cause.message
            : "The API could not be reached.",
        );
      } finally {
        setBusy(false);
      }
    },

    async build(): Promise<void> {
      if (busy) {
        return;
      }
      let parsed: unknown;
      try {
        parsed = parseDocument();
      } catch {
        clearErrors();
        hideResult();
        setState("validation-error", "The document is not valid JSON.");
        return;
      }
      setBusy(true);
      clearErrors();
      hideResult();
      try {
        await runBuild(parsed);
      } finally {
        setBusy(false);
      }
    },
  };
}

/**
 * Wire the export buttons: each uses the **logical id** the build returned.
 *
 * No path is built, no filename is guessed and no exporter is called here --
 * the URL is the artifact endpoint plus an identifier the backend issued.
 */
export function wireExports(
  elements: Elements,
  client: ApiClient,
  download: (url: string) => void,
): void {
  for (const kind of DOWNLOADABLE) {
    const button = elements.exportButtons[kind];
    button.addEventListener("click", () => {
      const logicalId = button.dataset.logicalId;
      if (logicalId) {
        download(client.artifactUrl(logicalId));
      }
    });
  }
}
