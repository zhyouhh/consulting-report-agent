from dataclasses import dataclass, field
from typing import Iterable
import os

import requests
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse


DEFAULT_ALLOWED_MODEL = "deepseek-v4-pro"


@dataclass
class ProxySettings:
    upstream_base_url: str = ""
    upstream_api_key: str = ""
    allowed_models: list[str] = field(default_factory=lambda: [DEFAULT_ALLOWED_MODEL])
    # selectable_models: subset shown in /v1/models (user-facing dropdown).
    # Defaults to None; resolved to full allowed_models at runtime via property.
    selectable_models: list[str] | None = None
    client_bearer_token: str = "managed"
    host: str = "127.0.0.1"
    port: int = 18731
    request_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        # Normalize: None means "same as allowed_models" (backward compat).
        if self.selectable_models is None:
            self.selectable_models = list(self.allowed_models)

    @classmethod
    def from_env(cls) -> "ProxySettings":
        allowed_models_raw = os.getenv("MANAGED_PROXY_ALLOWED_MODELS", DEFAULT_ALLOWED_MODEL)
        allowed_models = [item.strip() for item in allowed_models_raw.split(",") if item.strip()]
        allowed_models = allowed_models or [DEFAULT_ALLOWED_MODEL]

        selectable_raw = os.getenv("MANAGED_PROXY_SELECTABLE_MODELS", "")
        if selectable_raw.strip():
            selectable_models: list[str] | None = [
                item.strip() for item in selectable_raw.split(",") if item.strip()
            ]
        else:
            selectable_models = None  # will default to allowed_models in __post_init__

        return cls(
            upstream_base_url=os.getenv("MANAGED_PROXY_UPSTREAM_BASE_URL", "").rstrip("/"),
            upstream_api_key=os.getenv("MANAGED_PROXY_UPSTREAM_API_KEY", ""),
            allowed_models=allowed_models,
            selectable_models=selectable_models,
            client_bearer_token=os.getenv("MANAGED_PROXY_CLIENT_TOKEN", "managed"),
            host=os.getenv("MANAGED_PROXY_HOST", "127.0.0.1"),
            port=int(os.getenv("MANAGED_PROXY_PORT", "18731")),
            request_timeout_seconds=int(os.getenv("MANAGED_PROXY_TIMEOUT_SECONDS", "300")),
        )

    @property
    def primary_model(self) -> str:
        return self.allowed_models[0]


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix):].strip()
    return token or None


def _ensure_authorized(authorization: str | None, settings: ProxySettings) -> None:
    token = _extract_bearer_token(authorization)
    if token != settings.client_bearer_token:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _build_models_payload(settings: ProxySettings) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "consulting-report-managed-proxy",
            }
            for model in (settings.selectable_models or settings.allowed_models)
        ],
    }


def _iter_upstream_chunks(upstream_response: requests.Response) -> Iterable[bytes]:
    try:
        for chunk in upstream_response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    finally:
        upstream_response.close()


def create_app(settings: ProxySettings | None = None) -> FastAPI:
    runtime_settings = settings or ProxySettings.from_env()
    app = FastAPI(title="Consulting Report Managed Proxy")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "model": runtime_settings.primary_model,
            "allowed_models": runtime_settings.allowed_models,
            "selectable_models": runtime_settings.selectable_models,
        }

    @app.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        _ensure_authorized(authorization, runtime_settings)
        return _build_models_payload(runtime_settings)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _ensure_authorized(authorization, runtime_settings)

        payload = await request.json()
        requested_model = payload.get("model", runtime_settings.primary_model)
        if requested_model not in runtime_settings.allowed_models:
            raise HTTPException(status_code=400, detail=f"model '{requested_model}' is not allowed")

        payload["model"] = requested_model
        stream_requested = bool(payload.get("stream"))

        try:
            upstream_response = requests.post(
                f"{runtime_settings.upstream_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {runtime_settings.upstream_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                stream=stream_requested,
                timeout=(10, runtime_settings.request_timeout_seconds),
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc

        content_type = upstream_response.headers.get("content-type", "application/json")
        if not stream_requested or not content_type.startswith("text/event-stream"):
            body = upstream_response.content
            upstream_response.close()
            return Response(
                content=body,
                status_code=upstream_response.status_code,
                media_type=content_type,
            )

        return StreamingResponse(
            _iter_upstream_chunks(upstream_response),
            status_code=upstream_response.status_code,
            media_type=content_type,
        )

    return app


app = create_app()


if __name__ == "__main__":
    runtime_settings = ProxySettings.from_env()
    uvicorn.run(
        "managed_proxy.app:app",
        host=runtime_settings.host,
        port=runtime_settings.port,
        log_level="info",
    )
