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
