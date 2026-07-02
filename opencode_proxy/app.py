"""OpenCode Zen SSE 规范化反向代理（new-api → 本 sidecar → opencode）。

把 new-api 渠道 61 的 base_url 改指向本服务（如 http://opencode-sse-normalizer:18732），
本服务把请求原样转发到 opencode，并在响应是 SSE 时用 normalizer 还原成 OpenAI 标准流。
对非流式响应、错误响应一律逐字透传（保持 opencode 原状态码/内容）。

不做鉴权：本服务只面向内部（new-api 同 docker 网络 / loopback），绝不对公网暴露。
opencode 的渠道 key 由 new-api 每请求经 Authorization 头透传、本服务不落盘不缓存。

依赖：fastapi + uvicorn + requests（与既有 managed_proxy 同栈）。
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Iterator

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

try:  # package 上下文（仓库内 / 测试）
    from opencode_proxy.normalizer import normalize_sse
except ImportError:  # 扁平容器上下文（/app 下 `uvicorn app:app`）
    from normalizer import normalize_sse


DEFAULT_UPSTREAM_BASE_URL = "https://opencode.ai/zen/go"

# 只转发上游需要的入站头（尤其 Authorization = new-api 发来的渠道 key）；
# 逐跳头 / 由 requests 重算的头不透传。
_FORWARD_REQUEST_HEADERS = ("authorization", "content-type", "accept")
# 上游响应里不回传给 new-api 的头（长度/编码/连接类由 Starlette 依实际响应体重算）。
_STRIP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "content-encoding", "keep-alive",
}


@dataclass
class NormalizerSettings:
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    host: str = "127.0.0.1"
    port: int = 18732
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 300

    @classmethod
    def from_env(cls) -> "NormalizerSettings":
        return cls(
            upstream_base_url=os.getenv(
                "OPENCODE_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM_BASE_URL
            ).rstrip("/"),
            host=os.getenv("OPENCODE_NORMALIZER_HOST", "127.0.0.1"),
            port=int(os.getenv("OPENCODE_NORMALIZER_PORT", "18732")),
            connect_timeout_seconds=int(os.getenv("OPENCODE_CONNECT_TIMEOUT_SECONDS", "10")),
            read_timeout_seconds=int(os.getenv("OPENCODE_READ_TIMEOUT_SECONDS", "300")),
        )


def _forward_headers(request: Request) -> dict:
    out = {}
    for name in _FORWARD_REQUEST_HEADERS:
        val = request.headers.get(name)
        if val is not None:
            out[name] = val
    return out


def _resp_headers(upstream: requests.Response) -> dict:
    return {k: v for k, v in upstream.headers.items()
            if k.lower() not in _STRIP_RESPONSE_HEADERS}


def create_app(settings: NormalizerSettings | None = None) -> FastAPI:
    cfg = settings or NormalizerSettings.from_env()
    base = cfg.upstream_base_url.rstrip("/")
    app = FastAPI(title="OpenCode Zen SSE Normalizer")

    @app.get("/health")
    async def health():
        return {"status": "ok", "upstream": base}

    def _normalized_sse(upstream: requests.Response) -> Iterator[bytes]:
        try:
            for frame in normalize_sse(upstream.iter_lines(decode_unicode=True)):
                yield frame.encode("utf-8")
        finally:
            upstream.close()

    @app.api_route("/{full_path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(full_path: str, request: Request):
        url = f"{base}/{full_path}"
        body = await request.body()
        # 一律 stream=True 读上游，以便对 SSE 逐块规范化；非 SSE 再整体读回。
        try:
            upstream = requests.request(
                request.method,
                url,
                params=dict(request.query_params),
                headers=_forward_headers(request),
                data=body,
                stream=True,
                timeout=(cfg.connect_timeout_seconds, cfg.read_timeout_seconds),
            )
        except requests.RequestException as exc:
            return Response(content=f"upstream request failed: {exc}", status_code=502)

        content_type = upstream.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            return StreamingResponse(
                _normalized_sse(upstream),
                status_code=upstream.status_code,
                media_type="text/event-stream",
                headers=_resp_headers(upstream),
            )
        # 非流式 / 错误：逐字透传
        payload = upstream.content
        upstream.close()
        return Response(
            content=payload,
            status_code=upstream.status_code,
            media_type=content_type or "application/json",
            headers=_resp_headers(upstream),
        )

    return app


app = create_app()


if __name__ == "__main__":
    cfg = NormalizerSettings.from_env()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
