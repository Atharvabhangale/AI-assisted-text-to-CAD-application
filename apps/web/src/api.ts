/**
 * The HTTP client. A thin wrapper over the existing API, and nothing else.
 *
 * No CAD logic lives here: no validation, no geometry, no measurement, no
 * hashing and no artifact path building. Every field this module returns is a
 * field the backend sent, and every URL is built from an identifier the
 * backend gave us.
 */

import type { RenderModel } from "./render-model";

/**
 * Where the API is. A path, not an origin: the dev server proxies `/api` to
 * the backend, so the page and its API calls are the same origin and no CORS
 * is involved. Overridable at build time with `VITE_API_BASE`.
 */
export const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "/api";

/** The outputs this viewer asks a build for. */
export const REQUESTED_OUTPUTS = [
  "geometry",
  "step",
  "iges",
  "stl",
  "render",
] as const;

/** The artifact kinds that can be downloaded (Stage 23). */
export const DOWNLOADABLE_KINDS = ["step", "iges", "stl"] as const;
export type DownloadableKind = (typeof DOWNLOADABLE_KINDS)[number];

export interface ValidationErrorRecord {
  readonly rule: string;
  readonly message: string;
  readonly feature_id: string | null;
  readonly field_path: string | null;
  readonly feature_index: number | null;
}

export interface ErrorRecord {
  readonly failure: string;
  readonly message: string;
  readonly output: string | null;
  readonly rule_codes: readonly string[];
  readonly validation_errors: readonly ValidationErrorRecord[];
}

export interface ArtifactRecord {
  readonly kind: string;
  readonly format: string;
  readonly logical_id: string;
  readonly storage: string;
  readonly size_bytes: number | null;
  readonly checksum: string | null;
  readonly checksum_algorithm: string | null;
  readonly measurements: Record<string, unknown>;
}

export interface BuildResponse {
  readonly status: string;
  readonly succeeded: boolean;
  readonly document_hash: string | null;
  readonly build_key: string | null;
  readonly execution_id: string | null;
  readonly cache_hit: boolean;
  readonly outputs: readonly string[];
  readonly artifacts: readonly ArtifactRecord[];
  readonly error: ErrorRecord | null;
}

export interface ValidateResponse {
  readonly valid: boolean;
  readonly document_hash: string | null;
  readonly name: string | null;
  readonly feature_count: number | null;
  readonly document: Record<string, unknown> | null;
  readonly error: ErrorRecord | null;
}

/**
 * The five AI outcomes, exactly as the backend's own taxonomy names them.
 *
 * Not a second taxonomy: these strings are the backend's
 * `GenerationOutcome` values, and the page branches on them rather than on
 * anything it decides for itself.
 */
export const GENERATION_OUTCOMES = [
  "generated",
  "needs_clarification",
  "unsupported",
  "model_error",
  "invalid_model_output",
] as const;
export type GenerationOutcome = (typeof GENERATION_OUTCOMES)[number];

/**
 * One model call the backend made while answering a `POST /generate`.
 *
 * The backend checks each answer itself and may ask the model again; this is
 * its own record of how that went. The page relays it and decides nothing
 * from it: `stage` says where the backend turned an answer down, and
 * `rule_codes` and `issues` are the backend's reasons, shown verbatim.
 */
export interface GenerationAttempt {
  /** 1-based, in the order the calls were made. */
  readonly number: number;
  /** True only for the attempt whose document the backend accepted. */
  readonly accepted: boolean;
  /**
   * Where a rejected attempt was rejected. `null` when it was accepted, or
   * when the answer was a clarification or a refusal rather than a document.
   */
  readonly stage: "answer" | "validation" | "geometry" | null;
  readonly rule_codes: readonly string[];
  readonly issues: readonly string[];
}

/**
 * `POST /generate`'s answer.
 *
 * `document` is a CAD document the backend's validator **already accepted**.
 * The page never inspects it for validity, never repairs it and never edits
 * it: it shows it, and sends it back for building unchanged.
 *
 * `assumptions` and `attempts` are optional because an older backend sends
 * neither. A missing one means "none reported" and is read as an empty list.
 */
export interface GenerateResponse {
  readonly outcome: GenerationOutcome;
  readonly message: string;
  readonly document: Record<string, unknown> | null;
  readonly document_hash: string | null;
  readonly summary: string | null;
  readonly questions: readonly string[];
  readonly issues: readonly string[];
  readonly rule_codes: readonly string[];
  readonly error_kind: string | null;
  /** Choices the interpreter made that the description did not state. */
  readonly assumptions?: readonly string[] | null;
  /** One entry per model call made for this request, in order. */
  readonly attempts?: readonly GenerationAttempt[] | null;
}

/**
 * Which interpreter the next `POST /generate` uses, as the backend reports it.
 *
 * Every string here is the backend's: the provider names are opaque
 * identifiers, the labels and reasons are shown verbatim, and the page decides
 * nothing from them except which option is selected.
 */
export interface EffectiveSettings {
  readonly provider: string | null;
  readonly label: string | null;
  readonly model: string | null;
  /** `"saved"` (chosen on this page) or `"environment"` (from `.env`). */
  readonly source: string;
  readonly ready: boolean;
  readonly reason_if_not: string | null;
}

/** One interpreter the backend can use, and whether its setup is present. */
export interface ProviderOption {
  readonly name: string;
  readonly label: string;
  readonly kind: string;
  readonly available: boolean;
  readonly reason_if_not: string | null;
  /** What an empty Model field means for this provider. */
  readonly default_model: string;
  readonly model_required: boolean;
  readonly model_suggestions: readonly string[];
  /** Variable *names* to set in `.env`. Never a value. */
  readonly env_variables: readonly string[];
}

/** `GET /settings`, and the success answer of `PUT` / `DELETE /settings`. */
export interface SettingsResponse {
  readonly effective: EffectiveSettings;
  readonly environment: EffectiveSettings;
  readonly saved: { readonly provider: string; readonly model: string } | null;
  readonly providers: readonly ProviderOption[];
  readonly notes: readonly string[];
}

/** A refused settings change: the backend's reason and message. */
export interface SettingsRefusal {
  readonly error: {
    readonly message: string;
    readonly reason?: string;
    readonly failure?: string;
    readonly field?: string | null;
  };
}

/** A request that never reached the API, or an answer that was not JSON. */
export class NetworkError extends Error {}

export interface ApiClientOptions {
  readonly base?: string;
  readonly fetch?: typeof globalThis.fetch;
}

/** The API, as this page uses it: seven calls and one URL helper. */
// (generate, validate, build, renderModel, and the three settings calls --
// plus `artifactUrl`.)
export class ApiClient {
  private readonly base: string;
  private readonly request: typeof globalThis.fetch;

  constructor(options: ApiClientOptions = {}) {
    this.base = options.base ?? API_BASE;
    this.request = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /**
   * `POST /generate`: one description in, one interpretation out.
   *
   * The description is sent verbatim. This client does not pre-process it,
   * does not attach history, and makes exactly one request per call -- there
   * is no retry here and no conversation state anywhere. Whatever re-asking
   * happens is the backend's, inside that one request, and comes back
   * described in `attempts`.
   */
  async generate(text: string): Promise<GenerateResponse> {
    return this.json<GenerateResponse>("/generate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  }

  /** `POST /validate`. */
  async validate(document: unknown): Promise<ValidateResponse> {
    return this.json<ValidateResponse>("/validate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ document }),
    });
  }

  /** `POST /build`, asking for every output this viewer can use. */
  async build(
    document: unknown,
    outputs: readonly string[] = REQUESTED_OUTPUTS,
  ): Promise<BuildResponse> {
    return this.json<BuildResponse>("/build", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ document, outputs: [...outputs] }),
    });
  }

  /**
   * `GET /builds/{build_key}/render`.
   *
   * The render model comes back as the backend's own canonical JSON; this
   * method parses it and nothing more.
   */
  async renderModel(buildKey: string): Promise<RenderModel> {
    return this.json<RenderModel>(
      `/builds/${encodeURIComponent(buildKey)}/render`,
      { method: "GET" },
    );
  }

  /**
   * `GET /settings`: the interpreter in use and every one the backend offers.
   * Reads only; costs no model call.
   */
  async settings(): Promise<SettingsResponse> {
    return this.json<SettingsResponse>("/settings", { method: "GET" });
  }

  /**
   * `PUT /settings`: choose the interpreter for the next generation.
   *
   * Exactly two fields, both chosen by the person on the page. Nothing else
   * can be sent: keys, addresses and executable locations live in the
   * backend's `.env`, and the backend refuses any other field.
   */
  async saveSettings(
    provider: string,
    model: string,
  ): Promise<SettingsResponse | SettingsRefusal> {
    return this.json<SettingsResponse | SettingsRefusal>("/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider, model }),
    });
  }

  /** `DELETE /settings`: forget the page's choice; `.env` applies again. */
  async resetSettings(): Promise<SettingsResponse | SettingsRefusal> {
    return this.json<SettingsResponse | SettingsRefusal>("/settings", {
      method: "DELETE",
    });
  }

  /**
   * The download URL for an artifact, from the **logical id** the build
   * returned. No path is constructed and no filename is guessed.
   */
  artifactUrl(logicalId: string): string {
    return `${this.base}/artifacts/${encodeURIComponent(logicalId)}`;
  }

  private async json<T>(path: string, init: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await this.request(`${this.base}${path}`, init);
    } catch (cause) {
      throw new NetworkError("the API could not be reached");
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch (cause) {
      throw new NetworkError("the API returned a response that is not JSON");
    }
    return payload as T;
  }
}
