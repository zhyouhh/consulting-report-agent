# W2-C Cutover — 去 Windows 化导出 + web 下载 + 部署前置 + N6 F2（Part A+B）

日期：2026-06-23
分支：`feat/w2c-de-windows-export`（**未 merge / 未 push**）
spec：`docs/superpowers/specs/2026-06-23-w2c-deploy-and-de-windows-design.md`（Codex 5 轮 APPROVED）
plan：`docs/superpowers/plans/2026-06-23-w2c-de-windows-export-and-deploy-prep.md`（8 task TDD，Codex 4 轮 APPROVED）

## 目标与范围

把「导出可审草稿」做成跨平台（Linux/mac/Windows）+ 让 web 用户真能下载 docx，并补齐把 W2-B 多租户引擎部署到 kr-web-01 所需的前置代码。Part C（真机部署 runbook）不在本轮，交互式执行。

- **Part A** 去 Windows 化导出 + web 下载契约 + SSE 心跳 + 入口 env 化（Task 1-7）
- **Part B** N6 F2 收口：删 legacy 解析器（Task 8）
- **Part C** 部署 runbook（spec §5）——不在本轮，下一轮交互式执行

## 交付（实施真值源 = 项目 CLAUDE.md「## W2-C」段）

**导出（`backend/report_tools.py` 全 Python，无 PowerShell）**
- `_resolve_pandoc()` 平台守卫：仅 `sys.frozen`/`win32` 优先包内 `pandoc.exe`，否则系统 `pandoc`（防 Linux 误 exec 仓库根 Windows 二进制）。
- `export_reviewable_draft(report_path, output_dir)`（2 参，去 `script_path`）原子发布：`mkstemp` 同目录 temp.docx → pandoc → `os.replace`；失败清 temp + 保旧终名。全程锁外。
- 端点 `POST .../export-draft` 改同步 `def`（线程池跑、不阻塞事件循环、不取 request lock）；删 `get_script_path`。
- **web 下载**：新 `GET .../export-draft/download` `FileResponse`（确定文件名 + `resolve()` 穿越守卫 + `require_project` 属主隔离）。前端 `exportDraft` 按 status 判成败 + anchor 触发浏览器下载。

**SSE 防 CF ~100s 切空闲流（`backend/main.py`，两条流周期心跳）**
- 审查流 timeout 路径计时发 `: keepalive`；聊天流 `_sse_with_heartbeat(generate)` 线程+队列多路复用（`_CHAT_STREAM_EXECUTOR` 8 worker），空闲>20s 发心跳。
- 只在 SSE 帧层注入、不碰 chat.py provider/DeepSeek/request lock；不加 leading 心跳（快路径字节级零回归，已实测验证）。
- 锁释放正确性：pump `finally` `gen.close()` → GeneratorExit → chat `with request_lock` finally 释放；pump 先判 stop 再 `next`；两处 `call_soon_threadsafe` try/except `RuntimeError`；pump 异常记日志。

**入口（`run_web.py`）**：host/port 读 `CRA_BIND_HOST`/`CRA_BIND_PORT`；uvicorn `proxy_headers=True` + `forwarded_allow_ips` 读 `CRA_FORWARDED_ALLOW_IPS`(默 127.0.0.1)；`cookie_secure=True`；未设 `CRA_ALLOWED_ORIGIN` 告警。

**N6 F2**：删 4 个 legacy 解析器（`_legacy_read_document`/`_read_docx`/`_read_xlsx`/`_read_pdf`）；`_converter_read_document` 无 converter raise、converter-present 委派 `convert_document` 映射 `MaterialConversionError`→`ValueError`。

## 实施与 review 编排

subagent-driven 8 task TDD（实施派 Claude sonnet/opus，review 派 Codex `gpt-5.5` xhigh）。按耦合聚成 4 个 cluster 做 Codex spec/quality 双轨独立审（审→修→再审到 APPROVED）+ 整分支对抗式红队终审：

| Cluster | Task | spec 轨 | quality 轨 |
|---|---|---|---|
| R1 导出后端管线 | T1-3 | SPEC-COMPLIANT | APPROVED |
| R2 前端+脚本退役 | T4-5 | SPEC-COMPLIANT | APPROVED |
| R3 入口+SSE 心跳 | T6-7 | SPEC-COMPLIANT | APPROVED |
| R4 N6 F2 | T8 | SPEC-COMPLIANT | APPROVED |
| 整分支红队终审 | 全 | — | 见下（发现 1 BLOCKER，已修） |

**Codex 双轨挖出并修的真问题**：
- R1：原子发布测试半假绿（不验 -o 是 temp 路径，直写终名也能过）→ 补断言 `-o` parent==output 且 != final + frozen 分支用例。
- R3 quality 红队：`_sse_with_heartbeat` eager-drain 丢 ASGI 背压 → 核 reachability（单 worker/邀请制/按用户计费/锁释放更早）判 **trial accepted-risk** + 后置硬化；fut 异常静默吞 → 补 `_log_pump_exception`；in-loop `call_soon_threadsafe` 未守 loop-close → 补 try/except `RuntimeError`；`forwarded_allow_ips` 写死 → `CRA_FORWARDED_ALLOW_IPS` env 化。
- R4：缺 SkillEngine 层 `MaterialConversionError`→`ValueError` 映射测试 → 补。
- **整分支红队终审（BLOCKER，本轮关键发现）**：`clear_conversation` 是 `async def` 且在**事件循环线程**上 `with request_lock:`——聊天长 provider 调用持锁期间点「清空对话」会冻结事件循环 → `_sse_with_heartbeat` 发不出心跳 → CF 照样断流 + 单 worker 全员 stall，**直接拆 W2-C 心跳目标**。修：改同步 `def`（线程池跑、阻塞落 worker 非 loop，与导出端点同款）+ 前端 loading/uploading 禁用清空按钮 + handler 早返 + route-guard 测试。核实 `write_user_file`（同 async+持锁）安全（锁在 `_USER_WRITE_EXECUTOR` 内、`run_in_executor` 不阻塞 loop）。

## Trial accepted-risk / 后置硬化（已与 Codex 议定，非 bug）

1. `_sse_with_heartbeat` eager-drain + 无界队列——慢/半开客户端缓存整轮输出 + 烧发起者自己配额（有 token cap 上界、单 worker、按用户计费）；**锁释放不变差（更早）**；干净断连仍停。后置硬化：背压保持版（一次一个 in-flight `next()`、await-timeout 心跳、yield 门控下次提交）+ 前端 EOF-without-`[DONE]`=interrupted。
2. `_CHAT_STREAM_EXECUTOR` 8 worker = 单 worker 上 >8 并发长流串行化（trial 用户量不触及）。
3. `FileResponse` stat→open 与并发 `os.replace` TOCTOU 可能 Content-Length 错配（UI 顺序流不触发、自愈重下）。后置硬化：pin fd 流式。
4. symlinked output 目录绕守卫需服务器 FS 访问前提（web 用户不可达，server-compromise 才触及）。

## 跨任务正向不变式

T7 把聊天 generator 移到专用 `_CHAT_STREAM_EXECUTOR`（与用户写的 `_USER_WRITE_EXECUTOR` 仍是不同专用池）→ 保住 R3「用户写 `acquire` 靠 RLock 真阻塞到 chat 释放」的 CAS 防绕过性质（甚至更隔离）。`clear_conversation` 改 sync def 后跑 anyio 默认池、亦非 chat pump 线程 → RLock 真阻塞无重入绕过。

## 验证

- 后端 `pytest tests/`：**1437 passed**（实施期；BLOCKER 修复后 `test_main_api.py` 104 passed）+ 4 failed 全是已知 mac `/var`→`/private/var` realpath 环境差异（Windows 绿）+ 1 skipped。
- 前端 `node --test tests/`：**363 passed**（含新增 exportDraft/sseHeartbeat/clearGuard 守卫）。
- `npm run build`：绿（仅 pre-existing chunk-size warning）。
- DeepSeek 官渠兼容：**5 passed**。
- 禁改区 `backend/chat.py` / `backend/independent_review.py`：全分支零改动（`git diff` 核实）。

## 仍剩（下一轮）

- **Part C 部署 runbook（交互式）**：kr-web-01 反代+CF（`consulting.z0y0h.work`，Origin Cert+橙云）+ nginx[SSE 关 buffering + `set_real_ip_from` CF 段 `real_ip_header CF-Connecting-IP`] + systemd **单 worker**（B2/B3 进程内状态）+ env[`CRA_DATA_ROOT=/var/lib/consulting-report`/`CRA_INVITE_CODE`/`CRA_ALLOWED_ORIGIN`/bootstrap admin] + 装 pandoc+libreoffice。**风险**：kr-web-01 非自有账号，`managed_client_token`+搜索池凭据落非自有机，转生产换实例时轮换。
- **merge main**（本地，等用户确认）。
- 后置硬化项（见上 accepted-risk）记 backlog，桌面单用户低优先级。
