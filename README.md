# AI-assisted Text-to-CAD Application

An application for generating CAD geometry from natural-language descriptions.

The deterministic half is built: a neutral CAD document contract, a validator,
an OpenCascade-backed engine, exporters, a build/cache/isolation stack, an
application service, a transport-neutral API contract, an HTTP transport and a
browser viewer. The **first AI interpretation layer** now turns a
natural-language description into a candidate CAD document, for a small
subset — one box or one cylinder — behind a small provider boundary. The
browser still takes a canonical CAD document, not a description.

## Repository structure

```
/
├── apps/
│   ├── web/          Browser CAD viewer (Vite + TypeScript + Three.js)
│   └── api/          HTTP transport (cad_api) + AI interpretation (cad_ai)
├── packages/
│   └── cad-core/     Reserved for the future deterministic CAD generation engine
├── tests/            Reserved for integration / end-to-end tests
├── docs/             Reserved for architecture and technical documentation
├── .gitignore
└── README.md
```

### apps/web

The browser viewer: paste or load a canonical CAD document, build it, and see
the tessellated result in a WebGL viewport with STEP/IGES/STL downloads. It
is a **client** — every dimension, volume and mesh it shows is a field the
backend sent, and it contains no CAD logic, no kernel and no geometry of its
own. See `docs/web-application.md`.

```sh
cd apps/web && npm install
npm test          # 87 unit and DOM tests
npm run dev       # the page, with /api proxied to the backend
npm run e2e       # the real stack in a real browser
```

Natural-language input, LLM integration and MCP are **not** part of it yet.

### apps/api

Two packages, both above `cad-core` and both free of CAD business logic.

`cad_api` — a thin HTTP transport (FastAPI) over `cad-core`'s application
service: `POST /validate`, `POST /build`, `GET /builds/{build_key}`,
`GET /builds/{build_key}/render`, `GET /artifacts/{artifact_id}` and
`GET /health`. See `docs/http-api.md`.

`cad_ai` — the natural-language interpretation layer: a description in, a
**candidate CAD document** out, validated by `cad-core`'s existing validator.
The model is an interpreter, not the CAD engine: it emits a data document and
never Python, CadQuery, FeatureScript, STL or STEP, and nothing it returns is
executed. One provider (Anthropic), imported lazily and declared as the
optional `ai` extra, so neither `cad-core` nor the HTTP transport depends on
an LLM SDK. No HTTP endpoint yet — see `docs/text-to-cad-ai.md`.

### packages/cad-core

The deterministic CAD generation engine: given a structured description of a
part, it produces the corresponding geometry. Kept as a standalone package so
it can be developed and tested independently of the frontend and API, and it
imports neither.

### tests

Reserved for tests that span more than one component — integration and
end-to-end tests. Unit tests are expected to live alongside the code they cover.

### docs

Reserved for architecture notes, design decisions, and other technical
documentation.

## Status

`docs/` holds the CAD specification contract and the architecture notes.
`packages/cad-core` holds the typed representation, the static validator, the
local CAD engine, the exporters, the build and cache layers, the isolated
execution boundary, the application service and the transport-neutral API
contract. `apps/api` holds a thin HTTP transport over that contract
(`docs/http-api.md`) and the AI interpretation layer
(`docs/text-to-cad-ai.md`). `apps/web` holds the browser viewer
(`docs/web-application.md`), including its own end-to-end run against the
real stack. `tests/` is still a placeholder, holding only a `.gitkeep` file so
the empty directory is tracked by Git.

Everything here is for **local development**. There is no authentication, no
authorization, no rate limiting, no user isolation, no production sandboxing
and no cost control on the AI layer's paid API calls; see the security
limitations in `docs/http-api.md`, `docs/artifact-delivery.md`,
`docs/isolated-cad-execution.md` and `docs/text-to-cad-ai.md`.

Text-to-CAD is **not production-ready**. Stage 26 proves one controlled
natural-language → CAD-specification path for a deliberately tiny subset.
