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
