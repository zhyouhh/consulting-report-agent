import os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock
from backend import tenant


class TenantPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def test_data_root_env(self):
        from backend.config import data_root
        self.assertEqual(Path(os.path.realpath(data_root())), self._tmp)

    def test_user_paths(self):
        self.assertEqual(tenant.user_projects_dir("u1"), self._tmp / "users" / "u1" / "projects")
        self.assertEqual(tenant.user_config_path("u1"), self._tmp / "users" / "u1" / "config.json")
        self.assertEqual(tenant.app_db_path(), self._tmp / "app.db")

    def test_search_paths_under_data_root(self):
        from backend.config import get_search_runtime_state_path, get_search_cache_path
        self.assertEqual(get_search_runtime_state_path(), self._tmp / "search_runtime_state.json")
        self.assertEqual(get_search_cache_path(), self._tmp / "search_cache.json")

    def test_composite_key_sanitizes(self):
        self.assertEqual(tenant.tenant_project_key("u1", "proj-a"), "u1::proj-a")
        self.assertNotEqual(tenant.tenant_project_key("a", "b::c"), tenant.tenant_project_key("a::b", "c"))

    def test_composite_key_no_collision(self):
        self.assertNotEqual(tenant.tenant_project_key("a:", "p"), tenant.tenant_project_key("a_", "p"))
        self.assertNotEqual(tenant.tenant_project_key("a", "b:c"), tenant.tenant_project_key("a", "b_c"))
        self.assertEqual(tenant.tenant_project_key("u1", "proj-a"), "u1::proj-a")  # 常规无特殊字符不变

    def test_path_helpers_reject_traversal(self):
        # 含 Windows 盘符限定/相对（":"）——Windows 优先仓库必须拒
        for bad in ("..", "../x", "a/b", "a\\b", "", ".", "Z:tmp", "u:", "a:b"):
            with self.assertRaises(ValueError):
                tenant.user_dir(bad)
        self.assertEqual(tenant.user_dir("local"), self._tmp / "users" / "local")  # 合法 uid 通过
        self.assertEqual(tenant.user_dir("a" * 32), self._tmp / "users" / ("a" * 32))  # uuid-hex 形态通过
