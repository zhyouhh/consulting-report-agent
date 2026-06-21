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
