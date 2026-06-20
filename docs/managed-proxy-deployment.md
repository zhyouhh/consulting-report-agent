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
MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro,Qwen/Qwen3-VL-8B-Instruct
MANAGED_PROXY_SELECTABLE_MODELS=deepseek-v4-pro
MANAGED_PROXY_CLIENT_TOKEN=<dedicated-client-token>
```

## N6 视觉转写（2026-06-21 已上线 jp-app-01）

N6 起：纯文本主模型上传图片时，App 走内部视觉模型 `Qwen/Qwen3-VL-8B-Instruct` 转写。薄网关改为「白名单透传」（new-api 按模型名路由），不再强改写 model：

- `MANAGED_PROXY_ALLOWED_MODELS` 必须含视觉模型（可达）；`MANAGED_PROXY_SELECTABLE_MODELS` 仅列用户可选的主模型（视觉模型**不暴露**进 `/v1/models` 下拉）。`SELECTABLE` 缺省=ALLOWED（向后兼容）。
- 新增 `GET /health` 暴露 `allowed_models`/`selectable_models` 供 ops preflight。
- **上游 new-api 前置条件**（否则 proxy 透传后 new-api 仍 403/503）：
  1. 上游 token（proxy 用的 `MANAGED_PROXY_UPSTREAM_API_KEY` 对应的 new-api token）若开了 `model_limits`，必须把 `Qwen/Qwen3-VL-8B-Instruct` 加进其 `model_limits`。
  2. 承载该模型的渠道（jp-app-01 当前为渠道 60『商业·硅基流动』）的 `group` 必须含该 token 的 group；直接改 `channels.group` 后还要在 `abilities` 表补 `(group, model, channel_id)` 路由行（new-api 的 abilities 不随重启重建，只随渠道经 UI/API 保存时重建），再重启 new-api。
  - 改 new-api DB 前先 `cp one-api.db one-api.db.bak-<ts>`。

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
{"object":"list","data":[{"id":"deepseek-v4-pro"}]}
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
    model="deepseek-v4-pro",
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
