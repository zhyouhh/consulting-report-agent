from dataclasses import dataclass, replace


TIER_LIMITS = {
    "tier_1m": (1_000_000, 200_000),
    "tier_1m_eff_256k": (1_000_000, 256_000),
    "tier_400k": (400_000, 320_000),
    "tier_256k": (256_000, 200_000),
    "tier_200k": (200_000, 180_000),
    "tier_128k": (128_000, 110_000),
}

EXACT_MODEL_TIERS = {
    "gemini-3-flash": "tier_1m",
    "kimi-k2.5": "tier_256k",
    "deepseek-v4-pro": "tier_1m_eff_256k",
}

FAMILY_MODEL_TIERS = {
    "gpt-4.1": "tier_400k",
    "gpt-5": "tier_400k",
    "gemini-3": "tier_1m",
    "claude-": "tier_200k",
    "grok-4.1": "tier_200k",
}

UNKNOWN_FALLBACK_TIER = "tier_128k"
MIN_EFFECTIVE_CONTEXT_LIMIT = 4_096

# 输出预算：整篇重写类 tool_call（含 reasoning tokens）需要远超 8_192 的
# max_tokens，8_192 会把参数 JSON 掐断在字符串中间（2026-07-18 生产事故）。
# 策略层统一按 20% 规则乐观发放（封顶 65_536，生产两条 managed 渠道均实测接受
# >=51_200）；端点实际承受力自适应处理，不在这里按模型名/模式做白名单：
# - 端点拒收高 max_tokens（确定性 4xx）→ chat.py 降档到 CONSERVATIVE_OUTPUT_
#   BUDGET_TOKENS 重试一次并按端点缓存，之后不再多付失败请求；
# - 端点静默截断 → finish_reason=length 的「拆小修改」corrective 兜底。
OUTPUT_BUDGET_CEILING_TOKENS = 65_536
CONSERVATIVE_OUTPUT_BUDGET_TOKENS = 8_192


@dataclass(frozen=True)
class ResolvedContextPolicy:
    normalized_model: str
    provider_context_limit: int
    effective_context_limit: int
    reserved_output_tokens: int
    compress_threshold: int
    resolution_source: str


def normalize_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[1]
    return normalized


def calculate_context_thresholds(effective_context_limit: int) -> tuple[int, int]:
    reserved_output_tokens = min(
        OUTPUT_BUDGET_CEILING_TOKENS,
        max(2_048, int(effective_context_limit * 0.2)),
    )
    compress_threshold = min(
        int(effective_context_limit * 0.9),
        effective_context_limit - reserved_output_tokens,
    )
    return reserved_output_tokens, compress_threshold


def conservative_output_budget_policy(policy: ResolvedContextPolicy) -> ResolvedContextPolicy:
    """已实锤拒收高输出预算的端点：预算与压缩阈值一起回到保守值。

    compress_threshold 必须同步重算——继续用乐观预算算出的阈值会让长会话
    为一个发不出去的高预算提前压缩历史。"""
    if policy.reserved_output_tokens <= CONSERVATIVE_OUTPUT_BUDGET_TOKENS:
        return policy
    compress_threshold = min(
        int(policy.effective_context_limit * 0.9),
        policy.effective_context_limit - CONSERVATIVE_OUTPUT_BUDGET_TOKENS,
    )
    return replace(
        policy,
        reserved_output_tokens=CONSERVATIVE_OUTPUT_BUDGET_TOKENS,
        compress_threshold=compress_threshold,
    )


def clamp_custom_context_limit_override(
    custom_effective_limit: int | None,
    provider_context_limit: int | None = None,
) -> int | None:
    if custom_effective_limit is None:
        return None

    clamped_limit = max(MIN_EFFECTIVE_CONTEXT_LIMIT, int(custom_effective_limit))
    if provider_context_limit is not None:
        clamped_limit = min(clamped_limit, provider_context_limit)
    return clamped_limit


def build_context_policy(
    normalized_model: str,
    provider_context_limit: int,
    effective_context_limit: int,
    resolution_source: str,
) -> ResolvedContextPolicy:
    reserved_output_tokens, compress_threshold = calculate_context_thresholds(
        effective_context_limit,
    )
    return ResolvedContextPolicy(
        normalized_model=normalized_model,
        provider_context_limit=provider_context_limit,
        effective_context_limit=effective_context_limit,
        reserved_output_tokens=reserved_output_tokens,
        compress_threshold=compress_threshold,
        resolution_source=resolution_source,
    )


def resolve_context_policy(model_name: str, custom_effective_limit: int | None = None) -> ResolvedContextPolicy:
    normalized_model = normalize_model_name(model_name)
    tier_name = EXACT_MODEL_TIERS.get(normalized_model)
    resolution_source = "exact_match"

    if tier_name is None:
        tier_name = _resolve_family_tier(normalized_model)
        resolution_source = "family_fallback"

    if tier_name is None:
        tier_name = UNKNOWN_FALLBACK_TIER
        resolution_source = "unknown_fallback"

    provider_context_limit, effective_context_limit = TIER_LIMITS[tier_name]

    if custom_effective_limit is not None:
        effective_context_limit = clamp_custom_context_limit_override(
            custom_effective_limit,
            provider_context_limit=provider_context_limit,
        )
        resolution_source = "manual_override"

    return build_context_policy(
        normalized_model=normalized_model,
        provider_context_limit=provider_context_limit,
        effective_context_limit=effective_context_limit,
        resolution_source=resolution_source,
    )


def _resolve_family_tier(normalized_model: str) -> str | None:
    for family_prefix, tier_name in FAMILY_MODEL_TIERS.items():
        if normalized_model.startswith(family_prefix):
            return tier_name
    return None
