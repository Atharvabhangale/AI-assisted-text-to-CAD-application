/**
 * The experimental API client. Four calls, and no CAD logic.
 *
 * Separate from `apps/web/src/api.ts`: this talks to the experimental
 * endpoints on 8001, and coupling the two clients would let a change to one
 * application reach the other.
 */

import type { RenderModel } from "../../web/src/render-model";

export const API_BASE: string =
  (import.meta.env?.VITE_EXPERIMENTAL_API_BASE as string | undefined) ?? "/api";

export interface PlanOperation {
  readonly id: string;
  readonly type: string;
  /** Present on a modifier (through_hole, subtract): the solid it acts on. */
  readonly target?: string;
  /** Present on a subtract: the solids it removes, and consumes, in order. */
  readonly tools?: readonly string[];
  /** Absent on a subtract, which has no parameters. */
  readonly parameters?: Readonly<Record<string, unknown>>;
}

export interface PlanResponse {
  readonly status: string;
  readonly operations: readonly PlanOperation[];
  readonly summary: string;
  readonly reason?: string;
  readonly questions?: readonly string[];
  readonly error?: string;
  readonly metadata?: Readonly<Record<string, unknown>>;
}

export interface PlanProblem {
  readonly code: string;
  readonly message: string;
  readonly where: string;
}

export interface ValidateResponse {
  readonly valid: boolean;
  readonly parsed: boolean;
  readonly problems: readonly PlanProblem[];
}

export interface ArtifactRecord {
  readonly kind: string;
  readonly logical_id?: string;
  readonly details?: Readonly<Record<string, unknown>>;
}

export interface BuildResponse {
  readonly build?: {
    readonly succeeded: boolean;
    readonly build_key?: string | null;
    readonly document_hash?: string | null;
    readonly cache_hit?: boolean;
    readonly outputs?: readonly string[];
    readonly error?: { readonly message?: string } | null;
    /** Artifacts live on the manifest -- the manifest describes the build. */
    readonly manifest?: {
      readonly artifacts?: readonly ArtifactRecord[];
    } | null;
  };
  readonly render?: unknown;
  readonly document?: unknown;
  readonly error?: string;
  readonly problems?: readonly PlanProblem[];
  /**
   * Set when the plan is sound and this backend has no execution path for
   * it. Distinct from `error` on purpose: the plan is not wrong. The server
   * answers 501 on the build route, so the client reads it off the thrown
   * error's body rather than a success payload.
   */
  readonly execution_unsupported?: boolean;
  readonly unsupported_types?: readonly string[];
  readonly unsupported_operations?: readonly string[];
  /**
   * Set when the graph-driven executor built the plan rather than a V1
   * document -- which happens exactly when a selector is richer than
   * Section C.7 can express: a `straight`, a `circular`, or a rim's
   * `position`. Such a build has **no `build` and no `document`** by design,
   * so a reader that tests `build` alone concludes a successful build
   * failed. That is precisely what this page did.
   */
  readonly executed_by_graph?: boolean;
  readonly execution?: ExecutionReport;
  /**
   * Which engine built this, and by which route. Sent on both paths so the
   * page never has to infer the backend from which fields are present -- a
   * build whose engine had to be guessed is one nobody can attribute.
   */
  readonly backend?: string;
  readonly execution_path?: string;
}

/** The executor's own report. Measurements and selections, never a shape. */
export interface ExecutionReport {
  readonly succeeded: boolean;
  readonly backend: string;
  readonly order: readonly string[];
  readonly bodies: readonly {
    readonly id: string;
    readonly features: readonly string[];
    readonly measurement?: Readonly<Record<string, unknown>> | null;
  }[];
  readonly failure?: {
    readonly code?: string;
    readonly message?: string;
    readonly operation?: string;
  } | null;
  /** One resolution per selector-bearing operation, keyed by operation id. */
  readonly selections?: Readonly<
    Record<
      string,
      {
        readonly indices?: readonly number[];
        readonly candidates?: readonly number[];
        readonly seams?: readonly number[];
        readonly code?: string | null;
        readonly message?: string;
      }
    >
  >;
}

/** A failed request, carrying whatever the server said. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /**
     * The decoded error body, when there was one. Carried so a caller can
     * tell a *kind* of failure apart from its prose -- an unexecutable plan
     * arrives as 501 with `execution_unsupported`, and reading that off a
     * message string would be guessing.
     */
    readonly body: unknown = null,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError("the server did not return JSON", response.status);
  }
  if (!response.ok) {
    const record = payload as
      | { error?: string; execution_unsupported?: boolean }
      | null;
    throw new ApiError(
      record?.error ?? `request failed with ${response.status}`,
      response.status,
      payload,
    );
  }
  return payload as T;
}

export function generatePlan(text: string): Promise<PlanResponse> {
  return post<PlanResponse>("/experimental/generate-plan", { text });
}

export function validatePlan(plan: unknown): Promise<ValidateResponse> {
  return post<ValidateResponse>("/experimental/validate-plan", { plan });
}

export function buildPlan(plan: unknown): Promise<BuildResponse> {
  return post<BuildResponse>("/experimental/build-plan", { plan });
}

export interface FixtureRecord {
  readonly name: string;
  readonly description: string;
  readonly expected_bounding_box: Readonly<Record<string, number>>;
  readonly expected_volume_mm3: number;
}

/** Every local-development response carries these. Never a model result. */
export interface LocalStamp {
  readonly source: string;
  readonly is_live_model_result: boolean;
  readonly note: string;
}

export interface LocalPlanResponse extends LocalStamp, BuildResponse {
  readonly parsed: boolean;
  readonly plan_valid: boolean;
  readonly plan?: {
    readonly status: string;
    readonly summary: string;
    readonly operations: readonly PlanOperation[];
  };
  readonly problems?: readonly PlanProblem[];
  readonly built?: boolean;
}

export async function localFixtures(): Promise<
  LocalStamp & { readonly fixtures: readonly FixtureRecord[] }
> {
  const response = await fetch(`${API_BASE}/experimental/local-plan/fixtures`);
  if (!response.ok) {
    throw new ApiError("the fixtures could not be listed", response.status);
  }
  return (await response.json()) as LocalStamp & {
    readonly fixtures: readonly FixtureRecord[];
  };
}

export function runLocalFixture(name: string): Promise<LocalPlanResponse> {
  return post<LocalPlanResponse>("/experimental/local-plan", {
    fixture: name,
  });
}

export interface Health {
  readonly status: string;
  readonly model: string;
  readonly model_configured: boolean;
  readonly build_available: boolean;
  readonly prompt_version: string;
  readonly local_development_plan_available?: boolean;
  readonly local_development_label?: string;
  /**
   * Which engine this deployment would actually execute on. Reported by the
   * API from its own resolver, so the workspace displays the backend rather
   * than inferring or assuming one.
   */
  readonly backend?: {
    readonly name: string | null;
    readonly available: boolean;
    readonly version?: string;
    readonly error?: string;
  };
  readonly v1_document_path_available?: boolean;
}

export async function health(): Promise<Health> {
  const response = await fetch(`${API_BASE}/experimental/health`);
  if (!response.ok) {
    throw new ApiError("the experimental API is not reachable", response.status);
  }
  return (await response.json()) as Health;
}

// --- the multi-turn copilot session ----------------------------------------
//
// One call per turn. The server holds the current Operation Plan, so the page
// never sends the part back in order to change it -- and never holds a second
// copy of CAD state that could drift from the one that builds.

export interface SessionTurn {
  readonly role: string;
  readonly text: string;
  readonly at: number;
}

export interface SessionState {
  readonly session_id: string;
  readonly has_model: boolean;
  readonly can_undo: boolean;
  readonly revisions: number;
  readonly conversation: readonly SessionTurn[];
  readonly current: {
    readonly summary: string;
    readonly request: string;
    readonly backend: string;
    readonly measurement: Readonly<Record<string, unknown>>;
  } | null;
}

/**
 * One turn's answer.
 *
 * `status` says what happened, and only `"built"` carries geometry. Every
 * other status means the part was left exactly as it was -- which is the
 * whole point: a refused edit must never blank the viewport.
 */
export interface SessionReply {
  readonly status:
    | "built"
    | "needs_clarification"
    | "unsupported"
    | "invalid_plan"
    | "invalid_model_output"
    | "execution_unsupported"
    | "build_failed"
    | "nothing_to_undo"
    | "reset"
    | "unavailable";
  readonly reply?: string;
  readonly editing?: boolean;
  readonly session_id: string;
  readonly session: SessionState;
  readonly questions?: readonly string[];
  readonly plan?: unknown;
  readonly backend?: string;
  readonly execution_path?: string;
  readonly measurement?: Readonly<Record<string, unknown>>;
  readonly execution?: ExecutionReport;
  readonly render?: unknown;
  readonly failure?: { readonly message?: string; readonly operation?: string } | null;
  readonly problems?: readonly PlanProblem[];
  readonly error?: string;
  readonly metadata?: Readonly<Record<string, unknown>>;
}

export function sendTurn(sessionId: string, text: string): Promise<SessionReply> {
  return post<SessionReply>("/experimental/session/message", {
    session_id: sessionId,
    text,
  });
}

export function undoTurn(sessionId: string): Promise<SessionReply> {
  return post<SessionReply>("/experimental/session/undo", { session_id: sessionId });
}

export function resetSession(sessionId: string): Promise<SessionReply> {
  return post<SessionReply>("/experimental/session/reset", { session_id: sessionId });
}

export function sessionState(sessionId: string): Promise<SessionReply> {
  return post<SessionReply>("/experimental/session/state", { session_id: sessionId });
}

/** Download the current part. Throws if there is nothing built. */
export async function exportPart(
  sessionId: string,
  format: "step" | "stl",
): Promise<Blob> {
  const response = await fetch(`${API_BASE}/experimental/session/export`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, format }),
  });
  if (!response.ok) {
    let message = `export failed with ${response.status}`;
    try {
      message = ((await response.json()) as { error?: string }).error ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, response.status);
  }
  return response.blob();
}

// --- product surfaces -------------------------------------------------------

export interface Finding {
  readonly label: string;
  readonly value: string;
  readonly kind: "measured" | "calculated";
  readonly working?: string | null;
}

export function createDrawing(sessionId: string): Promise<Record<string, any>> {
  return post("/experimental/session/drawing", { session_id: sessionId });
}

export function askEngineering(
  sessionId: string,
  text?: string,
): Promise<Record<string, any>> {
  return post("/experimental/session/engineering",
              { session_id: sessionId, text: text ?? null });
}

export function searchCatalog(text: string): Promise<Record<string, any>> {
  return post("/experimental/catalog/search", { text });
}

export function listMacros(sessionId: string): Promise<Record<string, any>> {
  return post("/experimental/session/macros", { session_id: sessionId });
}

export function createMacro(
  sessionId: string, name: string, text: string,
): Promise<Record<string, any>> {
  return post("/experimental/session/macros",
              { session_id: sessionId, name, text, description: text });
}

export function runMacro(sessionId: string, name: string): Promise<Record<string, any>> {
  return post("/experimental/session/macros/run", { session_id: sessionId, name });
}

export type { RenderModel };
