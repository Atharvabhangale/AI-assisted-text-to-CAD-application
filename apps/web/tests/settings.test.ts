/**
 * The Settings panel: shows the backend's providers and their readiness,
 * saves a provider and model, resets to `.env`, and reports refusals.
 *
 * The `/settings` answers here are **hand-authored** to the shape
 * `docs/http-api.md` documents (and `apps/api/tests/test_settings_endpoint.py`
 * pins on the backend side). They show the page handles that shape; they are
 * not captures of a running backend.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { ApiClient } from "../src/api";
import type { SettingsResponse } from "../src/api";
import {
  collectSettingsElements,
  createSettings,
  describeEffective,
  wireSettings,
  type SettingsElements,
  type SettingsPanel,
} from "../src/settings";
import { installPage, stubFetch, type RecordedCall } from "./harness";

const ENV_HELP = "apps\\api\\.env";

function settingsResponse(
  overrides: Partial<SettingsResponse> = {},
): SettingsResponse {
  return {
    effective: {
      provider: "anthropic",
      label: "Anthropic API (API key)",
      model: "claude-haiku-5-5",
      source: "environment",
      ready: true,
      reason_if_not: null,
    },
    environment: {
      provider: "anthropic",
      label: "Anthropic API (API key)",
      model: "claude-haiku-5-5",
      source: "environment",
      ready: true,
      reason_if_not: null,
    },
    saved: null,
    providers: [
      {
        name: "anthropic",
        label: "Anthropic API (API key)",
        kind: "api_key",
        available: true,
        reason_if_not: null,
        default_model: "claude-haiku-5-5",
        model_required: false,
        model_suggestions: ["claude-haiku-5-5", "claude-sonnet-5-5"],
        env_variables: ["ANTHROPIC_API_KEY", "CAD_ANTHROPIC_API_KEY"],
      },
      {
        name: "claude_code",
        label: "Claude Code subscription",
        kind: "cli",
        available: false,
        reason_if_not:
          "The claude executable was not found on PATH. Install it and sign it in, or set CAD_CLAUDE_CODE_PATH in apps\\api\\.env to the executable, then restart the backend.",
        default_model: "claude-haiku-5-5",
        model_required: false,
        model_suggestions: ["claude-haiku-5-5", "claude-sonnet-5-5"],
        env_variables: ["CAD_CLAUDE_CODE_PATH"],
      },
      {
        name: "openai_compatible",
        label: "Open-weight model (OpenAI-compatible server)",
        kind: "server",
        available: true,
        reason_if_not: null,
        default_model: "",
        model_required: true,
        model_suggestions: [],
        env_variables: ["CAD_OPENAI_BASE_URL", "CAD_OPENAI_API_KEY", "CAD_AI_MODEL"],
      },
    ],
    notes: [
      "API keys, CLI executable paths and server addresses are read only from apps\\api\\.env and are never sent to or accepted from this page.",
    ],
    ...overrides,
  };
}

const SAVED_CLAUDE_CODE = settingsResponse({
  effective: {
    provider: "claude_code",
    label: "Claude Code subscription",
    model: "claude-sonnet-5-5",
    source: "saved",
    ready: false,
    reason_if_not: "The claude executable was not found on PATH.",
  },
  saved: { provider: "claude_code", model: "claude-sonnet-5-5" },
});

interface Setup {
  readonly elements: SettingsElements;
  readonly panel: SettingsPanel;
  readonly calls: RecordedCall[];
}

function setup(route: (call: RecordedCall) => unknown): Setup {
  installPage();
  const elements = collectSettingsElements(document);
  const stub = stubFetch({ routes: { "/settings": route } });
  const panel = createSettings({
    elements,
    client: new ApiClient({ base: "/api", fetch: stub.fetch }),
  });
  wireSettings(elements, panel);
  return { elements, panel, calls: stub.calls };
}

function optionTexts(elements: SettingsElements): string[] {
  return [...elements.provider.options].map((option) => option.textContent ?? "");
}

describe("the Settings panel", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("is closed until the button opens it, and Escape closes it", async () => {
    const { elements, panel } = setup(() => settingsResponse());
    await panel.load();
    expect(elements.panel.hidden).toBe(true);
    expect(elements.button.getAttribute("aria-expanded")).toBe("false");
    expect(elements.button.getAttribute("aria-controls")).toBe("settings-panel");
    elements.button.click();
    expect(panel.isOpen()).toBe(true);
    expect(elements.button.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(elements.provider);
    elements.panel.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(panel.isOpen()).toBe(false);
    expect(document.activeElement).toBe(elements.button);
    elements.button.click();
    elements.close.click();
    expect(panel.isOpen()).toBe(false);
  });

  it("lists the backend's providers with their readiness", async () => {
    const { elements, panel, calls } = setup(() => settingsResponse());
    await panel.load();
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].url).toBe("/api/settings");
    expect(optionTexts(elements)).toEqual([
      "Anthropic API (API key) — ready",
      "Claude Code subscription — not ready",
      "Open-weight model (OpenAI-compatible server) — ready",
    ]);
    expect(elements.provider.value).toBe("anthropic");
    expect(elements.providerStatus.textContent).toBe("Ready.");
    expect(elements.model.value).toBe("");
    expect(elements.model.placeholder).toBe("default: claude-haiku-5-5");
    const suggestions = [...elements.modelOptions.querySelectorAll("option")].map(
      (option) => option.value,
    );
    expect(suggestions).toEqual(["claude-haiku-5-5", "claude-sonnet-5-5"]);
    expect(elements.notes.textContent).toContain("never sent to or accepted");
  });

  it("shows the effective settings under the description box", async () => {
    const { elements, panel } = setup(() => settingsResponse());
    await panel.load();
    expect(elements.summary.textContent).toBe(
      `Using: Anthropic API (API key) · claude-haiku-5-5 (from ${ENV_HELP})`,
    );
    expect(elements.summary.dataset.ready).toBe("true");
  });

  it("explains a not-ready provider and names the .env variables only", async () => {
    const { elements, panel } = setup(() => settingsResponse());
    await panel.load();
    elements.provider.value = "claude_code";
    elements.provider.dispatchEvent(new Event("change"));
    expect(elements.providerStatus.textContent).toContain("Not ready: The claude executable");
    expect(elements.providerStatus.dataset.ready).toBe("false");
    expect(elements.envHelp.hidden).toBe(false);
    expect(elements.envHelp.textContent).toBe(
      `Configured in ${ENV_HELP}, never on this page: CAD_CLAUDE_CODE_PATH.`,
    );
  });

  it("asks for a model where the provider has no default", async () => {
    const { elements, panel } = setup(() => settingsResponse());
    await panel.load();
    elements.provider.value = "openai_compatible";
    elements.provider.dispatchEvent(new Event("change"));
    expect(elements.model.placeholder).toBe("required: the model your server serves");
    expect(elements.modelOptions.querySelectorAll("option")).toHaveLength(0);
  });

  it("saves exactly a provider and a model, and shows the new settings", async () => {
    const { elements, panel, calls } = setup((call) =>
      call.method === "PUT" ? SAVED_CLAUDE_CODE : settingsResponse(),
    );
    await panel.load();
    elements.button.click();
    elements.provider.value = "claude_code";
    elements.provider.dispatchEvent(new Event("change"));
    elements.model.value = " claude-sonnet-5-5 ";
    await panel.save();
    const put = calls.find((call) => call.method === "PUT");
    expect(put).toBeDefined();
    expect(put!.url).toBe("/api/settings");
    expect(put!.body).toEqual({ provider: "claude_code", model: "claude-sonnet-5-5" });
    expect(elements.summary.textContent).toBe(
      "Using: Claude Code subscription · claude-sonnet-5-5 (chosen in Settings) — not ready",
    );
    expect(elements.message.textContent).toContain("Saved.");
    expect(elements.message.textContent).toContain("not ready");
    expect(elements.provider.value).toBe("claude_code");
    expect(elements.model.value).toBe("claude-sonnet-5-5");
    // Saving is not generating: no other endpoint was called.
    expect(calls.every((call) => call.url === "/api/settings")).toBe(true);
  });

  it("shows a refusal and keeps the panel as it was", async () => {
    const { elements, panel } = setup((call) =>
      call.method === "PUT"
        ? {
            error: {
              reason: "model_not_allowed",
              message:
                "Opus models are not allowed (the owner's rule: never Opus). Choose claude-haiku-5-5 or claude-sonnet-5-5.",
              field: "model",
            },
          }
        : settingsResponse(),
    );
    await panel.load();
    elements.button.click();
    elements.model.value = "claude-opus-5-5";
    await panel.save();
    expect(elements.message.textContent).toBe(
      "Not saved: Opus models are not allowed (the owner's rule: never Opus). Choose claude-haiku-5-5 or claude-sonnet-5-5.",
    );
    expect(elements.message.getAttribute("role")).toBe("alert");
    expect(elements.message.dataset.kind).toBe("error");
    expect(elements.model.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(elements.model);
    // The summary still shows what is in force.
    expect(elements.summary.textContent).toContain("claude-haiku-5-5");
    expect(panel.isOpen()).toBe(true);
  });

  it("resets to the .env defaults", async () => {
    let saved = true;
    const { elements, panel, calls } = setup((call) => {
      if (call.method === "DELETE") {
        saved = false;
      }
      return saved ? SAVED_CLAUDE_CODE : settingsResponse();
    });
    await panel.load();
    expect(elements.provider.value).toBe("claude_code");
    await panel.reset();
    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
    expect(calls.filter((call) => call.method === "DELETE")[0].body).toBeUndefined();
    expect(elements.provider.value).toBe("anthropic");
    expect(elements.model.value).toBe("");
    expect(elements.message.textContent).toContain(`Reset to the ${ENV_HELP} defaults.`);
    expect(elements.summary.textContent).toContain(`from ${ENV_HELP}`);
  });

  it("reports an unreachable API without breaking the page", async () => {
    const { elements, panel } = setup(() => new Error("offline"));
    await panel.load();
    expect(elements.summary.textContent).toContain("settings unavailable");
    expect(elements.message.dataset.kind).toBe("error");
    await panel.save();
    expect(elements.message.textContent).toBe("Not saved: the API could not be reached.");
    expect(elements.save.disabled).toBe(false);
  });

  it("formats a summary with no model honestly", () => {
    expect(
      describeEffective({
        provider: "openai_compatible",
        label: "Open-weight model (OpenAI-compatible server)",
        model: "",
        source: "saved",
        ready: false,
        reason_if_not: "No model named",
      }),
    ).toBe(
      "Using: Open-weight model (OpenAI-compatible server) · no model (chosen in Settings) — not ready",
    );
  });
});
