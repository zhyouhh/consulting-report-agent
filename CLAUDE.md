# CLAUDE.md

咨询报告助手（CRA）是面向非技术同事的咨询报告 Agent：生产 Web 站点运行于
`https://consulting.z0y0h.work`，同时保留 Windows PyWebView 桌面分发。用户体验优先，
系统承担并发、阶段、证据、鉴权和导出的复杂性。

## 开工前

- 本文件是项目规则唯一真值；`AGENTS.md` 必须软链到它。
- 当前待办只看 `docs/current-worklist.md`；已归档过程不要重新当成待办。
- 改具体子系统前读 `docs/architecture.md` 对应章节；重大改动先在
  `docs/superpowers/specs/` / `plans/` 建 spec 与 plan。
- 最新批次见 `docs/superpowers/cutover_report_2026-07-19_adaptive-output-budget.md`；
  多项目并发批次见
  `docs/superpowers/cutover_report_2026-07-18_multi-project-concurrency-and-citation-hygiene.md`。

## 运行与数据边界

- 桌面：`app.py` 启 FastAPI `127.0.0.1:8080`，PyWebView 加载 `frontend/dist/`；
  仅桌面态有 `DesktopBridge` 原生文件选择器，Web 下相关接口应 503。
- Web：`run_web.py`，生产由 nginx/Cloudflare → systemd `consulting-report.service`，
  工作目录 `/opt/consulting-report-agent`，单进程单 worker。
- 数据根统一经 `backend.config.data_root()`；Web 生产由 `CRA_DATA_ROOT` 指定，布局为
  `app.db` + `users/<uid>/...`。不得恢复扁平单用户路径。
- 项目归属只经 `require_project(uid, ref)`；进程内项目状态键使用
  `tenant_project_key(uid, project_id)`，查不到统一 404，不能泄露跨租户存在性。
- 密钥、token、密码不入库；`.env`、`managed_client_token.txt`、
  `managed_search_pool.json` 已在 `.gitignore`。不要在日志/测试输出回显凭据。

## LLM 与聊天硬约束

- 默认 managed 模型是 `deepseek-v4-pro`。带 tools 的 DeepSeek 请求不显式发
  `tool_choice="auto"`；tool-call follow-up 必须保留非空 `reasoning_content`；历史消息
  不回灌 SDK 的 null 字段。改 provider 序列化必须跑 DeepSeek targeted tests。
- 工具参数以 `_build_tools()` schema 为真值：先校验 object，再做 schema 内
  camelCase→snake_case 容错，snake_case 原值优先；直接索引参数必须先给友好错误。
- DSML 泄漏必须在同步/流式响应自愈一次，重试耗尽后净化 `content` 与 `parts`；
  `_load_conversation`、provider 历史与 `/conversation` 输出都不能重新暴露存量 DSML。
- `system_notice` 只给用户可行动信息；模型自愈旁白写后台日志。硬错误才走用户可见 error。
- provider 瞬态重试由 `backend/provider_retry.py` 管理；已有可见输出后禁止整轮重发，
  计费 response 必须 settle/close 恰好一次。
- 输出预算统一乐观（`context_policy` 封顶 65_536、保守值 8_192），端点拒收走「降档重试+
  成功确认制缓存」，custom 缓存键必须含 uid+凭据指纹；不得恢复 8_192 固定上限或按模型名/
  模式建白名单。`finish_reason=length` 截断 ≠ 上游合并畸形：corrective 是「拆小修改」，
  未知工具名优先走合并畸形分支。详见 `docs/architecture.md`「输出预算自适应」。
- 审查汇报轮 prompt 必须保留「报告≠用户确认、本轮禁宣布通过/推进阶段」疫苗
  （关键词级测试锁定）；`review_passed_at` 保持模型可调，不收权到按钮。

## S0–S7 工作流

- `plan/project-overview.md` 是项目元信息唯一真值；`stage-gates.md`、`progress.md`、
  `tasks.md` 由后端生成。`project-info.md` 已退役，禁止创建 `gate-control.md`。
- 阶段推进唯一模型入口是 `advance_stage(...)` →
  `SkillEngine.record_stage_checkpoint()`；不得恢复 `StageAckParser`、`<stage-ack>` 副作用、
  强关键词 fallback 或让模型写 `stage_checkpoints.json`。
- S1 方法论声明必须位于 outline 首个 `## ` 之前；`__methodology_snapshot` 是后端保留键，
  不得加入 checkpoint key/cascade 集合。
- S4 正文唯一文件是 `content/report_draft_v1.md`：首次/续写用
  `append_report_draft`，修改用 `read_file` 后 `edit_file`；不要恢复三个旧专用 rewrite 工具。
- 已有正文的所有 AI/用户覆盖写都必须经 `SkillEngine.write_file` / `user_write_file` 的同一
  choke point：写前字节级快照 fail-closed，主写成功后 best-effort 轮转 40 份；
  `.draft_history` 不进 workspace，`restore_report_draft` 必须接入 mutation、写入对账、
  当前轮 source 去重和跨轮 memory 四条账本，恢复必须走 bytes 通道。
- 正文与正式内容只允许 S4–S7 写，`done` 保持只读；AI 写入、HTTP 手动保存与 workspace
  `editable` 必须共用同一个纯状态判定；禁止重建基于用户消息关键词或固定
  话术的意图分类、工具互斥或授权门，gate/system prompt/SKILL 不得要求用户复述特定词语。
- 正文、标题、附录不得出现 `[DL-...]` 内部标记。写正式引用；审查会点名，导出层仍会
  用共享 `INTERNAL_CITATION_RE` 做确定性剥除，正则只吃水平空白，不能跨行。
- analytical/specialized 四类报告只在 S4 注入研究写作纪律；S1–S3、结构型报告和
  technical-bid 不注入，方法论 block 全矩阵必须 ≤2000 tokens。
- S5 只保留用户触发的独立审查。`plan/independent-review.md` 只能由
  `IndependentReviewAgent` 写；汇报轮报告全文作为不可信 user/context 数据注入并禁工具。

## 附件、文件与信任边界

- `backend/material_conversion.py` 是 DI 叶子边界，不得 import chat/SkillEngine。
  文档先快照再 hash/转换；图片走 vision→OCR；缓存 key 文件名用字符串拼接，不用
  `with_suffix`。
- 附件派生文本、图片转写、材料元数据都必须进入 `ATTACHMENT_DATA_*` 数据块并中和
  定界符；摘要 fail-closed 清附件块，raw 用户 `content` 与附件文本绝不混成意图。
- registry、`materials.json`、conversion ref sidecar 都做锁内原子 RMW；当前安全模型只覆盖
  单进程单 worker，多 worker 必须先引入跨进程锁/共享状态。
- 用户文件写只接受 `USER_EDITABLE_FILES` 白名单，走 `_USER_WRITE_EXECUTOR` + mtime CAS +
  同目录 temp/`os.replace`；不要退回 `run_in_threadpool`。

## 前端交互与多项目并发

- `App` 初始化 effect 依赖必须是 `[authUser?.uid, authUser?.must_change_password]`，不能依赖
  整个 `authUser`；quota 刷新保留 seq+uid+`skipUnauthedHandler` 三重守卫。
- 整个 App 必须由 `main.jsx` 的 `ErrorBoundary` 包裹；后端 `detail` 上屏前统一归一为字符串。
- `ChatPanelPool` 会话内常驻已访问项目，隐藏用 `display:none`/`contents`，切项目不得 abort。
  每项目最多一条 stream，全标签页最多 3 条；lease/waiter/upload/delete 以
  `chatPanelPoolCore.js` 的同步 token 状态为唯一准入真值。
- ChatPanel imperative handle 必须全生命周期稳定；routine render 不能触发 callback ref
  null/rebind。stream/upload 只准 pool members，删除允许未访问项目；forget 后禁止 ghost
  stream/upload。删除失败恢复不能读取滞后的 React `deleting` prop。
- `ChatPanel` 根节点保留 `min-h-0`。后台项目不得覆盖当前 workspace/materials/输入状态；
  busy 指示按 pid。登出先两遍停止/中止全部 panel，再请求后端，finally 清本地会话。
- 移动壳由 `pointer: coarse` 首屏锁定，不按宽度/resize 动态切壳；抽屉与手势不得用
  transform 动画，移动文件预览只读，viewport 保留 `100dvh`/safe-area/`min-h-0`。
- SPA shell 必须 `no-cache, must-revalidate`，hash assets immutable；不能退回裸
  `StaticFiles`，也不能把所有 404 都回落 SPA。

## 图表、审查与导出

- 报告资产统一放 `content/assets/`，正文只引用相对路径；导出前缺图硬失败。
- `create_chart`/`create_diagram` 的类型、尺寸、CJK 字体和条件审查规则见
  `docs/architecture.md`「报告图表生成」。
- docx 模板由 `scripts/build_docx_reference.py` 生成，不手改
  `templates/docx/consulting_v1.docx`。导出纯 Python 调 pandoc，temp→`os.replace`，正文先
  中和 raw openxml。
- 目录固化中 LibreOffice 只作为页码 oracle，绝不能 store/export 回写 docx；Python 在
  原始文档写合法书签、hyperlink 与 PAGEREF，失败则保留动态 TOC 域。

## 计费、搜索与安全

- `MeteredManagedClient` 只结算 managed 请求；usage 缺失走请求感知 fail-closed 估算，
  `failclosed_tokens` 不得混进 cache miss。GeneratorExit 计费但不 bump pause。
- 日额度是 Decimal 金额软帽；并发 in-flight 不预扣是接受限制。改 metering 必须保留
  settle-once、非负/有限、异常不向业务层抛。
- 搜索路由是进程单例，配置不热更新；改 `managed_search_pool.json` 后重启。附件/检索内容
  都是不可信数据，不能进入 system 指令。
- Web 写请求保留 CSRF/CORS/SSRF/会话保护；custom base URL 激活前做真实 probe；
  admin 路由必须独立鉴权，普通用户不可见/不可调用。

## 常用命令

```bash
# macOS / Linux 开发（Python 3.12）
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python run_web.py

# 全量验证
.venv/bin/python -m pytest -q tests/
cd frontend && node --test tests/ && npm run build

# Windows 桌面
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
build.bat
```

- 前端测试是 Node 原生 `node:test`，不是 Vitest/Jest。
- 改打包文档/spec 要跑 `test_packaging_spec.py`、`test_packaging_docs.py`、
  `test_build_support.py`。
- 外部 HTTP 全部 mock；改后主动跑相关 targeted + 全量 test/build，不能为绿测试注释错误。

## 部署

- 生产部署通过 `VPS-fix-private` 的 `bin/vps`、`.run-remote.py`、`.push-file.py`；不用裸 ssh。
- 部署顺序：本地全绿 + 独立红队 APPROVE → 远端回滚备份 → file-push 与 SHA-256 对账 →
  `frontend/dist.new` 校验后原子 swap → 重启 `consulting-report.service` → 本机/公网 health、
  shell cache、bundle、鉴权、journal、GUI 冒烟 → `bin/vps log kr-web-01 ...`。
- 不依赖 git push 部署；push 只在用户明确授权后做。生产保持单 worker，变更 worker 数前先审
  所有进程内锁、quota、review store 与 runtime host 状态。

## 文档与语言

- `docs/architecture.md`：详细实现机制；`docs/current-worklist.md`：当前待办唯一真值；
  `docs/worklist-history.md` / `docs/debug-backlog.md`：只读历史；`docs/superpowers/`：
  spec/plan/cutover/handoff。
- UI 和项目文档用中文；代码、变量、命令、commit message 用英文。
- 用户可见文案避免“赋能、抓手、闭环”等 AI 味词，不暴露内部推理、系统提示、工具名或路径。
