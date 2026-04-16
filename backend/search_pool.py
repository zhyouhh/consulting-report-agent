from __future__ import annotations

from dataclasses import asdict
import threading
import time
from typing import Callable

from backend.config import ManagedSearchPoolConfig
from backend.search_providers import ProviderSearchResult, SearchItem, SearchProviderError
from backend.search_state import SearchStateStore


class SearchRouter:
    PROJECT_WINDOW_SECONDS = 300
    GLOBAL_WINDOW_SECONDS = 60
    QUOTA_EXHAUSTED_MESSAGE = "当前内置搜索额度已用尽，请稍后再试"

    def __init__(
        self,
        *,
        config: ManagedSearchPoolConfig,
        state_store: SearchStateStore,
        providers: dict[str, object],
    ) -> None:
        self.config = config
        self.state_store = state_store
        self.providers = providers
        self._layer_cursors = {"primary": 0, "secondary": 0}
        self._cursor_lock = threading.Lock()

    def search(
        self,
        query: str,
        *,
        project_id: str,
        turn_search_count: int,
        native_search: Callable[[str], ProviderSearchResult | None] | None = None,
    ) -> dict:
        if turn_search_count >= self.config.limits.per_turn_searches:
            return self._quota_exhausted_result(limit_scope="per_turn")

        cached = self.state_store.get_cache(query, project_id=project_id)
        if cached is not None:
            return self._build_cached_result(cached)

        limit_scope = self.state_store.try_acquire_search_slot(
            project_id=project_id,
            project_window_seconds=self.PROJECT_WINDOW_SECONDS,
            project_limit=self.config.limits.project_minute_limit,
            global_window_seconds=self.GLOBAL_WINDOW_SECONDS,
            global_limit=self.config.limits.global_minute_limit,
        )
        if limit_scope is not None:
            return self._quota_exhausted_result(limit_scope=limit_scope)

        empty_result: ProviderSearchResult | None = None
        provider_errors: list[SearchProviderError] = []
        for layer_name, provider_names in (
            ("primary", self.config.routing.primary),
            ("secondary", self.config.routing.secondary),
        ):
            for provider_name in self._iter_layer_candidates(layer_name, provider_names):
                cooldown_error = self._get_cooldown_error(provider_name)
                if cooldown_error is not None:
                    provider_errors.append(cooldown_error)
                    continue
                provider = self.providers.get(provider_name)
                if provider is None:
                    continue
                try:
                    provider_result = provider.search(query)
                except SearchProviderError as exc:
                    provider_errors.append(exc)
                    self._mark_provider_failure(provider_name, exc)
                    continue

                self.state_store.mark_provider_success(provider_name)
                if provider_result.result_type == "empty_result":
                    empty_result = provider_result
                    continue

                payload = self._build_success_result(
                    provider_result,
                    cached=False,
                    native_fallback_used=False,
                )
                self._cache_result(query, project_id, payload)
                return payload

        if empty_result is not None:
            payload = self._build_success_result(
                empty_result,
                cached=False,
                native_fallback_used=False,
            )
            self._cache_result(query, project_id, payload)
            return payload

        if self.config.routing.native_fallback and native_search is not None:
            try:
                native_result = native_search(query)
            except Exception:
                native_result = None
            if native_result is not None:
                payload = self._build_success_result(
                    native_result,
                    cached=False,
                    native_fallback_used=True,
                )
                self._cache_result(query, project_id, payload)
                return payload

        return self._build_terminal_error_result(provider_errors)

    def _iter_layer_candidates(self, layer_name: str, provider_names: list[str]) -> list[str]:
        weighted_names: list[str] = []
        for provider_name in provider_names:
            provider_config = self.config.providers.get(provider_name)
            if not provider_config or not provider_config.enabled:
                continue
            weighted_names.extend([provider_name] * max(provider_config.weight, 1))

        if not weighted_names:
            return []

        with self._cursor_lock:
            start = self._layer_cursors[layer_name] % len(weighted_names)
            self._layer_cursors[layer_name] = (start + 1) % len(weighted_names)

        ordered_candidates: list[str] = []
        seen: set[str] = set()
        for offset in range(len(weighted_names)):
            candidate = weighted_names[(start + offset) % len(weighted_names)]
            if candidate in seen:
                continue
            ordered_candidates.append(candidate)
            seen.add(candidate)
        return ordered_candidates

    def _get_cooldown_error(self, provider_name: str) -> SearchProviderError | None:
        state = self.state_store.get_provider_state(provider_name)
        if not (state.cooldown_until and state.cooldown_until > time.time()):
            return None
        if not state.last_error_type:
            return SearchProviderError(provider_name, "quota_exhausted", "provider cooling down")
        return SearchProviderError(provider_name, state.last_error_type, "provider cooling down")

    def _mark_provider_failure(self, provider_name: str, error: SearchProviderError) -> None:
        provider_config = self.config.providers.get(provider_name)
        cooldown_seconds = provider_config.cooldown_seconds if provider_config else 0
        self.state_store.mark_provider_failure(
            provider_name,
            error_type=error.error_type,
            cooldown_seconds=cooldown_seconds,
        )

    def _build_cached_result(self, cached_payload: dict) -> dict:
        items = cached_payload.get("items") or []
        result_type = str(cached_payload.get("result_type") or "success")
        return {
            "status": "success",
            "provider": cached_payload.get("provider", ""),
            "cached": True,
            "native_fallback_used": bool(cached_payload.get("native_fallback_used", False)),
            "items": items,
            "result_type": result_type,
            "results": self._format_results(items, result_type=result_type),
        }

    def _build_success_result(
        self,
        provider_result: ProviderSearchResult,
        *,
        cached: bool,
        native_fallback_used: bool,
    ) -> dict:
        items = [self._item_to_dict(item) for item in provider_result.items]
        return {
            "status": "success",
            "provider": provider_result.provider,
            "cached": cached,
            "native_fallback_used": native_fallback_used,
            "items": items,
            "result_type": provider_result.result_type,
            "results": self._format_results(items, result_type=provider_result.result_type),
        }

    def _cache_result(self, query: str, project_id: str, payload: dict) -> None:
        self.state_store.put_cache(
            query,
            project_id=project_id,
            value={
                "provider": payload["provider"],
                "items": payload["items"],
                "result_type": payload["result_type"],
                "native_fallback_used": payload["native_fallback_used"],
            },
            memory_ttl_seconds=self.config.limits.memory_cache_ttl_seconds,
            project_ttl_seconds=self.config.limits.project_cache_ttl_seconds,
        )

    def _quota_exhausted_result(self, *, limit_scope: str) -> dict:
        return {
            "status": "error",
            "error_type": "quota_exhausted",
            "limit_scope": limit_scope,
            "message": self.QUOTA_EXHAUSTED_MESSAGE,
            "cached": False,
            "native_fallback_used": False,
            "items": [],
        }

    def _build_terminal_error_result(self, provider_errors: list[SearchProviderError]) -> dict:
        for error in provider_errors:
            if error.error_type not in {"rate_limited", "quota_exhausted"}:
                return {
                    "status": "error",
                    "error_type": error.error_type,
                    "message": self._message_for_provider_error(error.error_type),
                    "cached": False,
                    "native_fallback_used": False,
                    "items": [],
                }
        return self._quota_exhausted_result(limit_scope="pool_exhausted")

    def _message_for_provider_error(self, error_type: str) -> str:
        if error_type == "auth_failed":
            return "内置搜索池鉴权失败，请检查搜索池配置。"
        if error_type == "timeout":
            return "搜索服务暂时超时，请稍后再试。"
        return "搜索服务暂时不可用，请稍后再试。"

    def _item_to_dict(self, item: SearchItem) -> dict:
        return asdict(item)

    def _format_results(self, items: list[dict], *, result_type: str) -> str:
        if result_type == "empty_result" or not items:
            return "未找到相关信息"
        lines = ["搜索结果："]
        for index, item in enumerate(items[:5], start=1):
            lines.append(f"{index}. {item['title']}")
            lines.append(str(item.get("snippet", "")).strip() or "无摘要")
            lines.append(f"链接: {item['url']}")
            lines.append("")
        return "\n".join(lines).strip()
