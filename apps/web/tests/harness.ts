/**
 * Test scaffolding: the real page markup, the real fixtures, a fake transport.
 *
 * The DOM under test is `index.html` itself, read from disk, so a renamed or
 * removed element fails the suite instead of passing against a hand-written
 * copy of the markup. The API responses are captured from the real backend
 * over the real HTTP API (see `tests/fixtures/README.md`) -- no response body
 * in this suite was written by hand.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { collectElements, type Elements, type ViewerPort } from "../src/app";
import type {
  BuildResponse,
  GenerateResponse,
  ValidateResponse,
} from "../src/api";
import type { RenderModel } from "../src/render-model";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = join(HERE, "..");
const FIXTURES = join(HERE, "fixtures");

/** The project's own `index.html`, verbatim. */
export function pageSource(): string {
  return readFileSync(join(WEB_ROOT, "index.html"), "utf8");
}

/** Read one source file, for the boundary assertions. */
export function sourceOf(name: string): string {
  return readFileSync(join(WEB_ROOT, "src", name), "utf8");
}

/** Install the real page body into the jsdom document. */
export function installPage(): Elements {
  const html = pageSource();
  const body = /<body[^>]*>([\s\S]*)<\/body>/.exec(html);
  if (body === null) {
    throw new Error("index.html has no <body>");
  }
  // The module script is dropped: these tests drive the app directly.
  document.body.innerHTML = body[1].replace(
    /<script[\s\S]*?<\/script>/g,
    "",
  );
  return collectElements(document);
}

/** A fixture, parsed. */
export function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(join(FIXTURES, name), "utf8")) as T;
}

export const successBuild = (): BuildResponse =>
  fixture<BuildResponse>("section-d-build.json");
export const cachedBuild = (): BuildResponse =>
  fixture<BuildResponse>("section-d-build-cached.json");
export const geometryFailure = (): BuildResponse =>
  fixture<BuildResponse>("build-error-geometry.json");
export const validationFailure = (): ValidateResponse =>
  fixture<ValidateResponse>("validation-error.json");
export const sectionDRender = (): RenderModel =>
  fixture<RenderModel>("section-d-render.json");
export const sectionDDocument = (): Record<string, unknown> =>
  fixture<Record<string, unknown>>("section-d-document.json");

/**
 * Real `POST /generate` answers, captured from the live Gemini provider.
 *
 * These are what the model actually returned for the descriptions named in
 * `fixtures/README.md`. They are evidence of *shape*, not of quality: the
 * suite asserts the page handles each shape, and nothing here claims the
 * model answers this way every time. (It does not -- see the README.)
 */
export const generatedPlate = (): GenerateResponse =>
  fixture<GenerateResponse>("generate-plate.json");
export const generatedCylinder = (): GenerateResponse =>
  fixture<GenerateResponse>("generate-cylinder.json");
export const clarificationNeeded = (): GenerateResponse =>
  fixture<GenerateResponse>("generate-clarification.json");
export const unsupportedRequest = (): GenerateResponse =>
  fixture<GenerateResponse>("generate-unsupported.json");
export const invalidModelOutput = (): GenerateResponse =>
  fixture<GenerateResponse>("generate-invalid-model-output.json");
export const generatedPlateBuild = (): BuildResponse =>
  fixture<BuildResponse>("generated-plate-build.json");
export const generatedPlateRender = (): RenderModel =>
  fixture<RenderModel>("generated-plate-render.json");

/** One recorded request: what the page actually sent. */
export interface RecordedCall {
  readonly url: string;
  readonly method: string;
  readonly body: unknown;
}

export interface StubOptions {
  /** Keyed by path suffix, e.g. `/build`. A function may throw. */
  readonly routes: Readonly<
    Record<string, unknown | ((call: RecordedCall) => unknown)>
  >;
}

export interface Stub {
  readonly calls: RecordedCall[];
  readonly fetch: typeof globalThis.fetch;
}

/**
 * A `fetch` that answers from fixtures and records what it was asked.
 *
 * A route value of `Error` (or a function that throws) reproduces a transport
 * failure, which is how the network-error path is exercised.
 */
export function stubFetch(options: StubOptions): Stub {
  const calls: RecordedCall[] = [];
  const fetch = (async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body =
      typeof init?.body === "string"
        ? (JSON.parse(init.body) as unknown)
        : undefined;
    const call: RecordedCall = { url, method, body };
    calls.push(call);
    // Longest suffix first: `/builds/<key>/render` also contains `/build`.
    const key = Object.keys(options.routes)
      .sort((left, right) => right.length - left.length)
      .find((suffix) => url.includes(suffix));
    if (key === undefined) {
      throw new Error(`no stub route for ${url}`);
    }
    const route = options.routes[key];
    const payload =
      typeof route === "function"
        ? (route as (call: RecordedCall) => unknown)(call)
        : route;
    if (payload instanceof Error) {
      throw payload;
    }
    return {
      ok: true,
      status: 200,
      async json(): Promise<unknown> {
        return payload;
      },
    } as unknown as Response;
  }) as typeof globalThis.fetch;
  return { calls, fetch };
}

/** A viewport that records instead of drawing. No WebGL in jsdom. */
export interface RecordingViewer extends ViewerPort {
  readonly shown: RenderModel[];
  fits: number;
}

export function recordingViewer(): RecordingViewer {
  const shown: RenderModel[] = [];
  return {
    shown,
    fits: 0,
    show(model: RenderModel): void {
      shown.push(model);
    },
    fit(): void {
      this.fits += 1;
    },
  };
}

/** The rendered result rows, as `{label: value}`. */
export function resultRows(elements: Elements): Record<string, string> {
  const rows: Record<string, string> = {};
  const terms = elements.result.querySelectorAll("dt");
  const details = elements.result.querySelectorAll("dd");
  terms.forEach((term, index) => {
    rows[term.textContent ?? ""] = details[index]?.textContent ?? "";
  });
  return rows;
}

/** Everything the user can read on the page, as one string. */
export function visibleText(elements: Elements): string {
  return [
    elements.status.textContent ?? "",
    elements.errors.textContent ?? "",
    elements.result.textContent ?? "",
    elements.geometrySummary.textContent ?? "",
  ].join("\n");
}
