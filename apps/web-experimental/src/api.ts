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
  readonly parameters: Readonly<Record<string, unknown>>;
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
  readonly build: {
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
}

/** A failed request, carrying whatever the server said. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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
    const record = payload as { error?: string } | null;
    throw new ApiError(
      record?.error ?? `request failed with ${response.status}`,
      response.status,
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

export interface Health {
  readonly status: string;
  readonly model: string;
  readonly model_configured: boolean;
  readonly build_available: boolean;
  readonly prompt_version: string;
}

export async function health(): Promise<Health> {
  const response = await fetch(`${API_BASE}/experimental/health`);
  if (!response.ok) {
    throw new ApiError("the experimental API is not reachable", response.status);
  }
  return (await response.json()) as Health;
}

export type { RenderModel };
