# backend/metering.py
"""中央计费叶子模块（只依赖 accounts/config；绝不 import chat/skill/main/independent_review）。"""
from __future__ import annotations
from backend.config import FALLBACK_MODEL_PRICING


def price_micro_yuan(model: str, hit: int, miss: int, completion: int, pricing: dict) -> int:
    """token×(元/百万token)=微元；单价表缺该模型时用 FALLBACK_MODEL_PRICING 保守计价。"""
    p_hit, p_miss, p_out = pricing.get(model, FALLBACK_MODEL_PRICING)
    return round(hit * p_hit + miss * p_miss + completion * p_out)
