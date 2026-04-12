from dataclasses import dataclass


TIER_LIMITS = {
    "tier_1m": (1_000_000, 200_000),
    "tier_400k": (400_000, 320_000),
    "tier_256k": (256_000, 200_000),
    "tier_200k": (200_000, 180_000),
    "tier_128k": (128_000, 110_000),
}

EXACT_MODEL_TIERS = {
    "gemini-3-flash": "tier_1m",
    "kimi-k2.5": "tier_256k",
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
    reserved_output_tokens = min(8_192, max(2_048, int(effective_context_limit * 0.2)))
    compress_threshold = min(
        int(effective_context_limit * 0.9),
        effective_context_limit - reserved_output_tokens,
    )
    return reserved_output_tokens, compress_threshold


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
    reserved_output_tokens, compress_threshold = calculate_context_thresholds(effective_context_limit)
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
