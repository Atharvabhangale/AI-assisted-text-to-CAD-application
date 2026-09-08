# AI-assisted Text-to-CAD Application

An application for generating CAD geometry from natural-language descriptions.

The deterministic half is built: a neutral CAD document contract, a validator,
an OpenCascade-backed engine, exporters, a build/cache/isolation stack, an
application service, a transport-neutral API contract, an HTTP transport and a
browser viewer. **Natural-language input is not implemented yet** — the viewer
takes a canonical CAD document, not a description.

## Repository structure

```
/
├── apps/
│   ├── web/          Browser CAD viewer (Vite + TypeScript + Three.js)
│   └── api/          HTTP transport over cad-core (FastAPI)
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

A thin HTTP transport (FastAPI) over `cad-core`'s application service:
`POST /validate`, `POST /build`, `GET /builds/{build_key}`,
`GET /builds/{build_key}/render`, `GET /artifacts/{artifact_id}` and
`GET /health`. It contains no CAD business logic — see `docs/http-api.md`.

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
(`docs/http-api.md`). `apps/web` holds the browser viewer
(`docs/web-application.md`), including its own end-to-end run against the
real stack. `tests/` is still a placeholder, holding only a `.gitkeep` file so
the empty directory is tracked by Git.

Everything here is for **local development**. There is no authentication, no
authorization, no rate limiting, no user isolation and no production
sandboxing; see the security limitations in `docs/http-api.md`,
`docs/artifact-delivery.md` and `docs/isolated-cad-execution.md`.
