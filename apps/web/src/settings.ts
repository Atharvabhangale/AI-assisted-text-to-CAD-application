/**
 * The Settings panel: which interpreter the next generation uses.
 *
 * A client of `GET` / `PUT` / `DELETE /settings`, and nothing more. The
 * backend decides which providers exist, what each is called, whether its
 * setup is present, which models to suggest and whether a model name is
 * acceptable; this module shows those answers and sends the two values the
 * person chose. It names no provider, holds no key and computes nothing --
 * a test asserts the provider names never appear in this source.
 *
 * Nothing here can generate: saving settings costs no model call.
 */

import { NetworkError } from "./api";
import type {
  ApiClient,
  EffectiveSettings,
  ProviderOption,
  SettingsRefusal,
  SettingsResponse,
} from "./api";

/** Where the backend reads keys, addresses and executable locations. */
export const ENV_FILE = "apps\\api\\.env";

/** The element ids the panel uses. Declared once, like `ELEMENT_IDS`. */
export const SETTINGS_IDS = {
  button: "settings-button",
  panel: "settings-panel",
  provider: "settings-provider",
  providerStatus: "settings-provider-status",
  envHelp: "settings-env-help",
  model: "settings-model",
  modelOptions: "settings-model-options",
  modelHelp: "settings-model-help",
  save: "settings-save",
  reset: "settings-reset",
  close: "settings-close",
  message: "settings-message",
  notes: "settings-notes",
  summary: "settings-summary",
} as const;

export interface SettingsElements {
  readonly button: HTMLButtonElement;
  readonly panel: HTMLElement;
  readonly provider: HTMLSelectElement;
  readonly providerStatus: HTMLElement;
  readonly envHelp: HTMLElement;
  readonly model: HTMLInputElement;
  readonly modelOptions: HTMLElement;
  readonly modelHelp: HTMLElement;
  readonly save: HTMLButtonElement;
  readonly reset: HTMLButtonElement;
  readonly close: HTMLButtonElement;
  readonly message: HTMLElement;
  readonly notes: HTMLElement;
  readonly summary: HTMLElement;
}

function find<T extends HTMLElement>(root: ParentNode, id: string): T {
  const element = root.querySelector<T>(`#${id}`);
  if (element === null) {
    throw new Error(`the page is missing #${id}`);
  }
  return element;
}

/** Collect the panel's elements from the page. */
export function collectSettingsElements(root: ParentNode): SettingsElements {
  return {
    button: find<HTMLButtonElement>(root, SETTINGS_IDS.button),
    panel: find(root, SETTINGS_IDS.panel),
    provider: find<HTMLSelectElement>(root, SETTINGS_IDS.provider),
    providerStatus: find(root, SETTINGS_IDS.providerStatus),
    envHelp: find(root, SETTINGS_IDS.envHelp),
    model: find<HTMLInputElement>(root, SETTINGS_IDS.model),
    modelOptions: find(root, SETTINGS_IDS.modelOptions),
    modelHelp: find(root, SETTINGS_IDS.modelHelp),
    save: find<HTMLButtonElement>(root, SETTINGS_IDS.save),
    reset: find<HTMLButtonElement>(root, SETTINGS_IDS.reset),
    close: find<HTMLButtonElement>(root, SETTINGS_IDS.close),
    message: find(root, SETTINGS_IDS.message),
    notes: find(root, SETTINGS_IDS.notes),
    summary: find(root, SETTINGS_IDS.summary),
  };
}

/** The one-line account under the description box, e.g. `Using: X · Y`. */
export function describeEffective(effective: EffectiveSettings): string {
  const label = effective.label ?? effective.provider ?? "no interpreter";
  const model =
    effective.model !== null && effective.model !== ""
      ? effective.model
      : "no model";
  const origin =
    effective.source === "saved" ? "chosen in Settings" : `from ${ENV_FILE}`;
  const head = `Using: ${label} · ${model} (${origin})`;
  return effective.ready ? head : `${head} — not ready`;
}

/** An option's text: the backend's label, and whether its setup is present. */
export function optionText(option: ProviderOption): string {
  return `${option.label} — ${option.available ? "ready" : "not ready"}`;
}

function isRefusal(
  payload: SettingsResponse | SettingsRefusal,
): payload is SettingsRefusal {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof (payload as SettingsRefusal).error === "object" &&
    (payload as SettingsRefusal).error !== null
  );
}

function isSettings(payload: unknown): payload is SettingsResponse {
  return (
    typeof payload === "object" &&
    payload !== null &&
    Array.isArray((payload as SettingsResponse).providers) &&
    typeof (payload as SettingsResponse).effective === "object"
  );
}

export interface SettingsPanel {
  /** Read the settings from the backend and show them. */
  load(): Promise<void>;
  open(): void;
  close(): void;
  toggle(): void;
  isOpen(): boolean;
  /** Send the chosen provider and model. */
  save(): Promise<void>;
  /** Forget the page's choice; `.env` applies again. */
  reset(): Promise<void>;
  /** The last settings the backend sent, if any. */
  current(): SettingsResponse | null;
}

export interface SettingsOptions {
  readonly elements: SettingsElements;
  readonly client: ApiClient;
}

export function createSettings(options: SettingsOptions): SettingsPanel {
  const { elements, client } = options;
  let settings: SettingsResponse | null = null;
  let busy = false;

  function say(text: string, kind: "info" | "error" | "ok"): void {
    elements.message.textContent = text;
    elements.message.dataset.kind = kind;
    elements.message.setAttribute("role", kind === "error" ? "alert" : "status");
  }

  function selected(): ProviderOption | undefined {
    return settings?.providers.find(
      (option) => option.name === elements.provider.value,
    );
  }

  /** Show what the selected provider needs, and what Model may hold. */
  function describeSelection(): void {
    const option = selected();
    elements.modelOptions.replaceChildren();
    if (option === undefined) {
      elements.providerStatus.textContent = "";
      elements.envHelp.hidden = true;
      return;
    }
    for (const suggestion of option.model_suggestions) {
      const item = document.createElement("option");
      item.value = suggestion;
      elements.modelOptions.append(item);
    }
    elements.providerStatus.dataset.ready = String(option.available);
    elements.providerStatus.textContent = option.available
      ? "Ready."
      : `Not ready: ${option.reason_if_not ?? "its setup is missing."}`;
    const names = option.env_variables.join(", ");
    elements.envHelp.hidden = names === "";
    elements.envHelp.textContent =
      names === ""
        ? ""
        : `Configured in ${ENV_FILE}, never on this page: ${names}.`;
    elements.model.placeholder = option.model_required
      ? "required: the model your server serves"
      : `default: ${option.default_model}`;
    const suggestions = option.model_suggestions.join(", ");
    elements.modelHelp.textContent = option.model_required
      ? "Type the model name exactly as your server lists it."
      : suggestions === ""
        ? `Leave empty for ${option.default_model}.`
        : `Pick ${suggestions}, or type another name. Leave empty for ${option.default_model}.`;
  }

  /** Put the backend's answer on the page. */
  function show(next: SettingsResponse): void {
    settings = next;
    elements.summary.textContent = describeEffective(next.effective);
    elements.summary.dataset.ready = String(next.effective.ready);
    elements.provider.replaceChildren();
    for (const option of next.providers) {
      const item = document.createElement("option");
      item.value = option.name;
      item.textContent = optionText(option);
      item.dataset.ready = String(option.available);
      elements.provider.append(item);
    }
    const chosen = next.saved?.provider ?? next.effective.provider ?? "";
    elements.provider.value = chosen;
    elements.model.value = next.saved !== null ? next.saved.model : "";
    elements.notes.replaceChildren();
    for (const note of next.notes) {
      const item = document.createElement("li");
      item.textContent = note;
      elements.notes.append(item);
    }
    describeSelection();
  }

  function setBusy(next: boolean): void {
    busy = next;
    elements.save.disabled = next;
    elements.reset.disabled = next;
  }

  async function load(): Promise<void> {
    try {
      const payload = await client.settings();
      if (!isSettings(payload)) {
        throw new NetworkError("the settings answer was not understood");
      }
      show(payload);
    } catch (error) {
      const reason =
        error instanceof Error ? error.message : "the API could not be reached";
      elements.summary.textContent = `Using: unknown (settings unavailable: ${reason})`;
      say(`The settings could not be read: ${reason}.`, "error");
    }
  }

  /** Apply a `PUT` or `DELETE` answer; report a refusal without changing anything. */
  async function apply(
    request: () => Promise<SettingsResponse | SettingsRefusal>,
    done: (next: SettingsResponse) => string,
  ): Promise<void> {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      const payload = await request();
      if (isRefusal(payload)) {
        say(`Not saved: ${payload.error.message}`, "error");
        if (payload.error.field === "model") {
          elements.model.setAttribute("aria-invalid", "true");
          elements.model.focus();
        }
        return;
      }
      if (!isSettings(payload)) {
        say("Not saved: the answer was not understood.", "error");
        return;
      }
      elements.model.removeAttribute("aria-invalid");
      show(payload);
      say(done(payload), payload.effective.ready ? "ok" : "info");
    } catch (error) {
      const reason =
        error instanceof Error ? error.message : "the API could not be reached";
      say(`Not saved: ${reason}.`, "error");
    } finally {
      setBusy(false);
    }
  }

  function outcome(prefix: string, next: SettingsResponse): string {
    const effective = next.effective;
    const using = `${effective.label ?? effective.provider ?? ""} · ${effective.model || "no model"}`;
    return effective.ready
      ? `${prefix} The next generation uses ${using}.`
      : `${prefix} The next generation uses ${using}, which is not ready: ${effective.reason_if_not ?? ""}`;
  }

  function open(): void {
    elements.panel.hidden = false;
    elements.button.setAttribute("aria-expanded", "true");
    elements.provider.focus();
  }

  function close(): void {
    elements.panel.hidden = true;
    elements.button.setAttribute("aria-expanded", "false");
    elements.button.focus();
  }

  elements.provider.addEventListener("change", () => {
    // A new provider starts from its own default, not the last one's model.
    elements.model.value =
      settings?.saved?.provider === elements.provider.value
        ? settings.saved.model
        : "";
    elements.model.removeAttribute("aria-invalid");
    describeSelection();
  });
  elements.panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });

  return {
    load,
    open,
    close,
    toggle(): void {
      if (elements.panel.hidden) {
        open();
      } else {
        close();
      }
    },
    isOpen(): boolean {
      return !elements.panel.hidden;
    },
    save(): Promise<void> {
      const provider = elements.provider.value;
      const model = elements.model.value.trim();
      return apply(
        () => client.saveSettings(provider, model),
        (next) => outcome("Saved.", next),
      );
    },
    reset(): Promise<void> {
      return apply(
        () => client.resetSettings(),
        (next) => outcome(`Reset to the ${ENV_FILE} defaults.`, next),
      );
    },
    current(): SettingsResponse | null {
      return settings;
    },
  };
}

/** Wire the panel's buttons. Separate so the tests drive the same wiring. */
export function wireSettings(
  elements: SettingsElements,
  panel: SettingsPanel,
): void {
  elements.button.addEventListener("click", () => {
    panel.toggle();
  });
  elements.close.addEventListener("click", () => {
    panel.close();
  });
  elements.save.addEventListener("click", () => {
    void panel.save();
  });
  elements.reset.addEventListener("click", () => {
    void panel.reset();
  });
}
