# W2-B / B1 多租户基座 + 鉴权 + 项目创建闭环 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单用户应用改成「登录后每个用户各自工作区隔离」的多租户基座——注册/登录/会话、按 uid 分目录、统一归属卡点 `require_project`、复合键锁（贯彻到 skill.py）、per-uid settings、项目创建服务端分配工作区，做完即「A 不可触达 B 的任何数据」。

**Architecture:** 数据落 `<data-root>/users/<uid>/...`；登录态从 httpOnly cookie 认出 uid；每请求经 `get_skill_engine(uid)` 拿到绑定该 uid 数据根的引擎实例（SkillEngine 持 `self.uid`），项目级接口统一过 `require_project(uid, ref)`（canonicalize 到 `rec["id"]`、查不到即 404）；进程锁/store/搜索 project 级状态全改复合键 `tenant_project_key(uid, cid)`，**含 skill.py `record_stage_checkpoint`**。引擎业务逻辑不动。本 plan 是 spec `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md` 的 B1 阶段（B2 计费 / B3 admin+安全硬化 另两 plan）。

**Tech Stack:** FastAPI + SQLite(`sqlite3`, WAL) + `argon2-cffi` + React/axios + unittest/TestClient。

**验收门（B1 完成判据）**：跨租户隔离回归全绿——A 建项目、B 用该 project_id/项目名访问任一接口都 404；未登录所有 `/api/*`(白名单除外)→401；同项目 id 与 name 不产生双锁；A 的 custom settings 不串到 B。

**Codex R1 已修（8 BLOCKER）**：① per-uid settings 纳入 B1(Task8) ② 复合键贯彻 skill.py record_stage_checkpoint(Task6) ③ 前端创建闭环(Task15) ④ 既有测试迁移给具体配方(Task11) ⑤ 重排：创建闭环(Task10) 先于跨租户接线(Task11) ⑥ 桌面 /me 返回合成 local(Task9) ⑦ search 路径接 data_root + quota 隔离测(Task2/13) ⑧ Field import(Task7)。NIT：复合键统一 JSON-safe 字符串(够用且消毒)、change-password 保当前会话、chat_stream 不变式写清。
**Codex R2 已修（4 BLOCKER）**：① test_settings_api 迁移改用模块级 save_settings + 具体配方(Task8 端点去函数内 import / Task8 Step4) ② review 锁/store 既有测试迁移规则补齐(Task11 Step4) ③ Task13 补真 quota 隔离测试 + 改掉假 TDD 标签 ④ `reload(main)` 隔离：AuthApiTestBase mock heal + 重置 chat/review 模块级单例(Task7/11)。NIT：custom 不可用至 B3 写进 cutover、models/list 归类修正、record_stage_checkpoint 复合键源码守卫(Task6)。
**Codex R3 已修（1 BLOCKER）**：`independent-review/stream` worker 的**裸变量** `IndependentReviewAgent(skill_engine, settings)` + `agent.run(project_id)` + review store/lock 裸 project_id → 改 `scope.engine`/`load_settings(scope.uid)`/`scope.project_id`/`scope.lock_key`，收尾 grep 用词边界 `\bskill_engine\b`/`\bsettings\b`(Task11)。NIT：Task13 加 global 共享断言、`_reset` 清 _records 持 guard、collection-time heal 注记。
**Codex R4 红队已修（2 BLOCKER）**：① `get_chat_handler` 旧签名漏迁移点（`/api/chat`、`write_user_file`、`clear_conversation`、delete_project handler pop）全列 + grep `get_chat_handler\(`(Task11) ② Task10 `WebCreateTests` 跨文件继承缺 `from tests.test_auth_api import AuthApiTestBase`(Task10)。NIT：`materials/select-from-workspace` 先 require_project 再 desktop_bridge。

---

## File Structure

**新增**：`backend/tenant.py`（路径+复合键）、`backend/accounts.py`（SQLite 账号层）；`tests/test_tenant.py`/`test_accounts.py`/`test_auth_api.py`/`test_tenant_isolation.py`；`frontend/src/components/Login.jsx`、`frontend/src/utils/authState.js`、`frontend/src/api.js`、`frontend/tests/{authState,login.source,sidebar.source}.test.mjs`。

**修改**：`backend/config.py`（`data_root` + search 路径默认 data_root + `load/save_settings(uid)`）、`backend/main.py`（工厂/依赖/auth 端点/23 接口/创建闭环/bootstrap/启动断言）、`backend/chat.py`（复合键锁 + 搜索复合键 + ChatHandler.uid）、`backend/independent_review.py`（复合键）、`backend/skill.py`（SkillEngine.uid + record_stage_checkpoint 复合键）、`backend/models.py`（workspace_dir 可选）、`frontend/src/{App.jsx,main.jsx}`、`frontend/src/components/{Sidebar,ProjectCreateModal}.jsx`、`frontend/src/utils/projectCreatePayload.js`、`requirements.txt`。

---

## Task 1: 加 argon2 依赖

**Files:** Modify `requirements.txt`; Test `tests/test_requirements.py`

- [ ] **Step 1: 写失败测试** — `tests/test_requirements.py` 末尾追加：
```python
def test_argon2_dependency_pinned():
    from pathlib import Path
    text = Path(__file__).resolve().parent.parent.joinpath("requirements.txt").read_text(encoding="utf-8")
    assert "argon2-cffi==" in text
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_requirements.py -k argon2 -v` → FAIL
- [ ] **Step 3: 加依赖** — `requirements.txt` 追加 `argon2-cffi==23.1.0`；`uv pip install --python .venv/bin/python argon2-cffi==23.1.0`
- [ ] **Step 4: 跑确认 PASS** — 同 Step2 命令 → PASS
- [ ] **Step 5: Commit** — `git add requirements.txt tests/test_requirements.py && git commit -m "feat(w2b-b1): add argon2-cffi"`

---

## Task 2: `backend/tenant.py` + data_root + search 路径接 data_root + 复合键

**Files:** Create `backend/tenant.py`; Modify `backend/config.py`; Test `tests/test_tenant.py`

- [ ] **Step 1: 写失败测试** — Create `tests/test_tenant.py`:
```python
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
        self.assertEqual(Path(os.path.realpath(tenant.data_root())), self._tmp)

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
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_tenant.py -v` → FAIL
- [ ] **Step 3: 实现**

`backend/config.py`：在 `get_user_config_dir` 后加 `data_root`；并把两个 search 路径默认改为 `data_root()`：
```python
def data_root() -> Path:
    import os
    env = os.environ.get("CRA_DATA_ROOT")
    root = Path(env).expanduser() if env else get_user_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root
```
把行 45-50 改为（默认走 `data_root()`）：
```python
def get_search_runtime_state_path(config_dir: Path | None = None) -> Path:
    return (config_dir or data_root()) / SEARCH_RUNTIME_STATE_FILENAME


def get_search_cache_path(config_dir: Path | None = None) -> Path:
    return (config_dir or data_root()) / SEARCH_CACHE_FILENAME
```
Create `backend/tenant.py`:
```python
"""多租户路径与复合键（叶子模块；只依赖 config，绝不 import chat/skill/main）。"""
from pathlib import Path
from .config import data_root


def app_db_path() -> Path:
    return data_root() / "app.db"


def user_dir(uid: str) -> Path:
    return data_root() / "users" / uid


def user_projects_dir(uid: str) -> Path:
    return user_dir(uid) / "projects"


def user_config_path(uid: str) -> Path:
    return user_dir(uid) / "config.json"


def ensure_user_dirs(uid: str) -> None:
    user_projects_dir(uid).mkdir(parents=True, exist_ok=True)


def tenant_project_key(uid: str, project_id: str) -> str:
    """唯一中央复合键。统一 JSON-safe 字符串形态（同时用于内存 dict 与持久化 JSON 键——
    字符串对内存 dict 完全够用，省去双形态维护）。消毒 ':' 防分隔符注入。任何处禁止手拼。"""
    return f"{str(uid).replace(':', '_')}::{str(project_id).replace(':', '_')}"
```
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_tenant.py tests/test_search_state.py -v` → PASS（search_state 既有用例显式传 path、不受默认改动影响）
- [ ] **Step 5: Commit** — `git add backend/tenant.py backend/config.py tests/test_tenant.py && git commit -m "feat(w2b-b1): tenant paths + data_root (incl search) + tenant_project_key"`

---

## Task 3: accounts — users + argon2

**Files:** Create `backend/accounts.py`; Test `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试** — Create `tests/test_accounts.py`:
```python
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
        self.assertNotIn("s3cret-pw", rec["password_hash"])
        self.assertTrue(accounts.verify_user_password("alice", "s3cret-pw"))
        self.assertFalse(accounts.verify_user_password("alice", "wrong"))

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
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_accounts.py -v` → FAIL
- [ ] **Step 3: 实现** — Create `backend/accounts.py`（users 部分）：
```python
"""SQLite 账号层（叶子模块；只依赖 tenant）。"""
import hashlib, secrets, sqlite3, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from .tenant import app_db_path

_PH = PasswordHasher()


class UsernameTakenError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(app_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                uid TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0, daily_cost_micro_yuan INTEGER,
                must_change_password INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, created_ip TEXT, user_agent TEXT, last_seen TEXT);
            CREATE TABLE IF NOT EXISTS app_config(key TEXT PRIMARY KEY, value TEXT);
            """
        )


def create_user(username, password, is_admin=False, must_change_password=False) -> str:
    uid = uuid.uuid4().hex
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users(uid,username,password_hash,is_admin,must_change_password,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (uid, username, _PH.hash(password), int(is_admin), int(must_change_password), _now()))
    except sqlite3.IntegrityError as e:
        raise UsernameTakenError(username) from e
    return uid


def _row_to_user(row) -> dict:
    d = dict(row)
    for k in ("is_admin", "disabled", "must_change_password"):
        d[k] = bool(d[k])
    return d


def get_user_by_username(username) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_uid(uid) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    return _row_to_user(row) if row else None


def verify_user_password(username, password) -> bool:
    rec = get_user_by_username(username)
    if not rec:
        return False
    try:
        return _PH.verify(rec["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def set_user_password(uid, new_password) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE uid=?",
                     (_PH.hash(new_password), uid))
```
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_accounts.py -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/accounts.py tests/test_accounts.py && git commit -m "feat(w2b-b1): accounts users + argon2"`

---

## Task 4: accounts — sessions

**Files:** Modify `backend/accounts.py`; Test `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**（追加 `AccountsSessionTests`，setUp 同 Task3 但 `self.uid = accounts.create_user("dave","pw")`）：
```python
    def test_roundtrip(self):
        t = accounts.create_session(self.uid, ttl_days=30, ip="1.2.3.4", ua="ua")
        self.assertEqual(accounts.get_session_uid(t), self.uid)

    def test_stored_as_hash(self):
        t = accounts.create_session(self.uid)
        with accounts._connect() as c:
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
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_accounts.py::AccountsSessionTests -v` → FAIL
- [ ] **Step 3: 实现**（追加到 accounts.py）：
```python
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(uid, ttl_days=30, ip="", ua="") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc); exp = now + timedelta(days=ttl_days)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash,uid,created_at,expires_at,created_ip,user_agent,last_seen)"
            " VALUES(?,?,?,?,?,?,?)",
            (_hash_token(token), uid, now.isoformat(timespec="seconds"),
             exp.isoformat(timespec="seconds"), ip, ua, now.isoformat(timespec="seconds")))
    return token


def get_session_uid(token) -> Optional[str]:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT s.uid uid, s.expires_at expires_at, u.disabled disabled "
            "FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=?", (_hash_token(token),)).fetchone()
    if not row or row["disabled"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return None
    return row["uid"]


def delete_session(token) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))


def delete_user_sessions(uid) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE uid=?", (uid,))


def delete_other_user_sessions(uid, keep_token) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE uid=? AND token_hash<>?", (uid, _hash_token(keep_token)))


def set_user_disabled(uid, disabled: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET disabled=? WHERE uid=?", (int(disabled), uid))
    if disabled:
        delete_user_sessions(uid)
```
（accounts.py 顶部 import 已含 hashlib/secrets/timedelta。）
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_accounts.py::AccountsSessionTests -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/accounts.py tests/test_accounts.py && git commit -m "feat(w2b-b1): accounts sessions (hash/expiry/revoke)"`

---

## Task 5: accounts — app_config（邀请码）

**Files:** Modify `backend/accounts.py`; Test `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**（追加 `AccountsConfigTests`，setUp 同 Task3）：
```python
    def test_get_set_default(self):
        self.assertEqual(accounts.get_config("invite_code", "fb"), "fb")
        accounts.set_config("invite_code", "JOIN"); self.assertEqual(accounts.get_config("invite_code"), "JOIN")

    def test_seed_idempotent(self):
        accounts.seed_config_if_absent("invite_code", "S1"); accounts.seed_config_if_absent("invite_code", "S2")
        self.assertEqual(accounts.get_config("invite_code"), "S1")
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_accounts.py::AccountsConfigTests -v` → FAIL
- [ ] **Step 3: 实现**（追加）：
```python
def get_config(key, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key, value) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO app_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def seed_config_if_absent(key, value) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES(?,?)", (key, value))
```
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_accounts.py::AccountsConfigTests -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/accounts.py tests/test_accounts.py && git commit -m "feat(w2b-b1): accounts app_config (invite code)"`

---

## Task 6: 复合键锁 — chat + independent_review + **skill.py**

**Files:** Modify `backend/chat.py`(392,1165 + 锁调用处)、`backend/independent_review.py`、`backend/skill.py`(912 加 uid, 1955 record_stage_checkpoint)；Test `tests/test_tenant_isolation.py`

> **关键（Codex R1-B2）**：`skill.py:record_stage_checkpoint`（行 1955-1987）内部用裸 `project_id` 取 `_get_project_request_lock` + `get_independent_review_lock`。若只改 chat/endpoint 不改这里，审查跑着时 checkpoint 会检查**另一把** review lock → `review_passed_at` 误放行。故 SkillEngine 持 `self.uid`，record_stage_checkpoint 用复合键。

- [ ] **Step 1: 写失败测试** — Create `tests/test_tenant_isolation.py`:
```python
import unittest
from backend.tenant import tenant_project_key
from backend import chat as chat_mod
from backend import independent_review as ir_mod


class CompositeLockKeyTests(unittest.TestCase):
    def test_two_users_distinct_locks(self):
        a = chat_mod._get_project_request_lock(tenant_project_key("uA", "proj-x"))
        b = chat_mod._get_project_request_lock(tenant_project_key("uB", "proj-x"))
        self.assertIsNot(a, b)
        self.assertIs(chat_mod._get_project_request_lock(tenant_project_key("uA", "proj-x")), a)

    def test_review_lock_composite(self):
        self.assertIsNot(ir_mod.get_independent_review_lock(tenant_project_key("uA", "proj-x")),
                         ir_mod.get_independent_review_lock(tenant_project_key("uB", "proj-x")))

    def test_skill_engine_carries_uid_default_local(self):
        from backend.skill import SkillEngine
        import tempfile, os
        eng = SkillEngine(__import__("pathlib").Path(os.path.realpath(tempfile.mkdtemp())), __import__("pathlib").Path("."))
        self.assertEqual(getattr(eng, "uid", None), "local")

    def test_record_stage_checkpoint_uses_composite_key(self):
        # Codex R2-NIT3：锁住 record_stage_checkpoint 用复合键取 request/review 锁（否则审查跑着时门禁误放行）
        import inspect
        from backend.skill import SkillEngine
        src = inspect.getsource(SkillEngine.record_stage_checkpoint)
        self.assertIn("tenant_project_key(self.uid", src)
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py::CompositeLockKeyTests -v` → FAIL（`SkillEngine` 无 `uid` 属性）
- [ ] **Step 3: 实现**

`backend/skill.py` `SkillEngine.__init__`（行 912）加 uid：
```python
def __init__(self, projects_dir: Path, skill_dir: Path, uid: str = "local"):
    self.uid = uid
    self.projects_dir = projects_dir
    # ...（其余不变）
```
`record_stage_checkpoint`（行 1955-1968）两处取锁改复合键：
```python
    from backend.chat import _get_project_request_lock
    from backend.tenant import tenant_project_key
    # ...
    lock = _get_project_request_lock(tenant_project_key(self.uid, project_id))
    with lock:
        if key == "review_passed_at" and action == "set":
            from backend.independent_review import get_independent_review_lock
            review_lock = get_independent_review_lock(tenant_project_key(self.uid, project_id))
```
`backend/chat.py` `ChatHandler.__init__`（行 392）加 `uid`：
```python
def __init__(self, settings: Settings, skill_engine: SkillEngine, uid: str = "local"):
    self.uid = uid
    # ...（其余不变）
```
`ChatHandler._get_project_request_lock`（行 1165）改复合键：
```python
def _get_project_request_lock(self, project_id: str):
    from .tenant import tenant_project_key
    return _get_project_request_lock(tenant_project_key(self.uid, project_id))
```
grep `backend/chat.py` 中 ChatHandler 内对 `get_independent_review_lock(`、`_REVIEW_SESSION_STORE.`、`_CONVERSATION_STATE_LOCKS`/`_get_conversation_state_lock(` 传 `project_id` 的调用，逐处包 `tenant_project_key(self.uid, project_id)`：
```bash
grep -n "get_independent_review_lock\|_REVIEW_SESSION_STORE\|_get_conversation_state_lock\|_CONVERSATION_STATE_LOCKS" backend/chat.py
```
（`independent_review.py` 的 `get_independent_review_lock`/`ReviewSessionStore._records` 签名不变——它们把入参当不透明 str 键；只需调用方传复合键。）
- [ ] **Step 4: 跑确认 PASS + 回归** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_chat_runtime.py tests/test_independent_review.py tests/test_skill_engine.py -v` → PASS（默认 uid="local" 保持既有单用户语义）
- [ ] **Step 5: Commit** — `git add backend/chat.py backend/independent_review.py backend/skill.py tests/test_tenant_isolation.py && git commit -m "feat(w2b-b1): composite (uid,pid) keys across chat/review/skill record_stage_checkpoint"`

---

## Task 7: per-uid 引擎/handler 工厂 + ProjectScope + 鉴权依赖

**Files:** Modify `backend/main.py`(行 1-95 区 + 74-89)；Test `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试** — Create `tests/test_auth_api.py`:
```python
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
        # Codex R2-B4: reload(main) 会跑 heal_stale_managed_model（可能发真实 /models 请求并 timeout）→ mock 掉
        self._heal = mock.patch("backend.config.heal_stale_managed_model", side_effect=lambda s: (s, None))
        self._heal.start()
        import backend.main as m; importlib.reload(m)
        self.m = m; m.app.state.auth_required = True
        self._reset_module_singletons()
        self.client = TestClient(m.app)

    def _reset_module_singletons(self):
        # chat/independent_review 不随 reload(main) 重置，逐测试清干净防串扰（Codex R2-B4）
        from backend import chat as cm, independent_review as im
        cm._PROJECT_REQUEST_LOCKS.clear(); cm._CONVERSATION_STATE_LOCKS.clear()
        cm._SEARCH_ROUTER_SINGLETON = None
        im._INDEPENDENT_REVIEW_LOCKS.clear()
        with im._REVIEW_SESSION_STORE._guard:   # R3-NIT2：清 _records 持其自身 guard
            im._REVIEW_SESSION_STORE._records.clear()

    def tearDown(self):
        self._heal.stop(); self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)


class UnauthedTests(AuthApiTestBase):
    def test_health_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
    def test_projects_needs_auth(self):
        self.assertEqual(self.client.get("/api/projects").status_code, 401)
```
> note：`importlib.reload(main)` 让每个测试拿到隔离的 `CRA_DATA_ROOT`；reload 顺序 config→accounts→main。main 模块级会跑 `heal_stale_managed_model`（**有网络**），故基类 reload 前已 mock 它（见上）。⚠️ Codex R3-NIT3：各测试文件**模块顶层** `import backend.main` 的**首次 collection import** 早于 setUp、mock 拦不住——若 CI/本地预检慢，建议加一个 `tests/conftest.py` autouse fixture 或 collection 前 env 关掉 heal 网络（可选、非阻塞，B1 不强制）。
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_auth_api.py::UnauthedTests::test_projects_needs_auth -v` → FAIL（现 200）
- [ ] **Step 3: 实现工厂 + 依赖**

`backend/main.py` 顶部 import：把 `from pydantic import BaseModel` 改 `from pydantic import BaseModel, Field`；追加：
```python
import os
from dataclasses import dataclass
from fastapi import Depends, Response
from .tenant import user_projects_dir, ensure_user_dirs, tenant_project_key
from . import accounts
```
替换行 74-89（全局 skill_engine + get_chat_handler）为：
```python
accounts.init_db()
accounts.seed_config_if_absent("invite_code", os.environ.get("CRA_INVITE_CODE", "change-me"))

_engines: dict[str, SkillEngine] = {}
_engines_guard = threading.Lock()
_chat_handlers: dict[tuple, ChatHandler] = {}
_settings_lock = threading.Lock()
_desktop_bridge = None
SESSION_COOKIE = "cra_session"


def get_skill_engine(uid: str) -> SkillEngine:
    with _engines_guard:
        eng = _engines.get(uid)
        if eng is None:
            ensure_user_dirs(uid)
            eng = SkillEngine(user_projects_dir(uid), settings.skill_dir, uid=uid)
            _engines[uid] = eng
        return eng


def get_chat_handler(uid: str, project_id: str) -> ChatHandler:
    # load_settings/save_settings 已在 main.py 顶部从 .config import（行30）——用模块级名字，
    # 不要函数内再 import，否则测试 `mock.patch.object(main_module, "save_settings")` 拦不到（Codex R2-B1）
    with _settings_lock:
        key = (uid, project_id)
        if key not in _chat_handlers:
            _chat_handlers[key] = ChatHandler(load_settings(uid), get_skill_engine(uid), uid=uid)
        return _chat_handlers[key]


def get_current_uid(request: Request) -> str:
    if not getattr(request.app.state, "auth_required", True):
        return "local"
    token = request.cookies.get(SESSION_COOKIE)
    uid = accounts.get_session_uid(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


def get_current_admin(uid: str = Depends(get_current_uid)) -> str:
    rec = accounts.get_user_by_uid(uid)
    if not rec or not rec["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return uid


@dataclass
class ProjectScope:
    uid: str
    project_id: str          # canonical rec["id"]
    engine: SkillEngine
    project_record: dict
    lock_key: str


def require_project(project_id: str, uid: str = Depends(get_current_uid)) -> ProjectScope:
    eng = get_skill_engine(uid)
    rec = eng.get_project_record(project_id)   # 认 id 或 name 别名
    if rec is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    cid = rec["id"]
    return ProjectScope(uid=uid, project_id=cid, engine=eng, project_record=rec,
                        lock_key=tenant_project_key(uid, cid))
```
模块底部（mount 前）设默认 `app.state.auth_required = True`。
> note（chat_stream 不变式）：`chat_stream` 在函数体先 `require_project()`（同步校验、不持 review lock），handler 仍在 `generate()` 内创建——不违反既有「review worker 必须在 endpoint 函数体释放 review lock」约束（chat stream 未被消费时不取 review lock）。Task 11 接线时保留 generate() 结构。
- [ ] **Step 4: 跑确认** — `.venv/bin/python -m pytest tests/test_auth_api.py::UnauthedTests::test_health_public -v` → PASS（`test_projects_needs_auth` 待 Task 11 接 `/api/projects` 后转绿）
- [ ] **Step 5: Commit** — `git add backend/main.py tests/test_auth_api.py && git commit -m "feat(w2b-b1): per-uid engine/chat factories + get_current_uid/require_project/ProjectScope"`

---

## Task 8: per-uid settings（隔离存储 + 读写接口）

**Files:** Modify `backend/config.py`(load/save_settings 加 uid)、`backend/main.py`(GET/POST /api/settings)；Test `tests/test_settings_api.py`

> **Codex R1-B1**：现 `load/save_settings` 固定走全局 `~/.consulting-report/config.json` → A 的 custom 配置串给 B。B1 必须按 uid 隔离存储。custom 模式**激活 + SSRF** 仍归 B3；B1 只保证「各人配置各存各的、互不可见」（managed-forced 不变，managed 全员相同、无泄漏面，但 custom_api_key 等敏感字段不再共享）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_settings_api.py`）：
```python
def test_settings_isolated_per_uid(self):
    from backend.config import load_settings, save_settings, Settings
    import os, tempfile
    from unittest import mock
    tmp = os.path.realpath(tempfile.mkdtemp())
    with mock.patch.dict(os.environ, {"CRA_DATA_ROOT": tmp}):
        sa = Settings(); sa.custom_api_key = "A-KEY"; save_settings(sa, uid="ua")
        sb = Settings(); sb.custom_api_key = "B-KEY"; save_settings(sb, uid="ub")
        self.assertEqual(load_settings("ua").custom_api_key, "A-KEY")
        self.assertEqual(load_settings("ub").custom_api_key, "B-KEY")
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_settings_api.py -k isolated -v` → FAIL（save_settings 不收 uid）
- [ ] **Step 3: 实现**

`backend/config.py` `load_settings`/`save_settings` 加可选 `uid`（None=全局/桌面 local 兼容）：
```python
def _config_path_for(uid: str | None) -> Path:
    if uid is None:
        return get_user_config_dir() / "config.json"
    from .tenant import user_config_path
    return user_config_path(uid)


def load_settings(uid: str | None = None) -> Settings:
    config_file = _config_path_for(uid)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return Settings(**normalize_settings_payload(json.load(f)))
    return Settings()


def save_settings(settings: Settings, uid: str | None = None):
    config_file = _config_path_for(uid)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_settings_payload(settings.model_dump())
    for key in ["mode", "api_key", "api_base", "model", "projects_dir", "skill_dir", "managed_client_token"]:
        data.pop(key, None)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
```
`backend/main.py` `GET /api/settings`（行 103）+ `POST /api/settings`（行 125）改 per-uid：GET 加 `uid: str = Depends(get_current_uid)`、用 `load_settings(uid)` 取代全局 `settings` 读；POST 加 `uid: str = Depends(get_current_uid)`、对 `load_settings(uid)` 改字段后 `save_settings(s, uid=uid)`、并 `with _settings_lock: _chat_handlers = {k:v for k,v in _chat_handlers.items() if k[0]!=uid}`（只清该用户的 handler 缓存）。具体：
```python
# 注意：load_settings/save_settings 用 main.py 顶部已有的模块级 import（行30），端点内不要再 import（Codex R2-B1）
@app.get("/api/settings")
async def get_settings(uid: str = Depends(get_current_uid)):
    data = load_settings(uid).model_dump(exclude={"managed_client_token"})
    data["api_key"] = "***" if data["api_key"] else ""
    data["custom_api_key"] = "***" if data.get("custom_api_key") else ""
    return data


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate, uid: str = Depends(get_current_uid)):
    with _settings_lock:
        s = load_settings(uid)
        s.mode = update.mode; s.managed_base_url = update.managed_base_url; s.managed_model = update.managed_model
        if "managed_vision_model" in update.model_fields_set and update.managed_vision_model is not None:
            s.managed_vision_model = update.managed_vision_model
        if "vision_enabled" in update.model_fields_set and update.vision_enabled is not None:
            s.vision_enabled = update.vision_enabled
        s.custom_api_base = update.custom_api_base
        if update.custom_api_key != "***":
            s.custom_api_key = update.custom_api_key
        s.custom_model = update.custom_model
        if "custom_context_limit_override" in update.model_fields_set:
            s.custom_context_limit_override = clamp_custom_context_limit_override(update.custom_context_limit_override)
        save_settings(s, uid=uid)
        for k in [k for k in _chat_handlers if k[0] == uid]:
            _chat_handlers.pop(k, None)
    return {"status": "ok"}
```
- [ ] **Step 4: 迁移既有 `test_settings_api.py`（Codex R2-B1，具体配方）**

既有测试改 `main_module.settings` + patch `main_module.save_settings`，现端点不再读全局 settings、改读 `load_settings(uid)`。逐项改：
- setUp 设 `main_module.app.state.auth_required = False`（uid=local），并 mock heal（同 AuthApiTestBase）。
- GET 断言：先 `from backend.config import save_settings, Settings; save_settings(Settings(), uid="local")` 预置，再断言 `client.get("/api/settings")` 读到的字段。
- POST「save 被调用」断言：patch **`backend.main.save_settings`**（main 顶部 import 的模块级名字，现已可拦截）→ `save_settings_mock.assert_called_once()`（不校验参数，签名已变 `(settings, uid=...)`）。
- 删掉对 `main_module.settings.model_dump()` 的 save/restore（端点不再用全局 settings）。
- [ ] **Step 5: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_settings_api.py -v` → PASS
- [ ] **Step 6: Commit** — `git add backend/config.py backend/main.py tests/test_settings_api.py && git commit -m "feat(w2b-b1): per-uid settings storage + isolated settings endpoints"`

---

## Task 9: `/api/auth/*` 端点（含桌面 local /me + change-password 保当前会话）

**Files:** Modify `backend/main.py`；Test `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试**（追加 `AuthFlowTests(AuthApiTestBase)`）：
```python
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
        self._reg(); self.assertEqual(self.client.post("/api/auth/login", json={"username":"alice","password":"bad"}).status_code, 401)
    def test_change_password_keeps_current_session(self):
        self._reg(); self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})
        r = self.client.post("/api/auth/change-password", json={"old_password":"pw-123456","new_password":"new-123456"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)  # 当前会话仍有效


class DesktopLocalTests(AuthApiTestBase):
    def setUp(self):
        super().setUp(); self.m.app.state.auth_required = False
    def test_me_returns_synthetic_local(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()["uid"], "local")
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_auth_api.py::AuthFlowTests tests/test_auth_api.py::DesktopLocalTests -v` → FAIL
- [ ] **Step 3: 实现 auth 端点**（追加到 main.py）：
```python
class RegisterPayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=6, max_length=200)
    invite_code: str

class LoginPayload(BaseModel):
    username: str
    password: str

class ChangePwPayload(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=200)


@app.post("/api/auth/register")
@limiter.limit("10/minute")
def auth_register(request: Request, payload: RegisterPayload):
    if payload.invite_code != accounts.get_config("invite_code", ""):
        raise HTTPException(status_code=403, detail="邀请码无效")
    try:
        uid = accounts.create_user(payload.username, payload.password)
    except accounts.UsernameTakenError:
        raise HTTPException(status_code=409, detail="用户名已被占用")
    ensure_user_dirs(uid)
    return {"status": "ok"}


@app.post("/api/auth/login")
@limiter.limit("10/minute")
def auth_login(request: Request, payload: LoginPayload, response: Response):
    if not accounts.verify_user_password(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    rec = accounts.get_user_by_username(payload.username)
    if rec["disabled"]:
        raise HTTPException(status_code=403, detail="账号已停用")
    token = accounts.create_session(rec["uid"], ip=request.client.host if request.client else "",
                                    ua=request.headers.get("user-agent", ""))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=bool(getattr(request.app.state, "cookie_secure", False)),
                        max_age=30 * 24 * 3600, path="/")
    return {"status": "ok"}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        accounts.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(request: Request, uid: str = Depends(get_current_uid)):
    if uid == "local" and not getattr(request.app.state, "auth_required", True):
        return {"uid": "local", "username": "本地用户", "is_admin": False, "must_change_password": False}
    rec = accounts.get_user_by_uid(uid)
    if not rec:
        raise HTTPException(status_code=401, detail="未登录")
    return {"uid": uid, "username": rec["username"], "is_admin": rec["is_admin"],
            "must_change_password": rec["must_change_password"]}


@app.post("/api/auth/change-password")
def auth_change_password(request: Request, payload: ChangePwPayload, uid: str = Depends(get_current_uid)):
    rec = accounts.get_user_by_uid(uid)
    if not accounts.verify_user_password(rec["username"], payload.old_password):
        raise HTTPException(status_code=401, detail="原密码错误")
    accounts.set_user_password(uid, payload.new_password)
    cur = request.cookies.get(SESSION_COOKIE)
    if cur:
        accounts.delete_other_user_sessions(uid, cur)   # 保当前、吊销其它（spec）
    else:
        accounts.delete_user_sessions(uid)
    return {"status": "ok"}
```
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_auth_api.py -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/main.py tests/test_auth_api.py && git commit -m "feat(w2b-b1): /api/auth/* + synthetic local /me + change-password keeps current session"`

---

## Task 10: 项目创建闭环（web 服务端分配工作区、拒客户端路径）

**Files:** Modify `backend/models.py`、`backend/main.py`(create 端点)；Test `tests/test_project_create_api.py`

> 先于 Task 11（跨租户接线用到无 workspace_dir 创建，须先把 model 改可选 + 创建端点闭环）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_project_create_api.py`，跨文件复用基类，**顶部加 import**）：
```python
from tests.test_auth_api import AuthApiTestBase   # ✦ Codex R4-B2：跨文件继承须显式 import


class WebCreateTests(AuthApiTestBase):
    def _login(self):
        self.client.post("/api/auth/register", json={"username":"alice","password":"pw-123456","invite_code":"JOIN"})
        self.client.post("/api/auth/login", json={"username":"alice","password":"pw-123456"})
        return self.client.get("/api/auth/me").json()["uid"]
    def test_web_rejects_client_path_and_allocates(self):
        uid = self._login()
        bad = self.client.post("/api/projects", json={"name":"x","project_type":"strategy","theme":"t",
            "deadline":"2026-12-31","expected_length":"1万字","workspace_dir":"/etc/evil"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/projects", json={"name":"x","project_type":"strategy","theme":"t",
            "deadline":"2026-12-31","expected_length":"1万字"})
        self.assertEqual(ok.status_code, 200)
        from backend.tenant import user_projects_dir
        self.assertIn(str(user_projects_dir(uid)), ok.json()["project"]["workspace_dir"])
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_project_create_api.py::WebCreateTests -v` → FAIL（现 workspace_dir 必填 422 / 接受任意路径）
- [ ] **Step 3: 实现**

`backend/models.py` `ProjectInfo.workspace_dir`（行 22）改可选：
```python
    workspace_dir: Optional[str] = Field(default=None, max_length=500)
```
`backend/main.py` create 端点（行 209-215）：
```python
@app.post("/api/projects")
async def create_project(info: ProjectInfo, request: Request, uid: str = Depends(get_current_uid)):
    if getattr(request.app.state, "auth_required", True):   # web
        if info.workspace_dir or info.initial_material_paths:
            raise HTTPException(status_code=400, detail="web 模式不接受客户端工作目录/本地材料路径")
        info = info.model_copy(update={"workspace_dir": str(user_projects_dir(uid) / uuid.uuid4().hex)})
    try:
        project = get_skill_engine(uid).create_project(info)
        return {"status": "ok", "project_id": project["id"], "project": project}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_project_create_api.py::WebCreateTests -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/models.py backend/main.py tests/test_project_create_api.py && git commit -m "feat(w2b-b1): web project creation allocates workspace server-side; reject client path"`

---

## Task 11: 23 接口接 require_project/uid + 既有测试迁移

**Files:** Modify `backend/main.py`(20 处 `skill_engine.`)、既有测试基类；Test `tests/test_tenant_isolation.py` + 迁移既有

**接口归类表（main.py 全 23）**：
- public：`GET /api/health`、`/api/auth/{register,login}`、SPA 静态。
- uid-scoped：`GET /api/projects`(行206)、`POST /api/projects`(已 Task10)、`GET/POST /api/settings`(已 Task8)、`POST /api/models/list`、`/api/auth/{logout,me,change-password}`。
- require_project（路径 `{project_id}`）：materials(list/select/upload/delete)、files(list/read/write)、workspace、independent-review(stream/discard)、export-draft、`DELETE /api/projects/{id}`、checkpoints、conversation(get/clear) —— 行 220/223/228/238/246/284/293/328/336-338/361/372/406/431/612-614/623/659/668/693。
- body-project_id：`POST /api/chat`、`POST /api/chat/stream` —— 函数体先 `require_project(chat_request.project_id, uid)`。
- desktop-only（web 503，前端隐藏）：`select-workspace-folder/files`（无 project_id，仅 `Depends(get_current_uid)` + `require_desktop_bridge`）、`materials/select-from-workspace`（有 project_id：✦ Codex R4-NIT 先 `Depends(require_project)` 校验归属、**再** `require_desktop_bridge()` 返 503——不泄项目存在性又守桌面边界）。（✦ R2-NIT2：`POST /api/models/list` 属 **uid-scoped**，非 desktop-only。）

- [ ] **Step 1: 写失败测试**（追加 `CrossTenantApiTests` 到 test_tenant_isolation.py；**继承 `AuthApiTestBase`** 复用 heal mock + 单例 reset，再起第二个 client）：
```python
from tests.test_auth_api import AuthApiTestBase

class CrossTenantApiTests(AuthApiTestBase):
    def setUp(self):
        super().setUp()   # heal mock + 单例 reset + auth_required=True + self.client(=A)
        from fastapi.testclient import TestClient
        self.A = self.client; self.B = TestClient(self.m.app)
        for c, u in ((self.A, "alice"), (self.B, "bob")):
            c.post("/api/auth/register", json={"username":u,"password":"pw-123456","invite_code":"JOIN"})
            c.post("/api/auth/login", json={"username":u,"password":"pw-123456"})
    def _create(self, c, name):
        return c.post("/api/projects", json={"name":name,"project_type":"strategy","theme":"t",
            "deadline":"2026-12-31","expected_length":"1万字"}).json()["project_id"]
    def test_b_cannot_read_a(self):
        pid = self._create(self.A, "A机密")
        self.assertEqual(self.A.get(f"/api/projects/{pid}/workspace").status_code, 200)
        self.assertEqual(self.B.get(f"/api/projects/{pid}/workspace").status_code, 404)
        self.assertEqual(self.B.get("/api/projects").json(), [])
    def test_name_ref_no_leak(self):
        self._create(self.A, "A机密")
        self.assertEqual(self.B.get("/api/projects/A机密/workspace").status_code, 404)
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py::CrossTenantApiTests -v` → FAIL
- [ ] **Step 3: 逐接口接线（样板）**

`GET /api/projects`→`async def list_projects(uid: str = Depends(get_current_uid)): return get_skill_engine(uid).list_projects()`
`GET .../{project_id}/workspace`→`async def get_workspace(scope: ProjectScope = Depends(require_project)): return scope.engine.get_workspace_summary(scope.project_id)`
`POST /api/chat/stream`（行 710）：
```python
@app.post("/api/chat/stream")
@limiter.limit("20/minute")
def chat_stream(request: Request, chat_request: ChatRequest, uid: str = Depends(get_current_uid)):
    scope = require_project(chat_request.project_id, uid)
    def generate():
        try:
            handler = get_chat_handler(scope.uid, scope.project_id)
            # ...（generate 体不变，把 chat_request.project_id 一律换 scope.project_id）
```
其余 20 处：把 `skill_engine.X(project_id, ...)` 换 `scope.engine.X(scope.project_id, ...)`（接 `scope: ProjectScope = Depends(require_project)`）。`checkpoints`(行659) 用 `scope.engine.record_stage_checkpoint(scope.project_id, key, action)`。

**✦ `independent-review/stream`（Codex R3-B1，裸变量陷阱）**：该 endpoint worker（行约 431/488）有**不带点的裸变量** `IndependentReviewAgent(skill_engine, settings)` + `agent.run(project_id, ...)` + review lock/store 用裸 project_id —— `grep skill_engine\.` 抓不到。明确改：
- `agent = IndependentReviewAgent(scope.engine, load_settings(scope.uid))`
- `agent.run(scope.project_id, ...)`
- endpoint 内所有 review lock 取用、`_REVIEW_SESSION_STORE` 的 `get_done_mtime/claim_first/claim_resume/finalize/discard` 全用 **`scope.lock_key`**（不是裸 project_id）
- `discard` endpoint(行592) 同样用 `scope.lock_key`

**✦ `get_chat_handler` 全调用点（Codex R4-B1，旧签名 `(project_id)`→`(uid, project_id)`，漏一个就 TypeError）**：除 `/api/chat/stream` 外，真实还有 `/api/chat`(行304)、`write_user_file`(行364)、`clear_conversation`(行696)。逐个改：先 `scope: ProjectScope = Depends(require_project)`（body-project_id 的 `/api/chat` 在函数体 `scope=require_project(...)`），再 `get_chat_handler(scope.uid, scope.project_id)`，锁/写入用 `scope.project_id`/`scope.engine`。`delete_project` 清 handler 缓存用 `_chat_handlers.pop((scope.uid, scope.project_id), None)`。

删全局 `skill_engine = SkillEngine(...)` 整行后，**收尾 grep 用词边界**：`rg "\bskill_engine\b" backend/main.py`（不是只 `skill_engine\.`，抓裸变量漏网）、`rg "get_chat_handler\(" backend/main.py`（核每个调用都是 `(uid, project_id)` 新签名）、`rg "\bsettings\b" backend/main.py`（复核 review/chat 路径用 `load_settings(uid)` 而非全局 settings）。
- [ ] **Step 4: 迁移既有测试（Codex R1-B4，必须做完才会绿）**

既有测试假设全局 `skill_engine` + 无鉴权，逐文件改：
- 通用：各测试基类 setUp 加 `main_module.app.state.auth_required = False`（走 uid=local，断言语义=单用户）。
- `test_main_api.py`：把 `@mock.patch("backend.main.skill_engine.X")` 改为 patch local 引擎实例的方法 + 让 require_project 过：
```python
def _local_engine(self):
    return main_module.get_skill_engine("local")
# checkpoint 测试改写：
def test_checkpoint_set_delegates(self):
    eng = self._local_engine()
    with mock.patch.object(eng, "get_project_record", return_value={"id": "demo", "name": "demo"}), \
         mock.patch.object(eng, "record_stage_checkpoint",
                           return_value={"status":"ok","key":"outline_confirmed_at","timestamp":"2026-04-17T12:00:00"}) as rec:
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed")
    self.assertEqual(r.status_code, 200)
    rec.assert_called_once_with("demo", "outline_confirmed_at", "set")
```
  （所有 patch `backend.main.skill_engine.*` → `mock.patch.object(main_module.get_skill_engine("local"), "*")`；凡走 `{project_id}` 路径的测试都加 `get_project_record` mock 返回 `{"id": <pid>, ...}` 让 require_project 过。）
- `test_stream_api.py`：`main_module.get_chat_handler = lambda project_id: handler` → `lambda uid, project_id: handler`；并 `mock.patch.object(main_module.get_skill_engine("local"), "get_project_record", return_value={"id": <pid>, "name": <pid>})` 让 require_project 过；setUp 设 `auth_required=False`。
- `test_project_create_api.py`：`@mock.patch("backend.main.skill_engine.create_project")` → `mock.patch.object(main_module.get_skill_engine("local"), "create_project", ...)`；setUp `auth_required=False`（桌面分支接受 workspace_dir，旧断言不变）。
- `test_settings_api.py`：见 Task 8 Step 4 具体配方（auth_required=False + patch `backend.main.save_settings` + 预置 `save_settings(Settings(), uid="local")`）。
- **✦ Codex R2-B2：review 锁/store 的既有测试**（如 `test_chat_runtime.py` 用 `_REVIEW_SESSION_STORE`/`get_independent_review_lock(project_id)` 的用例、`test_main_api.py` 直接操作 review store 的用例）：复合键后默认 local 的键变成 `tenant_project_key("local", project_id)`。统一迁移规则——**所有 direct review store/lock 测试操作**（`get_done_mtime`/`claim_first`/`discard`/`cleanup`/lock acquire/`_records[...]`）把裸 `project_id` 换成 `tenant_project_key("local", project_id)`。grep 定位：`grep -rn "_REVIEW_SESSION_STORE\|get_independent_review_lock\|_INDEPENDENT_REVIEW_LOCKS" tests/`。
- **✦ 通用（reload 隔离）**：任何 setUp 里 `importlib.reload(main)` 的测试基类，都要像 `AuthApiTestBase` 那样：reload 前 mock `backend.config.heal_stale_managed_model`，reload 后清 `chat._PROJECT_REQUEST_LOCKS/_CONVERSATION_STATE_LOCKS/_SEARCH_ROUTER_SINGLETON` + `independent_review._INDEPENDENT_REVIEW_LOCKS/_REVIEW_SESSION_STORE._records`（Codex R2-B4）。
- [ ] **Step 5: 跑确认 PASS + 全接口回归** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_main_api.py tests/test_stream_api.py tests/test_project_create_api.py tests/test_settings_api.py -v` → PASS
- [ ] **Step 6: Commit** — `git add backend/main.py tests/ && git commit -m "feat(w2b-b1): wire 23 endpoints via require_project/uid; migrate legacy tests; cross-tenant 404"`

---

## Task 12: bootstrap admin + invite 播种 + 桌面 loopback 启动断言

**Files:** Modify `backend/main.py`、`run_web.py`、`app.py`；Test `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试**（追加 `BootstrapSafetyTests(AuthApiTestBase)`）：
```python
    def test_bootstrap_admin_from_env(self):
        import importlib
        with mock.patch.dict(os.environ, {"CRA_BOOTSTRAP_ADMIN_USERNAME":"root","CRA_BOOTSTRAP_ADMIN_PASSWORD":"admin-pw"}):
            import backend.main as m; importlib.reload(m)
            from backend import accounts
            rec = accounts.get_user_by_username("root")
            self.assertIsNotNone(rec); self.assertTrue(rec["is_admin"])
    def test_assert_safe_startup(self):
        with self.assertRaises(SystemExit):
            self.m.assert_safe_startup(auth_required=False, host="0.0.0.0")
        self.m.assert_safe_startup(auth_required=False, host="127.0.0.1")
        self.m.assert_safe_startup(auth_required=True, host="0.0.0.0")
```
- [ ] **Step 2: 跑确认 FAIL** — `.venv/bin/python -m pytest tests/test_auth_api.py::BootstrapSafetyTests -v` → FAIL
- [ ] **Step 3: 实现**（main.py，accounts.init_db 之后）：
```python
def _bootstrap_admin():
    u = os.environ.get("CRA_BOOTSTRAP_ADMIN_USERNAME"); p = os.environ.get("CRA_BOOTSTRAP_ADMIN_PASSWORD")
    if u and p and accounts.get_user_by_username(u) is None:
        ensure_user_dirs(accounts.create_user(u, p, is_admin=True, must_change_password=True))

_bootstrap_admin()


def assert_safe_startup(auth_required: bool, host: str) -> None:
    if not auth_required and host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit(f"拒绝启动：auth 关闭时 host 必须 loopback，当前 {host!r}")
```
`run_web.py`：起服务前 `app.state.auth_required = True`、`assert_safe_startup(True, host)`。
`app.py`：起服务前 `app.state.auth_required = False`、`app.state.cookie_secure = False`、`settings.host = "127.0.0.1"`、`assert_safe_startup(False, settings.host)`。
- [ ] **Step 4: 跑确认 PASS** — `.venv/bin/python -m pytest tests/test_auth_api.py::BootstrapSafetyTests -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/main.py run_web.py app.py tests/test_auth_api.py && git commit -m "feat(w2b-b1): bootstrap admin + invite seed + loopback startup assertion"`

---

## Task 13: 搜索 project 级状态/缓存复合键 + quota 隔离测

**Files:** Modify `backend/chat.py`(search 调用处)；Test `tests/test_tenant_isolation.py`

- [ ] **Step 1: 写测试**（✦ Codex R2-B3：补真 quota 隔离测试，直接验证 state store 按传入键隔离 project-minute 配额 + cache）：
```python
class SearchCompositeKeyTests(unittest.TestCase):
    def _store(self):
        import os, tempfile
        from pathlib import Path
        from backend.search_state import SearchStateStore
        d = Path(os.path.realpath(tempfile.mkdtemp()))
        return SearchStateStore(runtime_state_path=d / "rs.json", cache_path=d / "c.json")

    def test_cache_key_isolated(self):
        from backend.tenant import tenant_project_key
        s = self._store()
        self.assertNotEqual(s._make_cache_key("q", tenant_project_key("uA", "proj-x")),
                            s._make_cache_key("q", tenant_project_key("uB", "proj-x")))

    def test_project_minute_quota_isolated_by_composite_key(self):
        from backend.tenant import tenant_project_key
        s = self._store()
        kA = tenant_project_key("uA", "proj-x"); kB = tenant_project_key("uB", "proj-x")
        kw = dict(project_window_seconds=60, project_limit=2, global_window_seconds=60, global_limit=100)
        # 注：try_acquire_search_slot 返回 None=取到 slot、返回 str(scope)=被某限额挡。实施前先到
        # backend/search_state.py 核对该返回语义，按真实语义调整断言。
        self.assertIsNone(s.try_acquire_search_slot(project_id=kA, **kw))
        self.assertIsNone(s.try_acquire_search_slot(project_id=kA, **kw))
        self.assertIsNotNone(s.try_acquire_search_slot(project_id=kA, **kw))  # uA 第3次被 project 限额挡
        self.assertIsNone(s.try_acquire_search_slot(project_id=kB, **kw))     # uB 同裸 proj-x 不受影响

    def test_global_limit_shared_across_users(self):  # Codex R3-NIT1：global 仍共享、不按 tenant 拆
        from backend.tenant import tenant_project_key
        s = self._store()
        kw = dict(project_window_seconds=60, project_limit=100, global_window_seconds=60, global_limit=2)
        self.assertIsNone(s.try_acquire_search_slot(project_id=tenant_project_key("uA", "p1"), **kw))
        self.assertIsNone(s.try_acquire_search_slot(project_id=tenant_project_key("uB", "p2"), **kw))
        self.assertIsNotNone(s.try_acquire_search_slot(project_id=tenant_project_key("uC", "p3"), **kw))  # 第3次被全局挡
```
- [ ] **Step 2: 跑确认 PASS（回归保护，非 fails-first）** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py::SearchCompositeKeyTests -v` → PASS。这两条**直接锁住 state store 按传入键隔离**（cache + project quota）；实际接通靠 Step 3 让调用方传复合键，由 Step 4 既有 search 回归套保护不回退。
- [ ] **Step 3: 调用方传复合键** — `backend/chat.py` grep `self._get_search_router().search(`，把 `project_id=<var>` 改 `project_id=tenant_project_key(self.uid, <var>)`：
```bash
grep -n "_get_search_router().search\|\.search(" backend/chat.py | grep project_id
```
（`SearchRouter.search` 内部把该字符串透传给 `state_store.get_cache(query, project_id=...)` 与 `try_acquire_search_slot(project_id=...)`——cache 与 project-minute quota 同时按复合键隔离；global-minute/cooldown 不受影响。）
- [ ] **Step 4: 跑回归** — `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_search_pool.py tests/test_search_state.py tests/test_chat_runtime.py -v` → PASS
- [ ] **Step 5: Commit** — `git add backend/chat.py tests/test_tenant_isolation.py && git commit -m "feat(w2b-b1): search project-scoped state/cache keyed by tenant_project_key"`

---

## Task 14: 前端 axios 全局配置 + authState

**Files:** Create `frontend/src/api.js`、`frontend/src/utils/authState.js`、`frontend/tests/authState.test.mjs`；Modify `frontend/src/main.jsx`

- [ ] **Step 1: 写失败测试** — Create `frontend/tests/authState.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reduceAuth, isAuthError } from '../src/utils/authState.js'

test('reduceAuth', () => {
  assert.equal(reduceAuth({ status: 'loading' }, { type: 'authed', user: { username: 'a' } }).status, 'authed')
  assert.equal(reduceAuth({ status: 'authed' }, { type: 'unauthed' }).status, 'login')
})
test('isAuthError', () => {
  assert.equal(isAuthError({ response: { status: 401 } }), true)
  assert.equal(isAuthError({ response: { status: 500 } }), false)
})
```
- [ ] **Step 2: 跑确认 FAIL** — `cd frontend && node --test tests/authState.test.mjs` → FAIL
- [ ] **Step 3: 实现** — `frontend/src/utils/authState.js`:
```javascript
export function reduceAuth(state, action) {
  switch (action.type) {
    case 'authed': return { status: 'authed', user: action.user }
    case 'unauthed': return { status: 'login', user: null }
    case 'loading': return { status: 'loading', user: null }
    default: return state
  }
}
export function isAuthError(error) {
  return Boolean(error && error.response && error.response.status === 401)
}
```
`frontend/src/api.js`:
```javascript
import axios from 'axios'
axios.defaults.withCredentials = true
let onUnauthed = null
export function setUnauthedHandler(fn) { onUnauthed = fn }
axios.interceptors.response.use((r) => r, (error) => {
  if (error?.response?.status === 401 && onUnauthed) onUnauthed()
  return Promise.reject(error)
})
```
`frontend/src/main.jsx` 顶部加 `import './api'`。
- [ ] **Step 4: 跑确认 PASS** — `cd frontend && node --test tests/authState.test.mjs` → PASS
- [ ] **Step 5: Commit** — `git add frontend/src/api.js frontend/src/utils/authState.js frontend/src/main.jsx frontend/tests/authState.test.mjs && git commit -m "feat(w2b-b1): frontend axios credentials/401 + authState"`

---

## Task 15: 前端项目创建闭环（去工作目录依赖）

**Files:** Modify `frontend/src/components/ProjectCreateModal.jsx`、`frontend/src/utils/projectCreatePayload.js`；Test `frontend/tests/projectCreatePayload.test.mjs`（既有则追加）

> **Codex R1-B3**：后端 web 拒 `workspace_dir`/`initial_material_paths`，前端必须停发。桌面已不再维护（spec D2），前端按 web-only 改：去掉目录/材料选择 + payload 不带这两字段。

- [ ] **Step 1: 写失败测试** — `frontend/tests/projectCreatePayload.test.mjs`（既有则追加）：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { prepareProjectCreatePayload } from '../src/utils/projectCreatePayload.js'

test('payload omits workspace_dir and initial_material_paths (web)', () => {
  const p = prepareProjectCreatePayload({ theme: '战略分析', project_type: 'strategy', deadline: '2026-12-31', expected_length: '1万字' })
  assert.equal('workspace_dir' in p, false)
  assert.equal('initial_material_paths' in p, false)
  assert.equal(p.name, '战略分析')
})
```
- [ ] **Step 2: 跑确认 FAIL** — `cd frontend && node --test tests/projectCreatePayload.test.mjs` → FAIL
- [ ] **Step 3: 实现**

`frontend/src/utils/projectCreatePayload.js` 去掉 `workspace_dir`/`initial_material_paths`：
```javascript
  return {
    name: theme,
    project_type: formData.project_type || "strategy-consulting",
    theme,
    deadline: (formData.deadline || "").trim(),
    expected_length: (formData.expected_length || "").trim(),
    notes: "",
  };
```
`frontend/src/components/ProjectCreateModal.jsx`：移除「选择工作目录」「选择目录内材料」两块 UI（行约 50-85）+ 相关 state（`workspace_dir`/`initial_material_paths`）+ 对 `/api/system/select-workspace-*` 的调用。材料改由项目内「上传材料」流程（既有 `/materials/upload`）后续添加。
- [ ] **Step 4: 跑确认 PASS + build** — `cd frontend && node --test tests/projectCreatePayload.test.mjs && npm run build` → PASS + build OK
- [ ] **Step 5: Commit** — `git add frontend/src/components/ProjectCreateModal.jsx frontend/src/utils/projectCreatePayload.js frontend/tests/projectCreatePayload.test.mjs && git commit -m "feat(w2b-b1): frontend project creation drops client workspace_dir/material paths"`

---

## Task 16: 前端登录页 + App 登录态门

**Files:** Create `frontend/src/components/Login.jsx`；Modify `frontend/src/App.jsx`；Test `frontend/tests/login.source.test.mjs`

- [ ] **Step 1: 写失败测试** — Create `frontend/tests/login.source.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('Login posts auth endpoints', () => {
  const s = readFileSync(new URL('../src/components/Login.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/login/); assert.match(s, /\/api\/auth\/register/); assert.match(s, /invite_code/)
})
test('App gates on /api/auth/me', () => {
  const s = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/me/); assert.match(s, /Login/); assert.match(s, /setUnauthedHandler/)
})
```
- [ ] **Step 2: 跑确认 FAIL** — `cd frontend && node --test tests/login.source.test.mjs` → FAIL
- [ ] **Step 3: 实现** — Create `frontend/src/components/Login.jsx`（用户名+密码+注册时邀请码；提交 register?→login→me→onAuthed）：
```jsx
import React, { useState } from 'react'
import axios from 'axios'

export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [invite, setInvite] = useState(''); const [err, setErr] = useState('')
  const submit = async (e) => {
    e.preventDefault(); setErr('')
    try {
      if (mode === 'register') await axios.post('/api/auth/register', { username, password, invite_code: invite })
      await axios.post('/api/auth/login', { username, password })
      onAuthed((await axios.get('/api/auth/me')).data)
    } catch (e2) { setErr(e2?.response?.data?.detail || '操作失败，请重试') }
  }
  return (
    <div className="flex items-center justify-center h-screen bg-[#0f0f23]">
      <form onSubmit={submit} className="bg-[#1a1a2e] p-8 rounded-xl w-80 border border-[#2a2a4a]">
        <h1 className="text-lg font-semibold text-[#e2e2f0] mb-4">咨询报告助手 · {mode === 'login' ? '登录' : '注册'}</h1>
        <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="密码" value={password} onChange={(e) => setPassword(e.target.value)} />
        {mode === 'register' && <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="邀请码" value={invite} onChange={(e) => setInvite(e.target.value)} />}
        {err && <div className="text-red-400 text-sm mb-3">{err}</div>}
        <button type="submit" className="w-full bg-blue-600 text-white py-2 rounded mb-2">{mode === 'login' ? '登录' : '注册并登录'}</button>
        <button type="button" className="w-full text-[#8888a8] text-sm" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  )
}
```
`frontend/src/App.jsx`：import `Login` + `{ setUnauthedHandler } from './api'`；在 `App()` 加 `authUser`/`authChecked` state + 起手 effect：
```jsx
  const [authUser, setAuthUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  useEffect(() => {
    setUnauthedHandler(() => setAuthUser(null))
    axios.get('/api/auth/me').then((r) => { setAuthUser(r.data); return initializeApp() }).catch(() => {}).finally(() => setAuthChecked(true))
  }, [])
```
在 `if (loading)` 之前插：
```jsx
  if (!authChecked) return <div className="flex items-center justify-center h-screen"><div className="text-[#8888a8]">加载中...</div></div>
  if (!authUser) return <Login onAuthed={(u) => { setAuthUser(u); initializeApp() }} />
```
并把 `authUser` + `onLoggedOut={() => setAuthUser(null)}` 透传给 `<Sidebar>`。
- [ ] **Step 4: 跑确认 PASS + build** — `cd frontend && node --test tests/login.source.test.mjs && npm run build` → PASS + build OK
- [ ] **Step 5: Commit** — `git add frontend/src/components/Login.jsx frontend/src/App.jsx frontend/tests/login.source.test.mjs && git commit -m "feat(w2b-b1): frontend login/register + App auth gate"`

---

## Task 17: 前端左下角账号块（用户名 + 登出）

**Files:** Modify `frontend/src/components/Sidebar.jsx`；Test `frontend/tests/sidebar.source.test.mjs`

- [ ] **Step 1: 写失败测试** — Create `frontend/tests/sidebar.source.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('Sidebar account block', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/logout/); assert.match(s, /authUser/); assert.match(s, /登出/)
})
```
- [ ] **Step 2: 跑确认 FAIL** — `cd frontend && node --test tests/sidebar.source.test.mjs` → FAIL
- [ ] **Step 3: 实现** — `Sidebar.jsx` 顶部 `import axios from 'axios'`；props 加 `authUser, onLoggedOut`；在底部「⚙ 连接设置」块上方插：
```jsx
        {authUser && (
          <div className="mb-2 px-3 py-2 rounded-lg bg-[#15162d] border border-[#2f3158] flex items-center justify-between">
            <span className="text-xs text-[#e2e2f0] truncate">{authUser.username}</span>
            <button onClick={async () => { await axios.post('/api/auth/logout'); onLoggedOut?.() }}
                    className="text-[11px] text-[#8888a8] hover:text-[#e2e2f0] ml-2">登出</button>
          </div>
        )}
```
- [ ] **Step 4: 跑确认 PASS + build** — `cd frontend && node --test tests/sidebar.source.test.mjs && npm run build` → PASS + build OK
- [ ] **Step 5: Commit** — `git add frontend/src/components/Sidebar.jsx frontend/tests/sidebar.source.test.mjs && git commit -m "feat(w2b-b1): sidebar account block (username + logout)"`

---

## Task 18: 全量回归 + cutover

**Files:** Create `docs/superpowers/cutover_report_2026-06-21_w2b-b1.md`

- [ ] **Step 1: 后端全量** — `.venv/bin/python -m pytest tests/ -q`（mac 上 4 个 realpath 环境差异除外）
- [ ] **Step 2: 前端全量 + build** — `cd frontend && node --test tests/ && npm run build`
- [ ] **Step 3: 手动 smoke** — `CRA_DATA_ROOT=$(mktemp -d) CRA_INVITE_CODE=JOIN .venv/bin/python run_web.py` → 浏览器注册→登录→建项目→无痕窗口注册第二账号验隔离→登出。
- [ ] **Step 4: cutover** — Create `docs/superpowers/cutover_report_2026-06-21_w2b-b1.md`（18 task、回归、偏离、B2/B3 衔接点）。**必写清（Codex R2-NIT1）**：B1 后 `normalize_settings_payload` 仍强制 `mode="managed"`，**custom 模式尚不可用**（per-uid 已隔离存储、但激活 + SSRF 防护归 B3），别误以为 B1 后能切 custom。
- [ ] **Step 5: Commit** — `git add docs/superpowers/cutover_report_2026-06-21_w2b-b1.md && git commit -m "docs(w2b-b1): cutover report"`

---

## 自查（Self-Review）

- **Spec §13 B1 覆盖**：租户路径(T2) + 引擎/handler 工厂(T7) + 复合键锁含 skill.py(T6) + 搜索复合键(T13) + accounts/argon2/auth(T1,3,4,5,9) + per-uid settings(T8) + 中间件+require_project+23 接口(T7,11) + bootstrap+loopback(T12) + 创建闭环(T10,15) + 前端登录态门+账号块(T14,16,17)。验收门=跨租户隔离(T11)。✅
- **B1 不含**（B2/B3）：中央计费/配额、admin 面板、CSRF/SSRF/CORS 收紧、custom 模式激活。`/api/settings`/`/api/models/list` 仅加 uid 归属 + per-uid 存储（custom 激活+SSRF 归 B3）。
- **类型一致**：`tenant_project_key(uid,pid)->str` 全程；`ProjectScope{uid,project_id(canonical),engine,project_record,lock_key}`(T7 定义,T11 用)；`get_chat_handler(uid,project_id)`(T7,T11)；`SkillEngine(projects_dir,skill_dir,uid="local")`(T6)；`ChatHandler(settings,skill_engine,uid="local")`(T6)；`load/save_settings(uid)`(T8)；accounts 函数名跨 task 一致。
- **Codex R1 闭环**：8 BLOCKER 全落任务（见头部「Codex R1 已修」）；既有测试迁移有 T11-Step4 具体配方；TDD 顺序重排（创建闭环 T10 先于跨租户 T11）。
- **既有硬约束**：DeepSeek 官渠/S5 审查/R3 文件锁/R5 方法论——本 plan 只加鉴权层 + 复合键，不碰 provider 序列化/审查内容/方法论装配；chat_stream 不变式见 T7 note。

---

## Execution Handoff

Plan 已修订（纳入 Codex R1 八条）。两种执行：
1. **Subagent-Driven（推荐）** — 每 task 派 Claude agent 实现、task 间 review、本项目 SOP（实施 Claude agent + 每 commit Codex review）。
2. **Inline Execution** — 本会话分批 + checkpoint。

但**先把这版修订过 Codex 复审到 APPROVED 再执行**。选哪个执行方式？
