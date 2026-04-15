from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any

from backend.config import get_search_cache_path, get_search_runtime_state_path


_TRAILING_PUNCTUATION = " \t\r\n!?,.;:，。！？；：、~～"
_RUNTIME_STATE_LOCK = threading.RLock()
_FILE_CACHE_LOCK = threading.RLock()


def normalize_search_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query or "").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.rstrip(_TRAILING_PUNCTUATION)
    return re.sub(r"\s+", " ", normalized).strip()


@dataclass
class ProviderState:
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    last_success_at: float | None = None
    last_error_type: str | None = None


class SearchStateStore:
    def __init__(
        self,
        runtime_state_path: Path | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.runtime_state_path = runtime_state_path or get_search_runtime_state_path()
        self.cache_path = cache_path or get_search_cache_path()
        self._memory_cache: dict[str, dict[str, Any]] = {}
        self._runtime_state = self._load_runtime_state()
        self._file_cache = self._load_file_cache()

    def get_cache(self, query: str, *, project_id: str) -> dict[str, Any] | None:
        normalized_query = normalize_search_query(query)
        memory_hit = self._get_memory_cache(normalized_query, project_id)
        if memory_hit is not None:
            return memory_hit

        project_hit = self._get_project_cache(normalized_query, project_id)
        if project_hit is not None:
            return project_hit
        return None

    def put_cache(
        self,
        query: str,
        *,
        project_id: str,
        value: dict[str, Any],
        memory_ttl_seconds: int,
        project_ttl_seconds: int,
    ) -> None:
        normalized_query = normalize_search_query(query)
        cache_key = self._make_cache_key(normalized_query, project_id)
        now = time.time()
        cached_value = deepcopy(value)

        with _FILE_CACHE_LOCK:
            self._file_cache = self._load_file_cache()
            self._memory_cache[cache_key] = {
                "value": cached_value,
                "expires_at": now + memory_ttl_seconds,
            }
            self._file_cache[cache_key] = {
                "project_id": project_id,
                "query": normalized_query,
                "value": cached_value,
                "memory_ttl_seconds": memory_ttl_seconds,
                "expires_at": now + project_ttl_seconds,
            }
            self._save_file_cache()

    def get_provider_state(self, provider: str) -> ProviderState:
        with _RUNTIME_STATE_LOCK:
            self._runtime_state = self._load_runtime_state()
            return self._provider_state_from_runtime(provider)

    def mark_provider_failure(self, provider: str, *, error_type: str, cooldown_seconds: int) -> None:
        with _RUNTIME_STATE_LOCK:
            self._runtime_state = self._load_runtime_state()
            provider_state = self._provider_state_from_runtime(provider)
            provider_state.consecutive_failures += 1
            provider_state.cooldown_until = time.time() + cooldown_seconds if cooldown_seconds > 0 else None
            provider_state.last_error_type = error_type
            self._set_provider_state(provider, provider_state)

    def mark_provider_success(self, provider: str) -> None:
        with _RUNTIME_STATE_LOCK:
            self._runtime_state = self._load_runtime_state()
            provider_state = self._provider_state_from_runtime(provider)
            provider_state.consecutive_failures = 0
            provider_state.cooldown_until = None
            provider_state.last_error_type = None
            provider_state.last_success_at = time.time()
            self._set_provider_state(provider, provider_state)

    def _get_memory_cache(self, normalized_query: str, project_id: str) -> dict[str, Any] | None:
        cache_key = self._make_cache_key(normalized_query, project_id)
        entry = self._memory_cache.get(cache_key)
        if not entry:
            return None
        if entry["expires_at"] <= time.time():
            self._memory_cache.pop(cache_key, None)
            return None
        return deepcopy(entry["value"])

    def _get_project_cache(self, normalized_query: str, project_id: str) -> dict[str, Any] | None:
        with _FILE_CACHE_LOCK:
            self._file_cache = self._load_file_cache()
            cache_key = self._make_cache_key(normalized_query, project_id)
            entry = self._file_cache.get(cache_key)
            if not entry:
                return None
            now = time.time()
            if entry["expires_at"] <= now:
                self._file_cache.pop(cache_key, None)
                self._save_file_cache()
                return None

            value = deepcopy(entry["value"])
            memory_expires_at = min(
                entry["expires_at"],
                now + int(entry.get("memory_ttl_seconds", 0) or 0),
            )
            self._memory_cache[cache_key] = {
                "value": value,
                "expires_at": memory_expires_at,
            }
            return deepcopy(value)

    def _set_provider_state(self, provider: str, provider_state: ProviderState) -> None:
        self._runtime_state = self._load_runtime_state()
        providers = self._runtime_state.setdefault("providers", {})
        providers[provider] = asdict(provider_state)
        self._save_runtime_state()

    def _provider_state_from_runtime(self, provider: str) -> ProviderState:
        provider_payload = (self._runtime_state.get("providers") or {}).get(provider) or {}
        return ProviderState(
            consecutive_failures=int(provider_payload.get("consecutive_failures", 0) or 0),
            cooldown_until=provider_payload.get("cooldown_until"),
            last_success_at=provider_payload.get("last_success_at"),
            last_error_type=provider_payload.get("last_error_type"),
        )

    def _load_runtime_state(self) -> dict[str, Any]:
        return self._load_json_object(self.runtime_state_path, default={"version": 1, "providers": {}})

    def _load_file_cache(self) -> dict[str, dict[str, Any]]:
        payload = self._load_json_object(self.cache_path, default={"version": 1, "entries": {}})
        entries = payload.get("entries")
        if isinstance(entries, dict):
            return entries
        return {}

    def _save_runtime_state(self) -> None:
        self._write_json_object(self.runtime_state_path, self._runtime_state)

    def _save_file_cache(self) -> None:
        self._write_json_object(self.cache_path, {"version": 1, "entries": self._file_cache})

    def _load_json_object(self, path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return deepcopy(default)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return deepcopy(default)
        if not isinstance(payload, dict):
            return deepcopy(default)
        return payload

    def _write_json_object(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _make_cache_key(self, normalized_query: str, project_id: str) -> str:
        return f"{project_id}::{normalized_query}"
