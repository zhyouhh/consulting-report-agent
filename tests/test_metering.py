# tests/test_metering.py
import unittest
from backend import metering
from backend.config import DEFAULT_MANAGED_MODEL_PRICING, DEFAULT_MANAGED_MODEL


class PriceTests(unittest.TestCase):
    def test_deepseek_three_tier_cost_matches_spec_numbers(self):
        # spec §6.1：p_hit=0.025 / p_miss=3 / p_out=6（元/百万token）
        # 非流式冷：hit=0 miss=1289 completion=500
        cost = metering.price_micro_yuan(DEFAULT_MANAGED_MODEL, hit=0, miss=1289, completion=500,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # 0*0.025 + 1289*3 + 500*6 = 3867 + 3000 = 6867 微元
        self.assertEqual(cost, 6867)

    def test_cache_hit_is_cheap(self):
        # 非流式热：hit=1280 miss=9 completion=500
        cost = metering.price_micro_yuan(DEFAULT_MANAGED_MODEL, hit=1280, miss=9, completion=500,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # round(1280*0.025) + 9*3 + 500*6 = 32 + 27 + 3000 = 3059
        self.assertEqual(cost, 3059)

    def test_unknown_model_uses_safe_fallback_pricing(self):
        cost = metering.price_micro_yuan("some/unknown-model", hit=0, miss=1000, completion=0,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # 未知模型 → fallback 三档（按 deepseek 价保守），1000*3 = 3000
        self.assertEqual(cost, 3000)


# tests/test_metering.py（追加）
class _FakeUsage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class ExtractUsageTests(unittest.TestCase):
    def test_reads_deepseek_cache_fields(self):
        u = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                       prompt_cache_miss_tokens=1289, completion_tokens=500)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (0, 1289, 500))

    def test_hot_cache(self):
        u = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=1280,
                       prompt_cache_miss_tokens=9, completion_tokens=500)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (1280, 9, 500))

    def test_miss_falls_back_to_prompt_minus_hit_when_absent(self):
        u = _FakeUsage(prompt_tokens=1000, prompt_cache_hit_tokens=200, completion_tokens=50)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (200, 800, 50))

    def test_returns_none_when_usage_missing(self):
        self.assertIsNone(metering.extract_billing_usage(None))

    def test_returns_none_when_no_token_fields(self):
        self.assertIsNone(metering.extract_billing_usage(_FakeUsage(foo=1)))

    def test_accepts_dict_usage(self):
        bu = metering.extract_billing_usage(
            {"prompt_tokens": 100, "prompt_cache_hit_tokens": 10,
             "prompt_cache_miss_tokens": 90, "completion_tokens": 5})
        self.assertEqual((bu.hit, bu.miss, bu.completion), (10, 90, 5))


# tests/test_metering.py（追加）
import datetime as _dt


class DayBoundaryTests(unittest.TestCase):
    def test_today_shanghai_is_yyyy_mm_dd(self):
        s = metering.today_shanghai()
        _dt.datetime.strptime(s, "%Y-%m-%d")  # 不抛即合法
        self.assertEqual(len(s), 10)


# tests/test_metering.py（追加；沿用 Task 3 的 CRA_DATA_ROOT 隔离 setUp/tearDown）
import importlib, os, tempfile


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeOpenAI:
    def __init__(self, response):
        self.chat = _FakeChat(response)


class _FakeResp:
    def __init__(self, usage):
        self.usage = usage
        self.choices = []


class MeteredNonStreamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name
        import backend.config as config; importlib.reload(config)
        import backend.tenant as tenant; importlib.reload(tenant)
        import backend.accounts as accounts; importlib.reload(accounts)
        accounts.init_db()
        self.accounts = accounts
        import backend.metering as m; importlib.reload(m)
        self.m = m

    def tearDown(self):
        if self._old is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._old
        self._tmp.cleanup()

    def _client(self, usage):
        raw = _FakeOpenAI(_FakeResp(usage))
        return self.m.MeteredManagedClient(raw, uid="u1", model_pricing=__import__(
            "backend.config", fromlist=["x"]).DEFAULT_MANAGED_MODEL_PRICING)

    def test_settles_cost_after_call(self):
        usage = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=1289, completion_tokens=500)
        c = self._client(usage)
        c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 6867)
        self.assertEqual(row["cache_miss_tokens"], 1289)

    def test_reserve_blocks_when_over_cap(self):
        self.accounts.set_config("global_daily_cap_micro_yuan", "100")
        self.accounts.add_usage("u1", self.m.today_shanghai(), 100, 0, 0, 0)  # 已达上限
        c = self._client(_FakeUsage(prompt_tokens=1, completion_tokens=1,
                                    prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1))
        with self.assertRaises(self.m.QuotaExceededError):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        self.assertEqual(len(c.chat.completions._raw_calls()), 0)  # reserve 在调用前，未触达 provider

    def test_fail_closed_when_usage_missing(self):
        c = self._client(None)  # provider 不返回 usage
        c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        # 保守封顶 = deepseek-v4-pro effective 上限(256000) × p_miss(3) = 768000 微元
        self.assertEqual(row["cost_micro_yuan"], 768000)

    def test_getattr_delegates_unknown_attrs_to_raw(self):
        raw = _FakeOpenAI(_FakeResp(None))
        raw.responses = "RAW_RESPONSES_SENTINEL"   # 模拟 .responses（原生搜索面）
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(raw, uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)
        self.assertEqual(c.responses, "RAW_RESPONSES_SENTINEL")  # 透传裸 client，不 AttributeError

    def test_vision_model_fail_closed_uses_explicit_ceiling(self):
        # ✦ Codex BLOCKER：视觉模型用显式锚（32768），不落 context_policy 未知 fallback。
        c = self._client(None)
        c.chat.completions.create(model="Qwen/Qwen3-VL-8B-Instruct", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 32768 * 3)   # 32768 × p_miss(3) = 98304
