"""OpenCode Zen SSE 规范化反向代理（new-api → 本 sidecar → opencode）。

把 new-api 渠道 61 的 base_url 改指向本服务（如 http://opencode-sse-normalizer:18732），
本服务把请求原样转发到 opencode，并在响应是**成功的 SSE**时用 normalizer 还原成 OpenAI
标准流。非流式响应、以及 4xx/5xx（含 SSE 错误体）一律逐字透传（保持 opencode 原状态码/内容）。

真异步：用 httpx.AsyncClient，避免在事件循环里跑阻塞 IO。不鉴权、只面向内部（new-api 同
docker 网络 / loopback），**绝不对公网暴露**；opencode 渠道 key 由 new-api 每请求经
Authorization 头透传，本服务不落盘不缓存、不跟随重定向。

依赖：fastapi + uvicorn + httpx。
"""
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

try:  # package 上下文（仓库内 / 测试）
    from opencode_proxy.normalizer import _SseNormalizer
except ImportError:  # 扁平容器上下文（/app 下 `uvicorn app:app`）
    from normalizer import _SseNormalizer


DEFAULT_UPSTREAM_BASE_URL = "https://opencode.ai/zen/go"

# 只转发上游需要的入站头（尤其 Authorization = new-api 发来的渠道 key）。
# 刻意不转发 Accept-Encoding：交给 httpx 协商并透明解压，normalizer 只见解码后的文本。
_FORWARD_REQUEST_HEADERS = ("authorization", "content-type", "accept")
# 逐跳头 + 由响应体重算的头，不回传给 new-api（RFC 7230 §6.1 + 内容相关头）。
_STRIP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "content-encoding", "keep-alive",
    "te", "trailer", "upgrade", "proxy-authenticate", "proxy-authorization",
}


@dataclass
class NormalizerSettings:
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    host: str = "127.0.0.1"          # 仅本地 `python app.py` 生效；容器由 Dockerfile CMD 固定 0.0.0.0:18732
    port: int = 18732
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "NormalizerSettings":
        return cls(
            upstream_base_url=os.getenv(
                "OPENCODE_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM_BASE_URL
            ).rstrip("/"),
            host=os.getenv("OPENCODE_NORMALIZER_HOST", "127.0.0.1"),
            port=int(os.getenv("OPENCODE_NORMALIZER_PORT", "18732")),
            connect_timeout_seconds=float(os.getenv("OPENCODE_CONNECT_TIMEOUT_SECONDS", "10")),
            read_timeout_seconds=float(os.getenv("OPENCODE_READ_TIMEOUT_SECONDS", "300")),
        )


def _forward_headers(request: Request) -> dict:
    out = {}
    for name in _FORWARD_REQUEST_HEADERS:
        val = request.headers.get(name)
        if val is not None:
            out[name] = val
    return out


def _resp_headers(upstream: httpx.Response) -> dict:
    return {k: v for k, v in upstream.headers.items()
            if k.lower() not in _STRIP_RESPONSE_HEADERS}


def create_app(settings: Optional[NormalizerSettings] = None,
               transport: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    cfg = settings or NormalizerSettings.from_env()
    base = cfg.upstream_base_url.rstrip("/")
    timeout = httpx.Timeout(cfg.read_timeout_seconds, connect=cfg.connect_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=transport,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="OpenCode Zen SSE Normalizer", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "upstream": base}

    async def _stream_normalized(upstream: httpx.Response) -> AsyncIterator[bytes]:
        normalizer = _SseNormalizer()
        try:
            async for line in upstream.aiter_lines():
                for frame in normalizer.feed(line):
                    yield frame.encode("utf-8")
            for frame in normalizer.close():
                yield frame.encode("utf-8")
        finally:
            await upstream.aclose()

    @app.api_route("/{full_path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(full_path: str, request: Request):
        client: httpx.AsyncClient = app.state.client
        url = f"{base}/{full_path}"
        body = await request.body()
        upstream_req = client.build_request(
            request.method, url,
            params=request.url.query or None,   # 原始 query string，保留重复/顺序
            headers=_forward_headers(request),
            content=body,
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.RequestError as exc:
            return Response(content=f"upstream request failed: {exc}", status_code=502)

        content_type = upstream.headers.get("content-type", "")
        is_sse = content_type.lower().startswith("text/event-stream")
        # 仅对**成功的 SSE** 规范化；4xx/5xx（含 SSE 错误体）与非流式一律逐字透传。
        if is_sse and upstream.status_code < 400:
            return StreamingResponse(
                _stream_normalized(upstream),
                status_code=upstream.status_code,
                media_type="text/event-stream",
                headers=_resp_headers(upstream),
            )
        try:
            payload = await upstream.aread()
        except httpx.RequestError as exc:
            return Response(content=f"upstream read failed: {exc}", status_code=502)
        finally:
            await upstream.aclose()
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
