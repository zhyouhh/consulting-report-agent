"""SQLite 账号层（叶子模块；只依赖 tenant）。"""
import sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
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
