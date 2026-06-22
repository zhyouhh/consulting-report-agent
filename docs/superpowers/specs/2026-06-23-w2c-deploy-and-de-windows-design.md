# W2-C：部署上线 + 去 Windows 化导出 + N6 F2 收口 — 设计

- 日期：2026-06-23
- 状态：设计稿（待用户 review → writing-plans）
- 关联：W2-B 多租户基座已全部 merge main（`ed6da02`）；本轮把它真正部署上线让同事用。
- 前序真值源：`docs/current-worklist.md`「🟢 服务器化」簇、项目 `CLAUDE.md`「## W2-B/*」段、`docs/managed-proxy-deployment.md`。

## 1. 背景与目标

W2-B 三段（B1 租户基座 / B2 中央计费 / B3 admin+安全硬化+custom 激活）已落地合并，引擎层面「登录即隔离的多用户 Web 产品」已成立，但**还没真部署，没人能用**。本轮（= worklist 的 W2-C）把它部署到试用机 kr-web-01，让同事打开浏览器就能注册、登录、写报告、导出。

阻塞「同事能用」的唯一跨平台硬约束是**导出可审草稿在非 Windows 上报错**（`export_draft.ps1` 经 `report_tools.py` 调硬编码 `powershell`）。S0–S5 写作/审查流早在 mac web 模式验证过跨平台，只有导出依赖 PowerShell。故本轮 = 去 Windows 化导出（前置）+ 部署（主体），并顺手收口 N6 F2（删 legacy 解析器死路径）。

**成功标准**：同事在 `https://consulting.z0y0h.work` 用邀请码注册 → 登录 → 建项目 → 聊天写作 → 独立审查 → **导出可审草稿成功**；admin 能进后台管用户/配额。

## 2. 范围

| 部分 | 内容 | 形态 |
|---|---|---|
| Part A | 去 Windows 化导出：删脚本层，Python 直调 pandoc（原子发布）+ **web 下载契约**（让浏览器用户真拿到 docx）+ **SSE 空闲心跳**（防 CF 断流） | 代码（spec/plan + Codex 双轨审 + pytest） |
| Part B | N6 F2 收口：删 skill.py 4 个 legacy 解析器 + 改无-converter 单测 | 代码（同上） |
| Part C | 部署 runbook：kr-web-01 反代 + Cloudflare HTTPS | 运维文档 + 交互式执行（CF DNS 走 MCP） |

**不在本轮**：B3 遗留硬化 backlog（原子 reserve / 多 worker 共享 `_LOGIN_FAILS`·`_miss_counter`·`_RUNTIME_ALLOWED_HOSTS` / pinned-IP-SNI 防 DNS rebinding / CAPTCHA·MFA / SSE 401 统一跳登录）——单 worker 试用阶段都不阻塞，转正经生产实例再做。Windows 打包 smoke（逐格式验 markitdown）独立于本轮，见 §6。

## 3. Part A — 去 Windows 化导出

### 3.1 现状
导出本质是一句 `pandoc input.md -o output.docx` + 打印一行。但现状包了三层：`skill/scripts/export_draft.ps1`（PowerShell）+ `export_draft.sh`（bash，已存在但**从未被 wire**）+ `report_tools.py:_run_powershell`（subprocess 调 powershell）+ 正则 `已生成可审草稿:\s*(.+)` 刨 stdout 取路径。`main.py:1109` 经 `get_script_path("export_draft.ps1")` 取脚本路径传入。非 Windows 无 `powershell` 命令 → 直接报错。

### 3.2 目标设计（奥卡姆：删脚本层）
`report_tools.py` 改为 Python 直接 `subprocess` 调 pandoc，自己解析 pandoc 路径，output_path 在 Python 里算（不再正则刨 stdout）。

新签名：
```python
def export_reviewable_draft(report_path: str, output_dir: str) -> dict:
    # 解析 pandoc：① 打包态/Windows 包内 pandoc.exe ② 系统 pandoc ③ 友好错误
    # final = output_dir / (basename_without_ext + ".docx")
    # 原子发布（见下）：pandoc 写同目录唯一 temp .docx → 成功 os.replace(temp, final)
    # 返回 {"status": "ok"/"error", "output": ..., "output_path": str(final), "filename": final.name}
```

**输出 docx 必须原子发布（Codex R3 BLOCKER）**：pandoc **不直接写终名** `report_draft_v1.docx`。`os.replace` 只护了源 `.md` 的读，不护输出 `.docx`——并发双击/双标签导出、下载流读到一半被覆写、旧 docx 被原地截断重写，都会损坏。做法：`fd, temp = tempfile.mkstemp(dir=output_dir, suffix=".docx")`（**保 `.docx` 后缀**让 pandoc 推断 docx writer）→ **立即 `os.close(fd)`**（Windows 文件占用，pandoc 才能写该路径）→ pandoc `-o temp` → 成功后 `os.replace(temp, final)` 一次到终名。**任一失败（pandoc 非零退出 / `os.replace` 抛错）都当导出失败**：保留旧 final 不动 + 清 temp + 返回 `{status:"error", ...}`。这样导出全程锁外、输出发布原子、下载端点要么读到完整旧版要么完整新版。

**导出不得阻塞事件循环（Codex R4 BLOCKER）**：现端点是 `async def export_draft`（`main.py:1105`）直接调同步 `subprocess.run`（`report_tools.py:6`）。单 worker 异步服务里，`async def` 路由跑在事件循环上，阻塞 pandoc 子进程会**卡死整个 loop**——拖垮无关用户请求，且**挡住 §3.7 异步流心跳的发送**，反噬该修复。做法：端点改**同步 `def`**（FastAPI 自动在线程池跑）或保持 `async def` 但 `await run_in_threadpool(export_reviewable_draft, report_path, output_dir)`。**不取 per-project request lock**（§3.6 已论证）；R3 的「专用 executor 防 RLock 重入」坑**在此不适用**（export 不取锁，可用默认 `run_in_threadpool` / sync 路由线程池）。加 source/test 守卫：「pandoc 导出不在 async 路由内直接同步调用」。

**pandoc 解析顺序**（保桌面 Windows 不变 + 解 Linux/mac）：
1. **包内 pandoc.exe——仅在「打包态或 Windows」才优先**（`getattr(sys, "frozen", False)` 或 `sys.platform == "win32"`）：`get_base_path() / "pandoc.exe"`，再 `get_base_path() / "pandoc" / "pandoc.exe"`（对应原 `.ps1` 的两个候选，且 `consulting_report.spec` 用 `resolve_bundle_pandoc` 把 pandoc 打到包根 `.`）。**非 Windows 非打包态绝不试 `.exe`**——`WINDOWS_BUILD.md:54-55,69` 要求维护者把 `pandoc.exe` 放仓库根，而 `get_base_path()` 在开发/服务器态 = 仓库根；无此守卫，Linux 会找到根目录 `pandoc.exe` 去 exec Windows 二进制（**Codex R1 BLOCKER**）。
2. **系统 pandoc**：`shutil.which("pandoc")`（Linux `apt install pandoc` / mac brew）。
3. 都没有 → `{"status": "error", "output": "未找到 pandoc：...请安装 pandoc 或重装完整安装包。"}`（不抛异常，端点返回该 dict）。

> 注：解析逻辑放在新 helper `_resolve_pandoc() -> str | None`，纯函数易测。`get_base_path()` 在 `backend/config.py`，打包态返回 `sys._MEIPASS`、开发态返回仓库根。**测试须覆盖：非 Windows 非 frozen 时即便根目录有 `pandoc.exe` 也跳过、走 `which`**（守卫的回归锁）。

**输出文案**：原脚本 `Write-Host "已生成可审草稿: ..."` + 说明行移入 Python 返回（`output` 字段），保留「可审草稿」「不替代最终中文排版」措辞（packaging docs 测试锁 `可审草稿` 句子，见 §3.4）。

### 3.3 删除与改接
- 删 `skill/scripts/export_draft.ps1`、`skill/scripts/export_draft.sh`。
- `main.py:1104-1112` 端点：去掉 `script_path = scope.engine.get_script_path("export_draft.ps1")`，改 `export_reviewable_draft(report_path, output_dir)`。
- `get_script_path`（`skill.py:1802`）**唯一消费者就是导出**（已 grep 确认：`main.py:1109` + `test_main_api.py:1490` mock，无其它调用）→ 随 Part A 一并删除。
- `report_tools.py:_run_powershell` 删除。
- **更新仍在教用户跑导出脚本的 skill 模块文档（Codex R1 BLOCKER：删脚本后模块文档悬挂引用）**：`skill/modules/final-delivery.md:34,40`（字面 `powershell ... export_draft.ps1` / `bash scripts/export_draft.sh` 命令块）改为「应用内『导出可审草稿』按钮」描述；`skill/modules/quality-review.md:136`「使用导出脚本」改「使用应用导出操作」。

### 3.4 测试同步
- `tests/test_report_tools.py`：从 `mock.patch("...subprocess.run")` 返回 powershell stdout，改为 mock pandoc subprocess（断言调用的是 pandoc + 正确 args + output_path 由 Python 计算而非 stdout 解析）；新增「无 pandoc → status=error 友好提示」用例；新增 pandoc 解析顺序用例（包内优先于系统）。
- `tests/test_skill_assets.py`：删 `export_draft.sh` 存在性断言（line 11）、`export_draft.ps1` 的 UTF8-BOM（line 21-32）/ force-utf8-stdout（line 34）/ prefers-bundled-pandoc（line 135）三个 PowerShell 专属用例。N7 删 quality_check 的负向断言（line 17-19）保留。
- `tests/test_main_api.py:1487`：端点测试去掉 `get_script_path` mock 与 `export_draft.ps1` 入参，改断言新签名 `export_reviewable_draft(report_path, output_dir)`。
- `tests/test_packaging_docs.py`：锁的是 BUILD/WINDOWS_BUILD 文档里 `resolve_bundle_pandoc`/`pandoc.exe`/`可审草稿` 句子——**Windows 打包仍打 pandoc.exe、桌面态走包内 pandoc**，故这些句子不变、测试不动。若文档提到「PowerShell 导出脚本」则同步改为「Python 直调 pandoc」。
- `consulting_report.spec`：仍 `resolve_bundle_pandoc` 打 pandoc.exe；`a.scripts`（PyInstaller 分析产物，与 skill 脚本无关）不动。skill/scripts 目录若整目录打包，删 `.ps1`/`.sh` 后自然不打。
- **新增 source-guard**：`skill/` 下 active 文档不再含 `scripts/export_draft`（防脚本引用回潮）。
- **前端测试**：`WorkspacePanel.jsx:exportDraft` 改为「成功触发下载（§3.6）+ `status !== "ok"` 当失败」后，加对应前端测试（现状把任意 200 当成功 + 只回显路径，Codex R1 BLOCKER）。

### 3.5 桌面打包零回归
Windows 桌面态：包内 pandoc.exe 仍在，导出改走 Python subprocess 调它，不再经 PowerShell。行为等价（同一 pandoc、同一产物），少一层进程。

### 3.6 Web 导出下载契约（Codex R1 BLOCKER：现状 web 用户拿不到 docx）

**现状缺陷**：`POST /export-draft` 返回 `{status, output, output_path}`，前端 `WorkspacePanel.jsx:exportDraft` 只 `showSuccess(已导出可审草稿：${output_path})`——`output_path` 是**服务器本地路径**（kr-web-01 上 `/var/lib/consulting-report/users/<uid>/projects/<pid>/output/...`），浏览器用户**根本够不到、下载不了**。且前端把任意 200 当成功，§3.2 的 `{status:"error"}` 不会被识别为失败。**这是「同事能导出报告」成功标准的直接缺口**，不是纯运维问题。

**设计**：
- 后端 `POST /export-draft` 仍服务端生成 docx + 返回 `{status, output_path, filename}`（桌面「存到项目文件夹」UX 保留）；**前端按 `status` 判成败**。导出产物文件名**确定**：`report_draft_v1.md` → `report_draft_v1.docx`（Python basename 逻辑，§3.2），落 `ensure_output_dir(project)` = `<project>/output`。
- 新增 `GET /api/projects/{project_id}/export-draft/download` → `FileResponse`（`Content-Disposition: attachment; filename=...`，经 `require_project` cookie 鉴权 + must_change_password 门——Codex R2 已确认 GET 豁免 CSRF、`require_project` 含改密门、租户隔离方向对）。
  - **不读「最近一份」**（Codex R2 BLOCKER：会选到 `output/` 里无关/手动/陈旧文件 + 并发点击竞态）。下载端点**只服务确定文件名** `report_draft_v1.docx`：在 `scope.engine` 的该项目 output 目录内解析，校验「解析后路径仍在该项目 output 目录内（防穿越）+ 文件存在」，否则 404；**绝不接受客户端任意 `filename`**。租户隔离由 `require_project`（canonical project_id + per-uid 引擎）天然保证。
  - **路由插点明确**（Codex R2 BLOCKER：防被 catch-all `GET /api/projects/{project_id}/files/{file_path:path}`（`main.py:817`）语义混淆）：注册在 `POST /api/projects/{project_id}/export-draft` 紧邻处，**路径不以 `/files` 开头**故不被其捕获；**路由测试断言** `GET .../export-draft/download` 命中本 handler、返回 `FileResponse`（`Content-Disposition` 头），**不是** JSON 文件读结果。
- **并发：锁外读，依赖 R3 原子写不变式（对 Codex R2「在 request lock 下导出」建议的技术反驳）**：export 读 `content/report_draft_v1.md` **不取** per-project request lock。理由——`chat_stream` 的 `request_lock`（RLock）**跨 yield 整轮持有**（`main.py:824-832` 注释明载），export 若取该锁会被整轮聊天生成阻塞到结束（可能数分钟），正是 R3 让 GET `/files` 锁外读所避免的冻结。R3 已确立所有 AI 写经 temp+`os.replace` 原子化（`skill.py:1316/1374`「no-lock read … never sees a half-written one」），pandoc 直接读路径同理（原子 rename → 必读到完整文件，非 torn read）。故 export 锁外读，与 GET `/files` 同模式；**plan 不得为 export 引入 request lock**（会重新引入 R3 已消除的冻结）。最坏只是导出比正在写的下一轮草稿旧一轮，语义可接受。
- 前端 `exportDraft`：成功后触发浏览器下载该 GET 端点（anchor href 或 fetch blob→save，**带 cookie 凭据**）；mode-agnostic（前端无桌面/web 分支，桌面 PyWebView 下载行为由 plan 验证，文件仍同时存项目文件夹兜底）。
- 测试：后端下载端点（属主成功、非属主 404、未生成 404、路径穿越拒绝、命中 FileResponse 非 catch-all）+ 前端 exportDraft（status 判定 + 触发下载）。

### 3.7 SSE 空闲心跳（Codex R3 BLOCKER：CF/nginx 会在长空闲时断流）

**问题**：nginx 关 buffering + `proxy_read_timeout 600s` 解决了缓冲，但模型长思考 / 工具长调用 / **无首包**（项目 P1 backlog 已知现象）期间，整条 SSE 流可能 >100s 不产出任何字节，Cloudflare 边缘会判空闲断连——流式聊天 / 独立审查在生产偶发失败。

**设计**：两条流在空闲时**至少每 ~20s 发一行 SSE 注释心跳** `: keepalive\n\n`（SSE 规范注释行，非 `data:`）。
- **前端零行为改动**（已核实）：两个消费者都过滤到 `data:` 负载——`IndependentReviewDrawer.jsx:105` `if (!block.startsWith('data: ')) continue`、`ChatPanel.jsx` 经 `extractSseDataPayload(line)` 对非 `data:` 行返 null（`:506-507`）。心跳注释行天然被忽略。仅加**容忍单测锁**（`extractSseDataPayload(': keepalive')===null` + review drawer 跳过注释块）防回归。
- **审查流（async generator，`main.py:1049-1056`）**：已有 `asyncio.wait_for(get, timeout=0.1)` + `continue` 空闲路径，记 last-yield 时间，>20s 未产出就 `yield ": keepalive\n\n"` 并重置。改动小、隔离。
- **聊天流（sync generator，`main.py:1208-1240` `generate()`）**：空闲在 `handler.chat_stream(...)` 内部首包/工具等待。**心跳只在 SSE 帧层（main.py）注入，绝不碰 chat.py 的 provider message/tool-call/`reasoning_content`/`tool_choice`/DeepSeek 逻辑**（纯 HTTP 流框架层，与官渠兼容正交）。机制（handler 长等待期周期 yield 心跳哨兵 vs 线程 + 队列多路复用包装，类比审查流）由 plan 选最小侵入解，**约束=DeepSeek 兼容回归不破 + 首包延迟期仍有字节到达**。
- 测试：前端容忍单测；后端「延迟首事件仍周期产出心跳」用例（审查流可直接测；聊天流按 plan 选定机制测）。

## 4. Part B — N6 F2 收口（删 legacy 解析器）

### 4.1 现状（如实）
N6 用 `MaterialConverter`（markitdown 全替换）接管文档转换，但 `skill.py` 仍留 feature-flag 期的 legacy 解析器：`_legacy_read_document`（1676）、`_read_docx`（3055）、`_read_xlsx`（3067）、`_read_pdf`（3083）。**运行时永不命中**——生产路径 `ChatHandler.__init__` 总装 converter；`_legacy_read_document` 仅在 `_converter_read_document`（`skill.py:1652`）里 `getattr(self, "_material_converter", None) is None` 时作「无 converter 的纯单测回退」被调。即它不是纯死码，有单测消费者。

### 4.2 目标
删 4 个函数 + 让命中无-converter 回退的单测改为注入假/最小 converter，消除回退依赖后删除。**正确符号（Codex R1 NIT）**：属性是 `self._material_converter`（非 `_converter`），回退调用点是 `_converter_read_document`。plan 阶段 grep 目标：`_material_converter`、`_legacy_read_document`、`_read_docx`、`_read_xlsx`、`_read_pdf`、`_converter_read_document`、`read_material_file`。

### 4.3 取舍（如实写明）
删 legacy 回退后，**markitdown 失败不再有 legacy 重试**，直接友好错误。这是可接受的：markitdown 是 N6 既定跨平台路径、已验证；Linux 试用本就只走 markitdown。Windows 打包逐格式 smoke（确认打包态 markitdown 处理各格式）是独立 packaging-QA 任务（§6），**不是保留死路径的理由**。

### 4.4 测试
`tests/test_skill_engine.py` / `tests/test_workspace_materials.py` 里命中无-converter 回退的用例改注入假 converter；删 legacy 函数后全套 pytest 绿。

## 5. Part C — 部署 runbook（kr-web-01，反代 + Cloudflare）

**目标站点**：`https://consulting.z0y0h.work` → kr-web-01（腾讯云首尔，2C2G+swap+40G，Debian 13，SSH 2233，已 fail2ban+ufw+komari）。运维登记在 VPS-fix 库 `notes/kr-web-01.md`（本项目不重复运维细节，只记 app 部署）。

### 5.1 装机
- `apt install pandoc nginx libreoffice`（pandoc 供导出；**libreoffice 供 N6 老 `.doc`/`.ppt` 转换 + `.xls` markitdown 失败回退**——`material_conversion.py:180,296` 依赖 `soffice`/`libreoffice`，缺它老 Office 格式上传会失败，Codex R1 NIT。2C2G+40G 上体积可接受；若想省空间可不装、并明确告知试用不支持老 `.doc`/`.ppt`）。
- `uv` 托管 **Python 3.12**（Debian 13 自带 3.13，curl_cffi/pydantic 无 wheel，与 mac 同坑）。
- git。

### 5.2 取码 + 前端产物 + 私有文件
- clone main → `/opt/consulting-report-agent`。
- **前端 dist（gitignored）**：在 mac `npm run build` 后 `rsync frontend/dist` 上去（2C2G build vite 易 OOM；rsync 最稳）。备选：服务器装 node20 自 build。
- **私有文件（gitignored）**：scp `managed_client_token.txt` + `managed_search_pool.json` 到仓库根（managed 模式 + 内置搜索依赖）。

### 5.3 venv + 依赖
- `uv venv --python 3.12 .venv` + `uv pip install --python .venv/bin/python -r requirements.txt`。

### 5.4 入口微调（小代码改，并入 Part A 的 plan）
`run_web.py` 现硬编码 `host="0.0.0.0"` / `port=8888` + 一行过期外网 IP 打印。改为：
- host/port 读 env（`CRA_BIND_HOST` 默认 `127.0.0.1`、`CRA_BIND_PORT` 默认 `8888`），systemd 绑 loopback（nginx 在前，app 不直接对外）。
- 删过期 IP 打印行。
- 保留 `app.state.auth_required=True` + `app.state.cookie_secure=True`（HTTPS 经 CF）+ `assert_safe_startup` + 缺 `CRA_ALLOWED_ORIGIN` 告警。
- **uvicorn 信任反代真实 IP（Codex R1 BLOCKER 配套，见 §5.7）**：`uvicorn.run(...)` 加 `proxy_headers=True, forwarded_allow_ips="127.0.0.1"`，使 `request.client.host` 取 nginx 注入的 `X-Forwarded-For`。否则 slowapi per-IP 登录限流（`get_remote_address`，`main.py:58`）+ 会话审计 IP（`request.client.host`，`main.py:381`）全塌成 nginx/CF 单 IP → 不同用户互相触发限流锁定。
> 必须用 `run_web.py` 作入口（裸 `uvicorn backend.main:app` 时 `cookie_secure` 默认 False → cookie 不安全）。

### 5.5 env（systemd EnvironmentFile，`/etc/consulting-report.env`，权限 600）
- `CRA_DATA_ROOT=/var/lib/consulting-report`（数据持久化在仓库外，升级不丢）。
- `CRA_INVITE_CODE=<邀请码>`（web 必设，否则 `assert_safe_startup` 拒启动）。
- `CRA_ALLOWED_ORIGIN=https://consulting.z0y0h.work`（缺它生产 cookie_secure 态所有写请求 fail-closed 403）。
- `CRA_BOOTSTRAP_ADMIN_USERNAME` + `CRA_BOOTSTRAP_ADMIN_PASSWORD`（首启建 admin、`must_change_password=True`、幂等）。
- `CRA_BIND_HOST=127.0.0.1` / `CRA_BIND_PORT=8888`。
- （不设 `CRA_COOKIE_INSECURE`——保持 secure。）

### 5.6 systemd 服务（`consulting-report.service`）
- `ExecStart=/opt/consulting-report-agent/.venv/bin/python run_web.py`，WorkingDirectory=仓库根，`EnvironmentFile=/etc/consulting-report.env`，`Restart=always`，专用非 root 用户（`consulting`，对 `CRA_DATA_ROOT` 有写权）。
- **单 worker 硬约束**：B2/B3 进程内状态（`_LOGIN_FAILS`/`_miss_counter`/`_RUNTIME_ALLOWED_HOSTS`/搜索单例/审查 store）都假设单进程。run_web 单 uvicorn 进程满足；**绝不加 `--workers >1` 或多实例**。

### 5.7 nginx 反代
- 443 → `127.0.0.1:8888`，装 **Cloudflare Origin Certificate**（免费 15 年、免续期）。
- **真实客户端 IP（Codex R1 BLOCKER；R2 NIT：指令上下文要对）**：橙云代理后，nginx 看到的 remote_addr 是 Cloudflare 边缘 IP。`set_real_ip_from <CF IP 段>;` + `real_ip_header CF-Connecting-IP;` 是 **http/server 上下文指令**（写在 `http{}` 或 `server{}` 块，**不是** per-location include；CF IP 段从 `https://www.cloudflare.com/ips/` 取，逐行列进 conf）。`location` 里只放 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`。再配合 §5.4 uvicorn `forwarded_allow_ips="127.0.0.1"`，app 才拿到真用户 IP（per-IP 限流/审计才正确）。
- 转发头（per-location，即下文 `proxy_common` = 这些 `proxy_set_header`）：`Host $host;`、`X-Forwarded-Proto https;`（好实践；**注意**：本 app 的 `cookie_secure` 由 `run_web.py` 进程状态决定、不读 `X-Forwarded-Proto`，CSRF 校验 `Origin`/`Referer` 对比 `CRA_ALLOWED_ORIGIN`、不读代理 Host——这两头不是这俩机制的依赖，保留以备 URL 构造等用途）。
- **SSE 端点关 buffering（关键，否则流式聊天/审查全断）**——给出**可直接用的精确 location**（`location /api/projects/*/...` 不是合法 nginx 通配，原写法不可用）：
  ```nginx
  # 流式：聊天 + 独立审查（main.py:1074 chat stream / :896 review stream）
  location = /api/chat/stream { proxy_pass http://127.0.0.1:8888; proxy_buffering off; proxy_read_timeout 600s; proxy_cache off; include proxy_common; }
  location ~ ^/api/projects/[^/]+/independent-review/stream$ { proxy_pass http://127.0.0.1:8888; proxy_buffering off; proxy_read_timeout 600s; proxy_cache off; include proxy_common; }
  ```
  > 简化可选：对整个 `location /api/ { proxy_buffering off; ... }` 统一关 buffering（非流式端点不受损），省去逐条 regex。`proxy_common` = 上面的 `proxy_set_header` 转发头片段（per-location）；`set_real_ip_from`/`real_ip_header` 是 http/server 上下文指令、**只放 `http{}`/`server{}` 块**、不进 `proxy_common`。
- 上传体积：`client_max_body_size 30m;`（≥ N6 素材上限 `material_limits.MAX_HEAVY_MATERIAL_BYTES`=25MB 留余量）。

### 5.8 Cloudflare（走 CF MCP）
- A 记录 `consulting.z0y0h.work` → kr-web-01 公网 IP，**橙云代理**（DDoS 防护 + 藏源站 IP）。
- SSL/TLS 模式 = **Full (strict)**（源站装 Origin Cert）。
- 签发 Origin Certificate 装到 nginx。

### 5.9 ufw
- 放行 443（理想：仅 Cloudflare IP 段，进一步收紧）+ SSH 2233。
- **挡死直连 8888**（app 只经 nginx）。

### 5.10 Smoke（部署验收）
1. `curl -sS https://consulting.z0y0h.work/api/health`（或既有 health 路由）→ 200。
2. 浏览器开站 → 登录门出现。
3. 邀请码注册新账号 → 自动登录。
4. admin 账号登录 → 强制改密 → 进 admin 面板看用户列表/配额。
5. 建项目 → 聊天（managed deepseek-v4-pro 真链路，验薄网关可达）→ 触发独立审查（验 SSE 流式不断）。
6. **导出可审草稿 → 浏览器真实下载到 .docx**（验 Part A 的 §3.6 web 下载契约，不是只回显路径）。
7. 第二账号建项目，按 id/名称访问第一账号项目 → 404（验租户隔离仍生效）。
8. **真实 IP 透传校验**：服务器日志/会话记录里登录 IP 是真用户 IP（非 CF/nginx 单 IP），确认 §5.4+§5.7 real-ip 链路生效（否则 per-IP 限流会误锁）。

## 6. 安全与已知限制

- **kr-web-01 非自有账号**（渠道商代购、仅试用）：`managed_client_token` + 搜索池凭据落非自有机 = 轻度风险。试用可接受；**转生产换自有/公司实例时轮换这些凭据 + 邀请码 + admin 密码**。
- **单 worker / 单进程**：B2/B3 限流·计费·白名单·审查 store 状态进程内。试用规模够用；横向扩展需先做 backlog 里的「多 worker 共享状态」。
- **DNS rebinding TOCTOU**：B3 SSRF 白名单是安全边界、非连接层 pin IP（沿用 B3 已知限制）。
- **Windows 打包逐格式 smoke**（原 N6 F2 前置）：本轮删 legacy 回退后，若仍要分发 Windows 桌面包，需独立做一次打包态 markitdown 逐格式验证——**列为后续 packaging-QA，不阻塞 Linux 试用部署**。
- **软帽非原子 reserve**（B2 沿用）；桌面 local 受 ¥5/天默认 cap（用户保持）。

## 7. 执行方式

- **Part A + B + 入口微调（§5.4）**：合并为一个代码 plan（writing-plans），subagent-driven 实施 + 每 commit Codex spec/quality 双轨独立 review（项目纪律），本地 pytest 全绿（mac realpath 4 例已知差异除外）。
- **Part C 部署**：写成 runbook（可放 `docs/deploy-kr-web-01.md` 或并入本 spec §5 执行清单），**交互式执行**——CF DNS/Origin Cert 走 CF MCP；机器上命令逐条给用户 `! ssh` 执行或贴出。非 TDD plan 料。
- 顺序：先 Part A+B 代码落地 + 测试绿 + merge → 再 Part C 部署（部署时导出已可用）。

## 8. 验收清单

- [ ] Part A：非 Windows pytest 全绿；导出端点本地 mac/Linux 真调 pandoc 出 docx；Windows 打包态走包内 pandoc.exe 不回归；pandoc 解析守卫（非 Windows 不试 `.exe`）有回归锁。
- [ ] Part A web 下载（§3.6）：下载端点属主 200 + 非属主 404 + 未生成 404 + 路径穿越拒绝 + 命中 FileResponse 非 catch-all；前端按 `status` 判成败 + 触发浏览器下载；source-guard 守 `skill/` 无 `scripts/export_draft` 残留。
- [ ] Part A 原子发布（§3.2）+ 不阻塞 loop + SSE 心跳（§3.7）：导出 temp.docx→`os.close(fd)`→pandoc→`os.replace` 终名、任一失败留旧；导出经 sync 路由/`run_in_threadpool`（不取锁）+ 守卫「pandoc 不在 async 路由直接同步调」；两条流空闲 ~20s 发 `: keepalive`、前端容忍单测、聊天流心跳不破 DeepSeek 兼容。
- [ ] Part B：4 个 legacy 函数删除；无-converter 单测改注入假 converter；全套 pytest 绿。
- [ ] Part C：§5.10 Smoke 8 步全过（含 web 真实下载 + 真实 IP 透传）；同事可注册登录写作导出；admin 可管理。
- [ ] 文档：worklist W2-C 状态更新；CLAUDE.md 增「## W2-C 部署」段；cutover report；skill 模块文档去脚本引用。
