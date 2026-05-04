import asyncio
import json
import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .chat import ChatHandler, LEGACY_EMPTY_ASSISTANT_FALLBACKS
from .config import Settings, get_base_path, load_settings, save_settings
from .context_policy import clamp_custom_context_limit_override
from .models import ChatRequest, ChatResponse, ProjectInfo
from .report_tools import export_reviewable_draft, run_quality_check
from .skill import SkillEngine


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="咨询报告写作助手")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

settings = load_settings()
skill_engine = SkillEngine(settings.projects_dir, settings.skill_dir)
_chat_handlers = {}
_settings_lock = threading.Lock()
_desktop_bridge = None


def register_desktop_bridge(bridge):
    global _desktop_bridge
    _desktop_bridge = bridge


def get_chat_handler(project_id: str) -> ChatHandler:
    with _settings_lock:
        if project_id not in _chat_handlers:
            _chat_handlers[project_id] = ChatHandler(settings, skill_engine)
        return _chat_handlers[project_id]


def require_desktop_bridge():
    if _desktop_bridge is None:
        raise HTTPException(status_code=503, detail="桌面文件选择器尚未就绪")
    return _desktop_bridge


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings():
    data = settings.model_dump(exclude={"managed_client_token"})
    data["api_key"] = "***" if data["api_key"] else ""
    data["custom_api_key"] = "***" if data.get("custom_api_key") else ""
    return data


class SettingsUpdate(BaseModel):
    """前端提交的设置更新"""

    mode: Literal["managed", "custom"]
    managed_base_url: str
    managed_model: str
    custom_api_base: str = ""
    custom_api_key: str = ""
    custom_model: str = ""
    custom_context_limit_override: int | None = None


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate):
    global settings, _chat_handlers
    with _settings_lock:
        settings.mode = update.mode
        settings.managed_base_url = update.managed_base_url
        settings.managed_model = update.managed_model
        settings.custom_api_base = update.custom_api_base
        if update.custom_api_key != "***":
            settings.custom_api_key = update.custom_api_key
        settings.custom_model = update.custom_model
        if "custom_context_limit_override" in update.model_fields_set:
            settings.custom_context_limit_override = clamp_custom_context_limit_override(
                update.custom_context_limit_override
            )

        if update.mode == "managed":
            settings.api_base = update.managed_base_url
            settings.model = update.managed_model
            settings.api_key = settings.managed_client_token
        else:
            settings.api_base = update.custom_api_base
            settings.model = update.custom_model
            settings.api_key = settings.custom_api_key

        save_settings(settings)
        _chat_handlers.clear()
    return {"status": "ok"}


class ModelsRequest(BaseModel):
    """获取模型列表请求"""

    api_key: str
    api_base: str


class WorkspaceFilesRequest(BaseModel):
    workspace_dir: str


@app.post("/api/models/list")
async def list_models(request: ModelsRequest):
    try:
        from openai import OpenAI
        import httpx

        http_client = httpx.Client(timeout=30.0)
        client = OpenAI(
            api_key=request.api_key,
            base_url=request.api_base,
            http_client=http_client,
        )
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        http_client.close()
        return {"models": model_ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


@app.post("/api/system/select-workspace-folder")
async def select_workspace_folder():
    bridge = require_desktop_bridge()
    selected_path = await asyncio.to_thread(bridge.select_workspace_folder)
    return {"path": selected_path or ""}


@app.post("/api/system/select-workspace-files")
async def select_workspace_files(request: WorkspaceFilesRequest):
    bridge = require_desktop_bridge()
    selected_paths = await asyncio.to_thread(bridge.select_workspace_files, request.workspace_dir)
    return {"paths": selected_paths or []}


@app.get("/api/projects")
async def list_projects():
    return skill_engine.list_projects()


@app.post("/api/projects")
async def create_project(info: ProjectInfo):
    try:
        project = skill_engine.create_project(info)
        return {"status": "ok", "project_id": project["id"], "project": project}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}/materials")
async def list_project_materials(project_id: str):
    project = skill_engine.get_project_record(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"materials": skill_engine.list_materials(project_id)}


@app.post("/api/projects/{project_id}/materials/select-from-workspace")
async def select_materials_from_workspace(project_id: str):
    project = skill_engine.get_project_record(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    bridge = require_desktop_bridge()
    file_paths = await asyncio.to_thread(bridge.select_workspace_files, project["workspace_dir"])
    if not file_paths:
        return {"materials": []}

    try:
        materials = skill_engine.add_materials(project_id, file_paths, added_via="workspace_select")
        return {"materials": materials}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/projects/{project_id}/materials/upload")
async def upload_materials(project_id: str, files: list[UploadFile] = File(...)):
    project = skill_engine.get_project_record(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    staged_paths = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for upload in files:
            safe_name = Path(upload.filename or "attachment").name
            temp_path = tmpdir_path / safe_name
            temp_path.write_bytes(await upload.read())
            staged_paths.append(str(temp_path))

        try:
            materials = skill_engine.add_materials(project_id, staged_paths, added_via="chat_upload")
            return {"materials": materials}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/projects/{project_id}/materials/{material_id}")
async def delete_material(project_id: str, material_id: str):
    try:
        skill_engine.remove_material(project_id, material_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat(request: Request, chat_request: ChatRequest):
    try:
        logger.info(f"Chat request for project: {chat_request.project_id}")
        handler = get_chat_handler(chat_request.project_id)
        result = await asyncio.to_thread(
            handler.chat,
            chat_request.project_id,
            chat_request.message_text,
            chat_request.attached_material_ids,
            [item.model_dump() for item in chat_request.transient_attachments],
        )
        token_usage = result.get("token_usage") or {}
        logger.info(f"Chat completed, tokens: {token_usage.get('context_used_tokens', 0)}")
        return ChatResponse(
            content=result["content"],
            token_usage=result.get("token_usage"),
            system_notices=result.get("system_notices"),
        )
    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/files")
async def list_files(project_id: str):
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")

    files = []
    for md_file in project_path.rglob("*.md"):
        rel_path = md_file.relative_to(project_path)
        normalized_path = str(rel_path).replace("\\", "/")
        if normalized_path == "plan/project-info.md":
            continue
        files.append(normalized_path)
    return {"files": files}


@app.get("/api/projects/{project_id}/files/{file_path:path}")
async def read_file(project_id: str, file_path: str):
    try:
        content = skill_engine.read_file(project_id, file_path)
        return {"content": content}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/projects/{project_id}/workspace")
async def get_workspace(project_id: str):
    try:
        return skill_engine.get_workspace_summary(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/projects/{project_id}/quality-check")
async def quality_check(project_id: str):
    try:
        report_path = skill_engine.get_primary_report_path(project_id)
        script_path = skill_engine.get_script_path("quality_check.ps1")
        return run_quality_check(report_path, script_path)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/projects/{project_id}/export-draft")
async def export_draft(project_id: str):
    try:
        report_path = skill_engine.get_primary_report_path(project_id)
        output_dir = skill_engine.ensure_output_dir(project_id)
        script_path = skill_engine.get_script_path("export_draft.ps1")
        return export_reviewable_draft(report_path, output_dir, script_path)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        skill_engine.delete_project(project_id)
        _chat_handlers.pop(project_id, None)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


_CHECKPOINT_ROUTES = {
    "s0-interview-done": "s0_interview_done_at",
    "outline-confirmed": "outline_confirmed_at",
    "review-started": "review_started_at",
    "review-passed": "review_passed_at",
    "presentation-ready": "presentation_ready_at",
    "delivery-archived": "delivery_archived_at",
}


@app.post("/api/projects/{project_id}/checkpoints/{name}")
async def set_checkpoint(project_id: str, name: str, action: str = "set"):
    key = _CHECKPOINT_ROUTES.get(name)
    if key is None:
        raise HTTPException(status_code=404, detail=f"未知 checkpoint: {name}")
    if action not in ("set", "clear"):
        raise HTTPException(status_code=400, detail=f"未知 action: {action}")
    if key == "s0_interview_done_at" and action == "set":
        raise HTTPException(
            status_code=400,
            detail=(
                "s0_interview_done_at 不能通过 endpoint 直接 set："
                "endpoint 层无对话上下文，无法执行 S0 对话级软门槛。"
                "set 只能走 StageAckParser / strong 关键词软门槛 / schema migration。"
            ),
        )
    try:
        return skill_engine.record_stage_checkpoint(project_id, key, action)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "项目不存在" in detail else 400
        raise HTTPException(status_code=status, detail=detail)


@app.get("/api/projects/{project_id}/conversation")
async def get_conversation(project_id: str):
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if conv_file.exists():
        with open(conv_file, "r", encoding="utf-8") as f:
            messages = json.load(f)
        # v5: sanitize 历史 fallback assistant，避免旧占位气泡重新展示。
        sanitized = [
            m for m in messages
            if not (
                m.get("role") == "assistant"
                and (m.get("content") or "").strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS
            )
        ]
        return {"messages": sanitized}
    return {"messages": []}


@app.delete("/api/projects/{project_id}/conversation")
async def clear_conversation(project_id: str):
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    handler = get_chat_handler(project_id)
    request_lock = handler._get_project_request_lock(project_id)
    with request_lock:
        for file_name in (
            "conversation.json",
            "conversation_state.json",
            "conversation_compact_state.json",
        ):
            target_path = project_path / file_name
            if target_path.exists():
                target_path.unlink()
    return {"status": "ok"}


@app.post("/api/chat/stream")
@limiter.limit("20/minute")
def chat_stream(request: Request, chat_request: ChatRequest):
    def generate():
        try:
            handler = get_chat_handler(chat_request.project_id)
            for chunk in handler.chat_stream(
                chat_request.project_id,
                chat_request.message_text,
                chat_request.attached_material_ids,
                [item.model_dump() for item in chat_request.transient_attachments],
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


frontend_dist = get_base_path() / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")


def start_server():
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="error")


if __name__ == "__main__":
    start_server()
