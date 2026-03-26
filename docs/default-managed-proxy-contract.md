# Default Managed Proxy Contract

Base URL: `https://newapi.z0y0h.work/client/v1`

## Endpoint Support

- `POST /chat/completions`
- `GET /models` (recommended)

## Required Behavior

- Return OpenAI-compatible JSON payloads.
- Force upstream model routing to `gemini-3-flash`.
- Reject non-whitelisted model names if the client attempts to override the default model.
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
