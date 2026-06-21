"""多租户路径与复合键（叶子模块；只依赖 config，绝不 import chat/skill/main）。"""
from pathlib import Path
from .config import data_root


def _safe_path_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 非法：必须是非空字符串")
    # ':' 必须拒绝：Windows 优先仓库，盘符限定/相对（如 "Z:tmp"、"u:"）会让 data_root()/users/uid 逃出隔离目录。
    if value in (".", "..") or any(ch in value for ch in ("/", "\\", "\0", ":")):
        raise ValueError(f"{label} 非法：含路径分隔符、盘符或穿越片段：{value!r}")
    return value


def app_db_path() -> Path:
    return data_root() / "app.db"


def user_dir(uid: str) -> Path:
    return data_root() / "users" / _safe_path_component(uid, "uid")


def user_projects_dir(uid: str) -> Path:
    return user_dir(uid) / "projects"


def user_config_path(uid: str) -> Path:
    return user_dir(uid) / "config.json"


def ensure_user_dirs(uid: str) -> None:
    user_projects_dir(uid).mkdir(parents=True, exist_ok=True)


def tenant_project_key(uid: str, project_id: str) -> str:
    """唯一中央复合键（叶子原语）。用于内存 dict 键与持久化 JSON 键（搜索状态/锁/审查 store）。
    无损可逆转义：先转义 '\\' 与 ':'，再以未转义的 '::' 作唯一分隔符——任何 (uid, project_id)
    都映射到唯一键，杜绝跨租户碰撞。键被当作不透明字符串，调用方不得 split。任何处禁止手拼。"""
    def _esc(part: str) -> str:
        return str(part).replace("\\", "\\\\").replace(":", "\\:")
    return f"{_esc(uid)}::{_esc(project_id)}"
