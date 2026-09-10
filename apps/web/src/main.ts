/**
 * Page entry point: find the elements, wire the buttons, create the viewer.
 *
 * The viewer is created lazily and its failure is survivable: a browser
 * without WebGL still validates, builds, and shows the result and the textual
 * geometry summary.
 */

import { ApiClient } from "./api";
import {
  ELEMENT_IDS,
  collectElements,
  createApp,
  requireElement,
  wireExports,
  type ViewerPort,
} from "./app";
import { createViewer } from "./viewer";

function start(): void {
  const elements = collectElements(document);
  const client = new ApiClient();
  let viewer: ViewerPort | null = null;

  const app = createApp({
    elements,
    client,
    viewer: () => {
      if (viewer !== null) {
        return viewer;
      }
      try {
        viewer = createViewer(
          requireElement<HTMLCanvasElement>(document, ELEMENT_IDS.viewerCanvas),
        );
      } catch {
        // No WebGL: the page still works, without a picture.
        viewer = null;
      }
      return viewer;
    },
  });

  elements.generateButton.addEventListener("click", () => {
    void app.generate();
  });
  elements.clarifyButton.addEventListener("click", () => {
    void app.clarify();
  });
  elements.loadExample.addEventListener("click", () => {
    app.loadExample();
  });
  elements.validateButton.addEventListener("click", () => {
    void app.validate();
  });
  elements.buildButton.addEventListener("click", () => {
    void app.build();
  });
  elements.fitButton.addEventListener("click", () => {
    viewer?.fit();
  });
  globalThis.addEventListener("resize", () => {
    (viewer as { resize?: () => void } | null)?.resize?.();
  });

  wireExports(elements, client, (url) => {
    globalThis.location.assign(url);
  });

  app.loadExample();

  // For the end-to-end test to read the real state without a screenshot.
  (globalThis as unknown as Record<string, unknown>).__cadApp = app;
}

start();
