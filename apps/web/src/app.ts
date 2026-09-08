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
  ValidateResponse,
} from "./api";
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
  | "building"
  | "success"
  | "validation-error"
  | "build-error"
  | "output-error"
  | "execution-error"
  | "network-error";

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
  loadExample(): void;
  validate(): Promise<void>;
  build(): Promise<void>;
  /** The last successful build, if any. */
  lastBuild(): BuildResponse | null;
  /** The last render model drawn, if any. */
  lastModel(): RenderModel | null;
}

const DOWNLOADABLE: readonly DownloadableKind[] = ["step", "iges", "stl"];

export function createApp(options: AppOptions): App {
  const { elements, client } = options;

  let state: AppState = "idle";
  let busy = false;
  let build: BuildResponse | null = null;
  let model: RenderModel | null = null;
  let viewer: ViewerPort | null = null;

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
      elements.exportButtons[kind].hidden = true;
    }
    elements.fitButton.disabled = true;
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

  return {
    state: () => state,
    lastBuild: () => build,
    lastModel: () => model,

    loadExample(): void {
      elements.documentInput.value = SECTION_D_JSON;
      clearErrors();
      hideResult();
      setState("idle", "Example loaded. Press Build.");
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
      setState("building", "Building...");
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
          response.cache_hit
            ? "Built (served from cache)."
            : "Built successfully.",
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
