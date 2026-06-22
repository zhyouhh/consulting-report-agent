import os
import unittest
from unittest import mock

from backend import url_guard


class PublicIpTests(unittest.TestCase):
    def test_private_and_loopback_and_metadata_rejected(self):
        for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                    "::1", "100.64.0.1", "0.0.0.0"):
            with self.assertRaises(url_guard.SsrfBlockedError):
                url_guard.assert_public_ip(bad)

    def test_public_ip_passes(self):
        url_guard.assert_public_ip("1.1.1.1")   # 不抛即通过
        url_guard.assert_public_ip("8.8.8.8")


class AllowlistTests(unittest.TestCase):
    def test_default_allowlist_includes_managed_and_mainstream(self):
        hosts = url_guard.custom_api_allowed_hosts()
        self.assertIn("newapi.z0y0h.work", hosts)
        self.assertIn("api.openai.com", hosts)
        self.assertIn("api.deepseek.com", hosts)

    def test_env_extends_allowlist(self):
        with mock.patch.dict(os.environ, {"CRA_CUSTOM_API_ALLOWED_HOSTS": "my.llm.cn, other.host"}):
            hosts = url_guard.custom_api_allowed_hosts()
            self.assertIn("my.llm.cn", hosts)
            self.assertIn("other.host", hosts)

    def test_validate_rejects_non_https(self):
        with self.assertRaises(url_guard.SsrfBlockedError):
            url_guard.validate_custom_api_base("http://api.openai.com/v1")

    def test_validate_rejects_offlist_host(self):
        with self.assertRaises(url_guard.SsrfBlockedError):
            url_guard.validate_custom_api_base("https://evil.example.com/v1")

    def test_validate_allowlisted_host_resolving_private_rejected(self):
        # 白名单内但解析到私网（误配/投毒）→ public-IP 二道防线拦下
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("10.0.0.9", 0))]):
            with self.assertRaises(url_guard.SsrfBlockedError):
                url_guard.validate_custom_api_base("https://api.openai.com/v1")

    def test_validate_allowlisted_host_public_passes(self):
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            self.assertEqual(
                url_guard.validate_custom_api_base("https://api.openai.com/v1 "),
                "https://api.openai.com/v1",
            )
