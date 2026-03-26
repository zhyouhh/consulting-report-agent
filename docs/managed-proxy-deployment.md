# Managed Proxy Deployment

## Public Endpoint

- Managed desktop base URL: `https://newapi.z0y0h.work/client/v1`
- Supported endpoints:
  - `GET /models`
  - `POST /chat/completions`

## Runtime Shape

- Nginx keeps the original `location /` -> `127.0.0.1:3000` route for `new-api`.
- A new `location /client/` route forwards to the thin managed proxy.
- The proxy runs as a small Docker container on the same host with `--network host`.
- Upstream calls stay on-host via `http://127.0.0.1:3000/v1`.

## Server Paths

- Deploy root: `/opt/consulting-report-managed-proxy`
- Env file: `/opt/consulting-report-managed-proxy/proxy.env`
- Nginx site file: `/etc/nginx/sites-available/newapi.z0y0h.work`
- Backup captured during first deployment:
  - `/root/newapi.z0y0h.work.bak-20260326-1814.conf`

## Env File Contract

Do not store real secrets in the repo.

Example runtime env:

```env
MANAGED_PROXY_UPSTREAM_BASE_URL=http://127.0.0.1:3000/v1
MANAGED_PROXY_UPSTREAM_API_KEY=<dedicated-upstream-key>
MANAGED_PROXY_ALLOWED_MODELS=gemini-3-flash
MANAGED_PROXY_CLIENT_TOKEN=<dedicated-client-token>
```

## Deploy Commands

Build:

```bash
cd /opt/consulting-report-managed-proxy
docker build -t consulting-report-managed-proxy:latest .
```

Run:

```bash
docker rm -f consulting-report-managed-proxy || true
docker run -d \
  --name consulting-report-managed-proxy \
  --restart unless-stopped \
  --env-file /opt/consulting-report-managed-proxy/proxy.env \
  --network host \
  consulting-report-managed-proxy:latest
```

Reload Nginx after editing the `/client/` route:

```bash
nginx -t
nginx -s reload
```

## Verify

```bash
curl -H "Authorization: Bearer <dedicated-client-token>" https://newapi.z0y0h.work/client/v1/models
```

Expected:

```json
{"object":"list","data":[{"id":"gemini-3-flash"}]}
```

```bash
python - <<'PY'
from openai import OpenAI
import httpx

client = OpenAI(
    api_key="<dedicated-client-token>",
    base_url="https://newapi.z0y0h.work/client/v1",
    http_client=httpx.Client(timeout=60.0),
)

print([m.id for m in client.models.list().data])
resp = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Reply with OK only."}],
    max_tokens=8,
)
print(resp.choices[0].message.content)
PY
```

## Rotate / Revoke

- To revoke desktop managed traffic immediately:
  - replace `MANAGED_PROXY_UPSTREAM_API_KEY` in `proxy.env`
  - restart the proxy container
- To change the client bearer later:
  - update `MANAGED_PROXY_CLIENT_TOKEN`
  - update the release package's `managed_client_token.txt`
  - rebuild or redistribute the desktop client
