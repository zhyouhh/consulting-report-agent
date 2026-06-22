import os, shutil, tempfile, unittest, importlib
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient


class AuthApiTestBase(unittest.TestCase):
    # CSRF guard（web 态）校验同源 Origin；web 态测试 client 默认带此源，
    # 并设 CRA_ALLOWED_ORIGIN 让中间件放行。缺失-Origin 用例须用 bare client（见 test_csrf.py）。
    _ALLOWED_ORIGIN = "https://app.example.com"

    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {
            "CRA_DATA_ROOT": str(self._tmp),
            "CRA_INVITE_CODE": "JOIN",
            "CRA_ALLOWED_ORIGIN": self._ALLOWED_ORIGIN,
        })
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
        self.client.headers.update({"origin": self._ALLOWED_ORIGIN})  # 满足 CSRF 同源

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


class AuthFlowTests(AuthApiTestBase):
    def _reg(self, u="alice", p="pw-123456", code="JOIN"):
        return self.client.post("/api/auth/register", json={"username": u, "password": p, "invite_code": code})
    def test_invite_gate(self):
        self.assertEqual(self._reg(code="X").status_code, 403); self.assertEqual(self._reg().status_code, 200)
    def test_login_me_logout(self):
        self._reg(); r = self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})
        self.assertEqual(r.status_code, 200); self.assertIn("cra_session", r.cookies)
        self.assertEqual(self.client.get("/api/auth/me").json()["username"], "alice")
        self.client.post("/api/auth/logout"); self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
    def test_wrong_password(self):
        # use a length-valid but wrong password so it reaches the 401 path (not 422 length-validation)
        self._reg(); self.assertEqual(self.client.post("/api/auth/login", json={"username":"alice","password":"wrong-pw"}).status_code, 401)
    def test_change_password_keeps_current_session(self):
        self._reg(); self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})
        r = self.client.post("/api/auth/change-password", json={"old_password":"pw-123456","new_password":"new-123456"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)  # current session still valid

    def test_duplicate_register_409(self):
        self._reg(); self.assertEqual(self._reg().status_code, 409)

    def test_disabled_user_login_403(self):
        from backend import accounts
        self._reg()
        uid = accounts.get_user_by_username("alice")["uid"]
        accounts.set_user_disabled(uid, True)
        self.assertEqual(self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"}).status_code, 403)

    def test_login_sets_secure_cookie_flags(self):
        self._reg()
        r = self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})
        sc = r.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", sc); self.assertIn("samesite=lax", sc); self.assertIn("max-age", sc)

    def test_login_validation_422(self):
        self._reg()
        self.assertEqual(self.client.post("/api/auth/login", json={"username":"alice","password":"x"}).status_code, 422)

    def test_logout_idempotent_without_session(self):
        # no cookie -> still 200, still clears
        r = self.client.post("/api/auth/logout")
        self.assertEqual(r.status_code, 200)

    def test_change_password_revokes_other_sessions(self):
        from backend import accounts
        self._reg()
        uid = accounts.get_user_by_username("alice")["uid"]
        other = accounts.create_session(uid)            # a second device's session
        self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})  # current session
        self.client.post("/api/auth/change-password", json={"old_password":"pw-123456","new_password":"new-123456"})
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)   # current kept
        self.assertIsNone(accounts.get_session_uid(other))                   # other revoked


class DesktopLocalTests(AuthApiTestBase):
    def setUp(self):
        super().setUp(); self.m.app.state.auth_required = False
    def test_me_returns_synthetic_local(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()["uid"], "local")


class BootstrapSafetyTests(AuthApiTestBase):
    def test_bootstrap_admin_from_env(self):
        import importlib
        with mock.patch.dict(os.environ, {"CRA_BOOTSTRAP_ADMIN_USERNAME": "root", "CRA_BOOTSTRAP_ADMIN_PASSWORD": "admin-pw"}):
            import backend.main as m; importlib.reload(m)
            from backend import accounts
            rec = accounts.get_user_by_username("root")
            self.assertIsNotNone(rec); self.assertTrue(rec["is_admin"]); self.assertTrue(rec["must_change_password"])
            # idempotent: a second reload doesn't create a duplicate / second admin
            importlib.reload(m)
            self.assertIsNotNone(accounts.get_user_by_username("root"))

    def test_env_invite_code_is_authoritative(self):
        from backend import accounts
        accounts.set_config("invite_code", "OLD-RANDOM")          # simulate a prior random seed
        import importlib
        with mock.patch.dict(os.environ, {"CRA_INVITE_CODE": "OPERATOR-CODE"}):
            import backend.main as m; importlib.reload(m)
            self.assertEqual(accounts.get_config("invite_code"), "OPERATOR-CODE")

    def test_assert_safe_startup(self):
        # auth off (desktop) must be loopback
        with self.assertRaises(SystemExit):
            self.m.assert_safe_startup(auth_required=False, host="0.0.0.0")
        self.m.assert_safe_startup(auth_required=False, host="127.0.0.1")  # loopback OK
        # auth on (web) on 0.0.0.0 is allowed (CRA_INVITE_CODE=JOIN is set by the base)
        self.m.assert_safe_startup(auth_required=True, host="0.0.0.0")

    def test_assert_safe_startup_web_requires_invite_code(self):
        # web mode (auth on) with NO CRA_INVITE_CODE must refuse to start
        # Use empty-string patch: assert_safe_startup treats empty as missing
        with mock.patch.dict(os.environ, {"CRA_INVITE_CODE": ""}):
            with self.assertRaises(SystemExit):
                self.m.assert_safe_startup(auth_required=True, host="0.0.0.0")


class MeCostFieldsTests(AuthApiTestBase):
    def test_me_includes_today_cost_and_cap(self):
        self.client.post("/api/auth/register",
                         json={"username": "bob", "password": "pw-123456", "invite_code": "JOIN"})
        self.client.post("/api/auth/login", json={"username": "bob", "password": "pw-123456"})
        import backend.accounts as accounts, backend.metering as metering
        uid = accounts.get_user_by_username("bob")["uid"]
        accounts.add_usage(uid, metering.today_shanghai(), 1_500_000, 0, 0, 0)  # ¥1.5
        body = self.client.get("/api/auth/me").json()
        self.assertAlmostEqual(body["today_cost_yuan"], 1.5, places=4)
        self.assertIn("daily_cap_yuan", body)
        self.assertGreater(body["daily_cap_yuan"], 0)
