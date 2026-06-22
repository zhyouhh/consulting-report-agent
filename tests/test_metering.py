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
class ProviderBoom(Exception):
    """测试专用：模拟 provider 流抛出的异常，与 settle 的 RuntimeError 区分。"""


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

    def test_negative_tokens_rejected_fail_closed(self):
        # Codex quality 轨 B6/round3：负 token（provider 异常）→ None（fail-closed），
        # 不静默归零（归零会假记 0 + 复位缺失计数、绕过暂停保护）。
        u = _FakeUsage(prompt_tokens=100, prompt_cache_hit_tokens=-5,
                       prompt_cache_miss_tokens=-90, completion_tokens=-5)
        self.assertIsNone(metering.extract_billing_usage(u))

    def test_malformed_usage_returns_none_fail_closed(self):
        # Codex quality 轨 B6：非数值字段 → None（→ fail-closed 保守封顶），不抛穿计费路径。
        self.assertIsNone(metering.extract_billing_usage(
            _FakeUsage(prompt_tokens="oops", completion_tokens=5)))

    def test_falsey_nonnumeric_present_values_return_none(self):
        # Codex quality 轨 round3：present-but-falsey 非数值（""/[]/{}）不得被 `x or 0` 静默归零。
        self.assertIsNone(metering.extract_billing_usage(
            {"prompt_tokens": "", "completion_tokens": ""}))
        self.assertIsNone(metering.extract_billing_usage(
            _FakeUsage(prompt_tokens=[], prompt_cache_hit_tokens={}, completion_tokens=0)))

    def test_bool_token_value_rejected(self):
        # Codex quality 轨 round3：bool 是 int 子类但不是 token 计数 → None（fail-closed）。
        self.assertIsNone(metering.extract_billing_usage(
            _FakeUsage(prompt_tokens=True, prompt_cache_hit_tokens=False,
                       prompt_cache_miss_tokens=True, completion_tokens=False)))

    def test_non_finite_usage_returns_none(self):
        # Codex quality 轨 B2：inf/nan → None（int(inf) 抛 OverflowError、int(nan) 抛 ValueError）。
        self.assertIsNone(metering.extract_billing_usage(
            _FakeUsage(prompt_tokens=float("inf"), prompt_cache_hit_tokens=0,
                       prompt_cache_miss_tokens=float("inf"), completion_tokens=5)))

    def test_implausibly_huge_token_count_returns_none(self):
        # Codex quality 轨 B2：超大整数能过 int() 但会撑爆 float 计价 / SQLite INTEGER → 视为畸形 → None。
        self.assertIsNone(metering.extract_billing_usage(
            _FakeUsage(prompt_tokens=10 ** 100, prompt_cache_hit_tokens=0,
                       prompt_cache_miss_tokens=10 ** 100, completion_tokens=0)))


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


# tests/test_metering.py（追加，在 MeteredNonStreamTests 同夹具风格下新建类）
class _Chunk:
    def __init__(self, usage=None):
        self.usage = usage
        self.choices = []


class MeteredStreamTests(MeteredNonStreamTests):  # 复用 setUp/tearDown/_FakeOpenAI 构造
    def _stream_client(self, chunks):
        class _StreamCompletions:
            def __init__(self): self.calls = []
            def create(self, **kwargs):
                self.calls.append(kwargs)
                return iter(chunks)
        class _Chat:
            def __init__(self): self.completions = _StreamCompletions()
        class _Raw:
            def __init__(self): self.chat = _Chat()
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        return self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)

    def test_stream_passes_through_chunks_and_settles_on_completion(self):
        usage = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=1289, completion_tokens=500)
        chunks = [_Chunk(), _Chunk(), _Chunk(usage=usage)]
        c = self._stream_client(chunks)
        out = list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        self.assertEqual(len(out), 3)  # 原样透传所有 chunk
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 6867)

    def test_stream_fail_closed_when_no_usage_chunk(self):
        c = self._stream_client([_Chunk(), _Chunk()])  # 无 usage 末包
        list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # 保守封顶 256000×3

    def test_stream_provider_error_midstream_fail_closed(self):
        # ✦ Codex BLOCKER：provider 流中途抛 → fail-closed，不当成主动中断而漏计。
        def _boom():
            yield _Chunk()
            raise RuntimeError("provider dropped mid-stream")
        class _SC:
            def __init__(self): self.calls = []
            def create(self, **kw): self.calls.append(kw); return _boom()
        class _Ch:
            def __init__(self): self.completions = _SC()
        class _Raw:
            def __init__(self): self.chat = _Ch()
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)
        with self.assertRaises(RuntimeError):
            list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # fail-closed 计入、错误再抛

    def test_stream_interrupt_before_usage_fail_closed(self):
        # ✦ Codex BLOCKER：消费方在第一个 chunk 后中断（GeneratorExit，含「处理 chunk 时抛异常」场景）
        # 且未见 usage → fail-closed，不漏计已起的 managed 流（spec §6.3）。
        usage = _FakeUsage(prompt_tokens=10, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=10, completion_tokens=5)
        chunks = [_Chunk(), _Chunk(usage=usage)]   # usage 在第二个，中断时尚未读到
        c = self._stream_client(chunks)
        gen = c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True)
        next(gen)          # 只取第一个就中断
        gen.close()        # GeneratorExit → finally fail-closed
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # 未见 usage → fail-closed 保守封顶

    def test_close_runs_and_error_propagates_even_if_settle_raises(self):
        # Codex quality 轨 B7：settle 抛错（如 DB 写失败）时，底层 provider 流仍必被关闭，
        # 且 settle 异常如实抛出（不被静默吞掉）。
        closed = {"v": False}

        class _Closable:
            def __init__(self): self._it = iter([_Chunk()])
            def __iter__(self): return self
            def __next__(self): return next(self._it)
            def close(self): closed["v"] = True

        class _SC:
            def create(self, **kw): return _Closable()

        class _Ch:
            def __init__(self): self.completions = _SC()

        class _Raw:
            def __init__(self): self.chat = _Ch()

        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)

        def _boom(*a, **k):
            raise RuntimeError("billing db down")
        c._settle = _boom   # 实例属性遮蔽 _settle，模拟结算抛错

        gen = c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True)
        with self.assertRaises(RuntimeError):
            list(gen)
        self.assertTrue(closed["v"])   # settle 抛错，底层流仍被关闭

    def test_provider_error_not_masked_when_settle_also_raises(self):
        # Codex quality 轨 B1（再审）：provider 流中途抛 + settle 同时抛 →
        # 调用方必须看到 provider 异常（非 settle 异常），且底层流仍被关闭。
        closed = {"v": False}

        class _Closable:
            def __init__(self):
                self._sent = False
            def __iter__(self): return self
            def __next__(self):
                if self._sent:
                    raise ProviderBoom("provider dropped")
                self._sent = True
                return _Chunk()
            def close(self): closed["v"] = True

        class _SC:
            def create(self, **kw): return _Closable()

        class _Ch:
            def __init__(self): self.completions = _SC()

        class _Raw:
            def __init__(self): self.chat = _Ch()

        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)

        def _boom(*a, **k):
            raise RuntimeError("billing db down")
        c._settle = _boom

        gen = c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True)
        with self.assertRaises(ProviderBoom):   # provider 异常优先，未被 settle 异常遮蔽
            list(gen)
        self.assertTrue(closed["v"])


# tests/test_metering.py（追加）
class MissCounterTests(MeteredNonStreamTests):
    def setUp(self):
        super().setUp()
        # _miss_counter 是模块级全局；显式清零更稳（reload 已新建模块）。
        self.m._miss_counter.clear()

    def test_three_consecutive_misses_pause_model(self):
        c = self._client(None)  # 每次都缺 usage
        for _ in range(3):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 第 4 次：reserve 阶段即暂停
        with self.assertRaises(self.m.ModelPausedError):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)

    def test_success_resets_miss_counter(self):
        miss_client = self._client(None)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 一次成功 settle 清零
        ok = self._client(_FakeUsage(prompt_tokens=1, prompt_cache_hit_tokens=0,
                                     prompt_cache_miss_tokens=1, completion_tokens=1))
        ok.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 再连续 2 次缺失仍不暂停（计数已清）
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)  # 不抛

    def test_pause_is_per_model(self):
        c_miss = self._client(None)
        for _ in range(3):
            c_miss.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        other = self._client(_FakeUsage(prompt_tokens=1, prompt_cache_hit_tokens=0,
                                        prompt_cache_miss_tokens=1, completion_tokens=1))
        other.chat.completions.create(model="Qwen/Qwen3-VL-8B-Instruct", messages=[], stream=False)  # 不抛

    def test_next_day_auto_resets_pause(self):
        # ✦ Codex BLOCKER：暂停后 reserve 在任何成功 settle 前就拦截 → 同键永不清零；
        # day 入键则次日自动清零。monkeypatch today_shanghai 模拟跨日（_reserve/_settle 均查模块级 today_shanghai）。
        c = self._client(None)
        orig = self.m.today_shanghai
        self.m.today_shanghai = lambda: "2026-06-22"
        try:
            for _ in range(3):
                c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
            with self.assertRaises(self.m.ModelPausedError):
                c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
            self.m.today_shanghai = lambda: "2026-06-23"   # 次日
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)  # 不抛 = 自动清零
        finally:
            self.m.today_shanghai = orig


# tests/test_metering.py（追加）
class WrapFactoryTests(MeteredNonStreamTests):
    def _settings(self, mode):
        class _S:  # 最小 settings 替身
            def __init__(self, mode): self.mode = mode
        return _S(mode)

    def test_managed_mode_wraps(self):
        raw = _FakeOpenAI(_FakeResp(None))
        wrapped = self.m.wrap_client_for_billing(raw, uid="u1", settings=self._settings("managed"))
        self.assertIsInstance(wrapped, self.m.MeteredManagedClient)

    def test_custom_mode_returns_raw_unwrapped(self):
        raw = _FakeOpenAI(_FakeResp(None))
        same = self.m.wrap_client_for_billing(raw, uid="u1", settings=self._settings("custom"))
        self.assertIs(same, raw)


# tests/test_metering.py（追加；锁死「managed 调用必经 MeteredManagedClient」）
import pathlib


class SourceGuardTests(unittest.TestCase):
    def _src(self, rel):
        return pathlib.Path(__file__).resolve().parent.parent.joinpath(rel).read_text(encoding="utf-8")

    # TODO(B2 簇B/C): 接线后移除 expectedFailure
    @unittest.expectedFailure
    def test_chat_handler_client_assigned_through_wrapper(self):
        # ✦ NIT：不只查字符串（死 import 也会过），断言 self.client 由 wrap_client_for_billing 赋值。
        src = self._src("backend/chat.py")
        self.assertRegex(src, r"self\.client\s*=\s*wrap_client_for_billing\(",
                         "ChatHandler.self.client 必须由 wrap_client_for_billing 赋值")

    # TODO(B2 簇B/C): 接线后移除 expectedFailure
    @unittest.expectedFailure
    def test_independent_review_client_returned_through_wrapper(self):
        src = self._src("backend/independent_review.py")
        self.assertRegex(src, r"return\s+wrap_client_for_billing\(",
                         "IndependentReviewAgent._build_client 必须 return wrap_client_for_billing(...)")
