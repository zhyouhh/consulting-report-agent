import asyncio
import json
import logging
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .chat import (
    ChatHandler,
    LEGACY_EMPTY_ASSISTANT_FALLBACKS,
    _strip_legacy_stage_ack,
    strip_tool_log_comments,
)
from .config import Settings, get_base_path, heal_stale_managed_model, load_settings, save_settings
from .context_policy import clamp_custom_context_limit_override
from .independent_review import (
    IndependentReviewAgent,
    _REVIEW_SESSION_STORE,
    get_independent_review_lock,
)
from .models import ChatRequest, ChatResponse, ProjectInfo
from .report_tools import (
    export_reviewable_draft,
    get_lint_report_lock,
    run_lint_report,
    run_quality_check,
)
from .skill import SkillEngine, StaleFileError, UserWriteForbiddenError


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

# 启动时若 managed_model 已不在网关白名单（例如老用户升级 exe 后保留的旧模型名），
# 自动切到网关 /v1/models 的第一项并持久化。Best-effort：网络失败一律不影响启动。
try:
    _healed_settings, _heal_msg = heal_stale_managed_model(settings)
    if _heal_msg:
        settings = _healed_settings
        save_settings(settings)
        logger.warning(_heal_msg)
except Exception:
    logger.exception("heal_stale_managed_model failed unexpectedly; continuing with stored settings")

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
    try:
        return {"files": skill_engine.list_workspace_files(project_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/projects/{project_id}/files/{file_path:path}")
async def read_file(project_id: str, file_path: str):
    try:
        normalized = skill_engine.normalize_file_path(project_id, file_path)
        data = skill_engine.read_file_with_mtime(project_id, file_path)
        data["editable"] = skill_engine.is_user_editable(normalized)
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class UserFileWrite(BaseModel):
    content: str
    base_mtime_ns: str  # opaque string; pydantic rejects a raw JSON number → 422


@app.post("/api/projects/{project_id}/files/{file_path:path}")
async def write_user_file(project_id: str, file_path: str, payload: UserFileWrite):
    # 项目不存在前置判 404（避免靠脆弱字符串匹配区分 404/400）
    if not skill_engine.get_project_path(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")

    handler = get_chat_handler(project_id)
    request_lock = handler._get_project_request_lock(project_id)

    def _write_under_lock():
        # 全段持与聊天同一把锁：CAS(stat) → os.replace 必须对 AI 写入原子互斥。
        # run_in_threadpool 包裹，锁阻塞落在线程池线程、不阻塞事件循环。
        with request_lock:
            new_mtime = skill_engine.user_write_file(
                project_id, file_path, payload.content, payload.base_mtime_ns
            )
            return {"status": "ok", "mtime_ns": new_mtime}

    try:
        return await run_in_threadpool(_write_under_lock)
    except UserWriteForbiddenError:
        raise HTTPException(status_code=403, detail="该文件不可编辑")
    except StaleFileError:
        raise HTTPException(
            status_code=409,
            detail="文件已被更新（可能是 AI 刚写过），请重新加载后再编辑",
        )
    except FileNotFoundError:
        # OSError 子类，必须排在下面 except OSError 之前
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError:
        # 剩余 ValueError = 路径穿越（非法的文件路径）
        raise HTTPException(status_code=400, detail="非法的文件路径")
    except OSError:
        # os.replace/write_text 失败（Windows 上文件被外部编辑器/同步盘/杀毒占用等）——
        # 该文件可编辑、只是临时写不进，给可重试提示，别误报成 403「不可编辑」。
        raise HTTPException(
            status_code=500,
            detail="文件写入失败（可能被外部程序占用），请关闭后重试",
        )


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


@app.post("/api/projects/{project_id}/independent-review/stream")
async def independent_review_stream_post(project_id: str, request: Request):
    """POST stream with frontend-stable run_id + resume/discard support.

    C5 cutover: the legacy GET endpoint was deleted and the front end now drives this POST
    contract exclusively. Body: {resume, run_id, supplement?}.
    Hard invariants (codex R2): the review lock is released on EVERY path (acquire fail,
    claim_first CAS fail, resume done/reject, worker finally); resume waits for the lock via
    to_thread (non-blocking the event loop) then re-reads the store; review-completed is
    re-emitted by this wrapper only after the worker finished AND the lock was released AND a
    done tombstone exists.
    """
    body = await request.json()
    resume = bool(body.get("resume"))
    run_id = body.get("run_id")
    supplement = body.get("supplement")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id required")

    try:
        workspace = skill_engine.get_workspace_summary(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if workspace.get("stage_code") != "S5":
        raise HTTPException(status_code=400, detail="独立审查只能在 S5 阶段使用")

    lock = get_independent_review_lock(project_id)
    store = _REVIEW_SESSION_STORE
    cancel_event = threading.Event()
    resume_snapshot = None
    done_mtime = None

    if not resume:
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="上一次独立审查仍在进行中，请等待")
        if not store.claim_first(project_id, run_id, cancel_event):
            lock.release()  # CAS 失败必须 release（红队：防 lock 泄漏）
            raise HTTPException(status_code=409, detail="已有进行中的审查")
    else:
        # 短 blocking 等锁，不阻塞事件循环：worker 可能正在收尾（release 在即）。
        got = await asyncio.to_thread(lock.acquire, True, 3.0)
        if not got:
            raise HTTPException(status_code=409, detail="上一次审查正在收尾，请稍候")
        # 拿到锁后重读 store（等锁期间 worker 可能已 atomic_commit 收尾、状态翻 done）。
        kind, payload = store.claim_resume(project_id, run_id, cancel_event)
        if kind == "errored":
            resume_snapshot = payload
        elif kind == "done":
            done_mtime = payload
            lock.release()  # done 不启 worker → 必须释放（lock 全路径）
        else:  # reject
            lock.release()
            raise HTTPException(status_code=400, detail="无可续审的会话")

    # Worker + review-lock release are created HERE (function body), NOT inside generate(), so the
    # lock is released even if generate() never runs. Starlette StreamingResponse.__call__ runs
    # stream_response and listen_for_disconnect concurrently in a task group and cancels the group as
    # soon as either finishes (starlette/responses.py:249-257). If the client already disconnected,
    # the disconnect listener can win before stream_response is scheduled, so generate()'s body —
    # which used to create the worker — may never execute, leaking the review lock forever (that
    # project's review then 409s until process restart; discard only clears the store, not the lock).
    # Creating the worker in the function body guarantees run_worker's finally releases the lock
    # regardless of whether generate() is ever consumed (codex C5 red-team B3).
    event_queue = None
    worker_task = None
    if done_mtime is None:
        event_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def enqueue_event(event):
            if loop.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)
            future.result()

        def run_worker():
            try:
                agent = IndependentReviewAgent(skill_engine, settings)
                for event in agent.run(
                    project_id,
                    run_id=run_id,
                    store=store,
                    resume_snapshot=resume_snapshot,
                    supplement=supplement,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    # 不透传 agent 内部的 review-completed：完成信号由 endpoint wrapper 在 lock
                    # 释放后按 store.get_done_mtime 重新发射，保证前端见 completion 时锁已可用。
                    if isinstance(event, dict) and event.get("type") == "review-completed":
                        continue
                    enqueue_event(event)
            except Exception as exc:
                if not cancel_event.is_set():
                    enqueue_event({"type": "error", "data": str(exc)})
            finally:
                try:
                    # worker 退出兜底（codex R2 BLOCKER 4）：record 仍 running 则收敛——续审场景留
                    # snapshot 可再 resume，无则清 record。done/errored/被 discard → no-op。
                    # 先收敛 store，再释放 review lock。
                    store.finalize_orphan_running(project_id, run_id, resume_snapshot)
                finally:
                    try:
                        enqueue_event(None)
                    finally:
                        lock.release()

        worker_task = asyncio.create_task(asyncio.to_thread(run_worker))

    async def generate():
        # 统一 completion 收尾（codex C4-quality BLOCKER）：done 短路分支与 worker 分支共用同一套
        # completion 时序，消除两分支不一致——发 review-completed 前永远 ① 检查 is_disconnected
        # （断连 → 不发 completed、不发 [DONE]）；② 重新 store.get_done_mtime 重读 tombstone（truthy
        # 才发，带重读到的 mtime，绝不用任何缓存值——防 release lock 后被并发 discard / 新 first-run
        # 覆盖 tombstone 仍发 stale completed）；③ 未断连才发 [DONE]。此刻 review lock 必已释放
        # （done 分支在 claim_resume 后即 release；worker 分支在 run_worker finally release）。
        async def emit_completion():
            if await request.is_disconnected():
                return
            final_mtime = store.get_done_mtime(project_id, run_id)
            if final_mtime:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "review-completed",
                            "run_id": run_id,
                            "report_mtime_ns": final_mtime,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        # done 分支：worker 不启动（resume 命中已落档的 done tombstone），直接走统一 completion。
        if done_mtime is not None:
            async for chunk in emit_completion():
                yield chunk
            return

        # worker + review-lock release 已在函数体创建（见上方 B3 注释）；generate 只消费 event_queue。
        # worker_task / event_queue 是函数体闭包变量（done 分支已在上方 return，到这里必为非 None）。
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            cancel_event.set()
            yield f"data: {json.dumps({'type': 'error', 'data': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            cancel_event.set()
            if not worker_task.done():
                await worker_task

        # worker 已结束 + lock 已在 run_worker finally 释放 → 走统一 completion（重读 tombstone +
        # disconnect guard 都在 emit_completion 内）。worker error / atomic replace 失败 / 自修失败 /
        # 断连 → emit_completion 重读 get_done_mtime 为空 / is_disconnected → 不发 completed。
        async for chunk in emit_completion():
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/projects/{project_id}/independent-review/discard")
async def independent_review_discard(project_id: str, request: Request):
    """C4: cancel an in-flight (or finished) review session without acquiring the review lock.

    Uses only the store guard so it can cancel even while a long-running worker holds the
    review lock. run_id must match the current record; on match it sets the cancel_event and
    drops the record (any late commit from the old worker is then rejected by run_id mismatch).
    This only cancels the session — it never deletes an already-written report.
    """
    body = await request.json()
    run_id = body.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id required")
    cancelled = _REVIEW_SESSION_STORE.discard(project_id, run_id)
    return {"cancelled": cancelled}


@app.post("/api/projects/{project_id}/lint-report")
async def lint_report(project_id: str):
    try:
        workspace = skill_engine.get_workspace_summary(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if workspace.get("stage_code") != "S5":
        raise HTTPException(status_code=400, detail="AI 味自查只能在 S5 阶段使用")

    lock = get_lint_report_lock(project_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一次 AI 味自查仍在进行中，请等待")
    try:
        try:
            report_path = skill_engine.get_primary_report_path(project_id)
            output_path = str(skill_engine.get_project_path(project_id) / "plan" / "lint-report.md")
            script_path = skill_engine.get_script_path("quality_check.ps1")
        except (ValueError, FileNotFoundError, OSError) as e:
            raise HTTPException(status_code=404, detail=str(e))

        try:
            return run_lint_report(report_path, output_path, script_path)
        except Exception as e:
            return {"status": "error", "detail": f"AI 味自查失败：{str(e)}"}
    finally:
        lock.release()


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
                "set 只能走 advance_stage / schema migration。"
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
    if not conv_file.exists():
        return {"messages": []}
    with open(conv_file, "r", encoding="utf-8") as f:
        messages = json.load(f)

    # v5: sanitize 历史 fallback assistant，避免旧占位气泡重新展示。
    sanitized = []
    for m in messages:
        if m.get("role") == "assistant":
            raw = m.get("content") or ""
            if raw.strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS:
                continue
            cleaned = strip_tool_log_comments(_strip_legacy_stage_ack(raw))
            sanitized.append({**m, "content": cleaned})
        else:
            sanitized.append(m)
    return {"messages": sanitized}


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
            # C5: thread run-bound trigger metadata end-to-end so the main agent can bind a
            # review report to the exact run that produced it (run_id/report_mtime_ns stay
            # opaque strings — pydantic already rejects raw ints; never coerce to Number).
            trigger_metadata = {
                "run_id": chat_request.run_id,
                "report_mtime_ns": chat_request.report_mtime_ns,
            }
            for chunk in handler.chat_stream(
                chat_request.project_id,
                chat_request.message_text,
                chat_request.attached_material_ids,
                [item.model_dump() for item in chat_request.transient_attachments],
                system_trigger=chat_request.system_trigger,
                trigger_metadata=trigger_metadata,
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
