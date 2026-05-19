# 2026-05-19 Stage Conductor Packaged QA

## 结论

本轮目标是根治 CRA 阶段推进卡住的问题，并验证聊天 Markdown 表格渲染。阶段推进机制已从模型文本信号 `<stage-ack>` 收敛为后端工具 `advance_stage`；后端负责 checkpoint 校验、阶段文件回写和越级门禁，模型不再通过输出关键词推进阶段。

最新打包态应用完成了可视化 S0-S7 验收：QA 项目显示 `当前阶段 已完成`、`状态：已归档`，S0-S7 checklist 全部勾选；聊天消息中的 Markdown 表格按表格单元格渲染。质量检查与导出可审草稿接口在打包态通过。

## 新包

- 输出目录：`dist\咨询报告助手\`
- 启动程序：`dist\咨询报告助手\咨询报告助手.exe`
- 最后一次 `build.bat`：exit 0

构建仍有非阻断警告：

- `npm audit` 报 1 个 high severity vulnerability。
- Vite 报 chunk size warning。
- PyInstaller 日志仍提示 Anaconda/conda 相关 warning，虽然构建命令从项目 `.venv` 发起。
- `pycparser.lextab` / `pycparser.yacctab` hidden import 未找到，未阻断构建。

## 自动化验证

已通过：

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_tool_log.py tests/test_main_api.py tests/test_packaging_docs.py -q
# 168 passed, 1 warning, 1 subtests passed

.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "advance_stage or stage_claim or stage_ack or LegacyTagSanitizer or LoadConversationSanitize or StreamTailGuardHelper or write_file or checkpoint or append_report_draft or canonical"
# 159 passed, 263 deselected, 1 warning, 48 subtests passed

.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "expected_plan_writes or obligation_stream_emits_notice"
# 5 passed, 417 deselected, 1 warning

.venv\Scripts\python -m pytest tests/test_packaging_docs.py tests/test_packaging_spec.py tests/test_build_support.py -q
# 28 passed, 1 warning

.venv\Scripts\python -m pytest tests/test_skill_assets.py tests/test_report_tools.py tests/test_main_api.py::WorkspaceApiTests::test_quality_check_endpoint_returns_script_output tests/test_main_api.py::WorkspaceApiTests::test_export_draft_endpoint_returns_output_path -q
# 7 passed, 1 warning

cd frontend && node --test tests/
# 185/185 pass

cd frontend && npm run build
# passed
```

未完成：

- `tests/test_chat_runtime.py` 全量单文件曾运行超过 20 分钟后超时；本轮用变更相关 selector 覆盖阶段推进、legacy tag 清理、正文写入、checkpoint、write obligation 等高风险路径。

## 打包态可视化验收

QA 项目：

- `proj-514e66ccf5c0`
- 工作区：`qa-workspace-clean`
- 交付形式：报告 + 演示

视觉证据：

- `docs/superpowers/handoffs/screenshots/2026-05-19-clean-s0.png`
- `docs/superpowers/handoffs/screenshots/2026-05-19-clean-done.png`
- `docs/superpowers/handoffs/screenshots/2026-05-19-markdown-table.png`
- `docs/superpowers/handoffs/screenshots/2026-05-19-final-packaged-s0-s7-markdown.png`
- `docs/superpowers/handoffs/screenshots/2026-05-19-final-rebuilt-packaged-s0-s7-markdown.png`

最新浏览器验收结果：

- 页面成功加载 `http://127.0.0.1:8080/`。
- 项目 `QA CLEAN S0-S7 20260519` 选中后，标题区显示 `当前阶段 已完成`。
- 阶段面板显示 `状态：已归档`。
- 已完成列表覆盖 S0-S7，包含 `delivery-log.md 更新`、`客户反馈收集`、`后续动作与归档记录`。
- Markdown 表格 fixture 渲染为表格单元格：`阶段 / 状态 / 证据`，不再显示原始 pipe 文本。
- Network 面板中应用接口均为 200。
- Console 仅有 1 条表单字段 id/name 可访问性提示，未见运行时 JS error。

最终重打包后接口验收：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/projects/proj-514e66ccf5c0/quality-check
# status=ok，输出 [CHECK]/[OK]，无 PowerShell 中文解析错误，无 emoji 乱码

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/projects/proj-514e66ccf5c0/export-draft
# status=ok，输出中文可读，output_path 指向 output\report_draft_v1.docx
```

## 真实模型通道观察

真实 managed 模型链路没有完整跑完 S0-S7。观察到：

- S0 首轮真实模型能正常提出澄清问题。
- 用户回答后，真实模型曾成功调用 `advance_stage` 推进到 S1。
- 后续真实模型请求出现过上游超时 / 长时间无首包，属于网关或模型通道稳定性风险，不是阶段状态机本身的确定性 bug。

为避免把外部通道不稳定误判为阶段逻辑失败，本轮最终用打包态确定性项目直接验证后端阶段机、UI 渲染、质量检查和导出链路。

## QA 中发现并已修复的问题

- 启动崩溃：旧配置里 `settings.mode` 可能为 `null`，前端首页 error boundary。已修复为前端能力判断容忍 null。
- 阶段推进误补写：成功 `advance_stage` 后，旧 write obligation guard 会要求模型手写后端自动生成的 `plan/stage-gates.md`。已改为忽略 `stage-gates.md`、`progress.md`、`tasks.md` 这类后端生成文件。
- Windows PowerShell 编码：打包态 `quality_check.ps1` / `export_draft.ps1` 在 Windows PowerShell 5.1 下可能中文解析失败。已改为 UTF-8 BOM，并补测试锁定。
- 质量检查输出乱码：emoji 在 API 捕获输出里显示为 `??`。已改为 ASCII 前缀 `[CHECK]`、`[OK]`、`[SUMMARY]`。
- 最终代码审查后补修：false stage claim detector 现在覆盖 `已确认大纲，进入资料采集`、`进入报告撰写`、`审查通过，可以交付` 等自然短语，同时抑制 `需要先进入研究设计阶段`、`请先进入资料采集` 这类前置条件/指令句。
- 最终代码审查后补修：`backend.report_tools` 现在固定以 UTF-8 + `errors="replace"` 捕获 PowerShell stdout/stderr，并用真实 `quality_check.ps1` smoke 覆盖中文输出。

## 残余风险

- managed 模型真实长链路仍受渠道稳定性影响：本轮出现过 timeout / 无首包。
- 浏览器控制台还有 1 条非阻断可访问性 issue：输入框缺少 `id` 或 `name`。
- 浏览器网络面板仍有非阻断 `favicon.ico` 404。
- 构建侧仍有 npm audit high、Vite chunk warning、PyInstaller conda warning，需要后续单独清理。
