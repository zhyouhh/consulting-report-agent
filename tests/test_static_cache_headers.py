"""SPA 静态资源缓存头回归测试（2026-06-23 调试）。

根因：StaticFiles 默认只发 ETag/Last-Modified、不发 Cache-Control → 浏览器对 index.html
做启发式缓存 → 重新部署后陈旧 shell 仍指向旧 hash bundle；旧 bundle 被原子 swap 删除后
返回 404 → React 脚本加载失败 → 空 #root → 满屏深色空白页、控制台静默 404、UI 无报错。

修复：SPA shell（index.html / text/html）必须 no-cache（每次校验，确保拿到最新 bundle 引用）；
带内容 hash 的 /assets/* 可 immutable 长缓存。
"""
import tempfile
import unittest
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from backend.main import _SPAStaticFiles


class StaticCacheHeaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
        (root / "assets").mkdir()
        (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
        app = Starlette()
        app.mount("/", _SPAStaticFiles(directory=str(root), html=True), name="static")
        self.client = TestClient(app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_index_html_served_no_cache(self):
        """根路径（SPA shell）必须 no-cache，否则陈旧 shell 指向已删 bundle 致空白页。"""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        cc = r.headers.get("cache-control", "")
        self.assertIn("no-cache", cc, f"index.html 应 no-cache，实际 Cache-Control={cc!r}")

    def test_explicit_index_html_no_cache(self):
        r = self.client.get("/index.html")
        self.assertIn("no-cache", r.headers.get("cache-control", ""))

    def test_hashed_asset_immutable(self):
        """带内容 hash 的资源可长缓存 immutable（hash 变即换 URL，永不陈旧）。"""
        r = self.client.get("/assets/index-abc123.js")
        self.assertEqual(r.status_code, 200)
        cc = r.headers.get("cache-control", "")
        self.assertIn("immutable", cc, f"hash 资源应 immutable，实际 Cache-Control={cc!r}")

    def test_index_304_revalidation_still_carries_no_cache(self):
        """codex BLOCKER：条件请求命中返回 304 时，仍必须带 no-cache，
        否则部署前缓存的旧 shell revalidate 得 304 却学不到 no-cache，迁移不彻底。"""
        r1 = self.client.get("/")
        etag = r1.headers.get("etag")
        self.assertTrue(etag, "index.html 应带 ETag 才能走条件请求")
        r2 = self.client.get("/", headers={"If-None-Match": etag})
        self.assertEqual(r2.status_code, 304, "ETag 命中应 304")
        self.assertIn("no-cache", r2.headers.get("cache-control", ""), "304 仍须带 no-cache")

    def test_asset_304_revalidation_still_immutable(self):
        r1 = self.client.get("/assets/index-abc123.js")
        etag = r1.headers.get("etag")
        self.assertTrue(etag)
        r2 = self.client.get("/assets/index-abc123.js", headers={"If-None-Match": etag})
        self.assertEqual(r2.status_code, 304)
        self.assertIn("immutable", r2.headers.get("cache-control", ""), "304 资源仍须 immutable")

    def test_cache_control_for_path_classification(self):
        """按规范化路径分类（覆盖 Windows 分隔符 + 仅根级 assets/、防子串误命中）。"""
        f = _SPAStaticFiles._cache_control_for
        for shell in (".", "", "index.html", "sub/index.html", "foo.html"):
            self.assertIn("no-cache", f(shell) or "", f"{shell!r} 应 no-cache")
        for asset in ("assets/x.js", "assets\\x.js"):  # 含 Windows 反斜杠
            self.assertIn("immutable", f(asset) or "", f"{asset!r} 应 immutable")
        # 非根级 assets 不应被误判成 immutable（防 "foo/assets/bar" 子串命中）
        self.assertIsNone(f("foo/assets/bar.js"))
        self.assertIsNone(f("favicon.ico"))


if __name__ == "__main__":
    unittest.main()


class SpaFallbackRouteTests(StaticCacheHeaderTests):
    """2026-07-06 /admin 独立页面：SPA 客户端路由白名单回退 index.html。"""

    def test_admin_route_falls_back_to_index_with_no_cache(self):
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("root", r.text)   # 服务的是 index.html shell
        self.assertIn("no-cache", r.headers.get("cache-control", ""))

    def test_unknown_route_still_404(self):
        # 白名单外的未知路径不回退——保持 404（陈旧 bundle 404 可见性前提）。
        r = self.client.get("/not-a-route")
        self.assertEqual(r.status_code, 404)

    def test_missing_asset_still_404(self):
        r = self.client.get("/assets/index-gone.js")
        self.assertEqual(r.status_code, 404)


# 置空继承的 test_（避免基类用例在子类重复跑）
for _inh in dir(StaticCacheHeaderTests):
    if _inh.startswith("test_") and _inh not in SpaFallbackRouteTests.__dict__:
        setattr(SpaFallbackRouteTests, _inh, None)
del _inh
