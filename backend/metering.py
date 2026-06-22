# backend/metering.py
"""中央计费叶子模块（只依赖 accounts/config；绝不 import chat/skill/main/independent_review）。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from backend.config import FALLBACK_MODEL_PRICING

_SHANGHAI_TZ = timezone(timedelta(hours=8))


def today_shanghai() -> str:
    """配额日界（spec §6.3）：Asia/Shanghai 的 YYYY-MM-DD。UTC+8 固定偏移（中国无夏令时）。"""
    return datetime.now(timezone.utc).astimezone(_SHANGHAI_TZ).strftime("%Y-%m-%d")


@dataclass
class BillingUsage:
    hit: int
    miss: int
    completion: int


# 任何真实单次调用的 token 量级上界（10 亿，远超任何模型上下文，但远低于 float 计价/SQLite INTEGER 溢出点）。
# 超出即视为畸形 usage → fail-closed。
_MAX_PLAUSIBLE_TOKENS = 10 ** 9


def _usage_get(usage, key: str):
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def extract_billing_usage(usage) -> BillingUsage | None:
    """从 provider usage（对象或 dict）取三档计费 token。无法识别 token 字段时返回 None（→ fail-closed）。"""
    if usage is None:
        return None
    prompt = _usage_get(usage, "prompt_tokens")
    completion = _usage_get(usage, "completion_tokens")
    hit = _usage_get(usage, "prompt_cache_hit_tokens")
    miss = _usage_get(usage, "prompt_cache_miss_tokens")
    if prompt is None and completion is None and hit is None and miss is None:
        return None
    # 防御性 fail-closed：非数值 / 非有限(inf,nan) / 溢出 → None（→ 保守封顶），不让其抛穿计费路径；
    # 负数 token（provider 异常）钳到 0，杜绝「负计费倒扣 usage_daily 逃配额」；
    # 超出真实量级（_MAX_PLAUSIBLE_TOKENS）视为畸形 → None，避免 float 计价溢出 / SQLite INTEGER 越界。
    try:
        hit = max(int(hit or 0), 0)
        if miss is None:
            miss = max(int(prompt or 0) - hit, 0)
        else:
            miss = max(int(miss), 0)
        completion = max(int(completion or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if max(hit, miss, completion) > _MAX_PLAUSIBLE_TOKENS:
        return None
    return BillingUsage(hit=hit, miss=miss, completion=completion)


def price_micro_yuan(model: str, hit: int, miss: int, completion: int, pricing: dict) -> int:
    """token×(元/百万token)=微元；单价表缺该模型时用 FALLBACK_MODEL_PRICING 保守计价。"""
    p_hit, p_miss, p_out = pricing.get(model, FALLBACK_MODEL_PRICING)
    return round(hit * p_hit + miss * p_miss + completion * p_out)


import logging
import sys
import threading
from backend import accounts

logger = logging.getLogger(__name__)
from backend.config import (DEFAULT_MANAGED_MODEL_PRICING, MAX_CONSECUTIVE_USAGE_MISS,
                            MANAGED_FAILCLOSED_CEILING)
from backend.context_policy import resolve_context_policy   # fail-closed 取该模型 effective 上下文上限


class QuotaExceededError(Exception):
    """今日 managed 额度已用尽。"""
    def __init__(self, used_micro: int, cap_micro: int):
        self.used_micro = used_micro
        self.cap_micro = cap_micro
        super().__init__(f"quota exceeded: {used_micro}/{cap_micro} micro-yuan")


class ModelPausedError(Exception):
    """连续 usage 缺失，暂停该 (uid, model) managed 调用。"""
    def __init__(self, model: str):
        self.model = model
        super().__init__(f"model paused after repeated missing usage: {model}")


# per-(uid, model, day) 连续缺失计数（进程级；单进程部署足够）。
# ✦ day 入键（Codex BLOCKER）：一旦暂停，reserve 在任何成功 settle 之前就拦截 → 同键永不清零；
#   day 入键则「次日 0 点自动清零」天然成立（新 day = 新键 = 计数 0），无需依赖一次成功 settle。
_miss_counter_lock = threading.Lock()
_miss_counter: dict[tuple[str, str, str], int] = {}


def _bump_miss(uid: str, model: str, day: str) -> int:
    with _miss_counter_lock:
        n = _miss_counter.get((uid, model, day), 0) + 1
        _miss_counter[(uid, model, day)] = n
        return n


def _reset_miss(uid: str, model: str, day: str) -> None:
    with _miss_counter_lock:
        _miss_counter.pop((uid, model, day), None)


class MeteredCompletions:
    def __init__(self, parent: "MeteredManagedClient"):
        self._parent = parent
        self._raw = parent._raw.chat.completions

    def _raw_calls(self):  # 测试访问器
        return getattr(self._raw, "calls", [])

    def create(self, **kwargs):
        return self._parent._create(self._raw, **kwargs)


class _MeteredChat:
    def __init__(self, parent): self.completions = MeteredCompletions(parent)


class MeteredManagedClient:
    """managed 调用唯一出口：reserve→call→settle，usage 缺失 fail-closed。
    暴露 .chat.completions.create(**kwargs) 与 OpenAI 接口同形，调用点零改动。"""
    def __init__(self, raw_client, uid: str, model_pricing: dict | None = None):
        self._raw = raw_client
        self.uid = uid
        self._pricing = model_pricing or DEFAULT_MANAGED_MODEL_PRICING
        self.chat = _MeteredChat(self)

    def __getattr__(self, name):
        # ✦ NIT：非 chat.completions.create 的属性（如 .responses 原生搜索）透传裸 client，
        # 避免包裹破坏其它调用面。仅 managed 模式才包裹，故 .responses 计费缺口不在 B2 scope（见 cutover 已知限制）。
        raw = self.__dict__.get("_raw")
        if raw is None:
            raise AttributeError(name)
        return getattr(raw, name)

    # --- 门禁 ---
    def _reserve(self, model: str):
        day = today_shanghai()
        cap = accounts.get_effective_daily_cap_micro(self.uid)
        used = accounts.get_usage_today(self.uid, day)["cost_micro_yuan"]
        if used >= cap:
            raise QuotaExceededError(used, cap)
        with _miss_counter_lock:
            paused = _miss_counter.get((self.uid, model, day), 0) >= MAX_CONSECUTIVE_USAGE_MISS
        if paused:
            raise ModelPausedError(model)

    # --- 累计 ---
    def _settle(self, model: str, usage) -> None:
        day = today_shanghai()
        bu = extract_billing_usage(usage)
        if bu is None:
            # fail-closed（spec §6.3「该模型上下文上限 × 未命中价估上界」）：视觉等模型有显式锚（不在 context_policy
            # EXACT tier），否则按该模型 effective 上下文上限（app 压缩保证 prompt ≤ effective），deepseek-v4-pro=256000；
            # resolve_context_policy 对未知模型走 UNKNOWN_FALLBACK_TIER、不抛。连续缺失计数 +1。
            ceiling = (MANAGED_FAILCLOSED_CEILING.get(model)
                       or resolve_context_policy(model).effective_context_limit)
            _, p_miss, _ = self._pricing.get(model, FALLBACK_MODEL_PRICING)
            cost = round(ceiling * p_miss)
            accounts.add_usage(self.uid, day, cost, 0, ceiling, 0)
            _bump_miss(self.uid, model, day)
            return
        cost = price_micro_yuan(model, bu.hit, bu.miss, bu.completion, self._pricing)
        accounts.add_usage(self.uid, day, cost, bu.hit, bu.miss, bu.completion)
        _reset_miss(self.uid, model, day)

    def _create(self, raw_completions, **kwargs):
        model = kwargs.get("model", "")
        self._reserve(model)
        if kwargs.get("stream"):
            raw_stream = raw_completions.create(**kwargs)
            return self._metered_stream(model, raw_stream)
        response = raw_completions.create(**kwargs)
        self._settle(model, getattr(response, "usage", None))
        return response

    def _metered_stream(self, model: str, raw_stream):
        """透传每个 chunk + 记最后带 usage 的 chunk。无论**自然结束 / provider 异常 / 消费方中断
        (GeneratorExit)**，`finally` 都结算恰好一次：last_usage 缺失 → fail-closed 保守封顶。
        ✦ Codex BLOCKER（R1+R2）：① provider 中途抛不得漏计；② **消费方在处理已 yield 的 chunk 时抛异常，
        经 gen.close()→GeneratorExit 到达此处**——与「用户主动放弃」无法从生成器内区分，故 spec §6.3
        「流式中断 + usage 缺失 = fail-closed」对 GeneratorExit 也一律 fail-closed（不留漏计后门）。"""
        last_usage = None
        try:
            for chunk in raw_stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    last_usage = u
                yield chunk
        finally:
            # close 用独立 finally 包住 settle：即便 settle 抛错（如 DB 写失败），底层 provider 流也必被关闭。
            # pending = 正在 unwinding 的原始异常（provider 抛 / GeneratorExit / 消费方异常）：
            #   有原始异常时 settle 失败只记日志、不得遮蔽它；正常结束时（pending is None）settle 失败如实抛出。
            pending = sys.exc_info()[1]
            try:
                self._settle(model, last_usage)   # 自然结束 / provider 异常 / GeneratorExit 都恰好结算一次
            except Exception:
                if pending is None:
                    raise
                logger.warning("metered stream settle failed while unwinding %r", pending, exc_info=True)
            finally:
                close = getattr(raw_stream, "close", None)   # 释放底层 provider 流（HTTP 连接）
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass


def wrap_client_for_billing(raw_client, uid: str, settings):
    """managed → MeteredManagedClient（计费）；custom → 原样返回（不计费）。
    settings.mode 在 ChatHandler/Review 构造时已定（per-handler 固定）。"""
    if getattr(settings, "mode", "managed") == "managed":
        return MeteredManagedClient(raw_client, uid=uid)
    return raw_client
