import unittest
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
