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

    def test_validate_rejects_userinfo_in_url(self):
        # userinfo (u:p@) 会让 httpx 发 Authorization: Basic 覆盖用户 Bearer key → 静默坏掉调用。
        # mock DNS 为公网放行：确保 raise 是因 userinfo 检测、而非 DNS（host 在白名单内）。
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            with self.assertRaises(url_guard.SsrfBlockedError) as ctx:
                url_guard.validate_custom_api_base("https://u:p@api.openai.com/v1")
        self.assertIn("用户名", str(ctx.exception))

    def test_validate_rejects_bad_port(self):
        # 坏端口：urlparse 不报错但 .port 访问抛 ValueError；保存期放行→运行期炸，体验差。
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            with self.assertRaises(url_guard.SsrfBlockedError) as ctx:
                url_guard.validate_custom_api_base("https://api.openai.com:bad/v1")
        self.assertIn("端口", str(ctx.exception))

    def test_validate_allowlisted_host_explicit_port_passes(self):
        # 合法显式端口不被误伤（白名单是 host-only）。
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            self.assertEqual(
                url_guard.validate_custom_api_base("https://api.openai.com:8443/v1"),
                "https://api.openai.com:8443/v1",
            )


class IsValidHostnameTests(unittest.TestCase):
    def test_plain_hostname_accepted(self):
        self.assertTrue(url_guard.is_valid_hostname("my.llm.cn"))
        self.assertTrue(url_guard.is_valid_hostname("api.openai.com"))

    def test_scheme_port_path_wildcard_space_rejected(self):
        for bad in ("https://x.com/v1", "x.com:8443", "10.0.0.1", "*.evil.com", "has space"):
            self.assertFalse(url_guard.is_valid_hostname(bad), bad)

    def test_overlong_tld_label_rejected(self):
        # codex 红队 NIT：末段 TLD label 不得超 63 字符（DNS label 上限）。
        long_tld = "a" * 64
        self.assertFalse(url_guard.is_valid_hostname(f"host.{long_tld}"))
        # 63 字符 label 仍合法（边界正好通过）。
        self.assertTrue(url_guard.is_valid_hostname("host." + "a" * 63))


class GuardedTransportTests(unittest.TestCase):
    """行为级：真起 build_guarded_http_client() 的 client 发请求，断言 transport 在
    校验阶段就抛 SsrfBlockedError（不真出网——off-list 在 super().handle_request 之前被拦；
    私网 case 用 mock 让 getaddrinfo 返私网 IP，同样在 connect 之前抛）。"""

    def test_request_to_offlist_host_blocked(self):
        client = url_guard.build_guarded_http_client(timeout=5.0)
        self.addCleanup(client.close)
        # 不 mock getaddrinfo：off-list 主机在白名单检查处即抛，不会走到 DNS / connect。
        with self.assertRaises(url_guard.SsrfBlockedError):
            client.get("https://evil.example.com/")

    def test_request_to_allowlisted_host_resolving_private_blocked(self):
        client = url_guard.build_guarded_http_client(timeout=5.0)
        self.addCleanup(client.close)
        # 白名单内主机但解析到私网（投毒/误配）→ public-IP 二道防线在 connect 前拦下。
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("10.0.0.9", 0))]):
            with self.assertRaises(url_guard.SsrfBlockedError):
                client.get("https://api.openai.com/v1/models")

    def test_request_to_non_https_blocked(self):
        client = url_guard.build_guarded_http_client(timeout=5.0)
        self.addCleanup(client.close)
        with self.assertRaises(url_guard.SsrfBlockedError):
            client.get("http://api.openai.com/v1/models")
