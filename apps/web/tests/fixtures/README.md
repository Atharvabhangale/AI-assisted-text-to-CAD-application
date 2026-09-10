# Frontend test fixtures

Every JSON file here was **captured from the real backend over the real HTTP
API** — `apps/api`'s `create_app` driven by Starlette's `TestClient` against a
temporary cache root, with the real CadQuery/OpenCascade kernel behind it. No
response body was written by hand, so the frontend suite is asserting against
what the backend actually sends.

| File | How it was produced |
|---|---|
| `section-d-document.json` | the CAD specification's Section D document, as posted |
| `section-d-build.json` | `POST /build` with all five outputs, cold cache |
| `section-d-build-cached.json` | the same request again — `cache_hit: true` |
| `section-d-render.json` | `GET /builds/{build_key}/render` for that build |
| `validation-error.json` | `POST /validate` with a zero-sized box (rule S10) |
| `build-error-geometry.json` | `POST /build` with a hole that misses its target (rule E1) |
| `generate-plate.json` | `POST /generate`, "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm thick." |
| `generate-cylinder.json` | `POST /generate`, "Create a cylinder 20 mm in diameter and 50 mm tall along +Z." |
| `generate-clarification.json` | `POST /generate`, "Create a plate 100 by 60 by 10." — no units stated |
| `generate-unsupported.json` | `POST /generate`, "Create a 100 mm cube with a 5 mm fillet on all edges." |
| `generate-invalid-model-output.json` | `POST /generate`, the cylinder prompt again — the model returned no document |
| `generated-plate-build.json` | `POST /build` with `generate-plate.json`'s document |
| `generated-plate-render.json` | `GET /builds/{build_key}/render` for that build |

## The five `generate-*.json` files

These came from the **live Gemini provider** (`gemini-3.6-flash`,
prompt version `2026-09-08.1`), over the running local backend — not from a
stub and not written by hand.

They are evidence of **shape, not of quality**. The suite uses them to assert
that the page handles each outcome correctly. Nothing here claims the model
answers this way every time: it does not. Measured over 13 live calls to
`POST /generate` during Stage 30, the same prompts produced **6 documents, 6
`invalid_model_output`s and 1 provider error** — `generate-invalid-model-
output.json` is one of those failures, kept deliberately so the unhappy path
is tested against something real. See `docs/text-to-cad-ai.md`.

The generated plate's build key is
`e3f4041986e4c5a536a9d276a0070117896f280fe170a98c2fb54e4a7a9dce3d` and its
document hash
`9e2be62478a01f2351e2e266af8b8181edeb69b096cfecdcdc45bfe2b1591e9d`.

The build key is
`a6cd6fa167f396db95f7bb618a98ef9867debaecc014846264b591ac30e2ca21` and the
document hash
`2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc`.

To regenerate them, re-run the capture described in `docs/web-application.md`.
Do not edit them by hand: a hand-edited fixture would let the frontend drift
away from the transport contract without any test noticing.
