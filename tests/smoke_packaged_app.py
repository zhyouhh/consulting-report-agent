r"""Smoke test for the packaged Windows bundle.

默认 pytest skip；设置 RUN_PACKAGED_SMOKE=1 才会在 pytest 下执行。
打完 `dist\咨询报告助手\` 之后也可以手动跑：

    .venv\Scripts\python tests\smoke_packaged_app.py

验证覆盖：私有文件注入、exe 启动、managed 模式配置加载、凭据脱敏、
项目脚手架、阶段真值源回写、`project-info.md` 已退役。**不调 `/api/chat`，
不消耗 LLM/搜索 API 额度。** UI/流式体感/真实 chat 门禁仍需人工点。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest


pytestmark = pytest.mark.slow


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE_DIR = REPO_ROOT / "dist" / "咨询报告助手"
DEFAULT_EXE_NAME = "咨询报告助手.exe"
DEFAULT_PORT = 8080
SERVER_BOOT_TIMEOUT_SECONDS = 20

REQUIRED_PLAN_FILES = {
    "project-overview.md",
    "stage-gates.md",
    "progress.md",
    "notes.md",
    "outline.md",
    "research-plan.md",
    "references.md",
    "tasks.md",
    "review.md",
    "data-log.md",
    "analysis-notes.md",
    "independent-review.md",
    "lint-report.md",
    "presentation-plan.md",
    "delivery-log.md",
}
FORBIDDEN_PLAN_FILES = {"project-info.md", "gate-control.md"}
REQUIRED_POOL_PROVIDER_FIELDS = {"weight", "minute_limit", "daily_soft_limit", "cooldown_seconds"}
REQUIRED_POOL_LIMIT_FIELDS = {
    "per_turn_searches",
    "project_minute_limit",
    "global_minute_limit",
    "memory_cache_ttl_seconds",
    "project_cache_ttl_seconds",
}


class SmokeFailure(Exception):
    pass


def log_step(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


def port_in_use(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def http_get(path: str, port: int, timeout: float = 5.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(path: str, port: int, payload: dict, timeout: float = 10.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(path: str, port: int, timeout: float = 10.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_request_json(
    path: str,
    port: int,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload_body = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload_body = {"detail": body}
        return exc.code, payload_body


def http_delete(path: str, port: int, timeout: float = 5.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_private_files(bundle_internal: Path) -> None:
    token_path = bundle_internal / "managed_client_token.txt"
    pool_path = bundle_internal / "managed_search_pool.json"

    if not token_path.exists() or not token_path.stat().st_size:
        raise SmokeFailure(f"managed_client_token.txt 缺失或为空: {token_path}")
    if not token_path.read_text(encoding="utf-8").strip():
        raise SmokeFailure("managed_client_token.txt 只有空白字符")
    log_step("managed_client_token.txt 注入", True, f"{token_path.stat().st_size} bytes")

    if not pool_path.exists():
        raise SmokeFailure(f"managed_search_pool.json 缺失: {pool_path}")
    pool = json.loads(pool_path.read_text(encoding="utf-8-sig"))
    providers = pool.get("providers") or {}
    if not providers:
        raise SmokeFailure("managed_search_pool.json 缺 providers")
    for name, entry in providers.items():
        missing = REQUIRED_POOL_PROVIDER_FIELDS - set(entry.keys())
        if missing:
            raise SmokeFailure(f"provider {name} 缺字段: {missing}")
    routing = pool.get("routing") or {}
    if not routing.get("primary"):
        raise SmokeFailure("managed_search_pool.json 缺 routing.primary")
    limits = pool.get("limits") or {}
    missing_limits = REQUIRED_POOL_LIMIT_FIELDS - set(limits.keys())
    if missing_limits:
        raise SmokeFailure(f"managed_search_pool.json limits 缺字段: {missing_limits}")
    log_step(
        "managed_search_pool.json schema",
        True,
        f"{len(providers)} providers, primary={routing.get('primary')}, "
        f"secondary={routing.get('secondary')}, native_fallback={routing.get('native_fallback')}",
    )

    if not (bundle_internal / "skill" / "SKILL.md").exists():
        raise SmokeFailure("skill/SKILL.md 未打入 _internal/skill/")
    independent_review_template = bundle_internal / "skill" / "plan-template" / "independent-review.md"
    lint_report_template = bundle_internal / "skill" / "plan-template" / "lint-report.md"
    if not independent_review_template.exists():
        raise SmokeFailure(f"independent-review.md 模板未打入: {independent_review_template}")
    if not lint_report_template.exists():
        raise SmokeFailure(f"lint-report.md 模板未打入: {lint_report_template}")

    quality_script = bundle_internal / "skill" / "scripts" / "quality_check.ps1"
    if not quality_script.exists():
        raise SmokeFailure(f"quality_check.ps1 未打入: {quality_script}")
    quality_script_text = quality_script.read_text(encoding="utf-8-sig")
    if not re.search(r"^\s*\[string\]\$OutputPath\b", quality_script_text, re.MULTILINE):
        raise SmokeFailure(f"quality_check.ps1 缺 -OutputPath 参数: {quality_script}")
    if not (bundle_internal / "frontend" / "dist").exists():
        raise SmokeFailure("frontend/dist 未打入 _internal/frontend/dist/")
    pandoc_path = bundle_internal / "pandoc.exe"
    if not pandoc_path.exists() or not pandoc_path.stat().st_size:
        raise SmokeFailure(f"pandoc.exe 缺失或为空: {pandoc_path}")
    log_step("skill/、frontend/dist/ 与 pandoc.exe 注入", True)
    log_step("S5 review 模板与新版 quality_check.ps1 注入", True)


def wait_for_server(port: int, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = http_get("/api/health", port, timeout=1.5)
            if payload.get("status") == "ok":
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout):
            pass
        time.sleep(0.5)
    raise SmokeFailure(f"FastAPI 在 {timeout}s 内没在 127.0.0.1:{port} 上响应 /api/health")


def check_settings(port: int) -> None:
    settings = http_get("/api/settings", port)
    if settings.get("mode") != "managed":
        raise SmokeFailure(f"默认模式不是 managed: {settings.get('mode')}")
    if not settings.get("api_base", "").startswith("https://"):
        raise SmokeFailure(f"api_base 未注入: {settings.get('api_base')}")
    if not settings.get("model"):
        raise SmokeFailure("model 未注入")
    if settings.get("api_key") not in ("***", ""):
        raise SmokeFailure("api_key 未脱敏（/api/settings 不应返回真实 token）")
    if settings.get("custom_api_key") not in ("***", ""):
        raise SmokeFailure("custom_api_key 未脱敏")
    skill_dir = settings.get("skill_dir", "")
    if "_internal" not in skill_dir.replace("/", "\\"):
        raise SmokeFailure(f"skill_dir 没指向 _internal/skill: {skill_dir}")
    log_step(
        "managed 模式运行时配置 + 凭据脱敏",
        True,
        f"model={settings.get('model')}, skill_dir 指向 _internal",
    )


def check_project_scaffolding(port: int, temp_workspace: Path) -> tuple[str, Path]:
    payload = {
        "name": "smoke-test-project",
        "workspace_dir": str(temp_workspace),
        "project_type": "specialized-research",
        "theme": "smoke test theme",
        "target_audience": "internal",
        "deadline": "2026-05-01",
        "expected_length": "1000",
        "notes": "automated smoke test",
    }
    result = http_post_json("/api/projects", port, payload)
    project_id = result["project_id"]
    project_dir = Path(result["project"]["project_dir"])

    plan_dir = project_dir / "plan"
    if not plan_dir.is_dir():
        raise SmokeFailure(f"plan/ 未创建: {plan_dir}")

    actual_plan_files = {p.name for p in plan_dir.glob("*.md")}
    missing = REQUIRED_PLAN_FILES - actual_plan_files
    if missing:
        raise SmokeFailure(f"plan 文件缺失: {missing}")
    forbidden_present = FORBIDDEN_PLAN_FILES & actual_plan_files
    if forbidden_present:
        raise SmokeFailure(f"违禁 plan 文件存在: {forbidden_present}")
    log_step(
        "项目脚手架 + 退役文件守门",
        True,
        f"{len(actual_plan_files)} plan 文件, 无 project-info.md/gate-control.md",
    )

    workspace = http_get(f"/api/projects/{project_id}/workspace", port)
    if workspace.get("stage_code") != "S0":
        raise SmokeFailure(f"初始阶段不是 S0: {workspace}")
    completed = workspace.get("completed_items") or []
    if "project-overview.md 创建" not in completed:
        raise SmokeFailure(f"S0 未勾选 project-overview.md 创建: {completed}")
    log_step("workspace API 返回 S0 + project-overview.md 已勾选", True)

    stage_gates = (plan_dir / "stage-gates.md").read_text(encoding="utf-8")
    if "[x] project-overview.md 创建" not in stage_gates:
        raise SmokeFailure("stage-gates.md 未回写 project-overview.md 勾选状态")
    if "S7 交付归档" not in stage_gates:
        raise SmokeFailure("stage-gates.md 缺 S7 阶段")
    log_step("stage-gates.md 后端回写覆盖 S0-S7", True)

    files = http_get(f"/api/projects/{project_id}/files", port).get("files") or []
    if any(f.endswith("project-info.md") for f in files):
        raise SmokeFailure("/api/projects/{id}/files 泄漏了 project-info.md")
    log_step(
        "/api/projects/{id}/files 过滤 project-info.md",
        True,
        f"{len(files)} files",
    )

    return project_id, project_dir


def check_s5_endpoint_guards(port: int, project_id: str) -> None:
    status, payload = http_request_json(
        f"/api/projects/{project_id}/lint-report",
        port,
        method="POST",
    )
    if status != 400 or "S5" not in str(payload.get("detail", "")):
        raise SmokeFailure(f"lint-report S0 拒绝不符合预期: status={status}, payload={payload}")
    log_step("S0 下 lint-report endpoint 拒绝", True)

    status, payload = http_request_json(
        f"/api/projects/{project_id}/independent-review/stream",
        port,
        method="POST",
        payload={"resume": False, "run_id": "smoke-s0"},
    )
    if status != 400 or "S5" not in str(payload.get("detail", "")):
        raise SmokeFailure(
            f"independent-review S0 拒绝不符合预期: status={status}, payload={payload}"
        )
    log_step("S0 下 independent-review endpoint 拒绝", True)


def check_review_tools(port: int, project_id: str, project_dir: Path) -> None:
    report_path = project_dir / "content" / "report_draft_v1.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# 测试报告\n\n"
        "## 一、关键发现\n\n"
        "数据显示，当前流程已经完成基础验证。数据来源：自动化打包 smoke。\n\n"
        "## 二、行动建议\n\n"
        "建议继续补齐真实业务材料，并在交付前完成人工复核。\n",
        encoding="utf-8",
    )

    quality = http_post(f"/api/projects/{project_id}/quality-check", port)
    if quality.get("status") != "ok":
        raise SmokeFailure(f"quality-check 失败: {quality}")
    if "检查摘要" not in (quality.get("output") or ""):
        raise SmokeFailure(f"quality-check 输出缺检查摘要: {quality}")
    log_step("打包态 quality-check 脚本", True)

    exported = http_post(f"/api/projects/{project_id}/export-draft", port, timeout=20.0)
    if exported.get("status") != "ok":
        raise SmokeFailure(f"export-draft 失败: {exported}")
    output_path = Path(exported.get("output_path") or "")
    if output_path.suffix.lower() != ".docx" or not output_path.exists():
        raise SmokeFailure(f"export-draft 未生成 docx: {exported}")
    if "已生成可审草稿" not in (exported.get("output") or ""):
        raise SmokeFailure(f"export-draft 输出缺生成提示: {exported}")
    log_step("打包态 export-draft + bundled Pandoc", True, str(output_path))


def delete_test_project(port: int, project_id: str) -> None:
    http_delete(f"/api/projects/{project_id}", port)
    projects = http_get("/api/projects", port)
    still_there = [p for p in projects if p["id"] == project_id]
    if still_there:
        raise SmokeFailure(f"删除后项目仍在 registry: {project_id}")
    log_step("测试项目删除 + registry 清理", True)


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            import signal
            import os as _os

            _os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_smoke(bundle_dir: Path, port: int) -> bool:
    if sys.platform != "win32":
        print(f"[SKIP] 本脚本只在 Windows 上有意义（当前 {sys.platform}）")
        return False

    exe_path = bundle_dir / DEFAULT_EXE_NAME
    bundle_internal = bundle_dir / "_internal"
    if not exe_path.exists():
        raise SmokeFailure(f"找不到 exe: {exe_path}（先运行 build.bat 打包）")
    if not bundle_internal.is_dir():
        raise SmokeFailure(f"找不到 _internal/: {bundle_internal}")

    print(f"[1/4] 静态校验打包产物: {bundle_dir}")
    check_private_files(bundle_internal)

    if port_in_use(port):
        raise SmokeFailure(
            f"端口 {port} 已被占用。先关闭本地开发服务或已经在跑的 exe，再重跑。"
        )

    print(f"[2/4] 启动 exe 并等待 FastAPI ({SERVER_BOOT_TIMEOUT_SECONDS}s 超时)...")
    proc = subprocess.Popen(
        [str(exe_path)],
        cwd=str(bundle_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    temp_workspace = Path(tempfile.mkdtemp(prefix="consulting-smoke-"))
    created_project_id: str | None = None
    try:
        wait_for_server(port, SERVER_BOOT_TIMEOUT_SECONDS)
        log_step("exe 启动 + /api/health", True)

        print(f"[3/4] 调用 HTTP API 验证业务流（不打 /api/chat）...")
        check_settings(port)
        created_project_id, project_dir = check_project_scaffolding(port, temp_workspace)
        check_s5_endpoint_guards(port, created_project_id)
        check_review_tools(port, created_project_id, project_dir)
        delete_test_project(port, created_project_id)
        created_project_id = None
        return True
    finally:
        print(f"[4/4] 清理...")
        if created_project_id is not None:
            try:
                http_delete(f"/api/projects/{created_project_id}", port)
            except Exception:
                pass
        kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(temp_workspace, ignore_errors=True)
        log_step("进程 kill + 临时工作区清理", True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Packaged bundle smoke test (API-level, no LLM).")
    parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_BUNDLE_DIR,
        help=f"打包产物目录（默认 {DEFAULT_BUNDLE_DIR}）",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"exe 监听端口（默认 {DEFAULT_PORT}）")
    args = parser.parse_args()

    print(f"=== Consulting Report packaged bundle smoke test ===")
    try:
        ok = run_smoke(args.bundle, args.port)
    except SmokeFailure as e:
        print(f"\n[FAIL] {e}")
        return 1
    except Exception as e:
        print(f"\n[ERROR] 未预期异常: {e!r}")
        return 2

    if ok:
        print("\n=== ALL SMOKE CHECKS PASSED ===")
        print("剩余必须人工验证：GUI 弹窗渲染、真实 chat 往返、流式体感、web_search→fetch_url→write_file 门禁、杀软首次拦截。")
        return 0
    return 0


def test_packaged_bundle_smoke() -> None:
    if os.environ.get("RUN_PACKAGED_SMOKE") != "1":
        pytest.skip("Packaged smoke needs a fresh dist; set RUN_PACKAGED_SMOKE=1 to run it under pytest.")
    run_smoke(DEFAULT_BUNDLE_DIR, DEFAULT_PORT)


if __name__ == "__main__":
    sys.exit(main())
