/**
 * The experimental CAD workspace: a viewport, a copilot, and the status of
 * what actually ran.
 *
 * This file is the product shell. It adds no CAD pipeline of its own -- every
 * step below is the existing one, reached through `./api`:
 *
 *   describe -> POST /experimental/generate-plan   (real model, real prompt)
 *            -> POST /experimental/validate-plan   (the plan validator)
 *            -> POST /experimental/build-plan      (backend abstraction)
 *            -> RenderModel                        (the one neutral contract)
 *            -> the viewport                       (shared with apps/web)
 *
 * The canonical Operation Plan stays the execution IR and is still what the
 * API is asked to build. What changed is that a person no longer has to read
 * or write it: it is shown under Technical details, as evidence rather than
 * as the interface.
 *
 * The page is backend-agnostic. It never names an engine of its own; it
 * displays the `backend` the API reports and nothing else, so the same page
 * serves a FreeCAD deployment and a CadQuery one identically.
 */

import {
  RenderModelError,
  assertRenderModel,
  describeMesh,
} from "../../web/src/render-model";
import { createViewer, type Viewer } from "../../web/src/viewer";
import {
  ApiError,
  health,
  localFixtures,
  askEngineering,
  createDrawing,
  createMacro,
  exportPart,
  listMacros,
  resetSession,
  runMacro,
  searchCatalog,
  runLocalFixture,
  sendTurn,
  undoTurn,
  type BuildResponse,
  type Finding,
  type SessionReply,
  type SessionState,
  type ExecutionReport,
  type PlanOperation,
  type PlanResponse,
} from "./api";

function need<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) throw new Error(`the page is missing #${id}`);
  return element as T;
}

const canvas = need<HTMLCanvasElement>("viewport");
const viewportEmpty = need<HTMLDivElement>("viewport-empty");
const viewportBusy = need<HTMLDivElement>("viewport-busy");
const viewportBusyLabel = need<HTMLSpanElement>("viewport-busy-label");
const meshNote = need<HTMLParagraphElement>("mesh-note");
const partTitle = need<HTMLHeadingElement>("part-title");
const partSub = need<HTMLSpanElement>("part-sub");
const fitButton = need<HTMLButtonElement>("fit-view");

const thread = need<HTMLDivElement>("thread");
const composer = need<HTMLFormElement>("composer");
const prompt = need<HTMLTextAreaElement>("prompt");
const sendButton = need<HTMLButtonElement>("send");
const undoButton = need<HTMLButtonElement>("undo");
const newPartButton = need<HTMLButtonElement>("new-part");
const copilotSub = need<HTMLSpanElement>("copilot-sub");
const opTree = need<HTMLUListElement>("op-tree");
const inspect = need<HTMLDListElement>("inspect");
const inspectSource = need<HTMLParagraphElement>("inspect-source");
const exportStepButton = need<HTMLButtonElement>("export-step");
const exportNote = need<HTMLParagraphElement>("export-note");

const statBackend = need<HTMLSpanElement>("stat-backend");
const statModel = need<HTMLSpanElement>("stat-model");
const statPath = need<HTMLSpanElement>("stat-path");
const statBuild = need<HTMLSpanElement>("stat-build");
const statLast = need<HTMLSpanElement>("stat-last");
const statMeasure = need<HTMLSpanElement>("stat-measure");

const detailsToggle = need<HTMLButtonElement>("details-toggle");
const details = need<HTMLElement>("details");
const opList = need<HTMLOListElement>("op-list");
const selectorEvidence = need<HTMLDListElement>("selector-evidence");
const measurements = need<HTMLDListElement>("measurements");
const planJson = need<HTMLPreElement>("plan-json");

const healthDot = need<HTMLSpanElement>("health-dot");
const healthLabel = need<HTMLSpanElement>("health-label");
const settingsEnv = need<HTMLDListElement>("settings-env");
const fixtureSelect = need<HTMLSelectElement>("fixture");
const runLocalButton = need<HTMLButtonElement>("run-local");
const localStatus = need<HTMLParagraphElement>("local-status");

let viewer: Viewer | null = null;

// --- small DOM helpers ------------------------------------------------------

function set(element: HTMLElement, text: string, tone?: "ok" | "bad" | "warn"): void {
  element.textContent = text;
  element.classList.remove("is-ok", "is-bad", "is-warn");
  if (tone) element.classList.add(`is-${tone}`);
}

function clear(element: HTMLElement): void {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function pairs(
  target: HTMLElement,
  entries: readonly (readonly [string, string])[],
  empty: string,
): void {
  clear(target);
  if (entries.length === 0) {
    const none = document.createElement("dd");
    none.className = "muted";
    none.textContent = empty;
    target.append(none);
    return;
  }
  for (const [key, value] of entries) {
    const term = document.createElement("dt");
    term.textContent = key;
    const definition = document.createElement("dd");
    definition.textContent = value;
    target.append(term, definition);
  }
}

function busy(label: string | null): void {
  viewportBusy.hidden = label === null;
  if (label !== null) viewportBusyLabel.textContent = label;
}

// --- the conversation -------------------------------------------------------

type Who = "you" | "copilot";

/**
 * Add one message. Returns a handle so a pending message can be rewritten in
 * place when the step it describes finishes -- which is what makes the panel
 * feel like progress rather than a log that appears all at once.
 */
function say(
  who: Who,
  text: string,
  kind?: "error" | "pending",
): { update: (text: string, kind?: "error" | "pending") => void; facts: (entries: readonly (readonly [string, string])[]) => void } {
  const message = document.createElement("div");
  message.className = `msg msg-${who === "you" ? "user" : "assistant"}`;
  if (kind) message.classList.add(`msg-${kind}`);

  const label = document.createElement("div");
  label.className = "msg-who";
  label.textContent = who;

  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;

  message.append(label, body);
  thread.append(message);
  thread.scrollTop = thread.scrollHeight;

  return {
    update(next: string, nextKind?: "error" | "pending") {
      body.textContent = next;
      message.classList.remove("msg-error", "msg-pending");
      if (nextKind) message.classList.add(`msg-${nextKind}`);
      thread.scrollTop = thread.scrollHeight;
    },
    facts(entries) {
      const list = document.createElement("dl");
      list.className = "msg-facts";
      for (const [key, value] of entries) {
        const term = document.createElement("dt");
        term.textContent = key;
        const definition = document.createElement("dd");
        definition.textContent = value;
        list.append(term, definition);
      }
      message.append(list);
      thread.scrollTop = thread.scrollHeight;
    },
  };
}

// --- reading what the backend reported --------------------------------------

/**
 * The measurement OF THE PART, which a part with several bodies does not have.
 *
 * This used to read `execution.bodies[0].measurement` -- the Stage 62 bug,
 * surviving on the client after Stage 71 removed it from the server. On a
 * two-body build the panel showed the first body's volume, envelope and face
 * count under the heading "measurements", with nothing saying it was one
 * body's. The numbers were real; the label was wrong, which is the harder
 * kind of wrong to notice.
 *
 * Now it returns `{}` for a multi-body build, exactly as the server's own
 * `_facts` does, and the per-body numbers are shown by `bodyMeasurements`
 * below where each says which body it is.
 */
function measurementOf(build: BuildResponse): Record<string, unknown> {
  if (build.executed_by_graph === true) {
    const bodies = build.execution?.bodies ?? [];
    if (bodies.length !== 1) return {};
    return (bodies[0]?.measurement ?? {}) as Record<string, unknown>;
  }
  const geometry = (build.build?.manifest?.artifacts ?? []).find(
    (artifact) => artifact.kind === "geometry",
  );
  const detail = (geometry?.details ?? {}) as Record<string, unknown>;
  const box = (detail["bounding_box"] ?? {}) as Record<string, unknown>;
  const size = (box["size"] ?? {}) as Record<string, unknown>;
  return {
    solid_count: detail["solid_count"],
    volume: detail["volume_mm3"],
    face_count: detail["face_count"],
    edge_count: detail["edge_count"],
    size: [size["x"], size["y"], size["z"]],
  };
}

function round(value: unknown): string {
  return typeof value === "number" ? value.toFixed(3) : String(value ?? "—");
}

function summarise(measured: Record<string, unknown>): string {
  const size = (measured["size"] ?? []) as unknown[];
  const solids = measured["solid_count"];
  const parts: string[] = [];
  // One solid is the expected outcome -- the contract requires it -- so it is
  // reported only when it is NOT one. Saying "1 solid" on every build spent
  // the width that the overall dimensions needed, and truncated them.
  if (typeof solids === "number" && solids !== 1) {
    parts.push(`${solids} solids`);
  }
  parts.push(
    `${round(measured["volume"])} mm³`,
    `${String(measured["face_count"] ?? "—")} faces`,
  );
  if (size.length === 3 && size[0] !== undefined) {
    parts.push(`${round(size[0])} × ${round(size[1])} × ${round(size[2])} mm`);
  }
  return parts.join("  ·  ");
}

/** Plain-English evidence for one resolved semantic selector. */
function selectorSentence(operation: string, resolution: {
  readonly indices?: readonly number[];
  readonly candidates?: readonly number[];
  readonly seams?: readonly number[];
}): string {
  const named = resolution.indices?.length ?? 0;
  const considered = resolution.candidates?.length ?? 0;
  const seams = resolution.seams?.length ?? 0;
  const seamNote = seams > 0 ? `, ${seams} seam edge(s) excluded` : "";
  return `${operation}: selected ${named} of ${considered} candidate edge(s)${seamNote}`;
}

// --- drawing ----------------------------------------------------------------

function draw(payload: unknown): boolean {
  return drawBodies([{ body_id: "", declared: false, render: payload }]);
}

/**
 * Draw every body of the part.
 *
 * One mesh per body and nothing merged -- the plan is the only thing in this
 * system that joins solids, and a viewer that fused two bodies to get them on
 * screen would be showing a part nobody asked for. The note under the
 * viewport names each body, so what is drawn is legible as several bodies
 * rather than as one oddly shaped one.
 */
function drawBodies(
  bodies: readonly { body_id: string; declared: boolean; render?: unknown }[],
): boolean {
  try {
    const drawable = bodies.filter((body) => body.render != null);
    if (drawable.length === 0) return false;
    const models = drawable.map((body) => assertRenderModel(body.render));
    if (viewer === null) viewer = createViewer(canvas);
    viewer.showBodies(models);
    viewportEmpty.hidden = true;
    // One body reads exactly as it always did. The body id is added only
    // when there is more than one, where it is the thing that makes the note
    // legible as several bodies rather than one oddly shaped one.
    meshNote.textContent =
      models.length === 1
        ? describeMesh(models[0])
        : drawable
            .map((body, index) => `${body.body_id}: ${describeMesh(models[index])}`)
            .join("\n");
    return true;
  } catch (error) {
    meshNote.textContent =
      error instanceof RenderModelError
        ? `the render model was rejected: ${error.message}`
        : String(error);
    return false;
  }
}


/**
 * Fill the Technical details panel.
 *
 * Shared by the copilot turn and the developer fixture runner so the two
 * cannot drift -- and so a fixture is described by exactly the code that
 * describes a generated part.
 */
function showDetails(
  plan: { summary?: string; operations?: readonly PlanOperation[] } | undefined,
  execution: ExecutionReport | undefined,
  measured: Record<string, unknown>,
): void {
  clear(opList);
  const operations = plan?.operations ?? [];
  if (operations.length === 0) {
    const none = document.createElement("li");
    none.className = "muted";
    none.textContent = "nothing built yet";
    opList.append(none);
  } else {
    for (const operation of operations) {
      const item = document.createElement("li");
      const type = document.createElement("span");
      type.className = "op-type";
      type.textContent = operation.type;
      item.append(`${operation.id} — `, type);
      opList.append(item);
    }
  }

  const selections = Object.entries(execution?.selections ?? {});
  pairs(
    selectorEvidence,
    selections.map(([operation, resolution]) => [
      operation,
      `${resolution.indices?.length ?? 0} of ${resolution.candidates?.length ?? 0} edge(s)` +
        ((resolution.seams?.length ?? 0) > 0
          ? `, ${resolution.seams?.length} seam excluded`
          : ""),
    ]),
    "no semantic selector resolved yet",
  );

  pairs(measurements, measurementRows(execution, measured), "—");

  planJson.textContent = plan
    ? JSON.stringify(
        { status: "generated", summary: plan.summary, operations: plan.operations },
        null,
        2,
      )
    : "—";
}

/**
 * The measurement rows: the part's, or EVERY BODY'S, each saying which it is.
 *
 * A part with several bodies has no single volume and no single envelope, so
 * there is nothing to put in a part-level row. Leaving the panel empty would
 * be honest but useless -- the numbers exist, one set per body -- so each
 * body's are listed under its own id, and the totals that genuinely add are
 * labelled as totals rather than as measurements.
 *
 * `size` is deliberately NOT totalled. The box containing two bodies also
 * contains the gap between them, and no kernel measured it; the server calls
 * that ASSUMED and this simply does not claim it.
 */
function measurementRows(
  execution: ExecutionReport | undefined,
  measured: Record<string, unknown>,
): (readonly [string, string])[] {
  const bodies = execution?.bodies ?? [];
  if (bodies.length > 1) {
    const rows: (readonly [string, string])[] = [];
    let volume = 0;
    for (const body of bodies) {
      const own = (body.measurement ?? {}) as Record<string, unknown>;
      const size = (own["size"] ?? []) as unknown[];
      const extent =
        size.length === 3 && size[0] !== undefined
          ? `${round(size[0])} × ${round(size[1])} × ${round(size[2])} mm`
          : "—";
      rows.push([body.id, `${round(own["volume"])} mm³, ${extent}`]);
      if (typeof own["volume"] === "number") volume += own["volume"];
    }
    rows.push(["bodies", String(bodies.length)]);
    rows.push(["total volume mm³", `${round(volume)} (summed)`]);
    return rows;
  }
  const size = (measured["size"] ?? []) as unknown[];
  return [
    ["solids", String(measured["solid_count"] ?? "—")],
    ["volume mm³", round(measured["volume"])],
    ["faces", String(measured["face_count"] ?? "—")],
    ["edges", String(measured["edge_count"] ?? "—")],
    ["size mm", size.length === 3 && size[0] !== undefined
      ? `${round(size[0])} × ${round(size[1])} × ${round(size[2])}`
      : "—"],
  ];
}

function showBuild(build: BuildResponse, plan: PlanResponse | null): void {
  const graph = build.executed_by_graph === true;
  const execution: ExecutionReport | undefined = build.execution;
  const measured = measurementOf(build);

  set(statBackend, build.backend ?? execution?.backend ?? "—",
      build.backend ? "ok" : undefined);
  set(statPath, build.execution_path ?? (graph ? "graph_executor" : "v1_document"));
  const succeeded = graph ? execution?.succeeded === true : build.build?.succeeded === true;
  set(statBuild, succeeded ? "built" : "failed", succeeded ? "ok" : "bad");

  const order = execution?.order ?? [];
  set(statLast, order.length > 0 ? order[order.length - 1] : "—");
  const bodyCount = (execution?.bodies ?? []).length;
  set(statMeasure,
      bodyCount > 1
        ? `${bodyCount} bodies — see Model`
        : summarise(measured));

  showDetails(plan ?? undefined, execution, measured);
  showModelSurface(plan ?? undefined, execution, measured);
}

// --- the copilot flow -------------------------------------------------------

/**
 * The session this browser is talking to.
 *
 * Kept in localStorage so a reload continues the same part rather than
 * silently starting a new one. It is an opaque development identifier: it
 * names nothing about the user and grants nothing.
 */
const SESSION_KEY = "cad-workspace-session";

function sessionId(): string {
  let id: string | null = null;
  try {
    id = globalThis.localStorage?.getItem(SESSION_KEY) ?? null;
  } catch {
    id = null; // private window, or storage disabled
  }
  if (!id) {
    id = `s-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
    try {
      globalThis.localStorage?.setItem(SESSION_KEY, id);
    } catch {
      /* a session that lasts one page load is still a session */
    }
  }
  return id;
}

const SESSION = sessionId();

/** Whether the next request will modify a part or create one. */
let hasModel = false;

function reflect(state: SessionState | undefined): void {
  if (!state) return;
  hasModel = state.has_model;
  undoButton.disabled = !state.can_undo;
  copilotSub.textContent = state.has_model
    ? `modifying ${state.current?.summary ?? "the current part"}`
    : "describe a part or a change";
  copilotSub.className = state.has_model ? "muted editing-note" : "muted";
}

/** Draw and describe whatever the server just built. */
function adopt(reply: SessionReply): boolean {
  const measured = (reply.measurement ?? {}) as Record<string, unknown>;
  set(statBackend, reply.backend ?? "—", reply.backend ? "ok" : undefined);
  set(statPath, reply.execution_path ?? "—");
  set(statBuild, "built", "ok");
  const order = reply.execution?.order ?? [];
  set(statLast, order.length > 0 ? order[order.length - 1] : "—");
  set(statMeasure, summarise(measured));

  const plan = reply.plan as
    | { summary?: string; operations?: readonly PlanOperation[] }
    | undefined;
  showDetails(plan, reply.execution, measured);
  showModelSurface(plan, reply.execution, measured);
  partTitle.textContent = plan?.summary || "Untitled part";
  partSub.textContent = `${plan?.operations?.length ?? 0} operation(s)`;

  // `bodies` first: it is present on every graph-executed build and covers
  // the single-body case too, so a two-body part can never fall through to
  // the single-body branch and arrive on screen missing a piece.
  if (reply.bodies && reply.bodies.length > 0) return drawBodies(reply.bodies);
  return reply.render ? draw(reply.render) : false;
}

/**
 * One conversational turn.
 *
 * Only `status === "built"` touches the viewport. Every other outcome leaves
 * the previous part exactly where it is -- that is the rule this whole
 * milestone is built around, and it is enforced here by simply not drawing.
 */
async function run(request: string): Promise<void> {
  say("you", request);
  const reply = say(
    "copilot",
    hasModel ? "working out the change…" : "interpreting the request…",
    "pending",
  );
  sendButton.disabled = true;
  undoButton.disabled = true;
  busy(hasModel ? "revising" : "building");

  try {
    const answer = await sendTurn(SESSION, request);
    reflect(answer.session);

    if (answer.status === "built") {
      const drew = adopt(answer);
      const lines = [answer.reply ?? "Done."];
      const selections = Object.entries(answer.execution?.selections ?? {});
      if (selections.length > 0) {
        lines.push(
          selections
            .map(([operation, resolution]) => selectorSentence(operation, resolution))
            .join("\n"),
        );
      }
      if (!drew) {
        lines.push("The geometry built and was measured, but no render model came back.");
      }
      reply.update(lines.join("\n\n"));

      const measured = (answer.measurement ?? {}) as Record<string, unknown>;
      const size = (measured["size"] ?? []) as unknown[];
      reply.facts([
        ["backend", answer.backend ?? "—"],
        ["path", answer.execution_path ?? "—"],
        ["volume", `${round(measured["volume"])} mm³`],
        ["faces", String(measured["face_count"] ?? "—")],
        ["edges", String(measured["edge_count"] ?? "—")],
        ["bounds", size.length === 3 && size[0] !== undefined
          ? `${round(size[0])} × ${round(size[1])} × ${round(size[2])} mm`
          : "—"],
      ]);
      return;
    }

    // Everything below left the model alone. Said plainly, and without the
    // alarming styling reserved for something actually going wrong.
    if (answer.status === "needs_clarification" || answer.status === "unsupported") {
      reply.update(answer.reply ?? "I need more information.");
      return;
    }
    reply.update(answer.reply ?? answer.error ?? "That did not work.", "error");
  } catch (error) {
    reply.update(
      error instanceof ApiError ? `${error.message} (HTTP ${error.status})` : String(error),
      "error",
    );
  } finally {
    sendButton.disabled = false;
    busy(null);
    reflect(undefined);
  }
}

undoButton.addEventListener("click", async () => {
  undoButton.disabled = true;
  sendButton.disabled = true;
  busy("undoing");
  const reply = say("copilot", "restoring the previous model…", "pending");
  try {
    const answer = await undoTurn(SESSION);
    reflect(answer.session);
    if (answer.status === "built") {
      adopt(answer);
      reply.update(answer.reply ?? "Undone.");
    } else {
      reply.update(answer.reply ?? "There is nothing to undo.");
    }
  } catch (error) {
    reply.update(String(error), "error");
  } finally {
    sendButton.disabled = false;
    busy(null);
  }
});

newPartButton.addEventListener("click", async () => {
  try {
    const answer = await resetSession(SESSION);
    reflect(answer.session);
    // The thread is kept -- a record of what was built should not vanish --
    // but the part, its evidence and its revisions are gone.
    viewer?.dispose();
    viewer = null;
    viewportEmpty.hidden = false;
    meshNote.textContent = "";
    partTitle.textContent = "Untitled part";
    partSub.textContent = "no model yet";
    for (const field of [statPath, statBuild, statLast, statMeasure]) {
      set(field, "—");
    }
    showDetails(undefined, undefined, {});
    showModelSurface(undefined, undefined, {});
    say("copilot", answer.reply ?? "Started a new part.");
  } catch (error) {
    say("copilot", String(error), "error");
  }
});

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const request = prompt.value.trim();
  if (request === "") return;
  prompt.value = "";
  void run(request);
});

prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

fitButton.addEventListener("click", () => viewer?.fit());

detailsToggle.addEventListener("click", () => {
  const open = details.hidden;
  details.hidden = !open;
  detailsToggle.setAttribute("aria-expanded", String(open));
  viewer?.resize();
});

globalThis.addEventListener("resize", () => viewer?.resize());

/**
 * The operation history, and what the part measures.
 *
 * The tree is the canonical plan's own operation list -- the first
 * constructive operation is the trunk and the modifiers hang off it, which is
 * the order the plan actually evaluates in. Selecting one highlights it and
 * names the geometry it resolved to; the renderer has no per-operation
 * picking, so this is the honest limit of "selection" here rather than a
 * fake one.
 */
function showModelSurface(
  plan: { operations?: readonly PlanOperation[] } | undefined,
  execution: ExecutionReport | undefined,
  measured: Record<string, unknown>,
): void {
  clear(opTree);
  const operations = plan?.operations ?? [];
  if (operations.length === 0) {
    const none = document.createElement("li");
    none.className = "muted";
    none.textContent = "no model yet";
    opTree.append(none);
  } else {
    operations.forEach((operation, index) => {
      const item = document.createElement("li");
      if (index > 0) item.className = "child";
      const button = document.createElement("button");
      button.type = "button";

      const name = document.createElement("span");
      name.className = "op-name";
      name.textContent = operation.id;

      const kind = document.createElement("span");
      kind.className = "op-kind";
      kind.textContent = operation.type;

      const params = document.createElement("span");
      params.className = "op-params";
      params.textContent = describeParameters(operation);

      button.append(name, kind, params);
      button.addEventListener("click", () => {
        for (const other of opTree.querySelectorAll("button")) {
          other.classList.remove("is-selected");
        }
        button.classList.add("is-selected");
        const resolution = execution?.selections?.[operation.id];
        say(
          "copilot",
          resolution
            ? `${operation.id} (${operation.type}) — the selector named ` +
              `${resolution.indices?.length ?? 0} of ` +
              `${resolution.candidates?.length ?? 0} candidate edge(s).`
            : `${operation.id} (${operation.type}) — ${describeParameters(operation) || "no parameters"}.`,
        );
      });
      item.append(button);
      opTree.append(item);
    });
  }

  const size = (measured["size"] ?? []) as unknown[];
  pairs(
    inspect,
    Object.keys(measured).length === 0
      ? []
      : [
          ["width mm", size.length === 3 ? round(size[0]) : "—"],
          ["depth mm", size.length === 3 ? round(size[1]) : "—"],
          ["height mm", size.length === 3 ? round(size[2]) : "—"],
          ["volume mm³", round(measured["volume"])],
          ["faces", String(measured["face_count"] ?? "—")],
          ["edges", String(measured["edge_count"] ?? "—")],
          ["solids", String(measured["solid_count"] ?? "—")],
        ],
    "build a part to measure it",
  );
  inspectSource.textContent =
    Object.keys(measured).length === 0
      ? ""
      : "Measured by the CAD engine on the current build — not estimated.";
  exportStepButton.disabled = operations.length === 0;
  exportStlButton.disabled = operations.length === 0;
  pairs(
    partsCurrent,
    operations.length === 0 ? [] : [
      ["operations", String(operations.length)],
      ["volume mm³", round(measured["volume"])],
      ["size mm", (measured["size"] as unknown[] | undefined)?.length === 3
        ? `${round((measured["size"] as unknown[])[0])} × ${round((measured["size"] as unknown[])[1])} × ${round((measured["size"] as unknown[])[2])}`
        : "—"],
    ],
    "no part built yet",
  );
}

/** A short, human-readable summary of one operation's parameters. */
function describeParameters(operation: PlanOperation): string {
  const p = (operation.parameters ?? {}) as Record<string, unknown>;
  const bits: string[] = [];
  if (typeof p["x"] === "number") bits.push(`${p["x"]}×${p["y"]}×${p["z"]} mm`);
  if (typeof p["diameter"] === "number") bits.push(`Ø${p["diameter"]} mm`);
  if (typeof p["radius"] === "number") bits.push(`R${p["radius"]}`);
  if (typeof p["distance"] === "number") bits.push(`${p["distance"]} mm`);
  if (typeof p["count"] === "number") bits.push(`×${p["count"]}`);
  const position = p["position"] as Record<string, unknown> | undefined;
  if (position && typeof position["x"] === "number") {
    bits.push(`at (${position["x"]}, ${position["y"]})`);
  }
  const edges = p["edges"] as Record<string, unknown> | undefined;
  if (edges) {
    bits.push(`${edges["select"]}${edges["axis"] ? `/${edges["axis"]}` : ""}${edges["position"] ? `/${edges["position"]}` : ""}`);
  }
  return bits.join("  ");
}


// --- product surfaces -------------------------------------------------------
//
// Each reads the session's current part from the server. None of them keeps
// CAD state of its own.

const makeDrawing = need<HTMLButtonElement>("make-drawing");
const drawingNote = need<HTMLParagraphElement>("drawing-note");
const drawingFacts = need<HTMLDListElement>("drawing-facts");
const drawingSheet = need<HTMLDivElement>("drawing-sheet");

const macroName = need<HTMLInputElement>("macro-name");
const macroText = need<HTMLInputElement>("macro-text");
const macroCreate = need<HTMLButtonElement>("macro-create");
const macroNote = need<HTMLParagraphElement>("macro-note");
const macroList = need<HTMLUListElement>("macro-list");
const macroActions = need<HTMLDListElement>("macro-actions");

const engQuestion = need<HTMLInputElement>("eng-question");
const engAsk = need<HTMLButtonElement>("eng-ask");
const engReport = need<HTMLButtonElement>("eng-report");
const engNote = need<HTMLParagraphElement>("eng-note");
const engFindings = need<HTMLUListElement>("eng-findings");

const finderQuery = need<HTMLInputElement>("finder-query");
const finderSearch = need<HTMLButtonElement>("finder-search");
const finderNote = need<HTMLParagraphElement>("finder-note");
const finderResults = need<HTMLUListElement>("finder-results");

const exportStlButton = need<HTMLButtonElement>("export-stl");
const partsCurrent = need<HTMLDListElement>("parts-current");

/** One finding, with its provenance shown rather than implied. */
function findingItem(finding: Finding): HTMLLIElement {
  const item = document.createElement("li");
  const label = document.createElement("div");
  label.className = "finding-label";
  label.textContent = finding.label;
  const badge = document.createElement("span");
  badge.className = `badge badge-${finding.kind}`;
  badge.textContent = finding.kind;
  label.append(badge);

  const value = document.createElement("div");
  value.className = "finding-value";
  value.textContent = finding.value;
  item.append(label, value);

  if (finding.working) {
    const working = document.createElement("div");
    working.className = "finding-working";
    working.textContent = finding.working;
    item.append(working);
  }
  return item;
}

// --- drawings ---------------------------------------------------------------

makeDrawing.addEventListener("click", async () => {
  makeDrawing.disabled = true;
  drawingNote.textContent = "projecting views…";
  try {
    const reply = await createDrawing(SESSION);
    const sheet = reply["drawing"] as Record<string, any>;
    // The SVG is built by the server from real projections. It is inserted
    // as markup because it IS a drawing; it carries no script and comes from
    // this application's own renderer, not from a model.
    drawingSheet.innerHTML = String(sheet["svg"] ?? "");
    pairs(
      drawingFacts,
      [
        ["part", String(sheet["part_name"])],
        ["scale", Number(sheet["scale"]) >= 1
          ? `${sheet["scale"]}:1` : `1:${1 / Number(sheet["scale"])}`],
        ["units", String(sheet["units"])],
        ["engine", String(sheet["backend"])],
        ["views", (sheet["views"] as unknown[]).map(
          (v) => (v as Record<string, unknown>)["name"]).join(", ")],
      ],
      "—",
    );
    const notes = (sheet["notes"] ?? []) as string[];
    drawingNote.textContent = notes.length
      ? `Drawn. ${notes.join("; ")}`
      : "Drawn from the current part. Dimensions are measured, not estimated.";
  } catch (error) {
    drawingSheet.textContent = "";
    drawingNote.textContent =
      error instanceof ApiError ? error.message : String(error);
  } finally {
    makeDrawing.disabled = false;
  }
});

// --- engineering ------------------------------------------------------------

async function engineering(question?: string): Promise<void> {
  engNote.textContent = "reading the evidence…";
  clear(engFindings);
  try {
    const reply = await askEngineering(SESSION, question);
    const findings = (reply["findings"] ?? []) as Finding[];
    if (!reply["answered"] || findings.length === 0) {
      engNote.textContent = String(reply["reply"] ?? "Nothing to report.");
      return;
    }
    for (const finding of findings) engFindings.append(findingItem(finding));
    const measured = findings.filter((f) => f.kind === "measured").length;
    engNote.textContent =
      `${measured} measured by the CAD engine, ` +
      `${findings.length - measured} calculated from them.`;
  } catch (error) {
    engNote.textContent =
      error instanceof ApiError ? error.message : String(error);
  }
}

engAsk.addEventListener("click", () => void engineering(engQuestion.value.trim()));
engReport.addEventListener("click", () => void engineering());
engQuestion.addEventListener("keydown", (event) => {
  if (event.key === "Enter") void engineering(engQuestion.value.trim());
});

// --- part finder ------------------------------------------------------------

async function findParts(): Promise<void> {
  const text = finderQuery.value.trim();
  if (text === "") return;
  clear(finderResults);
  finderNote.textContent = "searching the local catalogue…";
  try {
    const reply = await searchCatalog(text);
    const results = (reply["results"] ?? []) as Record<string, any>[];
    if (results.length === 0) {
      finderNote.textContent = `Nothing in the catalogue matches that. ${reply["note"]}`;
      return;
    }
    for (const record of results) {
      const item = document.createElement("li");
      const label = document.createElement("div");
      label.className = "finding-label";
      label.textContent = `${record["part_number"]} · ${record["category"]}` +
        (record["standard"] ? ` · ${record["standard"]}` : "");
      const value = document.createElement("div");
      value.className = "finding-value";
      value.textContent = String(record["description"]);
      const dims = document.createElement("div");
      dims.className = "finding-working";
      dims.textContent = Object.entries(record["dimensions"] ?? {})
        .map(([k, v]) => `${k}=${v}`).join("  ");
      item.append(label, value, dims);
      finderResults.append(item);
    }
    // Said every time: this is a local table, never a supplier.
    finderNote.textContent = `${reply["total"]} match(es) · ${reply["note"]}`;
  } catch (error) {
    finderNote.textContent =
      error instanceof ApiError ? error.message : String(error);
  }
}

finderSearch.addEventListener("click", () => void findParts());
finderQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") void findParts();
});

// --- macros -----------------------------------------------------------------

async function refreshMacros(): Promise<void> {
  try {
    const reply = await listMacros(SESSION);
    const macros = (reply["macros"] ?? []) as Record<string, any>[];
    clear(macroList);
    if (macros.length === 0) {
      const none = document.createElement("li");
      none.className = "muted";
      none.textContent = "no macros yet";
      macroList.append(none);
    }
    for (const macro of macros) {
      const item = document.createElement("li");
      const run = document.createElement("button");
      run.type = "button";
      const name = document.createElement("span");
      name.className = "op-name";
      name.textContent = String(macro["name"]);
      const steps = document.createElement("span");
      steps.className = "op-params";
      steps.textContent = (macro["steps"] as Record<string, any>[])
        .map((s) => s["action"]).join(" → ");
      run.append(name, steps);
      run.addEventListener("click", async () => {
        macroNote.textContent = `running ${macro["name"]}…`;
        try {
          const result = await runMacro(SESSION, String(macro["name"]));
          const done = (result["steps"] ?? []) as Record<string, any>[];
          macroNote.textContent = done
            .map((s) => `${s["action"]}: ${s["ok"] ? (s["bytes"] ? `${s["bytes"]} bytes` : "ok") : s["error"]}`)
            .join(" · ");
        } catch (error) {
          macroNote.textContent =
            error instanceof ApiError ? error.message : String(error);
        }
      });
      item.append(run);
      macroList.append(item);
    }
    pairs(macroActions,
      Object.entries((reply["actions"] ?? {}) as Record<string, string>),
      "—");
  } catch {
    /* the panel simply stays as it was */
  }
}

macroCreate.addEventListener("click", async () => {
  const name = macroName.value.trim();
  const text = macroText.value.trim();
  if (name === "") { macroNote.textContent = "give the macro a name"; return; }
  macroCreate.disabled = true;
  try {
    const reply = await createMacro(SESSION, name, text);
    const macro = reply["macro"] as Record<string, any>;
    macroNote.textContent =
      `Created "${macro["name"]}" with ` +
      `${(macro["steps"] as unknown[]).length} action(s).`;
    macroName.value = "";
    macroText.value = "";
    await refreshMacros();
  } catch (error) {
    // A request whose words matched no known action is refused, and says so
    // rather than storing an empty macro.
    macroNote.textContent =
      error instanceof ApiError ? error.message : String(error);
  } finally {
    macroCreate.disabled = false;
  }
});

// --- STL, alongside STEP ----------------------------------------------------

async function download(format: "step" | "stl"): Promise<void> {
  const button = format === "step" ? exportStepButton : exportStlButton;
  button.disabled = true;
  exportNote.textContent = `exporting ${format.toUpperCase()}…`;
  try {
    const blob = await exportPart(SESSION, format);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `part.${format}`;
    link.click();
    URL.revokeObjectURL(url);
    exportNote.textContent =
      `Exported ${(blob.size / 1024).toFixed(1)} kB of ${format.toUpperCase()}.`;
  } catch (error) {
    exportNote.textContent =
      error instanceof ApiError ? error.message : String(error);
  } finally {
    button.disabled = false;
  }
}

exportStlButton.addEventListener("click", () => void download("stl"));

exportStepButton.addEventListener("click", () => void download("step"));

// --- surfaces ---------------------------------------------------------------


const panels: Record<string, HTMLElement> = {
  copilot: need<HTMLElement>("panel-copilot"),
  model: need<HTMLElement>("panel-model"),
  drawings: need<HTMLElement>("panel-drawings"),
  macros: need<HTMLElement>("panel-macros"),
  engineering: need<HTMLElement>("panel-engineering"),
  finder: need<HTMLElement>("panel-finder"),
  parts: need<HTMLElement>("panel-parts"),
  settings: need<HTMLElement>("panel-settings"),
};

function showSurface(name: string): void {
  for (const panel of Object.values(panels)) panel.hidden = true;
  (panels[name] ?? panels["copilot"]).hidden = false;
  if (name === "macros") void refreshMacros();
  viewer?.resize();
}

for (const button of document.querySelectorAll<HTMLButtonElement>(".rail-item")) {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll(".rail-item")) {
      other.classList.remove("is-active");
    }
    button.classList.add("is-active");
    showSurface(button.dataset["surface"] ?? "copilot");
  });
}

// --- environment ------------------------------------------------------------

async function connect(): Promise<void> {
  try {
    const status = await health();
    const backend = status.backend;
    const engine = backend?.name ?? "unknown";
    const reachable = backend?.available !== false;

    healthDot.classList.add(reachable ? "is-ok" : "is-bad");
    healthLabel.textContent = `${engine}${backend?.version ? ` ${backend.version}` : ""}`;

    set(statBackend, engine, reachable ? "ok" : "bad");
    set(statModel, status.model_configured ? status.model : "no model configured",
        status.model_configured ? undefined : "warn");

    pairs(settingsEnv, [
      ["backend", `${engine}${backend?.version ? ` ${backend.version}` : ""}`],
      ["backend available", String(backend?.available ?? "unknown")],
      ["model", status.model],
      ["model configured", String(status.model_configured)],
      ["prompt", status.prompt_version],
      ["V1 document path", String(status.v1_document_path_available ?? "unknown")],
    ], "—");

    if (!status.model_configured) {
      say("copilot",
        "No model credential is configured here, so I cannot interpret a request. The viewport and the build path still work from a plan.",
        "error");
    }
  } catch (error) {
    healthDot.classList.add("is-bad");
    healthLabel.textContent = "API unreachable";
    set(statBackend, "unreachable", "bad");
    say("copilot", `The experimental API is not reachable: ${String(error)}`, "error");
  }
}

/** Developer fixtures, kept out of the product flow and behind Settings. */
async function loadFixtures(): Promise<void> {
  try {
    const listing = await localFixtures();
    for (const fixture of listing.fixtures) {
      const option = document.createElement("option");
      option.value = fixture.name;
      option.textContent = `${fixture.name} — ${fixture.description}`;
      fixtureSelect.append(option);
    }
    localStatus.textContent = `${listing.fixtures.length} fixtures · ${listing.source}`;
  } catch {
    localStatus.textContent = "fixtures unavailable";
  }
}

runLocalButton.addEventListener("click", async () => {
  const name = fixtureSelect.value;
  if (name === "") return;
  runLocalButton.disabled = true;
  localStatus.textContent = "running…";
  try {
    const result = await runLocalFixture(name);
    // Stamped, always: a fixture is never a model result and the page must
    // not let one be mistaken for one.
    localStatus.textContent = `${result.source} · is_live_model_result=${String(result.is_live_model_result)}`;
    showBuild(result, null);
    if (result.render) draw(result.render);
    partTitle.textContent = name;
    partSub.textContent = "developer fixture — not a model result";
  } catch (error) {
    localStatus.textContent = String(error);
  } finally {
    runLocalButton.disabled = false;
  }
});

say("copilot",
  "Describe a part and I will build it. For example: a 100 × 60 × 10 mm plate with an 8 mm through hole at x=10, y=10, then fillet the four vertical edges by 2 mm.");

void connect();
void loadFixtures();
