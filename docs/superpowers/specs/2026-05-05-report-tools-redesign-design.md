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

### 2.1 保留 + 重构：`append_report_draft(content)`

工具签名不变（只接 content 参数，覆盖 begin / continue 两种意图）。fix3 cutover Session A/D 实测正确。

**改造**：入口校验从分散在 `_validate_append_turn_canonical_draft_write` / `_validate_required_report_draft_prewrite` 等多处迁移到工具内部 inline check（per spec r2 §C16），统一调用 §3.1 共享 helpers。

**Reject 路径**（统一到工具入口，不再走旧的 `_validate_required_report_draft_prewrite` 路径）:
| 失败原因 | 引导消息 |
|---|---|
| 阶段 < S4 | "本工具仅在 S4 阶段可用。当前阶段：X" |
| outline 未确认 | "请先在工作区确认大纲，再起草正文" |
| 本轮含多个 action family（mixed-intent） | "请把'起草正文'拆成单独一轮处理" |
| **已有本轮 canonical draft mutation** | "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改" |
| draft 已存在但本轮未 `read_file` 过 canonical draft | "draft 已存在，续写前请先 `read_file` 读最新正文" |
| 本轮已 `read_file` 但 mtime 已变 | "草稿在你阅读后被修改，请先重新 `read_file` 再提交" |
| web_search 后未 `fetch_url`（按 fetch_url gate） | "请先 `fetch_url` 读取候选网页正文，再写正文" |

**首次起草例外（draft 不存在）**：read-before-write 不强制；其他 check 仍生效。

成功路径：调用现有 file mutation infrastructure（`append_report_draft` 内部已有的 append 逻辑）。**写盘成功后** set `turn_context["canonical_draft_mutation"] = {...}`。

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
| 本轮含多个 action family（mixed-intent） | "请把章节重写拆成单独一轮处理" |
| **已有本轮 canonical draft mutation** | "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改" |
| draft 不存在 | "当前还没有正文草稿，请先用 `append_report_draft` 起草第一版" |
| user 消息不含章节前缀 | "请在消息中明确说明要改哪一章/节，例如'重写第二章'" |
| 章节前缀未唯一定位到 heading | "找不到 '第X章' 对应的 heading，请用 `read_file` 核对章节标题" |
| 多个章节前缀指向不同 heading（partial multi-prefix） | "本轮只支持改写一个章节。请单独发起每个章节的修改请求" |
| content 不以 `## ` 开头 | "`content` 必须以 `## 章节标题` 开头" |
| content 含多个 `## ` heading | "`content` 不能涉及多个章节。请只提交目标章节的完整内容" |
| content 长度超过 cap（max(3000, 3× target_snapshot 长度)） | "提交内容超过预期范围（X 字 vs 上限 Y 字），请只提交目标章节的内容" |
| 本轮未 `read_file` 过 canonical draft | "请先 `read_file` 读取正文，再修改" |
| 本轮已调用过 read_file 但 draft mtime 已变 | "草稿在你阅读后被修改，请先重新 `read_file` 再提交" |
| web_search 后未 `fetch_url` | "请先 `fetch_url` 读取候选网页正文，再写正文" |

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
| 本轮含多个 action family（mixed-intent） | "请把文字替换拆成单独一轮处理" |
| **已有本轮 canonical draft mutation** | "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改" |
| draft 不存在 | "当前还没有正文草稿..." |
| `old` 在 draft 中 0 次出现 | "目标文本 `<old>` 在草稿中未找到。请先 `read_file` 核对原文" |
| `old` 在 draft 中 ≥ 2 次出现 | "目标文本 `<old>` 在草稿中出现 N 次（不唯一）。请提供更具体的上下文使其唯一" |
| 本轮未 `read_file` 过 canonical draft | "请先 `read_file` 读取正文，再修改" |
| 本轮已调用过 read_file 但 draft mtime 已变 | 同 §2.2 |
| web_search 后未 `fetch_url` | 同 §2.2 |

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
| 本轮含多个 action family（mixed-intent） | "请把整篇重写拆成单独一轮处理" |
| **已有本轮 canonical draft mutation** | "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改" |
| draft 不存在 | "当前还没有正文草稿，请先用 `append_report_draft` 起草第一版" |
| user 消息不含全文重写关键词 | "看起来你只想改一部分。重写整章请用 `rewrite_report_section`，替换文字用 `replace_report_text`。如果确实要整篇重写，请明确说'整篇重写'或'全文重写'" |
| `content` 不以 `# ` 开头 | "`content` 必须以 `# 报告标题` 开头" |
| `content` 不含 `## ` heading | "`content` 必须含至少一个章节标题（`## `级别）" |
| content 长度超过 cap（max(8000, 2× current_draft_text 长度)） | "提交内容超过预期范围（X 字 vs 上限 Y 字），请只提交完整草稿" |
| 本轮未 `read_file` 过 canonical draft | "请先 `read_file` 读取正文，再修改" |
| 本轮已 read_file 但 mtime 已变 | "草稿在你阅读后被修改，请先重新 `read_file` 再提交" |
| web_search 后未 `fetch_url` | 同 §2.2 |

成功路径：调底层 `edit_file(file_path=REPORT_DRAFT_PATH, old_string=current_draft_text, new_string=content)`。

**用户消息检测**：reject 第 4 条要求 user 消息含明确"整篇重写"关键词。沿用现有 `REPORT_BODY_WHOLE_REWRITE_KEYWORDS`（"整篇重写", "全文重写", "推倒重写", "全部改写"）**并扩充 "推倒重来"**（per spec r2 reviewer 校对：tool description 用"推倒重来"，但常量列表只有"推倒重写"，必须同步）。如果工具被调用但 user 消息不含这些关键词 → reject 并引导。

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

每个工具入口**统一**调用 §3.1 helpers，**显式 inline**，不依赖外层 turn_context decision 变量。所有 4 个写工具共享的 check 列表（顺序固定）：

```python
SHARED_PRE_WRITE_CHECKS = (
    check_report_writing_stage,
    check_outline_confirmed,
    check_no_mixed_intent_in_turn,
    check_no_prior_canonical_mutation_in_turn,  # mutation limit
    check_no_fetch_url_pending,
)
```

工具入口示例：

```python
def rewrite_report_section(self, project_id, content):
    user_message = self._turn_context.get("user_message_text") or ""
    
    # 1. 共享 pre-write checks（含 mutation limit）
    for check in SHARED_PRE_WRITE_CHECKS:
        err = check(self.skill_engine, project_id, self._turn_context, user_message)
        if err: return {"status": "error", "message": err}
    
    # 2. 工具特定 check（read-before-write + draft 存在 + target unique resolve）
    err = check_read_before_write_canonical_draft(self._turn_context, project_id)
    if err: return {"status": "error", "message": err}
    
    draft_text = self._read_project_file_text(project_id, REPORT_DRAFT_PATH) or ""
    if not draft_text:
        return {"status": "error", "message": "当前还没有正文草稿..."}
    
    target = resolve_section_target(user_message, draft_text)
    if target is None:
        return {"status": "error", "message": "请明确说明要改哪一章/节..."}
    
    # 3. content 校验（## 开头 + 不含多 ## + 长度 cap）
    if not content.startswith("## "):
        return {"status": "error", "message": "`content` 必须以 `## 章节标题` 开头"}
    extra_h2 = sum(1 for line in content.split("\n") if line.startswith("## "))
    if extra_h2 != 1:
        return {"status": "error", "message": "`content` 不能涉及多个章节..."}
    cap = max(3000, 3 * len(target["snapshot"]))
    if len(content) > cap:
        return {"status": "error", "message": f"提交内容超过预期范围（{len(content)} 字 vs 上限 {cap} 字）..."}
    
    # 4. 写盘 → 成功后 set canonical_draft_mutation
    result = self._do_edit_file(REPORT_DRAFT_PATH, old_string=target["snapshot"],
                                new_string=content)
    if result.get("status") == "success":
        self._turn_context["canonical_draft_mutation"] = {
            "tool": "rewrite_report_section",
            "label": target["label"],
            "ts": now(),
        }
    return result
```

`append_report_draft` / `replace_report_text` / `rewrite_report_draft` 入口模板同构，差异只在工具特定 check（如 append 首次起草放宽 read-before-write、replace 检查 unique old、rewrite_draft 检查 user 消息含全文重写关键词等）。**所有 4 个工具入口都包含 `SHARED_PRE_WRITE_CHECKS` 全套**（含 mutation limit）。

### 3.3 与 `_turn_context` 的关系

**不再需要 `canonical_draft_decision` / `required_write_snapshots` / `draft_action_events` 等 turn_context 字段**——意图判定移到工具入口，不再有"轮次开始时预判后续工具行为"的需要。

`_turn_context` 字段调整：

**新增**：
- `user_message_text`（**new**）— 在 `_build_turn_context` 中用现有 helper `_extract_user_message_text(current_user_message)` 取出 raw user msg 字符串并 cache。新工具在 `rewrite_report_section` / `rewrite_report_draft` 入口读这个字段做 keyword 检测和 `resolve_section_target`。
- `canonical_draft_write_obligation`（**new**, §3.5）— 由 `_detect_canonical_draft_write_obligation` 在 turn-start 写入；turn-end 时 `_chat_stream_unlocked` / `_chat_unlocked` 的 no-tool-call 分支检查"obligation 存在但 mutation 没发生" 触发 retry。**retry 不在 `_finalize_assistant_turn` 内**（该函数只 finalize/persist）。
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

**turn-end 对账**：retry 入口**不在** `_finalize_assistant_turn`（该函数负责 finalize/persist，不重试）；retry 入口在外层 chat loop——具体两处 model emit 完后、`_finalize_assistant_turn` 调用前：

- `_chat_stream_unlocked` 的 no-tool-call 分支（流式路径）
- `_chat_unlocked` 的 no-tool-call 分支（非流式路径）

伪代码（两处对称插入）：

```python
# 在 model emit 完成、判断本轮是否还要 loop 时
obligation = self._turn_context.get("canonical_draft_write_obligation")
mutation = self._turn_context.get("canonical_draft_mutation")
if obligation and not mutation:
    if assistant_text_claims_modification(assistant_message):
        # model 在文本里说"已修改"但 0 个 mutation event → 强行再 loop 一次
        corrective = (
            "你在回复中声称已修改正文（"
            f"obligation={obligation['tool_family']}），"
            "但本轮没有成功调用任何写正文工具（append_report_draft / "
            "rewrite_report_section / replace_report_text / rewrite_report_draft）。"
            "请实际调用对应工具完成写入，不要只在文字中声明已完成。"
        )
        # 注入 corrective 进 messages 后 continue 主 loop（max_iter 仍生效）
        self._inject_synthetic_user_correction(corrective)
        continue  # 进下一轮 model emit
```

**关键决策**：retry 不是 `_finalize_assistant_turn` 的责任（它只 persist），而是 chat loop 层的责任。这跟 fix4 现有的 `required_write_snapshots` 触发的 retry 路径**位置一致**——所以不是新机制，是现有机制的简化复用。

**`assistant_text_claims_modification(text)` 启发式（per spec r2 reviewer §13 提议的 regex）**：

```python
_TEXT_CLAIM_RE_1 = re.compile(
    r"(?:已|已经|完成|写完|改完|重写完|替换完|同步|落盘)"
    r"[^。！？!?\n]{0,30}"
    r"(?:正文|草稿|报告|章节|第[一二三四五六七八九十0-9]+章|content/report_draft_v1\.md)"
)
_TEXT_CLAIM_RE_2 = re.compile(
    r"(?:正文|草稿|报告|章节|第[一二三四五六七八九十0-9]+章)"
    r"[^。！？!?\n]{0,30}"
    r"(?:已|已经|完成|写完|改完|重写完|替换完|同步|落盘)"
)
_INTENT_RE = re.compile(r"我会|我准备|我将|我正在|我可以|让我")  # 排除：意图陈述非声称完成

def assistant_text_claims_modification(text: str) -> bool:
    if _INTENT_RE.search(text):
        # "我会重写第二章" / "我将修改正文" — 是动作意图，不是已完成声明
        # 但如果同时含 _TEXT_CLAIM_RE_1/2 ，仍算（model 可能既声明意图又声称已完成混合）
        if not (_TEXT_CLAIM_RE_1.search(text) or _TEXT_CLAIM_RE_2.search(text)):
            return False
    return bool(_TEXT_CLAIM_RE_1.search(text) or _TEXT_CLAIM_RE_2.search(text))
```

放入 `backend/report_writing.py`，单测 cover 经典正反例（"已完成第二章重写" → True；"我会重写第二章" → False；"我会重写第二章，已完成" → True 兜底）。

**这样保留了 fix4 当前的 "model 撒谎说写了但实际没写" 保护**，但 detector 简化（只回答 yes/no，不分类），且不再驱动 gate（gate 删除）。

**关键设计区别**：
- 当前 fix4：preflight 输出精细 `mode/scope/target` → 驱动 gate + scope enforcement + write obligation 三件事
- 新设计：write obligation 只用粗粒度 yes/no 信号；gate 不存在；scope enforcement 移到工具内 inline check

### 3.6 一轮一次 canonical mutation 限制（保留）

当前 fix4 在某处用 `turn_context["canonical_draft_mutation"]` track 已经做过 canonical draft 修改，防 model 一轮内反复改坏。

**保留**：所有 4 个写正文工具入口都加共享 helper：

```python
def check_no_prior_canonical_mutation_in_turn(turn_context) -> str | None:
    if turn_context.get("canonical_draft_mutation"):
        return "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改"
    return None
```

**全部 4 个写工具的入口 check 列表显式包含 `check_no_prior_canonical_mutation_in_turn`**：

- `append_report_draft` 入口 — §3.2 模板 + §2.1 reject table
- `rewrite_report_section` 入口 — §3.2 模板 + §2.2 reject table
- `replace_report_text` 入口 — §3.2 模板 + §2.3 reject table
- `rewrite_report_draft` 入口 — §3.2 模板 + §2.4 reject table

工具**成功写盘后**才 set `turn_context["canonical_draft_mutation"] = {...metadata...}`。**Reject 路径不 set**（已经 fail 了，让 model 用别的工具或重试时还能写一次）。

**Cross-turn 行为**：新 turn 调 `_new_turn_context` 时，`canonical_draft_mutation` 默认 None，所以新 turn 可以正常写。

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
| `backend/chat.py` | `_validate_append_turn_canonical_draft_write` 函数体 + `_validate_required_report_draft_prewrite` line 5522-5529 调用点 | -80 | **删** — 把 append-turn 校验逻辑 inline 迁移到 `append_report_draft` 工具入口（per spec r2 reviewer §C16）。这样消除 fix4 隐藏的"老 hidden-decision coupling"。 |
| `backend/chat.py` | `_validate_required_report_draft_prewrite` line 5617-5634 (generic edit/write fallthrough) | 0 | **保留** — 服务于 `edit_file` / `write_file` 写非 canonical 路径的通用校验，与 canonical draft 工具无关 |
| `backend/chat.py` | full-draft rewrite branch lines 5547-5584 | 0 | **保留**（被新 `rewrite_report_draft` 工具 inline 替代后再决定，第一轮 spec 实施保留）|
| `backend/chat.py` | `_run_phase2a_compare_writer` | -50 | **删**（compare 不再有意义） |
| `backend/chat.py` | `_extract_user_message_text` | 0 | **保留** — 仍是 utility，新工具内会用 |
| `backend/chat.py` | 安全删除的常量集（per reviewer grep, only used in deleted classifier/preflight）：`_DRAFT_INTENT_PREFLIGHT_KEYWORDS`、`REPORT_BODY_FIRST_DRAFT_KEYWORDS`、`REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS`、`REPORT_BODY_WHOLE_REWRITE_KEYWORDS`、`REPORT_BODY_SECTION_REWRITE_KEYWORDS`、`REPORT_BODY_CONDITIONAL_TARGET_EXPANSION_KEYWORDS`、`REPORT_BODY_FOLLOWUP_EXPANSION_SIGNALS`、`REPORT_BODY_EXPLICIT_WRITE_KEYWORDS`、`REPORT_BODY_SHORT_CONTINUATION_KEYWORDS`、`REPORT_BODY_CHAPTER_WRITE_RE`、`REPORT_BODY_INLINE_EDIT_RE`、`REPORT_BODY_REPLACE_TEXT_INTENT_RE` | -60 | **删** — but §3.5 detector 复用其中部分作为粗粒度 yes/no 信号；实施时把需要的 list 复制到 `backend/report_writing.py` 后再删 chat.py 原位 |
| `backend/chat.py` | 保留常量：`REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS`、`REPORT_BODY_INSPECT_FILE_KEYWORDS`（被 `_should_allow_non_plan_write` 等仍用） | 0 | **保留** |
| `backend/chat.py` | turn_context 新增 3 字段：`user_message_text`、`canonical_draft_write_obligation`、`read_file_snapshots` 默认 `""/None/{}` | +10 | **新增** |
| `backend/chat.py` | 3 个新工具的 callable（`rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`）+ tool schema 注册（`_get_tools` per reviewer grep）+ dispatch 路由（`_execute_tool` per reviewer grep）| +280 | **新增** |
| `backend/chat.py` | `_detect_canonical_draft_write_obligation` 函数 + `_chat_stream_unlocked` / `_chat_unlocked` 的 no-tool-call 分支 obligation 对账（retry 注入） | +60 | **新增** — retry 入口在 chat loop 层不在 `_finalize_assistant_turn`（§3.5）|
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

**v1 缓解（mandatory，per spec r2 reviewer §C13）**：

每个 rewrite 类工具内 enforce 简单 content 长度上限：

```python
MAX_CONTENT_LEN = max(3000, 3 * len(rewrite_target_snapshot))  # rewrite_report_section
MAX_CONTENT_LEN = max(8000, 2 * len(current_draft_text))        # rewrite_report_draft
```

content 超出 cap → reject "提交内容超过预期范围（X 字 vs target 上限 Y 字），请只提交目标章节/草稿的内容"。这个 cap 是廉价的 sanity check：catch model 把整份草稿当章节内容塞进来的明显 misuse。

**其他更严的检查**（**不在 v1**）：检测 `### 第N章` pattern 暗示新章节标题——观察 v1 实际 model 行为再决定是否需要。

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

### 7.4 Risk δ — `_validate_required_report_draft_prewrite` 拆解（与 §5.1 对齐）

该函数当前同时服务于多种场景；本 spec 拆解如下（与 §5.1 deletion 表 + §10 reviewer Q5 的 inline-migrate 决议一致）：

- `append_report_draft` 的"轮内必须实际写入" 校验：**inline migrate 到 `append_report_draft` 工具入口**（§3.2 SHARED_PRE_WRITE_CHECKS + 工具特定 check）；旧函数 `_validate_append_turn_canonical_draft_write` **删除**
- `edit_file` 写 canonical draft 的 old_string 校验：**整段删**（line 5531-5615 / 5636-5716，per §5.1），因为通用 edit_file 不再写 canonical draft（§2.5 enforce）
- `_required_write_snapshots` 对账：仅删除 canonical draft 部分；plan/* 文件保留 turn-end 对账（per §3.3 turn_context 字段保留说明）
- `chat.py:7156` 第二调用点：reviewer 要求实施前 grep 确认是否触达 canonical draft 路径；如触达，按上述规则改写

**实施 confirm**：删除范围跟 §5.1 deletion table 一一对应，不重不漏。spec 不再有"保留 canonical draft prewrite"的描述（v3 之前 §7.4 错误地说"保留"，v3 修正）。

### 7.5 Risk ε — frontend 影响

新工具的 tool log 显示需要 frontend 适配？预期 `frontend/src/components/ChatPanel.jsx` 通过 OpenAI 协议接收 tool name/args 自动渲染——**不需要改 frontend**。但要确认 `frontend/utils/chatPresentation.js` 的 tool block 解析对未知工具名的兼容性（默认就显示 tool name，应该 OK）。

### 7.6 Risk ζ — model 不调任何 tool 但声称"已修改"（reviewer §A3 提出）

**保留方案在 §3.5**：用粗粒度 `_detect_canonical_draft_write_obligation` + turn-end 对账 retry 机制保护。这是当前 fix4 的 `required_write_snapshots` 机制的**简化版**。

**实施时关键测试**：
- model 输出 "已经把第二章重写完毕" 但 0 个 write tool_call → 触发 retry，告诉 model "你只声称修改但没调用工具，请实际调 `rewrite_report_section`"
- model 输出 "好的我来重写" 但 stop reason 是 length（被截断）→ 不应视为撒谎，应 retry/continue 不带特定提示
- model 调了 `read_file` 但没调写工具 → 取决于 obligation：如果 obligation=section_rewrite 但 0 mutation，触发 retry

`assistant_text_claims_modification(text)` 启发式：具体 regex 见 §3.5 `_TEXT_CLAIM_RE_1` / `_TEXT_CLAIM_RE_2` / `_INTENT_RE` 三组合。**正面 case**（return True）：含 "已 + 正文" / "草稿 + 完成" 这类完成声明短语；**负面 case**（return False）：仅含 "我会修改" / "我准备重写" 这类意图陈述。`backend/report_writing.py` 实现 + 单测 cover 5+ 正反例。

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
9. **No regression**（具体测试 / smoke cases，**per spec r2 reviewer §C10 要求 mechanically verifiable**；测试 class 名以现有 codebase 实际存在的为准）:
   - stage-ack 系统：以下 test class 必须保持 PASS（实际命名见 `tests/test_chat_runtime.py`）— `StageAckFinalizePipelineTests`、`StreamSplitSafeTailDraftActionTests`（剪枝保留 stage-ack 部分；类名按实际文件保留或重命名为 `StreamSplitSafeTailStageAckTests`，由 plan 阶段决定具体动作），加上新增 `StageAckRegressionTests`（针对 draft-action 删除后 stage-ack 流式扫描 / parse / strip 不受影响）
   - 现有 plan/* 写文件路径：用户说 "确认大纲" → 触发 `outline_confirmed_at` checkpoint set；plan/* 文件 missing 时 `_required_writes_satisfied` 仍 detect
   - 现有 stage advance 路径：用户说 "开始审查" → S4→S5 切换 + `<stage-ack>review_started_at</stage-ack>` 执行成功（注意 KEY 是 `review_started_at` 不是 `review_started`，per `backend/skill.py:42` / `backend/stage_ack.py:23` 的 enum 定义）
   - 现有 fetch_url gate：先 web_search 再 write 必须 fail；fetch_url 后 write 必须 pass
   - mixed-intent split：用户消息含 "重写第二章并导出 PDF" → reject 让 model 拆轮
   - 以上 case 必须以 named tests 在 `tests/test_chat_runtime.py` 或 `tests/test_report_writing.py` 中存在；CI run 全部 pass。**plan 阶段**逐项 grep 现有测试名 confirm 命名，避免 spec 跟实现 drift。

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
- ✅ content 长度 cap → **v1 加 cap mandatory**（§7.1 v3 修订：rewrite_report_section 用 `max(3000, 3× target_snapshot.length)`；rewrite_report_draft 用 `max(8000, 2× current_draft_text.length)`；超出立即 reject）— 已在 §7.1 由 r2 reviewer 推论确认
- ✅ `backend/report_writing.py` 模块 → **新模块**，仅放 pure helpers + target resolution + text scanner；dispatch / stateful tool execution 保留在 `chat.py`

**spec r2 reviewer 已 answered (resolved)**：

- ✅ Q1: detector keyword source — 用 raw intent signals（phrase-hit sets + `REPORT_BODY_REPLACE_TEXT_INTENT_RE`），不迁移 stage gate / scope / target resolution / priority logic（§3.5 文本已对齐）
- ✅ Q2: `assistant_text_claims_modification` 启发式 — 用 §3.5 给出的 `_TEXT_CLAIM_RE_1/2` + `_INTENT_RE` regex 组合，含 intent-statement 排除
- ✅ Q3: mutation flag on reject — confirmed only-on-success（§3.6 已 explicitly 写明 reject 不 set）
- ✅ Q4: 10-message benchmark suite — reviewer 给出具体 10 messages（§8 验收标准 + plan 阶段 implement）
- ✅ Q5: `_validate_append_turn_canonical_draft_write` retention — **inline migrate** to `append_report_draft` tool entry，删 legacy（§5.1 已修订）

**剩余待评估（plan 阶段）**：

1. **scope split 决策（per r2 reviewer §C17）**：reviewer 建议 split into Phase B-1 (新工具 + schemas + SKILL cleanup + write-obligation retry) + B-2 (mutation hardening + mtime snapshots + legacy deletion)。**保持单一 spec design 不变**，但在 plan 阶段切分为两个独立 plans，每个 plan 自己走 codex review + cutover smoke。**plan stage 决定**。

应在 plan 阶段细化以上 1 点。
