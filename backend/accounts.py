"""SQLite 账号层（叶子模块；只依赖 tenant）。"""
import hashlib, secrets, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from .tenant import app_db_path

_PH = PasswordHasher()


class UsernameTakenError(Exception):
    pass


class InactiveUserError(Exception):
    """目标 uid 不存在或已停用——拒绝签发会话。"""
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(app_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        with conn:          # commits on success / rolls back on exception
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
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
            CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid);
            CREATE TABLE IF NOT EXISTS app_config(key TEXT PRIMARY KEY, value TEXT);
            """
        )


def create_user(username, password, is_admin=False, must_change_password=False) -> str:
    uid = uuid.uuid4().hex
    try:
        with _db() as conn:
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


def _get_user_row(where_col, value):
    with _db() as conn:
        row = conn.execute(f"SELECT * FROM users WHERE {where_col}=?", (value,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_username(username) -> Optional[dict]:
    rec = _get_user_row("username", username)
    if rec is not None:
        rec.pop("password_hash", None)
    return rec


def get_user_by_uid(uid) -> Optional[dict]:
    rec = _get_user_row("uid", uid)
    if rec is not None:
        rec.pop("password_hash", None)
    return rec


def verify_user_password(username, password) -> bool:
    rec = _get_user_row("username", username)
    if not rec:
        return False
    try:
        return _PH.verify(rec["password_hash"], password)
    except (VerificationError, InvalidHashError):
        return False


def set_user_password(uid, new_password) -> None:
    with _db() as conn:
        conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE uid=?",
                     (_PH.hash(new_password), uid))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(uid, ttl_days=30, ip="", ua="") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc); exp = now + timedelta(days=ttl_days)
    with _db() as conn:
        # last_seen 仅创建时写一次，read 路径不更新（避热路径 WAL 膨胀；B3 审计保留列）
        cur = conn.execute(
            "INSERT INTO sessions(token_hash,uid,created_at,expires_at,created_ip,user_agent,last_seen) "
            "SELECT ?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM users WHERE uid=? AND disabled=0)",
            (_hash_token(token), uid, now.isoformat(timespec="seconds"),
             exp.isoformat(timespec="seconds"), ip, ua, now.isoformat(timespec="seconds"), uid))
        if cur.rowcount != 1:
            raise InactiveUserError(uid)
    return token


def get_session_uid(token) -> Optional[str]:
    if not token:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT s.uid uid, s.expires_at expires_at, u.disabled disabled "
            "FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=?",
            (_hash_token(token),)).fetchone()
    if not row or row["disabled"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return None
    return row["uid"]


def delete_session(token) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))


def delete_user_sessions(uid) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE uid=?", (uid,))


def delete_other_user_sessions(uid, keep_token) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE uid=? AND token_hash<>?",
                     (uid, _hash_token(keep_token)))


def set_user_disabled(uid, disabled: bool) -> None:
    # 停用与撤销会话必须同一事务：否则中途崩溃/锁会留下会话行，重新启用即复活（Codex review）。
    with _db() as conn:
        conn.execute("UPDATE users SET disabled=? WHERE uid=?", (int(disabled), uid))
        if disabled:
            conn.execute("DELETE FROM sessions WHERE uid=?", (uid,))
