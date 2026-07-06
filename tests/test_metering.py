# tests/test_metering.py
import math
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

    def test_settle_failure_is_swallowed_and_logged(self):
        # Codex 全分支综合审 BLOCKER：结算（add_usage 等）失败不得抛给调用方——否则 _summarize_messages
        # 的宽 except 会把它静默吞成「摘要失败」、既不计费又不可观测。改为记日志、调用照常返回。
        usage = _FakeUsage(prompt_tokens=10, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=10, completion_tokens=5)
        c = self._client(usage)

        def _boom(*a, **k):
            raise RuntimeError("sqlite write failed")
        self.accounts.add_usage = _boom   # 模拟结算 DB 写失败

        with self.assertLogs("backend.metering", level="WARNING") as logs:
            resp = c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        self.assertIsNotNone(resp)                                    # 调用正常返回、未抛
        self.assertTrue(any("unbilled" in line for line in logs.output))


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
        # Codex quality 轨 B7（防御性）：真 _settle 已 best-effort（内部吞失败、不抛）；本测试 monkeypatch
        # _settle 强制抛，验证万一 settle 意外抛出时——底层 provider 流仍必被关闭、且该异常如实抛出（不静默吞）。
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
        # Codex quality 轨 B1（再审，防御性）：真 _settle 已 best-effort；本测试 monkeypatch _settle 强制抛，
        # 验证 provider 流中途抛 + settle 也抛时——调用方仍看到 provider 异常（非 settle 异常）、底层流仍关闭。
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


# tests/test_metering.py（追加，2026-07-06 fail-closed 请求感知估算 + failclosed 独立列）
class FailClosedEstimateTests(MeteredNonStreamTests):
    _MSGS = [{"role": "system", "content": "你是写咨询报告的助手。" * 40},
             {"role": "user", "content": "帮我写一份市场分析。" * 20}]

    def test_estimator_counts_cjk_and_ascii(self):
        est = self.m.estimate_request_tokens_upper_bound(
            {"messages": [{"role": "user", "content": "中文四个字" + "abcd"}]})
        # 5 CJK×1 + 4 ascii/2 + role extras + margin + base：只锁「有值且量级合理」
        self.assertIsNotNone(est)
        self.assertLess(est, 3000)

    def test_estimator_returns_none_for_multimodal_or_bad_shape(self):
        self.assertIsNone(self.m.estimate_request_tokens_upper_bound(None))
        self.assertIsNone(self.m.estimate_request_tokens_upper_bound({"messages": []}))
        self.assertIsNone(self.m.estimate_request_tokens_upper_bound(
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]}))
        self.assertIsNone(self.m.estimate_request_tokens_upper_bound({"messages": ["not-a-dict"]}))

    def test_estimator_emoji_and_symbols_priced_higher_than_ascii(self):
        # Codex BLOCKER round2：emoji/非 ASCII 非 CJK 字符 token 密度高（实测 1-3/字符），
        # ÷2 不是上界 → 按 2/字符计。1000 个 emoji 的估算必须 ≥ 2000 token（margin 前）。
        emoji_est = self.m.estimate_request_tokens_upper_bound(
            {"messages": [{"content": "\U0001F600" * 1000}]})
        ascii_est = self.m.estimate_request_tokens_upper_bound(
            {"messages": [{"content": "a" * 1000}]})
        self.assertGreaterEqual(emoji_est - ascii_est, int(1000 * 1.5))  # emoji 2/字 vs ascii 0.5/字

    def test_estimator_counts_tools_and_tool_calls(self):
        base = self.m.estimate_request_tokens_upper_bound({"messages": self._MSGS})
        with_tools = self.m.estimate_request_tokens_upper_bound(
            {"messages": self._MSGS, "tools": [{"type": "function", "function": {"name": "web_search", "description": "d" * 400}}]})
        self.assertGreater(with_tools, base)

    def test_fail_closed_bills_estimate_into_failclosed_column(self):
        # 有 messages 的请求缺 usage → 按估算计费（远小于 256k ceiling），tokens 进 failclosed 列、miss 保持 0。
        c = self._client(None)
        c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=False)
        est = self.m.estimate_request_tokens_upper_bound({"messages": self._MSGS, "stream": False, "model": "deepseek-v4-pro"})
        day = self.m.today_shanghai()
        row = [r for r in self.accounts.get_usage_history(day) if r["uid"] == "u1"][0]
        self.assertEqual(row["failclosed_tokens"], est)
        self.assertEqual(row["cache_miss_tokens"], 0)      # 命中率统计不被污染
        self.assertEqual(row["cost_micro_yuan"], est * 3)  # est × p_miss(3)
        self.assertLess(est, 256000)

    def test_fail_closed_estimate_clamped_to_model_ceiling(self):
        huge = [{"role": "user", "content": "字" * 600_000}]   # 估算 > 256k
        c = self._client(None)
        c.chat.completions.create(model="deepseek-v4-pro", messages=huge, stream=False)
        day = self.m.today_shanghai()
        row = [r for r in self.accounts.get_usage_history(day) if r["uid"] == "u1"][0]
        self.assertEqual(row["failclosed_tokens"], 256000)   # clamp 到 ceiling，绝不比旧封顶更贵

    def test_stream_interrupt_bills_streamed_completion_at_output_price(self):
        # Codex BLOCKER round2：短 prompt + 已流出长输出后断流 → 已流出的 completion 字符
        # 按 1 token/字符 ×1.15 计输出价，不能只按 prompt 估算漏掉输出成本。
        self.m._miss_counter.clear()
        streamed = "答" * 4000   # 4000 字符已流出
        chunk_with_text = _Chunk()
        chunk_with_text.choices = [type("C", (), {"delta": type("D", (), {"content": streamed})()})()]
        c = self._stream_client([chunk_with_text, _Chunk()])
        gen = c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=True)
        next(gen)
        gen.close()   # 中断，无 usage
        day = self.m.today_shanghai()
        row = [r for r in self.accounts.get_usage_history(day) if r["uid"] == "u1"][0]
        prompt_est = self.m.estimate_request_tokens_upper_bound({"messages": self._MSGS})
        completion_est = math.ceil(4000 * 1.15)
        self.assertEqual(row["failclosed_tokens"], prompt_est + completion_est)
        self.assertEqual(row["cost_micro_yuan"], prompt_est * 3 + completion_est * 6)  # miss价 + 输出价

    def test_stream_generator_exit_bills_but_does_not_bump_pause(self):
        # 消费方关流（用户停止/断连/瞬态重试关旧流）→ 照常 fail-closed 计费，但不累计暂停计数——
        # 否则手机切后台 3 次就把该用户当日模型锁死。
        self.m._miss_counter.clear()
        for _ in range(4):
            chunks = [_Chunk(), _Chunk()]
            c = self._stream_client(chunks)
            gen = c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=True)
            next(gen)
            gen.close()   # GeneratorExit → fail-closed 计费但 bump_pause=False
        day = self.m.today_shanghai()
        row = [r for r in self.accounts.get_usage_history(day) if r["uid"] == "u1"][0]
        self.assertGreater(row["failclosed_tokens"], 0)      # 计了 4 次估算账
        self.assertEqual(self.m._miss_counter, {})            # 暂停计数未动
        # 第 5 次照常可发起（未被暂停）
        c = self._stream_client([_Chunk()])
        list(c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=True))

    def test_stream_provider_error_still_bumps_pause(self):
        # provider 真异常（非消费方关流）仍计暂停计数：连续 3 次后第 4 次 reserve 拦截。
        self.m._miss_counter.clear()
        def _make_boom():
            def _boom():
                yield _Chunk()
                raise RuntimeError("provider dropped")
            return _boom()
        class _SC:
            def __init__(self): self.calls = []
            def create(self, **kw): self.calls.append(kw); return _make_boom()
        class _Ch:
            def __init__(self): self.completions = _SC()
        class _Raw:
            def __init__(self): self.chat = _Ch()
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                list(c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=True))
        with self.assertRaises(self.m.ModelPausedError):
            c.chat.completions.create(model="deepseek-v4-pro", messages=self._MSGS, stream=True)

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

    def test_chat_handler_client_assigned_through_wrapper(self):
        # ✦ NIT：不只查字符串（死 import 也会过），断言 self.client 由 wrap_client_for_billing 赋值。
        # 允许可选 `metering.` 前缀（模块限定访问，reload 安全——见 chat.py __init__ 注释）。
        src = self._src("backend/chat.py")
        self.assertRegex(src, r"self\.client\s*=\s*(?:metering\.)?wrap_client_for_billing\(",
                         "ChatHandler.self.client 必须由 wrap_client_for_billing 赋值")

    def test_independent_review_client_returned_through_wrapper(self):
        src = self._src("backend/independent_review.py")
        self.assertRegex(src, r"return\s+(?:metering\.)?wrap_client_for_billing\(",
                         "IndependentReviewAgent._build_client 必须 return wrap_client_for_billing(...)")
