"""搜索池用量记账 + 额度报告（backend/search_quota.py）。

夹具沿用 test_accounts.py 范式：隔离 CRA_DATA_ROOT + init_db；tavily /usage 一律 mock requests。
key 身份 = sha256 指纹（key_fingerprint），测试里用配置 key 现算，绝不硬编码下标。
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import accounts, search_quota
from backend.config import (
    ManagedSearchLimitsConfig,
    ManagedSearchPoolConfig,
    ManagedSearchProviderConfig,
    ManagedSearchQuotaConfig,
    ManagedSearchRoutingConfig,
)

TAVILY_KEYS = ("tvly-aaaa1111", "tvly-bbbb2222")
BRAVE_KEYS = ("brave-key-0001",)
SERPER_KEYS = ("serper-key-0001", "serper-key-0002")
EXA_KEYS = ("exa-key-00000001",)

fp = search_quota.key_fingerprint


def _provider_cfg(*, api_keys=("k",), weight=1, enabled=True, quota=None):
    return ManagedSearchProviderConfig(
        enabled=enabled,
        api_key=api_keys[0] if api_keys else "",
        api_keys=tuple(api_keys),
        weight=weight,
        minute_limit=60,
        daily_soft_limit=1200,
        cooldown_seconds=180,
        quota=quota or ManagedSearchQuotaConfig(),
    )


def _make_pool_config(providers_override=None):
    providers = {
        "tavily": _provider_cfg(
            api_keys=TAVILY_KEYS,
            weight=3,
            quota=ManagedSearchQuotaConfig(model="monthly", unit="credits", per_key_quota=1000),
        ),
        "brave": _provider_cfg(
            api_keys=BRAVE_KEYS,
            quota=ManagedSearchQuotaConfig(model="monthly", unit="requests", per_key_quota=1000),
        ),
        "serper": _provider_cfg(
            api_keys=SERPER_KEYS,
            quota=ManagedSearchQuotaConfig(model="one_time", unit="credits", per_key_quota=2500),
        ),
        "exa": _provider_cfg(
            api_keys=EXA_KEYS,
            quota=ManagedSearchQuotaConfig(
                model="one_time", unit="usd", per_key_quota=10, est_cost_per_call=0.004
            ),
        ),
    }
    if providers_override:
        providers.update(providers_override)
    return ManagedSearchPoolConfig(
        version=1,
        providers=providers,
        routing=ManagedSearchRoutingConfig(
            primary=["tavily", "brave"],
            secondary=["serper", "exa"],
            native_fallback=True,
        ),
        limits=ManagedSearchLimitsConfig(
            per_turn_searches=5,
            project_minute_limit=30,
            global_minute_limit=60,
            memory_cache_ttl_seconds=21600,
            project_cache_ttl_seconds=86400,
        ),
    )


class SearchQuotaTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()
        accounts.init_db()
        # tavily TTL 缓存是模块级进程内状态，逐测试清空防串味
        search_quota._tavily_cache.clear()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class KeyFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_nonsecret_and_short(self):
        a = search_quota.key_fingerprint("serper-key-0001")
        self.assertEqual(a, search_quota.key_fingerprint("serper-key-0001"))
        self.assertEqual(len(a), 12)
        self.assertNotIn("serper", a)

    def test_empty_key_folds_to_unknown(self):
        self.assertEqual(search_quota.key_fingerprint(""), search_quota.UNKNOWN_KEY_ID)
        self.assertEqual(search_quota.key_fingerprint(None), search_quota.UNKNOWN_KEY_ID)


class RecordSearchUsageTests(SearchQuotaTestBase):
    def test_records_calls_units_and_errors_per_key_day(self):
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value="2026-07-07"):
            search_quota.record_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[1]), calls=1, units=2.0, errors=0
            )
            search_quota.record_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[1]), calls=1, units=1.0, errors=0
            )
            search_quota.record_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[0]), calls=0, units=0.0, errors=1
            )

        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(len(rows), 2)
        by_key = {r["key_id"]: r for r in rows}
        self.assertEqual(by_key[fp(SERPER_KEYS[1])]["calls"], 2)
        self.assertEqual(by_key[fp(SERPER_KEYS[1])]["units"], 3.0)
        self.assertEqual(by_key[fp(SERPER_KEYS[0])]["errors"], 1)

    def test_missing_key_id_folds_to_sentinel(self):
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value="2026-07-07"):
            search_quota.record_search_usage(
                provider="brave", key_id=None, calls=1, units=1.0, errors=0
            )
        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(rows[0]["key_id"], search_quota.UNKNOWN_KEY_ID)

    def test_persists_quota_snapshot_to_app_config(self):
        snapshot = {"month_remaining": 950, "month_limit": 1000, "observed_at": 1751000000.0}
        kid = fp(BRAVE_KEYS[0])
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value="2026-07-07"):
            search_quota.record_search_usage(
                provider="brave", key_id=kid, calls=1, units=1.0, errors=0, quota_snapshot=snapshot
            )
        raw = accounts.get_config(f"search_quota_snapshot:brave:{kid}")
        self.assertEqual(json.loads(raw), snapshot)

    def test_recorder_is_best_effort_and_never_raises(self):
        with mock.patch(
            "backend.search_quota.accounts.add_search_usage", side_effect=RuntimeError("db down")
        ):
            # 不应上抛——记账故障不能影响搜索
            search_quota.record_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[0]), calls=1, units=1.0, errors=0
            )


class EnqueueSearchUsageTests(SearchQuotaTestBase):
    def test_enqueue_lands_in_db_via_background_worker(self):
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value="2026-07-07"):
            search_quota.enqueue_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[0]), calls=1, units=1.0, errors=0
            )
            self.assertTrue(search_quota.wait_for_usage_idle(timeout=5))
        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calls"], 1)

    def test_enqueue_never_blocks_or_raises_when_queue_full(self):
        # 塞满有界队列：多出来的必须被丢弃而不是阻塞/抛错（丢监控数据 > 卡用户搜索）
        with mock.patch.object(search_quota, "_ensure_usage_worker"):   # 不起 worker，队列只进不出
            fresh_queue = search_quota.queue.Queue(maxsize=4)
            with mock.patch.object(search_quota, "_usage_queue", fresh_queue):
                for _ in range(10):
                    search_quota.enqueue_search_usage(
                        provider="serper", key_id="x", calls=1, units=1.0, errors=0
                    )
                self.assertEqual(fresh_queue.qsize(), 4)

    def test_malformed_kwargs_do_not_kill_worker(self):
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value="2026-07-07"):
            search_quota.enqueue_search_usage(bogus_field="boom")   # record 不认识的形状
            search_quota.enqueue_search_usage(
                provider="serper", key_id=fp(SERPER_KEYS[0]), calls=1, units=1.0, errors=0
            )
            self.assertTrue(search_quota.wait_for_usage_idle(timeout=5))
        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(len(rows), 1)   # 畸形消息被吞掉、正常消息照常落库


class FetchTavilyUsageTests(SearchQuotaTestBase):
    def _ok_response(self, plan_usage=100, plan_limit=1000, plan="Free", key_usage=42):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "key": {"usage": key_usage, "limit": None},
            "account": {
                "current_plan": plan,
                "plan_usage": plan_usage,
                "plan_limit": plan_limit,
            },
        }
        return resp

    def test_fetches_per_key_and_parses_fields(self):
        with mock.patch("backend.search_quota.requests.get", return_value=self._ok_response()) as m:
            results = search_quota.fetch_tavily_usage(["tvly-a", "tvly-b"])
        self.assertEqual(m.call_count, 2)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(results[0]["plan_usage"], 100.0)
        self.assertEqual(results[0]["plan_limit"], 1000.0)
        self.assertEqual(results[0]["key_usage"], 42.0)
        self.assertIsNone(results[0]["key_limit"])

    def test_http_error_degrades_to_per_key_error(self):
        resp = mock.Mock()
        resp.status_code = 401
        with mock.patch("backend.search_quota.requests.get", return_value=resp):
            results = search_quota.fetch_tavily_usage(["tvly-bad"])
        self.assertFalse(results[0]["ok"])
        self.assertIn("401", results[0]["error"])

    def test_network_exception_degrades_to_per_key_error(self):
        with mock.patch("backend.search_quota.requests.get", side_effect=OSError("boom")):
            results = search_quota.fetch_tavily_usage(["tvly-a"])
        self.assertFalse(results[0]["ok"])

    def test_ttl_cache_avoids_refetch_within_window(self):
        with mock.patch("backend.search_quota.requests.get", return_value=self._ok_response()) as m:
            search_quota.fetch_tavily_usage(["tvly-a"])
            search_quota.fetch_tavily_usage(["tvly-a"])
        self.assertEqual(m.call_count, 1)

    def test_force_refresh_bypasses_cache(self):
        with mock.patch("backend.search_quota.requests.get", return_value=self._ok_response()) as m:
            search_quota.fetch_tavily_usage(["tvly-a"])
            search_quota.fetch_tavily_usage(["tvly-a"], force_refresh=True)
        self.assertEqual(m.call_count, 2)

    def test_failed_result_not_cached(self):
        bad = mock.Mock()
        bad.status_code = 500
        with mock.patch(
            "backend.search_quota.requests.get", side_effect=[bad, self._ok_response()]
        ) as m:
            first = search_quota.fetch_tavily_usage(["tvly-a"])
            second = search_quota.fetch_tavily_usage(["tvly-a"])
        self.assertEqual(m.call_count, 2)
        self.assertFalse(first[0]["ok"])
        self.assertTrue(second[0]["ok"])


class BuildReportTests(SearchQuotaTestBase):
    TODAY = "2026-07-07"

    def _seed_usage(self):
        # serper：一次性 credits——全时段累计进估算（含 6 月的历史行）
        accounts.add_search_usage("serper", fp(SERPER_KEYS[0]), "2026-06-20", calls=100, units=100.0)
        accounts.add_search_usage("serper", fp(SERPER_KEYS[1]), "2026-07-07", calls=40, units=41.0)
        # exa：usd 估算按 calls × est_cost_per_call
        accounts.add_search_usage("exa", fp(EXA_KEYS[0]), "2026-07-01", calls=500, units=500.0)
        # brave：本月至今 30 次（monthly 估算窗口只认 7 月，6 月行不算）
        accounts.add_search_usage("brave", fp(BRAVE_KEYS[0]), "2026-06-30", calls=999, units=999.0)
        accounts.add_search_usage("brave", fp(BRAVE_KEYS[0]), "2026-07-06", calls=30, units=30.0)
        # 失败计数
        accounts.add_search_usage("serper", fp(SERPER_KEYS[0]), "2026-07-07", errors=2)

    def _build(self, config=None, **kwargs):
        with mock.patch("backend.search_quota.metering.today_shanghai", return_value=self.TODAY):
            return search_quota.build_search_quota_report(config or _make_pool_config(), **kwargs)

    def test_tavily_uses_live_usage_api(self):
        self._seed_usage()
        live = [
            {"ok": True, "plan": "Free", "plan_usage": 200.0, "plan_limit": 1000.0,
             "key_usage": None, "key_limit": None},
            {"ok": False, "error": "http 401"},
        ]
        with mock.patch("backend.search_quota.fetch_tavily_usage", return_value=live):
            report = self._build()
        tavily = next(p for p in report["providers"] if p["name"] == "tavily")
        self.assertEqual(tavily["source"], "live")
        self.assertEqual(tavily["total_used"], 200.0)
        self.assertEqual(tavily["total_quota"], 1000.0)
        self.assertEqual(tavily["total_remaining"], 800.0)
        self.assertEqual(tavily["keys"][0]["remaining"], 800.0)
        self.assertEqual(tavily["keys"][1]["error"], "http 401")

    def test_tavily_dedupes_same_account_keys(self):
        # 两把 key 属同一账号：plan 字段账号级，逐 key 求和会翻倍 → 必须去重
        same_account = {"ok": True, "plan": "Free", "plan_usage": 200.0, "plan_limit": 1000.0,
                        "key_usage": 10.0, "key_limit": None}
        live = [dict(same_account), dict(same_account)]
        with mock.patch("backend.search_quota.fetch_tavily_usage", return_value=live):
            report = self._build()
        tavily = next(p for p in report["providers"] if p["name"] == "tavily")
        self.assertEqual(tavily["total_quota"], 1000.0)     # 不是 2000
        self.assertEqual(tavily["total_used"], 200.0)       # 不是 400
        self.assertIn("去重", tavily["note"])

    def test_tavily_zero_usage_identical_accounts_not_deduped(self):
        # 月初重置后多个免费账号全是 (Free, 0, 1000)：元组无区分度，不得误合并
        #（部署实测踩过：3 账号被折成 1000/1000，真实 3000/3000）
        fresh = {"ok": True, "plan": "Free", "plan_usage": 0.0, "plan_limit": 1000.0,
                 "key_usage": 0.0, "key_limit": None}
        live = [dict(fresh), dict(fresh)]
        with mock.patch("backend.search_quota.fetch_tavily_usage", return_value=live):
            report = self._build()
        tavily = next(p for p in report["providers"] if p["name"] == "tavily")
        self.assertEqual(tavily["total_quota"], 2000.0)
        self.assertEqual(tavily["total_remaining"], 2000.0)
        self.assertIsNone(tavily["note"])

    def test_report_never_echoes_api_keys_or_their_tails(self):
        live = [
            {"ok": True, "plan": "Free", "plan_usage": 1.0, "plan_limit": 1000.0,
             "key_usage": None, "key_limit": None},
            {"ok": True, "plan": "Free", "plan_usage": 2.0, "plan_limit": 1000.0,
             "key_usage": None, "key_limit": None},
        ]
        with mock.patch("backend.search_quota.fetch_tavily_usage", return_value=live):
            report = self._build()
        dump = json.dumps(report, ensure_ascii=False)
        for key in (*TAVILY_KEYS, *BRAVE_KEYS, *SERPER_KEYS, *EXA_KEYS):
            self.assertNotIn(key, dump)
            self.assertNotIn(key[-4:], dump)   # 连尾 4 位都不许出现（label 只用 sha256 指纹）

    def test_brave_uses_observed_header_snapshot(self):
        self._seed_usage()
        accounts.set_config(
            f"search_quota_snapshot:brave:{fp(BRAVE_KEYS[0])}",
            json.dumps({"month_remaining": 940, "month_limit": 1000, "observed_at": 1751000000.0}),
        )
        report = self._build(include_live=False)
        brave = next(p for p in report["providers"] if p["name"] == "brave")
        self.assertEqual(brave["source"], "observed")
        self.assertEqual(brave["total_remaining"], 940.0)
        self.assertEqual(brave["total_quota"], 1000.0)
        self.assertEqual(brave["keys"][0]["used"], 60.0)

    def test_brave_zero_limit_snapshot_treated_as_no_signal(self):
        # brave 文档：月配额段 0 = unlimited（计量档）→ 无月度信号，回退本地估算
        self._seed_usage()
        accounts.set_config(
            f"search_quota_snapshot:brave:{fp(BRAVE_KEYS[0])}",
            json.dumps({"month_remaining": 0, "month_limit": 0, "observed_at": 1751000000.0}),
        )
        report = self._build(include_live=False)
        brave = next(p for p in report["providers"] if p["name"] == "brave")
        self.assertEqual(brave["source"], "estimated")
        # monthly 估算 = 本月至今 units（30），不含 6 月的 999
        self.assertEqual(brave["total_used"], 30.0)
        self.assertEqual(brave["total_remaining"], 970.0)

    def test_serper_estimates_from_alltime_units(self):
        self._seed_usage()
        report = self._build(include_live=False)
        serper = next(p for p in report["providers"] if p["name"] == "serper")
        self.assertEqual(serper["source"], "estimated")
        self.assertEqual(serper["total_quota"], 5000.0)   # 2500 × 2 keys
        self.assertEqual(serper["total_used"], 141.0)     # 100 + 41（全时段 units 真值）
        self.assertEqual(serper["total_remaining"], 4859.0)

    def test_retired_key_usage_excluded_from_estimate(self):
        # 换掉的 key（指纹不在当前配置里）的历史消耗不再拖累新 key 的剩余估算
        self._seed_usage()
        accounts.add_search_usage("serper", fp("serper-key-RETIRED"), "2026-06-01", calls=2500, units=2500.0)
        report = self._build(include_live=False)
        serper = next(p for p in report["providers"] if p["name"] == "serper")
        self.assertEqual(serper["total_used"], 141.0)     # 不含退役 key 的 2500

    def test_serper_estimate_includes_baseline_used(self):
        config = _make_pool_config(providers_override={
            "serper": _provider_cfg(
                api_keys=SERPER_KEYS,
                quota=ManagedSearchQuotaConfig(
                    model="one_time", unit="credits", per_key_quota=2500, baseline_used=1200
                ),
            ),
        })
        self._seed_usage()
        report = self._build(config, include_live=False)
        serper = next(p for p in report["providers"] if p["name"] == "serper")
        self.assertEqual(serper["total_used"], 1341.0)   # baseline 1200 + 观测 141

    def test_exa_estimates_usd_by_calls_times_unit_cost(self):
        self._seed_usage()
        report = self._build(include_live=False)
        exa = next(p for p in report["providers"] if p["name"] == "exa")
        self.assertEqual(exa["source"], "estimated")
        self.assertEqual(exa["total_quota"], 10.0)
        self.assertEqual(exa["total_used"], 2.0)          # 500 calls × $0.004
        self.assertEqual(exa["total_remaining"], 8.0)

    def test_no_quota_declared_yields_source_none_with_observed_counts(self):
        config = _make_pool_config(providers_override={
            "serper": _provider_cfg(api_keys=SERPER_KEYS),
        })
        self._seed_usage()
        report = self._build(config, include_live=False)
        serper = next(p for p in report["providers"] if p["name"] == "serper")
        self.assertEqual(serper["source"], "none")
        self.assertIsNone(serper["total_remaining"])
        self.assertGreater(serper["observed_calls_30d"], 0)

    def test_history_aggregates_keys_per_provider_day(self):
        self._seed_usage()
        report = self._build(include_live=False)
        serper_today = [
            h for h in report["history"] if h["provider"] == "serper" and h["day"] == self.TODAY
        ]
        self.assertEqual(len(serper_today), 1)
        self.assertEqual(serper_today[0]["calls"], 40.0)
        self.assertEqual(serper_today[0]["errors"], 2.0)

    def test_layer_and_meta_fields_present(self):
        report = self._build(include_live=False)
        by_name = {p["name"]: p for p in report["providers"]}
        self.assertEqual(by_name["tavily"]["layer"], "primary")
        self.assertEqual(by_name["serper"]["layer"], "secondary")
        self.assertEqual(by_name["tavily"]["key_count"], 2)
        self.assertEqual(by_name["exa"]["quota_unit"], "usd")
        self.assertIn("since", report)
        self.assertIn("today", report)


if __name__ == "__main__":
    unittest.main()
