from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import uvicorn
import threading
from .config import load_settings, save_settings, Settings
from .skill import SkillEngine
from .chat import ChatHandler
from .models import ChatRequest, ChatResponse, ProjectInfo

app = FastAPI(title="咨询报告写作助手")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
settings = load_settings()
skill_engine = SkillEngine(settings.projects_dir, settings.skill_dir)
chat_handler = ChatHandler(settings, skill_engine)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings():
    data = settings.model_dump()
    data["api_key"] = "***" if data["api_key"] else ""  # 隐藏API Key
    return data


class SettingsUpdate(BaseModel):
    """前端提交的设置更新（只包含API相关字段）"""
    api_provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate):
    global settings, chat_handler
    # 只更新前端传来的字段，保留路径等其他配置
    if update.api_provider is not None:
        settings.api_provider = update.api_provider
    if update.api_key is not None and update.api_key != "***":
        settings.api_key = update.api_key
    if update.api_base is not None:
        settings.api_base = update.api_base
    if update.model is not None:
        settings.model = update.model
    save_settings(settings)
    chat_handler = ChatHandler(settings, skill_engine)
    return {"status": "ok"}


@app.get("/api/projects")
async def list_projects():
    return skill_engine.list_projects()


@app.post("/api/projects")
async def create_project(info: ProjectInfo):
    try:
        skill_engine.create_project(
            info.name,
            info.report_type,
            info.theme,
            info.target_audience
        )
        return {"status": "ok", "project_name": info.name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        result = chat_handler.chat(request.project_name, request.message)
        return ChatResponse(
            content=result["content"],
            token_usage=result.get("token_usage")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_name}/files")
async def list_files(project_name: str):
    """列出项目所有文件"""
    project_path = skill_engine.get_project_path(project_name)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")

    files = []
    for md_file in project_path.rglob("*.md"):
        rel_path = md_file.relative_to(project_path)
        files.append(str(rel_path))
    return {"files": files}


@app.get("/api/projects/{project_name}/files/{file_path:path}")
async def read_file(project_name: str, file_path: str):
    try:
        content = skill_engine.read_file(project_name, file_path)
        return {"content": content}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# 静态文件挂载必须在所有API路由之后，避免拦截/api请求
from .config import get_base_path
frontend_dist = get_base_path() / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")


def start_server():
    """启动FastAPI服务"""
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="error")


if __name__ == "__main__":
    start_server()
