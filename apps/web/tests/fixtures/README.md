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

The build key is
`a6cd6fa167f396db95f7bb618a98ef9867debaecc014846264b591ac30e2ca21` and the
document hash
`2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc`.

To regenerate them, re-run the capture described in `docs/web-application.md`.
Do not edit them by hand: a hand-edited fixture would let the frontend drift
away from the transport contract without any test noticing.
