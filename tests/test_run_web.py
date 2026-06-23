import unittest
from pathlib import Path


class RunWebConfigTests(unittest.TestCase):
    def setUp(self):
        self.src = (Path(__file__).resolve().parents[1] / "run_web.py").read_text(encoding="utf-8")

    def test_host_port_from_env(self):
        self.assertIn("CRA_BIND_HOST", self.src)
        self.assertIn("CRA_BIND_PORT", self.src)

    def test_uvicorn_trusts_proxy_headers(self):
        self.assertIn("proxy_headers=True", self.src)
        self.assertIn("forwarded_allow_ips=forwarded_allow_ips", self.src)

    def test_forwarded_allow_ips_env_configurable_default_loopback(self):
        # 默认 127.0.0.1（反代同机），可经 CRA_FORWARDED_ALLOW_IPS 覆盖（nginx 走 ::1/bridge 时）
        self.assertIn("CRA_FORWARDED_ALLOW_IPS", self.src)
        self.assertIn('"127.0.0.1"', self.src)

    def test_no_stale_hardcoded_external_ip(self):
        self.assertNotIn("57.129.103.127", self.src)
