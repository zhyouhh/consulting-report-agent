import os, shutil, tempfile, unittest, importlib
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient


class AuthApiTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp), "CRA_INVITE_CODE": "JOIN"})
        self._env.start()
        from backend import accounts, config
        importlib.reload(config); importlib.reload(accounts)
        # reload(main) runs heal_stale_managed_model at module scope (may hit network) → mock it.
        self._heal = mock.patch("backend.config.heal_stale_managed_model", side_effect=lambda s: (s, None))
        self._heal.start()
        import backend.main as m; importlib.reload(m)
        self.m = m; m.app.state.auth_required = True
        self._reset_module_singletons()
        self.client = TestClient(m.app)

    def _reset_module_singletons(self):
        from backend import chat as cm, independent_review as im
        cm._PROJECT_REQUEST_LOCKS.clear(); cm._CONVERSATION_STATE_LOCKS.clear()
        cm._SEARCH_ROUTER_SINGLETON = None
        im._INDEPENDENT_REVIEW_LOCKS.clear()
        with im._REVIEW_SESSION_STORE._guard:
            im._REVIEW_SESSION_STORE._records.clear()

    def tearDown(self):
        self._heal.stop(); self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)


class UnauthedTests(AuthApiTestBase):
    def test_health_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)


class GetCurrentUidTests(AuthApiTestBase):
    # Directly exercise the dependency (endpoints are wired in T11, so we can't go through /api/projects yet).
    def _req(self):
        import types
        # minimal fake request carrying app.state + cookies
        return types.SimpleNamespace(app=self.m.app, cookies={})

    def test_uid_local_when_auth_disabled(self):
        self.m.app.state.auth_required = False
        self.assertEqual(self.m.get_current_uid(self._req()), "local")

    def test_401_when_auth_enabled_no_cookie(self):
        self.m.app.state.auth_required = True
        with self.assertRaises(self.m.HTTPException) as ctx:
            self.m.get_current_uid(self._req())
        self.assertEqual(ctx.exception.status_code, 401)


class FactoryAndScopeTests(AuthApiTestBase):
    def test_skill_engine_cached_per_uid(self):
        self.assertIs(self.m.get_skill_engine("uA"), self.m.get_skill_engine("uA"))
        self.assertIsNot(self.m.get_skill_engine("uA"), self.m.get_skill_engine("uB"))

    def test_require_project_canonicalizes_and_isolates(self):
        from unittest import mock
        from backend.tenant import tenant_project_key
        engA = self.m.get_skill_engine("uA")
        # owner: resolve a ref (id or name) to canonical id + composite lock_key
        with mock.patch.object(engA, "get_project_record", return_value={"id": "proj-abc", "name": "x"}):
            scope = self.m.require_project("x", uid="uA")
        self.assertEqual(scope.project_id, "proj-abc")
        self.assertEqual(scope.lock_key, tenant_project_key("uA", "proj-abc"))
        # other uid's fresh engine has no such project -> 404 (natural isolation)
        with self.assertRaises(self.m.HTTPException) as ctx:
            self.m.require_project("proj-abc", uid="uB")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_admin_dependency(self):
        from backend import accounts
        admin_uid = accounts.create_user("adm", "pw-123456", is_admin=True)
        user_uid = accounts.create_user("usr", "pw-123456")
        self.assertEqual(self.m.get_current_admin(admin_uid), admin_uid)   # admin passes
        with self.assertRaises(self.m.HTTPException) as ctx:
            self.m.get_current_admin(user_uid)                            # non-admin -> 403
        self.assertEqual(ctx.exception.status_code, 403)
