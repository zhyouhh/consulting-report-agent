import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import (
    ManagedSearchLimitsConfig,
    ManagedSearchPoolConfig,
    ManagedSearchProviderConfig,
    ManagedSearchRoutingConfig,
)
from backend.search_pool import SearchRouter
from backend.search_providers import (
    ProviderSearchResult,
    SearchItem,
    SearchProviderError,
)
from backend.search_state import SearchStateStore


def _provider_cfg(weight=1):
    return ManagedSearchProviderConfig(
        enabled=True,
        api_key="k",
        weight=weight,
        minute_limit=60,
        daily_soft_limit=1200,
        cooldown_seconds=180,
    )


def _make_config(
    *,
    primary=None,
    secondary=None,
    per_turn_searches=2,
    project_minute_limit=10,
    global_minute_limit=20,
):
    return ManagedSearchPoolConfig(
        version=1,
        providers={
            "serper": _provider_cfg(weight=5),
            "brave": _provider_cfg(weight=3),
            "tavily": _provider_cfg(weight=1),
            "exa": _provider_cfg(weight=1),
        },
        routing=ManagedSearchRoutingConfig(
            primary=primary if primary is not None else ["serper", "brave"],
            secondary=secondary if secondary is not None else ["tavily", "exa"],
            native_fallback=True,
        ),
        limits=ManagedSearchLimitsConfig(
            per_turn_searches=per_turn_searches,
            project_minute_limit=project_minute_limit,
            global_minute_limit=global_minute_limit,
            memory_cache_ttl_seconds=60,
            project_cache_ttl_seconds=300,
        ),
    )


def _make_result(provider, title="result"):
    return ProviderSearchResult(
        provider=provider,
        items=[
            SearchItem(
                title=title,
                snippet="snippet",
                url=f"https://example.com/{provider}",
                domain="example.com",
                score=1.0,
            )
        ],
        result_type="success",
    )


class FakeProvider:
    def __init__(self, outcome):
        self.outcomes = list(outcome) if isinstance(outcome, list) else [outcome]
        self.calls = []

    def search(self, query: str):
        self.calls.append(query)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SearchRouterTests(unittest.TestCase):
    def _make_state(self, tmpdir):
        return SearchStateStore(
            runtime_state_path=Path(tmpdir) / "state.json",
            cache_path=Path(tmpdir) / "cache.json",
        )

    def test_router_prefers_primary_layer_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(_make_result("serper", "serper result")),
                    "brave": FakeProvider(_make_result("brave", "brave result")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            result = router.search("猪猪侠", project_id="demo", turn_search_count=0)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "serper")
        self.assertFalse(result["native_fallback_used"])

    def test_router_rotates_primary_selection_by_weight(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(
                    primary=["serper", "brave"],
                    secondary=[],
                    project_minute_limit=100,
                    global_minute_limit=100,
                ),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(_make_result("serper", "serper result")),
                    "brave": FakeProvider(_make_result("brave", "brave result")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            providers = []
            for index in range(8):
                result = router.search(f"query-{index}", project_id="demo", turn_search_count=0)
                providers.append(result["provider"])

        self.assertEqual(
            providers,
            ["serper", "serper", "serper", "serper", "serper", "brave", "brave", "brave"],
        )

    def test_router_returns_cached_result_before_dispatching_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            state.put_cache(
                "猪猪侠",
                project_id="demo",
                value={
                    "provider": "serper",
                    "items": [
                        {
                            "title": "cached",
                            "snippet": "cached snippet",
                            "url": "https://example.com/cached",
                            "domain": "example.com",
                            "score": 1.0,
                        }
                    ],
                    "result_type": "success",
                },
                memory_ttl_seconds=60,
                project_ttl_seconds=300,
            )
            provider = FakeProvider(_make_result("serper", "live result"))
            router = SearchRouter(
                config=_make_config(),
                state_store=state,
                providers={
                    "serper": provider,
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            result = router.search("猪猪侠", project_id="demo", turn_search_count=0)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["cached"])
        self.assertEqual(result["provider"], "serper")
        self.assertEqual(provider.calls, [])

    def test_router_skips_primary_provider_in_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            with mock.patch("backend.search_state.time.time", return_value=1000):
                state.mark_provider_failure("serper", error_type="rate_limited", cooldown_seconds=180)

            router = SearchRouter(
                config=_make_config(),
                state_store=state,
                providers={
                    "serper": FakeProvider(_make_result("serper")),
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            with mock.patch("backend.search_pool.time.time", return_value=1001):
                result = router.search("猪猪侠", project_id="demo", turn_search_count=0)

        self.assertEqual(result["provider"], "brave")

    def test_router_falls_to_secondary_layer_when_primary_layer_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rate_limited = SearchProviderError("serper", "rate_limited", "too many requests")
            backend_error = SearchProviderError("brave", "backend_error", "oops")
            router = SearchRouter(
                config=_make_config(),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(rate_limited),
                    "brave": FakeProvider(backend_error),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            result = router.search("猪猪侠", project_id="demo", turn_search_count=0)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "tavily")

    def test_router_blocks_after_two_searches_in_same_turn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(per_turn_searches=2),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(_make_result("serper")),
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            result = router.search("第三次", project_id="demo", turn_search_count=2)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "quota_exhausted")

    def test_router_blocks_project_when_project_minute_limit_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(project_minute_limit=1, global_minute_limit=20),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider([_make_result("serper", "first"), _make_result("serper", "second")]),
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            with mock.patch("backend.search_pool.time.time", return_value=1000), mock.patch(
                "backend.search_state.time.time", return_value=1000
            ):
                first = router.search("第一次", project_id="demo", turn_search_count=0)

            with mock.patch("backend.search_pool.time.time", return_value=1001), mock.patch(
                "backend.search_state.time.time", return_value=1001
            ):
                second = router.search("第二次", project_id="demo", turn_search_count=1)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "error")
        self.assertEqual(second["error_type"], "quota_exhausted")

    def test_router_blocks_globally_when_global_minute_limit_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(project_minute_limit=10, global_minute_limit=1),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider([_make_result("serper", "first"), _make_result("serper", "second")]),
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            with mock.patch("backend.search_pool.time.time", return_value=1000), mock.patch(
                "backend.search_state.time.time", return_value=1000
            ):
                first = router.search("第一次", project_id="project-a", turn_search_count=0)

            with mock.patch("backend.search_pool.time.time", return_value=1001), mock.patch(
                "backend.search_state.time.time", return_value=1001
            ):
                second = router.search("第二次", project_id="project-b", turn_search_count=0)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "error")
        self.assertEqual(second["error_type"], "quota_exhausted")

    def test_router_uses_native_fallback_only_after_all_pool_providers_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            failure = SearchProviderError("serper", "rate_limited", "too many requests")
            native = mock.Mock(return_value=_make_result("native", "native result"))
            router = SearchRouter(
                config=_make_config(),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(failure),
                    "brave": FakeProvider(SearchProviderError("brave", "backend_error", "oops")),
                    "tavily": FakeProvider(SearchProviderError("tavily", "timeout", "slow")),
                    "exa": FakeProvider(SearchProviderError("exa", "quota_exhausted", "quota")),
                },
            )

            result = router.search(
                "猪猪侠",
                project_id="demo",
                turn_search_count=0,
                native_search=native,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "native")
        self.assertTrue(result["native_fallback_used"])
        native.assert_called_once_with("猪猪侠")

    def test_router_preserves_native_fallback_flag_on_cached_hits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            native = mock.Mock(return_value=_make_result("native", "native result"))
            router = SearchRouter(
                config=_make_config(project_minute_limit=100, global_minute_limit=100),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(SearchProviderError("serper", "rate_limited", "too many requests")),
                    "brave": FakeProvider(SearchProviderError("brave", "rate_limited", "too many requests")),
                    "tavily": FakeProvider(SearchProviderError("tavily", "quota_exhausted", "quota")),
                    "exa": FakeProvider(SearchProviderError("exa", "quota_exhausted", "quota")),
                },
            )

            first = router.search(
                "猪猪侠",
                project_id="demo",
                turn_search_count=0,
                native_search=native,
            )
            second = router.search(
                "猪猪侠",
                project_id="demo",
                turn_search_count=0,
                native_search=native,
            )

        self.assertEqual(first["status"], "success")
        self.assertTrue(first["native_fallback_used"])
        self.assertEqual(second["status"], "success")
        self.assertTrue(second["cached"])
        self.assertTrue(second["native_fallback_used"])

    def test_router_returns_quota_exhausted_when_native_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(SearchProviderError("serper", "rate_limited", "too many requests")),
                    "brave": FakeProvider(SearchProviderError("brave", "rate_limited", "too many requests")),
                    "tavily": FakeProvider(SearchProviderError("tavily", "quota_exhausted", "quota")),
                    "exa": FakeProvider(SearchProviderError("exa", "quota_exhausted", "quota")),
                },
            )

            result = router.search(
                "猪猪侠",
                project_id="demo",
                turn_search_count=0,
                native_search=mock.Mock(return_value=None),
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "quota_exhausted")

    def test_router_returns_backend_error_when_pool_failed_for_service_reasons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(SearchProviderError("serper", "backend_error", "oops")),
                    "brave": FakeProvider(SearchProviderError("brave", "timeout", "slow")),
                    "tavily": FakeProvider(SearchProviderError("tavily", "rate_limited", "too many requests")),
                    "exa": FakeProvider(SearchProviderError("exa", "quota_exhausted", "quota")),
                },
            )

            result = router.search(
                "猪猪侠",
                project_id="demo",
                turn_search_count=0,
                native_search=mock.Mock(return_value=None),
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "backend_error")

    def test_router_preserves_service_error_when_provider_is_still_in_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SearchRouter(
                config=_make_config(primary=["serper"], secondary=[]),
                state_store=self._make_state(tmpdir),
                providers={
                    "serper": FakeProvider(SearchProviderError("serper", "backend_error", "oops")),
                    "brave": FakeProvider(_make_result("brave")),
                    "tavily": FakeProvider(_make_result("tavily")),
                    "exa": FakeProvider(_make_result("exa")),
                },
            )

            with mock.patch("backend.search_pool.time.time", return_value=1000), mock.patch(
                "backend.search_state.time.time", return_value=1000
            ):
                first = router.search(
                    "第一次",
                    project_id="demo",
                    turn_search_count=0,
                    native_search=mock.Mock(return_value=None),
                )

            with mock.patch("backend.search_pool.time.time", return_value=1001), mock.patch(
                "backend.search_state.time.time", return_value=1001
            ):
                second = router.search(
                    "第二次",
                    project_id="demo",
                    turn_search_count=0,
                    native_search=mock.Mock(return_value=None),
                )

        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error_type"], "backend_error")
        self.assertEqual(second["status"], "error")
        self.assertEqual(second["error_type"], "backend_error")


if __name__ == "__main__":
    unittest.main()
