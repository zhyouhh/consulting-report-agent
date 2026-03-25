from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import uvicorn
import threading
import asyncio
import json
import logging
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from .config import load_settings, save_settings, Settings
from .skill import SkillEngine
from .chat import ChatHandler
from .models import ChatRequest, ChatResponse, ProjectInfo

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 速率限制
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="咨询报告写作助手")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源（开发模式）
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# 全局变量（带线程锁保护）
settings = load_settings()
skill_engine = SkillEngine(settings.projects_dir, settings.skill_dir)
_chat_handlers = {}  # 每个项目独立的ChatHandler
_settings_lock = threading.Lock()  # 保护settings和chat_handlers的并发修改

def get_chat_handler(project_name: str) -> ChatHandler:
    """获取或创建项目的ChatHandler（线程安全）"""
    with _settings_lock:
        if project_name not in _chat_handlers:
            _chat_handlers[project_name] = ChatHandler(settings, skill_engine)
        return _chat_handlers[project_name]


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
    global settings, _chat_handlers
    with _settings_lock:
        if update.api_provider is not None:
            settings.api_provider = update.api_provider
        if update.api_key is not None and update.api_key != "***":
            settings.api_key = update.api_key
        if update.api_base is not None:
            settings.api_base = update.api_base
        if update.model is not None:
            settings.model = update.model
        save_settings(settings)
        _chat_handlers.clear()  # 清空所有handler，下次使用时重新创建
    return {"status": "ok"}


class ModelsRequest(BaseModel):
    """获取模型列表请求"""
    api_key: str
    api_base: str


@app.post("/api/models/list")
async def list_models(request: ModelsRequest):
    """从API获取可用模型列表"""
    try:
        from openai import OpenAI
        import httpx
        # 创建自定义 http_client，避免 proxies 参数问题
        http_client = httpx.Client(timeout=30.0)
        client = OpenAI(
            api_key=request.api_key,
            base_url=request.api_base,
            http_client=http_client
        )
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        http_client.close()
        return {"models": model_ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


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
@limiter.limit("20/minute")
async def chat(request: Request, chat_request: ChatRequest):
    """非流式响应（保持兼容）"""
    import asyncio
    try:
        logger.info(f"Chat request for project: {chat_request.project_name}")
        handler = get_chat_handler(chat_request.project_name)
        result = await asyncio.to_thread(
            handler.chat,
            chat_request.project_name,
            chat_request.message
        )
        logger.info(f"Chat completed, tokens: {result.get('token_usage', {}).get('current_tokens', 0)}")
        return ChatResponse(
            content=result["content"],
            token_usage=result.get("token_usage")
        )
    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)
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


@app.delete("/api/projects/{project_name}")
async def delete_project(project_name: str):
    import shutil
    project_path = skill_engine.get_project_path(project_name)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        shutil.rmtree(project_path)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.get("/api/projects/{project_name}/conversation")
async def get_conversation(project_name: str):
    """获取对话历史"""
    project_path = skill_engine.get_project_path(project_name)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if conv_file.exists():
        import json
        with open(conv_file, 'r', encoding='utf-8') as f:
            messages = json.load(f)
        return {"messages": messages}
    return {"messages": []}

@app.delete("/api/projects/{project_name}/conversation")
async def clear_conversation(project_name: str):
    """清空对话历史"""
    project_path = skill_engine.get_project_path(project_name)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if conv_file.exists():
        conv_file.unlink()
    return {"status": "ok"}


@app.post("/api/chat/stream")
@limiter.limit("20/minute")
async def chat_stream(request: Request, chat_request: ChatRequest):
    """流式响应接口"""
    async def generate():
        try:
            handler = get_chat_handler(chat_request.project_name)
            # 使用真正的流式方法
            for chunk in handler.chat_stream(chat_request.project_name, chat_request.message):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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
