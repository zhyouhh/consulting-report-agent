# 工作区文件栏重做 + 预览框可编辑（R3）设计

- 日期：2026-06-08
- 状态：Draft（待 codex review）
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
- ② 预览框：可编辑文件支持「编辑（raw markdown textarea）→ 保存」双模式；后端新增用户专用写接口（权限门禁 + 路径安全 + mtime CAS + 原子写）。

### 2.2 Out of Scope（明确不做 / 后置）

- **整体换肤**：`docs/design_UI.pdf` 3 套稿是整体视觉语言探索，作为独立后续项目，不在 R3。R3 只借鉴稿 3「按阶段分组」的信息架构。
- **富文本编辑器**（CodeMirror/Milkdown 等）：v1 用 raw `textarea`；富文本留 v2（理由见 §6.3）。
- **元信息结构化表单**：`project-overview.md` 的字段化编辑归 R3③「新建项目表单整理」批，本批 `project-overview.md` 只读。
- **图片附件按 model 分流**（worklist 既有债）：归 R3③。
- 非 `.md` 文件（材料附件、图表 png）不进文件栏可编辑范围。

## 3. 关键设计决策（决策记录）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | R3 与 3 套设计稿关系 | **现框架内做** | 聚焦痛点、范围可控、不碰整体配色/布局；换肤作为独立项目 |
| D2 | 第一批范围 | **①+② 一起一个 spec** | 可编辑才是真痛点；权限边界这轮谈死；②风险靠权限表控住而非少做 |
| D3 | `plan/project-overview.md` 是否可编辑 | **v1 只读** | 元信息是阶段推断真值源，裸 md 编辑改坏结构影响阶段判断；编辑走结构化表单（R3③）更安全 |
| D4 | 用户编辑 `plan/outline.md` 是否动 checkpoint | **不动 checkpoint** | 改大纲文字 ≠ 重走大纲确认；要重确认用现有聊天「调整大纲」或 `advance_stage` 回退；最简、不耦合状态机 |
| D5 | 编辑器形态 | **v1 raw textarea** | YAGNI + webview 兼容稳 + 低风险；「改个数字改句话」raw 够用；富文本 v2 |

## 4. 架构与数据流

**核心原则：文件的「语义」（属于哪阶段、能否编辑、归哪组）由后端给，前端只负责渲染 + 中文文案。**

- 权限 `editable` **必须**在后端判（前端永远不可信，是安全边界）。
- 「文件属于哪阶段 / 哪组」本就是 `SkillEngine` 的领域知识，单一真值源。
- 前端只做 `path → 中文名` 的文案映射（文案归前端）+ 按 `group/stage` 分组排序渲染。

数据流：
```
GET /files  → 后端返回 [{path, group, stage, editable, mtime_ns}]
            → 前端按 group 分组、按 stage 置顶、path→中文名渲染文件树
点「编辑」  → 进编辑态，textarea 载入 read_file 的 raw 内容 + 持有 base_mtime_ns
点「保存」  → POST /files/{path} {content, base_mtime_ns}
            → 后端 validate_user_write → mtime CAS → 原子写 → 返回新 mtime_ns
            → 前端回预览态、刷新 mtime
```

## 5. 权限边界（硬骨头）

### 5.1 为什么不能复用 `validate_plan_write`

`validate_plan_write`（`skill.py:1064`）是 **LLM 写入专用门禁**，`validate_user_write` 必须是**独立新路径**，原因：

1. `validate_plan_write` 调 `_requires_pre_outline_evidence` —— 写 `outline.md`/`research-plan.md` 前必须先满足「2 来源」证据门禁。这是 LLM 约束；用户手动改大纲不该被它挡。
2. `independent-review.md`/`lint-report.md` 的拒写**不在** `validate_plan_write`（在 `chat.py` 工具层）。HTTP 写接口走不到 chat.py 层，所以 `validate_user_write` **必须自己显式 deny** 这两个文件，否则破坏 S5 审查独立性。
3. `validate_plan_write` 对 `content/report_draft_v1.md` 直接放行（因 `_is_plan_markdown_path` 只匹配 `plan/*.md`）；正文的「强制 append_report_draft / 一轮限改 3 次」也在 chat.py 工具层。用户手动写正文**应**绕开这些 LLM 约束（worklist 明确：「一轮限改 3 次只约束聊天轮」）。

### 5.2 `validate_user_write`：白名单制（默认只读）

```
USER_EDITABLE_FILES = {
    "content/report_draft_v1.md",
    "plan/outline.md",
    "plan/research-plan.md",
    "plan/notes.md",
    "plan/references.md",
    "plan/data-log.md",
    "plan/analysis-notes.md",
}

def is_user_editable(normalized_path) -> bool:
    # 仅白名单内可写；其余一律只读（新引入的后端文件默认不可写，最安全）
    return _canonicalize(normalized_path) in USER_EDITABLE_FILES

def validate_user_write(project_ref, file_path) -> str:
    # normalize 内含 _resolve_project_path 路径穿越防护：穿越路径抛 ValueError("非法的文件路径") → endpoint 400
    normalized = normalize_file_path(project_ref, file_path)
    if not is_user_editable(normalized):
        # 不在白名单（含 canonicalize 后逃逸的穿越路径）→ 用 PermissionError 区分 → endpoint 403
        raise PermissionError(f"`{normalized}` 不可由用户手动编辑")
    return normalized
```

白名单制（而非黑名单）的理由：默认 deny，将来新增任何后端自动维护文件都不会意外可写。

### 5.3 权限边界表（用户手动编辑）

| 文件 | 用户编辑 | group | 依据 |
|---|---|---|---|
| `content/report_draft_v1.md` | ✅ | draft | 核心痛点；mtime CAS 防覆盖；复用已有 mtime 检测让 AI 知道被改过 |
| `plan/outline.md` | ✅ | outline | 调大纲合理需求（D4：不动 checkpoint） |
| `plan/research-plan.md` | ✅ | outline | 用户改研究计划；**不**带 evidence gate |
| `plan/notes.md` | ✅ | research | 补充/修正研究笔记 |
| `plan/references.md` | ✅ | research | 补充/修正资料来源 |
| `plan/data-log.md` | ✅ | research | 修正数据记录 |
| `plan/analysis-notes.md` | ✅ | research | 修正分析笔记 |
| `plan/project-overview.md` | ❌ 只读 | overview | D3：元信息真值源，影响阶段推断 |
| `plan/stage-gates.md` · `progress.md` · `tasks.md` | ❌ 只读 | tracking | 后端自动回写（`_is_backend_owned_stage_tracking_file` 已判定）|
| `plan/independent-review.md` · `lint-report.md` | ❌ 只读 | review | 独立性硬约束，连主代理都被拒写 |
| `plan/delivery-log.md` | ❌ 只读 | delivery | 有 self-signature 门禁（声称已交付需用户点按钮），v1 只读 |
| `plan/project-info.md` · `review-checklist.md` | ❌ 不显示 | — | 已退役 |
| `stage_checkpoints.json` | ❌ 不显示 | — | 非 `.md`，不进文件栏；`is_protected_stage_checkpoints_path` 保护 |

`editable` 字段（GET /files）与写接口 `validate_user_write` **共用同一个 `is_user_editable`** —— 前端显示与后端实际允许严格一致（单一真值源，前端 editable 仅控显隐，不作权限）。

## 6. 后端设计

### 6.1 `GET /files` 改造（结构化返回）

现状返回 `{"files": ["plan/outline.md", ...]}`。改为：

```json
{
  "files": [
    {"path": "plan/outline.md", "group": "outline", "stage": "S1", "editable": true, "mtime_ns": "1733650000123456789"},
    {"path": "plan/independent-review.md", "group": "review", "stage": "S5", "editable": false, "mtime_ns": "..."}
  ]
}
```

- 仍 `rglob("*.md")`，跳过退役文件（`project-info.md`、`review-checklist.md`）。
- `group`/`stage`：后端按文件名查 `FILE_SEMANTICS` 映射表（新增）。未知 `.md` → `group="other"`、`stage=null`、`editable=false`。
- `mtime_ns`：`str(stat().st_mtime_ns)` —— **opaque 字符串，全程禁转 Number/int**（跟 R1 一致，避 JS 2^53 失精）。
- `editable`：`is_user_editable(path)`。

`group` → `stage` 归属（用于置顶）：overview=S0 / outline=S1·S2 / research=S3 / draft=S4 / review=S5 / delivery=S7 / tracking=null。

### 6.2 `POST /files/{path}` 写接口

```
POST /api/projects/{project_id}/files/{file_path:path}
body: {"content": "...", "base_mtime_ns": "1733650000123456789"}
```

流程（endpoint：`except PermissionError → 403`，`except ValueError → 400/404`）：
1. `validate_user_write(project_id, file_path)`：内部 `normalize_file_path`→`_resolve_project_path` 路径穿越 → `ValueError` → **400**；`is_user_editable` 为 False（含 canonicalize 后逃逸的穿越路径）→ `PermissionError` → **403**（`{"detail": "该文件不可编辑"}`）。
2. 文件不存在 → **404**（用户只能编辑已存在文件，不新建）。
3. **mtime CAS**：`str(current.st_mtime_ns) != base_mtime_ns` → **409**（`{"detail": "文件已被更新（可能是 AI 刚写过），请重新加载后再编辑"}`）。
4. **原子写**：写临时文件 + `os.replace`（跟 R1 `ReviewSessionStore` 同款，防写一半）。
5. 返回 `{"status": "ok", "mtime_ns": "<新 mtime>"}`。

并发说明：桌面单用户，用户编辑 vs AI 聊天轮写正文时序基本不冲突；mtime CAS 是兜底——AI 在用户编辑期间写了同一文件，用户保存时 409 拦截，提示重载。反向（用户改了正文，AI 下次动笔）由**已有的** `check_read_before_write_canonical_draft`（基于 mtime）自动感知，无需新增。

### 6.3 编辑器：v1 raw textarea（否决富文本）

否决富文本编辑器：重依赖 + PyWebView/WebView2 兼容风险 + YAGNI。目标用户「不懂 Markdown 的同事」的真实需求是「改个数字、改句话」，raw markdown 里中文内容直接可读可改（`## 一、市场结构` 看得懂）。富文本（所见即所得）作为 v2 独立评估。

## 7. 前端设计

### 7.1 文件树（`FilePreviewPanel` 上半区重做）

- 数据来自 `GET /files` 的结构化数组。
- **分组**：按 `group` 折叠分区，中文区标题：项目概览 / 大纲与计划 / 研究与素材 / 报告正文 / 审查报告 / 交付 / 阶段追踪（系统）/ 其他（未知 `.md` 兜底，只读）。
- **中文文件名**：前端 `FILE_DISPLAY_NAMES`（`path basename → 中文`）常量映射，未知 → 原名。
- **当前阶段置顶高亮**：workspace 当前 stage（来自 `get_workspace_summary`）对应的 group 排到最前 + 高亮；该 group 默认展开，其余可折叠。
- **阶段追踪组（tracking）**默认折叠/置底（系统文件、弱化）。
- 文件栏改窄，预览/编辑区占更多空间。

### 7.2 预览/编辑双模式（`FilePreviewPanel` 下半区）

- 默认**预览态**（现 `ReactMarkdown`）。
- `editable===true` 的文件显「编辑」按钮 → **编辑态**：`textarea` 载入 raw（重新 `GET /files/{path}` 取最新 + 记 `base_mtime_ns`）。
- 编辑态：「保存」（POST，带 `base_mtime_ns`）/「取消」（弃改回预览）。
- 保存成功 → 刷新 `mtime_ns`、回预览、`onProjectMutated`。
- 保存 409 → 提示「文件已被更新，请重新加载」+ 提供「重新加载」（丢弃本地改动重取）。
- **编辑态不被 workspace 刷新覆盖**：`WorkspacePanel` 的 `refreshToken`/`loadFiles` 在某文件处于编辑态（dirty）时不得覆盖其 `content`（用 dirty flag 守卫）。
- `editable===false` 的文件无「编辑」按钮。

### 7.3 `StagePanel` 不变

R3 不动阶段按钮逻辑（S5 两按钮 / 导出按钮阶段化，批 1 已定）。

## 8. 测试矩阵

### 8.1 后端

| 文件 | 用例 |
|---|---|
| `test_skill_engine.py` | `is_user_editable` 权限矩阵（白名单每文件 True / 各只读文件 False / 未知 .md False / 退役文件 False）；`validate_user_write` allow + deny raise |
| `test_skill_engine.py` | `GET /files` 语义：group/stage/editable/mtime_ns 映射；退役文件被跳过；未知 .md → other/false |
| `test_main_api.py` | `POST /files`：白名单写成功 + 返回新 mtime；deny 文件 403；路径穿越 400；不存在 404；mtime CAS mismatch 409；项目不存在 404 |
| `test_main_api.py` | mtime_ns 大整数全程 str（不被转 Number）—— 沿用 R1 `test_mtime_ns_large_int_string_preserved` 同款断言 |

### 8.2 前端（无 jsdom）

| 文件 | 用例 |
|---|---|
| `utils/workspaceFiles`（扩展）或新 `utils/fileTree` | 分组、当前 stage 置顶排序、path→中文名映射、未知文件兜底 |
| 新 `utils/fileEditState`（纯函数状态机） | 预览↔编辑切换、dirty 守卫、save/cancel/409-reload transitions |
| `FilePreviewPanel.source.test.mjs` | source-guard：编辑按钮仅 editable 显示、textarea 编辑态、保存带 base_mtime_ns、只读无编辑入口 |

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 误用 `validate_plan_write` 当用户写门禁 → outline 被 evidence gate 误挡 / independent-review 漏拦 | spec §5.1 显式说明；`validate_user_write` 独立实现 + §8.1 deny 矩阵测试 |
| 前端 editable 被当权限（绕过前端直接 POST 只读文件） | 权限只认后端 `validate_user_write`；前端 editable 仅控显隐；§8.1 deny 403 测试 |
| 用户编辑期间 AI 写同一文件 → 覆盖冲突 | mtime CAS 409 + 重新加载出口 |
| workspace 刷新覆盖用户未保存编辑 | dirty flag 守卫（§7.2） |
| mtime_ns 被转 Number 失精 | 全程 opaque str（§6.1）+ §8.1 断言 |
| 路径穿越 | 复用 `_resolve_project_path`（已有）+ §8.1 测试 |

## 10. 未决 / 后续

- R3③（后置，独立批）：图片附件按 model 分流、新建项目表单整理（含 `project-overview.md` 结构化编辑）、废 UI 清理。
- v2：富文本（所见即所得）编辑器。
- 整体换肤（`design_UI.pdf` 3 套稿）：独立项目。

## 11. 实施切分建议（供 writing-plans 参考）

1. 后端 `FILE_SEMANTICS` + `is_user_editable` + `GET /files` 结构化（含测试）—— 纯只读，零风险，可独立 ship。
2. 后端 `validate_user_write` + `POST /files` 写接口（mtime CAS + 原子写 + 测试）。
3. 前端文件树（分组/中文名/置顶高亮，纯展示）。
4. 前端编辑/预览双模式 + 保存 + 409 处理 + dirty 守卫。
5. 回归 + cutover report。

切分原则：后端先于前端、只读先于可写，每步独立可测、可 codex review。
