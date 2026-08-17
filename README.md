# OpenAI-Compatible Router

A small reusable BYO-provider routing service for OpenAI-compatible chat-completions endpoints.

This repository is intentionally independent of Nexus Synapse. It exists as a recyclable infrastructure component that can sit between a caller and any OpenAI-compatible provider while keeping provider credentials short-lived and isolated.

The router supports:

- short-lived in-memory routes;
- per-route API keys and model locks;
- standard text chat completions;
- streaming SSE;
- OpenAI-style `tools` / `tool_calls` pass-through;
- `/v1/models` compatibility;
- route expiry and deletion;
- request ceilings;
- secret redaction;
- outbound endpoint validation / SSRF protection.

## Why a separate router?

Separating model routing from the application using it creates a clean provider boundary:

```text
caller
  |
  v
OpenAI-Compatible Router
  |
  +-- temporary route A --> provider/model A
  +-- temporary route B --> provider/model B
  +-- temporary route C --> provider/model C
```

Applications can target the router without learning or persisting the evaluator's provider credential.

The initial Nexus black-box validation work uses this pattern so an evaluator can bring an OpenAI-compatible provider/model without the public validation gateway paying for inference or exposing private runtime assembly logic.

## Run

```bash
python -m pip install -e .
uvicorn nexus_byo_router.app:app --host 127.0.0.1 --port 8091
```

## Security model

Provider credentials are held only in process memory for the lifetime of a route. They are not written to SQLite, files, evidence artifacts, or response bodies.

Public deployments should put the router's route-management interface behind a trusted private network or authenticated gateway. See `SECURITY.md`.

## Status

v0.1.0 reference implementation.
