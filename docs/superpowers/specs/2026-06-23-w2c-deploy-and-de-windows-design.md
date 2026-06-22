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
| Part A | 去 Windows 化导出：删脚本层，Python 直调 pandoc | 代码（spec/plan + Codex 双轨审 + pytest） |
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
    # 解析 pandoc：① 桌面打包态包内 pandoc ② 系统 pandoc ③ 友好错误
    # 算 output_path = output_dir / (basename_without_ext + ".docx")
    # subprocess.run([pandoc, report_path, "-o", output_path], ...)
    # 返回 {"status": "ok"/"error", "output": ..., "output_path": str(output_path)}
```

**pandoc 解析顺序**（保桌面 Windows 不变 + 解 Linux/mac）：
1. **包内 pandoc**：`get_base_path() / "pandoc.exe"`，再 `get_base_path() / "pandoc" / "pandoc.exe"`（对应原 `.ps1` 的两个候选 `..\..\pandoc.exe` / `..\..\pandoc\pandoc.exe`，且 `consulting_report.spec` 用 `resolve_bundle_pandoc` 把 pandoc 打到包根 `.`）。
2. **系统 pandoc**：`shutil.which("pandoc")`（Linux `apt install pandoc` / mac brew）。
3. 都没有 → `{"status": "error", "output": "未找到 pandoc：...请安装 pandoc 或重装完整安装包。"}`（不抛异常，端点返回该 dict）。

> 注：解析逻辑放在新 helper `_resolve_pandoc() -> str | None`，纯函数易测。Windows 包内候选用 `.exe`；非 Windows 系统 pandoc 无后缀。`get_base_path()` 在 `backend/config.py`，打包态返回 `sys._MEIPASS`、开发态返回仓库根。

**输出文案**：原脚本 `Write-Host "已生成可审草稿: ..."` + 说明行移入 Python 返回（`output` 字段），保留「可审草稿」「不替代最终中文排版」措辞（packaging docs 测试锁 `可审草稿` 句子，见 §3.4）。

### 3.3 删除与改接
- 删 `skill/scripts/export_draft.ps1`、`skill/scripts/export_draft.sh`。
- `main.py:1104-1112` 端点：去掉 `script_path = scope.engine.get_script_path("export_draft.ps1")`，改 `export_reviewable_draft(report_path, output_dir)`。
- `get_script_path`（`skill.py:1802`）**唯一消费者就是导出**（已 grep 确认：`main.py:1109` + `test_main_api.py:1490` mock，无其它调用）→ 随 Part A 一并删除。
- `report_tools.py:_run_powershell` 删除。

### 3.4 测试同步
- `tests/test_report_tools.py`：从 `mock.patch("...subprocess.run")` 返回 powershell stdout，改为 mock pandoc subprocess（断言调用的是 pandoc + 正确 args + output_path 由 Python 计算而非 stdout 解析）；新增「无 pandoc → status=error 友好提示」用例；新增 pandoc 解析顺序用例（包内优先于系统）。
- `tests/test_skill_assets.py`：删 `export_draft.sh` 存在性断言（line 11）、`export_draft.ps1` 的 UTF8-BOM（line 21-32）/ force-utf8-stdout（line 34）/ prefers-bundled-pandoc（line 135）三个 PowerShell 专属用例。N7 删 quality_check 的负向断言（line 17-19）保留。
- `tests/test_main_api.py:1487`：端点测试去掉 `get_script_path` mock 与 `export_draft.ps1` 入参，改断言新签名 `export_reviewable_draft(report_path, output_dir)`。
- `tests/test_packaging_docs.py`：锁的是 BUILD/WINDOWS_BUILD 文档里 `resolve_bundle_pandoc`/`pandoc.exe`/`可审草稿` 句子——**Windows 打包仍打 pandoc.exe、桌面态走包内 pandoc**，故这些句子不变、测试不动。若文档提到「PowerShell 导出脚本」则同步改为「Python 直调 pandoc」。
- `consulting_report.spec`：仍 `resolve_bundle_pandoc` 打 pandoc.exe；`a.scripts`（PyInstaller 分析产物，与 skill 脚本无关）不动。skill/scripts 目录若整目录打包，删 `.ps1`/`.sh` 后自然不打。

### 3.5 桌面打包零回归
Windows 桌面态：包内 pandoc.exe 仍在，导出改走 Python subprocess 调它，不再经 PowerShell。行为等价（同一 pandoc、同一产物），少一层进程。

## 4. Part B — N6 F2 收口（删 legacy 解析器）

### 4.1 现状（如实）
N6 用 `MaterialConverter`（markitdown 全替换）接管文档转换，但 `skill.py` 仍留 feature-flag 期的 legacy 解析器：`_legacy_read_document`（1676）、`_read_docx`（3055）、`_read_xlsx`（3067）、`_read_pdf`（3083）。**运行时永不命中**——生产路径 `ChatHandler.__init__` 总装 converter；`_legacy_read_document` 仅在 `skill.py:1654` 作「无 converter 的纯单测回退」被调（`self._converter is None` 时）。即它不是纯死码，有单测消费者。

### 4.2 目标
删 4 个函数 + 让那几个「不装 converter 直接构造 SkillEngine」的单测改为注入一个假/最小 converter（或显式 skip），消除回退依赖后删除。plan 阶段先 grep 出所有 `_converter is None` / 无 converter 构造点，逐个改测。

### 4.3 取舍（如实写明）
删 legacy 回退后，**markitdown 失败不再有 legacy 重试**，直接友好错误。这是可接受的：markitdown 是 N6 既定跨平台路径、已验证；Linux 试用本就只走 markitdown。Windows 打包逐格式 smoke（确认打包态 markitdown 处理各格式）是独立 packaging-QA 任务（§6），**不是保留死路径的理由**。

### 4.4 测试
`tests/test_skill_engine.py` / `tests/test_workspace_materials.py` 里命中无-converter 回退的用例改注入假 converter；删 legacy 函数后全套 pytest 绿。

## 5. Part C — 部署 runbook（kr-web-01，反代 + Cloudflare）

**目标站点**：`https://consulting.z0y0h.work` → kr-web-01（腾讯云首尔，2C2G+swap+40G，Debian 13，SSH 2233，已 fail2ban+ufw+komari）。运维登记在 VPS-fix 库 `notes/kr-web-01.md`（本项目不重复运维细节，只记 app 部署）。

### 5.1 装机
- `apt install pandoc nginx`（pandoc 供导出）。
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
- 转发头：`X-Forwarded-Proto https`、`Host $host`（CSRF Origin 校验 + cookie_secure 依赖正确 proto/host）。
- **SSE 端点关 buffering（关键，否则流式聊天/审查全断）**：对 `/api/chat/stream`、`/api/projects/*/independent-review/stream` 设 `proxy_buffering off; proxy_read_timeout 600s; proxy_cache off;`。可对整个 `/api/` 关 buffering 简化（非流式端点不受损）。
- 上传体积：`client_max_body_size` 调到 ≥ N6 素材上限（`material_limits.MAX_HEAVY_MATERIAL_BYTES`=25MB，设 30M 留余量）。

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
6. **导出可审草稿 → 成功下载 docx**（验 Part A 跨平台）。
7. 第二账号建项目，按 id/名称访问第一账号项目 → 404（验租户隔离仍生效）。

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

- [ ] Part A：非 Windows pytest 全绿；导出端点本地 mac/Linux 真调 pandoc 出 docx；Windows 打包态走包内 pandoc.exe 不回归。
- [ ] Part B：4 个 legacy 函数删除；无-converter 单测改注入假 converter；全套 pytest 绿。
- [ ] Part C：§5.10 Smoke 7 步全过；同事可注册登录写作导出；admin 可管理。
- [ ] 文档：worklist W2-C 状态更新；CLAUDE.md 增「## W2-C 部署」段；cutover report。
