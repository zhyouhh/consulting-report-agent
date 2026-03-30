import unittest

from backend.context_policy import (
    ResolvedContextPolicy,
    calculate_context_thresholds,
    normalize_model_name,
    resolve_context_policy,
)


class ContextPolicyTests(unittest.TestCase):
    def test_normalize_model_name_lowercases_and_strips_vendor_prefix(self):
        self.assertEqual(normalize_model_name("MoonshotAI/Kimi-K2.5"), "kimi-k2.5")

    def test_normalize_model_name_strips_multi_segment_vendor_prefix(self):
        self.assertEqual(
            normalize_model_name("openrouter/google/gemini-3-flash"),
            "gemini-3-flash",
        )

    def test_exact_match_for_managed_gemini_uses_1m_provider_and_500k_effective(self):
        policy = resolve_context_policy("gemini-3-flash")

        self.assertIsInstance(policy, ResolvedContextPolicy)
        self.assertEqual(policy.normalized_model, "gemini-3-flash")
        self.assertEqual(policy.provider_context_limit, 1_000_000)
        self.assertEqual(policy.effective_context_limit, 500_000)
        self.assertEqual(policy.resolution_source, "exact_match")

    def test_vendor_prefixed_model_is_normalized_before_exact_lookup(self):
        policy = resolve_context_policy("moonshotai/Kimi-K2.5")

        self.assertEqual(policy.normalized_model, "kimi-k2.5")
        self.assertEqual(policy.provider_context_limit, 256_000)
        self.assertEqual(policy.effective_context_limit, 200_000)
        self.assertEqual(policy.resolution_source, "exact_match")

    def test_family_fallback_handles_gpt_5_4(self):
        policy = resolve_context_policy("gpt-5.4")

        self.assertEqual(policy.provider_context_limit, 400_000)
        self.assertEqual(policy.effective_context_limit, 320_000)
        self.assertEqual(policy.resolution_source, "family_fallback")

    def test_vendor_prefixed_openrouter_gemini_hits_gemini_tier(self):
        policy = resolve_context_policy("openrouter/google/gemini-3-flash")

        self.assertEqual(policy.normalized_model, "gemini-3-flash")
        self.assertEqual(policy.provider_context_limit, 1_000_000)
        self.assertEqual(policy.effective_context_limit, 500_000)
        self.assertEqual(policy.resolution_source, "exact_match")

    def test_gpt_4_1_mini_uses_a_known_tier_instead_of_unknown_fallback(self):
        policy = resolve_context_policy("gpt-4.1-mini")

        self.assertEqual(policy.provider_context_limit, 400_000)
        self.assertEqual(policy.effective_context_limit, 320_000)
        self.assertEqual(policy.resolution_source, "family_fallback")

    def test_unknown_model_falls_back_to_128k_tier(self):
        policy = resolve_context_policy("totally-unknown-model")

        self.assertEqual(policy.provider_context_limit, 128_000)
        self.assertEqual(policy.effective_context_limit, 110_000)
        self.assertEqual(policy.resolution_source, "unknown_fallback")

    def test_manual_override_is_clamped_to_provider_limit(self):
        policy = resolve_context_policy("gpt-5.2", custom_effective_limit=900_000)

        self.assertEqual(policy.provider_context_limit, 400_000)
        self.assertEqual(policy.effective_context_limit, 400_000)
        self.assertEqual(policy.resolution_source, "manual_override")

    def test_manual_override_has_ui_aligned_minimum_limit_of_4096(self):
        policy = resolve_context_policy("gpt-5.2", custom_effective_limit=3000)

        self.assertEqual(policy.effective_context_limit, 4096)
        self.assertGreaterEqual(policy.compress_threshold, 0)
        self.assertLessEqual(policy.compress_threshold, policy.effective_context_limit)
        self.assertEqual(policy.resolution_source, "manual_override")

    def test_manual_override_has_a_sane_lower_bound_for_derived_thresholds(self):
        policy = resolve_context_policy("gpt-5.2", custom_effective_limit=1)

        self.assertEqual(policy.effective_context_limit, 4096)
        self.assertGreaterEqual(policy.effective_context_limit, policy.reserved_output_tokens)
        self.assertGreaterEqual(policy.compress_threshold, 0)
        self.assertLessEqual(policy.compress_threshold, policy.effective_context_limit)
        self.assertEqual(policy.resolution_source, "manual_override")

    def test_threshold_helper_centralizes_reserved_and_compress_math(self):
        reserved_output_tokens, compress_threshold = calculate_context_thresholds(500_000)

        self.assertEqual(reserved_output_tokens, 8_192)
        self.assertEqual(compress_threshold, 450_000)


if __name__ == "__main__":
    unittest.main()
