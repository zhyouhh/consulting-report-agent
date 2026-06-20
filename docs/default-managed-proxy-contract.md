# Default Managed Proxy Contract

Base URL: `https://newapi.z0y0h.work/client/v1`

## Endpoint Support

- `POST /chat/completions`
- `GET /models` (recommended)

## Required Behavior

- Return OpenAI-compatible JSON payloads.
- **Pass through** the client's requested model **iff** it is in `MANAGED_PROXY_ALLOWED_MODELS`; reject non-whitelisted models with HTTP 400. (N6 2026-06-21: no longer force-rewrites every request to a single model — new-api routes by model name, so an internal vision model can be reachable. A missing `model` defaults to the primary/first allowed model.)
- Expose only `MANAGED_PROXY_SELECTABLE_MODELS` (a subset of allowed) via `GET /models` — internal models such as the vision model `Qwen/Qwen3-VL-8B-Instruct` are *allowed/reachable* but *not* shown in the user dropdown. `GET /health` returns `allowed_models`+`selectable_models` for ops preflight.
- Remain thin: no database, no queue, no heavy cache layer.
- Keep resource usage modest on the existing server.

## Authentication

- Client should not hold the real upstream credential.
- Any bearer token accepted by this proxy belongs to the proxy boundary, not the upstream provider.
- The proxy may ignore the client bearer token and inject upstream credentials server-side.

## Operational Notes

- This endpoint is intended for the desktop client's managed mode only.
- Custom API mode bypasses this endpoint entirely.
- The proxy should be easy to disable or rotate without requiring a desktop client rebuild.
