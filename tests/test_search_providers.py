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


if __name__ == "__main__":
    unittest.main()
