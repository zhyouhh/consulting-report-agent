import os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock
from backend import accounts


class AccountsUserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start(); accounts.init_db()

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_and_verify(self):
        uid = accounts.create_user("alice", "s3cret-pw")
        self.assertTrue(uid)
        rec = accounts.get_user_by_username("alice")
        self.assertEqual(rec["uid"], uid)
        self.assertNotIn("password_hash", rec)   # 公共 getter 不暴露 hash
        self.assertTrue(accounts.verify_user_password("alice", "s3cret-pw"))
        self.assertFalse(accounts.verify_user_password("alice", "wrong"))

    def test_verify_with_corrupted_hash_returns_false(self):
        uid = accounts.create_user("dave", "pw")
        with accounts._db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE uid=?", ("$argon2id$garbage", uid))
        self.assertFalse(accounts.verify_user_password("dave", "pw"))

    def test_duplicate_username(self):
        accounts.create_user("bob", "pw1")
        with self.assertRaises(accounts.UsernameTakenError):
            accounts.create_user("bob", "pw2")

    def test_change_password(self):
        uid = accounts.create_user("carol", "old")
        accounts.set_user_password(uid, "new")
        self.assertFalse(accounts.verify_user_password("carol", "old"))
        self.assertTrue(accounts.verify_user_password("carol", "new"))
        self.assertEqual(accounts.get_user_by_uid(uid)["username"], "carol")


class AccountsSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start(); accounts.init_db()
        self.uid = accounts.create_user("dave", "pw")

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_roundtrip(self):
        t = accounts.create_session(self.uid, ttl_days=30, ip="1.2.3.4", ua="ua")
        self.assertEqual(accounts.get_session_uid(t), self.uid)

    def test_stored_as_hash(self):
        t = accounts.create_session(self.uid)
        with accounts._db() as c:
            rows = c.execute("SELECT token_hash FROM sessions").fetchall()
        self.assertTrue(all(t not in r["token_hash"] for r in rows))

    def test_expired_rejected(self):
        self.assertIsNone(accounts.get_session_uid(accounts.create_session(self.uid, ttl_days=-1)))

    def test_delete_and_delete_all(self):
        t1 = accounts.create_session(self.uid); t2 = accounts.create_session(self.uid)
        accounts.delete_session(t1)
        self.assertIsNone(accounts.get_session_uid(t1)); self.assertEqual(accounts.get_session_uid(t2), self.uid)
        accounts.delete_user_sessions(self.uid); self.assertIsNone(accounts.get_session_uid(t2))

    def test_disabled_user_rejected(self):
        t = accounts.create_session(self.uid); accounts.set_user_disabled(self.uid, True)
        self.assertIsNone(accounts.get_session_uid(t))

    def test_disable_revokes_and_survives_reenable(self):
        t = accounts.create_session(self.uid)
        accounts.set_user_disabled(self.uid, True)
        self.assertIsNone(accounts.get_session_uid(t))          # rejected while disabled
        accounts.set_user_disabled(self.uid, False)
        self.assertIsNone(accounts.get_session_uid(t))          # still dead after re-enable (row was deleted)

    def test_disabled_user_cannot_get_new_session(self):
        accounts.set_user_disabled(self.uid, True)
        with self.assertRaises(accounts.InactiveUserError):
            accounts.create_session(self.uid)

    def test_session_for_unknown_uid_raises(self):
        with self.assertRaises(accounts.InactiveUserError):
            accounts.create_session("no-such-uid")


class AccountsConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start(); accounts.init_db()

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_get_set_default(self):
        self.assertEqual(accounts.get_config("invite_code", "fb"), "fb")
        accounts.set_config("invite_code", "JOIN"); self.assertEqual(accounts.get_config("invite_code"), "JOIN")

    def test_seed_idempotent(self):
        accounts.seed_config_if_absent("invite_code", "S1"); accounts.seed_config_if_absent("invite_code", "S2")
        self.assertEqual(accounts.get_config("invite_code"), "S1")

    def test_set_config_updates_existing(self):
        # 锁住 upsert 的 UPDATE 分支：坏成 INSERT OR IGNORE 会在此处失败
        accounts.set_config("invite_code", "A")
        accounts.set_config("invite_code", "B")
        self.assertEqual(accounts.get_config("invite_code"), "B")


# tests/test_accounts.py（追加；该文件已有 CRA_DATA_ROOT 隔离夹具，沿用其 setUp/tearDown 模式）
import importlib


class UsageDailyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name
        import backend.config as config; importlib.reload(config)
        import backend.tenant as tenant; importlib.reload(tenant)
        global accounts
        import backend.accounts as accounts; importlib.reload(accounts)
        accounts.init_db()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CRA_DATA_ROOT", None)
        else:
            os.environ["CRA_DATA_ROOT"] = self._old
        self._tmp.cleanup()

    def test_add_usage_accumulates_atomically(self):
        accounts.add_usage("u1", "2026-06-22", 100, hit=1, miss=2, output=3)
        accounts.add_usage("u1", "2026-06-22", 50, hit=4, miss=5, output=6)
        row = accounts.get_usage_today("u1", "2026-06-22")
        self.assertEqual(row["cost_micro_yuan"], 150)
        self.assertEqual(row["cache_hit_tokens"], 5)
        self.assertEqual(row["cache_miss_tokens"], 7)
        self.assertEqual(row["output_tokens"], 9)

    def test_cross_day_separate_rows(self):
        accounts.add_usage("u1", "2026-06-22", 100, 0, 0, 0)
        accounts.add_usage("u1", "2026-06-23", 7, 0, 0, 0)
        self.assertEqual(accounts.get_usage_today("u1", "2026-06-22")["cost_micro_yuan"], 100)
        self.assertEqual(accounts.get_usage_today("u1", "2026-06-23")["cost_micro_yuan"], 7)

    def test_get_usage_today_zero_when_absent(self):
        self.assertEqual(accounts.get_usage_today("nobody", "2026-06-22")["cost_micro_yuan"], 0)

    def test_add_usage_failclosed_column_accumulates_separately(self):
        # fail-closed 估算 token 独立列：不混入 cache_miss（2026-07-06 命中率污染修复）。
        accounts.add_usage("u1", "2026-07-06", 100, hit=1, miss=2, output=3, failclosed=256)
        accounts.add_usage("u1", "2026-07-06", 50, 0, 0, 0, failclosed=100)
        row = [r for r in accounts.get_usage_history("2026-07-06") if r["uid"] == "u1"][0]
        self.assertEqual(row["failclosed_tokens"], 356)
        self.assertEqual(row["cache_miss_tokens"], 2)   # miss 不被 failclosed 污染
        self.assertEqual(row["cost_micro_yuan"], 150)

    def test_init_db_migrates_legacy_usage_daily_without_failclosed_column(self):
        # 已部署库（B2 老 schema、无 failclosed_tokens）→ init_db 幂等 ALTER 补列、旧行默认 0。
        with accounts._db() as conn:
            conn.execute("DROP TABLE usage_daily")
            conn.execute(
                "CREATE TABLE usage_daily(uid TEXT NOT NULL, day TEXT NOT NULL,"
                " cost_micro_yuan INTEGER NOT NULL DEFAULT 0, cache_hit_tokens INTEGER NOT NULL DEFAULT 0,"
                " cache_miss_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,"
                " PRIMARY KEY(uid, day))")
            conn.execute("INSERT INTO usage_daily(uid,day,cost_micro_yuan) VALUES('legacy','2026-07-01',42)")
        accounts.init_db()   # 迁移
        row = [r for r in accounts.get_usage_history("2026-07-01") if r["uid"] == "legacy"][0]
        self.assertEqual(row["failclosed_tokens"], 0)
        self.assertEqual(row["cost_micro_yuan"], 42)
        accounts.init_db()   # 再跑一次：幂等不炸

    def test_effective_cap_prefers_user_override_then_global_then_default(self):
        # 无 override、无 app_config → 默认 5_000_000
        uid = accounts.create_user("alice", "pw-strong-123")
        from backend.config import DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN)
        # 设全局 → 取全局
        accounts.set_config("global_daily_cap_micro_yuan", "2000000")
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 2000000)
        # 设 user override → 取 override
        accounts.set_user_daily_cap_micro(uid, 9000)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 9000)
        # override 清回 None → 退全局
        accounts.set_user_daily_cap_micro(uid, None)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 2000000)


class AdminAccountsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start(); accounts.init_db()

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_list_all_users_returns_rows_without_password_hash(self):
        accounts.create_user("alice", "pw-123456")
        accounts.create_user("bob", "pw-123456")
        rows = accounts.list_all_users()
        self.assertEqual({r["username"] for r in rows}, {"alice", "bob"})
        self.assertNotIn("password_hash", rows[0])
        for k in ("uid", "username", "is_admin", "disabled", "created_at"):
            self.assertIn(k, rows[0])

    def test_admin_reset_password_sets_flag_and_revokes_sessions(self):
        uid = accounts.create_user("carol", "old-123456")
        token = accounts.create_session(uid)
        accounts.admin_reset_password(uid, "new-123456")
        self.assertTrue(accounts.verify_user_password("carol", "new-123456"))
        self.assertIsNone(accounts.get_session_uid(token))           # 会话被撤
        self.assertTrue(accounts.get_user_by_uid(uid)["must_change_password"])

    def test_rotate_invite_code_changes_and_returns_new(self):
        accounts.set_config("invite_code", "OLD")
        new_code = accounts.rotate_invite_code()
        self.assertNotEqual(new_code, "OLD")
        self.assertEqual(accounts.get_config("invite_code"), new_code)
        self.assertGreaterEqual(len(new_code), 8)

    def test_custom_api_extra_hosts_roundtrip(self):
        self.assertEqual(accounts.get_custom_api_extra_hosts(), [])
        accounts.set_custom_api_extra_hosts(["My.LLM.cn ", "", "other.host"])
        # 归一化：去空白 + 小写 + 去空项；持久化可读回
        self.assertEqual(set(accounts.get_custom_api_extra_hosts()), {"my.llm.cn", "other.host"})

    # —— BLOCKER 1（codex 红队）：admin_set_user_disabled 原子守卫（last-admin + 撤会话同事务）——
    def test_admin_set_user_disabled_rejects_last_admin(self):
        admin = accounts.create_user("solo-admin", "pw-123456", is_admin=True)
        with self.assertRaises(accounts.LastAdminError):
            accounts.admin_set_user_disabled(admin, True)
        # 仍活跃（事务回滚 / 未写）
        self.assertFalse(accounts.get_user_by_uid(admin)["disabled"])

    def test_admin_set_user_disabled_disables_non_last_admin_and_revokes(self):
        a1 = accounts.create_user("a1", "pw-123456", is_admin=True)
        a2 = accounts.create_user("a2", "pw-123456", is_admin=True)
        tok = accounts.create_session(a2)
        accounts.admin_set_user_disabled(a2, True)   # 还剩 a1 活跃 → 允许
        self.assertTrue(accounts.get_user_by_uid(a2)["disabled"])
        self.assertIsNone(accounts.get_session_uid(tok))   # 会话同事务撤销

    def test_admin_set_user_disabled_allows_disable_non_admin(self):
        accounts.create_user("only-admin", "pw-123456", is_admin=True)
        user = accounts.create_user("plain", "pw-123456")
        accounts.admin_set_user_disabled(user, True)   # 非 admin 不受 last-admin 守卫
        self.assertTrue(accounts.get_user_by_uid(user)["disabled"])

    def test_admin_set_user_disabled_can_reenable(self):
        a1 = accounts.create_user("a1", "pw-123456", is_admin=True)
        a2 = accounts.create_user("a2", "pw-123456", is_admin=True)
        accounts.admin_set_user_disabled(a2, True)
        accounts.admin_set_user_disabled(a2, False)   # 重新启用不受守卫（disabled=False）
        self.assertFalse(accounts.get_user_by_uid(a2)["disabled"])

    def test_concurrent_mutual_admin_disable_keeps_one_active(self):
        # 核心回归：两 admin 并发互禁。原子事务（BEGIN IMMEDIATE）串行化两请求，
        # 第二个提交前重读计数会看到只剩 1 个活跃 admin → 抛 LastAdminError。
        # 断言：最终活跃 admin 数 ≥ 1（绝不归零）。
        import threading
        a1 = accounts.create_user("ca1", "pw-123456", is_admin=True)
        a2 = accounts.create_user("ca2", "pw-123456", is_admin=True)
        barrier = threading.Barrier(2)
        results = {}

        def worker(actor, target):
            barrier.wait()   # 尽量同时进入
            try:
                accounts.admin_set_user_disabled(target, True)
                results[actor] = "ok"
            except accounts.LastAdminError:
                results[actor] = "rejected"
            except Exception as e:   # noqa: BLE001 — 记录意外异常便于诊断
                results[actor] = f"error:{e!r}"

        t1 = threading.Thread(target=worker, args=("t1", a2))
        t2 = threading.Thread(target=worker, args=("t2", a1))
        t1.start(); t2.start(); t1.join(); t2.join()

        active = [u for u in accounts.list_all_users() if u["is_admin"] and not u["disabled"]]
        self.assertGreaterEqual(len(active), 1, f"活跃 admin 归零！results={results}")
        # 至少一个请求被原子守卫拒绝（不可能两个都 ok）
        self.assertIn("rejected", results.values(), f"两请求都成功 → 守卫失效；results={results}")


class SearchUsageDailyTests(unittest.TestCase):
    """搜索池用量表（provider × key × 天）：累加 / 历史 / 全时段汇总。"""

    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start(); accounts.init_db()

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_add_search_usage_accumulates_atomically(self):
        accounts.add_search_usage("serper", "fp-a", "2026-07-07", calls=1, units=1.0)
        accounts.add_search_usage("serper", "fp-a", "2026-07-07", calls=1, units=2.0, errors=1)
        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calls"], 2)
        self.assertEqual(rows[0]["units"], 3.0)
        self.assertEqual(rows[0]["errors"], 1)

    def test_keys_and_providers_are_separate_rows(self):
        accounts.add_search_usage("serper", "fp-a", "2026-07-07", calls=1, units=1.0)
        accounts.add_search_usage("serper", "fp-b", "2026-07-07", calls=1, units=1.0)
        accounts.add_search_usage("exa", "fp-a", "2026-07-07", calls=1, units=1.0)
        rows = accounts.get_search_usage_history("2026-07-07")
        self.assertEqual(len(rows), 3)

    def test_history_filters_by_since_day(self):
        accounts.add_search_usage("serper", "fp-a", "2026-06-01", calls=5, units=5.0)
        accounts.add_search_usage("serper", "fp-a", "2026-07-07", calls=1, units=1.0)
        rows = accounts.get_search_usage_history("2026-07-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day"], "2026-07-07")

    def test_totals_aggregate_across_days(self):
        accounts.add_search_usage("serper", "fp-a", "2026-06-01", calls=5, units=5.0, errors=1)
        accounts.add_search_usage("serper", "fp-a", "2026-07-07", calls=1, units=2.0)
        accounts.add_search_usage("serper", "fp-b", "2026-07-07", calls=3, units=3.0)
        totals = {(r["provider"], r["key_id"]): r for r in accounts.get_search_usage_totals()}
        self.assertEqual(totals[("serper", "fp-a")]["calls"], 6)
        self.assertEqual(totals[("serper", "fp-a")]["units"], 7.0)
        self.assertEqual(totals[("serper", "fp-a")]["errors"], 1)
        self.assertEqual(totals[("serper", "fp-b")]["calls"], 3)

    def test_init_db_migration_is_idempotent(self):
        accounts.init_db()
        accounts.init_db()
        accounts.add_search_usage("brave", "fp-a", "2026-07-07", calls=1, units=1.0)
        self.assertEqual(len(accounts.get_search_usage_totals()), 1)
