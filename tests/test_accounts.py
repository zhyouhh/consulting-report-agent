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
