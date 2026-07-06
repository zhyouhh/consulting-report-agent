"""B3 admin API：只读端点 + 写端点。沿用 AuthApiTestBase 范式（起隔离 + bootstrap admin + 登录拿 cookie）。"""
from tests.test_auth_api import AuthApiTestBase
from backend import accounts


class AdminApiTestBase(AuthApiTestBase):
    """复用 AuthApiTestBase（reload main + mock heal + 单例 reset + 默认带同源 Origin）。
    再补 admin/regular 登录辅助：直接建用户（B1 机制）+ /api/auth/login 拿 cookie（TestClient 持久化 cookie）。"""

    def _login_as_admin(self, username="rootadmin", password="admin-123456"):
        accounts.create_user(username, password, is_admin=True)
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return username

    def _login_as_regular_user(self, username="plainuser", password="user-123456"):
        accounts.create_user(username, password)
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return username


class AdminReadEndpointTests(AdminApiTestBase):
    def test_get_users_requires_admin(self):
        # 用非 admin 用户登录 → 403
        self._login_as_regular_user()
        resp = self.client.get("/api/admin/users")
        self.assertEqual(resp.status_code, 403)

    def test_admin_lists_users_with_cost_fields(self):
        self._login_as_admin()
        resp = self.client.get("/api/admin/users")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertTrue(all("today_cost_yuan" in r and "daily_cap_yuan" in r for r in rows))

    def test_admin_get_invite_code(self):
        self._login_as_admin()
        resp = self.client.get("/api/admin/invite-code")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("invite_code", resp.json())

    def test_admin_get_allowed_hosts(self):
        self._login_as_admin()
        resp = self.client.get("/api/admin/allowed-hosts")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for k in ("builtin_hosts", "env_hosts", "extra_hosts"):
            self.assertIn(k, body)
        self.assertIn("api.openai.com", body["builtin_hosts"])


class AdminWriteEndpointTests(AdminApiTestBase):
    def test_admin_reset_user_password(self):
        self._login_as_admin()
        uid = accounts.create_user("dave", "old-123456")
        resp = self.client.post(f"/api/admin/users/{uid}/password",
                                headers={"origin": "https://app.example.com"},
                                json={"new_password": "fresh-123456"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(accounts.verify_user_password("dave", "fresh-123456"))

    def test_admin_set_cap_yuan(self):
        self._login_as_admin()
        uid = accounts.create_user("erin", "pw-123456")
        resp = self.client.post(f"/api/admin/users/{uid}/cap",
                                headers={"origin": "https://app.example.com"},
                                json={"daily_cost_yuan": "20"})   # 字符串入参
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 20_000_000)

    def test_admin_disable_user(self):
        self._login_as_admin()
        uid = accounts.create_user("frank", "pw-123456")
        token = accounts.create_session(uid)
        resp = self.client.post(f"/api/admin/users/{uid}/disabled",
                                headers={"origin": "https://app.example.com"},
                                json={"disabled": True})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(accounts.get_session_uid(token))   # 即时失效

    def test_admin_cannot_disable_last_admin(self):
        # 仅 bootstrap admin 一人时禁用自己 → 400
        self._login_as_admin()
        admin_uid = self.client.get("/api/auth/me").json()["uid"]
        resp = self.client.post(f"/api/admin/users/{admin_uid}/disabled",
                                headers={"origin": "https://app.example.com"},
                                json={"disabled": True})
        self.assertEqual(resp.status_code, 400)

    def test_admin_rotate_invite_code(self):
        self._login_as_admin()
        old = accounts.get_config("invite_code")
        resp = self.client.post("/api/admin/invite-code/rotate",
                                headers={"origin": "https://app.example.com"}, json={})
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.json()["invite_code"], old)

    def test_admin_set_allowed_hosts_refreshes_guard(self):
        from backend import url_guard
        self._login_as_admin()
        resp = self.client.post("/api/admin/allowed-hosts",
                                headers={"origin": "https://app.example.com"},
                                json={"hosts": ["my.llm.cn"]})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("my.llm.cn", url_guard.custom_api_allowed_hosts())  # 即时刷新生效

    def test_admin_set_allowed_hosts_rejects_malformed(self):
        self._login_as_admin()
        for bad in ("https://x.com/v1", "x.com:8443", "10.0.0.1", "*.evil.com", "has space"):
            resp = self.client.post("/api/admin/allowed-hosts",
                                    headers={"origin": "https://app.example.com"},
                                    json={"hosts": [bad]})
            self.assertEqual(resp.status_code, 400, bad)

    # —— BLOCKER 2（codex 红队）：cap 坏输入必须 400、绝不 500 ——
    def test_admin_set_cap_rejects_bad_inputs_with_400(self):
        self._login_as_admin()
        uid = accounts.create_user("capuser", "pw-123456")
        for bad in ("1e1000000", "1e308", "9" * 40, "-5", "NaN", "Infinity", "abc"):
            resp = self.client.post(f"/api/admin/users/{uid}/cap",
                                    headers={"origin": "https://app.example.com"},
                                    json={"daily_cost_yuan": bad})
            self.assertEqual(resp.status_code, 400, f"{bad!r} should be 400, got {resp.status_code}")

    def test_admin_set_cap_normal_value_succeeds(self):
        self._login_as_admin()
        uid = accounts.create_user("capuser2", "pw-123456")
        resp = self.client.post(f"/api/admin/users/{uid}/cap",
                                headers={"origin": "https://app.example.com"},
                                json={"daily_cost_yuan": "20"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 20_000_000)

    def test_admin_set_cap_null_clears_override(self):
        self._login_as_admin()
        uid = accounts.create_user("capuser3", "pw-123456")
        accounts.set_user_daily_cap_micro(uid, 9000)
        resp = self.client.post(f"/api/admin/users/{uid}/cap",
                                headers={"origin": "https://app.example.com"},
                                json={"daily_cost_yuan": None})
        self.assertEqual(resp.status_code, 200)
        from backend.config import DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN)

    # —— BLOCKER 1（codex 红队）：last-admin 禁用走 accounts 原子守卫 ——
    def test_disable_last_admin_via_endpoint_rejected_400(self):
        # 仅一个 admin（bootstrap 登录态）：禁用最后一个 admin（即自己）→ 400。
        self._login_as_admin()
        admin_uid = self.client.get("/api/auth/me").json()["uid"]
        resp = self.client.post(f"/api/admin/users/{admin_uid}/disabled",
                                headers={"origin": "https://app.example.com"},
                                json={"disabled": True})
        self.assertEqual(resp.status_code, 400)

    def test_disable_non_last_admin_succeeds(self):
        # 两个 admin：禁用其中一个（非当前登录者）成功，仍剩一个活跃 admin。
        self._login_as_admin()
        other_admin = accounts.create_user("admin2", "pw-123456", is_admin=True)
        resp = self.client.post(f"/api/admin/users/{other_admin}/disabled",
                                headers={"origin": "https://app.example.com"},
                                json={"disabled": True})
        self.assertEqual(resp.status_code, 200)
        active = [u for u in accounts.list_all_users() if u["is_admin"] and not u["disabled"]]
        self.assertGreaterEqual(len(active), 1)


class AdminUsageHistoryTests(AdminApiTestBase):
    """2026-07-06 /admin 独立页面：历史用量端点。"""

    def test_usage_requires_admin(self):
        self._login_as_regular_user()
        resp = self.client.get("/api/admin/usage")
        self.assertEqual(resp.status_code, 403)

    def test_usage_returns_rows_with_username_join(self):
        from backend import metering
        self._login_as_admin()
        uid = accounts.create_user("grace", "pw-123456")
        today = metering.today_shanghai()
        accounts.add_usage(uid, today, cost_micro_yuan=1_234_000, hit=1000, miss=200, output=300)

        resp = self.client.get("/api/admin/usage?days=7")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["today"], today)
        rows = [r for r in body["rows"] if r["uid"] == uid]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "grace")
        self.assertEqual(rows[0]["day"], today)
        self.assertAlmostEqual(rows[0]["cost_yuan"], 1.234)
        self.assertEqual(rows[0]["cache_hit_tokens"], 1000)
        self.assertEqual(rows[0]["cache_miss_tokens"], 200)
        self.assertEqual(rows[0]["output_tokens"], 300)

    def test_usage_days_clamped_and_old_rows_excluded(self):
        from backend import metering
        self._login_as_admin()
        uid = accounts.create_user("henry", "pw-123456")
        today = metering.today_shanghai()
        accounts.add_usage(uid, today, 1_000_000, 10, 10, 10)
        accounts.add_usage(uid, "2000-01-01", 9_000_000, 90, 90, 90)   # 远古行必须被窗口排除

        resp = self.client.get("/api/admin/usage?days=99999")   # 夹到 90
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        days = [r["day"] for r in body["rows"]]
        self.assertIn(today, days)
        self.assertNotIn("2000-01-01", days)

        resp_min = self.client.get("/api/admin/usage?days=-5")   # 夹到 1 → since=today
        self.assertEqual(resp_min.status_code, 200)
        self.assertEqual(resp_min.json()["since"], today)


class AdminSearchQuotaTests(AdminApiTestBase):
    """GET /api/admin/search-quota：admin 门禁 + 报告装配 + 缺配置优雅降级。"""

    def _pool_config(self):
        from backend.config import (
            ManagedSearchLimitsConfig,
            ManagedSearchPoolConfig,
            ManagedSearchProviderConfig,
            ManagedSearchQuotaConfig,
            ManagedSearchRoutingConfig,
        )
        return ManagedSearchPoolConfig(
            version=1,
            providers={
                "serper": ManagedSearchProviderConfig(
                    enabled=True, api_key="serper-key-0001",
                    api_keys=("serper-key-0001",),
                    weight=3, minute_limit=60, daily_soft_limit=1200, cooldown_seconds=180,
                    quota=ManagedSearchQuotaConfig(model="one_time", unit="credits", per_key_quota=2500),
                ),
            },
            routing=ManagedSearchRoutingConfig(primary=["serper"], secondary=[], native_fallback=True),
            limits=ManagedSearchLimitsConfig(
                per_turn_searches=5, project_minute_limit=30, global_minute_limit=60,
                memory_cache_ttl_seconds=21600, project_cache_ttl_seconds=86400,
            ),
        )

    def test_requires_admin(self):
        self._login_as_regular_user()
        resp = self.client.get("/api/admin/search-quota")
        self.assertEqual(resp.status_code, 403)

    def test_returns_report_with_estimated_remaining(self):
        from unittest import mock as _mock
        from backend import metering
        from backend.search_quota import key_fingerprint
        self._login_as_admin()
        accounts.add_search_usage(
            "serper", key_fingerprint("serper-key-0001"), metering.today_shanghai(),
            calls=10, units=12.0,
        )
        # load_managed_search_pool_config 是端点内局部 import，patch 目标是 backend.config
        with _mock.patch("backend.config.load_managed_search_pool_config",
                         return_value=self._pool_config()):
            resp = self.client.get("/api/admin/search-quota")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["configured"])
        serper = next(p for p in body["providers"] if p["name"] == "serper")
        self.assertEqual(serper["source"], "estimated")
        self.assertEqual(serper["total_quota"], 2500.0)
        self.assertEqual(serper["total_used"], 12.0)
        self.assertEqual(serper["total_remaining"], 2488.0)
        # 响应不得包含完整 api key
        self.assertNotIn("serper-key-0001", resp.text)

    def test_missing_pool_config_degrades_gracefully(self):
        from unittest import mock as _mock
        self._login_as_admin()
        with _mock.patch("backend.config.load_managed_search_pool_config",
                         side_effect=FileNotFoundError("no file")):
            resp = self.client.get("/api/admin/search-quota")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])
