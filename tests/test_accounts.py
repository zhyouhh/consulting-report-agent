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
