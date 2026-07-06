import unittest
from unittest import mock

import requests

from backend.search_providers import (
    BraveProvider,
    ExaProvider,
    ProviderSearchResult,
    SearchProviderError,
    SerperProvider,
    TavilyProvider,
)


def _mock_response(status_code=200, payload=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = payload if payload is not None else {}
    return response


class SearchProvidersTests(unittest.TestCase):
    def test_serper_adapter_maps_organic_results(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(
            payload={
                "organic": [
                    {
                        "title": "猪猪侠",
                        "snippet": "动画系列",
                        "link": "https://example.com/a",
                        "position": 1,
                    }
                ]
            }
        )

        adapter = SerperProvider(api_key="k", session=session)
        result = adapter.search("猪猪侠")

        self.assertIsInstance(result, ProviderSearchResult)
        self.assertEqual(result.provider, "serper")
        self.assertEqual(result.items[0].title, "猪猪侠")
        self.assertEqual(result.items[0].snippet, "动画系列")
        self.assertEqual(result.items[0].url, "https://example.com/a")
        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.kwargs["headers"]["X-API-KEY"], "k")

    def test_serper_adapter_tolerates_invalid_position_value(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(
            payload={
                "organic": [
                    {
                        "title": "猪猪侠",
                        "snippet": "动画系列",
                        "link": "https://example.com/a",
                        "position": "top",
                    }
                ]
            }
        )

        adapter = SerperProvider(api_key="k", session=session)
        result = adapter.search("猪猪侠")

        self.assertEqual(result.provider, "serper")
        self.assertEqual(result.items[0].title, "猪猪侠")

    def test_tavily_adapter_maps_results(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(
            payload={
                "results": [
                    {
                        "title": "OpenAI news",
                        "content": "latest updates",
                        "url": "https://example.com/openai",
                        "score": 0.8,
                    }
                ]
            }
        )

        adapter = TavilyProvider(api_key="k", session=session)
        result = adapter.search("OpenAI")

        self.assertEqual(result.provider, "tavily")
        self.assertEqual(result.items[0].title, "OpenAI news")
        self.assertEqual(result.items[0].snippet, "latest updates")
        self.assertEqual(result.items[0].score, 0.8)
        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer k")

    def test_exa_adapter_maps_results(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(
            payload={
                "results": [
                    {
                        "title": "LLM paper",
                        "text": "paper summary",
                        "url": "https://example.com/paper",
                    }
                ]
            }
        )

        adapter = ExaProvider(api_key="k", session=session)
        result = adapter.search("Latest research in LLMs")

        self.assertEqual(result.provider, "exa")
        self.assertEqual(result.items[0].title, "LLM paper")
        self.assertEqual(result.items[0].snippet, "paper summary")
        self.assertEqual(result.items[0].domain, "example.com")
        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.kwargs["headers"]["x-api-key"], "k")

    def test_brave_adapter_maps_web_results(self):
        session = mock.Mock()
        session.get.return_value = _mock_response(
            payload={
                "web": {
                    "results": [
                        {
                            "title": "Brave result",
                            "description": "web snippet",
                            "url": "https://example.com/brave",
                        }
                    ]
                }
            }
        )

        adapter = BraveProvider(api_key="k", session=session)
        result = adapter.search("Brave")

        self.assertEqual(result.provider, "brave")
        self.assertEqual(result.items[0].title, "Brave result")
        self.assertEqual(result.items[0].snippet, "web snippet")
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.kwargs["headers"]["X-Subscription-Token"], "k")

    def test_provider_maps_429_to_rate_limited(self):
        session = mock.Mock()
        session.get.return_value = _mock_response(status_code=429, text="too many requests")
        adapter = BraveProvider(api_key="k", session=session)

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("猪猪侠")

        self.assertEqual(exc.exception.provider, "brave")
        self.assertEqual(exc.exception.error_type, "rate_limited")

    def test_provider_maps_auth_error(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(status_code=401, text="unauthorized")
        adapter = TavilyProvider(api_key="k", session=session)

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("OpenAI")

        self.assertEqual(exc.exception.provider, "tavily")
        self.assertEqual(exc.exception.error_type, "auth_failed")

    def test_provider_maps_quota_exhausted(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(status_code=402, text="quota exhausted")
        adapter = ExaProvider(api_key="k", session=session)

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("OpenAI")

        self.assertEqual(exc.exception.provider, "exa")
        self.assertEqual(exc.exception.error_type, "quota_exhausted")

    def test_provider_maps_timeout(self):
        session = mock.Mock()
        session.post.side_effect = requests.Timeout("timed out")
        adapter = ExaProvider(api_key="k", session=session)

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("OpenAI")

        self.assertEqual(exc.exception.provider, "exa")
        self.assertEqual(exc.exception.error_type, "timeout")

    def test_provider_maps_empty_result_without_throwing(self):
        session = mock.Mock()
        session.get.return_value = _mock_response(payload={"web": {"results": []}})
        adapter = BraveProvider(api_key="k", session=session)

        result = adapter.search("猪猪侠")

        self.assertEqual(result.provider, "brave")
        self.assertEqual(result.items, [])
        self.assertEqual(result.result_type, "empty_result")

    def test_multi_key_round_robin_rotates_per_search(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": []})
        adapter = SerperProvider(api_keys=["a", "b", "c"], session=session)

        for _ in range(4):
            adapter.search("q")

        used = [
            call.kwargs["headers"]["X-API-KEY"]
            for call in session.post.call_args_list
        ]
        self.assertEqual(used, ["a", "b", "c", "a"])
        # api_key 暴露首个 key，向后兼容单 key 读取。
        self.assertEqual(adapter.api_key, "a")

    def test_single_key_via_api_keys_backward_compat(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": []})
        adapter = SerperProvider(api_keys=["solo"], session=session)

        adapter.search("q")

        self.assertEqual(
            session.post.call_args.kwargs["headers"]["X-API-KEY"], "solo"
        )


class SearchUsageAttributionTests(unittest.TestCase):
    """搜索池额度监控的记账元数据：key 归属 / serper credits / brave 响应头快照。"""

    def test_result_carries_rotating_key_index(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": []})
        adapter = SerperProvider(api_keys=["a", "b", "c"], session=session)

        indexes = [adapter.search("q").key_index for _ in range(4)]

        self.assertEqual(indexes, [0, 1, 2, 0])

    def test_single_key_result_key_index_is_zero(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": []})
        adapter = SerperProvider(api_key="k", session=session)

        self.assertEqual(adapter.search("q").key_index, 0)

    def test_provider_error_carries_key_index(self):
        session = mock.Mock()
        session.post.side_effect = [
            _mock_response(payload={"organic": []}),
            _mock_response(status_code=429, text="too many requests"),
        ]
        adapter = SerperProvider(api_keys=["a", "b"], session=session)
        adapter.search("q")   # 用掉 key 0

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("q")

        self.assertEqual(exc.exception.key_index, 1)

    def test_serper_units_used_reads_response_credits(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": [], "credits": 2})
        adapter = SerperProvider(api_key="k", session=session)

        self.assertEqual(adapter.search("q").units_used, 2.0)

    def test_serper_units_used_falls_back_to_one_on_malformed_credits(self):
        session = mock.Mock()
        for credits in (None, "two", True, -1, 10**9):
            payload = {"organic": []}
            if credits is not None:
                payload["credits"] = credits
            session.post.return_value = _mock_response(payload=payload)
            adapter = SerperProvider(api_key="k", session=session)
            self.assertEqual(adapter.search("q").units_used, 1.0)

    def test_non_serper_units_used_defaults_to_one(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"results": []})
        adapter = TavilyProvider(api_key="k", session=session)

        self.assertEqual(adapter.search("q").units_used, 1.0)

    def test_brave_quota_snapshot_from_rate_limit_headers(self):
        session = mock.Mock()
        response = _mock_response(payload={"web": {"results": []}})
        response.headers = {
            "X-RateLimit-Limit": "1, 1000",
            "X-RateLimit-Remaining": "1, 940",
        }
        session.get.return_value = response
        adapter = BraveProvider(api_key="k", session=session)

        result = adapter.search("q")

        self.assertIsNotNone(result.quota_snapshot)
        self.assertEqual(result.quota_snapshot["month_remaining"], 940)
        self.assertEqual(result.quota_snapshot["month_limit"], 1000)
        self.assertIn("observed_at", result.quota_snapshot)

    def test_brave_snapshot_none_when_headers_missing_or_malformed(self):
        session = mock.Mock()
        for headers in ({}, {"X-RateLimit-Remaining": "5"}, {"X-RateLimit-Remaining": "a, b"}):
            response = _mock_response(payload={"web": {"results": []}})
            response.headers = headers
            session.get.return_value = response
            adapter = BraveProvider(api_key="k", session=session)
            self.assertIsNone(adapter.search("q").quota_snapshot)

    def test_brave_quota_snapshot_captured_on_429_error(self):
        # 429 的响应头恰恰带着 remaining=0 的关键信号：必须先观测再抛错，快照挂错误对象
        session = mock.Mock()
        response = _mock_response(status_code=429, text="too many requests")
        response.headers = {
            "X-RateLimit-Limit": "1, 1000",
            "X-RateLimit-Remaining": "0, 0",
        }
        session.get.return_value = response
        adapter = BraveProvider(api_key="k", session=session)

        with self.assertRaises(SearchProviderError) as exc:
            adapter.search("q")

        self.assertEqual(exc.exception.error_type, "rate_limited")
        self.assertIsNotNone(exc.exception.quota_snapshot)
        self.assertEqual(exc.exception.quota_snapshot["month_remaining"], 0)
        self.assertEqual(exc.exception.quota_snapshot["month_limit"], 1000)

    def test_snapshot_does_not_leak_across_calls(self):
        # 第一次带头、第二次不带：thread-local 快照必须逐调用清空，不残留上次的值
        session = mock.Mock()
        with_headers = _mock_response(payload={"web": {"results": []}})
        with_headers.headers = {"X-RateLimit-Remaining": "1, 940", "X-RateLimit-Limit": "1, 1000"}
        without_headers = _mock_response(payload={"web": {"results": []}})
        without_headers.headers = {}
        session.get.side_effect = [with_headers, without_headers]
        adapter = BraveProvider(api_key="k", session=session)

        self.assertIsNotNone(adapter.search("q").quota_snapshot)
        self.assertIsNone(adapter.search("q").quota_snapshot)

    def test_non_brave_provider_has_no_quota_snapshot(self):
        session = mock.Mock()
        session.post.return_value = _mock_response(payload={"organic": []})
        adapter = SerperProvider(api_key="k", session=session)

        self.assertIsNone(adapter.search("q").quota_snapshot)


if __name__ == "__main__":
    unittest.main()
