# AI-assisted Text-to-CAD Application

An application for generating CAD geometry from natural-language descriptions.

This repository is currently a **project skeleton**. It contains only the
directory layout and repository-level configuration; no frameworks, application
code, or dependencies have been added yet.

## Repository structure

```
/
├── apps/
│   ├── web/          Reserved for the future browser frontend
│   └── api/          Reserved for the future backend API
├── packages/
│   └── cad-core/     Reserved for the future deterministic CAD generation engine
├── tests/            Reserved for integration / end-to-end tests
├── docs/             Reserved for architecture and technical documentation
├── .gitignore
└── README.md
```

### apps/web

Reserved for the browser frontend that will let users describe a part in text
and inspect the resulting CAD model.

### apps/api

Reserved for the backend API that will sit between the frontend and the CAD
generation engine.

### packages/cad-core

Reserved for the deterministic CAD generation engine: given a structured
description of a part, it produces the corresponding geometry. Kept as a
standalone package so it can be developed and tested independently of the
frontend and API.

### tests

Reserved for tests that span more than one component — integration and
end-to-end tests. Unit tests are expected to live alongside the code they cover.

### docs

Reserved for architecture notes, design decisions, and other technical
documentation.

## Status

Stage 0 — project skeleton. Each directory above is a placeholder and currently
holds only a `.gitkeep` file so the empty directory is tracked by Git.
