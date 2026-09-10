/**
 * The experimental page: describe -> plan -> validate -> build -> view.
 *
 * A client, exactly like the stable page. Every number it shows is a field
 * the backend sent; it holds no CAD logic, no kernel and no geometry of its
 * own. The viewer and the render-model reader are imported from the stable
 * application rather than copied, so there is one of each.
 */

import { createViewer, type Viewer } from "../../web/src/viewer";
import {
  assertRenderModel,
  describeMesh,
  RenderModelError,
} from "../../web/src/render-model";
import {
  ApiError,
  buildPlan,
  generatePlan,
  health,
  localFixtures,
  runLocalFixture,
  validatePlan,
  type BuildResponse,
  type PlanOperation,
  type PlanResponse,
} from "./api";

function need<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`the page is missing #${id}`);
  }
  return element as T;
}

const description = need<HTMLTextAreaElement>("description");
const generateButton = need<HTMLButtonElement>("generate");
const generateStatus = need<HTMLSpanElement>("generate-status");
const planSummary = need<HTMLDivElement>("plan-summary");
const operationTree = need<HTMLOListElement>("operation-tree");
const planJson = need<HTMLTextAreaElement>("plan-json");
const validateButton = need<HTMLButtonElement>("validate");
const validateStatus = need<HTMLSpanElement>("validate-status");
const problemList = need<HTMLUListElement>("problems");
const buildButton = need<HTMLButtonElement>("build");
const buildStatus = need<HTMLSpanElement>("build-status");
const measurements = need<HTMLDListElement>("measurements");
const meshNote = need<HTMLParagraphElement>("mesh-note");
const canvas = need<HTMLCanvasElement>("viewport");
const fixtureSelect = need<HTMLSelectElement>("fixture");
const runLocalButton = need<HTMLButtonElement>("run-local");
const localStatus = need<HTMLSpanElement>("local-status");
const planSource = need<HTMLDivElement>("plan-source");

let viewer: Viewer | null = null;

function say(
  element: HTMLElement,
  message: string,
  tone: "" | "ok" | "bad" | "warn" = "",
): void {
  element.textContent = message;
  element.className = tone === "" ? "status" : `status ${tone}`;
}

function clear(element: HTMLElement): void {
  while (element.firstChild !== null) {
    element.removeChild(element.firstChild);
  }
}

/**
 * One parameter, as text.
 *
 * An edge selector gets a readable form rather than raw JSON: `edges` is the
 * only nested object in the vocabulary, and `all` / `axis_parallel Z` is what
 * a reader actually wants to see.
 */
function describeParameter(name: string, value: unknown): string {
  if (name === "edges" && typeof value === "object" && value !== null) {
    const selector = value as { select?: string; axis?: string };
    return selector.axis === undefined
      ? String(selector.select)
      : `${String(selector.select)} ${selector.axis}`;
  }
  if ((name === "geometry" || name === "constraints") && Array.isArray(value)) {
    // A sketch's two lists, as `type #id` rather than as a JSON blob. Long
    // enough to read, and it still says nothing the response did not.
    if (value.length === 0) {
      return "none";
    }
    return value
      .map((entry) => {
        const record = entry as { id?: unknown; type?: unknown };
        return `${String(record.type)} #${String(record.id)}`;
      })
      .join(", ");
  }
  return typeof value === "object" && value !== null
    ? JSON.stringify(value)
    : String(value);
}

/**
 * Render the plan as a tree.
 *
 * Built with `textContent`, never `innerHTML`: the operation ids, types and
 * summaries are model-authored strings, and the one place they are displayed
 * is the one place an injected fragment would matter.
 */
function showOperations(operations: readonly PlanOperation[]): void {
  clear(operationTree);
  for (const operation of operations) {
    const item = document.createElement("li");

    const heading = document.createElement("div");
    const type = document.createElement("span");
    type.className = "op-type";
    type.textContent = operation.type;
    const id = document.createElement("span");
    id.className = "op-id";
    id.textContent = `  #${operation.id}`;
    heading.append(type, id);
    if (operation.target !== undefined) {
      // A modifier acts on a solid; showing which one is the difference
      // between a readable plan and a list of unattached operations.
      const target = document.createElement("span");
      target.className = "op-target";
      target.textContent = `→ ${operation.target}`;
      heading.append(target);
    }
    item.append(heading);

    if (operation.type === "sketch") {
      // A sketch declares a profile, and this backend cannot build one. Saying
      // so beside the operation is the difference between a page that reads as
      // a part and a page that tells the truth about what it has.
      const note = document.createElement("div");
      note.className = "op-note";
      note.textContent = "profile — not a solid, and not executable here";
      item.append(note);
    }

    if (operation.tools !== undefined && operation.tools.length > 0) {
      // A subtract's tools are consumed, so naming them is the difference
      // between a readable history and a list of ids.
      const tools = document.createElement("div");
      tools.className = "op-tools";
      tools.textContent = `tools: [${operation.tools.join(", ")}] · consumed`;
      item.append(tools);
    }

    const list = document.createElement("dl");
    for (const [name, value] of Object.entries(operation.parameters ?? {})) {
      const term = document.createElement("dt");
      term.textContent = name;
      const definition = document.createElement("dd");
      definition.textContent = describeParameter(name, value);
      list.append(term, definition);
    }
    item.append(list);
    operationTree.append(item);
  }
}

/** The plan as the backend would accept it back. */
function planPayload(): unknown {
  return JSON.parse(planJson.value);
}

/**
 * Say where the displayed plan came from.
 *
 * The page must never let a developer-written fixture read as a model
 * answer, so this is called on every path that puts a plan on screen and
 * takes the label from the response itself rather than from what the page
 * happened to request.
 */
function showSource(kind: "local" | "model", detail: string): void {
  planSource.hidden = false;
  planSource.className = `source-badge ${kind}`;
  planSource.textContent =
    kind === "local"
      ? `LOCAL DEVELOPMENT PLAN — ${detail}`
      : `MODEL RESULT — ${detail}`;
}

function showPlan(plan: PlanResponse): void {
  const status = plan.status;
  planSummary.className = "summary";
  if (status === "generated") {
    planSummary.textContent = plan.summary || "a plan was generated";
  } else if (status === "unsupported") {
    planSummary.className = "summary unsupported";
    planSummary.textContent = `unsupported — ${plan.reason ?? plan.summary}`;
  } else if (status === "needs_clarification") {
    planSummary.className = "summary clarify";
    planSummary.textContent = `needs clarification — ${(plan.questions ?? []).join(" ")}`;
  } else {
    planSummary.className = "summary unsupported";
    planSummary.textContent = plan.error ?? `the model returned ${status}`;
  }

  showOperations(plan.operations);
  planJson.value = JSON.stringify(
    {
      status: plan.status,
      summary: plan.summary,
      ...(plan.reason === undefined ? {} : { reason: plan.reason }),
      ...(plan.questions === undefined ? {} : { questions: plan.questions }),
      operations: plan.operations,
    },
    null,
    2,
  );

  refreshBuildable();
}

generateButton.addEventListener("click", async () => {
  const text = description.value.trim();
  if (text === "") {
    say(generateStatus, "describe a part first", "warn");
    return;
  }
  generateButton.disabled = true;
  say(generateStatus, "asking the model…");
  clear(problemList);
  clear(measurements);
  try {
    const plan = await generatePlan(text);
    showPlan(plan);
    showSource(
      "model",
      plan.metadata ? String(plan.metadata["model"]) : "provider result",
    );
    say(
      generateStatus,
      `${plan.status}${plan.metadata ? ` · ${String(plan.metadata["model"])}` : ""}`,
      plan.status === "generated" ? "ok" : "warn",
    );
  } catch (error) {
    const message =
      error instanceof ApiError
        ? `${error.message} (${error.status})`
        : String(error);
    say(generateStatus, message, "bad");
  } finally {
    generateButton.disabled = false;
  }
});

validateButton.addEventListener("click", async () => {
  clear(problemList);
  let payload: unknown;
  try {
    payload = planPayload();
  } catch {
    say(validateStatus, "the plan JSON does not parse", "bad");
    return;
  }
  say(validateStatus, "validating…");
  try {
    const verdict = await validatePlan(payload);
    for (const problem of verdict.problems) {
      const item = document.createElement("li");
      item.textContent = `${problem.code} ${problem.where} ${problem.message}`;
      problemList.append(item);
    }
    say(
      validateStatus,
      verdict.valid ? "the plan is valid" : "the plan is not valid",
      verdict.valid ? "ok" : "bad",
    );
  } catch (error) {
    say(validateStatus, String(error), "bad");
  }
});

function showMeasurements(entries: readonly (readonly [string, string])[]): void {
  clear(measurements);
  for (const [name, value] of entries) {
    const term = document.createElement("dt");
    term.textContent = name;
    const definition = document.createElement("dd");
    definition.textContent = value;
    measurements.append(term, definition);
  }
}

/**
 * Show a build's measurements and draw its mesh.
 *
 * Shared by the model path and the local-development path so the two cannot
 * drift, and so a fixture is measured by exactly the code that measures a
 * generated plan.
 */
function renderBuild(result: BuildResponse): void {
  const build = result.build;
  if (build === undefined) {
    showMeasurements([]);
    return;
  }
  const geometry = (build.manifest?.artifacts ?? []).find(
    (artifact) => artifact.kind === "geometry",
  );
  const details = (geometry?.details ?? {}) as Record<string, unknown>;
  const box = (details["bounding_box"] ?? {}) as Record<string, unknown>;
  const size = (box["size"] ?? {}) as Record<string, unknown>;
  showMeasurements([
    ["build key", String(build.build_key ?? "").slice(0, 16)],
    ["cache hit", String(build.cache_hit ?? false)],
    ["solids", String(details["solid_count"] ?? "—")],
    ["volume mm³", String(details["volume_mm3"] ?? "—")],
    ["faces", String(details["face_count"] ?? "—")],
    [
      "size mm",
      `${String(size["x"] ?? "—")} × ${String(size["y"] ?? "—")} × ${String(size["z"] ?? "—")}`,
    ],
  ]);

  if (result.render === undefined || result.render === null) {
    say(meshNote, "the build returned no render model", "warn");
    return;
  }
  try {
    const model = assertRenderModel(result.render);
    if (viewer === null) {
      viewer = createViewer(canvas);
    }
    viewer.show(model);
    meshNote.className = "status";
    meshNote.textContent = describeMesh(model);
  } catch (error) {
    const message =
      error instanceof RenderModelError
        ? `the render model was rejected: ${error.message}`
        : String(error);
    say(meshNote, message, "bad");
  }
}

/**
 * Whether a rejected build was refused for want of an execution path.
 *
 * Read from the response body's own flag, never from the message text or the
 * status code alone: the page must not decide on the backend's behalf what
 * kind of refusal it received.
 */
function isExecutionUnsupported(error: ApiError): boolean {
  if (typeof error.body !== "object" || error.body === null) {
    return false;
  }
  return (
    (error.body as { execution_unsupported?: unknown })
      .execution_unsupported === true
  );
}

buildButton.addEventListener("click", async () => {
  let payload: unknown;
  try {
    payload = planPayload();
  } catch {
    say(buildStatus, "the plan JSON does not parse", "bad");
    return;
  }
  buildButton.disabled = true;
  say(buildStatus, "building…");
  try {
    const result = await buildPlan(payload);
    // `build` is optional on the shared response type because the
    // local-development route omits it when nothing was built.
    if (result.build === undefined || !result.build.succeeded) {
      say(
        buildStatus,
        result.build?.error?.message ?? "the build failed",
        "bad",
      );
      showMeasurements([]);
      return;
    }

    renderBuild(result);
    say(buildStatus, "built", "ok");
  } catch (error) {
    if (error instanceof ApiError && isExecutionUnsupported(error)) {
      // Not a failure of the plan, and shown as such: `warn`, not `bad`, no
      // measurements invented, and no mesh drawn.
      const body = error.body as { unsupported_types?: readonly string[] };
      const types = (body.unsupported_types ?? []).join(", ");
      say(
        buildStatus,
        `not executable here — this backend has no build path for ${types}. ` +
          `The plan is valid; nothing was built.`,
        "warn",
      );
      showMeasurements([]);
      return;
    }
    const message =
      error instanceof ApiError
        ? `${error.message} (${error.status})`
        : String(error);
    say(buildStatus, message, "bad");
  } finally {
    buildButton.disabled = false;
  }
});

runLocalButton.addEventListener("click", async () => {
  const name = fixtureSelect.value;
  if (name === "") {
    say(localStatus, "no fixture selected", "warn");
    return;
  }
  runLocalButton.disabled = true;
  say(localStatus, "running the local plan…");
  clear(problemList);
  clear(measurements);
  try {
    const result = await runLocalFixture(name);

    // Take the label from the response. A page that decided this for
    // itself could be wrong; the server is the one that knows.
    showSource("local", `${name} · no model called`);
    showPlan({
      status: result.plan?.status ?? "generated",
      operations: result.plan?.operations ?? [],
      summary: result.plan?.summary ?? "",
    });

    for (const problem of result.problems ?? []) {
      const item = document.createElement("li");
      item.textContent = `${problem.code} ${problem.where} ${problem.message}`;
      problemList.append(item);
    }
    say(
      validateStatus,
      result.plan_valid ? "the plan is valid" : "the plan is not valid",
      result.plan_valid ? "ok" : "bad",
    );

    if (result.execution_unsupported === true) {
      // The local route answers 200 with a flag rather than 501, because the
      // fixture *ran* -- it parsed, validated and then had nowhere to go. It
      // is still not a build failure, and not shown as one.
      const types = (result.unsupported_types ?? []).join(", ");
      say(
        localStatus,
        `${name} — valid, and not executable here (${types})`,
        "warn",
      );
      say(
        buildStatus,
        `not executable here — no build path for ${types}. ` +
          `The plan is valid; nothing was built.`,
        "warn",
      );
      showMeasurements([]);
      return;
    }
    if (result.built !== true || result.build === undefined) {
      say(localStatus, "the plan did not build", "bad");
      say(buildStatus, "not built", "bad");
      return;
    }
    renderBuild(result);
    say(buildStatus, "built", "ok");
    say(localStatus, `${name} built · not a Claude result`, "warn");
  } catch (error) {
    const message =
      error instanceof ApiError
        ? `${error.message} (${error.status})`
        : String(error);
    say(localStatus, message, "bad");
  } finally {
    runLocalButton.disabled = false;
  }
});

for (const button of document.querySelectorAll<HTMLButtonElement>(
  "button.example",
)) {
  button.addEventListener("click", () => {
    description.value = button.dataset["text"] ?? "";
    say(generateStatus, "");
  });
}

/**
 * Enable Build only when the plan in the textarea is a generated plan with
 * operations. Called after a generate, after a validate, and on every edit,
 * so a hand-written plan is as usable as a generated one -- which matters
 * when no credential is configured and generation is unavailable.
 */
function refreshBuildable(): void {
  let buildable = false;
  try {
    const plan = planPayload() as {
      status?: string;
      operations?: readonly unknown[];
    };
    buildable =
      plan.status === "generated" && (plan.operations ?? []).length > 0;
  } catch {
    buildable = false;
  }
  buildButton.disabled = !buildable;
}

planJson.addEventListener("input", refreshBuildable);
refreshBuildable();

void (async () => {
  try {
    const listing = await localFixtures();
    for (const entry of listing.fixtures) {
      const option = document.createElement("option");
      option.value = entry.name;
      option.textContent = `${entry.name} — ${entry.description}`;
      fixtureSelect.append(option);
    }
    say(
      localStatus,
      `${listing.fixtures.length} fixtures · ${listing.source}`,
      "warn",
    );
  } catch {
    say(localStatus, "the fixtures could not be listed", "bad");
    runLocalButton.disabled = true;
  }

  try {
    const status = await health();
    if (!status.model_configured) {
      say(
        generateStatus,
        `no credential configured — generation is unavailable (model ${status.model})`,
        "warn",
      );
      generateButton.disabled = true;
    } else {
      say(generateStatus, `ready · ${status.model}`, "ok");
    }
  } catch {
    say(
      generateStatus,
      "the experimental API is not running on 8001",
      "bad",
    );
    generateButton.disabled = true;
  }
})();
