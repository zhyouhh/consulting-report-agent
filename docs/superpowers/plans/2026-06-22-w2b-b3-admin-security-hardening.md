# W2-B / B3 Admin 面板 + 安全硬化 + custom 模式激活 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给多租户 Web 基座补上后台管理面板、CSRF/CORS/SSRF 安全硬化、登录限流、`must_change_password` 强制，并真正激活 custom 模式（用户自带 OpenAI 兼容 API key/base）。

**Architecture:** 复用 B1/B2 已落地的 `get_current_uid`/`get_current_admin` 依赖、`accounts.py`（users/sessions/app_config/usage_daily + argon2）、`metering.wrap_client_for_billing`、前端 `quotaFormat.js` 与 source-guard 测试范式。新增一个叶子模块 `backend/url_guard.py`（SSRF 防护：**域名白名单=安全边界 + 请求时 public-IP 校验=第二道防线**；白名单未在连接层 pin IP、对「攻击者控制白名单内域名的 rebinding」仍有 TOCTOU，pinned-IP-with-SNI transport 作为后置增强——见 spec §8.3 R3-NIT3 退路）。custom 激活 = 解锁 `normalize_settings_payload` 的无条件 managed 强制（legacy 配置仍强制迁移），并让所有 LLM client 走 guarded http_client。

**Tech Stack:** FastAPI + Starlette middleware、slowapi（已有 per-IP limiter）、httpx（OpenAI SDK 底层传输）、argon2、SQLite；前端 React + Vite + `node:test`（无 jsdom，逻辑抽 `utils/` + 组件 source-guard）。

**Spec 真值源:** `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（§7 admin、§8 CSRF/SSRF/CORS、§13 B3 验收门）。本 plan 不重开 spec，复用总 spec。

**关键设计决策（2026-06-22 用户拍板）:**
- **custom 是主路径**，¥5/天 managed 配额只是试用引子 → custom 必须真激活 + SSRF 必做。
- **SSRF = admin 维护的域名白名单（= 安全边界）+ 请求时 public-IP 校验（第二道防线）**。白名单来源三层合并：内置默认（managed 上游 + openai/deepseek/moonshot/zhipu/qwen）∪ env `CRA_CUSTOM_API_ALLOWED_HOSTS`（bootstrap）∪ **app_config 运行时项（admin 面板可增删，无需重启）**。**诚实定性（Codex R1-BLOCKER1）**：白名单是「只允许 admin 批准的主机被连接」这一安全边界，**不声称通用防 DNS rebinding**——它防的是「用户把 base_url 指向私网/元数据」，靠的是「未批准主机连不上」而非连接层 pin IP。真正的 pinned-IP-with-SNI transport（彻底防 rebinding）作为后置增强（§8.3 R3-NIT3 明确允许白名单作为 B3 终态）。**这解决「别预设同事用主流 provider」**：同事要用冷门/自建服务，admin 在面板把域名加进白名单即可。
- **桌面 local 受 ¥5/天默认 cap，不在 admin 调控面内**（无用户记录/无 admin 面板），符合预期，B3 不为 local 做特殊处理。

**执行说明:** 6 个 Phase，每个 Phase 可独立 commit + Codex 双轨 review。Phase 1（SSRF + custom）必须先于一切 custom 暴露——先有护栏再开门。后端先于前端。

---

## File Structure

**新建:**
- `backend/url_guard.py` — SSRF 防护叶子模块（只依赖 httpx + stdlib `ipaddress`/`socket`/`os`/`urllib`，**绝不 import chat/skill/main/config**）。public-IP 校验 + 白名单 + guarded http client 工厂。
- `frontend/src/components/AdminPanel.jsx` — 后台管理面板组件。
- `frontend/src/components/ForcePasswordChange.jsx` — `must_change_password` 强制改密屏。
- `frontend/src/utils/adminApi.js` — admin 面板纯逻辑（行内编辑态/校验/请求体组装），无 jsdom 单测。
- `tests/test_url_guard.py`、`tests/test_admin_api.py`、`tests/test_csrf.py` — 后端回归。
- `frontend/tests/adminApi.test.mjs`、`frontend/tests/adminPanel.source.test.mjs`、`frontend/tests/forcePasswordChange.source.test.mjs` — 前端回归。

**修改:**
- `backend/accounts.py` — 加 `list_all_users`、`admin_reset_password`、`rotate_invite_code`。
- `backend/config.py` — 解锁 `normalize_settings_payload` 的 custom（344-345 行）。
- `backend/main.py` — admin 路由、CSRF 中间件、CORS 收紧、per-username 登录限流、`must_change_password` 依赖、settings 端点校验 custom base、models/list 走 guarded client、cookie_secure web 态。
- `backend/chat.py` — client 走 `url_guard.build_guarded_http_client`；`_ensure_public_ip` 委派 url_guard（DRY）。
- `backend/independent_review.py` — `_build_client` 走 guarded http_client。
- `frontend/src/components/Sidebar.jsx` — admin 入口（仅 `is_admin`）。
- `frontend/src/App.jsx` — `must_change_password` 强制屏挂载 + AdminPanel 挂载。
- `frontend/src/components/ChatPanel.jsx`、`IndependentReviewDrawer.jsx` — raw fetch 补 `credentials:'include'`（防御）。
- `run_web.py` — `app.state.cookie_secure = True`（web 态默认）。

---

# Phase 1 — SSRF 护栏 + custom 模式激活（后端）

> 先建护栏再开门：custom 任何暴露之前，url_guard 必须就位。

### Task 1: url_guard public-IP 校验（从 chat.py 抽出，单一真值源）

**Files:**
- Create: `backend/url_guard.py`
- Test: `tests/test_url_guard.py`

**背景:** `backend/chat.py:5771-5805` 已有 `_ensure_public_ip`（拦私网/loopback/link-local/CGNAT/metadata），但只服务 `fetch_url` 工具。B3 把这套 IP 判定抽成 url_guard 的纯函数单一真值源，chat.py 后续委派（Task 5b）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_url_guard.py
import unittest
from backend import url_guard

class PublicIpTests(unittest.TestCase):
    def test_private_and_loopback_and_metadata_rejected(self):
        for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                    "::1", "100.64.0.1", "0.0.0.0"):
            with self.assertRaises(url_guard.SsrfBlockedError):
                url_guard.assert_public_ip(bad)

    def test_public_ip_passes(self):
        url_guard.assert_public_ip("1.1.1.1")   # 不抛即通过
        url_guard.assert_public_ip("8.8.8.8")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_url_guard.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.url_guard`）

- [ ] **Step 3: 写最小实现**

```python
# backend/url_guard.py
"""SSRF 防护叶子模块：public-IP 校验 + custom API 白名单 + guarded http client。
绝不 import chat/skill/main/config——只依赖 httpx + stdlib。"""
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class SsrfBlockedError(ValueError):
    """目标地址未通过 SSRF 校验（协议/主机/IP 不合法）。"""


def assert_public_ip(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text)
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or (ip in _CGNAT)
        or getattr(ip, "is_site_local", False)
    ):
        raise SsrfBlockedError("不允许访问本地或内网地址。")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_url_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/url_guard.py tests/test_url_guard.py
git commit -m "feat(b3): url_guard public-IP validation (SSRF leaf module)"
```

---

### Task 2: 域名白名单 + custom base 校验 + guarded http client

**Files:**
- Modify: `backend/url_guard.py`
- Test: `tests/test_url_guard.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_url_guard.py （追加）
from unittest import mock

class AllowlistTests(unittest.TestCase):
    def test_default_allowlist_includes_managed_and_mainstream(self):
        hosts = url_guard.custom_api_allowed_hosts()
        self.assertIn("newapi.z0y0h.work", hosts)
        self.assertIn("api.openai.com", hosts)
        self.assertIn("api.deepseek.com", hosts)

    def test_env_extends_allowlist(self):
        with mock.patch.dict(os.environ, {"CRA_CUSTOM_API_ALLOWED_HOSTS": "my.llm.cn, other.host"}):
            hosts = url_guard.custom_api_allowed_hosts()
            self.assertIn("my.llm.cn", hosts)
            self.assertIn("other.host", hosts)

    def test_validate_rejects_non_https(self):
        with self.assertRaises(url_guard.SsrfBlockedError):
            url_guard.validate_custom_api_base("http://api.openai.com/v1")

    def test_validate_rejects_offlist_host(self):
        with self.assertRaises(url_guard.SsrfBlockedError):
            url_guard.validate_custom_api_base("https://evil.example.com/v1")

    def test_validate_allowlisted_host_resolving_private_rejected(self):
        # 白名单内但解析到私网（误配/投毒）→ public-IP 二道防线拦下
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("10.0.0.9", 0))]):
            with self.assertRaises(url_guard.SsrfBlockedError):
                url_guard.validate_custom_api_base("https://api.openai.com/v1")

    def test_validate_allowlisted_host_public_passes(self):
        with mock.patch("backend.url_guard.socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            self.assertEqual(
                url_guard.validate_custom_api_base("https://api.openai.com/v1 "),
                "https://api.openai.com/v1",
            )
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_url_guard.py::AllowlistTests -v`
Expected: FAIL（`custom_api_allowed_hosts` 未定义）

- [ ] **Step 3: 写实现**

```python
# backend/url_guard.py （追加）
# url_guard 保持叶子（只依赖 httpx + stdlib），绝不 import accounts。
# 运行时 admin 增删的白名单由 main.py 启动/编辑后经 set_runtime_allowed_hosts 注入。
_DEFAULT_ALLOWED_HOSTS = (
    "newapi.z0y0h.work",        # managed 上游（服务端常量，始终允许）
    "api.openai.com",
    "api.deepseek.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",         # 智谱
    "dashscope.aliyuncs.com",   # 通义千问
)
_RUNTIME_ALLOWED_HOSTS: set[str] = set()   # app_config 注入，admin 面板可增删


def set_runtime_allowed_hosts(hosts) -> None:
    """main.py 启动从 app_config 载入 + admin 编辑后刷新；归一化为小写集合。"""
    global _RUNTIME_ALLOWED_HOSTS
    _RUNTIME_ALLOWED_HOSTS = {h.strip().lower() for h in (hosts or []) if h and h.strip()}


import re
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def is_valid_hostname(host: str) -> bool:
    """纯主机名（无 scheme/port/path/逗号/空白/通配符）。供 admin 白名单输入校验。"""
    return bool(_HOSTNAME_RE.match((host or "").strip().lower()))


def builtin_allowed_hosts() -> set[str]:
    return {h.lower() for h in _DEFAULT_ALLOWED_HOSTS}


def env_allowed_hosts() -> set[str]:
    raw = (os.environ.get("CRA_CUSTOM_API_ALLOWED_HOSTS") or "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()} if raw else set()


def custom_api_allowed_hosts() -> set[str]:
    """白名单 = 内置默认 ∪ env(bootstrap) ∪ 运行时(app_config，admin 维护)。"""
    return builtin_allowed_hosts() | env_allowed_hosts() | _RUNTIME_ALLOWED_HOSTS


def assert_resolves_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"无法解析主机：{host}") from exc
    if not infos:
        raise SsrfBlockedError(f"无法解析主机：{host}")
    for info in infos:
        assert_public_ip(info[4][0])


def validate_custom_api_base(url: str) -> str:
    """校验用户自填 custom_api_base：https + 白名单主机 + 解析到公网。
    返回去空白后的 URL；任何不合法抛 SsrfBlockedError。"""
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise SsrfBlockedError("自定义 API 地址必须是 https。")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("自定义 API 地址缺少主机名。")
    if host not in custom_api_allowed_hosts():
        raise SsrfBlockedError(f"主机 {host} 不在允许列表，请联系管理员添加。")
    assert_resolves_public(host)
    return cleaned


class _GuardedHTTPTransport(httpx.HTTPTransport):
    """每次请求都重校验：https + 主机在白名单 + 解析到公网。
    安全边界 = 白名单（只有 admin 批准的主机能被连接）。public-IP 校验是第二道防线，
    拦「白名单主机被误配/投毒到私网」。**注意**：此 transport 未在连接层 pin IP，
    对「攻击者控制白名单内域名 + 解析后到连接前翻转到私网」的 DNS rebinding 仍有 TOCTOU；
    彻底防 rebinding 需 pinned-IP-with-SNI transport（后置增强，§8.3 R3-NIT3 允许白名单为 B3 终态）。"""

    def handle_request(self, request):
        host = (request.url.host or "").lower()
        if request.url.scheme != "https":
            raise SsrfBlockedError("阻止非 https 请求。")
        if host not in custom_api_allowed_hosts():
            raise SsrfBlockedError(f"阻止对 {host!r} 的请求（不在允许列表）。")
        assert_resolves_public(host)
        return super().handle_request(request)


def build_guarded_http_client(timeout) -> httpx.Client:
    """供 OpenAI SDK 用的受控 http client：白名单 transport + 忽略环境代理 + 不跟随重定向。"""
    return httpx.Client(
        timeout=timeout,
        trust_env=False,          # 忽略 HTTP(S)_PROXY，防经代理绕过
        follow_redirects=False,   # 不跟随重定向，防重定向到私网
        transport=_GuardedHTTPTransport(),
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_url_guard.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add backend/url_guard.py tests/test_url_guard.py
git commit -m "feat(b3): custom API allowlist + guarded http client (SSRF)"
```

---

### Task 3: managed_base_url 改服务端只读（堵 managed-mode SSRF 口）

**Files:**
- Modify: `backend/main.py`（`POST /api/settings`，约 302-325 行）
- Test: `tests/test_settings_api.py`

**背景（Codex R1-BLOCKER2 + R2-BLOCKER2 — 修正）:** 仅删端点赋值**不够**。`normalize_settings_payload` 用 `setdefault` 保留已存 `managed_base_url`（`config.py:327`），managed runtime alias 继续用它（`config.py:347-348`），且 import 期 `heal_stale_managed_model(settings)` 也读旧值——历史污染配置仍可能生效。**正确修法**：① `normalize_settings_payload` **强制** `managed_base_url = DEFAULT_MANAGED_BASE_URL`（不再 setdefault）；② `SettingsUpdate.managed_base_url` 现为必填（`main.py:292`），改为 `Optional` 且服务端忽略（否则前端 Task 18 去字段后 422）；③ 端点删 `s.managed_base_url = update.managed_base_url`（`main.py:307`）。
**⚠️ R2-BLOCKER2:** `SettingsUpdate` 还有**另一个必填** `managed_model`（`main.py:293`，保持必填——前端 managed 模型选择器一直发它）。故所有 `POST /api/settings` 测试 body **必须带 `managed_model`**，否则 422。本 plan 统一用 helper `_settings_body(**overrides)` 拼完整必填体（`mode` + `managed_model`，custom 时补 `custom_*`），所有 settings/CSRF POST 测试复用它。

- [ ] **Step 1: 写失败测试 + 加测试 helper**

```python
# tests/test_settings_api.py 顶部（模块级，供本文件 + CSRF/admin 测试复用思路）
def _settings_body(**overrides):
    body = {"mode": "managed", "managed_model": "deepseek-v4-pro"}
    body.update(overrides)
    return body

# —— 用例 ——
def test_normalize_forces_managed_base_url_constant(self):
    from backend.config import normalize_settings_payload, DEFAULT_MANAGED_BASE_URL
    out = normalize_settings_payload({"managed_base_url": "https://attacker.internal/v1"})
    self.assertEqual(out["managed_base_url"], DEFAULT_MANAGED_BASE_URL)
    self.assertEqual(out["api_base"], DEFAULT_MANAGED_BASE_URL)  # managed alias 也回常量

def test_post_settings_without_managed_base_url_ok(self):
    # 前端不再发 managed_base_url（Task 18），带必填 managed_model 应 200 而非 422
    resp = self.client.post("/api/settings",
                            headers={"origin": "https://app.example.com"},
                            json=_settings_body())   # 不含 managed_base_url
    self.assertEqual(resp.status_code, 200)

def test_post_settings_ignores_client_managed_base_url(self):
    from backend.config import DEFAULT_MANAGED_BASE_URL
    self.client.post("/api/settings",
                     headers={"origin": "https://app.example.com"},
                     json=_settings_body(managed_base_url="https://attacker.internal/v1"))
    s = self._load_uid_settings()  # 基类读取 helper；若无则读 users/<uid>/config.json
    self.assertEqual(s.managed_base_url, DEFAULT_MANAGED_BASE_URL)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_settings_api.py -k "managed_base_url or without_managed" -v`
Expected: FAIL

- [ ] **Step 3a: 改 config.py `normalize_settings_payload`（327 行）** — 把 `setdefault` 改成强制覆盖：

```python
    normalized["managed_base_url"] = DEFAULT_MANAGED_BASE_URL   # 服务端只读，覆盖任何历史/客户端值
```

（其余 `managed_*` setdefault 保持不变；只 `managed_base_url` 强制。）

- [ ] **Step 3b: 改 main.py `SettingsUpdate`（291 行）** — `managed_base_url` 改可选并忽略：

```python
class SettingsUpdate(BaseModel):
    ...
    managed_base_url: str | None = None   # 服务端只读：接收但忽略，永远用 DEFAULT_MANAGED_BASE_URL
```

`POST /api/settings` 处理体里删除 `s.managed_base_url = update.managed_base_url`（managed_base_url 完全由 normalize 决定）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_settings_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/main.py tests/test_settings_api.py
git commit -m "fix(b3): managed_base_url server read-only (normalize forces constant + ignore client)"
```

---

### Task 4: 解锁 custom 模式（normalize honor mode + 保存时校验 base）

**Files:**
- Modify: `backend/config.py`（`normalize_settings_payload`，342-345 行）
- Modify: `backend/main.py`（`POST /api/settings`）
- Test: `tests/test_settings_api.py`

**背景:** `config.py:344-345` 无条件 `mode="managed"`。`model_post_init`/normalize 的 custom 分支（352-354）本就备好，一解锁即生效。legacy 配置（`config_version < DESKTOP_CONFIG_VERSION`）仍强制 managed（迁移安全）。保存 custom 时在端点用 `url_guard.validate_custom_api_base` 即时校验、给用户即时反馈。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_settings_api.py （追加）
def test_custom_mode_honored_for_current_config(self):
    from backend.config import normalize_settings_payload, DESKTOP_CONFIG_VERSION
    out = normalize_settings_payload({
        "config_version": DESKTOP_CONFIG_VERSION,
        "mode": "custom",
        "custom_api_base": "https://api.openai.com/v1",
        "custom_api_key": "sk-xxx",
        "custom_model": "gpt-4o",
    })
    self.assertEqual(out["mode"], "custom")
    self.assertEqual(out["api_base"], "https://api.openai.com/v1")
    self.assertEqual(out["api_key"], "sk-xxx")
    self.assertEqual(out["model"], "gpt-4o")

def test_legacy_config_still_coerced_to_managed(self):
    from backend.config import normalize_settings_payload
    out = normalize_settings_payload({"config_version": 0, "mode": "custom"})
    self.assertEqual(out["mode"], "managed")  # legacy 迁移安全

def test_post_settings_custom_offlist_base_rejected(self):
    # 带必填 managed_model（_settings_body），custom 字段过 SSRF 白名单校验 → 400
    resp = self.client.post("/api/settings",
                            headers={"origin": "https://app.example.com"},
                            json=_settings_body(mode="custom",
                                                custom_api_base="https://evil.example.com/v1",
                                                custom_api_key="sk-x", custom_model="x"))
    self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_settings_api.py -k "custom_mode_honored or legacy_config or offlist" -v`
Expected: FAIL

- [ ] **Step 3: 改 config.py（342-345 行）**

```python
    # 桌面默认 managed；legacy 配置强制迁移到 managed；非 legacy 配置 honor 用户选择的 mode。
    if is_legacy_config:
        normalized["mode"] = "managed"
    else:
        requested = normalized.get("mode", "managed")
        normalized["mode"] = requested if requested in ("managed", "custom") else "managed"
```

- [ ] **Step 4: 改 main.py `POST /api/settings`** — 解析后若 mode==custom，校验 base：

```python
    from backend import url_guard
    if (update.mode or "") == "custom":
        try:
            url_guard.validate_custom_api_base(update.custom_api_base or "")
        except url_guard.SsrfBlockedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
```

放在写入 settings 之前（校验失败不落盘）。

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_settings_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/main.py tests/test_settings_api.py
git commit -m "feat(b3): activate custom mode (normalize honors mode + validate base on save)"
```

---

### Task 5: LLM client 走 guarded http_client（chat + review + models/list）

**Files:**
- Modify: `backend/chat.py`（约 410-421 行 client 构建；`_ensure_public_ip` 约 5771）
- Modify: `backend/independent_review.py`（`_build_client`，约 264-272）
- Modify: `backend/main.py`（`POST /api/models/list`，约 338-356）
- Test: `tests/test_chat_runtime.py`、`tests/test_models.py`

**背景:** 三处构建 OpenAI client 都用裸 `httpx.Client()`。B3 全部换成 `url_guard.build_guarded_http_client(timeout)`。managed 上游 `newapi.z0y0h.work` 在默认白名单内，故 managed/custom 统一走 guarded transport，无需分叉。`/api/models/list` 直收用户 `api_base`——额外用 `validate_custom_api_base` 卡。

- [ ] **Step 1: 写失败测试（models/list 拒私网 + guarded client 注入）**

```python
# tests/test_models.py （追加；沿用既有 setUp）
def test_models_list_rejects_offlist_base(self):
    resp = self.client.post("/api/models/list", json={
        "api_key": "sk-x", "api_base": "https://evil.example.com/v1",
    })
    self.assertEqual(resp.status_code, 400)
```

```python
# tests/test_chat_runtime.py （追加 source-guard，确认 client 走 guarded http_client）
def test_chat_client_uses_guarded_http_client(self):
    import inspect
    from backend import chat
    src = inspect.getsource(chat)
    self.assertIn("url_guard.build_guarded_http_client", src)
    self.assertNotRegex(src, r"http_client\s*=\s*httpx\.Client\(timeout=120")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_models.py -k offlist tests/test_chat_runtime.py -k guarded -v`
Expected: FAIL

- [ ] **Step 3a: 改 chat.py client 构建（约 410 行）**

```python
        from backend import url_guard
        http_client = url_guard.build_guarded_http_client(timeout=120.0)
        raw_client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            http_client=http_client,
        )
        self.client = metering.wrap_client_for_billing(raw_client, uid=self.uid, settings=settings)
```

- [ ] **Step 3b: 改 independent_review.py `_build_client`（约 264 行）**

```python
        from backend import url_guard
        http_client = url_guard.build_guarded_http_client(
            timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)
        )
        raw = OpenAI(api_key=self.settings.api_key, base_url=self.settings.api_base, http_client=http_client)
        return metering.wrap_client_for_billing(raw, uid=self.uid, settings=self.settings)
```

- [ ] **Step 3c: 改 main.py `/api/models/list`（约 338-356 行）**

**Codex R1-BLOCKER4:** 现 handler 整段 `try ... except Exception -> 500`（`main.py:338`）。校验产生的 `HTTPException(400)` 会被宽 except 吞成 500、Task 5 测试不成立。**两处必改**：① `validate_custom_api_base` 放在宽 try **之外**（先校验再进探测）；② 宽 except 前加 `except HTTPException: raise`；③ `http_client` 用 `finally: http_client.close()`。

```python
    from backend import url_guard
    # —— 校验在宽 try 之外，400 不被吞成 500 ——
    try:
        validated_base = url_guard.validate_custom_api_base(request.api_base or "")
    except url_guard.SsrfBlockedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    http_client = url_guard.build_guarded_http_client(timeout=30.0)
    try:
        client = OpenAI(api_key=request.api_key, base_url=validated_base, http_client=http_client)
        models = client.models.list()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型列表获取失败：{exc}")
    finally:
        http_client.close()
```

- [ ] **Step 3d: chat.py `_ensure_public_ip` 委派 url_guard（DRY）** — 把 `_ensure_public_ip`（5771-5805）体改为：

```python
    def _ensure_public_ip(self, ip):
        from backend import url_guard
        url_guard.assert_public_ip(str(ip))
```

（`fetch_url` 既有 hostname 黑名单逻辑保留；IP 判定单一真值源移到 url_guard。注意 `assert_public_ip` 抛 `SsrfBlockedError`(继承 `ValueError`)，与原 `ValueError` 兼容，调用方 catch 不变。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_models.py tests/test_chat_runtime.py -v`
Expected: PASS（含既有 DeepSeek/tool-call 回归——注入 http_client 不改 provider message/tool_choice/reasoning_content 序列化）

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py backend/independent_review.py backend/main.py tests/
git commit -m "feat(b3): route all LLM clients through guarded http client; models/list validates base"
```

---

# Phase 2 — CSRF + CORS + cookie + 登录限流

### Task 6: CSRF Origin/Referer 中间件

**Files:**
- Modify: `backend/main.py`（middleware 区，约 58 行 CORS 附近）
- Test: `tests/test_csrf.py`

**背景:** 当前只靠 `SameSite=Lax`，无 Origin 校验（探查确认）。B3 加中间件：对所有状态变更方法（POST/PUT/PATCH/DELETE）+ web 态（`auth_required`）校验 Origin（缺失则退 Referer）∈ 允许源；不匹配 403。桌面 loopback（`auth_required=False`）跳过。允许源 = env `CRA_ALLOWED_ORIGIN`（逗号分隔）∪ loopback。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_csrf.py （沿用 test_auth_api.py 的 AuthApiTestBase 隔离范式 + 设 auth_required=True）
import unittest
from unittest import mock
from tests.test_settings_api import _settings_body   # 复用必填体 helper（含 managed_model）

class CsrfTests(unittest.TestCase):
    # setUp: 起隔离 CRA_DATA_ROOT + CRA_INVITE_CODE + CRA_ALLOWED_ORIGIN=https://app.example.com，
    #        reload(main)，app.state.auth_required=True，TestClient(app)。注册+登录拿 cookie。
    def test_cross_site_origin_post_rejected(self):
        resp = self.client.post("/api/settings",
                                headers={"origin": "https://evil.example.com"},
                                json={"mode": "managed"})
        self.assertEqual(resp.status_code, 403)

    def test_same_site_origin_post_allowed(self):
        resp = self.client.post("/api/settings",
                                headers={"origin": "https://app.example.com"},
                                json={"mode": "managed"})
        self.assertNotEqual(resp.status_code, 403)

    def test_get_not_csrf_checked(self):
        resp = self.client.get("/api/auth/me", headers={"origin": "https://evil.example.com"})
        self.assertNotEqual(resp.status_code, 403)

    # test_missing_origin_and_referer_rejected_on_state_change 用全新 bare client 实现，见 Step 3b
    # （self.client 带默认 Origin，headers={} 不能删，必须 fresh TestClient）
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_csrf.py -v`
Expected: FAIL（无 Origin 校验，跨站 POST 现在会通过）

- [ ] **Step 3: 写实现（main.py）**

```python
import os
from urllib.parse import urlparse
from starlette.responses import JSONResponse

_LOOPBACK_ORIGINS = {
    "http://127.0.0.1:8080", "http://localhost:8080",
    "http://127.0.0.1:8888", "http://localhost:8888",
    "http://localhost:3000", "http://127.0.0.1:3000",  # vite dev
}

def allowed_origins() -> set[str]:
    origins = set(_LOOPBACK_ORIGINS)
    raw = (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip()
    if raw:
        origins |= {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}
    return origins

def _origin_from_referer(referer: str | None) -> str | None:
    if not referer:
        return None
    p = urlparse(referer)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return None

@app.middleware("http")
async def csrf_origin_guard(request, call_next):
    if (request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and getattr(request.app.state, "auth_required", True)):
        origin = request.headers.get("origin") or _origin_from_referer(request.headers.get("referer"))
        if not origin or origin.rstrip("/") not in allowed_origins():
            return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
    return await call_next(request)
```

**顺序/定义约束:** ① `allowed_origins()` 与 `_LOOPBACK_ORIGINS` 必须定义在 CORS middleware 注册之前（Task 7 的 `allow_origins=list(allowed_origins())` 在 import 期求值）。② 该中间件只检查 POST/PUT/PATCH/DELETE，不碰 OPTIONS preflight，与 CORS 顺序无强耦合；保持 CORSMiddleware 最后 add（最外层）即可。

- [ ] **Step 3b: 迁移既有测试夹具（Codex R1-BLOCKER5，否则全套 POST 测试变 403）**

现有大量 auth/settings/admin/project 测试直接 `.post(...)` 不带 Origin（如 `tests/test_auth_api.py:88`、`tests/test_settings_api.py:79`）。CSRF 中间件上线后这些会全 403。**统一修法**：在 web 态测试基类（`AuthApiTestBase` 及派生）给 TestClient 设默认 Origin header，一处覆盖：

```python
# tests/test_auth_api.py AuthApiTestBase.setUp 内，建 TestClient 后：
self.client = TestClient(m.app)
self.client.headers.update({"origin": "https://app.example.com"})  # 满足 CSRF 同源
# setUp 同时设 CRA_ALLOWED_ORIGIN=https://app.example.com（mock.patch.dict）
```

逐个核对继承 `AuthApiTestBase` 的测试类都吃到该默认头；非 web 态（`auth_required=False`）测试不受影响（中间件跳过）。

**R2-BLOCKER3:** httpx/TestClient 会**合并** client 默认 headers，`headers={}` **不会删除**默认 Origin。故「缺失 Origin → 403」用例不能靠 `headers={}` 覆盖，必须用一个**全新、不带默认 Origin 的 client**：

```python
def test_missing_origin_and_referer_rejected_on_state_change(self):
    from starlette.testclient import TestClient
    bare = TestClient(self.m.app)   # 不 update 默认 origin
    bare.cookies.update(self.client.cookies)   # 复用已登录会话
    resp = bare.post("/api/settings", json=_settings_body())
    self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 3c: 加 SSE 流式端点同源通过测试**

```python
# tests/test_csrf.py （追加）
def test_sse_chat_stream_same_origin_not_csrf_blocked(self):
    # /api/chat/stream 是 POST，同源 Origin 不应被 CSRF 403（可能因别的原因失败，但不是 403）
    resp = self.client.post("/api/chat/stream",
                            headers={"origin": "https://app.example.com"},
                            json={"project_id": "nonexistent", "message_text": "hi"})
    self.assertNotEqual(resp.status_code, 403)
```

- [ ] **Step 4: 运行确认通过（含全套未被打爆）**

Run: `.venv/bin/python -m pytest tests/test_csrf.py tests/test_auth_api.py tests/test_settings_api.py tests/test_main_api.py -v`
Expected: PASS（既有用例经默认 Origin 头继续绿）

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/
git commit -m "feat(b3): CSRF Origin/Referer guard + migrate test fixtures to send Origin"
```

---

### Task 7: CORS 收紧 + web 态 cookie_secure

**Files:**
- Modify: `backend/main.py`（CORS，约 58-64）
- Modify: `run_web.py`（约 14 行）
- Test: `tests/test_csrf.py`、`tests/test_auth_api.py`

**背景:** 现 `allow_origins=["*"]` + `allow_credentials=True`（浏览器层非法组合）。改为 `allow_origins=list(allowed_origins())`。`run_web.py` 没设 `cookie_secure`（默认 False）→ web 态 cookie 无 Secure；改默认 True（部署在 https 后）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_csrf.py （追加）
def test_cors_not_wildcard(self):
    import inspect
    from backend import main as m
    src = inspect.getsource(m)
    self.assertIn("allow_origins=list(allowed_origins())", src)
    self.assertNotRegex(src, r'allow_origins=\[\s*["\']\*["\']\s*\]')
```

```python
# tests/test_auth_api.py （追加；web 态）
def test_web_cookie_has_secure_flag(self):
    # R2-BLOCKER4: AuthApiTestBase 只设 auth_required，run_web 不参与 TestClient，
    # 故测试里显式置 cookie_secure（login set-cookie 读 app.state.cookie_secure）
    self.m.app.state.cookie_secure = True
    self.client.post("/api/auth/register", headers={"origin": "https://app.example.com"},
                     json={"username": "u", "password": "pw-123456", "invite_code": "JOIN"})
    resp = self.client.post("/api/auth/login", headers={"origin": "https://app.example.com"},
                            json={"username": "u", "password": "pw-123456"})
    self.assertIn("Secure", resp.headers.get("set-cookie", ""))
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_csrf.py -k cors tests/test_auth_api.py -k secure -v`
Expected: FAIL

- [ ] **Step 3: 改 main.py CORS**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
```

- [ ] **Step 4: 改 run_web.py（约 14 行后）**

```python
    app.state.auth_required = True
    app.state.cookie_secure = True   # web 默认部署在 https 之后；本地 http 调试可设 CRA_COOKIE_INSECURE
```

兼容本地 http 调试：

```python
    if (os.environ.get("CRA_COOKIE_INSECURE") or "").strip():
        app.state.cookie_secure = False
```

**NIT（Codex R1）:** web 态若没配 `CRA_ALLOWED_ORIGIN`，`allowed_origins()` 只剩 loopback，远程访问的 POST 会全 403。`run_web.py` 启动时若 `auth_required and not CRA_ALLOWED_ORIGIN`，打一条**显著告警日志**（不强制退出——本地 loopback 调试合法）：

```python
    if not (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip():
        print("⚠️ 未设 CRA_ALLOWED_ORIGIN：仅 loopback 来源的写请求会被 CSRF 放行；远程部署必须设为你的站点 origin（如 https://app.example.com）")
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_csrf.py tests/test_auth_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/main.py run_web.py tests/
git commit -m "feat(b3): tighten CORS to allowlist + Secure cookie in web mode"
```

---

### Task 8: per-username 登录限流

**Files:**
- Modify: `backend/main.py`（`POST /api/auth/login`，约 222 行）
- Test: `tests/test_auth_api.py`

**背景:** 现仅 per-IP `10/minute`（slowapi）。撞库可轮换 IP 绕过。加 per-username 进程内滑窗：同一 username 5 分钟内失败 ≥10 次 → 429。`time.monotonic()` 计时。成功登录清该 username 计数。

- [ ] **Step 1: 写失败测试**

**R3-BLOCKER:** 端点已有 per-IP `@limiter.limit("10/minute")`（`main.py:222`）。同一 TestClient 连打 11 次会先吃 per-IP 429，**username throttle 没实现也会 429** → 假通过。两道隔离：① 直接测纯函数（与 slowapi 无关）；② 端点测试**关掉 slowapi**（`m.limiter.enabled = False`），让唯一 429 来源是 username throttle。

```python
# tests/test_auth_api.py （追加）
def test_login_throttle_pure_logic_and_casefold(self):
    from backend import main as m
    for _ in range(m._LOGIN_MAX_FAILS):
        m._record_login_fail("victim")
    self.assertTrue(m._login_throttled("victim"))
    self.assertTrue(m._login_throttled("  Victim  "))   # trim+casefold 共享计数
    m._clear_login_fails("VICTIM")
    self.assertFalse(m._login_throttled("victim"))

def test_login_per_username_throttle_endpoint(self):
    from backend import main as m
    m.limiter.enabled = False   # 关 per-IP slowapi，隔离出 username 维度
    try:
        self.client.post("/api/auth/register", headers={"origin": "https://app.example.com"},
                         json={"username": "victim", "password": "right-123456", "invite_code": "JOIN"})
        for _ in range(m._LOGIN_MAX_FAILS):
            self.client.post("/api/auth/login", headers={"origin": "https://app.example.com"},
                             json={"username": "victim", "password": "wrong-xxxxx"})
        resp = self.client.post("/api/auth/login", headers={"origin": "https://app.example.com"},
                                json={"username": "victim", "password": "wrong-xxxxx"})
        self.assertEqual(resp.status_code, 429)
    finally:
        m.limiter.enabled = True
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py -k "throttle" -v`
Expected: FAIL（纯函数 `_login_throttled` 未定义；端点 11 次仍 401）

- [ ] **Step 3: 写实现（main.py）**

```python
import threading
import time as _time

_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_FAILS_LOCK = threading.Lock()
_LOGIN_WINDOW_SEC = 300.0
_LOGIN_MAX_FAILS = 10

def _norm_login_key(username: str) -> str:
    return (username or "").strip().casefold()   # NIT: trim + casefold，键归一

def _prune_login_fails(now: float) -> None:
    # NIT: 顺带清空过期键，防进程内存随用户名爆涨（持锁内调用）
    for k in list(_LOGIN_FAILS):
        kept = [t for t in _LOGIN_FAILS[k] if now - t < _LOGIN_WINDOW_SEC]
        if kept:
            _LOGIN_FAILS[k] = kept
        else:
            del _LOGIN_FAILS[k]

def _login_throttled(username: str) -> bool:
    key, now = _norm_login_key(username), _time.monotonic()
    with _LOGIN_FAILS_LOCK:
        _prune_login_fails(now)
        return len(_LOGIN_FAILS.get(key, [])) >= _LOGIN_MAX_FAILS

def _record_login_fail(username: str) -> None:
    key, now = _norm_login_key(username), _time.monotonic()
    with _LOGIN_FAILS_LOCK:
        _prune_login_fails(now)
        _LOGIN_FAILS.setdefault(key, []).append(now)

def _clear_login_fails(username: str) -> None:
    with _LOGIN_FAILS_LOCK:
        _LOGIN_FAILS.pop(_norm_login_key(username), None)
```

在 login 处理体最前面（验证密码前）：

```python
    if _login_throttled(payload.username):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后再试。")
```

密码错误分支调 `_record_login_fail(payload.username)`；成功分支调 `_clear_login_fails(payload.username)`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(b3): per-username login throttle (anti credential-stuffing)"
```

---

# Phase 3 — accounts admin 函数 + admin API

### Task 9: accounts.list_all_users

**Files:**
- Modify: `backend/accounts.py`
- Test: `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_accounts.py （追加；沿用隔离 setUp）
def test_list_all_users_returns_rows_without_password_hash(self):
    accounts.create_user("alice", "pw-123456")
    accounts.create_user("bob", "pw-123456")
    rows = accounts.list_all_users()
    self.assertEqual({r["username"] for r in rows}, {"alice", "bob"})
    self.assertNotIn("password_hash", rows[0])
    for k in ("uid", "username", "is_admin", "disabled", "created_at"):
        self.assertIn(k, rows[0])
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -k list_all_users -v`
Expected: FAIL

- [ ] **Step 3: 写实现（accounts.py）**

```python
def list_all_users() -> list[dict]:
    with _db() as con:
        rows = con.execute(
            "SELECT uid, username, is_admin, daily_cost_micro_yuan, "
            "must_change_password, disabled, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]
```

（注意：不 SELECT `password_hash`——遵循 B1「公共查询剥 hash」铁律。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(b3): accounts.list_all_users (no password_hash)"
```

---

### Task 10: accounts.admin_reset_password

**Files:**
- Modify: `backend/accounts.py`
- Test: `tests/test_accounts.py`

**背景:** B1 有 `set_user_password`（用户自改，清 `must_change_password`）。admin 强制改他人密码语义不同：**改密 + 撤销该用户全部会话 + 置 `must_change_password=1`**（强制对方下次登录改密）。一个事务内做。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_accounts.py （追加）
def test_admin_reset_password_sets_flag_and_revokes_sessions(self):
    uid = accounts.create_user("carol", "old-123456")
    token = accounts.create_session(uid)
    accounts.admin_reset_password(uid, "new-123456")
    self.assertTrue(accounts.verify_user_password("carol", "new-123456"))
    self.assertIsNone(accounts.get_session_uid(token))           # 会话被撤
    self.assertTrue(accounts.get_user_by_uid(uid)["must_change_password"])
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -k admin_reset -v`
Expected: FAIL

- [ ] **Step 3: 写实现（accounts.py）**

```python
def admin_reset_password(uid: str, new_password: str) -> None:
    """管理员重置他人密码：改 hash + 置 must_change_password=1 + 撤销全部会话（同一事务）。"""
    new_hash = _PH.hash(new_password)
    with _db() as con:
        con.execute(
            "UPDATE users SET password_hash=?, must_change_password=1 WHERE uid=?",
            (new_hash, uid),
        )
        con.execute("DELETE FROM sessions WHERE uid=?", (uid,))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(b3): accounts.admin_reset_password (reset + force-change + revoke sessions)"
```

---

### Task 11: accounts.rotate_invite_code + custom API 允许域名存取（app_config）

**Files:**
- Modify: `backend/accounts.py`
- Test: `tests/test_accounts.py`

**背景:** 除轮换邀请码外，BLOCKER1 的「admin 可维护白名单」需要 app_config 持久化允许域名（admin 增删、无需重启）。两组都基于 `get_config`/`set_config`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_accounts.py （追加）
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -k "rotate_invite or extra_hosts" -v`
Expected: FAIL

- [ ] **Step 3: 写实现（accounts.py）**

```python
import secrets

def rotate_invite_code() -> str:
    code = secrets.token_urlsafe(9)   # ~12 字符
    set_config("invite_code", code)
    return code

def get_custom_api_extra_hosts() -> list[str]:
    raw = get_config("custom_api_allowed_hosts") or ""
    return [h.strip().lower() for h in raw.split(",") if h.strip()]

def set_custom_api_extra_hosts(hosts) -> None:
    cleaned = sorted({h.strip().lower() for h in (hosts or []) if h and h.strip()})
    set_config("custom_api_allowed_hosts", ",".join(cleaned))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(b3): accounts.rotate_invite_code + custom API allowed-hosts store"
```

---

### Task 12: admin 只读端点（GET users + GET invite-code）

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_admin_api.py`

**背景:** 全部 `Depends(get_current_admin)`（B1 已有，403 非 admin）。返回每用户今日花费/有效 cap（复用 `metering.today_shanghai` + `get_usage_today` + `get_effective_daily_cap_micro`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_admin_api.py （沿用 AuthApiTestBase 范式：起隔离 + bootstrap admin + 登录拿 cookie）
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py -k "get_users or invite_code or allowed_hosts" -v`
Expected: FAIL

- [ ] **Step 3: 写实现（main.py）**

```python
@app.get("/api/admin/users")
def admin_list_users(admin_uid: str = Depends(get_current_admin)):
    from backend import metering   # 模块限定（B2 铁律，避 reload 异常身份失配）；若 main 顶层已 import 则复用
    day = metering.today_shanghai()
    out = []
    for u in accounts.list_all_users():
        used = accounts.get_usage_today(u["uid"], day)["cost_micro_yuan"]
        cap = accounts.get_effective_daily_cap_micro(u["uid"])
        out.append({
            "uid": u["uid"], "username": u["username"],
            "is_admin": bool(u["is_admin"]), "disabled": bool(u["disabled"]),
            "created_at": u["created_at"],
            "today_cost_yuan": round(used / 1_000_000, 4),
            "daily_cap_yuan": round(cap / 1_000_000, 4),
        })
    return out

@app.get("/api/admin/invite-code")
def admin_get_invite_code(admin_uid: str = Depends(get_current_admin)):
    return {"invite_code": accounts.get_config("invite_code") or ""}

@app.get("/api/admin/allowed-hosts")
def admin_get_allowed_hosts(admin_uid: str = Depends(get_current_admin)):
    from backend import url_guard
    return {
        "builtin_hosts": sorted(url_guard.builtin_allowed_hosts()),   # 内置默认（只读）
        "env_hosts": sorted(url_guard.env_allowed_hosts()),           # env 注入（只读）
        "extra_hosts": accounts.get_custom_api_extra_hosts(),         # app_config（admin 可编辑）
    }
```

- [ ] **Step 3b: 启动时把 app_config 白名单注入 url_guard（main.py 模块加载/init_db 之后）**

```python
# main.py，init_db() 之后、app 建好后：
from backend import url_guard
url_guard.set_runtime_allowed_hosts(
    [h for h in accounts.get_custom_api_extra_hosts() if url_guard.is_valid_hostname(h)]
)  # NIT R3: 启动加载顺手过滤历史非法项（accounts 层只存不校验）
```

（确保进程起来后 admin 之前存的允许域名即生效；admin 改动时端点会再调一次刷新——见 Task 13。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_admin_api.py
git commit -m "feat(b3): admin read endpoints (users + invite-code + allowed-hosts) + startup load"
```

---

### Task 13: admin 写端点（改密 / 调 cap / 禁用 / 轮换邀请码）

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_admin_api.py`

**背景:** 全部 POST + `Depends(get_current_admin)`（自动经 CSRF 中间件）。`cap` 接收 `daily_cost_yuan|null`（元，内部转微元）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_admin_api.py （追加）
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py -k "reset_user or set_cap or disable_user or rotate_invite or allowed_hosts" -v`
Expected: FAIL

- [ ] **Step 3: 写实现（main.py）** — 先定义请求体模型，再写端点：

```python
from decimal import Decimal, InvalidOperation

class AdminPasswordBody(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)

class AdminCapBody(BaseModel):
    daily_cost_yuan: str | None = None   # NIT: 字符串入参，Decimal 解析免 float 漂移；null=回退全局

class AdminDisabledBody(BaseModel):
    disabled: bool

@app.post("/api/admin/users/{uid}/password")
def admin_set_password(uid: str, body: AdminPasswordBody, admin_uid: str = Depends(get_current_admin)):
    if accounts.get_user_by_uid(uid) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    accounts.admin_reset_password(uid, body.new_password)
    return {"status": "ok"}

@app.post("/api/admin/users/{uid}/cap")
def admin_set_cap(uid: str, body: AdminCapBody, admin_uid: str = Depends(get_current_admin)):
    if accounts.get_user_by_uid(uid) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.daily_cost_yuan is None or body.daily_cost_yuan.strip() == "":
        micro = None
    else:
        try:
            yuan = Decimal(body.daily_cost_yuan.strip())
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="额度格式非法")
        if not yuan.is_finite():   # 拒 NaN/Infinity（Decimal 不当解析错误）
            raise HTTPException(status_code=400, detail="额度格式非法")
        if yuan < 0:
            raise HTTPException(status_code=400, detail="额度不能为负")
        micro = int(yuan * 1_000_000)
    accounts.set_user_daily_cap_micro(uid, micro)
    return {"status": "ok"}

@app.post("/api/admin/users/{uid}/disabled")
def admin_set_disabled(uid: str, body: AdminDisabledBody, admin_uid: str = Depends(get_current_admin)):
    target = accounts.get_user_by_uid(uid)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # NIT: 防锁死——不许禁用自己；不许禁用最后一个在用 admin
    if body.disabled and uid == admin_uid:
        raise HTTPException(status_code=400, detail="不能禁用当前登录的管理员")
    if body.disabled and target.get("is_admin"):
        active_admins = [u for u in accounts.list_all_users() if u["is_admin"] and not u["disabled"]]
        if len(active_admins) <= 1:
            raise HTTPException(status_code=400, detail="不能禁用最后一个管理员")
    accounts.set_user_disabled(uid, body.disabled)
    return {"status": "ok"}

@app.post("/api/admin/invite-code/rotate")
def admin_rotate_invite(admin_uid: str = Depends(get_current_admin)):
    return {"invite_code": accounts.rotate_invite_code()}

class AllowedHostsBody(BaseModel):
    hosts: list[str] = Field(default_factory=list)

@app.post("/api/admin/allowed-hosts")
def admin_set_allowed_hosts(body: AllowedHostsBody, admin_uid: str = Depends(get_current_admin)):
    from backend import url_guard
    cleaned = [h.strip().lower() for h in body.hosts if h and h.strip()]
    bad = [h for h in cleaned if not url_guard.is_valid_hostname(h)]   # NIT: 拒 scheme/port/path/通配符/空白
    if bad:
        raise HTTPException(status_code=400, detail=f"非法域名：{', '.join(bad)}（只填主机名，如 my.llm.cn）")
    accounts.set_custom_api_extra_hosts(cleaned)
    url_guard.set_runtime_allowed_hosts(accounts.get_custom_api_extra_hosts())  # 即时刷新，无需重启
    return {"extra_hosts": accounts.get_custom_api_extra_hosts()}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_admin_api.py
git commit -m "feat(b3): admin write endpoints (password/cap/disabled/invite rotate)"
```

---

# Phase 4 — must_change_password 路由级强制

### Task 14: require_password_current 依赖

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_auth_api.py`

**背景:** B2 已让 `/api/auth/me` 返回 `must_change_password`。B3 加后端依赖：标志为真时，对业务路由（项目/聊天/settings/admin）403，前端据此弹强制改密屏。豁免：`/api/auth/me`、`/api/auth/change-password`、`/api/auth/logout`（否则死锁）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_auth_api.py （追加）
def test_must_change_password_blocks_business_routes(self):
    uid = accounts.create_user("grace", "pw-123456", must_change_password=True)
    token = accounts.create_session(uid)
    self.client.cookies.set("cra_session", token)
    org = {"origin": "https://app.example.com"}
    # path-param 项目路由被拦
    self.assertEqual(self.client.get("/api/projects").status_code, 403)
    # body-project 路由也被拦（BLOCKER3：显式串依赖才覆盖）
    self.assertEqual(self.client.post("/api/models/list", headers=org,
                                      json={"api_key": "x", "api_base": "https://api.openai.com/v1"}).status_code, 403)
    self.assertEqual(self.client.post("/api/chat/stream", headers=org,
                                      json={"project_id": "x", "message_text": "hi"}).status_code, 403)
    self.assertEqual(self.client.post("/api/chat", headers=org,
                                      json={"project_id": "x", "message_text": "hi"}).status_code, 403)
    # me / change-password 仍可用（否则无法自救）
    self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

def test_after_change_password_business_routes_unblocked(self):
    uid = accounts.create_user("heidi", "pw-123456", must_change_password=True)
    token = accounts.create_session(uid)
    self.client.cookies.set("cra_session", token)
    self.client.post("/api/auth/change-password",
                     headers={"origin": "https://app.example.com"},
                     json={"old_password": "pw-123456", "new_password": "new-123456"})
    self.assertNotEqual(self.client.get("/api/projects").status_code, 403)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py -k must_change -v`
Expected: FAIL

- [ ] **Step 3: 写实现（main.py）**

```python
def require_password_current(uid: str = Depends(get_current_uid)) -> str:
    if not getattr(app.state, "auth_required", True):
        return uid   # 桌面 local 无此约束
    user = accounts.get_user_by_uid(uid)
    if user and user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="must_change_password")
    return uid
```

**Codex R1-BLOCKER3 — 覆盖必须显式逐路由，不能只改 require_project**：`require_project` 是依赖注入（path-param `{project_id}` 端点经它），但 `/api/chat`(`main.py:473`)、`/api/chat/stream`(`main.py:926`)、`/api/models/list`(`main.py:339`) 是 **body 带 project_id**、先 `uid=Depends(get_current_uid)` 再**手动函数调用** `require_project(chat_request.project_id, uid)`——改 require_project 的默认依赖**覆盖不到**它们。两层都要改：

1. `require_project` 默认参数：`uid: str = Depends(require_password_current)`（覆盖所有 path-param `{project_id}` 端点）。
2. `get_current_admin` 的入参：`uid: str = Depends(require_password_current)`（覆盖所有 `/api/admin/*`）。
3. **逐个显式**把这些 `uid=Depends(get_current_uid)` 改成 `uid=Depends(require_password_current)`：`/api/chat`、`/api/chat/stream`、`/api/models/list`、`/api/settings`、`/api/projects`(GET 列表)、`/api/projects`(POST 创建)。

依赖链 `require_project → require_password_current → get_current_uid`、`get_current_admin → require_password_current → get_current_uid` 均无环。豁免集（不串 require_password_current）：`/api/auth/me`、`/api/auth/change-password`、`/api/auth/logout`、`/api/health`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py tests/test_admin_api.py tests/test_tenant_isolation.py -v`
Expected: PASS（确认跨租户隔离回归未被破坏）

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(b3): route-level must_change_password enforcement"
```

---

# Phase 5 — 前端

### Task 15: adminApi 纯逻辑（utils + 单测）

**Files:**
- Create: `frontend/src/utils/adminApi.js`
- Test: `frontend/tests/adminApi.test.mjs`

**背景:** 无 jsdom，AdminPanel 的行内编辑/校验/请求体组装抽成纯函数测。

- [ ] **Step 1: 写失败测试**

```javascript
// frontend/tests/adminApi.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { capPayload, validateNewPassword, summarizeUser } from '../src/utils/adminApi.js'

test('capPayload: 空字符串 → null（回退全局/默认）；非空 → 字符串（后端 Decimal 解析）', () => {
  assert.deepEqual(capPayload(''), { daily_cost_yuan: null })
  assert.deepEqual(capPayload('20'), { daily_cost_yuan: '20' })   // 字符串，匹配后端 AdminCapBody: str|None
})

test('capPayload: 非法输入抛错', () => {
  assert.throws(() => capPayload('abc'))
  assert.throws(() => capPayload('-5'))
})

test('validateNewPassword: 长度下限 8', () => {
  assert.equal(validateNewPassword('1234567'), false)
  assert.equal(validateNewPassword('12345678'), true)
})

test('summarizeUser: 额度比例 [0,1]', () => {
  const s = summarizeUser({ today_cost_yuan: 2.5, daily_cap_yuan: 5 })
  assert.equal(s.ratio, 0.5)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && node --test tests/adminApi.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写实现**

```javascript
// frontend/src/utils/adminApi.js
import { quotaRatio } from './quotaFormat.js'

export function capPayload(input) {
  const raw = (input ?? '').toString().trim()
  if (raw === '') return { daily_cost_yuan: null }
  const n = Number(raw)
  if (!Number.isFinite(n) || n < 0) throw new Error('额度必须是非负数字')
  return { daily_cost_yuan: raw }   // 字符串送后端（AdminCapBody: str|None → Decimal 解析，免 float 漂移）
}

export function validateNewPassword(pw) {
  return typeof pw === 'string' && pw.length >= 8
}

export function summarizeUser(u) {
  return {
    ...u,
    ratio: quotaRatio(u?.today_cost_yuan ?? 0, u?.daily_cap_yuan ?? 0),
  }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && node --test tests/adminApi.test.mjs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/adminApi.js frontend/tests/adminApi.test.mjs
git commit -m "feat(b3): adminApi pure logic + tests"
```

---

### Task 16: AdminPanel 组件 + Sidebar 入口（source-guard）

**Files:**
- Create: `frontend/src/components/AdminPanel.jsx`
- Modify: `frontend/src/components/Sidebar.jsx`（账号块，74-104）
- Modify: `frontend/src/App.jsx`（挂载 AdminPanel）
- Test: `frontend/tests/adminPanel.source.test.mjs`、`frontend/tests/sidebarQuota.source.test.mjs`（扩断言）

**背景:** AdminPanel 作 state 驱动弹窗（无 react-router），类似 SettingsModal。Sidebar 账号块仅 `is_admin` 显「👤 用户管理」入口。复用 `quotaFormat` + `adminApi`。axios 自带 `withCredentials`（api.js 已配）。

- [ ] **Step 1: 写失败测试（source-guard）**

```javascript
// frontend/tests/adminPanel.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/AdminPanel.jsx', import.meta.url), 'utf8')

test('AdminPanel 调 admin 端点 + 复用 adminApi', () => {
  assert.match(src, /\/api\/admin\/users/)
  assert.match(src, /\/api\/admin\/users\/\$\{[^}]+\}\/(password|cap|disabled)/)
  assert.match(src, /\/api\/admin\/invite-code\/rotate/)
  assert.match(src, /\/api\/admin\/allowed-hosts/)
  assert.match(src, /from '\.\.\/utils\/adminApi'/)
})
```

```javascript
// frontend/tests/sidebarQuota.source.test.mjs （追加断言）
test('Sidebar 账号块在 is_admin 时露用户管理入口', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.match(s, /authUser\??\.is_admin/)
  assert.match(s, /用户管理/)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && node --test tests/adminPanel.source.test.mjs tests/sidebarQuota.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 写 AdminPanel.jsx**

```jsx
// frontend/src/components/AdminPanel.jsx
import { useEffect, useState } from 'react'
import axios from 'axios'
import { formatYuan } from '../utils/quotaFormat'
import { capPayload, validateNewPassword } from '../utils/adminApi'

export default function AdminPanel({ onClose }) {
  const [users, setUsers] = useState([])
  const [invite, setInvite] = useState('')
  const [hosts, setHosts] = useState('')          // 允许域名（每行一个，含默认只读 + extra 可编辑）
  const [defaultHosts, setDefaultHosts] = useState([])
  const [err, setErr] = useState('')

  async function reload() {
    try {
      const [u, c, h] = await Promise.all([
        axios.get('/api/admin/users'),
        axios.get('/api/admin/invite-code'),
        axios.get('/api/admin/allowed-hosts'),
      ])
      setUsers(u.data); setInvite(c.data.invite_code)
      setDefaultHosts([...(h.data.builtin_hosts || []), ...(h.data.env_hosts || [])])
      setHosts((h.data.extra_hosts || []).join('\n'))
    } catch (e) { setErr('加载失败') }
  }
  useEffect(() => { reload() }, [])

  async function saveHosts() {
    const list = hosts.split('\n').map((s) => s.trim()).filter(Boolean)
    try { await axios.post('/api/admin/allowed-hosts', { hosts: list }); reload() }
    catch (e) { setErr('保存允许域名失败') }
  }

  async function setCap(uid, input) {
    try { await axios.post(`/api/admin/users/${uid}/cap`, capPayload(input)); reload() }
    catch (e) { setErr(e?.message || '调整额度失败') }
  }
  async function resetPassword(uid, pw) {
    if (!validateNewPassword(pw)) { setErr('新密码至少 8 位'); return }
    try { await axios.post(`/api/admin/users/${uid}/password`, { new_password: pw }); setErr('') }
    catch (e) { setErr('重置密码失败') }
  }
  async function toggleDisabled(uid, disabled) {
    try { await axios.post(`/api/admin/users/${uid}/disabled`, { disabled }); reload() }
    catch (e) { setErr('操作失败') }
  }
  async function rotateInvite() {
    try { const r = await axios.post('/api/admin/invite-code/rotate', {}); setInvite(r.data.invite_code) }
    catch (e) { setErr('轮换失败') }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#15162d] rounded-xl p-6 w-[680px] max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between mb-4">
          <h2 className="text-[#e2e2f0] font-semibold">用户管理</h2>
          <button onClick={onClose} className="text-[#8888a8]">关闭</button>
        </div>
        {err && <div className="text-red-400 text-sm mb-2">{err}</div>}
        <div className="mb-4 text-sm text-[#e2e2f0]">
          邀请码：<code>{invite}</code>
          <button onClick={rotateInvite} className="ml-3 text-[#64ffda]">轮换</button>
        </div>
        <div className="mb-4 text-sm text-[#e2e2f0]">
          <div className="mb-1">自定义 API 允许域名（每行一个，同事要用别的服务在这里加）：</div>
          <div className="text-[11px] text-[#8888a8] mb-1">默认内置：{defaultHosts.join('、')}</div>
          <textarea value={hosts} onChange={(e) => setHosts(e.target.value)} rows={3}
                    className="w-full bg-[#0f0f23] px-2 py-1 text-[#e2e2f0]" placeholder="my.llm.cn" />
          <button onClick={saveHosts} className="mt-1 text-[#64ffda]">保存允许域名</button>
        </div>
        <table className="w-full text-sm text-[#e2e2f0]">
          <thead><tr className="text-[#8888a8]"><th>用户</th><th>今日</th><th>额度</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.uid}>
                <td>{u.username}{u.is_admin ? ' (admin)' : ''}</td>
                <td>{formatYuan(u.today_cost_yuan)}</td>
                <td>
                  <input defaultValue={u.daily_cap_yuan} className="w-16 bg-[#0f0f23] px-1"
                         onBlur={(e) => setCap(u.uid, e.target.value)} />
                </td>
                <td>{u.disabled ? '已禁用' : '正常'}</td>
                <td>
                  <button onClick={() => toggleDisabled(u.uid, !u.disabled)} className="text-[#64ffda] mr-2">
                    {u.disabled ? '启用' : '禁用'}
                  </button>
                  <button onClick={() => { const pw = prompt('新密码（≥8 位）'); if (pw) resetPassword(u.uid, pw) }}
                          className="text-[#64ffda]">改密</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Sidebar 账号块加入口** — 在账号块（74-104）`authUser.username` 行附近：

```jsx
{authUser?.is_admin && (
  <button onClick={() => onOpenAdmin?.()} className="text-[11px] text-[#64ffda] hover:underline mt-1">
    👤 用户管理
  </button>
)}
```

并在 Sidebar props 增加 `onOpenAdmin`。

- [ ] **Step 5: App.jsx 挂载** — 加 `const [showAdmin, setShowAdmin] = useState(false)`，给 Sidebar 传 `onOpenAdmin={() => setShowAdmin(true)}`，主界面渲染 `{showAdmin && <AdminPanel onClose={() => setShowAdmin(false)} />}`。

- [ ] **Step 6: 运行确认通过**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: PASS + build 零错

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/AdminPanel.jsx frontend/src/components/Sidebar.jsx frontend/src/App.jsx frontend/tests/
git commit -m "feat(b3): AdminPanel + sidebar admin entry"
```

---

### Task 17: must_change_password 强制改密屏

**Files:**
- Create: `frontend/src/components/ForcePasswordChange.jsx`
- Modify: `frontend/src/App.jsx`（gating，239-244）
- Test: `frontend/tests/forcePasswordChange.source.test.mjs`

**背景:** App.jsx 现 gating：`!authUser → Login`。B3 在登录后、主界面前插一道：`authUser.must_change_password → ForcePasswordChange`（不可关，改完刷新 authUser）。后端 Task 14 已硬拦业务路由（双保险）。

- [ ] **Step 1: 写失败测试**

```javascript
// frontend/tests/forcePasswordChange.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('ForcePasswordChange 调改密端点', () => {
  const s = readFileSync(new URL('../src/components/ForcePasswordChange.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/change-password/)
})

test('App 在 must_change_password 时挂强制改密屏', () => {
  const s = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(s, /must_change_password/)
  assert.match(s, /ForcePasswordChange/)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && node --test tests/forcePasswordChange.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 写 ForcePasswordChange.jsx**

```jsx
// frontend/src/components/ForcePasswordChange.jsx
import { useState } from 'react'
import axios from 'axios'
import { validateNewPassword } from '../utils/adminApi'

export default function ForcePasswordChange({ onChanged }) {
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [err, setErr] = useState('')
  async function submit() {
    if (!validateNewPassword(newPw)) { setErr('新密码至少 8 位'); return }
    try {
      await axios.post('/api/auth/change-password', { old_password: oldPw, new_password: newPw })
      onChanged?.()
    } catch (e) { setErr('修改失败，请检查原密码') }
  }
  return (
    <div className="h-screen flex items-center justify-center bg-[#0f0f23]">
      <div className="bg-[#15162d] rounded-xl p-6 w-[360px]">
        <h2 className="text-[#e2e2f0] font-semibold mb-3">首次登录请修改密码</h2>
        {err && <div className="text-red-400 text-sm mb-2">{err}</div>}
        <input type="password" placeholder="原密码" value={oldPw}
               onChange={(e) => setOldPw(e.target.value)} className="w-full mb-2 bg-[#0f0f23] px-2 py-1 text-[#e2e2f0]" />
        <input type="password" placeholder="新密码（≥8 位）" value={newPw}
               onChange={(e) => setNewPw(e.target.value)} className="w-full mb-3 bg-[#0f0f23] px-2 py-1 text-[#e2e2f0]" />
        <button onClick={submit} className="w-full bg-[#64ffda] text-[#0f0f23] rounded py-1">确认修改</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: App.jsx gating 插入**（在 `!authUser → Login` 之后、主界面之前）：

```jsx
if (authUser.must_change_password) {
  return <ForcePasswordChange onChanged={async () => {
    const r = await axios.get('/api/auth/me'); setAuthUser(r.data)
  }} />
}
```

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: PASS + build 零错

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ForcePasswordChange.jsx frontend/src/App.jsx frontend/tests/
git commit -m "feat(b3): force password change screen on must_change_password"
```

---

### Task 18: custom 模式 UI 确认 + raw fetch 补 credentials（防御）

**Files:**
- Modify: `frontend/src/components/SettingsModal.jsx`（确认 custom UI 可用 + 不再发 managed_base_url）
- Modify: `frontend/src/components/ChatPanel.jsx`（fetch，约 458）、`IndependentReviewDrawer.jsx`（fetch，约 69/190）
- Test: `frontend/tests/settingsModal.source.test.mjs`、既有 source-guard

**背景:** 探查确认 SettingsModal custom UI 已存在且未被禁用（managed-forced 只在后端 normalize）。Task 4 后端解锁后 custom 即可用。前端两件小事：① SettingsModal 不再发送 `managed_base_url`（Task 3 已服务端只读，前端同步去掉避免误导）；② raw `fetch` 默认 `credentials: 'same-origin'`（同源会带 cookie），但显式加 `credentials: 'include'` 更稳健、与 CSRF 同源 Origin 校验一致。

- [ ] **Step 1: 写失败测试**

```javascript
// frontend/tests/settingsModal.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('SettingsModal custom 模式输入仍在 + 不再提交 managed_base_url', () => {
  const s = readFileSync(new URL('../src/components/SettingsModal.jsx', import.meta.url), 'utf8')
  assert.match(s, /custom_api_base/)
  assert.match(s, /custom_api_key/)
  assert.doesNotMatch(s, /managed_base_url:/)   // 不再把 managed_base_url 放进提交体
})
```

```javascript
// frontend/tests/chatPanelCredentials.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('ChatPanel stream fetch 带 credentials', () => {
  const s = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')
  assert.match(s, /credentials:\s*'include'/)
})

test('IndependentReviewDrawer 两个 fetch（stream + discard）都带 credentials', () => {
  const s = readFileSync(new URL('../src/components/IndependentReviewDrawer.jsx', import.meta.url), 'utf8')
  const hits = (s.match(/credentials:\s*'include'/g) || []).length
  assert.ok(hits >= 2, `应有 ≥2 处 credentials（stream + discard），实际 ${hits}`)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && node --test tests/settingsModal.source.test.mjs tests/chatPanelCredentials.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 改实现** — SettingsModal 提交体里删 `managed_base_url`（若有）。ChatPanel/IndependentReviewDrawer 的 `fetch(url, { method:'POST', headers, body, signal })` 加 `credentials: 'include'`。

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: PASS + build 零错

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ tests/
git commit -m "feat(b3): custom UI confirm + raw fetch credentials"
```

---

# Phase 6 — 回归 + cutover

### Task 19: 全量回归 + DeepSeek 兼容 + cutover report

**Files:**
- Create: `docs/superpowers/cutover_report_2026-06-22_w2b-b3.md`
- Modify: `docs/current-worklist.md`、`CLAUDE.md`（加「## W2-B/B3」段）

- [ ] **Step 1: 后端全量回归**

Run: `.venv/bin/python -m pytest tests/`
Expected: 全绿（4 个已知 mac-realpath 环境差异除外）。**特别确认**：`tests/test_chat_runtime.py` 的 DeepSeek/tool-call follow-up 用例未回归（B3 注入 http_client 不碰 provider message/tool_choice/reasoning_content 序列化）；`tests/test_tenant_isolation.py` 跨租户隔离全绿（CSRF/admin 未破坏复合键归属）。

- [ ] **Step 2: 前端全量回归 + build**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: 全绿 + build 零错

- [ ] **Step 3: 手动安全验收（spec §12 B3 验收门）** — 用 TestClient/curl 逐项确认：
  - CSRF：跨站 Origin 的 POST（含 admin）被 403。
  - SSRF：custom_api_base 设私网/loopback/metadata/非 https/非白名单 → 400；models/list 同。
  - 越权：非 admin 访问 `/api/admin/*` → 403；A 用户访问 B 项目 → 404（B1 隔离回归）。
  - custom 激活：mode=custom + 白名单 https base → 设置保存成功 + 对话走自带 key（不计入 ¥ 配额，custom 走裸 client）。
  - must_change_password：bootstrap admin 首登被强制改密屏挡住业务路由。

- [ ] **Step 4: 写 cutover report** — 记录交付清单、红队修复、验收证据、已知限制（pinned-IP transport 后置、白名单需运维维护、`_LOGIN_FAILS` 单进程）。

- [ ] **Step 5: 更新 worklist + CLAUDE.md** — worklist W2 段标 B3 完成；CLAUDE.md 加「## W2-B/B3 admin + 安全硬化 + custom 激活」段（url_guard 白名单 SSRF / CSRF 中间件 / admin 端点 / must_change_password / custom 解锁的硬约束）。

- [ ] **Step 6: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(b3): cutover report + worklist/CLAUDE.md sync (admin + security + custom)"
```

---

## Self-Review

**1. Spec 覆盖（§7 admin / §8 安全 / §13 B3 验收门）:**
- §7 admin 面板（users 列表 + 改密/cap/禁用/邀请码轮换）→ Task 9-13、16 ✅
- §8.2 CSRF（Origin/Referer + CORS 收紧）→ Task 6-7 ✅
- §8.3 SSRF（请求时校验 + 不跟随重定向 + trust_env=False + managed_base_url 只读）→ Task 1-5 ✅（取白名单终态，§8.3 R3-NIT3 明确允许）
- §8 cookie Secure / 登录限流（per-username）→ Task 7-8 ✅
- must_change_password 强制 → Task 14、17 ✅
- custom 激活 → Task 4、5、18 ✅
- §13 验收门「CSRF/SSRF/越权 red-team 回归」→ Task 19 Step 3 ✅

**2. Placeholder 扫描:** 各 Task 给了真实测试 + 实现代码 + 命令 + 期望输出，无 TBD/TODO。

**3. 类型一致性:**
- `url_guard.SsrfBlockedError`/`assert_public_ip`/`validate_custom_api_base`/`build_guarded_http_client` 在 Task 1-2 定义，Task 3-5 一致引用。
- `accounts.list_all_users`/`admin_reset_password`/`rotate_invite_code` 在 Task 9-11 定义，Task 12-13 一致引用。
- 前端 `capPayload`/`validateNewPassword`/`summarizeUser`（Task 15）被 AdminPanel/ForcePasswordChange（Task 16-17）一致引用。
- `allowed_origins()`（Task 6）被 CORS（Task 7）复用。

## 变更记录

- **2026-06-22 初稿**（subagent 4-Explore 探查 + writing-plans）。
- **2026-06-22 Codex R1（CHANGES-REQUESTED，5 BLOCKER + 4 NIT）全修：**
  - **B1 SSRF 诚实化 + admin 可维护白名单**：删除「白名单通用防 rebinding」误述，明确白名单=安全边界（未连接层 pin IP、仍有 TOCTOU，pinned-IP 后置）；白名单改三层合并（默认 ∪ env ∪ **app_config 运行时项**），新增 `accounts.get/set_custom_api_extra_hosts` + `url_guard.set_runtime_allowed_hosts` + `GET/POST /api/admin/allowed-hosts` + AdminPanel UI + 启动加载——**解决「别预设同事用主流 provider」**（admin 面板增删、无需重启）。
  - **B2 managed_base_url 真只读**：`normalize_settings_payload` 强制回 `DEFAULT_MANAGED_BASE_URL`（清历史污染）+ `SettingsUpdate.managed_base_url` 改 Optional-ignore（避免前端去字段后 422）。
  - **B3 must_change_password 显式逐路由**：require_project / get_current_admin 默认依赖 + 显式改 `/api/chat`、`/api/chat/stream`、`/api/models/list`、`/api/settings`、`/api/projects`（body-project 路由改 require_project 默认依赖覆盖不到）。
  - **B4 models/list 400 不被吞**：校验移出宽 try + `except HTTPException: raise` + `finally close`。
  - **B5 CSRF 夹具迁移**：测试基类 TestClient 设默认 Origin + SSE 同源通过测试，防全套 POST 测试变 403。
  - **NIT**：登录限流 trim/casefold + 过期键清理；admin 防禁用自己/最后一个 admin；cap 用 Decimal 字符串解析；web 未设 CRA_ALLOWED_ORIGIN 启动告警。

- **2026-06-22 Codex R2（CHANGES-REQUESTED，6 BLOCKER + 4 NIT）全修**——主方向被确认对了（三层白名单覆盖保存/models-list/transport、normalize 不破坏 custom、require_password_current 方向对、DeepSeek 无新风险），BLOCKER 几乎全是 TDD 准确性：
  - B1 删 Architecture 段（line 7）残留「按构造即防 rebinding」误述。
  - B2 `SettingsUpdate` 另有必填 `managed_model`（main.py:293）→ 所有 settings POST 测试改用 `_settings_body(**overrides)` helper 补全必填体。
  - B3 缺失-Origin CSRF 测试不能靠 `headers={}`（httpx 合并默认头不删）→ 改 fresh `TestClient`。
  - B4 cookie_secure 测试显式 `self.m.app.state.cookie_secure=True`（AuthApiTestBase 不设）。
  - B5 前端 `capPayload` 返回**字符串**（匹配后端 `AdminCapBody: str|None`，免 422）+ 测试同步。
  - B6 chat/chat_stream 测试体字段是 `message_text`（models.py:68）非 `message`。
  - NIT：allowed-hosts 加 `is_valid_hostname` 形态校验（拒 scheme/port/path/通配符）；`admin_list_users` 局部 import metering；GET allowed-hosts 返 builtin/env/extra 三类；Task 18 加 IndependentReviewDrawer credentials source-guard。

- **2026-06-22 Codex R3（CHANGES-REQUESTED，1 BLOCKER + 4 NIT）全修**——Codex 确认主线全部可落地，仅剩：
  - **BLOCKER**：Task 8 per-username 限流测试被现有 per-IP slowapi（`main.py:222` 10/min）抢先 429 → 假通过。改为「纯函数测 `_login_throttled`/`_record_login_fail` + casefold + 端点测试 `m.limiter.enabled=False` 隔离 username 维度」。
  - NIT：cap Decimal 加 `is_finite()` 拒 NaN/Inf；`test_csrf.py` 显式 `from tests.test_settings_api import _settings_body`；启动加载白名单过滤非法历史项（accounts 层只存不校验）；Task 18 断言 IndependentReviewDrawer 两个 fetch（stream+discard）都带 credentials。
  - Codex R3 复核确认：白名单三处一致吃 `custom_api_allowed_hosts()`、normalize 不破坏 custom、`require_password_current` 不破坏 B1 隔离、DeepSeek 无新风险；多 worker 下 `_RUNTIME_ALLOWED_HOSTS` 需广播（后续部署事，不阻塞 B3）。

**Codex R3 结论原话**：「改掉 Task 8 的假通过测试后，这份 plan 我会给 APPROVED」。
- **2026-06-22 Codex R4（对抗式复核）→ Verdict: APPROVED，无残留 BLOCKER**。确认 Task 8 测试形态有效（纯函数先失败 + 端点 `limiter.enabled=False` 隔离 slowapi）、R3 NIT 全部改对；安全/架构 recap：admin 白名单一致喂 save 校验/models-list/runtime transport、`require_password_current` 不弱化 B1 隔离、DeepSeek 序列化未碰。唯一残留 NIT（不阻塞）：`test_csrf.py` 跨文件 import `_settings_body` 可改本地 helper（`test_settings_api` import 期会 import `backend.main`）——实施时随手做。
- **线程**：codex-server `019eef21-100e-7952-87f0-8f46aa6ca006`（spec+quality 合并轨，4 轮含 3 轮红队对抗）。
