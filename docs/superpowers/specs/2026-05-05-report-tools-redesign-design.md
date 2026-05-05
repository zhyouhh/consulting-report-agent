# Report Writing Tool Redesign — Replace `<draft-action>` Tag + Gate System with Specialized Tools

**Date**: 2026-05-05
**Status**: Draft, awaiting codex spec review loop until APPROVED.
**Supersedes**: spec `2026-05-04-context-signal-and-intent-tag-design.md` §4.3-§4.12 (`<draft-action>` tag + gate fallback + scope enforcement architecture). §1-§4.2 (preflight basics) and §5+ (other concerns) remain valid.

## TL;DR

替换当前的 `<draft-action>` tag + keyword fallback + scope enforcement 整套机制（fix4 v5 amendment 的核心）成 **4 个专用写正文工具**（`append_report_draft` 不变，新增 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`）。模型通过工具调用本身声明意图，**后端用 preflight 已 resolve 的 target snapshot 自己控制 `old_string`**——结构性消除 model 必须精确复述 1500 字章节原文这个失败模式。

保留三层"系统侧保护"：(1) **turn-start 写正文意图检测**触发 turn-end 对账（防 model 不调任何工具就声称已写）；(2) **per-turn 一次 canonical mutation 限制**（防一轮反复改坏）；(3) **read-before-write mtime 跟踪**（防脏写）。

净简化：删 ~1300 行后端代码 + 1000 行测试，加 ~250 行工具实现 + 500 行测试 + 130 行 spec 文档。

`<stage-ack>` tag 系统**不动**（独立系统，已实测可工作）。

## §1 背景与失败模式（设计动机）

### 1.1 fix4 cutover Session B evidence

`docs/superpowers/cutover_report_2026-05-05_fix4.md` 记录 reality_test session B "把第二章重写一下" 的实际行为：fix4 让 gate fallback 14 次 fired（vs fix3 19 次 gate-block dead-loop），**但 14 次都被 `_validate_required_report_draft_prewrite` 拒绝**，最终用户停止。

### 1.2 backend 校验的实际语义

`backend/chat.py:5495-5615` `_validate_required_report_draft_prewrite` edit_file 分支校验的是 **`old_string` 字段**，三种 reject：

```python
# line 5598-5601 — old_string 等于整份草稿
"本轮只允许改写目标章节，不能用覆盖整份草稿的 `edit_file.old_string`。"

# line 5602-5606 — old_string 含多个 ## 标题
"本轮只允许改写单个目标章节，`edit_file.old_string` 不能同时覆盖多个标题段。"

# line 5609-5614 — old_string 跟目标章节原文不完全相等
f"本轮要求改写 {label_hint}，`edit_file.old_string` 必须等于该章节的完整原文。"
```

cutover smoke 截图显示 14 次失败全部命中后两种。

### 1.3 失败根因

backend 强制要求 model 在 `edit_file.old_string` 提交**目标章节的完整原文（约 1500 字）一字不差**——精确字符串等价检查（line 5587 + 5609）。

`gemini-3-flash`（项目唯一可用免费批量模型）实测做不到精确复述：
- 截取错位（少几行 / 多几行）
- 加入不该加的空格 / 标点 / 换行
- 把多个章节当作一个 `old_string` 提交（"重写第二章"被 model 扩展成"重写整篇"）

`conversation.json.before-fix4-20260505-182813` (fix3 cutover backup msg 25/29) 同时确认这个 pattern：fix3 时 model 也是同类失败。

### 1.4 设计抉择

两条路：

- **加重 prompt / SKILL.md 引导（fix5 candidate）**：让 model "更努力"精确复述。**已被实测否决** — fix4 cutover 14 次失败的 error message 已经具体到目标 heading，model 仍未学会缩窄。这是 model 能力硬约束，不是 prompt 表达问题。
- **结构性消除 model 控制 old_string 的需求**：让后端用 preflight 已 resolve 的 `rewrite_target_snapshot` 自己当 old_string，model 只给 new content。**采纳方向**。

后者落实需要：让"修改章节"成为一个独立工具——而不是通用 edit_file 加意图标签。工具调用本身就是意图声明，schema 强约束，比自由文本 tag 可靠。

## §2 工具集

### 2.1 保留：`append_report_draft(content)`

不变。当前实现（chat.py:4187-4202）已经覆盖 begin / continue 两种意图。fix3 cutover Session A/D 实测正确。

### 2.2 新增：`rewrite_report_section(content)`

**Schema**:
```json
{
  "name": "rewrite_report_section",
  "description": "重写正文草稿（content/report_draft_v1.md）中已存在的某一章/节。目标章节由系统从用户消息中自动定位（要求消息中含'第N章/节/部分'前缀，且草稿中存在唯一对应 heading）。仅在 S4 阶段、草稿存在、目标可唯一定位时可用；任一前提不满足时工具直接 reject 并返回引导消息，不写盘。",
  "parameters": {
    "content": {
      "type": "string",
      "description": "目标章节的新版完整内容，从 `## 章节标题` 行开始，到下一个同级 `##` 之前为止。不能包含其他 `##` 级别的标题。"
    }
  }
}
```

**关键约束**:
- **无 `file_path` 参数** — canonical draft 路径硬编码
- **无 `label` 参数** — target 由后端从用户消息自动 resolve（复用现有 `_preflight_resolve_section_target` 逻辑迁移）
- **无 `old_string` 参数** — backend 用 `rewrite_target_snapshot` 当 old_string，调底层 file edit
- **content 校验**：必须以 `## ` 开头；不能含其他 `## ` heading（防 model 提交多章节）

**Reject 路径**（工具自身返回 status=error，不写盘）:
| 失败原因 | 引导消息 |
|---|---|
| 阶段 < S4 | "本工具仅在 S4 阶段可用。当前阶段：X" |
| outline 未确认 | "请先在工作区确认大纲，再修改正文章节" |
| draft 不存在 | "当前还没有正文草稿，请先用 `append_report_draft` 起草第一版" |
| user 消息不含章节前缀 | "请在消息中明确说明要改哪一章/节，例如'重写第二章'" |
| 章节前缀未唯一定位到 heading | "找不到 '第X章' 对应的 heading，请用 `read_file` 核对章节标题" |
| 多个章节前缀指向不同 heading（partial multi-prefix） | "本轮只支持改写一个章节。请单独发起每个章节的修改请求" |
| content 不以 `## ` 开头 | "`content` 必须以 `## 章节标题` 开头" |
| content 含多个 `## ` heading | "`content` 不能涉及多个章节。请只提交目标章节的完整内容" |
| 本轮已调用过 read_file 但 draft mtime 已变 | "草稿在你阅读后被修改，请先重新 `read_file` 再提交" |

成功路径：调底层 `edit_file(file_path=REPORT_DRAFT_PATH, old_string=rewrite_target_snapshot, new_string=content)`，复用现有 file mutation infrastructure。

### 2.3 新增：`replace_report_text(old, new)`

**Schema**:
```json
{
  "name": "replace_report_text",
  "description": "把正文草稿（content/report_draft_v1.md）中的某段文字替换为新文字。要求 `old` 在草稿中**唯一**出现（恰好 1 次）。仅在 S4 阶段、草稿存在时可用。",
  "parameters": {
    "old": {
      "type": "string",
      "description": "要替换的原文片段。必须在草稿中唯一出现，长度建议 5-200 字以确保唯一性。"
    },
    "new": {
      "type": "string",
      "description": "替换后的新文字。可以为空（删除场景）。"
    }
  }
}
```

**关键约束**:
- **无 `file_path`** — canonical draft 路径硬编码
- **`old` 必须在 draft 中 count == 1**（既不能 0 次也不能 ≥ 2 次）

**Reject 路径**:
| 失败原因 | 引导消息 |
|---|---|
| 阶段 < S4 | "本工具仅在 S4 阶段可用" |
| outline 未确认 | "请先确认大纲" |
| draft 不存在 | "当前还没有正文草稿..." |
| `old` 在 draft 中 0 次出现 | "目标文本 `<old>` 在草稿中未找到。请先 `read_file` 核对原文" |
| `old` 在 draft 中 ≥ 2 次出现 | "目标文本 `<old>` 在草稿中出现 N 次（不唯一）。请提供更具体的上下文使其唯一" |
| 本轮已调用过 read_file 但 draft mtime 已变 | 同 §2.2 |

成功路径：调底层 `edit_file(file_path=REPORT_DRAFT_PATH, old_string=old, new_string=new)`。

### 2.4 新增：`rewrite_report_draft(content)`

**Schema**:
```json
{
  "name": "rewrite_report_draft",
  "description": "重写整份正文草稿（content/report_draft_v1.md）。仅在用户明确要求'整篇重写' / '推倒重来' / '全文重写' 时使用；个别章节调整请用 `rewrite_report_section`，文字替换用 `replace_report_text`。仅在 S4 阶段、草稿存在时可用。",
  "parameters": {
    "content": {
      "type": "string",
      "description": "完整新草稿内容，从 `# 报告标题` 开始。必须含至少一个 `## ` 级别章节标题。"
    }
  }
}
```

**关键约束**:
- **无 `file_path`** — canonical draft 路径硬编码
- **无 `old_string`** — backend 用 read 到的当前 draft 全文当 old_string
- **`content` 校验**：必须以 `# ` 开头（h1 报告标题）；至少含 1 个 `## ` 级别 heading

**Reject 路径**:
| 失败原因 | 引导消息 |
|---|---|
| 阶段 < S4 | "本工具仅在 S4 阶段可用" |
| outline 未确认 | "请先确认大纲" |
| draft 不存在 | "当前还没有正文草稿，请先用 `append_report_draft` 起草第一版" |
| user 消息不含全文重写关键词 | "看起来你只想改一部分。重写整章请用 `rewrite_report_section`，替换文字用 `replace_report_text`。如果确实要整篇重写，请明确说'整篇重写'或'全文重写'" |
| `content` 不以 `# ` 开头 | "`content` 必须以 `# 报告标题` 开头" |
| `content` 不含 `## ` heading | "`content` 必须含至少一个章节标题（`## `级别）" |
| 已有本轮 canonical draft mutation | "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改" |
| 本轮已 read_file 但 mtime 已变 | "草稿在你阅读后被修改，请先重新 `read_file` 再提交" |

成功路径：调底层 `edit_file(file_path=REPORT_DRAFT_PATH, old_string=current_draft_text, new_string=content)`。

**用户消息检测**：reject 第 4 条要求 user 消息含明确"整篇重写"关键词，沿用现有 `REPORT_BODY_WHOLE_REWRITE_KEYWORDS`（"整篇重写", "全文重写", "推倒重写", "全部改写"）。如果工具被调用但 user 消息不含这些关键词 → reject 并引导。

### 2.5 不动：现有禁用规则

`write_file` / `edit_file` 通用工具对 canonical draft 路径**继续禁止**。enforcement 在 `chat.py:5334-5337` (`_build_canonical_draft_write_file_block_message`) + 对 `edit_file` 的相应分支已存在。这部分**不需要新加 enforcement**。

## §3 工具内部 invariant + 共享 helpers

### 3.1 共享 helpers（抽出，新工具/旧工具都用）

放入新模块 `backend/report_writing.py`，导出以下函数（pure function，参数 explicit，方便测试）:

```python
def check_report_writing_stage(skill_engine, project_id) -> str | None:
    """阶段必须 S4-S7 才能写正文。返回 None=ok，str=error message."""

def check_outline_confirmed(skill_engine, project_id) -> str | None:
    """outline_confirmed_at 必须 set。"""

def check_no_mixed_intent_in_turn(turn_context) -> str | None:
    """复用现有 `_secondary_action_families_in_message` 逻辑：本轮 secondary action 数 ≤ 1。"""

def check_read_before_write_canonical_draft(turn_context, project_id) -> str | None:
    """本轮必须 read_file 过 canonical draft 才能 modify；mtime 变了要重读。"""

def check_no_fetch_url_pending(turn_context) -> str | None:
    """web_search 后必须 fetch_url 才能落盘外部信息。"""

def resolve_section_target(user_message, draft_text) -> dict | None:
    """复用 fix4 fix2 的 `_preflight_resolve_section_target` 逻辑（含 partial-multi-prefix fail-fast）。"""
```

### 3.2 工具调用入口模板

每个工具入口统一调用这些 helpers，**显式 inline**，不依赖外层 turn_context decision 变量：

```python
def append_report_draft(self, project_id, content):
    for check in (check_report_writing_stage, check_outline_confirmed,
                  check_no_mixed_intent_in_turn, check_no_fetch_url_pending):
        err = check(...)
        if err: return {"status": "error", "message": err}
    # ... business logic
    
def rewrite_report_section(self, project_id, content):
    for check in (check_report_writing_stage, check_outline_confirmed,
                  check_no_mixed_intent_in_turn,
                  check_read_before_write_canonical_draft,
                  check_no_fetch_url_pending):
        err = check(...)
        if err: return {"status": "error", "message": err}
    # 工具特定 check：draft 必须 exist + target 必须 unique resolve
    user_message = self._turn_context.get("user_message_text") or ""
    draft_text = self._read_project_file_text(project_id, REPORT_DRAFT_PATH) or ""
    if not draft_text:
        return {"status": "error", "message": "当前还没有正文草稿..."}
    target = resolve_section_target(user_message, draft_text)
    if target is None:
        return {"status": "error", "message": "请明确说明要改哪一章/节..."}
    # content 校验（## 开头 + 不含多 ##）
    if not content.startswith("## "):
        return {"status": "error", "message": "`content` 必须以 `## 章节标题` 开头"}
    extra_h2 = sum(1 for line in content.split("\n") if line.startswith("## "))
    if extra_h2 != 1:
        return {"status": "error", "message": "`content` 不能涉及多个章节..."}
    # 写盘
    return self._do_edit_file(REPORT_DRAFT_PATH, old_string=target["snapshot"],
                              new_string=content)
```

### 3.3 与 `_turn_context` 的关系

**不再需要 `canonical_draft_decision` / `required_write_snapshots` / `draft_action_events` 等 turn_context 字段**——意图判定移到工具入口，不再有"轮次开始时预判后续工具行为"的需要。

`_turn_context` 字段调整：

**新增**：
- `user_message_text`（**new**）— 在 `_build_turn_context` 中用现有 helper `_extract_user_message_text(current_user_message)` 取出 raw user msg 字符串并 cache。新工具在 `rewrite_report_section` / `rewrite_report_draft` 入口读这个字段做 keyword 检测和 `resolve_section_target`。
- `canonical_draft_write_obligation`（**new**, §3.5）— 由 `_detect_canonical_draft_write_obligation` 在 turn-start 写入；turn-end 时 `_finalize_assistant_turn` 检查"obligation 存在但 mutation 没发生" 触发 retry。
- `read_file_snapshots: dict[path, mtime]`（**new**, §3.7）— `read_file` 工具完成时写入；写正文工具入口检查 mtime 未变。

**保留**：
- `canonical_draft_mutation`（**keep**, §3.6）— 一轮一次 mutation 限制
- `read_file_paths`（保留 — 兼容现有逻辑，跟 `read_file_snapshots` 互补但不重复）
- `web_search_performed` / `fetch_url_performed`（保留 — 用于 fetch_url gate）
- `pending_system_notices`（保留 — 用于 system_notice 去重）
- `pending_stage_keyword`（保留 — stage-ack 系统）
- `can_write_non_plan` / `generic_non_plan_write_allowed`（保留 — `_should_allow_non_plan_write` 路径暂不动）
- `checkpoint_event`（保留 — stage-ack 系统）
- `required_write_snapshots`（**保留**——继续给 plan/* 文件用作"轮内对账"，仅删除 canonical draft 专属的精细字段；§5.1 详述边界）

**删除**：
- `canonical_draft_decision`
- `draft_action_events`
- `compare_baseline_event_count`

### 3.4 mixed-intent 处理

当前 `_secondary_action_families_in_message` 已存在；new tools 共享 helper `check_no_mixed_intent_in_turn` 直接复用该函数。逻辑不变。

### 3.5 turn-start 写正文意图检测 + turn-end 对账（保留 fix4 的 no-tool-call 保护）

**问题**：tool-entry check 只在 model 真的调工具时触发；如果 model 输出文本声称"已修改第二章"但 0 个 write tool_call，没有任何机制 catch 这个虚假声明。

**保留方案**（简化版 `required_write_snapshots` 等价物）：

`_build_turn_context` 时（user message 处理早期）调用一个轻量级 detector：

```python
def _detect_canonical_draft_write_obligation(self, user_message: str, project_id: str) -> dict | None:
    """轻量检测 user 消息是否触发"本轮要写正文"信号。返回 None=无信号，dict=
    {'tool_family': 'append'|'rewrite_section'|'replace_text'|'rewrite_draft',
     'detected_keywords': [...]}.

    复用现有 keyword 列表（REPORT_BODY_FIRST_DRAFT_KEYWORDS / 
    REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS / REPORT_BODY_SECTION_REWRITE_KEYWORDS / 
    REPORT_BODY_WHOLE_REWRITE_KEYWORDS / REPORT_BODY_REPLACE_TEXT_INTENT_RE)，
    但**不再做精细分类**（不输出 mode/scope/target）——只回答"用户是不是在让你写正文"。"""
```

返回非 None 时，cache 到 `turn_context["canonical_draft_write_obligation"]`（NEW field）。

**turn-end 对账**：复用现有 `_required_writes_satisfied` 思路（chat.py:3153+）。`_finalize_assistant_turn` 临近末尾检查：

```python
obligation = self._turn_context.get("canonical_draft_write_obligation")
if obligation and not self._turn_context.get("canonical_draft_mutation"):
    # user 让写但 model 一次也没成功调写工具
    if assistant_text_claims_modification(assistant_message):
        # model 在文本里说"已修改" / "已起草" 但 0 个 tool_call
        return retry_with_correction(...)  # 同 fix4 现有 retry 机制
```

**这样保留了 fix4 当前的 "model 撒谎说写了但实际没写" 保护**，但 detector 简化（只回答 yes/no，不分类），且不再驱动 gate（gate 删除）。

**关键设计区别**：
- 当前 fix4：preflight 输出精细 `mode/scope/target` → 驱动 gate + scope enforcement + write obligation 三件事
- 新设计：write obligation 只用粗粒度 yes/no 信号；gate 不存在；scope enforcement 移到工具内 inline check

### 3.6 一轮一次 canonical mutation 限制（保留）

当前 fix4 在某处用 `turn_context["canonical_draft_mutation"]` track 已经做过 canonical draft 修改，防 model 一轮内反复改坏。

**保留**：每个写正文工具入口加共享 helper：

```python
def check_no_prior_canonical_mutation_in_turn(turn_context) -> str | None:
    if turn_context.get("canonical_draft_mutation"):
        return "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改"
    return None
```

工具成功写盘后 set `turn_context["canonical_draft_mutation"] = {...metadata...}`。

### 3.7 read-before-write mtime 跟踪（具体机制）

**问题**：当前 `read_file_paths` 只 track 路径，不 track 当时的 mtime。要做"读后未修改"check 需要更多信息。

**新机制**：turn_context 加 `read_file_snapshots: dict[normalized_path, mtime_at_read_time]` 字段。

- `read_file` 工具完成后（chat.py 当前 `_execute_read_file` 类逻辑）记录 `(path, mtime)` 到字段
- 每个写正文工具的 `check_read_before_write_canonical_draft` helper：
  1. 检查 `REPORT_DRAFT_PATH` 在 `read_file_snapshots` 中
  2. 比较当前 mtime 跟记录的 mtime；如果不等 → reject "草稿在你阅读后被修改，请重新 read_file"

**注意**：mtime 跟踪只对 canonical draft 路径做（不对所有 read_file 启用），减少 turn_context 内存占用。

**append_report_draft 例外**：起草第一版时 draft 不存在，read 不出来；起草成功后才有文件。所以 `append_report_draft` 工具入口的 read-before-write check 只在 draft 已存在时启用（即等价于 continue 模式）。

## §4 流式 + tag 系统的简化

### 4.1 保留：`<stage-ack>` 完整不动

- `backend/stage_ack.py` 整个 module 不动
- `_STAGE_ACK_MARKER` 常量保留
- `_TAIL_GUARD_MARKERS = (_STAGE_ACK_MARKER,)` 简化为单 marker tuple
- `TAIL_TAG_SCAN_RE` 删 draft-action 相关分支，保留 stage-ack 分支
- `stream_split_safe_tail` 函数保留，但 `_TAIL_GUARD_MARKERS` 缩小后行为自动简化

### 4.2 删除：`<draft-action>` 系列

- `backend/draft_action.py` 整个 module 删除
- `_DRAFT_ACTION_MARKER` 常量删除
- `TAIL_TAG_SCAN_RE` 中 `<draft-action>` + `<draft-action-replace>` 部分删除
- `_finalize_assistant_turn` 7-step orchestrator 中的 draft-action parser/strip/apply 步骤删除（保留 stage-ack 部分）

### 4.3 SKILL.md 改造

`skill/SKILL.md` §S4 改造：
- 删除 §S4 "draft-action 标签" 子章节
- 删除附录"draft-action 标签规范"
- 在 §S4 加入 3 个写正文工具的明确语义指引（每个工具一段说明 + 示例对话）

具体新增内容：

```markdown
### S4 正文写作工具

| 用户意图 | 调用工具 |
|---|---|
| 起草初稿 / 续写正文 / 写下一段或下一章 | `append_report_draft(content="<新内容>")` |
| 重写已有的某一章/节（用户说"重写第N章/节"） | `rewrite_report_section(content="## 第N章 ...\n<新章节完整内容>\n")` |
| 替换正文中的具体文字（用户说"把 X 改成 Y"） | `replace_report_text(old="X", new="Y")` |

注意：
- 这三个工具内部已经做了阶段、大纲、草稿存在性、章节定位等校验。如果不满足前提，工具会直接返回 error 引导你下一步动作。
- **不要**对 `content/report_draft_v1.md` 使用通用 `edit_file` 或 `write_file`——会被拒绝。
- 一轮只能改一处：先确定用户最关心的那一处修改完，再问用户下一步。
```

stage-ack 部分保持原样（独立系统）。

## §5 删除清单（具体）

### 5.1 后端代码（净 ~1050 行）

**deletion 边界依据 spec r1 reviewer's grep + 我自己 verify**：

| 路径 | 项目 | 估行数 | 处置 |
|---|---|---|---|
| `backend/draft_action.py` | 整个文件 | -158 | **删** |
| `backend/chat.py` | `_DRAFT_ACTION_MARKER`, `_DRAFT_INTENT_PREFLIGHT_KEYWORDS`, `_SECTION_PREFIX_RE`, `_TAIL_GUARD_MARKERS` 双值定义 | -30 | 删 / 改单值 |
| `backend/chat.py` | `TAIL_TAG_SCAN_RE` draft-action 部分 | -3 | 改 regex |
| `backend/chat.py` | `_preflight_canonical_draft_check` body | -150 | **删** |
| `backend/chat.py` | `_classify_canonical_draft_turn` body | -350 | **删** |
| `backend/chat.py` | `_resolve_section_rewrite_targets` | -75 | **删**（被 `resolve_section_target` 替代） |
| `backend/chat.py` | `_preflight_resolve_section_target` | -45 | **迁移**到 `backend/report_writing.py` |
| `backend/chat.py` | `_gate_canonical_draft_tool_call` | -65 | **删** |
| `backend/chat.py` | `_record_tagless_fallback_event`, `_record_draft_decision_compare_event`, `_record_draft_decision_exception_event`, `_record_draft_gate_block_event` | -100 | **删** |
| `backend/chat.py` | `_make_canonical_draft_decision`, `_empty_canonical_draft_decision` | -80 | **删** |
| `backend/chat.py` | `_validate_required_report_draft_prewrite`：lines 5507-5520 (replace_text dispatch) + 5531-5615 (canonical draft scope/section enforcement for edit_file) + 5636-5716 (related branches per reviewer's grep) | -200 | **删** — 新工具不走这个函数；删后这些 reject paths 移到工具入口 |
| `backend/chat.py` | `_validate_required_report_draft_prewrite`：line 5522-5529 (`_validate_append_turn_canonical_draft_write` 调用) + 5617-5634 (generic edit/write fallthrough) | 0 | **保留**（如果 `append_report_draft` 现有 turn-end 校验仍需要，则保留；否则随 `_validate_append_turn_canonical_draft_write` 整套迁移） |
| `backend/chat.py` | full-draft rewrite branch lines 5547-5584 | 0 | **保留**（被新 `rewrite_report_draft` 工具 inline 替代后再决定，第一轮 spec 实施保留）|
| `backend/chat.py` | `_run_phase2a_compare_writer` | -50 | **删**（compare 不再有意义） |
| `backend/chat.py` | `_extract_user_message_text` | 0 | **保留** — 仍是 utility，新工具内会用 |
| `backend/chat.py` | 安全删除的常量集（per reviewer grep, only used in deleted classifier/preflight）：`_DRAFT_INTENT_PREFLIGHT_KEYWORDS`、`REPORT_BODY_FIRST_DRAFT_KEYWORDS`、`REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS`、`REPORT_BODY_WHOLE_REWRITE_KEYWORDS`、`REPORT_BODY_SECTION_REWRITE_KEYWORDS`、`REPORT_BODY_CONDITIONAL_TARGET_EXPANSION_KEYWORDS`、`REPORT_BODY_FOLLOWUP_EXPANSION_SIGNALS`、`REPORT_BODY_EXPLICIT_WRITE_KEYWORDS`、`REPORT_BODY_SHORT_CONTINUATION_KEYWORDS`、`REPORT_BODY_CHAPTER_WRITE_RE`、`REPORT_BODY_INLINE_EDIT_RE`、`REPORT_BODY_REPLACE_TEXT_INTENT_RE` | -60 | **删** — but §3.5 detector 复用其中部分作为粗粒度 yes/no 信号；实施时把需要的 list 复制到 `backend/report_writing.py` 后再删 chat.py 原位 |
| `backend/chat.py` | 保留常量：`REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS`、`REPORT_BODY_INSPECT_FILE_KEYWORDS`（被 `_should_allow_non_plan_write` 等仍用） | 0 | **保留** |
| `backend/chat.py` | turn_context 新增 3 字段：`user_message_text`、`canonical_draft_write_obligation`、`read_file_snapshots` 默认 `""/None/{}` | +10 | **新增** |
| `backend/chat.py` | 3 个新工具的 callable（`rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`）+ tool schema 注册（`_get_tools` per reviewer grep）+ dispatch 路由（`_execute_tool` per reviewer grep）| +280 | **新增** |
| `backend/chat.py` | `_detect_canonical_draft_write_obligation` 函数 + `_finalize_assistant_turn` 末尾 obligation 对账分支 | +60 | **新增** |
| `backend/chat.py` | `read_file` 工具完成 hook：写入 `read_file_snapshots[REPORT_DRAFT_PATH] = mtime` | +5 | **新增** |
| `backend/report_writing.py` | 新模块（共享 helpers + `resolve_section_target` 迁移自 fix4 fix2 + `assistant_text_claims_modification` text scanner） | +220 | **新增** |
| `backend/chat.py` | `_finalize_assistant_turn` 7-step orchestrator 中的 draft-action 步骤 | -40 | 删 |
| **后端净删** | | **~-1300 行**（再加 2 个新工具 + helpers ~+250，净 -1050） | |

### 5.2 测试（净 ~700 行）

数字基于 spec r1 reviewer 的实际 wc + grep 校正：

| 路径 | Test class | 估行数 | 处置 |
|---|---|---|---|
| `tests/test_draft_action.py` | 整个文件（实测 92 行）| -92 | **删** |
| `tests/test_chat_runtime.py` | `StreamSplitSafeTailDraftActionTests` | -80 | 改剪（保留 stage-ack 部分） |
| `tests/test_chat_runtime.py` | `PreflightCheckTests` | -200 | 大部分删，stage gate / outline check 部分迁移到新工具测试 |
| `tests/test_chat_runtime.py` | `DraftActionPreCheckTests` | -100 | **删** |
| `tests/test_chat_runtime.py` | `GateCanonicalDraftToolCallTests`（实测 437 行 starting line 12481）| -437 | **删** |
| `tests/test_chat_runtime.py` | `DraftDecisionCompareEventTests` | -150 | **删** |
| `tests/test_chat_runtime.py` | `ExtractUserMessageTextTests` | 0 | **保留** |
| `tests/test_draft_decision_compare_report.py` | 整个文件 | -50 | **删** |
| **新增 tests/test_report_writing.py** | helpers（resolve_section_target 等）+ assistant_text_claims_modification | +200 | **新增** |
| **新增 tests/test_chat_runtime.py 各工具 test class** | `AppendReportDraftToolTests`（regression）/ `RewriteReportSectionToolTests` / `ReplaceReportTextToolTests` / `RewriteReportDraftToolTests` | +500 | **新增**（每个 50-150 行）|
| **新增 tests/test_chat_runtime.py 端到端** | `WriteObligationRetryTests`（model 不调工具但声称已写）/ `CanonicalMutationLimitTests`（一轮一次）/ `ReadBeforeWriteSnapshotTests`（mtime 跟踪）/ `StageAckRegressionTests`（stage-ack 不受影响）| +200 | **新增** |
| **测试净** | | **删 -1109，加 +900，净 -209**（更保守估计，但删除大头是真的）| |

### 5.3 spec / 文档

| 路径 | 处置 |
|---|---|
| `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` §4.3-§4.12 | **标注 superseded**（保留作为历史，不删） |
| `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` §4.1, §4.2, §1-§3, §5+ | 保留（preflight basic / mixed-intent / 其他不相关章节） |
| `docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md` (this file) | **新增**（spec 主体）|
| `docs/superpowers/cutover_report_2026-05-05_fix4.md` | 保留（历史 cutover 数据） |
| `docs/superpowers/handoffs/*.md` | 保留（历史 handoff） |
| `tools/draft_decision_compare_report.py` | **删**（compare 不再有意义） |
| `skill/SKILL.md` §S4 + 附录 | 改写如 §4.3 |

### 5.4 用户可见 / 系统可见的 `<draft-action>` 字符串清理（per reviewer §B9 grep）

需要清理的现有字符串（避免 model / user 看到指引去用已删除的 tag）：

| 文件 | 行号 | 当前内容（摘要） | 处置 |
|---|---|---|---|
| `backend/chat.py` | 343-345 | `REPORT_BODY_INLINE_EDIT_RE` 等正则定义注释 | 随常量删 / 保留无关注释 |
| `backend/chat.py` | 5289 | `user_action="请按 SKILL.md 附录的 draft-action 标签规范操作"` | 改成 "请使用 `rewrite_report_section` / `replace_report_text` 工具" |
| `backend/chat.py` | 7459 | `user_action="请先发 <draft-action>begin</draft-action> 起草，再来重写章节"` | 改成 "请先用 `append_report_draft` 起草，再用 `rewrite_report_section` 重写章节" |
| `backend/chat.py` | 7521 | `user_action="请先发 <draft-action>begin</draft-action> 起草"` | 改成 "请先用 `append_report_draft` 起草" |
| `skill/SKILL.md` | 113-123 (S4 draft-action 子章节) | 整个 draft-action 标签表格 | **删** + 替换为 §4.3 提到的新工具表格 |
| `skill/SKILL.md` | 228-246 (附录 draft-action 标签规范) | 整个附录章节 | **删** |
| `backend/chat.py` `_finalize_assistant_turn` | 7-step orchestrator 中 draft-action 相关步骤 | parser/strip/apply 调用 | **删**（保留 stage-ack 步骤） |
| `backend/chat.py` `_get_tools` (per reviewer §B10) | 工具 schema 列表 | 找到 `append_report_draft` schema 定义处，加 3 个新工具 | 改 |
| `backend/chat.py` `_execute_tool` | 工具 dispatch | 加 3 个新工具的 case | 改 |

### 5.5 净简化估算（修订）

总计（修订后基于 reviewer wc 校正 + 新加 4 工具/3 turn_context 字段/2 detector）：
- 后端代码：净删 ~1050 行
- 测试：净删 ~209 行（删大头 + 加大头基本相抵；但 dead test 代码删除是真的简化）
- spec / 工具脚本：约持平（删 1 工具 + 1 文件章节，加 spec 主体）

**最终净简化 ~1260 行后端代码 + cleanup 一批 dead 测试代码**。比 v1 表述更保守但更准确。

## §6 迁移策略

提两个方案让 reviewer 评估：

### 6.1 方案 X — 渐进双通道（保守）

**Phase A**（约 2-3 小时）：
1. 加 `rewrite_report_section` / `replace_report_text` 工具实现 + unit tests
2. 加共享 helpers `backend/report_writing.py`
3. SKILL.md 加新工具引导（不删旧 draft-action 说明）
4. 现有 `<draft-action>` tag + gate + scope enforcement 路径**继续工作**
5. cutover smoke 重测 4 sessions
6. 期望：model 至少 80% 选用新工具（SKILL.md 引导优先级 + 工具描述清晰）

**Phase B**（约 3-4 小时，仅在 Phase A 验证通过后）：
1. 删除 `<draft-action>` 系列代码 + 测试
2. 删除 fix4 v5 amendment 相关 chat.py 大段代码
3. SKILL.md 完全替换 §S4
4. 二次 cutover smoke 验证

**优点**：分两步降低风险，Phase A 验证完成形再删旧。
**缺点**：Phase A 期间维护双通道（含 fix4 全部复杂度 + 新工具），暂时**变得更复杂**。fix3-fix4 的 review/cutover 教训表明：Phase 2a 维护双通道一周累积复杂度很高，再叠加 Phase A 容易过载。

### 6.2 方案 Y — 一次性替换（激进，倾向）

一个 commit（或一组紧邻 commits）完成：
1. 加 3 个工具 + helpers + 单测
2. 删 `<draft-action>` 系列 + tag parser + gate + scope enforcement code
3. 改 SKILL.md
4. spec 标注 §4.3-§4.12 superseded
5. 一次性 cutover smoke 验证

**优点**：avoid 双通道维护成本。codebase 一步到位简化。回退路径清晰（revert commit）。
**缺点**：失败时 revert 范围大；cutover 验证压力集中在一次。

### 6.3 推荐

**倾向方案 Y**，理由：
- 单元测试可覆盖大部分 invariant 行为安全
- 工具语义清晰，cutover smoke 4 sessions 即可验证 model 选择行为（A: begin 选 append；B: section 选 rewrite_section；C: replace 选 replace_text；D: continue 选 append）
- 双通道复杂度的负面经验（Phase 2a fix1/2/3/4 累积一周）说明应避免再来一次
- 一旦失败可整个 revert（git history 是 atomic commit）

由 spec reviewer 评估方案 X 还是 Y。

### 6.4 Plan Y 内部 commit 顺序（per reviewer §C17）

即使采用 Plan Y 一次性替换，PR 内部按以下顺序排列 commits（可分多个 commit 落盘但作为 atomic PR 合并）：

1. **commit 1 — 加 `backend/report_writing.py` 模块**（共享 helpers + `resolve_section_target` 迁移 + `assistant_text_claims_modification`）+ 该模块的 unit tests `tests/test_report_writing.py`。**这一 commit 不破坏现有任何路径**，纯加法。
2. **commit 2 — 加 turn_context 新字段** (`user_message_text` / `canonical_draft_write_obligation` / `read_file_snapshots`) + `_detect_canonical_draft_write_obligation` + `read_file` 工具的 mtime 写入 hook + 对应单测。**不破坏现有路径**，新字段并存。
3. **commit 3 — 加 4 个工具实现** (`append_report_draft` 重构调 helpers / `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`) + 工具 schema 注册 + dispatch 路由 + 各 ToolTests 类。**此时旧 path（draft-action tag + gate）仍工作**，新工具并存。
4. **commit 4 — 改 SKILL.md 引导用新工具**（保留旧 tag 说明作为 fallback）+ 改 user-facing reject messages。**model 现在被引导用新工具，但旧路径仍 work**。
5. **commit 5 — 删旧 code**：`backend/draft_action.py` / `_classify_canonical_draft_turn` / `_preflight_canonical_draft_check` / `_gate_canonical_draft_tool_call` / 各 record helpers / 等等；删 `tests/test_draft_action.py` + 对应 test classes；改 `_TAIL_GUARD_MARKERS` / `TAIL_TAG_SCAN_RE`；删 SKILL.md 旧 draft-action 章节；删 `tools/draft_decision_compare_report.py`。**这一 commit 是最大的删除，但前 4 个 commit 已经用新工具替代了所有功能**。
6. **commit 6 — cutover smoke artifacts + 更新 worklist + memory + handoff**。

合并是 atomic（一个 PR），但回滚 granularity 是单 commit（万一 commit 5 引入 regression 可单独 revert 它，保留前 4 个 commit 的进展）。

### 6.5 cutover smoke 改进

针对 fix3/fix4 cutover smoke 中遇到的可观测性问题，建议本次 cutover：
1. **不点"清空对话"按钮**（实测会清 events.json 字段，污染分析）。每次 session 之间用更直接的方法：要么不清，按时间戳分割 events；要么重启 dist app + 在新 project 跑（更彻底）
2. **增加单 session 等待上限**：fix4 cutover Session C model hung 8 min — 加一个明确的"超过 5 min 无 tool_call 即视为 stuck"的判定
3. **预测试每个 session 的 user message**：先用 unit test 验证 preflight resolve 出预期 target，再到 GUI 测；避免"消息表述歧义"导致测试不可重复

## §7 隐含风险 & 缓解（spec reviewer 重点检查项）

### 7.1 Risk α — model 给的 content 仍可能"过宽"

**现象**：model 调 `rewrite_report_section(content=...)`，content 内含多个 `## ` heading（试图把"重写第二章"扩展成"重写第二、三章"）。

**当前缓解**：§2.2 工具内 reject 规则"content 必须以 `## ` 开头"+"content 中只能有 1 个 `## ` 级别 heading"。第二条等价于"行首匹配 `## ` 的行数恰好为 1"——既排除了 0 heading 的（误用为 replace_text 的纯片段），也排除了多 heading 的（误用为 full rewrite）。

**风险残留**：model 把多章节内容塞进单 content 字段，但用 `### ` 三级标题假装只有一个章节。工具校验通过（开头 `## ` + 只有 1 个 `## `），但写入后第二章实际嵌入了不属于第二章的小节内容。

**评估**：这种 case 在 model 行为上等价于"model 完全误解'重写第二章'的范围"，工具的 schema 表述 + SKILL.md 引导已经明确告知"目标章节的新版完整内容"。如果 model 仍然这样做，是 model 严重 misuse；rewrite_report_section 写入后的草稿可见性高，user 容易发现。**v1 不加额外校验**（YAGNI），等观察到这种 model 行为再针对性修。

**进一步缓解**（option，**不一定纳入 v1**）：limit content 总长度（如 ≤ rewrite_target_snapshot 长度的 3 倍）；或加一个 sanity check "content 中不能含 `### 第N章/节` 这种 pattern 暗示新章节" 的启发式拒绝。

### 7.2 Risk β — 工具间选错

**现象**：model 把"把'体能'改成'力量'" 用 `rewrite_report_section` 而非 `replace_report_text`，把整章改成新版本——错伤范围大。

**缓解**：
- SKILL.md 三个工具表格清晰对应"用户意图模式"
- 工具描述（schema description）含明确"何时不该用"
- `rewrite_report_section` 内：如果 user 消息含 "X 改成 Y" 模式但用了 rewrite_section → 工具内可加 hint reject

### 7.3 Risk γ — 复用 fix4 helpers 的 invariant 完整性

`_preflight_resolve_section_target` (fix4 fix2 实现) 迁移到 `backend/report_writing.py:resolve_section_target` 时必须**保留**：
- partial-multi-prefix fail-fast (fix2 Bug 7)
- `_SECTION_PREFIX_RE` negative-lookahead 防 `第二章节` overmatch (fix1 Bug 3)
- multi-prefix dedup by heading position (fix1 Bug 5)

迁移后必须 port 对应的 unit tests（fix4 fix2 的 `test_preflight_section_partial_multi_prefix_returns_none` 等）。**reviewer 必须检查这一点**。

### 7.4 Risk δ — `_validate_required_report_draft_prewrite` 中保留部分

该函数当前同时服务于：
- `append_report_draft` 的"轮内必须实际写入" 校验（保留）
- `edit_file` 写 canonical draft 的 old_string 校验（删，因为新工具替代）
- `_required_write_snapshots` 对账（删，因为不再有跨工具调用对账需求）

**reviewer 需确认**：保留部分是否仍正确触发；删除部分是否有别的代码路径调用（如 `chat.py:7156` 的另一调用点）。

### 7.5 Risk ε — frontend 影响

新工具的 tool log 显示需要 frontend 适配？预期 `frontend/src/components/ChatPanel.jsx` 通过 OpenAI 协议接收 tool name/args 自动渲染——**不需要改 frontend**。但要确认 `frontend/utils/chatPresentation.js` 的 tool block 解析对未知工具名的兼容性（默认就显示 tool name，应该 OK）。

### 7.6 Risk ζ — model 不调任何 tool 但声称"已修改"（reviewer §A3 提出）

**保留方案在 §3.5**：用粗粒度 `_detect_canonical_draft_write_obligation` + turn-end 对账 retry 机制保护。这是当前 fix4 的 `required_write_snapshots` 机制的**简化版**。

**实施时关键测试**：
- model 输出 "已经把第二章重写完毕" 但 0 个 write tool_call → 触发 retry，告诉 model "你只声称修改但没调用工具，请实际调 `rewrite_report_section`"
- model 输出 "好的我来重写" 但 stop reason 是 length（被截断）→ 不应视为撒谎，应 retry/continue 不带特定提示
- model 调了 `read_file` 但没调写工具 → 取决于 obligation：如果 obligation=section_rewrite 但 0 mutation，触发 retry

`assistant_text_claims_modification(text)` 启发式：检测 "已"/"完成"/"修改"+"正文"/"草稿"等词。复用现有 `assistant_message` 文本扫描思路。

### 7.7 Risk η — 一轮一次 mutation 限制不严格（reviewer §A3 提出）

**保留方案在 §3.6**：每个写工具入口检查 `turn_context["canonical_draft_mutation"]`；写盘成功后 set 它。

**关键测试**：
- model 第一次调 `rewrite_report_section` 成功 → mutation set
- model 第二次调任何写工具 → reject "本轮已经修改过正文草稿一次"
- 但用户主动让 model "继续修改" 并再发一轮（新 turn）→ 新 turn 的 turn_context.canonical_draft_mutation 默认 None，可以再写

### 7.8 Risk θ — 4 工具 vs 3 工具的选择决策（reviewer §C16 implicit assumption）

**实施前** 先用 unit test 验证 model schema 下的"工具选择正确率"假设：

- 准备 10 个 user message，每个对应明确意图（begin / continue / section / replace / full_rewrite / mixed / ambiguous 等）
- 用 model 在 schema 模式下调用，看选对工具的比例
- 期望：≥ 80%（这是 fix3 cutover Session A 的隐含基线）
- 如果实测 < 80%，重新设计工具命名 / description（不是放弃方案，是优化 schema）

**当前评估**：3 工具语义跟 `append_report_draft` (已用 1 年验证 work) 同形态，4th `rewrite_report_draft` 跟 `rewrite_report_section` 是 strict subset 关系（模型只在用户**明确说全文**时才选后者），认知压力可控。但 reviewer 提的 "未做 A/B benchmark" 是真的——should add a smoke benchmark step to the implementation plan.

## §8 验收标准

实施完成的 Definition of Done：

1. **Unit tests pass**: 4 个新工具的所有正/反 case 全 pass；`backend/report_writing.py` helpers 单测 pass；`tests/test_chat_runtime.py` 删除目标 class 后剩余 test 全 pass
2. **新增的端到端测试 pass**:
   - `WriteObligationRetryTests` — model 不调工具但声称已写 → 触发 retry
   - `CanonicalMutationLimitTests` — 一轮 ≥ 2 次 mutation 被 reject
   - `ReadBeforeWriteSnapshotTests` — mtime 跟踪正确
   - `StageAckRegressionTests` — `<stage-ack>` tag 流式扫描 / parsing / strip 不受影响
3. **Wider sanity**: chat_runtime 全 suite 0 fail（仅整个删除的 class 不再存在）
4. **Frontend tests**: 168 / 168 pass（不动 frontend）
5. **Build**: `build.ps1` 重新打包成功，dist/ 大小不超过当前 ±5%
6. **Spec / SKILL.md**: 旧 spec §4.3-§4.12 标注 superseded；本 spec doc 进 main；SKILL.md §S4 替换为新表格；附录 draft-action 章节删除
7. **工具选择正确率 smoke**（per §7.8）: 10 个 user message × tool schema 模式调用，正确率 ≥ 80%
8. **Cutover smoke**: 5 sessions （A 起草 / B 重写第二章 / C 替换文本 / D 续写第三章 / E 整篇重写）至少 4 个写盘成功（含 B 章节实际被替换 + E 整份草稿被替换）；剩 1 个允许 model behavior issue 但**工具不卡 dead-loop**
9. **No regression**:
   - stage-ack 系统所有现有 test pass；`<stage-ack>` tag 在 cutover 期间正常工作
   - 现有 plan/* 写文件路径不受影响（grep 验证 `_required_writes_satisfied` 仍能 detect plan/* 文件 missing）
   - Phase 2a 13 commits + fix4 三轮 commits 的非删除部分行为保持（用 git diff 看每一个非整删的 helper 是否仍有等价覆盖）

## §9 与现有 Phase 3 plan 的关系

`docs/superpowers/handoffs/2026-05-05-phase2a-fully-done-phase3-ready.md` 描述的 Phase 3 (Tasks 24-27) 计划"删 legacy classifier"——本 spec **替代** Phase 3：
- 不再分两阶段（Phase 2a 灰度并行 → Phase 3 切主），而是直接迁移到全新工具集
- Phase 3 plan 中"删 `_classify_canonical_draft_turn`" 包含在本 spec §5.1
- Phase 3 plan 中"切 caller 到 `_preflight_canonical_draft_check`" 替换为"工具内入口直接 inline check"

完成后 `2026-05-04-context-signal-and-intent-tag.md` plan 标注 retired。

## §10 Open Questions（reviewer 重点）

**spec r1 reviewer 已 answered（resolved）**：

- ✅ 方案 X vs Y → **方案 Y（一次性替换）**，但内部分 6 个 commit 顺序（§6.4）以降低风险
- ✅ `REPORT_BODY_REPLACE_TEXT_INTENT_RE` → **删**（per reviewer grep，仅 `_parse_report_body_replacement_intent` 使用，被旧 preflight/classifier 调用，不被 `_should_allow_non_plan_write`）
- ✅ `_validate_required_report_draft_prewrite` 边界 → §5.1 详述（删 5507-5520 + 5531-5615 + 5636-5716；保留 5522-5529 / 5617-5634 with caveats）
- ✅ content 长度 cap → **v1 加 cap**（§7.1 修订：max(3000, 3× target_snapshot.length)）
- ✅ `backend/report_writing.py` 模块 → **新模块**，仅放 pure helpers + target resolution + text scanner；dispatch / stateful tool execution 保留在 `chat.py`

**spec r2 reviewer 待评估（剩余）**：

1. **§3.5 `_detect_canonical_draft_write_obligation` 实施细节**：detector 的 keyword list 是从原 `_classify_canonical_draft_turn` 整体迁移还是只取部分？
2. **§7.6 `assistant_text_claims_modification` 启发式**：用什么具体 regex / pattern？误报率 vs 漏报率 trade-off？是否覆盖"我会去修改"（intent statement）vs"我已修改"（claim of completion）的区分？
3. **§7.7 mutation limit 是否适用于"读后写不通过"的 reject 路径**：如果 model 调 `rewrite_report_section` 但工具内 reject（target unresolved），mutation 是否 count？建议：**只在写盘成功后 set mutation**——本 spec §3.6 已暗示但未明说，r2 reviewer 应明确确认。
4. **§7.8 工具选择 benchmark 的 baseline**：10 个 message 中具体哪 10 个？谁拟定这个 benchmark suite？建议在 plan 阶段定义。
5. **`_validate_append_turn_canonical_draft_write` (chat.py:5522-5529 调用) 是否保留**：spec §5.1 标"保留 with caveats"，r2 reviewer 应确认实际是 keep 还是迁移。

应在 spec r2 review 中拍板上述 5 点，或在 plan 阶段细化（取决于复杂度）。
