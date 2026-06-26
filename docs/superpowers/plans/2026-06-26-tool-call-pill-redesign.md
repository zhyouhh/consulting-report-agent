# 工具调用卡片重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把聊天里**正常的**工具调用从「多行 emoji 原始日志」改成原型的优雅单行 pill（工具图标 + 工具名 + 参数 + 成功/失败状态图标），call↔result 配对，复制时干净 strip，主聊天与独立审查共用一个组件。

**Architecture:** 后端把**正常工具 call/result** 的 2 条 emoji 文本 SSE 事件换成结构化 `tool_call` / `tool_result` 事件（带 `id` 配对，主聊天与独立审查都带 id）；**其余 `type:"tool"` 诊断文本事件（禁工具/畸形/自修正/漏写重试等）原样保留**。前端把工具事件存进 message 上的 `toolEvents` map（按 id 键），content 里只留一个轻量占位 sentinel `<<tool-call:ID>>` 保持「文字—工具—文字」的行内顺序；新增共享 `ToolCallPill` 组件按状态渲染。**全程只改 SSE 呈现层 + 前端，不碰 provider message / tool-call / `reasoning_content` / `tool_choice` 序列化，也不碰持久化 `_format_tool_pair_line`（DeepSeek 官渠兼容 + tool-log 持久化不回归）。**

**Tech Stack:** FastAPI SSE（`backend/chat.py` / `backend/independent_review.py`）、React + Tailwind 语义 token、Node `node:test`、pytest/unittest。

**Scope（明确边界）:**
- **In scope:** 正常工具 call/result 的 live 渲染（主聊天 + 独立审查）、call/result 配对、状态图标（成功/失败/进行中）、复制 strip、共享 pill 组件、参数展示、去掉**正常工具**的 emoji、测试。
- **Out of scope（非目标，理由）:**
  - **诊断类 `type:"tool"` 文本事件**（禁工具、畸形 tool_calls、自修正、漏写重试、过多调用等，见 chat.py ~2963/2973/2992/3020/3095/3108/3126）——保持现状作文本提示，**不 pill 化、不去其 emoji**。本次只动「正常 call/result」。
  - **reload 后 pill 持久显示**——当前行为即 reload 时 `<!-- tool-log -->` 被 strip、工具不显示；本次不引入回归也不顺带修（需改后端持久化格式 + 双格式解析，单列 follow-up）。

**关键不变式（实施期必须守）:**
- 不改 `backend/chat.py` provider 相关：`_to_provider_message` / `_normalize_collected_assistant_tool_call_message`(~3612) / `_serialize_assistant_tool_call_message`(~3542) / tool role 消息(`{"role":"tool",...}`, ~3081-3085) / `reasoning_content` / `tool_choice`。只改正常 call/result 的 `yield {...}` SSE payload。
- 不改持久化：`_append_tool_log_to_assistant` / `_format_tool_pair_line`(~1492) **完全不动**。SSE 展示用**独立的**新 helper，不与持久化逻辑共用（持久化串格式由 `test_tool_log.py` 锁，避免牵连）。
- 不删 ChatPanel 的 legacy `type:"tool"` 分支（诊断文本仍要显示），只新增 `tool_call`/`tool_result` 分支。
- 前端设计 token 制：颜色/字号走语义 token 类（`bg-card2`/`text-t2`/`text-t3`/`text-text`/`text-success`/`text-error`/`text-abright`/`text-11`/`text-xs` 等），**禁止裸 hex / `bg-[#..]` / emoji 字符**（含源码注释里的 ✓/✗/🔧——`paletteGuard` 的 `☀-➿`、`\u{1F300}-\u{1FAFF}` 会命中）。深色靠 token 自动切，源码禁用 `dark:` 前缀。
- 信任边界：`arg`/`summary` 是派生展示字符串，渲染为纯文本（`{value}`，绝不 `dangerouslySetInnerHTML`）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/chat.py` | 加独立 SSE helper；正常 call/result 发结构化 `tool_call`/`tool_result`（带 id）；删 2929 预告；诊断事件不动 | Modify |
| `backend/independent_review.py` | tool_call/tool_result SSE 事件**带上已有的 `id`**（~643/606-653） | Modify |
| `tests/test_chat_runtime.py` | 正常工具事件断言迁到结构化；保留诊断 `type:"tool"` 断言（~733/2562/7287） | Modify |
| `tests/test_stream_api.py` | 端点能转发 `tool_call`/`tool_result`（mock chat_stream 产出结构化） | Modify |
| `tests/test_tool_log.py` | 持久化格式回归守卫（**不改**，跑通即可） | 跑通 |
| `tests/test_independent_review.py` | 审查 SSE 带 id 断言 | Modify |
| `frontend/src/utils/toolEvents.js` | 纯函数：`reduceToolEvent` / `toolCallSentinel` / `parseToolCallSentinelLine` / `TOOL_CALL_SENTINEL_RE` / `firstArgValue` | Create |
| `frontend/src/utils/chatPresentation.js` | `splitAssistantMessageBlocks` 认 sentinel（**移除 emoji-行识别**）+ 复制 strip sentinel | Modify |
| `frontend/src/components/icons.jsx` | 新增 `IconTool` | Modify |
| `frontend/src/components/ToolCallPill.jsx` | 共享 pill 组件 | Create |
| `frontend/src/components/ChatPanel.jsx` | **保留** legacy `type:"tool"` 分支 + **新增** `tool_call`/`tool_result` 路由；渲染 ToolCallPill | Modify |
| `frontend/src/utils/independentReviewDrawer.js` | tool_call/tool_result 按 **id** 配对成单个 tool bubble | Modify |
| `frontend/src/components/IndependentReviewDrawer.jsx` | 用 ToolCallPill 渲染 tool bubble | Modify |
| `frontend/tests/toolEvents.test.mjs` | reducer/sentinel/firstArgValue 纯函数测试 | Create |
| `frontend/tests/toolCallPill.source.test.mjs` | pill 组件 source-guard | Create |
| `frontend/tests/chatPresentation.test.mjs` | sentinel split + strip；移除旧 emoji-行 tool 断言 | Modify |
| `frontend/tests/chatPanelSseRouting.test.mjs` | tool_call/tool_result 路由 + legacy tool 分支仍在 | Modify |
| `frontend/tests/reviewChatWindow.test.mjs` | 审查按 id 配对成单 pill | Modify |

**统一数据形状** `ToolEvent`（前后端约定）：
```
{ id: string, tool: string, arg: string, status: "pending"|"success"|"error", summary: string }
```
- `tool_call` 事件携带 `{id, tool, arg}`；`tool_result` 事件携带 `{id, tool, status, summary}`。
- 主聊天与独立审查**都带 id**，配对方式统一（按 id），不用「同名启发式」。

---

## Task 1: 后端正常 call/result 发结构化事件（独立 SSE helper，不碰持久化）

**Files:**
- Modify: `backend/chat.py`（2927-2929 删预告；3043-3085 正常 call/result）
- Test: `tests/test_chat_runtime.py`（结构化事件 + 更新旧 emoji 断言）、`tests/test_tool_log.py`（回归守卫，不改）

- [ ] **Step 1: 写失败测试（在 test_chat_runtime.py，复用既有 fake stream）**

先读 `tests/test_chat_runtime.py` 找到既有「fake OpenAI stream 发 tool call」的构造（关键词 `_make_stream_tool_call_chunk` / fake chunk / `🔧 调用工具`，约 L733/L2562/L7287），仿其模式加：

```python
def test_normal_tool_call_emits_structured_events(self):
    # 正常工具 call/result 现在是结构化 tool_call/tool_result（带 id），不再是 emoji 文本。
    events = self._run_chat_collecting_events(  # 用本文件既有的 collect-events 方式（仿 L733 用例）
        tool_call={"id": "call_1", "name": "read_file", "arguments": '{"file_path": "materials/x.md"}'},
        tool_result={"status": "success", "content": "hi"},
    )
    call = next(e for e in events if e.get("type") == "tool_call")
    res = next(e for e in events if e.get("type") == "tool_result")
    self.assertEqual((call["id"], call["tool"], call["arg"]), ("call_1", "read_file", "materials/x.md"))
    self.assertEqual((res["id"], res["tool"], res["status"]), ("call_1", "read_file", "success"))
    # 正常 call/result 不再发 emoji 文本
    self.assertFalse(any(e.get("type") == "tool" and ("🔧 调用工具" in str(e.get("data","")) or "结果:" in str(e.get("data",""))) for e in events))
```

> 实施时以 `test_chat_runtime.py` 真实的 ChatHandler + fake stream 构造为准内联（不要新建 helper 抽象）。同时**改**该文件中 L733/L2562 断言 `🔧 准备调用工具`/`🔧 调用工具` 的旧用例：正常 call 改断言结构化 `tool_call`；L7287 从 `type=="tool"` 找正常写工具事件的逻辑改为找 `tool_call`/`tool_result`。**保留**任何断言**诊断** `type:"tool"`（禁工具/畸形/漏写重试）的用例不动。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py::<Class>::test_normal_tool_call_emits_structured_events -v`
Expected: FAIL（当前无 `tool_call`/`tool_result`）

- [ ] **Step 3: 加独立 SSE helper（不碰 `_format_tool_pair_line`）**

在 `backend/chat.py` `_format_tool_pair_line` 附近加两个**独立**方法（自有逻辑，不与持久化共用）：

```python
def _sse_tool_arg(self, name: str, args: "str | dict") -> str:
    """SSE pill 用：工具首参数短值（无引号）。read_file→路径、web_search→query、append_report_draft→''。"""
    try:
        d = json.loads(args) if isinstance(args, str) else (args or {})
    except json.JSONDecodeError:
        d = {}
    if not isinstance(d, dict) or not d or name == "append_report_draft":
        return ""
    val = str(next(iter(d.values())))
    return val[:37] + "..." if len(val) > 40 else val

def _sse_tool_summary(self, name: str, result: dict) -> str:
    """SSE pill 用：结果短摘要。失败给错误首句，成功按工具给一句或空。"""
    if result.get("status") != "success":
        return str(result.get("message") or result.get("error") or "失败")[:40]
    if name == "web_search":
        return f"{len(result.get('results') or [])} results"
    if name == "fetch_url":
        return f"{round(len(result.get('content') or '') / 1024, 1)} KB"
    return ""
```

- [ ] **Step 4: 改正常 call/result 的 SSE（只这两处 + 删预告）**

(a) 删 L2927-2929：
```python
# 删除：yield {"type": "tool", "data": f"🔧 准备调用工具: {tc['function']['name']}"}
```

(b) L3046-3049 换成：
```python
yield {"type": "tool_call", "id": tool_call["id"], "tool": func_name,
       "arg": self._sse_tool_arg(func_name, func_args)}
```

(c) L3079-3080 换成：
```python
yield {"type": "tool_result", "id": tool_call["id"], "tool": func_name,
       "status": result.get("status", "error"),
       "summary": self._sse_tool_summary(func_name, result)}
```

> **不动**：L3041-3043（assistant_tool_message 装配）、L3081-3085（tool role 消息）、`_execute_tool`、`_append_tool_log_to_assistant`、`_format_tool_pair_line`，以及 ~2963/2973/2992/3020/3095/3108/3126 的诊断 `type:"tool"` 事件。

- [ ] **Step 5: 跑测试确认通过（含持久化 + DeepSeek 回归）**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py tests/test_tool_log.py -q`
Expected: PASS（结构化事件 OK；持久化串不变；DeepSeek 兼容用例不回归）

- [ ] **Step 6: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(tool-pill): emit structured tool_call/tool_result for normal tool calls"
```

---

## Task 2: 端点转发结构化事件（test_stream_api）

**Files:**
- Modify: `tests/test_stream_api.py`（mock `handler.chat_stream` 产出结构化，断言 HTTP SSE 转发）

- [ ] **Step 1: 写失败测试**

读 `tests/test_stream_api.py` L45-124 的既有「mock chat_stream + uvicorn 验证转发」模式，加一条：让 mock 的 `chat_stream` yield `{"type":"tool_call","id":"c1","tool":"read_file","arg":"a.md"}` 和 `{"type":"tool_result","id":"c1","tool":"read_file","status":"success","summary":""}`，断言响应 SSE 流里这两个事件被原样转发（按该文件既有的解析断言风格）。

- [ ] **Step 2: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_stream_api.py -q`
Expected: 先 FAIL（若端点对未知事件类型有过滤）→ 确认端点透传逻辑（一般直接 `data: {json}`，应已透传）→ PASS。若端点白名单过滤事件类型，扩展白名单含 `tool_call`/`tool_result`。

- [ ] **Step 3: Commit**

```bash
git add tests/test_stream_api.py
git commit -m "test(tool-pill): assert endpoint forwards structured tool events"
```

---

## Task 3: 前端 toolEvents 纯函数模块

**Files:**
- Create: `frontend/src/utils/toolEvents.js`
- Test: `frontend/tests/toolEvents.test.mjs`

- [ ] **Step 1: 写失败测试**

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reduceToolEvent, toolCallSentinel, parseToolCallSentinelLine, firstArgValue } from '../src/utils/toolEvents.js'

test('reduceToolEvent：call 建 pending，result 按 id 更新且不丢 arg', () => {
  let m = reduceToolEvent({}, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  assert.deepEqual(m.c1, { id: 'c1', tool: 'read_file', arg: 'a.md', status: 'pending', summary: '' })
  m = reduceToolEvent(m, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: 'ok' })
  assert.equal(m.c1.status, 'success'); assert.equal(m.c1.summary, 'ok'); assert.equal(m.c1.arg, 'a.md')
})

test('reduceToolEvent：result 先到也建条目', () => {
  const m = reduceToolEvent({}, { type: 'tool_result', id: 'c9', tool: 'x', status: 'error', summary: 'boom' })
  assert.equal(m.c9.status, 'error')
})

test('sentinel 生成 + 整行解析', () => {
  assert.equal(toolCallSentinel('c1'), '<<tool-call:c1>>')
  assert.equal(parseToolCallSentinelLine('<<tool-call:c1>>'), 'c1')
  assert.equal(parseToolCallSentinelLine('普通文字'), null)
  assert.equal(parseToolCallSentinelLine('前<<tool-call:c1>>后'), null) // 必须整行
})

test('firstArgValue：取首值截断', () => {
  assert.equal(firstArgValue({ file_path: 'a.md' }), 'a.md')
  assert.equal(firstArgValue({}), '')
  assert.equal(firstArgValue({ q: 'x'.repeat(50) }).length, 40)
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node --test tests/toolEvents.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `frontend/src/utils/toolEvents.js`**

```js
// 工具调用事件纯函数：map reducer（主聊天）、sentinel 与首参数值（共享）。无副作用。
export const TOOL_CALL_SENTINEL_RE = /<<tool-call:([^>\n]+)>>/g

export function toolCallSentinel(id) {
  return `<<tool-call:${id}>>`
}

// 整行匹配才算 sentinel（避免误吃正文里的 << >>）。返回 id 或 null。
export function parseToolCallSentinelLine(line = '') {
  const m = /^<<tool-call:([^>\n]+)>>$/.exec(line)
  return m ? m[1] : null
}

export function firstArgValue(args) {
  const d = args && typeof args === 'object' ? args : {}
  const keys = Object.keys(d)
  if (!keys.length) return ''
  const val = String(d[keys[0]])
  return val.length > 40 ? val.slice(0, 37) + '...' : val
}

export function reduceToolEvent(map = {}, event = {}) {
  const id = event.id
  if (!id) return map
  const prev = map[id] || { id, tool: event.tool || '', arg: '', status: 'pending', summary: '' }
  if (event.type === 'tool_call') {
    return { ...map, [id]: { ...prev, tool: event.tool ?? prev.tool, arg: event.arg ?? prev.arg, status: 'pending' } }
  }
  if (event.type === 'tool_result') {
    return { ...map, [id]: { ...prev, tool: event.tool ?? prev.tool, status: event.status || 'error', summary: event.summary ?? '' } }
  }
  return map
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && node --test tests/toolEvents.test.mjs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/toolEvents.js frontend/tests/toolEvents.test.mjs
git commit -m "feat(tool-pill): toolEvents reducer + sentinel + firstArgValue"
```

---

## Task 4: IconTool 图标

**Files:**
- Modify: `frontend/src/components/icons.jsx`（已存在 `IconCheck` L73、`IconClose` L80）

- [ ] **Step 1: 加图标（原型双箭头 code 图标，无 emoji）**

```jsx
export function IconTool({ size = 13, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M9 18l-6-6 6-6M15 6l6 6-6 6" />
    </svg>
  )
}
```

- [ ] **Step 2: 构建确认无语法错误**

Run: `cd frontend && npm run build`
Expected: built 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/icons.jsx
git commit -m "feat(tool-pill): add IconTool icon"
```

---

## Task 5: ToolCallPill 共享组件

**Files:**
- Create: `frontend/src/components/ToolCallPill.jsx`
- Test: `frontend/tests/toolCallPill.source.test.mjs`

- [ ] **Step 1: 写 source-guard 失败测试**

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const src = readFileSync(new URL('../src/components/ToolCallPill.jsx', import.meta.url), 'utf8')

test('ToolCallPill：单行 pill + 工具名/参数 + 按状态切图标，全 token 配色、无 emoji', () => {
  assert.match(src, /inline-flex/)
  assert.match(src, /IconTool/)
  assert.match(src, /IconCheck/)            // 成功用图标（非字符）
  assert.match(src, /IconClose/)            // 失败用图标（非字符）
  assert.match(src, /status\s*===\s*['"]pending['"]/)
  assert.match(src, /font-mono/)
  assert.match(src, /min-w-0/)              // 长参数能 truncate（codex NIT）
  assert.match(src, /event\.tool/)
  assert.match(src, /event\.arg/)
  assert.doesNotMatch(src, /#[0-9a-fA-F]{3,6}\b/)                                   // 无裸 hex
  assert.doesNotMatch(src, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)               // 无 emoji（含 ✓/✗/🔧）
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node --test tests/toolCallPill.source.test.mjs`
Expected: FAIL（组件不存在）

- [ ] **Step 3: 实现 `frontend/src/components/ToolCallPill.jsx`**

> 注意：源码与注释一律用「success / error / pending」英文词，**不出现 ✓/✗/🔧 等字符**（paletteGuard 会命中）。状态用 IconCheck/IconClose/脉冲点表达。

```jsx
import React from 'react'
import { IconTool, IconCheck, IconClose } from './icons'

// 原型单行 pill：tool 图标 + 工具名(mono) + 参数(淡, 可截断) + 右侧状态:
//   success -> IconCheck(绿); error -> IconClose(红); pending -> 脉冲点。
// 全 token 配色，深色自动切。event = {tool, arg, status, summary}
export default function ToolCallPill({ event }) {
  if (!event) return null
  const { tool = '', arg = '', status = 'pending', summary = '' } = event
  return (
    <div className="inline-flex items-center gap-[9px] border border-border rounded-ibtn bg-card2 px-[11px] py-[7px] font-mono max-w-full min-w-0">
      <IconTool size={13} className="text-abright flex-shrink-0" />
      <span className="text-xs text-text whitespace-nowrap flex-shrink-0">{tool}</span>
      {arg && <span className="text-11 text-t3 truncate min-w-0">{arg}</span>}
      {status === 'success' && <IconCheck size={13} className="text-success flex-shrink-0 ml-auto" />}
      {status === 'error' && <IconClose size={13} className="text-error flex-shrink-0 ml-auto" />}
      {status === 'pending' && (
        <span className="ml-auto w-[7px] h-[7px] rounded-full bg-t3 animate-pulse flex-shrink-0" aria-label="进行中" />
      )}
      {status === 'error' && summary && <span className="sr-only">{summary}</span>}
    </div>
  )
}
```

- [ ] **Step 4: 跑测试 + 构建**

Run: `cd frontend && node --test tests/toolCallPill.source.test.mjs && npm run build`
Expected: PASS、built 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ToolCallPill.jsx frontend/tests/toolCallPill.source.test.mjs
git commit -m "feat(tool-pill): shared ToolCallPill component"
```

---

## Task 6: ChatPanel 接入（保留 legacy 诊断分支）

**Files:**
- Modify: `frontend/src/utils/chatPresentation.js`（`splitAssistantMessageBlocks` L122-159；`getCopyableAssistantMessageText` L69-71）
- Modify: `frontend/src/components/ChatPanel.jsx`（SSE 路由 L533-543；渲染 L966-970）
- Test: `frontend/tests/chatPresentation.test.mjs`、`frontend/tests/chatPanelSseRouting.test.mjs`

- [ ] **Step 1: 写失败测试（split 认 sentinel；移除旧 emoji-行；复制 strip）**

在 `frontend/tests/chatPresentation.test.mjs`：

```js
test('splitAssistantMessageBlocks 把整行 <<tool-call:ID>> 解析为 tool block（带 id，保持行内顺序）', () => {
  const blocks = splitAssistantMessageBlocks('前文\n<<tool-call:c1>>\n后文')
  assert.deepEqual(blocks.map(b => b.type), ['text', 'tool', 'text'])
  assert.equal(blocks[1].id, 'c1')
})

test('splitAssistantMessageBlocks 不再把 emoji 行特殊处理（当普通文本）', () => {
  const blocks = splitAssistantMessageBlocks('🔧 调用工具: x\n✅ 结果: y')
  assert.ok(blocks.every(b => b.type !== 'tool'))
})

test('getCopyableAssistantMessageText 去掉 sentinel', () => {
  const out = getCopyableAssistantMessageText('正文\n<<tool-call:c1>>\n更多')
  assert.doesNotMatch(out, /<<tool-call:/)
  assert.match(out, /正文/); assert.match(out, /更多/)
})
```

删除该文件中断言 emoji 行被识别成 tool block 的旧用例（L77-108 附近）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node --test tests/chatPresentation.test.mjs`
Expected: FAIL

- [ ] **Step 3: 改 `chatPresentation.js`**

(a) 顶部 `import { parseToolCallSentinelLine } from './toolEvents.js'`。

(b) `splitAssistantMessageBlocks` 的 `appendNonThinkingSegment`：把 emoji 行识别整段换成 sentinel 识别：
```js
const appendNonThinkingSegment = (segment = "") => {
  const lines = segment.split("\n");
  for (const line of lines) {
    const toolId = parseToolCallSentinelLine(line);
    if (toolId) {
      flushTextBuffer();
      blocks.push({ type: "tool", id: toolId });
      continue;
    }
    textBuffer.push(line);
  }
};
```

(c) `getCopyableAssistantMessageText`（L69-71）：
```js
export function getCopyableAssistantMessageText(content = "") {
  const noSentinels = (content || "").replace(/<<tool-call:[^>\n]+>>/g, "");
  return stripThinkingBlocks(stripToolLogComments(noSentinels));
}
```

- [ ] **Step 4: 改 `ChatPanel.jsx` SSE 路由（L533-543：保留 legacy + 新增）**

把原 `else if (parsed.type === 'tool') {...}` 分支**保留不动**（诊断文本仍走它，append 到 content 显示为文本），在其后**新增**：
```jsx
} else if (parsed.type === 'tool_call') {
  if (shouldFlushStreamingQueueImmediately('tool')) {
    flushStreamingQueueImmediately(assistantId, requestProjectId)
  }
  setMessages(prev => prev.map(m =>
    m.id === assistantId
      ? { ...m, content: appendToolEventContent(m.content, toolCallSentinel(parsed.id)),
          toolEvents: reduceToolEvent(m.toolEvents, parsed) }
      : m))
} else if (parsed.type === 'tool_result') {
  setMessages(prev => prev.map(m =>
    m.id === assistantId ? { ...m, toolEvents: reduceToolEvent(m.toolEvents, parsed) } : m))
}
```
顶部 import：`import { reduceToolEvent, toolCallSentinel } from '../utils/toolEvents'`。

- [ ] **Step 5: 改 `ChatPanel.jsx` 渲染（L966-970）**

```jsx
{assistantBlocks.map((block, index) => block.type === 'tool' ? (
  <ToolCallPill key={index} event={msg.toolEvents?.[block.id]} />
) : block.type === 'thinking' ? (
```
顶部 import `import ToolCallPill from './ToolCallPill'`，删除旧内联 pill div（写死 IconCheck 的那段）。

- [ ] **Step 6: 路由 source-guard（chatPanelSseRouting.test.mjs）**

补断言：源码同时含 legacy `parsed.type === 'tool'` 与新增 `parsed.type === 'tool_call'` / `'tool_result'`，且渲染用 `ToolCallPill`。

- [ ] **Step 7: 跑测试 + 构建**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: 全 PASS、built 成功

- [ ] **Step 8: Commit**

```bash
git add frontend/src/utils/chatPresentation.js frontend/src/components/ChatPanel.jsx frontend/tests/chatPresentation.test.mjs frontend/tests/chatPanelSseRouting.test.mjs
git commit -m "feat(tool-pill): render pills in main chat, keep legacy diagnostics text"
```

---

## Task 7: 独立审查路径（后端发 id + 前端按 id 配对 + ToolCallPill）

**Files:**
- Modify: `backend/independent_review.py`（~606-611/643-653：tool_call/tool_result 事件加 `id`）
- Modify: `tests/test_independent_review.py`
- Modify: `frontend/src/utils/independentReviewDrawer.js`（聚合 L34-67）
- Modify: `frontend/src/components/IndependentReviewDrawer.jsx`（渲染 L287-300）
- Test: `frontend/tests/reviewChatWindow.test.mjs`

- [ ] **Step 1: 后端审查 SSE 带 id**

读 `backend/independent_review.py` ~643（tool_call 发射）与 ~606-611/648-653（tool_result）：该处 `tc` 已有 `tc.get("id")`。在两个事件字典里加 `"id": tc.get("id") or f"rev-{index}"`（确保 call/result 同 id）。同时给 tool_call 加 `"arg"`（用现有 args 取首值，或前端 `firstArgValue` 等价逻辑），tool_result 保持 `status`/`summary`。补/改 `tests/test_independent_review.py` 断言事件带 `id` 且 call/result 同 id。

- [ ] **Step 2: 前端按 id 配对（失败测试）**

`frontend/tests/reviewChatWindow.test.mjs`：
```js
test('审查流 tool_call + tool_result 按 id 配对成单个 tool bubble', () => {
  let list = aggregateContentDelta([], { type: 'tool_call', id: 'r1', tool: 'read_file', arg: 'a.md' })
  list = aggregateContentDelta(list, { type: 'tool_result', id: 'r1', tool: 'read_file', status: 'success', summary: 'ok' })
  const tools = list.filter(b => b.kind === 'tool')
  assert.equal(tools.length, 1)
  assert.equal(tools[0].status, 'success')
  assert.equal(tools[0].id, 'r1')
})
```

- [ ] **Step 3: 改聚合器 `independentReviewDrawer.js`**

tool_call 追加 `{kind:'tool', id, tool, arg, status:'pending', summary:''}`；tool_result 按 **id** 找该 bubble 原地更新（找不到则新建已完成 bubble）：
```js
if (event.type === "tool_call") {
  return [...list, { kind: "tool", id: event.id, tool: event.tool || "", arg: event.arg || "", status: "pending", summary: "" }];
}
if (event.type === "tool_result") {
  const idx = list.findIndex(b => b.kind === "tool" && b.id === event.id && b.status === "pending");
  if (idx === -1) return [...list, { kind: "tool", id: event.id, tool: event.tool || "", arg: "", status: event.status || "error", summary: event.summary || "" }];
  const next = list.slice();
  next[idx] = { ...next[idx], status: event.status || "error", summary: event.summary || "" };
  return next;
}
```

- [ ] **Step 4: 改渲染 `IndependentReviewDrawer.jsx`（L287-300）**

```jsx
if (bubble.kind === 'tool') {
  return <ToolCallPill key={i} event={bubble} />
}
```
删除原 `tool_call`/`tool_result` 两条 `ToolCard` 分支，import `ToolCallPill`。跑 `grep -rn "ToolCard" frontend/src`，若 `ToolCard`（MarkdownMessage.jsx:83）再无引用则删它 + 清 import。

- [ ] **Step 5: 跑测试 + 构建**

Run: `.venv/bin/python -m pytest tests/test_independent_review.py -q && cd frontend && node --test tests/ && npm run build`
Expected: 全 PASS、built 成功

- [ ] **Step 6: Commit**

```bash
git add backend/independent_review.py tests/test_independent_review.py frontend/src/utils/independentReviewDrawer.js frontend/src/components/IndependentReviewDrawer.jsx frontend/src/components/MarkdownMessage.jsx frontend/tests/reviewChatWindow.test.mjs
git commit -m "feat(tool-pill): unify independent-review tools on id-paired ToolCallPill"
```

---

## Task 8: 收口验证（仅针对正常工具去 emoji）

- [ ] **Step 1: 确认正常 call/result 的 emoji 已清，诊断 emoji 保留**

Run:
```bash
grep -n "🔧 调用工具\|🔧 准备调用工具\|结果: {" backend/chat.py    # 应为空（正常 call/result 已结构化）
grep -rn "🔧 调用工具:\|✅ 结果:\|⚠️ 结果:" frontend/src | grep -iv test  # 应为空（split 不再认 emoji 行）
```
Expected: 两条均为空。诊断类 `⚠️`/`type:"tool"` 文本事件**仍在**（预期，不动）。

- [ ] **Step 2: 后端全量**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（已知 2 个 mac realpath 用例失败属环境差异，Windows 全绿）

- [ ] **Step 3: 前端全量 + 构建**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: 全 PASS、built 成功

- [ ] **Step 4: 真模型 GUI 自测（mac web 模式）**

`.venv/bin/python run_web.py`（需 `CRA_INVITE_CODE`）。发会触发工具的消息（`read_file` 某材料 / `web_search`），目视：正常工具单行 pill（图标+工具名+参数+成功/失败图标+进行中脉冲）、复制不含 sentinel/emoji；故意触发诊断（如让模型并行多调）确认诊断文本仍显示；独立审查窗口工具同样 pill 化。

- [ ] **Step 5: Commit（如有收口改动）**

```bash
git add -A && git commit -m "chore(tool-pill): scope-de-emoji normal tool calls + full verification"
```

---

## Self-Review（计划自检）

**Spec 覆盖：** 单行 pill→Task 5；call↔result 按 id 配对→Task 1/3（主聊天）+ Task 7（审查）；复制 strip→Task 6 Step 3；正常工具去 emoji→Task 1 + Task 8（**诊断 emoji 保留**）；主聊天+审查统一 ToolCallPill→Task 6/7；DeepSeek 兼容→Task 1 只改 SSE payload + 跑 test_chat_runtime；持久化不破→不碰 _format_tool_pair_line + test_tool_log 守卫。**reload 存活=非目标**（当前即不显示，无回归）。

**类型一致性：** `ToolEvent {id,tool,arg,status,summary}` 在后端两处 SSE、`reduceToolEvent`、审查聚合 bubble、`ToolCallPill` 一致；sentinel 解析统一走 `parseToolCallSentinelLine`（split/copy/test 同一实现）。

**Placeholder 扫描：** 无 TBD；每个 code step 给实际代码。`test_chat_runtime.py`/`test_stream_api.py`/`test_independent_review.py` 的具体构造以各文件真实既有模式为准内联（已在步骤中注明参照行号）。

---

## 风险 / 实施注意（含 codex 红队结论）

1. **不删 legacy `type:"tool"` 分支**（BLOCKER①）：chat.py ~2963/2973/2992/3020/3095/3108/3126 的诊断事件仍发文本 `type:"tool"`，ChatPanel 必须保留该分支，否则禁工具/畸形/漏写重试等提示消失。
2. **不重构 `_format_tool_pair_line`**（BLOCKER②）：SSE 用独立 helper；持久化串由 `test_tool_log.py` 守、本计划零改动。
3. **结构化事件测试放 test_chat_runtime.py，不放 test_stream_api.py**（BLOCKER③）：后者只 mock chat_stream 测 HTTP 转发，无 fake provider。
4. **更新 test_chat_runtime.py 旧 emoji 断言**（BLOCKER④，~733/2562/7287）：正常工具改结构化断言，保留诊断断言。
5. **去 emoji 只针对正常 call/result**（BLOCKER⑤）：Task 8 grep 用精确模式（`🔧 调用工具`/`✅ 结果:`/`⚠️ 结果:`），不碰诊断 `⚠️`。
6. **组件源码/注释零 emoji 字符**（BLOCKER⑥）：用 IconCheck/IconClose + 英文词，不写 ✓/✗。
7. **审查侧带 id 配对**（NIT②）：后端 `tc.get("id")` 已有，发出来即可按 id 配对，免「同名启发式」的多调用/续审/异常乱序坑。
8. **sentinel 整行匹配 + 单一解析器**（NIT③）：`parseToolCallSentinelLine` 要求整行，`appendToolEventContent` 保证 sentinel 独占一行；与 thinking-block 解析互不干扰（thinking 先于行扫描切走）。
9. **min-w-0**（NIT⑤）：pill 容器与参数 span 加 `min-w-0` 否则 flex 下 `truncate` 不收缩。
10. DeepSeek 边界（NIT①）：只改 SSE `yield`，不碰 `_to_provider_message`/`_normalize_collected_assistant_tool_call_message`/`_serialize_assistant_tool_call_message`/tool role/`reasoning_content`/`tool_choice`；Task 1 Step 5 跑 test_chat_runtime 当守卫。

---

## 后续（follow-up，非本计划）
- **reload 存活**：改 `_append_tool_log_to_assistant` 持久化结构化 JSON + reload 解析成 toolEvents 渲染 pill（双格式兼容旧会话）。记 `docs/current-worklist.md`。
- 诊断类 `type:"tool"` 文本提示是否也升级成结构化通知（与 `system_notice` 合流）——独立评估。
- 成功摘要（web_search「3 results」等）是否在 pill 上显示，按评审口味定。
