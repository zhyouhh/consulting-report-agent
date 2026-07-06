from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_SEARCH_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RESULTS = 5


@dataclass(frozen=True)
class SearchItem:
    title: str
    snippet: str
    url: str
    domain: str
    score: float


@dataclass(frozen=True)
class ProviderSearchResult:
    provider: str
    items: list[SearchItem]
    result_type: str = "success"
    # ── 用量记账元数据（搜索池额度监控）────────────────────────────
    # key_index: 本次调用实际使用的 key 在 api_keys 里的下标（多 key 轮询归属）
    # units_used: 本次调用消耗的 provider 计量单位（serper 从响应体 credits 读真值，其余 1 次/调用）
    # quota_snapshot: provider 响应顺带暴露的额度快照（目前仅 brave 响应头），无则 None
    key_index: int = 0
    units_used: float = 1.0
    quota_snapshot: dict[str, Any] | None = None


class SearchProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        error_type: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.error_type = error_type
        self.status_code = status_code
        # 失败调用的 key 归属（BaseSearchProvider.search 捕获后回填；路由层冷却错误无此值）
        self.key_index: int | None = None


class BaseSearchProvider:
    provider_name = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_keys: list[str] | tuple[str, ...] | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        keys = [str(key) for key in (api_keys or []) if str(key)]
        if not keys and api_key:
            keys = [api_key]
        self._api_keys = keys or [""]
        self.api_key = self._api_keys[0]
        self._key_index = 0
        self._key_lock = threading.Lock()
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        # 单次调用内传递响应观测数据（如 brave 响应头额度快照）。
        # provider 实例是跨线程共享的路由单例成员，必须 thread-local 才不会串调用。
        self._tls = threading.local()

    def _next_api_key(self) -> str:
        """多账号轮询：每次取一个 key 并前移游标（线程安全）。单 key 时直接返回。"""
        return self._next_key_slot()[0]

    def _next_key_slot(self) -> tuple[str, int]:
        """同 _next_api_key，额外返回 key 下标（用量记账按 key 归属）。"""
        if len(self._api_keys) <= 1:
            return self._api_keys[0], 0
        with self._key_lock:
            index = self._key_index % len(self._api_keys)
            self._key_index += 1
            return self._api_keys[index], index

    def search(self, query: str) -> ProviderSearchResult:
        api_key, key_index = self._next_key_slot()
        self._tls.quota_snapshot = None
        try:
            payload = self._request_payload(query, api_key)
        except SearchProviderError as exc:
            exc.key_index = key_index
            raise
        items = self._parse_items(payload)
        return ProviderSearchResult(
            provider=self.provider_name,
            items=items,
            result_type="empty_result" if not items else "success",
            key_index=key_index,
            units_used=self._units_used(payload),
            quota_snapshot=getattr(self._tls, "quota_snapshot", None),
        )

    def _units_used(self, payload: dict[str, Any]) -> float:
        """本次成功调用消耗的 provider 计量单位；默认 1 次/调用，serper 覆写读响应体真值。"""
        return 1.0

    def _observe_response(self, response) -> None:
        """成功响应的观测钩子（默认 no-op）；brave 覆写解析响应头额度快照。"""

    def _request_payload(self, query: str, api_key: str) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_items(self, payload: dict[str, Any]) -> list[SearchItem]:
        raise NotImplementedError

    def _request_json(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        try:
            response = getattr(self.session, method)(
                url,
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise SearchProviderError(
                self.provider_name,
                "timeout",
                str(exc) or "request timed out",
            ) from exc
        except requests.RequestException as exc:
            raise SearchProviderError(
                self.provider_name,
                "backend_error",
                str(exc) or "request failed",
            ) from exc

        if response.status_code >= 400:
            self._raise_response_error(response)

        try:
            self._observe_response(response)
        except Exception:
            # 观测钩子纯附加值（额度快照），任何解析问题都不能影响搜索本身。
            pass

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                self.provider_name,
                "backend_error",
                "provider returned invalid json",
                status_code=response.status_code,
            ) from exc

        if not isinstance(payload, dict):
            raise SearchProviderError(
                self.provider_name,
                "backend_error",
                "provider returned invalid payload",
                status_code=response.status_code,
            )
        return payload

    def _raise_response_error(self, response) -> None:
        status_code = int(getattr(response, "status_code", 0) or 0)
        message = str(getattr(response, "text", "") or "").strip() or f"http {status_code}"
        if status_code == 429:
            error_type = "rate_limited"
        elif status_code in {401, 403}:
            error_type = "auth_failed"
        elif status_code in {402, 432, 433}:
            error_type = "quota_exhausted"
        elif status_code == 408:
            error_type = "timeout"
        elif status_code >= 500:
            error_type = "backend_error"
        else:
            error_type = "backend_error"
        raise SearchProviderError(
            self.provider_name,
            error_type,
            message,
            status_code=status_code,
        )

    def _build_item(
        self,
        *,
        title: str,
        snippet: str,
        url: str,
        position: int,
        score: float | int | None = None,
    ) -> SearchItem | None:
        clean_title = str(title or "").strip()
        clean_url = str(url or "").strip()
        if not clean_title or not clean_url:
            return None
        clean_snippet = str(snippet or "").strip()
        parsed = urlparse(clean_url)
        domain = parsed.netloc
        normalized_score = float(score) if isinstance(score, (int, float)) else 1.0 / max(position, 1)
        return SearchItem(
            title=clean_title,
            snippet=clean_snippet,
            url=clean_url,
            domain=domain,
            score=normalized_score,
        )

    def _coerce_position(self, raw_position: Any, fallback: int) -> int:
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            return fallback
        return position if position > 0 else fallback


class SerperProvider(BaseSearchProvider):
    provider_name = "serper"
    endpoint = "https://google.serper.dev/search"

    def _request_payload(self, query: str, api_key: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            json={"q": query},
        )

    def _parse_items(self, payload: dict[str, Any]) -> list[SearchItem]:
        items: list[SearchItem] = []
        for position, raw_item in enumerate(payload.get("organic") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = self._build_item(
                title=raw_item.get("title", ""),
                snippet=raw_item.get("snippet", ""),
                url=raw_item.get("link", ""),
                position=self._coerce_position(raw_item.get("position"), position),
            )
            if item is not None:
                items.append(item)
        return items

    def _units_used(self, payload: dict[str, Any]) -> float:
        # serper 响应体带本次消耗的 credits（如带 num 参数一次可扣多枚）；缺失/畸形回退 1。
        credits = payload.get("credits")
        if isinstance(credits, bool) or not isinstance(credits, (int, float)):
            return 1.0
        if not 0 <= credits <= 1000:
            return 1.0
        return float(credits)


def _parse_rate_limit_month_value(raw: object) -> int | None:
    """brave 限流头是逗号分隔的「每秒段, 每月段」，取每月段；解析失败返回 None。"""
    parts = [p.strip() for p in str(raw or "").split(",")]
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def parse_brave_quota_headers(headers) -> dict[str, Any] | None:
    """从 brave 响应头解析月度额度快照；头缺失/两段格式不符时返回 None。

    注意：brave 文档标注月配额段为 0 表示 unlimited（计量计费档）——0 原样透出，
    由展示层解释，不在此处猜语义。
    """
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    remaining = _parse_rate_limit_month_value(getter("X-RateLimit-Remaining"))
    limit = _parse_rate_limit_month_value(getter("X-RateLimit-Limit"))
    if remaining is None and limit is None:
        return None
    return {
        "month_remaining": remaining,
        "month_limit": limit,
        "observed_at": time.time(),
    }


class BraveProvider(BaseSearchProvider):
    provider_name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def _observe_response(self, response) -> None:
        snapshot = parse_brave_quota_headers(getattr(response, "headers", None) or {})
        if snapshot is not None:
            self._tls.quota_snapshot = snapshot

    def _request_payload(self, query: str, api_key: str) -> dict[str, Any]:
        return self._request_json(
            "get",
            self.endpoint,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={
                "q": query,
                "count": DEFAULT_MAX_RESULTS,
            },
        )

    def _parse_items(self, payload: dict[str, Any]) -> list[SearchItem]:
        web_payload = payload.get("web") or {}
        if not isinstance(web_payload, dict):
            return []
        items: list[SearchItem] = []
        for position, raw_item in enumerate(web_payload.get("results") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = self._build_item(
                title=raw_item.get("title", ""),
                snippet=raw_item.get("description", ""),
                url=raw_item.get("url", ""),
                position=position,
            )
            if item is not None:
                items.append(item)
        return items


class TavilyProvider(BaseSearchProvider):
    provider_name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def _request_payload(self, query: str, api_key: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": DEFAULT_MAX_RESULTS,
            },
        )

    def _parse_items(self, payload: dict[str, Any]) -> list[SearchItem]:
        items: list[SearchItem] = []
        for position, raw_item in enumerate(payload.get("results") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = self._build_item(
                title=raw_item.get("title", ""),
                snippet=raw_item.get("content", ""),
                url=raw_item.get("url", ""),
                position=position,
                score=raw_item.get("score"),
            )
            if item is not None:
                items.append(item)
        return items


class ExaProvider(BaseSearchProvider):
    provider_name = "exa"
    endpoint = "https://api.exa.ai/search"

    def _request_payload(self, query: str, api_key: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "numResults": DEFAULT_MAX_RESULTS,
                "contents": {"text": True},
            },
        )

    def _parse_items(self, payload: dict[str, Any]) -> list[SearchItem]:
        items: list[SearchItem] = []
        for position, raw_item in enumerate(payload.get("results") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = self._build_item(
                title=raw_item.get("title", ""),
                snippet=raw_item.get("text") or raw_item.get("summary", ""),
                url=raw_item.get("url", ""),
                position=position,
            )
            if item is not None:
                items.append(item)
        return items
