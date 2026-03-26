from pydantic_settings import BaseSettings
from pathlib import Path
import json
import sys

DEFAULT_MANAGED_BASE_URL = "https://newapi.z0y0h.work/client/v1"
DEFAULT_MANAGED_MODEL = "gemini-3-flash"


def get_base_path() -> Path:
    """获取基础路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent


def get_user_config_dir() -> Path:
    """获取用户配置目录"""
    config_dir = Path.home() / ".consulting-report"
    config_dir.mkdir(exist_ok=True)
    return config_dir


class Settings(BaseSettings):
    """应用配置"""

    # 连接模式
    mode: str = "managed"

    # 默认托管通道
    managed_base_url: str = DEFAULT_MANAGED_BASE_URL
    managed_model: str = DEFAULT_MANAGED_MODEL

    # 自定义API配置
    custom_api_key: str = ""
    custom_api_base: str = ""
    custom_model: str = ""

    # 兼容旧代码的别名字段
    api_provider: str = "siliconflow"
    api_key: str = ""
    api_base: str = ""
    model: str = ""

    # 项目路径
    projects_dir: Path = get_user_config_dir() / "projects"
    skill_dir: Path = get_base_path() / "skill"

    # 上下文管理配置
    context_window: int = 128000       # 模型上下文窗口大小
    compress_threshold: int = 60000    # 压缩触发阈值（tokens）
    keep_recent_messages: int = 6      # 压缩时保留最近N条消息

    # 服务配置
    host: str = "127.0.0.1"
    port: int = 8080

    class Config:
        env_file = ".env"


def normalize_settings_payload(data: dict) -> dict:
    """兼容旧配置，并同步当前模式对应的运行时字段。"""
    normalized = dict(data)

    if "mode" not in normalized:
        normalized["mode"] = "custom" if normalized.get("api_key") else "managed"

    normalized.setdefault("managed_base_url", DEFAULT_MANAGED_BASE_URL)
    normalized.setdefault("managed_model", DEFAULT_MANAGED_MODEL)
    normalized.setdefault("custom_api_base", normalized.get("api_base", ""))
    normalized.setdefault("custom_api_key", normalized.get("api_key", ""))
    normalized.setdefault("custom_model", normalized.get("model", ""))

    if normalized["mode"] == "managed":
        normalized["api_base"] = normalized["managed_base_url"]
        normalized["model"] = normalized["managed_model"]
        normalized["api_key"] = "managed"
    else:
        normalized["api_base"] = normalized["custom_api_base"]
        normalized["model"] = normalized["custom_model"]
        normalized["api_key"] = normalized["custom_api_key"]

    return normalized


def load_settings() -> Settings:
    """加载配置"""
    config_file = get_user_config_dir() / "config.json"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return Settings(**normalize_settings_payload(data))
    return Settings()


def save_settings(settings: Settings):
    """保存配置"""
    config_file = get_user_config_dir() / "config.json"
    data = normalize_settings_payload(settings.model_dump())
    # 将Path对象转为字符串（兼容已经是字符串的情况）
    for key in ["projects_dir", "skill_dir"]:
        if isinstance(data[key], Path):
            data[key] = str(data[key])
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
