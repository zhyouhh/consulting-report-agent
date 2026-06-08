# 工作区文件栏重做 + 预览框可编辑（R3）设计

- 日期：2026-06-08
- 状态：Draft（codex review 中，R1 REJECTED → 本版修订）
- 关联：`docs/current-worklist.md` 领导评审反馈整改簇 **R3**（批 2）
- 范围决策：现框架内小切口 · 第一批做 ①+②（③图片分流/表单整理后置）
- 前置：批 1（R1+R2，S5 审查迷你聊天 + 续审）已 merge 进 main（`f111f0e`）

---

## 1. 背景与问题

demo 现场领导亲见两处硬伤：

1. **左侧文件栏是一堆英文文件名平铺**（`WorkspacePanel.loadFiles` 把 `GET /files` 的路径直接 `path.split('/').pop().replace('.md','')`），不懂技术的同事找不到对应文件。
2. **预览框只读**（`FilePreviewPanel` 纯 `ReactMarkdown` 渲染），不懂 Markdown 的同事没法改 AI 写的文档。

R3 解决这两点：文件栏改**分层 + 中文名 + 当前阶段置顶高亮**，预览框改**预览↔编辑双模式 + 保存**，后端补**用户专用写接口**（现仅有读接口）。

## 2. 目标与非目标

### 2.1 In Scope（第一批 ①+②）

- ① 文件栏：按语义分组（折叠）+ 裸英文名→中文名 + 当前阶段所属文件置顶高亮 + 文件栏改窄。
- ② 预览框：可编辑文件支持「编辑（raw markdown textarea）→ 保存」双模式；后端新增用户专用写接口（白名单门禁 + 路径安全 + per-project 锁内 mtime CAS + 原子写）。

### 2.2 Out of Scope（明确不做 / 后置）

- **整体换肤**：`docs/design_UI.pdf` 3 套稿是整体视觉语言探索，作为独立后续项目；R3 只借鉴稿 3「按阶段分组」的信息架构。
- **富文本编辑器**（CodeMirror/Milkdown 等）：v1 用 raw `textarea`；富文本留 v2（理由见 §6.3）。
- **元信息结构化表单**：`project-overview.md` 字段化编辑归 R3③，本批 `project-overview.md` 只读。
- **图片附件按 model 分流**、**新建项目表单整理**：worklist 既有债，归 R3③。
- **收紧 `allow_origins` / 加本地 token**：是全局架构债（既有读接口与 materials/checkpoint 写接口早已暴露在 `allow_origins=["*"]` 下），不该由 R3 独扛；本批只为新写接口写清 threat model（§9），全局收紧记后续。
- 非 `.md` 文件（材料附件、图表 png）不进文件栏可编辑范围。

## 3. 关键设计决策（决策记录）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | R3 与 3 套设计稿关系 | **现框架内做** | 聚焦痛点、范围可控、不碰整体配色/布局；换肤独立项目 |
| D2 | 第一批范围 | **①+② 一起一个 spec** | 可编辑才是真痛点；权限边界这轮谈死；②风险靠白名单+锁控住而非少做 |
| D3 | `plan/project-overview.md` 可编辑 | **v1 只读** | 元信息是阶段推断真值源，裸 md 编辑改坏结构影响阶段判断；编辑走结构化表单（R3③）更安全 |
| D4 | 用户编辑 `plan/outline.md` 是否动 checkpoint | **不动 checkpoint** | 改大纲文字 ≠ 重走大纲确认；要重确认用现有聊天「调整大纲」或 `advance_stage` 回退；不耦合状态机 |
| D5 | 编辑器形态 | **v1 raw textarea** | YAGNI + webview 兼容稳 + 低风险；「改个数字改句话」raw 够用；富文本 v2 |
| D6 | 用户改正文后审查报告失效策略 | **advisory 标 stale，不强制清 checkpoint** | 改正文后 `review_passed_at` 与两份审查报告不再针对当前正文；但「改一字作废审查」过度，且违反项目 advisory 风格。改为：标 `review_stale` flag + UI 提示「正文已改动，建议重新审查」，不强制清除、不硬阻 S6/S7（硬门禁记后续）。详见 §5.4 |
| D7 | 跨源/CSRF 安全 | **记既有债，R3 只写 threat model** | `allow_origins=["*"]` 是全局现状，读接口与既有写接口同样暴露；R3 不独扛全局收紧，但写清新写接口的暴露面与后续建议（§9） |

## 4. 架构与数据流

**核心原则：文件的「语义」（属于哪阶段、能否编辑、归哪组）由后端给，前端只负责渲染 + 中文文案。**

- 权限 `editable` **必须**在后端判（前端永远不可信，是安全边界）；前端 `editable` 仅控显隐，不作权限。
- 「文件属于哪阶段 / 哪组」是 `SkillEngine` 的领域知识，单一真值源（§6.1 `FILE_SEMANTICS`）。
- 前端只做 `path → 中文名` 的文案映射（文案归前端）+ 按 `group` 分组、按文件级 `stage` 置顶。

数据流：
```
GET /files          → [{path, group, stage, editable, mtime_ns}]      （列表，渲染文件树）
点某文件 / 点编辑   → GET /files/{path} → {content, mtime_ns, editable}（持锁内一次读 + stat，编辑态以此 mtime 为 base）
点保存              → POST /files/{path} {content, base_mtime_ns}
                    → 持 _get_project_request_lock：validate_user_write → stat CAS → 写 temp → os.replace → re-stat
                    → 返回 {status, mtime_ns}；前端回预览态、刷新 mtime
```

## 5. 权限边界（硬骨头）

### 5.1 为什么不能复用 `validate_plan_write`

`validate_plan_write`（`skill.py:1064`）是 **LLM 写入专用门禁**，`validate_user_write` 必须是**独立新路径**：

1. `validate_plan_write` 调 `_requires_pre_outline_evidence` —— 写 `outline.md`/`research-plan.md` 前必须满足「2 来源」证据门禁。这是 LLM 约束；用户手动改大纲不该被它挡。
2. `independent-review.md`/`lint-report.md` 的拒写**不在** `validate_plan_write`（在 `chat.py` 工具层）。HTTP 写接口走不到 chat.py 层，所以 `validate_user_write` **必须自己显式拒**这些文件（白名单制天然实现），否则破坏 S5 审查独立性。
3. 正文 `content/report_draft_v1.md` 的「强制 append / 一轮限改 3 次」也在 chat.py 工具层；用户手动写正文**应**绕开这些 LLM 约束（worklist 明确：「一轮限改 3 次只约束聊天轮」）。

### 5.2 `validate_user_write`：白名单制（默认只读）

```
USER_EDITABLE_FILES = {            # canonical = 统一小写后的 posix 相对路径
    "content/report_draft_v1.md",
    "plan/outline.md",
    "plan/research-plan.md",
    "plan/notes.md",
    "plan/references.md",
    "plan/data-log.md",
    "plan/analysis-notes.md",
    "plan/presentation-plan.md",
}

def _canonical_user_path(normalized_path) -> str:
    # 注意：不复用 _canonicalize_plan_markdown_path（它只对 plan/*.md 小写，content/ 不 casefold）。
    # 这里对整条 posix 相对路径统一小写——Windows 文件系统大小写不敏感，
    # content/Report_Draft_V1.MD 与 content/report_draft_v1.md 必须判为同一文件。
    return to_posix(normalized_path).lstrip("/").casefold()

def is_user_editable(normalized_path) -> bool:
    return _canonical_user_path(normalized_path) in USER_EDITABLE_FILES   # 白名单内才可写

def validate_user_write(project_ref, file_path) -> str:
    # normalize_file_path 内含 _resolve_project_path 路径穿越防护：穿越路径抛 ValueError → endpoint 400
    normalized = normalize_file_path(project_ref, file_path)
    if not is_user_editable(normalized):
        # 不在白名单（含 canonicalize 后逃逸的穿越路径）→ PermissionError → endpoint 403
        raise PermissionError(f"`{normalized}` 不可由用户手动编辑")
    return normalized
```

白名单制（而非黑名单）：默认 deny，将来新增任何后端自动维护文件都不会意外可写。`USER_EDITABLE_FILES` 是判定 `editable`（GET）与写接口（POST）的**唯一真值源**——显示与实际允许严格一致。

### 5.3 文件语义与权限表（全 15 个 FORMAL_PLAN_FILES + content + 退役）

stage 是**文件级**属性（用于置顶，§7.1）；group 是视觉分组（中文标签见 §7.1）。

| 文件 | group | stage | 用户编辑 | 依据 |
|---|---|---|---|---|
| `plan/project-overview.md` | overview | S0 | ❌ 只读 | D3：元信息真值源，影响阶段推断 |
| `plan/notes.md` | research | S1 | ✅ | 研究搜集笔记（SKILL.md L33） |
| `plan/references.md` | research | S1 | ✅ | 资料来源（SKILL.md L33） |
| `plan/data-log.md` | research | S2 | ✅ | 资料采集（SKILL.md L84） |
| `plan/outline.md` | analysis | S1 | ✅ | 研究设计产物（SKILL.md L78）；D4 不动 checkpoint |
| `plan/research-plan.md` | analysis | S1 | ✅ | 研究设计；**不**带 evidence gate（§5.1） |
| `plan/analysis-notes.md` | analysis | S3 | ✅ | 分析沉淀（SKILL.md L110） |
| `content/report_draft_v1.md` | draft | S4 | ✅ | 核心痛点；改后触发 §5.4 review-stale |
| `plan/independent-review.md` | review | S5 | ❌ 只读 | 独立性硬约束，连主代理都被拒写 |
| `plan/lint-report.md` | review | S5 | ❌ 只读 | 同上（脚本写入） |
| `plan/presentation-plan.md` | delivery | S6 | ✅ | S6 演示计划（SKILL.md L163）；用户手动写不受 chat.py:4963 LLM 门禁 |
| `plan/delivery-log.md` | delivery | S7 | ❌ 只读 | 有 self-signature 门禁（声称交付需用户点按钮，`validate_self_signature`），v1 只读 |
| `plan/stage-gates.md` | tracking | — | ❌ 只读 | 后端自动回写（`_is_backend_owned_stage_tracking_file`） |
| `plan/progress.md` | tracking | — | ❌ 只读 | 同上 |
| `plan/tasks.md` | tracking | — | ❌ 只读 | 同上 |
| `plan/review.md` | other | — | ❌ 只读 | 在 `FORMAL_PLAN_FILES` 但 SKILL.md 无产出说明；用途不明，v1 保守只读（不参与置顶）|
| `plan/project-info.md`·`review-checklist.md` | — | — | 不显示 | 已退役（GET /files 跳过） |
| `stage_checkpoints.json` | — | — | 不显示 | 非 `.md`，`is_protected_stage_checkpoints_path` 保护 |

### 5.4 编辑对下游状态的影响（D6）

| 用户改了什么 | 处理 |
|---|---|
| `content/report_draft_v1.md` | 若 `review_passed_at` 已置：`get_workspace_summary` 增 `review_stale=true` flag，UI 提示「正文已改动，建议重新审查」。**不**强制清 checkpoint、**不**硬阻 S6/S7 推进（advisory）。判定靠正文 mtime 晚于 `review_passed_at` 时间戳（或晚于两份报告 mtime）。AI 下次动笔由现有 `check_read_before_write_canonical_draft`（基于 mtime）感知，无需新增。 |
| `plan/outline.md`·`research-plan.md`·`data-log.md`·`analysis-notes.md`·`notes.md`·`references.md` | 维持 D4：不动任何 checkpoint。这些是过程文件，用户随时修订；要重走某阶段用聊天「调整大纲」或 `advance_stage` 回退。 |
| `plan/presentation-plan.md` | 不动 checkpoint。 |

`review_stale` 仅 advisory（v1）。若实测「交付未审草稿」成真痛点，再加硬门禁（后续）。

## 6. 后端设计

### 6.1 `GET /files` 与 `GET /files/{path}` 改造

**列表 `GET /files`**：仍 `rglob("*.md")`，跳过退役（`project-info.md`、`review-checklist.md`）。每文件经 `FILE_SEMANTICS`（新常量：`{basename → (group, stage)}`，覆盖 §5.3 全部）+ `is_user_editable` 映射为：

```json
{"files": [
  {"path": "plan/data-log.md", "group": "research", "stage": "S2", "editable": true, "mtime_ns": "1733650000123456789"},
  {"path": "plan/independent-review.md", "group": "review", "stage": "S5", "editable": false, "mtime_ns": "..."}
]}
```

- 未知 `.md`（不在 `FILE_SEMANTICS`）→ `group="other"`、`stage=null`、`editable=false`。
- `mtime_ns`：`str(stat().st_mtime_ns)` —— **opaque 字符串，禁转 Number/int**（避 JS 2^53 失精）。
- group→stage 不再写死成区间；stage 来自 `FILE_SEMANTICS` 的文件级值。

**单文件 `GET /files/{path}`**：返回从 `{content}` 扩为 `{content, mtime_ns, editable}`——编辑态以此 `mtime_ns` 为 `base_mtime_ns`（**不**用列表里可能更旧的 mtime，避免误 409）。该读取在 `_get_project_request_lock` 内做「读内容 + stat」一次完成，保证 content 与返回 mtime 一致。

### 6.2 `POST /files/{path}` 写接口

```
POST /api/projects/{project_id}/files/{file_path:path}
body: {"content": "<str>", "base_mtime_ns": "<str, 后端拒绝 number 类型>"}
```

**全段持有 `_get_project_request_lock(project_id)`**（与聊天写入 chat.py:3216 同一把 `threading.RLock`），临界区内顺序：

1. `validate_user_write`：路径穿越（`ValueError`）→ **400**；不在白名单（`PermissionError`）→ **403**（`{"detail":"该文件不可编辑"}`）。
2. 项目不存在 → **404**；文件不存在 → **404**（用户只能编辑已存在文件，不新建）。
3. **mtime CAS**：`str(current.st_mtime_ns) != base_mtime_ns` → **409**（`{"detail":"文件已被更新（可能是 AI 刚写过），请重新加载后再编辑"}`）。
4. **原子写**：写**同目录** temp 文件 → `os.replace`（跟 R1 `ReviewSessionStore` 同款）；写/replace 异常时清理 temp 并 500。
5. re-stat，返回 `{"status":"ok","mtime_ns":"<新 mtime str>"}`。
6. 若 `file_path` 是 `content/report_draft_v1.md`：触发 §5.4 review-stale 标记（在锁内或随后由 `get_workspace_summary` 按 mtime 判定）。

**为什么必须持锁**：CAS 单靠 `stat` 有 TOCTOU——`stat` 通过后、`os.replace` 前，AI 聊天轮仍可写同文件，用户保存会覆盖 AI 写入。持有与聊天同一把锁才真正互斥。`RLock` 可重入、同步阻塞；async endpoint 下临界区只含本地文件 IO（小），用 `run_in_threadpool` 包裹避免阻塞事件循环。

**异常分流**：endpoint `except PermissionError → 403`；`except ValueError →` 按来源 400（路径非法）/404（项目或文件不存在）——建议用不同异常子类或明确 message 前缀，不靠脆弱的字符串匹配。

### 6.3 编辑器：v1 raw textarea（否决富文本）

否决富文本：重依赖 + PyWebView/WebView2 兼容风险 + YAGNI。目标用户「不懂 Markdown 的同事」的真实需求是「改个数字、改句话」，raw markdown 里中文内容直接可读可改。富文本（所见即所得）作为 v2 独立评估。

## 7. 前端设计

### 7.1 文件树（`FilePreviewPanel` 上半区重做）

- 数据来自 `GET /files` 结构化数组。
- **分组**：按 `group` 折叠分区，中文区标题：项目概览（overview）/ 研究与素材（research）/ 大纲与分析（analysis）/ 报告正文（draft）/ 审查报告（review）/ 演示与交付（delivery）/ 阶段追踪·系统（tracking）/ 其他（other）。
- **中文文件名**：前端 `FILE_DISPLAY_NAMES` 按**完整 path**（非 basename，避免未来同名冲突）映射，未知 → 原名。
- **当前阶段置顶高亮**：`get_workspace_summary` 给当前 stage（如 `S2`）；文件树把 `stage === 当前stage` 的文件置顶 + 高亮，所在 group 默认展开。stage 为 `null` 的（tracking/other）不参与置顶；tracking 组默认折叠置底（系统文件、弱化）。
- 文件栏改窄，预览/编辑区占更多空间。

### 7.2 预览/编辑双模式（`FilePreviewPanel` 下半区）

- 默认**预览态**（现 `ReactMarkdown`）。`editable===true` 文件显「编辑」按钮 → **编辑态**：重新 `GET /files/{path}` 取最新 `{content, mtime_ns}`，`textarea` 载入 content、记 `base_mtime_ns`。
- 编辑态：「保存」（POST 带 `base_mtime_ns`）/「取消」（弃改回预览）。保存成功 → 刷新 `mtime_ns`、回预览、`onProjectMutated`。保存 409 → 提示 + 「重新加载」（丢弃本地改动重取）。
- **dirty 守卫覆盖所有离开路径**（不止 workspace refresh）：当某文件处于编辑态且有未保存改动（dirty）时，以下动作都要先提示「保存 / 放弃 / 取消」：
  - workspace `refreshToken`/`loadFiles` 刷新（不得覆盖编辑态 content）；
  - **切换到另一文件**（现 `WorkspacePanel.loadFile` 会直接覆盖 `content/currentFile`）；
  - **切换项目**（`projectId` 变）、**切 tab**（阶段/文件/材料）、关闭编辑；
- dirty 状态归属在 `FilePreviewPanel`（或上提 `WorkspacePanel`），离开动作经统一 `guardLeave(next)` 决策。

### 7.3 `StagePanel` 不变

R3 不动阶段按钮逻辑（S5 两按钮 / 导出按钮阶段化，批 1 已定）。

## 8. 测试矩阵

### 8.1 后端

| 文件 | 用例 |
|---|---|
| `test_skill_engine.py` | `is_user_editable` 权限矩阵：8 白名单 True（含 `content/report_draft_v1.md` 大写变体经 casefold 仍 True）/ 各只读文件 False / 未知 .md False / 退役 False；`validate_user_write` allow + deny `PermissionError` + 穿越 `ValueError` |
| `test_skill_engine.py` | `GET /files` 语义：`FILE_SEMANTICS` 全 15 文件 group/stage/editable 正确（**重点 S1 outline / S2 data-log / S3 analysis-notes / S6 presentation-plan / S7 delivery-log**）；退役跳过；未知→other/null/false |
| `test_main_api.py` | `GET /files/{path}` 返回 `{content, mtime_ns, editable}` |
| `test_main_api.py` | `POST /files`：白名单写成功+返回新 mtime；deny 403；穿越 400；项目/文件不存在 404；mtime CAS mismatch 409；`base_mtime_ns` 传 number 被拒 |
| `test_main_api.py` | 锁内 CAS 竞争：持 `_get_project_request_lock` 期间并发写被串行化（覆盖不丢） |
| `test_main_api.py` / `test_skill_engine.py` | 改 `report_draft_v1.md` 后 `review_passed_at` 已置 → `get_workspace_summary` `review_stale=true`；改过程文件不置 stale |
| `test_main_api.py` | `mtime_ns` 大整数全程 str（沿用 R1 `test_mtime_ns_large_int_string_preserved` 同款断言） |

### 8.2 前端（无 jsdom）

| 文件 | 用例 |
|---|---|
| 新 `utils/fileTree` | 分组、当前 stage 置顶排序（S2→data-log 顶、S6→presentation-plan 顶）、path→中文名、未知文件兜底 other |
| 新 `utils/fileEditState`（纯函数状态机） | 预览↔编辑切换、dirty 标记、save/cancel/409-reload、`guardLeave` 在 切文件/切项目/切 tab/refresh 各路径的决策 |
| `FilePreviewPanel.source.test.mjs` | source-guard：编辑按钮仅 `editable` 显示、textarea 编辑态、保存带 `base_mtime_ns`、只读无编辑入口、dirty 离开提示 |

## 9. 安全 / threat model（新写接口）

- **既有现状**：FastAPI `allow_origins=["*"]`（main.py:57）+ 后端绑 `127.0.0.1:8080`。读接口、`materials/upload`、`checkpoints` 等写接口早已在此暴露面下。
- **R3 增量**：`POST /files` 新增「写白名单文件任意内容」能力。理论攻击：用户浏览器访问的恶意网页跨源 `fetch` 本地 `127.0.0.1:8080`，枚举 `/api/projects` 后写白名单文件（如往正文塞内容）。
- **缓解（本批）**：白名单制把可写面限到 8 个用户内容文件，**不含**任何阶段/审查/checkpoint 文件——即使被 CSRF，也只能改用户自己的草稿内容，不能推进阶段、不能伪造审查、不能越权读写其他文件；路径穿越已挡。
- **后续（记既有债，D7）**：全局收紧 `allow_origins` 到具体 localhost origin，或加 PyWebView 注入的本地 session token 校验所有写接口。不在 R3 独扛（应统一覆盖所有既有写接口）。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 误用 `validate_plan_write` 当用户写门禁 → outline 被 evidence gate 误挡 / independent-review 漏拦 | §5.1 显式说明；`validate_user_write` 独立白名单 + §8.1 deny 矩阵测试 |
| 前端 `editable` 被当权限（绕过前端直接 POST 只读文件） | 权限只认后端 `validate_user_write`；§8.1 deny 403 测试 |
| 用户编辑 vs AI 写同一文件覆盖（TOCTOU） | 全段持 `_get_project_request_lock` + mtime CAS（§6.2）+ §8.1 锁内竞争测试 |
| 编辑态被切文件/切项目/刷新覆盖丢改动 | §7.2 dirty `guardLeave` 覆盖所有离开路径 |
| Windows 大小写/反斜杠绕过白名单 | `_canonical_user_path` 统一 casefold 整路径（§5.2）+ §8.1 大写变体测试 |
| 当前阶段置顶高亮错组 | 文件级 `stage`（`FILE_SEMANTICS`，§6.1）+ §8 S1/S2/S3/S6/S7 置顶测试 |
| 改正文后交付未审草稿 | §5.4 `review_stale` advisory 提示（硬门禁记后续 D6） |
| `mtime_ns` 转 Number 失精 | 全程 opaque str（§6.1）+ 后端拒 number（§6.2）+ §8.1 断言 |
| 跨源写接口滥用 | §9 白名单限面 + 既有债后续收紧 |

## 11. 未决 / 后续

- R3③（后置）：图片附件按 model 分流、新建项目表单整理（含 `project-overview.md` 结构化编辑）、废 UI 清理。
- v2：富文本（所见即所得）编辑器。
- 后续硬化：`review_stale` 硬门禁（若需）、全局 `allow_origins` 收紧 / 写接口本地 token（D7）。
- `plan/review.md` 用途待查清（疑似退役）；查清后或并入退役不显示。
- 整体换肤（`design_UI.pdf` 3 套稿）：独立项目。

## 12. 实施切分建议（供 writing-plans 参考）

1. 后端 `FILE_SEMANTICS` + `is_user_editable` + `GET /files` 结构化 + `GET /files/{path}` 扩 mtime（含测试）—— 纯只读，零风险，可独立 ship。
2. 后端 `validate_user_write` + `POST /files`（锁 + CAS + 原子写 + 异常分流 + 测试）。
3. 后端 `review_stale`（§5.4）+ `get_workspace_summary` flag（含测试）。
4. 前端文件树（分组/中文名/置顶高亮，纯展示）。
5. 前端编辑/预览双模式 + 保存 + 409 + dirty `guardLeave`（所有离开路径）。
6. 回归 + cutover report。

切分原则：后端先于前端、只读先于可写，每步独立可测、可 codex review。
