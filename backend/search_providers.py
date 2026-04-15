from __future__ import annotations

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


class BaseSearchProvider:
    provider_name = ""

    def __init__(
        self,
        *,
        api_key: str,
        session: requests.Session | Any | None = None,
        timeout_seconds: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def search(self, query: str) -> ProviderSearchResult:
        payload = self._request_payload(query)
        items = self._parse_items(payload)
        return ProviderSearchResult(
            provider=self.provider_name,
            items=items,
            result_type="empty_result" if not items else "success",
        )

    def _request_payload(self, query: str) -> dict[str, Any]:
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

    def _request_payload(self, query: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "X-API-KEY": self.api_key,
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


class BraveProvider(BaseSearchProvider):
    provider_name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def _request_payload(self, query: str) -> dict[str, Any]:
        return self._request_json(
            "get",
            self.endpoint,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
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

    def _request_payload(self, query: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
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

    def _request_payload(self, query: str) -> dict[str, Any]:
        return self._request_json(
            "post",
            self.endpoint,
            headers={
                "x-api-key": self.api_key,
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
