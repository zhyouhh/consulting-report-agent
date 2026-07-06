"""provider_retry 叶子模块：瞬态错误分类 + 指数退避（2026-07-06）。"""
import unittest

from backend import provider_retry


class _StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class ClassifierTests(unittest.TestCase):
    def test_no_status_code_is_retryable(self):
        # 连接失败/超时/断流等网络层错误没有 HTTP 状态码 → 瞬态。
        self.assertTrue(provider_retry.is_retryable_provider_error(RuntimeError("boom")))
        self.assertTrue(provider_retry.is_retryable_provider_error(TimeoutError("read timed out")))
        self.assertTrue(provider_retry.is_retryable_provider_error(ConnectionError("reset")))

    def test_transient_status_codes_are_retryable(self):
        for code in (408, 425, 429, 500, 502, 503, 504, 522, 524):
            with self.subTest(code=code):
                self.assertTrue(provider_retry.is_retryable_provider_error(_StatusError(code)))

    def test_deterministic_client_errors_are_not_retryable(self):
        for code in (400, 401, 403, 404, 413, 422):
            with self.subTest(code=code):
                self.assertFalse(provider_retry.is_retryable_provider_error(_StatusError(code)))

    def test_bool_status_code_does_not_crash_and_stays_retryable(self):
        err = _StatusError(True)   # bool 是 int 子类，防误配进 4xx 分支
        self.assertTrue(provider_retry.is_retryable_provider_error(err))

    def test_leaf_module_has_no_project_imports(self):
        # 叶子铁律：不 import chat/skill/main/metering（防循环依赖 + 保持可独立测试）。
        import inspect

        src = inspect.getsource(provider_retry)
        for banned in ("from .chat", "from .skill", "from .main", "from .metering",
                       "import chat", "import skill", "import metering"):
            self.assertNotIn(banned, src)


class BackoffTests(unittest.TestCase):
    def test_exponential_backoff_with_cap(self):
        self.assertEqual(provider_retry.backoff_seconds(1), 2.0)
        self.assertEqual(provider_retry.backoff_seconds(2), 4.0)
        self.assertEqual(provider_retry.backoff_seconds(3), 8.0)
        self.assertEqual(provider_retry.backoff_seconds(4), 8.0)   # 封顶

    def test_attempt_below_one_clamps(self):
        self.assertEqual(provider_retry.backoff_seconds(0), 2.0)
        self.assertEqual(provider_retry.backoff_seconds(-3), 2.0)

    def test_retry_status_text_mentions_attempt(self):
        text = provider_retry.retry_status_text(1, 3)
        self.assertIn("1/3", text)
        self.assertIn("重试", text)


if __name__ == "__main__":
    unittest.main()
