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
        self.assertIn('forwarded_allow_ips="127.0.0.1"', self.src)

    def test_no_stale_hardcoded_external_ip(self):
        self.assertNotIn("57.129.103.127", self.src)
