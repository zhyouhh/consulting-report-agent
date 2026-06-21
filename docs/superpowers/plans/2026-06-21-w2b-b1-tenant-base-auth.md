# W2-B / B1 多租户基座 + 鉴权 + 项目创建闭环 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单用户应用改成「登录后每个用户各自工作区隔离」的多租户基座——注册/登录/会话、按 uid 分目录、统一归属卡点 `require_project`、复合键锁、项目创建服务端分配工作区，做完即「A 不可触达 B 的任何数据」。

**Architecture:** 数据落 `<data-root>/users/<uid>/...`；登录态从 httpOnly cookie 认出 uid；每请求经 `get_skill_engine(uid)` 拿到绑定该 uid 数据根的引擎实例，项目级接口统一过 `require_project(uid, ref)`（canonicalize 到 `rec["id"]`、查不到即 404 = 天然归属）；进程锁/store/搜索 project 级状态全改复合键 `tenant_project_key(uid, cid)`。引擎业务逻辑不动。本 plan 是 spec `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md` 的 B1 阶段（B2 计费 / B3 admin+安全硬化 另两 plan）。

**Tech Stack:** FastAPI + SQLite(`sqlite3` 标准库, WAL) + `argon2-cffi`（密码哈希）+ React/axios（前端）+ unittest/TestClient（测试）。

**验收门（B1 完成判据）**：跨租户隔离回归全绿——A 建项目、B 用该 project_id/项目名访问任一接口都 404；未登录所有 `/api/*`(白名单除外)→401；同项目 id 与 name 两种 ref 不产生双锁。

---

## File Structure

**新增**
- `backend/tenant.py` — 数据根 + per-uid 路径拼接 + `tenant_project_key`。叶子模块，只依赖 `config.get_user_config_dir`，绝不 import chat/skill/main。
- `backend/accounts.py` — SQLite 账号层（`users`/`sessions`/`app_config` 表 + argon2 密码 + 查询函数）。叶子模块，只依赖 `tenant`。
- `tests/test_tenant.py` / `tests/test_accounts.py` / `tests/test_auth_api.py` / `tests/test_tenant_isolation.py`
- `frontend/src/components/Login.jsx` — 登录/注册页
- `frontend/src/utils/authState.js` — 登录态纯函数 + `frontend/tests/authState.test.mjs`
- `frontend/src/api.js` — axios 全局配置（withCredentials + 401 拦截）

**修改**
- `backend/config.py` — 新增 `data_root()` 用 env `CRA_DATA_ROOT`（薄改，复用 `get_user_config_dir` 作默认）。
- `backend/main.py` — `get_skill_engine(uid)`/`get_chat_handler(uid, pid)` 工厂；`ProjectScope`+`get_current_uid`+`require_project`+`get_current_admin` 依赖；`/api/auth/*` 端点；23 接口接入归类表；项目创建闭环；bootstrap admin + invite 播种；启动安全断言。
- `backend/chat.py` — `_get_project_request_lock` 改收复合键；`ChatHandler` 持 `self.uid` 并内部用复合键；`_get_search_router().search(...)` 传 `scope` 复合键。
- `backend/independent_review.py` — review 锁 + `ReviewSessionStore` 改复合键。
- `backend/models.py` — `ProjectInfo.workspace_dir` 改 `Optional`（web 服务端分配）。
- `frontend/src/App.jsx` — 起手查 `/api/auth/me`，401 渲染 `Login`。
- `frontend/src/main.jsx` — import `./api`（装 axios 拦截）。
- `frontend/src/components/Sidebar.jsx` — 左下角账号块（用户名 + 登出）。
- `requirements.txt` — 加 `argon2-cffi`。

---

## Task 1: 加 argon2 依赖

**Files:**
- Modify: `requirements.txt`
- Test: `tests/test_requirements.py`（已存在，验依赖清单）

- [ ] **Step 1: 写失败测试**

在 `tests/test_requirements.py` 末尾追加：
```python
def test_argon2_dependency_pinned():
    text = Path(__file__).resolve().parent.parent.joinpath("requirements.txt").read_text(encoding="utf-8")
    assert "argon2-cffi==" in text, "argon2-cffi must be pinned for password hashing (W2-B B1)"
```
（若该测试文件无 `from pathlib import Path` 则在顶部补 import。）

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_requirements.py -k argon2 -v`
Expected: FAIL（`argon2-cffi==` 不在 requirements.txt）

- [ ] **Step 3: 加依赖**

`requirements.txt` 末尾追加一行：
```
argon2-cffi==23.1.0
```
并安装：`uv pip install --python .venv/bin/python argon2-cffi==23.1.0`

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_requirements.py -k argon2 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_requirements.py
git commit -m "feat(w2b-b1): add argon2-cffi for password hashing"
```

---

## Task 2: `backend/tenant.py` — 数据根 + per-uid 路径 + 复合键

**Files:**
- Create: `backend/tenant.py`
- Modify: `backend/config.py`（加 `data_root`）
- Test: `tests/test_tenant.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tenant.py`:
```python
import os
import unittest
from pathlib import Path
from unittest import mock

from backend import tenant


class TenantPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(__import__("tempfile").mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        __import__("shutil").rmtree(self._tmp, ignore_errors=True)

    def test_data_root_honors_env(self):
        self.assertEqual(Path(os.path.realpath(tenant.data_root())), self._tmp)

    def test_user_paths_under_uid(self):
        self.assertEqual(tenant.user_projects_dir("u1"), self._tmp / "users" / "u1" / "projects")
        self.assertEqual(tenant.user_config_path("u1"), self._tmp / "users" / "u1" / "config.json")
        self.assertEqual(tenant.app_db_path(), self._tmp / "app.db")

    def test_tenant_project_key_sanitizes_and_is_stable(self):
        self.assertEqual(tenant.tenant_project_key("u1", "proj-abc"), "u1::proj-abc")
        # 分隔符注入被消毒，不同 (uid,pid) 不得碰撞
        self.assertNotEqual(
            tenant.tenant_project_key("a", "b::c"),
            tenant.tenant_project_key("a::b", "c"),
        )
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_tenant.py -v`
Expected: FAIL（`No module named 'backend.tenant'`）

- [ ] **Step 3: 实现 config.data_root + tenant.py**

`backend/config.py`：在 `get_user_config_dir` 之后加：
```python
def data_root() -> Path:
    """多租户数据根；env CRA_DATA_ROOT 覆盖，默认沿用单用户目录。"""
    import os
    env = os.environ.get("CRA_DATA_ROOT")
    root = Path(env).expanduser() if env else get_user_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root
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
    """唯一中央复合键（JSON-safe 字符串，同时用于内存 dict 键与持久化 JSON 键）。
    消毒 ':' 防分隔符注入；uid/pid 实际不含 ':'，此为纵深防御。任何处禁止手拼。"""
    safe_uid = str(uid).replace(":", "_")
    safe_pid = str(project_id).replace(":", "_")
    return f"{safe_uid}::{safe_pid}"
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_tenant.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/tenant.py backend/config.py tests/test_tenant.py
git commit -m "feat(w2b-b1): tenant path layer + tenant_project_key composite key"
```

---

## Task 3: `backend/accounts.py` — users 表 + argon2 密码

**Files:**
- Create: `backend/accounts.py`
- Test: `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_accounts.py`:
```python
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import accounts


class AccountsUserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()
        accounts.init_db()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_and_verify_user(self):
        uid = accounts.create_user("alice", "s3cret-pw", is_admin=False)
        self.assertTrue(uid)
        rec = accounts.get_user_by_username("alice")
        self.assertEqual(rec["uid"], uid)
        self.assertNotIn("s3cret-pw", rec["password_hash"])  # 不存明文
        self.assertTrue(accounts.verify_user_password("alice", "s3cret-pw"))
        self.assertFalse(accounts.verify_user_password("alice", "wrong"))

    def test_duplicate_username_rejected(self):
        accounts.create_user("bob", "pw1")
        with self.assertRaises(accounts.UsernameTakenError):
            accounts.create_user("bob", "pw2")

    def test_get_user_by_uid_and_change_password(self):
        uid = accounts.create_user("carol", "old-pw")
        accounts.set_user_password(uid, "new-pw")
        self.assertFalse(accounts.verify_user_password("carol", "old-pw"))
        self.assertTrue(accounts.verify_user_password("carol", "new-pw"))
        self.assertEqual(accounts.get_user_by_uid(uid)["username"], "carol")
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: FAIL（`No module named 'backend.accounts'`）

- [ ] **Step 3: 实现 accounts.py（users 部分）**

Create `backend/accounts.py`:
```python
"""SQLite 账号层（叶子模块；只依赖 tenant）。users / sessions / app_config。"""
import sqlite3
import uuid
from datetime import datetime, timezone
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


def create_user(username: str, password: str, is_admin: bool = False,
                must_change_password: bool = False) -> str:
    uid = uuid.uuid4().hex
    pwhash = _PH.hash(password)
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users(uid, username, password_hash, is_admin, must_change_password, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (uid, username, pwhash, int(is_admin), int(must_change_password), _now()),
            )
    except sqlite3.IntegrityError as e:
        raise UsernameTakenError(username) from e
    return uid


def _row_to_user(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["is_admin"] = bool(d["is_admin"])
    d["disabled"] = bool(d["disabled"])
    d["must_change_password"] = bool(d["must_change_password"])
    return d


def get_user_by_username(username: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_uid(uid: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    return _row_to_user(row) if row else None


def verify_user_password(username: str, password: str) -> bool:
    rec = get_user_by_username(username)
    if not rec:
        return False
    try:
        return _PH.verify(rec["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def set_user_password(uid: str, new_password: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE uid=?",
                     (_PH.hash(new_password), uid))
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(w2b-b1): accounts SQLite layer — users + argon2 password"
```

---

## Task 4: accounts — sessions（建/查/删/过期）

**Files:**
- Modify: `backend/accounts.py`
- Test: `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_accounts.py`）

```python
class AccountsSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()
        accounts.init_db()
        self.uid = accounts.create_user("dave", "pw")

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_session_roundtrip(self):
        token = accounts.create_session(self.uid, ttl_days=30, ip="1.2.3.4", ua="ua")
        self.assertTrue(token)
        self.assertEqual(accounts.get_session_uid(token), self.uid)

    def test_session_stored_as_hash_not_plaintext(self):
        token = accounts.create_session(self.uid)
        with accounts._connect() as conn:
            rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
        self.assertTrue(all(token not in r["token_hash"] for r in rows))

    def test_expired_session_rejected(self):
        token = accounts.create_session(self.uid, ttl_days=-1)  # 已过期
        self.assertIsNone(accounts.get_session_uid(token))

    def test_delete_session_and_delete_all_user_sessions(self):
        t1 = accounts.create_session(self.uid)
        t2 = accounts.create_session(self.uid)
        accounts.delete_session(t1)
        self.assertIsNone(accounts.get_session_uid(t1))
        self.assertEqual(accounts.get_session_uid(t2), self.uid)
        accounts.delete_user_sessions(self.uid)
        self.assertIsNone(accounts.get_session_uid(t2))

    def test_disabled_user_session_rejected(self):
        token = accounts.create_session(self.uid)
        accounts.set_user_disabled(self.uid, True)
        self.assertIsNone(accounts.get_session_uid(token))
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_accounts.py::AccountsSessionTests -v`
Expected: FAIL（`create_session` 不存在）

- [ ] **Step 3: 实现 sessions**

`backend/accounts.py` 顶部 import 补 `import hashlib, secrets` 与 `from datetime import timedelta`。追加：
```python
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(uid: str, ttl_days: int = 30, ip: str = "", ua: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=ttl_days)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, uid, created_at, expires_at, created_ip, user_agent, last_seen)"
            " VALUES(?,?,?,?,?,?,?)",
            (_hash_token(token), uid, now.isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds"), ip, ua, now.isoformat(timespec="seconds")),
        )
    return token


def get_session_uid(token: str) -> Optional[str]:
    """返回有效会话的 uid；过期/不存在/用户被禁用 → None。"""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT s.uid AS uid, s.expires_at AS expires_at, u.disabled AS disabled "
            "FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=?",
            (_hash_token(token),),
        ).fetchone()
    if not row:
        return None
    if row["disabled"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return None
    return row["uid"]


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))


def delete_user_sessions(uid: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE uid=?", (uid,))


def set_user_disabled(uid: str, disabled: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET disabled=? WHERE uid=?", (int(disabled), uid))
    if disabled:
        delete_user_sessions(uid)
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_accounts.py::AccountsSessionTests -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(w2b-b1): accounts sessions — token hash, expiry, disable revocation"
```

---

## Task 5: accounts — app_config（邀请码）

**Files:**
- Modify: `backend/accounts.py`
- Test: `tests/test_accounts.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
class AccountsConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp)})
        self._env.start()
        accounts.init_db()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_app_config_get_set_default(self):
        self.assertEqual(accounts.get_config("invite_code", "fallback"), "fallback")
        accounts.set_config("invite_code", "JOIN-2026")
        self.assertEqual(accounts.get_config("invite_code"), "JOIN-2026")

    def test_seed_config_if_absent_is_idempotent(self):
        accounts.seed_config_if_absent("invite_code", "SEED1")
        accounts.seed_config_if_absent("invite_code", "SEED2")  # 不覆盖
        self.assertEqual(accounts.get_config("invite_code"), "SEED1")
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_accounts.py::AccountsConfigTests -v`
Expected: FAIL（`get_config` 不存在）

- [ ] **Step 3: 实现 app_config**（追加到 accounts.py）

```python
def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_config(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def seed_config_if_absent(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES(?,?)", (key, value))
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_accounts.py::AccountsConfigTests -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/accounts.py tests/test_accounts.py
git commit -m "feat(w2b-b1): accounts app_config — invite code get/set/seed"
```

---

## Task 6: 复合键锁 — chat / independent_review

**Files:**
- Modify: `backend/chat.py:270-278`（`_get_project_request_lock`）、`backend/chat.py:392`（`ChatHandler.__init__` 加 uid）、`backend/chat.py:1165`（方法）
- Modify: `backend/independent_review.py:817`（review 锁）、`830`（store records）
- Test: `tests/test_tenant_isolation.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tenant_isolation.py`:
```python
import unittest
from backend.tenant import tenant_project_key
from backend import chat as chat_mod
from backend import independent_review as ir_mod


class CompositeLockKeyTests(unittest.TestCase):
    def test_two_users_same_project_id_get_distinct_locks(self):
        k_a = tenant_project_key("userA", "proj-x")
        k_b = tenant_project_key("userB", "proj-x")
        lock_a = chat_mod._get_project_request_lock(k_a)
        lock_b = chat_mod._get_project_request_lock(k_b)
        self.assertIsNot(lock_a, lock_b)
        # 同键复用同一把锁
        self.assertIs(chat_mod._get_project_request_lock(k_a), lock_a)

    def test_review_lock_and_store_keyed_by_composite(self):
        k_a = tenant_project_key("userA", "proj-x")
        k_b = tenant_project_key("userB", "proj-x")
        self.assertIsNot(ir_mod.get_independent_review_lock(k_a),
                         ir_mod.get_independent_review_lock(k_b))
```

- [ ] **Step 2: 跑测试确认 FAIL/PASS 现状**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py -v`
Expected: PASS（现签名已是 `(key: str)`，但语义上调用方仍传裸 project_id）——本任务核心是**让调用方传复合键**。先确认锁工厂本身按传入键区分（应 PASS），再改调用方。

- [ ] **Step 3: 让 ChatHandler 持 uid 并内部用复合键**

`backend/chat.py` `ChatHandler.__init__`（行 392）签名改为：
```python
def __init__(self, settings: Settings, skill_engine: SkillEngine, uid: str = "local"):
    self.uid = uid
    self.settings = settings
    self.skill_engine = skill_engine
    # ...（其余不变）
```
`ChatHandler._get_project_request_lock`（行 1165）改为用复合键：
```python
def _get_project_request_lock(self, project_id: str):
    from .tenant import tenant_project_key
    return _get_project_request_lock(tenant_project_key(self.uid, project_id))
```
同理，ChatHandler 内所有 `get_independent_review_lock(project_id)` / `_REVIEW_SESSION_STORE` 调用、`_CONVERSATION_STATE_LOCKS` 取用，凡传 `project_id` 处一律改传 `tenant_project_key(self.uid, project_id)`。用 grep 定位：
```bash
grep -n "project_id" backend/chat.py | grep -Ei "lock|review_session|conversation_state|_records"
```
逐处把裸 `project_id` 换成 `tenant_project_key(self.uid, project_id)`。`ReviewSessionStore` 的 records 键、`get_independent_review_lock` 的入参同样接受这个复合字符串（它们签名已是 `str`，无需改 independent_review.py 内部，只需调用方传复合键）。

- [ ] **Step 4: 跑回归确认无破坏**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_chat_runtime.py tests/test_independent_review.py -v`
Expected: PASS（隔离测试 + 既有 chat/review 回归全绿；既有测试若直接构造 ChatHandler 未传 uid，默认 `"local"` 保持旧行为）

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py backend/independent_review.py tests/test_tenant_isolation.py
git commit -m "feat(w2b-b1): composite (uid,project_id) keys for locks + review store"
```

---

## Task 7: per-uid 引擎/handler 工厂 + ProjectScope + 鉴权依赖

**Files:**
- Modify: `backend/main.py`（行 74-89 工厂；新增依赖）
- Test: `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_auth_api.py`:
```python
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


class AuthApiTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp), "CRA_INVITE_CODE": "JOIN"})
        self._env.start()
        import importlib
        from backend import accounts, config
        importlib.reload(config); importlib.reload(accounts)
        import backend.main as main_module
        importlib.reload(main_module)
        self.main = main_module
        main_module.app.state.auth_required = True
        self.client = TestClient(main_module.app)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class UnauthenticatedTests(AuthApiTestBase):
    def test_projects_requires_auth(self):
        r = self.client.get("/api/projects")
        self.assertEqual(r.status_code, 401)

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::UnauthenticatedTests -v`
Expected: FAIL（`/api/projects` 现返回 200，无鉴权）

- [ ] **Step 3: 实现工厂 + 依赖**

`backend/main.py` 顶部 import 区追加：
```python
from dataclasses import dataclass
from fastapi import Depends, Response
from .tenant import user_projects_dir, ensure_user_dirs, tenant_project_key
from . import accounts
```
把全局 `skill_engine = SkillEngine(...)` + `get_chat_handler` 改为 per-uid 工厂（替换行 74-89）：
```python
accounts.init_db()
accounts.seed_config_if_absent("invite_code", os.environ.get("CRA_INVITE_CODE", "change-me"))

_engines: dict[str, SkillEngine] = {}
_engines_guard = threading.Lock()
_chat_handlers: dict[tuple[str, str], ChatHandler] = {}
_settings_lock = threading.Lock()
_desktop_bridge = None
SESSION_COOKIE = "cra_session"


def get_skill_engine(uid: str) -> SkillEngine:
    with _engines_guard:
        eng = _engines.get(uid)
        if eng is None:
            ensure_user_dirs(uid)
            eng = SkillEngine(user_projects_dir(uid), settings.skill_dir)
            _engines[uid] = eng
        return eng


def get_chat_handler(uid: str, project_id: str) -> ChatHandler:
    with _settings_lock:
        key = (uid, project_id)
        if key not in _chat_handlers:
            _chat_handlers[key] = ChatHandler(settings, get_skill_engine(uid), uid=uid)
        return _chat_handlers[key]
```
（顶部需 `import os`。）追加鉴权依赖：
```python
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
    rec = eng.get_project_record(project_id)   # 现认 id 或 name 别名
    if rec is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    cid = rec["id"]                            # canonicalize（R3-B1）
    return ProjectScope(uid=uid, project_id=cid, engine=eng, project_record=rec,
                        lock_key=tenant_project_key(uid, cid))
```
默认 `app.state.auth_required` 在模块底部（mount 之前）设 `app.state.auth_required = True`（web 默认；桌面入口会覆盖为 False，见 Task 11）。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::UnauthenticatedTests -v`
Expected: PASS——但需先把 `/api/projects` 接上 `Depends(get_current_uid)`（见 Task 9）。**本任务先让依赖与工厂就位**；`test_health_is_public` 应 PASS，`test_projects_requires_auth` 待 Task 9 接线后转绿。先只断言 health：
Run: `.venv/bin/python -m pytest tests/test_auth_api.py::UnauthenticatedTests::test_health_is_public -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(w2b-b1): per-uid engine/chat factories + get_current_uid/require_project/ProjectScope"
```

---

## Task 8: `/api/auth/*` 端点 + 中间件白名单

**Files:**
- Modify: `backend/main.py`（新增 auth 端点 + 中间件）
- Test: `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试**（追加到 test_auth_api.py）

```python
class AuthFlowTests(AuthApiTestBase):
    def _register(self, u="alice", p="pw-123456", code="JOIN"):
        return self.client.post("/api/auth/register", json={"username": u, "password": p, "invite_code": code})

    def test_register_requires_valid_invite(self):
        self.assertEqual(self._register(code="WRONG").status_code, 403)
        self.assertEqual(self._register().status_code, 200)

    def test_login_sets_cookie_and_me_works(self):
        self._register()
        r = self.client.post("/api/auth/login", json={"username": "alice", "password": "pw-123456"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("cra_session", r.cookies)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "alice")

    def test_logout_clears_session(self):
        self._register()
        self.client.post("/api/auth/login", json={"username": "alice", "password": "pw-123456"})
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_wrong_password_401(self):
        self._register()
        r = self.client.post("/api/auth/login", json={"username": "alice", "password": "bad"})
        self.assertEqual(r.status_code, 401)
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::AuthFlowTests -v`
Expected: FAIL（`/api/auth/register` 404）

- [ ] **Step 3: 实现 auth 端点**

`backend/main.py` 追加（用 `BaseModel` 定义入参）：
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
    secure = bool(getattr(request.app.state, "cookie_secure", False))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=secure, max_age=30 * 24 * 3600, path="/")
    return {"status": "ok"}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        accounts.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(uid: str = Depends(get_current_uid)):
    rec = accounts.get_user_by_uid(uid)
    if not rec:
        raise HTTPException(status_code=401, detail="未登录")
    return {"uid": uid, "username": rec["username"], "is_admin": rec["is_admin"],
            "must_change_password": rec["must_change_password"]}


@app.post("/api/auth/change-password")
def auth_change_password(payload: ChangePwPayload, uid: str = Depends(get_current_uid)):
    rec = accounts.get_user_by_uid(uid)
    if not accounts.verify_user_password(rec["username"], payload.old_password):
        raise HTTPException(status_code=401, detail="原密码错误")
    accounts.set_user_password(uid, payload.new_password)
    accounts.delete_user_sessions(uid)   # 吊销其它会话
    return {"status": "ok"}
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::AuthFlowTests -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(w2b-b1): /api/auth register/login/logout/me/change-password + invite gate"
```

---

## Task 9: 23 接口接入归类表（uid-scoped / require_project）

**Files:**
- Modify: `backend/main.py`（逐接口加 `Depends`）
- Test: `tests/test_tenant_isolation.py`

**归类表（逐项接入真值，目标守门）**：

| 接口 | 改法 |
|---|---|
| `GET /api/projects` | 加 `uid=Depends(get_current_uid)` → `get_skill_engine(uid).list_projects()` |
| `POST /api/projects` | 加 `uid=Depends(get_current_uid)`（创建闭环见 Task 10） |
| `GET/POST /api/settings` | 加 `uid=Depends(get_current_uid)`（per-user settings 深化在 B2/N5；B1 先归属隔离） |
| `POST /api/models/list` | 加 `uid=Depends(get_current_uid)`（SSRF 在 B3） |
| `GET .../{project_id}/materials`、`POST .../materials/upload`、`DELETE .../materials/{mid}` | 参数 `scope: ProjectScope = Depends(require_project)`，body 内 `scope.engine`/`scope.project_id` |
| `GET .../{project_id}/files`、`GET/POST .../files/{path}` | 同上 `Depends(require_project)` |
| `GET .../{project_id}/workspace` | `Depends(require_project)`（样板见 Step 3） |
| `POST .../independent-review/stream`、`POST .../discard` | `Depends(require_project)`；review 锁/store 用 `scope.lock_key` |
| `POST .../export-draft` | `Depends(require_project)` |
| `DELETE /api/projects/{project_id}` | `Depends(require_project)` |
| `POST .../checkpoints/{name}` | `Depends(require_project)` |
| `GET/DELETE .../conversation` | `Depends(require_project)` |
| `POST /api/chat`、`POST /api/chat/stream` | **body 带 project_id**：先 `uid=Depends(get_current_uid)`，函数体内 `scope=require_project(chat_request.project_id, uid)`，再 `get_chat_handler(scope.uid, scope.project_id)` |
| `select-workspace-*`、`materials/select-from-workspace` | 桌面专用，保留（web 503）；前端隐藏 |
| `GET /api/health` | 不动（public 白名单） |

- [ ] **Step 1: 写失败测试**（追加到 test_tenant_isolation.py，用 TestClient）

```python
import os, shutil, tempfile
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient

class CrossTenantApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {"CRA_DATA_ROOT": str(self._tmp), "CRA_INVITE_CODE": "JOIN"})
        self._env.start()
        import importlib
        from backend import accounts, config
        importlib.reload(config); importlib.reload(accounts)
        import backend.main as m; importlib.reload(m)
        m.app.state.auth_required = True
        self.m = m
        self.A = TestClient(m.app); self.B = TestClient(m.app)
        for c in (self.A, self.B):
            pass
        self._login(self.A, "alice"); self._login(self.B, "bob")

    def tearDown(self):
        self._env.stop(); shutil.rmtree(self._tmp, ignore_errors=True)

    def _login(self, client, user):
        client.post("/api/auth/register", json={"username": user, "password": "pw-123456", "invite_code": "JOIN"})
        client.post("/api/auth/login", json={"username": user, "password": "pw-123456"})

    def test_b_cannot_read_a_project(self):
        created = self.A.post("/api/projects", json={
            "name": "A机密", "project_type": "strategy", "theme": "t",
            "deadline": "2026-12-31", "expected_length": "1万字"}).json()
        pid = created["project_id"]
        # A 自己能拿
        self.assertEqual(self.A.get(f"/api/projects/{pid}/workspace").status_code, 200)
        # B 拿 A 的 project_id → 404
        self.assertEqual(self.B.get(f"/api/projects/{pid}/workspace").status_code, 404)
        # B 的项目列表里看不到 A 的项目
        self.assertEqual(self.B.get("/api/projects").json(), [])

    def test_name_ref_does_not_leak(self):
        self.A.post("/api/projects", json={
            "name": "A机密", "project_type": "strategy", "theme": "t",
            "deadline": "2026-12-31", "expected_length": "1万字"})
        # B 用项目"名字"当 ref 也拿不到（require_project 在 B 的 registry 里查不到）
        self.assertEqual(self.B.get("/api/projects/A机密/workspace").status_code, 404)
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py::CrossTenantApiTests -v`
Expected: FAIL（接口未接 require_project，B 能读到 A 或 500）

- [ ] **Step 3: 按归类表逐接口接线（样板）**

`GET /api/projects`（替换行 204-206）：
```python
@app.get("/api/projects")
async def list_projects(uid: str = Depends(get_current_uid)):
    return get_skill_engine(uid).list_projects()
```
`GET .../{project_id}/workspace`（替换行 403-408）：
```python
@app.get("/api/projects/{project_id}/workspace")
async def get_workspace(scope: ProjectScope = Depends(require_project)):
    return scope.engine.get_workspace_summary(scope.project_id)
```
`POST /api/chat/stream`（body project_id，替换行 710-745 头部）：
```python
@app.post("/api/chat/stream")
@limiter.limit("20/minute")
def chat_stream(request: Request, chat_request: ChatRequest, uid: str = Depends(get_current_uid)):
    scope = require_project(chat_request.project_id, uid)   # 先校验归属，再建 handler
    def generate():
        try:
            handler = get_chat_handler(scope.uid, scope.project_id)
            # ...（其余 generate 体不变，但把 chat_request.project_id 换成 scope.project_id）
```
其余接口按归类表同模式：项目级路径接口统一 `scope: ProjectScope = Depends(require_project)`、用 `scope.engine`/`scope.project_id`，把原 `skill_engine.xxx(project_id)` 换成 `scope.engine.xxx(scope.project_id)`；纯 uid 接口加 `uid: str = Depends(get_current_uid)` 换 `get_skill_engine(uid)`。逐个替换 `skill_engine.`（全局已删）→ 编译期即能抓到漏网接口。

- [ ] **Step 4: 跑测试确认 PASS + 全接口回归**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_main_api.py tests/test_auth_api.py -v`
Expected: PASS（跨租户 404 + 既有 main_api 用例——既有用例需补登录态，见 Step 5 note）

> note：既有 `tests/test_main_api.py`/`test_stream_api.py`/`test_project_create_api.py` 等假设无鉴权，需在各自 setUp 注入 `app.state.auth_required=False`（走 uid=local 兼容路径）或登录后带 cookie。本步顺带把这些测试基类改为 `auth_required=False`，保持既有断言语义（单用户=local）。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_tenant_isolation.py tests/test_main_api.py
git commit -m "feat(w2b-b1): wire all project endpoints through require_project; cross-tenant 404"
```

---

## Task 10: 项目创建闭环（web 服务端分配工作区、拒客户端路径）

**Files:**
- Modify: `backend/models.py`（`ProjectInfo.workspace_dir` 改 Optional）、`backend/main.py`（create_project 端点）
- Test: `tests/test_project_create_api.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_web_create_allocates_under_user_dir_and_rejects_client_path(self):
    # auth_required=True 的 client（已登录 alice）
    bad = self.client.post("/api/projects", json={
        "name": "x", "project_type": "strategy", "theme": "t",
        "deadline": "2026-12-31", "expected_length": "1万字",
        "workspace_dir": "/etc/evil"})
    self.assertEqual(bad.status_code, 400)   # web 不接受客户端路径
    ok = self.client.post("/api/projects", json={
        "name": "x", "project_type": "strategy", "theme": "t",
        "deadline": "2026-12-31", "expected_length": "1万字"})
    self.assertEqual(ok.status_code, 200)
    rec = ok.json()["project"]
    from backend.tenant import user_projects_dir
    self.assertTrue(str(user_projects_dir(self._uid)) in rec["workspace_dir"])
```
（测试基类需登录并记录 `self._uid`；可从 `/api/auth/me` 取。）

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_project_create_api.py -k web_create -v`
Expected: FAIL（现接受 workspace_dir、且落用户给的路径）

- [ ] **Step 3: 实现闭环**

`backend/models.py` `ProjectInfo`：`workspace_dir` 改可选：
```python
    workspace_dir: Optional[str] = Field(default=None, max_length=500)
```
`backend/main.py` create 端点（替换行 209-215）：
```python
@app.post("/api/projects")
async def create_project(info: ProjectInfo, request: Request, uid: str = Depends(get_current_uid)):
    web_mode = getattr(request.app.state, "auth_required", True)
    if web_mode:
        # web：拒收客户端路径，服务端在 users/<uid>/projects/ 下分配
        if info.workspace_dir or info.initial_material_paths:
            raise HTTPException(status_code=400, detail="web 模式不接受客户端工作目录/本地材料路径")
        alloc = user_projects_dir(uid) / uuid.uuid4().hex
        info = info.model_copy(update={"workspace_dir": str(alloc)})
    try:
        project = get_skill_engine(uid).create_project(info)
        return {"status": "ok", "project_id": project["id"], "project": project}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```
（`uuid` 已在 main.py import。）

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_project_create_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/main.py tests/test_project_create_api.py
git commit -m "feat(w2b-b1): web project creation allocates workspace server-side; reject client path"
```

---

## Task 11: bootstrap admin + invite 播种 + 桌面 loopback 启动断言

**Files:**
- Modify: `backend/main.py`（bootstrap + `assert_safe_startup`）、`run_web.py`、`app.py`
- Test: `tests/test_auth_api.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
class BootstrapAndSafetyTests(AuthApiTestBase):
    def test_bootstrap_admin_created_from_env(self):
        # 重载时带 admin env
        with mock.patch.dict(os.environ, {"CRA_BOOTSTRAP_ADMIN_USERNAME": "root",
                                          "CRA_BOOTSTRAP_ADMIN_PASSWORD": "admin-pw"}):
            import importlib, backend.main as m
            importlib.reload(m)
            from backend import accounts
            rec = accounts.get_user_by_username("root")
            self.assertIsNotNone(rec)
            self.assertTrue(rec["is_admin"])

    def test_assert_safe_startup_rejects_auth_off_non_loopback(self):
        with self.assertRaises(SystemExit):
            self.main.assert_safe_startup(auth_required=False, host="0.0.0.0")
        # loopback + auth off 允许（桌面）
        self.main.assert_safe_startup(auth_required=False, host="127.0.0.1")
        # auth on 任意 host 允许
        self.main.assert_safe_startup(auth_required=True, host="0.0.0.0")
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::BootstrapAndSafetyTests -v`
Expected: FAIL（`assert_safe_startup` 不存在 + 无 bootstrap）

- [ ] **Step 3: 实现 bootstrap + 断言**

`backend/main.py`（accounts.init_db 之后）：
```python
def _bootstrap_admin():
    u = os.environ.get("CRA_BOOTSTRAP_ADMIN_USERNAME")
    p = os.environ.get("CRA_BOOTSTRAP_ADMIN_PASSWORD")
    if u and p and accounts.get_user_by_username(u) is None:
        uid = accounts.create_user(u, p, is_admin=True, must_change_password=True)
        ensure_user_dirs(uid)

_bootstrap_admin()


def assert_safe_startup(auth_required: bool, host: str) -> None:
    loopback = {"127.0.0.1", "::1", "localhost"}
    if not auth_required and host not in loopback:
        raise SystemExit(f"拒绝启动：auth 关闭时 host 必须是 loopback，当前 {host!r}")
```
`run_web.py`：起服务前设 `app.state.auth_required = True` 并 `assert_safe_startup(True, host)`。
`app.py`：起服务前设 `app.state.auth_required = False`、`app.state.cookie_secure = False`，`settings.host` 强制 `"127.0.0.1"` 并 `assert_safe_startup(False, settings.host)`。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::BootstrapAndSafetyTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py run_web.py app.py tests/test_auth_api.py
git commit -m "feat(w2b-b1): bootstrap admin + invite seed + loopback startup assertion"
```

---

## Task 12: 搜索 project 级状态/缓存改复合键

**Files:**
- Modify: `backend/chat.py`（`_get_search_router().search(...)` 调用处传复合键）
- Test: `tests/test_tenant_isolation.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
class SearchCompositeKeyTests(unittest.TestCase):
    def test_search_called_with_composite_key(self):
        from backend.tenant import tenant_project_key
        from backend import search_state
        # _make_cache_key 用复合 key 时，不同 uid 同 project_id 的缓存键不同
        store = search_state.SearchStateStore.__new__(search_state.SearchStateStore)
        k1 = store._make_cache_key("q", tenant_project_key("uA", "proj-x"))
        k2 = store._make_cache_key("q", tenant_project_key("uB", "proj-x"))
        self.assertNotEqual(k1, k2)
```

- [ ] **Step 2: 跑测试确认 PASS（验证机制）**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py::SearchCompositeKeyTests -v`
Expected: PASS（`_make_cache_key` 按传入 key 区分——核心是改**调用方**传复合键）

- [ ] **Step 3: 调用方传复合键**

`backend/chat.py`：grep 搜索调用处 `self._get_search_router().search(`，把 `project_id=project_id` 改为 `project_id=tenant_project_key(self.uid, project_id)`：
```bash
grep -n "\.search(" backend/chat.py | grep project_id
```
对每处：`project_id=...` → `project_id=tenant_project_key(self.uid, <原project_id变量>)`。`SearchRouter` 与 `SearchStateStore` 内部签名不变（它们只把这个字符串当不透明 key 用），仅调用方语义升级为复合键。

- [ ] **Step 4: 跑回归确认**

Run: `.venv/bin/python -m pytest tests/test_tenant_isolation.py tests/test_search_pool.py tests/test_search_state.py tests/test_chat_runtime.py -v`
Expected: PASS（搜索全局 minute-limit/cooldown 不受影响、project 级按复合键隔离）

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py tests/test_tenant_isolation.py
git commit -m "feat(w2b-b1): search project-scoped state/cache keyed by tenant_project_key"
```

---

## Task 13: 前端 axios 全局配置（withCredentials + 401 拦截）+ authState

**Files:**
- Create: `frontend/src/api.js`、`frontend/src/utils/authState.js`、`frontend/tests/authState.test.mjs`
- Modify: `frontend/src/main.jsx`
- Test: `frontend/tests/authState.test.mjs`

- [ ] **Step 1: 写失败测试**

Create `frontend/tests/authState.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reduceAuth, isAuthError } from '../src/utils/authState.js'

test('reduceAuth transitions', () => {
  assert.equal(reduceAuth({ status: 'loading' }, { type: 'authed', user: { username: 'a' } }).status, 'authed')
  assert.equal(reduceAuth({ status: 'authed' }, { type: 'unauthed' }).status, 'login')
  assert.deepEqual(reduceAuth({ status: 'loading' }, { type: 'authed', user: { username: 'a' } }).user, { username: 'a' })
})

test('isAuthError detects 401', () => {
  assert.equal(isAuthError({ response: { status: 401 } }), true)
  assert.equal(isAuthError({ response: { status: 500 } }), false)
  assert.equal(isAuthError({}), false)
})
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd frontend && node --test tests/authState.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

Create `frontend/src/utils/authState.js`:
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
Create `frontend/src/api.js`:
```javascript
import axios from 'axios'

axios.defaults.withCredentials = true

let onUnauthed = null
export function setUnauthedHandler(fn) { onUnauthed = fn }

axios.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401 && onUnauthed) onUnauthed()
    return Promise.reject(error)
  },
)
```
`frontend/src/main.jsx` 顶部加 `import './api'`。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd frontend && node --test tests/authState.test.mjs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/utils/authState.js frontend/src/main.jsx frontend/tests/authState.test.mjs
git commit -m "feat(w2b-b1): frontend axios credentials + 401 interceptor + authState"
```

---

## Task 14: 前端登录页 + App 登录态门

**Files:**
- Create: `frontend/src/components/Login.jsx`
- Modify: `frontend/src/App.jsx`
- Test: `frontend/tests/login.source.test.mjs`

- [ ] **Step 1: 写失败测试（source-guard，无 jsdom）**

Create `frontend/tests/login.source.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('Login posts to auth endpoints', () => {
  const src = readFileSync(new URL('../src/components/Login.jsx', import.meta.url), 'utf8')
  assert.match(src, /\/api\/auth\/login/)
  assert.match(src, /\/api\/auth\/register/)
  assert.match(src, /invite_code/)
})

test('App gates on /api/auth/me and renders Login when unauthed', () => {
  const src = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(src, /\/api\/auth\/me/)
  assert.match(src, /Login/)
  assert.match(src, /setUnauthedHandler/)
})
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd frontend && node --test tests/login.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现 Login.jsx + App 门**

Create `frontend/src/components/Login.jsx`:
```jsx
import React, { useState } from 'react'
import axios from 'axios'

export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const [err, setErr] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    try {
      if (mode === 'register') {
        await axios.post('/api/auth/register', { username, password, invite_code: invite })
      }
      await axios.post('/api/auth/login', { username, password })
      const me = await axios.get('/api/auth/me')
      onAuthed(me.data)
    } catch (e2) {
      setErr(e2?.response?.data?.detail || '操作失败，请重试')
    }
  }

  return (
    <div className="flex items-center justify-center h-screen bg-[#0f0f23]">
      <form onSubmit={submit} className="bg-[#1a1a2e] p-8 rounded-xl w-80 border border-[#2a2a4a]">
        <h1 className="text-lg font-semibold text-[#e2e2f0] mb-4">咨询报告助手 · {mode === 'login' ? '登录' : '注册'}</h1>
        <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="用户名"
               value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="密码"
               value={password} onChange={(e) => setPassword(e.target.value)} />
        {mode === 'register' && (
          <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="邀请码"
                 value={invite} onChange={(e) => setInvite(e.target.value)} />
        )}
        {err && <div className="text-red-400 text-sm mb-3">{err}</div>}
        <button type="submit" className="w-full bg-blue-600 text-white py-2 rounded mb-2">
          {mode === 'login' ? '登录' : '注册并登录'}
        </button>
        <button type="button" className="w-full text-[#8888a8] text-sm"
                onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  )
}
```
`frontend/src/App.jsx`：顶部 import：
```jsx
import Login from './components/Login'
import { setUnauthedHandler } from './api'
```
在 `App()` 内加登录态（替换 `initializeApp`/首段）：
```jsx
  const [authUser, setAuthUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    setUnauthedHandler(() => { setAuthUser(null) })
    axios.get('/api/auth/me')
      .then((r) => { setAuthUser(r.data); return initializeApp() })
      .catch(() => {})
      .finally(() => setAuthChecked(true))
  }, [])
```
在 `if (loading)` 之前加：
```jsx
  if (!authChecked) {
    return <div className="flex items-center justify-center h-screen"><div className="text-[#8888a8]">加载中...</div></div>
  }
  if (!authUser) {
    return <Login onAuthed={(u) => { setAuthUser(u); initializeApp(); }} />
  }
```
并把 `authUser` 透传给 `Sidebar`（Task 15 用）。

- [ ] **Step 4: 跑测试 + build 确认**

Run: `cd frontend && node --test tests/login.source.test.mjs && npm run build`
Expected: PASS + build 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Login.jsx frontend/src/App.jsx frontend/tests/login.source.test.mjs
git commit -m "feat(w2b-b1): frontend login/register page + App auth gate"
```

---

## Task 15: 前端左下角账号块（用户名 + 登出）

**Files:**
- Modify: `frontend/src/components/Sidebar.jsx`、`frontend/src/App.jsx`（传 authUser + onLoggedOut）
- Test: `frontend/tests/sidebar.source.test.mjs`

- [ ] **Step 1: 写失败测试**

Create `frontend/tests/sidebar.source.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('Sidebar shows account block with username + logout', () => {
  const src = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.match(src, /\/api\/auth\/logout/)
  assert.match(src, /authUser/)
  assert.match(src, /登出/)
})
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd frontend && node --test tests/sidebar.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现账号块**

`frontend/src/components/Sidebar.jsx`：props 加 `authUser, onLoggedOut`；在底部「连接设置」块之上插入账号块：
```jsx
        {authUser && (
          <div className="mb-2 px-3 py-2 rounded-lg bg-[#15162d] border border-[#2f3158] flex items-center justify-between">
            <span className="text-xs text-[#e2e2f0] truncate">{authUser.username}</span>
            <button
              onClick={async () => { await axios.post('/api/auth/logout'); onLoggedOut?.() }}
              className="text-[11px] text-[#8888a8] hover:text-[#e2e2f0] ml-2"
            >登出</button>
          </div>
        )}
```
（Sidebar 顶部需 `import axios from 'axios'`。）`App.jsx` 给 `<Sidebar>` 传 `authUser={authUser}` 与 `onLoggedOut={() => setAuthUser(null)}`。

- [ ] **Step 4: 跑测试 + build**

Run: `cd frontend && node --test tests/sidebar.source.test.mjs && npm run build`
Expected: PASS + build 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.jsx frontend/src/App.jsx frontend/tests/sidebar.source.test.mjs
git commit -m "feat(w2b-b1): sidebar account block — username + logout"
```

---

## Task 16: 全量回归 + cutover

**Files:**
- Create: `docs/superpowers/cutover_report_2026-06-21_w2b-b1.md`

- [ ] **Step 1: 后端全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿（mac 上 4 个 realpath 环境差异用例除外，与 B1 无关）。重点确认 `test_tenant.py`/`test_accounts.py`/`test_auth_api.py`/`test_tenant_isolation.py`/`test_main_api.py`/`test_project_create_api.py`/`test_chat_runtime.py`/`test_independent_review.py`/`test_search_*.py` 通过。

- [ ] **Step 2: 前端回归 + build**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: 全绿 + build 成功

- [ ] **Step 3: 手动 smoke（web 模式）**

Run: `CRA_DATA_ROOT=$(mktemp -d) CRA_INVITE_CODE=JOIN .venv/bin/python run_web.py`
浏览器开 `http://localhost:8888`：注册→登录→建项目→看不到他人项目（开无痕窗口注册第二账号验证隔离）→登出回登录页。

- [ ] **Step 4: 写 cutover report**

Create `docs/superpowers/cutover_report_2026-06-21_w2b-b1.md`：记录实现的 16 task、回归结果、与 spec 的偏离（若有）、B2/B3 衔接点（计费/admin/安全硬化未做）。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/cutover_report_2026-06-21_w2b-b1.md
git commit -m "docs(w2b-b1): cutover report — tenant base + auth + creation closure"
```

---

## 自查（Self-Review，写完即查）

- **Spec 覆盖**：B1 段（spec §13）= 租户路径(Task2) + per-uid 引擎/handler 工厂(Task7) + 复合键锁(Task6) + 搜索复合键(Task12) + accounts/argon2/auth(Task1,3,4,5,8) + 中间件+require_project+23接口(Task7,9) + bootstrap + loopback 断言(Task11) + 创建闭环(Task10) + 前端登录态门+账号块(Task13,14,15)。验收门=跨租户隔离回归(Task9 CrossTenantApiTests + Task16)。✅ 全覆盖。
- **B1 不含**（属 B2/B3）：中央计费/配额、admin 面板、CSRF/SSRF/CORS 收紧、per-user settings 深化——本 plan 不实现，仅 `/api/settings`/`/api/models/list` 加 uid 归属。
- **类型一致**：`tenant_project_key(uid, pid)->str` 全程一致；`ProjectScope{uid,project_id(canonical),engine,project_record,lock_key}` 字段在 Task7 定义、Task9 使用一致；`get_chat_handler(uid, project_id)` 新签名 Task7 定义、Task9 chat_stream 使用一致；`accounts` 函数名（create_user/verify_user_password/create_session/get_session_uid/get_config…）跨 Task 一致。
- **既有测试兼容**：Task9 note 已交待——既有 `test_main_api`/`test_stream_api`/`test_project_create_api` 基类需设 `app.state.auth_required=False`（uid=local）或带登录 cookie，保持旧断言语义。实施时务必同步改这些基类，否则它们会因 401 全红。

---

## Execution Handoff

Plan 已写好。两种执行方式：

**1. Subagent-Driven（推荐）** — 每 task 派 fresh subagent 实现、task 间 review、快迭代（本项目 SOP：实施 Claude agent + 每 commit Codex review）。

**2. Inline Execution** — 本会话内分批执行 + checkpoint review。

选哪个？
