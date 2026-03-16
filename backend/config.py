from pydantic_settings import BaseSettings
from pathlib import Path
import json
import sys


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

    # API配置
    api_provider: str = "siliconflow"
    api_key: str = ""
    api_base: str = "https://api.siliconflow.cn/v1"
    model: str = "deepseek-ai/DeepSeek-V3"

    # 项目路径
    projects_dir: Path = get_user_config_dir() / "projects"
    skill_dir: Path = get_base_path() / "skill"

    # 服务配置
    host: str = "127.0.0.1"
    port: int = 8080

    class Config:
        env_file = ".env"


def load_settings() -> Settings:
    """加载配置"""
    config_file = get_user_config_dir() / "config.json"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return Settings(**data)
    return Settings()


def save_settings(settings: Settings):
    """保存配置"""
    config_file = get_user_config_dir() / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(settings.model_dump(), f, indent=2, ensure_ascii=False)
