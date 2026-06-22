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
    hit = int(hit or 0)
    if miss is None:
        miss = max(int(prompt or 0) - hit, 0)
    else:
        miss = int(miss)
    return BillingUsage(hit=hit, miss=miss, completion=int(completion or 0))


def price_micro_yuan(model: str, hit: int, miss: int, completion: int, pricing: dict) -> int:
    """token×(元/百万token)=微元；单价表缺该模型时用 FALLBACK_MODEL_PRICING 保守计价。"""
    p_hit, p_miss, p_out = pricing.get(model, FALLBACK_MODEL_PRICING)
    return round(hit * p_hit + miss * p_miss + completion * p_out)


import threading
from backend import accounts
from backend.config import (DEFAULT_MANAGED_MODEL_PRICING,
                            DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN, MAX_CONSECUTIVE_USAGE_MISS,
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
            raise NotImplementedError("stream path implemented in Task 5")
        response = raw_completions.create(**kwargs)
        self._settle(model, getattr(response, "usage", None))
        return response
