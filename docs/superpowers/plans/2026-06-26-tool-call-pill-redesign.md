# 工具调用卡片重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把聊天里**正常的**工具调用从「多行 emoji 原始日志」改成原型的优雅单行 pill（工具图标 + 工具名 + 参数 + 成功/失败/进行中），call↔result 配对，**reload 后也持久显示**，摘要点击展开，复制干净，主聊天与独立审查共用一个组件。

**Architecture（v4，用户拍板 reload 入范围；codex 3 轮红队后定稿——reload 走「结构化 sibling 字段」而非注释，规避容器风险）:**
- 后端把**正常工具 call/result** 的 2 条 emoji 文本 SSE 事件换成结构化 `tool_call`/`tool_result`（带 `id` 配对；主聊天与独立审查都带 id）。**其余 `type:"tool"` 诊断文本事件原样保留**。
- 后端持久化**新增 `tool_events` 兄弟字段**到 `conversation.json` 的 assistant 消息上（`{role,content,tool_events:[{tool,arg,status,summary}]}`）；**现有 `<!-- tool-log -->` 注释机制完全不动**（保 provider 历史连续性 + 零容器风险）。arg/summary 复用 Task 1 的 `_sse_tool_arg`/`_sse_tool_summary`（live SSE 与 sibling **同一套派生**）。
- `GET /api/projects/{id}/conversation` **直接返回** assistant 消息的 `tool_events` 字段（无解析、无正则、无容器风险）；content 仍 strip 掉 legacy 注释保持正文干净。
- 前端每条 assistant 消息有 `toolEvents` 数组：**live** 从 SSE `reduceToolEvent`（按 id），**reload** 从端点 `tool_events` 字段。两路统一进 `msg.toolEvents`，`ToolCallList` 只渲染它（**前端不解析任何文本、无 emoji 字符、无 content 变异、无 sentinel**），pill 成组渲染在正文上方。
- **不碰** provider message / tool-call / `reasoning_content` / `tool_choice`（DeepSeek 官渠兼容不回归）；`_to_provider_message`(4036) assistant 只回 `{role,content}`、天然丢 `tool_events`，sibling 绝不泄漏 provider。

**Tech Stack:** FastAPI SSE（`backend/chat.py` / `backend/main.py` / `backend/independent_review.py`）、React + Tailwind 语义 token、Node `node:test`、pytest/unittest。

**Scope:**
- **In scope:** 正常工具 call/result 的 live + reload 渲染、配对、状态图标、摘要 click-to-expand、复制 strip、共享 pill、正常工具去 emoji、主聊天与审查统一、测试。
- **Out of scope:** 诊断类 `type:"tool"` 文本事件（禁工具/畸形/自修正/漏写重试/过多调用，chat.py ~2963/2973/2992/3020/3095/3108/3126）保持现状作文本提示、不 pill 化、不去其 emoji（用户拍板②）。**老对话（本次改动前已存的 assistant 消息）reload 不显示 pill**（无 `tool_events` 字段 → []，= 当前行为、无回归；老 `<!-- tool-log -->` 注释仍被 strip 出正文）。

**关键不变式（实施期必须守）:**
- 不改 `backend/chat.py` provider 相关：`_to_provider_message`(4022) / `_normalize_collected_assistant_tool_call_message`(~3612) / `_serialize_assistant_tool_call_message`(~3542) / tool role 消息(~3081-3085) / `reasoning_content` / `tool_choice`。
- **持久化注释不动**：`_append_tool_log_to_assistant`(~1543) / `_format_tool_pair_line`(~1492) **保持原样**（test_tool_log 现有断言不回归）。tool_events 是**新增并列字段**，不进 content。
- **`_load_conversation`(6041) 必须保留 `tool_events` 字段**（像 `attachment_transcripts` 那样，6064），否则下一轮 `_save_conversation`(6083) 重写整个文件会**抹掉**历史消息的 tool_events（load 重建只保白名单字段＝硬坑）。
- 前端设计 token 制：颜色/字号走语义 token 类，**禁裸 hex / `bg-[#..]` / emoji 字符**（v4 前端**无任何**工具文本解析、无 ✓/✗，天然无风险）。深色靠 token 自动切、禁 `dark:` 前缀。
- 信任边界：`tool`/`arg`/`summary` 是派生展示字符串，渲染纯文本（`{value}`，绝不 `dangerouslySetInnerHTML`）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/chat.py` | `_sse_tool_arg`/`_sse_tool_summary` helper；正常 call/result 发结构化 SSE（带 id）；删 2929 预告；**新增 `_build_tool_events` + 持久化时把 `tool_events` 作 sibling 字段写到 assistant 消息**；`_load_conversation` 保留 `tool_events`；注释机制 + 诊断事件**不动** | Modify |
| `backend/main.py` | `GET /conversation` 每条 assistant 直接返回 `m.get("tool_events", [])`（+ reload-i id）；content 仍 strip | Modify |
| `backend/independent_review.py` | tool_call/tool_result SSE 带 `id`（~606-653），malformed 分支也给合成 id | Modify |
| `tests/test_tool_log.py` | **现有断言不动**（注释机制不变）+ 加 `_build_tool_events` 结构化输出测试 | Modify |
| `tests/test_chat_runtime.py` | 正常工具事件断言迁结构化（~733/2562/7287）；保留诊断 `type:"tool"`；+ 持久化 sibling 字段断言 + `_load_conversation` 保留 sibling 回归 | Modify |
| `tests/test_main_api.py` | `GET /conversation` 返回 `tool_events`（有字段直返 + 老消息无字段降级 []） | Modify |
| `tests/test_stream_api.py` | 端点转发 `tool_call`/`tool_result` | Modify |
| `tests/test_independent_review.py` | 审查 SSE 带 id 断言 | Modify |
| `frontend/src/utils/toolEvents.js` | 纯函数：`reduceToolEvent`(live by id) / `firstArgValue`（**无文本解析、无 emoji**） | Create |
| `frontend/src/utils/chatPresentation.js` | `splitAssistantMessageBlocks` 移除 emoji-行 tool 识别 | Modify |
| `frontend/src/components/icons.jsx` | 新增 `IconTool`（+ `IconChevronDown` 若无） | Modify |
| `frontend/src/components/ToolCallPill.jsx` | 共享 pill（单行 + click-to-expand 摘要） | Create |
| `frontend/src/components/ToolCallList.jsx` | 渲染一条消息的 `toolEvents` 成 pill 组 | Create |
| `frontend/src/components/ChatPanel.jsx` | SSE 收集 tool_call/tool_result→`msg.toolEvents`（留 legacy `tool` 分支）；reload 映射端点 `tool_events`→`msg.toolEvents`；正文上方渲染 `ToolCallList` | Modify |
| `frontend/src/utils/independentReviewDrawer.js` | tool_call/tool_result 按 **id** 配对成 tool bubble | Modify |
| `frontend/src/components/IndependentReviewDrawer.jsx` | 用 ToolCallPill 渲染 | Modify |
| 前端测试 | `toolEvents`/`toolCallPill.source`/`chatPresentation`/`chatPanelSseRouting`/`reviewChatWindow` | Create/Modify |

**统一数据形状** `ToolEvent`：`{ id, tool, arg, status:"pending"|"success"|"error", summary }`。persist 的 sibling 元素无 id（端点补 `reload-<i>` 作 React key）；live SSE 与审查带真 id。

---

## Task 1: 后端结构化 SSE + tool_events sibling 持久化

**Files:** Modify `backend/chat.py`；Test `tests/test_chat_runtime.py`、`tests/test_tool_log.py`

- [ ] **Step 1a: 写失败测试（live 结构化，test_chat_runtime.py）**

读本文件既有 fake-stream tool 用例（真实 helper `_make_stream_tool_call_chunk`，~L134/733/2562/7287）**仿其构造**（不要发明 `_run_chat_collecting_events`，用已有方式收集 yield 的事件）加：
```python
def test_normal_tool_call_emits_structured_events(self):
    # ...仿既有用例用 _make_stream_tool_call_chunk 造 read_file 调用 + 收集 events...
    call = next(e for e in events if e.get("type") == "tool_call")
    res = next(e for e in events if e.get("type") == "tool_result")
    self.assertEqual((call["id"], call["tool"], call["arg"]), ("call_1", "read_file", "materials/x.md"))
    self.assertEqual((res["id"], res["tool"], res["status"]), ("call_1", "read_file", "success"))
    self.assertFalse(any(e.get("type") == "tool" and ("🔧 调用工具" in str(e.get("data","")) or "结果:" in str(e.get("data",""))) for e in events))
```
改 L733/L2562 旧 `🔧 调用工具`/`🔧 准备调用工具` 断言→结构化；L7287 `type=="tool"` 找正常工具→改 `tool_call`/`tool_result`；**保留诊断 `type:"tool"` 断言**。

- [ ] **Step 1b: 写失败测试（`_build_tool_events`，test_tool_log.py，现有断言不动）**

```python
def test_build_tool_events_structured(self):
    handler = self._handler()  # 本文件既有构造
    turn = [  # 仿 _pair_tool_calls_with_results 真实输入
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": '{"query": "县域文旅"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"status": "success", "results": [1, 2, 3]})},
    ]
    self.assertEqual(handler._build_tool_events(turn),
                     [{"tool": "web_search", "arg": "县域文旅", "status": "success", "summary": "3 results"}])
```

- [ ] **Step 2: 跑测试确认失败** → FAIL

- [ ] **Step 3: 加共享 helper（chat.py，`_format_tool_pair_line` 附近）**

```python
def _sse_tool_arg(self, name: str, args: "str | dict") -> str:
    try:
        d = json.loads(args) if isinstance(args, str) else (args or {})
    except json.JSONDecodeError:
        d = {}
    if not isinstance(d, dict) or not d or name == "append_report_draft":
        return ""
    val = str(next(iter(d.values())))
    return val[:37] + "..." if len(val) > 40 else val

def _sse_tool_summary(self, name: str, result: dict) -> str:
    if result.get("status") != "success":
        return str(result.get("message") or result.get("error") or "失败")[:40]
    if name == "web_search":
        return f"{len(result.get('results') or [])} results"
    if name == "fetch_url":
        return f"{round(len(result.get('content') or '') / 1024, 1)} KB"
    return ""

def _build_tool_events(self, current_turn_messages: List[Dict]) -> list:
    return [{
        "tool": p.name,
        "arg": self._sse_tool_arg(p.name, p.args),
        "status": "success" if p.result.get("status") == "success" else "error",
        "summary": self._sse_tool_summary(p.name, p.result),
    } for p in self._pair_tool_calls_with_results(current_turn_messages)]
```
（`ToolPair` 字段＝`name`/`args`/`result`，见 chat.py:286 / `_pair_tool_calls_with_results`:1393。）

- [ ] **Step 4: 改正常 call/result SSE（删预告 + 两处结构化）**

(a) 删 L2927-2929 `🔧 准备调用工具` yield。
(b) L3046-3049 → `yield {"type": "tool_call", "id": tool_call["id"], "tool": func_name, "arg": self._sse_tool_arg(func_name, func_args)}`
(c) L3079-3080 → `yield {"type": "tool_result", "id": tool_call["id"], "tool": func_name, "status": result.get("status","error"), "summary": self._sse_tool_summary(func_name, result)}`

- [ ] **Step 5: 持久化加 sibling 字段（`_finalize_assistant_turn` ~6788-6805，注释机制不动）**

`persisted_content` 行（6789-6794）**保持不变**（仍 append 注释）。改 Step 6 持久化块：
```python
tool_events = self._build_tool_events(current_turn_messages) if current_turn_messages else []
assistant_msg = {"role": "assistant", "content": persisted_content}
if tool_events:
    assistant_msg["tool_events"] = tool_events
if self._turn_context.get("system_triggered"):
    history.extend([assistant_msg])
else:
    history.extend([current_user_message, assistant_msg])
self._save_conversation(project_id, history)
return persisted_content
```

- [ ] **Step 6: `_load_conversation` 保留 sibling（6057-6070，仿 attachment_transcripts）**

`entry` 构造后加（**只接受 list + 只拷标量字段**，防损坏 conversation.json 把大对象带进前端/下次保存，codex R4-NIT3）：
```python
raw_te = message.get("tool_events")
if isinstance(raw_te, list):
    entry["tool_events"] = [{
        "tool": str(e.get("tool") or ""), "arg": str(e.get("arg") or ""),
        "status": e.get("status") if e.get("status") in ("success", "error", "pending") else "success",
        "summary": str(e.get("summary") or ""),
    } for e in raw_te if isinstance(e, dict) and e.get("tool")]
```
补 test_chat_runtime 回归：save 带 tool_events 的 assistant → `_load_conversation` 仍带该字段（防下一轮 re-save 抹掉）。

- [ ] **Step 6b: compaction summarizer 不消费工具元数据（codex R4-NIT2）**

`_sanitize_message_for_summary`(chat.py ~878) 加 `sanitized.pop("tool_events", None)`（compaction 摘要器不该看到结构化工具参数/摘要、避免重复消费旧工具信息——与现有附件净化同源思路）。补一条 compaction 回归测试：带 tool_events 的历史进 `_summarize_messages` 不报错且摘要输入不含工具元数据。

- [ ] **Step 7: 跑测试** Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py tests/test_tool_log.py -q` → PASS（结构化 + sibling 持久/保留 + 注释机制不回归 + DeepSeek 绿）

- [ ] **Step 8: Commit** `git commit -am "feat(tool-pill): structured tool SSE + persist tool_events sibling field"`

---

## Task 2: GET /conversation 返回 tool_events 字段

**Files:** Modify `backend/main.py`（~1276-1284）；Test `tests/test_main_api.py`

- [ ] **Step 1: 写失败测试** persist 一条 assistant `{role,content:"正文", tool_events:[{tool:"read_file",arg:"a.md",status:"success",summary:""}]}` 到 conversation.json；GET /conversation 断言该消息含 `tool_events`（首元素 tool/arg/status 对、带 `id`）。再断言**老消息**（仅 content 带 `<!-- tool-log\n- x ✓\n-->`、无 tool_events 字段）→ `tool_events == []` + content 被 strip。
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现** assistant 分支（~1276-1281）改：
```python
cleaned = strip_tool_log_comments(_strip_legacy_stage_ack(raw))
raw_events = m.get("tool_events") if isinstance(m.get("tool_events"), list) else []
# 显式构造、id 放最后（防历史项已有 id 被 **e 覆盖，codex R4-NIT1）+ 只拷标量字段（R4-NIT3）
tool_events = [{
    "tool": str(e.get("tool") or ""), "arg": str(e.get("arg") or ""),
    "status": e.get("status") if e.get("status") in ("success", "error", "pending") else "success",
    "summary": str(e.get("summary") or ""), "id": f"reload-{i}",
} for i, e in enumerate(raw_events) if isinstance(e, dict) and e.get("tool")]
sanitized.append({**m, "content": cleaned, "tool_events": tool_events})
```
（无正则、无 `re` import、无容器解析——字段直读。）
- [ ] **Step 4: 跑测试** → PASS / **Step 5: Commit**

---

## Task 3: 端点转发结构化 SSE（test_stream_api）

- [ ] **Step 1:** 仿 L45-124 mock chat_stream yield `tool_call`/`tool_result`，断言 SSE 转发。
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_stream_api.py -q` → PASS（透传；白名单则扩）。
- [ ] **Step 3: Commit**

---

## Task 4: 前端 toolEvents 纯函数（live reduce，无文本解析）

**Files:** Create `frontend/src/utils/toolEvents.js`、`frontend/tests/toolEvents.test.mjs`

- [ ] **Step 1: 写失败测试**
```js
import { test } from 'node:test'; import assert from 'node:assert/strict'
import { reduceToolEvent, firstArgValue } from '../src/utils/toolEvents.js'
test('reduceToolEvent：call 建 pending，result 按 id 更新且不丢 arg、保序', () => {
  let m = reduceToolEvent([], { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  assert.deepEqual(m, [{ id: 'c1', tool: 'read_file', arg: 'a.md', status: 'pending', summary: '' }])
  m = reduceToolEvent(m, { type: 'tool_call', id: 'c2', tool: 'web_search', arg: 'q' })
  m = reduceToolEvent(m, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: 'ok' })
  assert.deepEqual(m.map(e => [e.id, e.status]), [['c1','success'],['c2','pending']]); assert.equal(m[0].arg, 'a.md')
})
test('reduceToolEvent：result 先到也建条目', () => {
  assert.equal(reduceToolEvent([], { type: 'tool_result', id: 'z', tool: 'x', status: 'error', summary: 'b' })[0].status, 'error')
})
test('firstArgValue：取首值截断', () => {
  assert.equal(firstArgValue({ file_path: 'a.md' }), 'a.md')
  assert.equal(firstArgValue({}), '')
  assert.equal(firstArgValue({ q: 'x'.repeat(50) }).length, 40)
})
```

- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现 `frontend/src/utils/toolEvents.js`**
```js
// 工具事件纯函数（无副作用、无文本解析、无 emoji 字符）。reload 的结构化数据由后端给，前端只 reduce live SSE。
export function firstArgValue(args) {
  const d = args && typeof args === 'object' ? args : {}
  const keys = Object.keys(d)
  if (!keys.length) return ''
  const val = String(d[keys[0]])
  return val.length > 40 ? val.slice(0, 37) + '...' : val
}
export function reduceToolEvent(list = [], event = {}) {
  const id = event.id
  if (!id) return list
  const idx = list.findIndex(e => e.id === id)
  if (event.type === 'tool_call') {
    if (idx === -1) return [...list, { id, tool: event.tool || '', arg: event.arg || '', status: 'pending', summary: '' }]
    const c = list.slice(); c[idx] = { ...c[idx], tool: event.tool ?? c[idx].tool, arg: event.arg ?? c[idx].arg }; return c
  }
  if (event.type === 'tool_result') {
    if (idx === -1) return [...list, { id, tool: event.tool || '', arg: '', status: event.status || 'error', summary: event.summary ?? '' }]
    const c = list.slice(); c[idx] = { ...c[idx], tool: event.tool ?? c[idx].tool, status: event.status || 'error', summary: event.summary ?? '' }; return c
  }
  return list
}
```
- [ ] **Step 4: 跑测试** → PASS / **Step 5: Commit**

---

## Task 5: IconTool（+ 展开箭头）

- [ ] **Step 1:** `icons.jsx` 加 `IconTool`（`<path d="M9 18l-6-6 6-6M15 6l6 6-6 6"/>`，`stroke="currentColor"`）；无 `IconChevronDown` 则加（`<path d="M6 9l6 6 6-6"/>`）。
- [ ] **Step 2:** `cd frontend && npm run build` → built / **Step 3: Commit**

---

## Task 6: ToolCallPill（单行 + click-to-expand）

**Files:** Create `frontend/src/components/ToolCallPill.jsx`、`frontend/tests/toolCallPill.source.test.mjs`

- [ ] **Step 1: source-guard 失败测试**
```js
import { test } from 'node:test'; import assert from 'node:assert/strict'; import { readFileSync } from 'node:fs'
const src = readFileSync(new URL('../src/components/ToolCallPill.jsx', import.meta.url), 'utf8')
test('ToolCallPill：单行 + 状态图标 + 摘要 click-to-expand，全 token、无 emoji', () => {
  assert.match(src, /inline-flex/); assert.match(src, /IconTool/); assert.match(src, /IconCheck/); assert.match(src, /IconClose/)
  assert.match(src, /status\s*===\s*['"]pending['"]/); assert.match(src, /font-mono/); assert.match(src, /min-w-0/)
  assert.match(src, /useState/); assert.match(src, /summary/); assert.match(src, /event\.tool/); assert.match(src, /event\.arg/)
  assert.doesNotMatch(src, /#[0-9a-fA-F]{3,6}\b/); assert.doesNotMatch(src, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
})
```
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现**（源码/注释零 ✓/✗ 字符）
```jsx
import React, { useState } from 'react'
import { IconTool, IconCheck, IconClose, IconChevronDown } from './icons'
export default function ToolCallPill({ event }) {
  const [open, setOpen] = useState(false)
  if (!event) return null
  const { tool = '', arg = '', status = 'pending', summary = '' } = event
  const expandable = Boolean(summary)
  return (
    <div className="inline-flex flex-col max-w-full min-w-0">
      <div className={`inline-flex items-center gap-[9px] border border-border rounded-ibtn bg-card2 px-[11px] py-[7px] font-mono min-w-0 ${expandable ? 'cursor-pointer' : ''}`}
        onClick={expandable ? () => setOpen(o => !o) : undefined}
        role={expandable ? 'button' : undefined} aria-expanded={expandable ? open : undefined}>
        <IconTool size={13} className="text-abright flex-shrink-0" />
        <span className="text-xs text-text whitespace-nowrap flex-shrink-0">{tool}</span>
        {arg && <span className="text-11 text-t3 truncate min-w-0">{arg}</span>}
        {status === 'success' && <IconCheck size={13} className="text-success flex-shrink-0 ml-auto" />}
        {status === 'error' && <IconClose size={13} className="text-error flex-shrink-0 ml-auto" />}
        {status === 'pending' && <span className="ml-auto w-[7px] h-[7px] rounded-full bg-t3 animate-pulse flex-shrink-0" aria-label="进行中" />}
        {expandable && <IconChevronDown size={12} className={`text-t3 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </div>
      {expandable && open && <div className="text-11 text-t3 font-mono px-[11px] py-[5px] break-words">{summary}</div>}
    </div>
  )
}
```
- [ ] **Step 4: 跑测试 + build** → PASS / built / **Step 5: Commit**

---

## Task 7: ToolCallList + ChatPanel 接入

**Files:** Create `ToolCallList.jsx`；Modify `ChatPanel.jsx`、`chatPresentation.js`；Test `chatPresentation.test.mjs`、`chatPanelSseRouting.test.mjs`

- [ ] **Step 1: ToolCallList**
```jsx
import React from 'react'
import ToolCallPill from './ToolCallPill'
export default function ToolCallList({ toolEvents }) {
  if (!toolEvents || !toolEvents.length) return null
  return (<div className="flex flex-col items-start gap-[6px] mb-[10px]">
    {toolEvents.map(e => <ToolCallPill key={e.id} event={e} />)}</div>)
}
```
- [ ] **Step 2: chatPresentation 移除 emoji-行识别（失败测试先行）** `chatPresentation.test.mjs` 加 `splitAssistantMessageBlocks('🔧 调用工具: x\n✅ 结果: y')` 断言无 `type:'tool'` block；删旧 emoji-行→tool 用例（~L77-108）。改 `appendNonThinkingSegment` 删 `isToolLine` 分支。`getCopyableAssistantMessageText` 沿用 `stripToolLogComments`——不改。
- [ ] **Step 3: ChatPanel SSE 路由（L533-543，留 legacy + 新增收集）** `else if (parsed.type === 'tool')`（诊断）**保留**；后加：
```jsx
} else if (parsed.type === 'tool_call' || parsed.type === 'tool_result') {
  if (parsed.type === 'tool_call' && shouldFlushStreamingQueueImmediately('tool')) {
    flushStreamingQueueImmediately(assistantId, requestProjectId)
  }
  setMessages(prev => prev.map(m => m.id === assistantId
    ? { ...m, toolEvents: reduceToolEvent(m.toolEvents || [], parsed) } : m))
}
```
顶部 `import { reduceToolEvent } from '../utils/toolEvents'`。
- [ ] **Step 4: ChatPanel reload 映射端点 tool_events** `loadConversation`（~L139 GET /conversation 后映射后端消息处）每条 assistant 带 `toolEvents: m.tool_events || []`（读该映射真实代码、就地加字段）。
- [ ] **Step 5: ChatPanel 渲染** assistant 消息体 `<div className="space-y-2 ...">` 顶部插 `<ToolCallList toolEvents={msg.toolEvents} />`；删 L966-970 旧 `block.type === 'tool'` 内联 pill 分支。顶部 `import ToolCallList from './ToolCallList'`。
- [ ] **Step 6: 路由 source-guard（chatPanelSseRouting.test.mjs）** 断言含 legacy `parsed.type === 'tool'` + 新 `tool_call`/`tool_result` 收集 `reduceToolEvent` + reload `tool_events` 映射 + 渲染 `ToolCallList`。
- [ ] **Step 7: 跑测试 + build** `cd frontend && node --test tests/ && npm run build` → PASS（更新 `chatPanelSseRouting`/`sseEvents` 旧 `type:'tool'` 断言） / **Step 8: Commit**

---

## Task 8: 独立审查统一用 ToolCallPill

**Files:** Modify `backend/independent_review.py`、`tests/test_independent_review.py`、`independentReviewDrawer.js`、`IndependentReviewDrawer.jsx`；Test `reviewChatWindow.test.mjs`

- [ ] **Step 1:** 后端 tool_call/tool_result 事件加 `"id"`：正常分支用 `tc.get("id")`，**malformed 分支（~606）也给合成 id**（`f"rev-{index}"`，需 `enumerate`），tool_call 加 `"arg"`。补/改 test_independent_review 断言带 id。
- [ ] **Step 2:** `reviewChatWindow.test.mjs`：tool_call(id r1)+tool_result(id r1) → 1 个 `kind:'tool'` bubble、`status:'success'`、`id:'r1'`（失败先行）。
- [ ] **Step 3:** 聚合器：tool_call 追加 `{kind:'tool', id, tool, arg, status:'pending', summary:''}`；tool_result 按 **id** 找 pending bubble 原地更新（找不到追加完成态）。
- [ ] **Step 4:** 渲染（L287-300）`if (bubble.kind === 'tool') return <ToolCallPill key={i} event={bubble} />`；删旧两 `ToolCard` 分支、import `ToolCallPill`；`grep -rn ToolCard frontend/src` 无引用则删 `ToolCard`（MarkdownMessage.jsx:83）+ 清 import。
- [ ] **Step 5:** `.venv/bin/python -m pytest tests/test_independent_review.py -q && cd frontend && node --test tests/ && npm run build` → PASS / **Step 6: Commit**

---

## Task 9: 收口验证

- [ ] **Step 1:** `grep -n "🔧 调用工具\|🔧 准备调用工具" backend/chat.py`（空）+ `grep -rn "🔧 调用工具:\|✅ 结果:\|⚠️ 结果:" frontend/src | grep -iv test`（空）。诊断 `⚠️`/`type:"tool"` 仍在＝预期。
- [ ] **Step 2:** 后端全量 `.venv/bin/python -m pytest tests/ -q` → PASS（2 mac realpath 失败属环境差异）
- [ ] **Step 3:** 前端全量 + build `cd frontend && node --test tests/ && npm run build` → PASS
- [ ] **Step 4: 真模型 GUI 自测** `run_web.py`：发触发工具的消息→正常工具单行 pill；有摘要点开看摘要；复制不含 emoji/工具日志；**刷新**确认 pill 仍在（端点 tool_events sibling）；诊断（并行多调）仍文本显示；独立审查窗口工具 pill 化。
- [ ] **Step 5: Commit**

---

## Self-Review

**Spec 覆盖：** 单行 pill→T6；配对→T1(live id)+T8(审查 id)；**reload 持久**→T1(sibling 持久化 + `_load_conversation` 保留)+T2(端点直返)+T7(reload 映射 + ToolCallList)；摘要 expand→T6；复制 strip→沿用 stripToolLogComments；去 emoji→T1+T9；主聊天+审查统一→T7/T8；DeepSeek→T1 只改 SSE payload + sibling 字段（`_to_provider_message` 丢之、不入 provider）；诊断保持现状＝out of scope；老对话降级 []。

**类型一致性：** `ToolEvent {id,tool,arg,status,summary}` 在后端 SSE + `_build_tool_events` + 端点 + `reduceToolEvent` + 审查 bubble + `ToolCallPill` 一致。

**Placeholder 扫描：** 无 TBD；测试构造以文件真实既有模式为准（`_make_stream_tool_call_chunk` 等，注了参照行号）。

---

## 风险 / 实施注意（含 codex 3 轮红队结论）

1. 保留 legacy `type:"tool"` 诊断分支（R1-B①）。
2. **持久化注释机制 + `_format_tool_pair_line` 完全不动**（R1-B②）；tool_events 是新增 sibling 字段，与注释并存（注释供 provider 历史/legacy strip，sibling 供 reload pill）。
3. 结构化 live 测试在 test_chat_runtime；`_build_tool_events` 测试在 test_tool_log；端点 tool_events 测试在 test_main_api（R1-B③/④）。
4. 去 emoji 精确只碰正常 call/result，不碰诊断 `⚠️`（R1-B⑤）。
5. 前端**无任何**工具文本解析、无 ✓/✗ 字符（R1-B⑥ + R2-NIT1 彻底消除）。
6. **reload 数据源（R2-B① 修正）**：`GET /conversation` 服务端 strip 注释 → 改端点返回 sibling `tool_events` 字段（T2），前端不碰文本。
7. **容器风险彻底规避（R3-B①②③ 修正）**：tool_events 走 `conversation.json` 的**并列 JSON 字段**、不嵌 HTML 注释 → 无 `-->`/`]` 截断、无正则解析、无 `re` import。
8. **`_load_conversation` 必须保留 tool_events 字段**（不变式段）——否则下一轮 re-save 抹掉历史 sibling（load 白名单重建＝硬坑），T1 Step 6 + 回归测试守。
9. **双渲染（R2-B④）**：`ToolCallList` 只读 `msg.toolEvents`（live=SSE，reload=端点字段），不解析 content；正文恒 stripToolLogComments → 无双渲染。
10. **pills-at-top**：渲染正文上方成组，live（SSE，现状 tools 先于正文）与 reload（端点字段）同位同源；罕见「文字-工具-文字」交错丢中间位（可接受）。
11. 审查 malformed 分支给合成 id（R2-NIT2），防 id 聚合吞错误卡。
12. **DeepSeek 边界**：只改 SSE `yield` + assistant 消息加并列字段（`_to_provider_message`:4036 只回 `{role,content}`、丢 sibling）；T1 Step 7 跑 test_chat_runtime 守。

---

## 后续（follow-up，非本计划）
- 老对话（本次前已存）reload 不显 pill——如需补，单独写一次性迁移：解析旧 `<!-- tool-log -->` 注释回填 `tool_events` sibling（低优先级）。
- 折叠态摘要计数角标（web_search 旁标「3」）按实测口味再定。
- 注释机制最终能否退役（一旦 provider 历史不再需要它、sibling 全覆盖）——独立评估。
