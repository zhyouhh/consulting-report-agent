# 工具调用按时间线穿插（有序 parts · per-event 镜像）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 assistant 一轮里的工具调用与文本叙述从「工具全堆顶、文本全堆底」改成**按时间顺序穿插**（文本→工具→文本→工具…）。live 正确穿插（含 thinking/诊断）；reload 也穿插（thinking 为 live-only、不持久化 = 现状）；并顺带修复既有「reload 丢工具前中间叙述」。

**Architecture（per-event 镜像，Codex plan 审 R1+R2+R3 收敛后定稿）:**

把 assistant 消息建模成**有序片段** `parts = [{type:"text",text} | {type:"tool",id,tool,arg,status,summary}]`，**工具调用切分文本段**。关键决策：

- **不动 `msg.content` 装配**（content append / thinking 末块改写 / 诊断 append / error 替换 全部原样、所有现有测试不破）。在每个写 `msg.content` 的 SSE handler **旁边并行**按**正确的 per-event 算子**建 `msg.parts`：
  - content delta → 追加进**当前文本段**（parts 末尾若是 text 段则续、否则新建）。
  - thinking delta → 用**现有 helper**（`appendThinkingEventContent`）并进当前文本段的尾部思考块（**不是无脑 append**——R2 教训）。
  - 诊断 `type:"tool"` → 用现有诊断算子并进当前文本段。
  - `tool_call` → **切段**：push 工具片段（按 id，reduceToolEvent 同语义），当前文本段封口；下一段文本从新 text 段起。
  - `tool_result` → 按 id 更新工具片段。
  - in-stream error / network → 追加一个报错文本段 + `closePendingToolParts`（pending 工具→error）。
- **渲染**：每个 text 片段走抽出的 `renderAssistantText(text)`（= 现有 `stripToolLogComments` → `splitAssistantMessageBlocks` → 逐 `block.content` 渲染，含 ThinkingBlock + `<MarkdownMessage>{block.content}</MarkdownMessage>` **children** 用法）；工具片段走 `ToolCallPill`。**thinking/诊断/stage-ack/GFM 表格全部白捡保住**（它们在 text 片段里，由现有渲染还原）。
- **reload**：后端 `_build_message_parts` 从 `current_turn_messages`（每轮 content + 该轮 tool_calls 顺序）+ 末轮 `visible_content` 构建同形状 parts（text 段=每轮 content，无 thinking），持久化、端点返回 → 同一 `MessageParts` 渲染。
- **复制**：`partsToText(parts)`（拼 text 段）再套**既有** `getCopyableAssistantMessageText`/`stripToolLogComments`（去 thinking 标记 + tool-log）。
- 持久化 `content`（末轮）、provider 历史、压缩摘要、流式补尾切片**一律不动**（parts 独立于 content）。

**为什么不偏移量方案（R3 否决）**：content 非单调可变（thinking 改写末块、error 替换），char 偏移不稳——thinking 一变长前面记的工具偏移就错位、pill 插进句中。per-event 镜像把工具当**切段点**，不依赖偏移、天然稳定。
**为什么不无脑「凡进 content 都 append」（R2 否决）**：thinking 是改写不是 append。本方案对每种事件用**对应算子**（content append / thinking merge / error append）。
**为什么不统一 content 成全轮（R1 警示）**：会连带改 `result_content[already_emitted_len:]` 补尾切片（高风险）。parts 独立、不触发补尾。

**Tech Stack:** FastAPI（`backend/chat.py` / `backend/main.py`）、React + Tailwind 语义 token、Node `node:test`、pytest/unittest。

---

## 关键设计边界（实施期必须守）

- **`msg.content` 装配零改动**：content/thinking/诊断/error 写 `msg.content` 的现有分支全部保留（现有 source-guard/行为测试不破）。本计划在每个分支**旁边新增** parts 算子调用。
- **per-event 算子正确**：thinking 用 `appendThinkingEventContent` 并入当前段（非 append）；error 追加报错段（非替换全部）。绝不一条 blanket append（R2）。
- **parts 只服务前端渲染+复制**；`content` 只服务 provider+compaction（现状）。parts **绝不进 provider**。
- **renderAssistantText 必须含 strip + 用 `block.content`**：`splitAssistantMessageBlocks` 输出 `{type, content}`（**不是 `.text`**，R3 B4）、且本身不 strip tool-log（R3 B1）——抽出时保留现状 `stripToolLogComments` 前置 + 用 `block.content`。
- **复制经既有 strip**：`partsToText` 仍含 thinking 标记 + tool-log → 复制套 `getCopyableAssistantMessageText`/`stripToolLogComments`。
- **DeepSeek 官渠兼容**：parts/tool_events 不进 provider message；不碰 `tool_choice`/`reasoning_content`/tool-call 序列化、不碰 `persisted_content`/`already_emitted_len`/`remainder`。
- **信任边界**：parts 的 text/arg/summary 纯文本渲染（text 走 renderAssistantText 安全路径、tool 走 ToolCallPill `{value}`），绝不 `dangerouslySetInnerHTML`。
- **老对话无回归**：改动前已存 assistant 无 `parts` → 前端 fallback 现状（`ToolCallList` 分组在上 + `renderAssistantText(content)`）、复制走 content。
- **tool_events / reduceToolEvent / closePendingToolEvents 保留**：parts 的工具算子与它们同语义（可内部复用或并行）；不删既有字段（老消息 fallback 用）。
- **独立审查不改**：已按 bubble 时间线穿插，零改动其文件。

## 统一数据形状

`Part`：`{ "type": "text", "text": str }` 或 `{ "type": "tool", "id", "tool", "arg", "status", "summary" }`。`parts` 有序。工具片段 `id` = 真实 `tool_call_id`（live 真 id / reload 后端真 id）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/chat.py` | `_build_message_parts`；`_finalize_assistant_turn` 新增持久化 `parts`（content/补尾/tool_events 不动）；`_load_conversation` 保留+净化 `parts`；`_sanitize_message_for_summary` pop `parts`；`_SYNTHETIC_BARRIER_NOTES` 常量集 | Modify |
| `backend/main.py` | `GET /conversation` 新增返回净化 `parts`（pop + 仅 list + pending→终态）；content/tool_events 不动 | Modify |
| `tests/test_chat_runtime.py` | `_build_message_parts`（穿插/留候选叙述/跳两隔板/末轮去重）+ finalize 持久化 + load 保留/终态 + compaction pop；既有不回归 | Modify |
| `tests/test_main_api.py` | `GET /conversation` parts（有→净化 / 老→缺省 / 非 list→缺省） | Modify |
| `frontend/src/utils/messageParts.js` | 纯函数：`appendTextToCurrentPart` / `mutateCurrentTextPart` / `applyToolEventToParts` / `closePendingToolParts` / `appendErrorPart` / `partsToText` | Create |
| `frontend/tests/messageParts.test.mjs` | 上述纯函数单测 | Create |
| `frontend/src/components/assistantTextRender.jsx` | 从 ChatPanel 抽出 `renderAssistantText(text)`（strip→split→逐 `block.content`），供 MessageParts + 老 fallback 共用 | Create |
| `frontend/src/components/MessageParts.jsx` | 按序渲染 parts（text→renderAssistantText、tool→ToolCallPill） | Create |
| `frontend/src/components/ChatPanel.jsx` | 每个写 content 的 handler 旁并行建 `msg.parts`；reload 映射 `parts`；渲染 `parts ? MessageParts : 旧分组`；复制走 parts+strip；正文/ fallback 改调 `renderAssistantText` | Modify |
| `frontend/tests/chatPanelParts.source.test.mjs` | 接线 source-guard | Create |

**禁改**：`backend/chat.py` 的 `persisted_content`/`result_content`/`already_emitted_len`/`remainder`/`_to_provider_message`/`_append_tool_log_to_assistant`/`_build_tool_events`/`tool_choice`/`reasoning_content`；前端 content 装配现有分支（只在旁新增 parts）/`toolEvents.js`/`ToolCallList.jsx`/`ToolCallPill.jsx`/`chatPresentation.js`/`independentReviewDrawer.js`；`backend/independent_review.py`。

---

## Task 1: 后端 `_build_message_parts` + 合成隔板常量集

**Files:** Modify `backend/chat.py`；Test `tests/test_chat_runtime.py`

- [ ] **Step 1: 写失败测试**
```python
def test_build_message_parts_interleaves_text_and_tools(self):
    handler = self._handler()
    turn = [
        {"role": "assistant", "content": "先读文件再改。",
         "tool_calls": [
             {"id": "c1", "function": {"name": "read_file", "arguments": '{"file_path": "a.md"}'}},
             {"id": "c2", "function": {"name": "edit_file", "arguments": '{"file_path": "a.md"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"status": "success"})},
        {"role": "tool", "tool_call_id": "c2", "content": json.dumps({"status": "error", "message": "锚点未找到"})},
        {"role": "assistant", "content": "再搜索一下规范。",
         "tool_calls": [{"id": "c3", "function": {"name": "web_search", "arguments": '{"query": "技术标 规范"}'}}]},
        {"role": "tool", "tool_call_id": "c3", "content": json.dumps({"status": "success", "results": [1, 2]})},
    ]
    parts, full_text = handler._build_message_parts(turn, "好，框架搭好了。")
    self.assertEqual(parts, [
        {"type": "text", "text": "先读文件再改。"},
        {"type": "tool", "id": "c1", "tool": "read_file", "arg": "a.md", "status": "success", "summary": ""},
        {"type": "tool", "id": "c2", "tool": "edit_file", "arg": "a.md", "status": "error", "summary": "锚点未找到"},
        {"type": "text", "text": "再搜索一下规范。"},
        {"type": "tool", "id": "c3", "tool": "web_search", "arg": "技术标 规范", "status": "success", "summary": "2 results"},
        {"type": "text", "text": "好，框架搭好了。"}])
    self.assertEqual(full_text, "先读文件再改。再搜索一下规范。好，框架搭好了。")

def test_build_message_parts_keeps_non_barrier_text_without_tool_calls(self):
    handler = self._handler()
    parts, _ = handler._build_message_parts([{"role": "assistant", "content": "我先试试这个方案。"}], "完成。")
    self.assertEqual(parts, [{"type": "text", "text": "我先试试这个方案。"}, {"type": "text", "text": "完成。"}])

def test_build_message_parts_skips_known_synthetic_barriers(self):
    handler = self._handler()
    for note in ("（上条工具调用被上游合并成畸形条目，已作废本轮调用。）", "（本轮为纯转述，不调用任何工具。）"):
        parts, _ = handler._build_message_parts(
            [{"role": "assistant", "content": note}, {"role": "user", "content": "x"}], "重试成功。")
        self.assertEqual(parts, [{"type": "text", "text": "重试成功。"}])

def test_build_message_parts_dedups_trailing(self):
    handler = self._handler()
    parts, _ = handler._build_message_parts([{"role": "assistant", "content": "完成。"}], "完成。")
    self.assertEqual(parts, [{"type": "text", "text": "完成。"}])
```

- [ ] **Step 2: 跑测试确认失败** `.venv/bin/python -m pytest tests/test_chat_runtime.py -k build_message_parts -q` → FAIL

- [ ] **Step 3: 实现（chat.py，`_build_tool_events` 附近）**

先 grep 两处合成隔板注入逐字抄常量（malformed barrier ~L3105/3107、system-trigger no-tools ~L3052）：
```python
_SYNTHETIC_BARRIER_NOTES = frozenset({
    "（上条工具调用被上游合并成畸形条目，已作废本轮调用。）",  # ← 对齐 chat.py 注入处
    "（本轮为纯转述，不调用任何工具。）",                      # ← 对齐 chat.py 注入处
})
```
（加守护测试：断言这两个字符串确实出现在 chat.py 注入分支源码里，防漂移。）
```python
def _build_message_parts(self, current_turn_messages, assistant_message):
    """有序展示片段（文本/工具按时间穿插）+ 全轮可见文本（仅供前端复制）。
    文本来源：每个 assistant 子消息 content（含 retry 候选叙述），跳已知合成隔板；末轮 visible_content 去重。
    工具按 tool_call_id 配对。持久化 content/provider 不受影响。"""
    results_by_id = {}
    for msg in current_turn_messages or []:
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id")
        if not tc_id:
            continue
        try:
            result = json.loads(msg.get("content") or "{}")
            if not isinstance(result, dict):
                result = {"status": "error", "raw": str(result)}
        except json.JSONDecodeError:
            result = {"status": "error", "raw": msg.get("content")}
        results_by_id[tc_id] = result
    parts, text_segments = [], []
    for msg in current_turn_messages or []:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if (isinstance(content, str) and content.strip()
                and content not in _SYNTHETIC_BARRIER_NOTES):
            parts.append({"type": "text", "text": content}); text_segments.append(content)
        for tc in (msg.get("tool_calls") or []):
            tc_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            name, args = fn.get("name") or "", fn.get("arguments") or ""
            result = results_by_id.get(tc_id)
            if isinstance(result, dict):
                status = "success" if result.get("status") == "success" else "error"
                summary = self._sse_tool_summary(name, result)
            else:
                status, summary = "error", ""
            parts.append({"type": "tool", "id": tc_id, "tool": name,
                          "arg": self._sse_tool_arg(name, args), "status": status, "summary": summary})
    if isinstance(assistant_message, str) and assistant_message.strip():
        if not (text_segments and text_segments[-1] == assistant_message):
            parts.append({"type": "text", "text": assistant_message}); text_segments.append(assistant_message)
    return parts, "".join(text_segments)
```

- [ ] **Step 4: 跑测试** → PASS / **Step 5: Commit** `git commit -am "feat(tool-parts): _build_message_parts builder + barrier skip set"`

---

## Task 2: 后端持久化 parts sibling（content/补尾/tool_events 不动）

**Files:** Modify `backend/chat.py`（`_finalize_assistant_turn` / `_load_conversation` / `_sanitize_message_for_summary`）；Test `tests/test_chat_runtime.py`

- [ ] **Step 1: 写失败测试**
```python
def test_finalize_persists_parts_content_unchanged(self):
    handler = self._handler(); history = []
    turn = [{"role": "assistant", "content": "读一下。",
             "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": '{"file_path": "a.md"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"status": "success"})}]
    handler._finalize_assistant_turn("proj", history, {"role": "user", "content": "go"}, "完成。", turn)
    a = history[-1]
    self.assertIn("完成。", a["content"]); self.assertIn("tool_events", a)
    self.assertEqual(a["parts"], [
        {"type": "text", "text": "读一下。"},
        {"type": "tool", "id": "c1", "tool": "read_file", "arg": "a.md", "status": "success", "summary": ""},
        {"type": "text", "text": "完成。"}])

def test_load_preserves_and_terminalizes_parts(self):
    handler = self._handler()
    raw = [{"role": "assistant", "content": "完成。", "parts": [
        {"type": "text", "text": "读一下。"},
        {"type": "tool", "id": "c1", "tool": "read_file", "arg": "a.md", "status": "pending", "summary": ""}]}]
    loaded = handler._roundtrip_through_load(raw)  # ← read 既有 _load_conversation 测试真实写盘+load 方式替换
    self.assertEqual(loaded[-1]["parts"][0], {"type": "text", "text": "读一下。"})
    self.assertNotEqual(loaded[-1]["parts"][1]["status"], "pending")

def test_summarize_drops_parts(self):
    handler = self._handler()
    self.assertNotIn("parts", handler._sanitize_message_for_summary(
        {"role": "assistant", "content": "x", "parts": [{"type": "text", "text": "y"}]}))
```

- [ ] **Step 2: 跑测试确认失败** → FAIL

- [ ] **Step 3: `_finalize_assistant_turn` 新增 parts** read 真实持久化块；`persisted_content`/`tool_events` 行不动，新增：
```python
        parts, _full = self._build_message_parts(current_turn_messages, visible_content)
        assistant_msg = {"role": "assistant", "content": persisted_content}
        if tool_events:
            assistant_msg["tool_events"] = tool_events
        if parts:
            assistant_msg["parts"] = parts
```
（末轮传 `visible_content` 不带 tool-log。`_finalize_empty_assistant_turn` 不产 parts。）

- [ ] **Step 4: `_load_conversation` 保留+净化 parts** read 既有保留 `tool_events` 块，其后新增 `entry["parts"] = [q for q in (self._sanitize_part_scalar(p) for p in raw_parts) if q]`（`raw_parts` 仅 list 时）。加 helper：
```python
    @staticmethod
    def _sanitize_part_scalar(p):
        if not isinstance(p, dict): return None
        if p.get("type") == "text":
            t = p.get("text"); return {"type": "text", "text": t} if isinstance(t, str) and t else None
        if p.get("type") == "tool":
            tool = str(p.get("tool") or "")
            if not tool: return None
            st = p.get("status")
            return {"type": "tool", "id": str(p.get("id") or ""), "tool": tool, "arg": str(p.get("arg") or ""),
                    "status": st if st in ("success", "error") else "success", "summary": str(p.get("summary") or "")}
        return None
```

- [ ] **Step 5: `_sanitize_message_for_summary` pop parts** 加 `sanitized.pop("parts", None)`。

- [ ] **Step 6: 跑测试 + 既有不回归** `.venv/bin/python -m pytest tests/test_chat_runtime.py -q` → PASS

- [ ] **Step 7: Commit** `git commit -am "feat(tool-parts): persist+load+summary-pop parts (content/provider untouched)"`

---

## Task 3: `GET /conversation` 返回 parts（pop + 仅 list + 非 list 锁测）

**Files:** Modify `backend/main.py`；Test `tests/test_main_api.py`

- [ ] **Step 1: 写失败测试** persist assistant 含 `parts` → 断言返回含净化 parts（带 text/tool）。老消息无 parts → 无 parts 字段。`parts` 为字符串（非 list）→ 无 parts 字段。
- [ ] **Step 2: 跑测试确认失败** → FAIL
- [ ] **Step 3: 实现**（现有 tool_events 构造之后）
```python
            raw_parts = m.get("parts") if isinstance(m.get("parts"), list) else None
            cleaned_parts = None
            if raw_parts is not None:
                cleaned_parts = []
                for p in raw_parts:
                    if not isinstance(p, dict): continue
                    if p.get("type") == "text":
                        t = p.get("text")
                        if isinstance(t, str) and t: cleaned_parts.append({"type": "text", "text": t})
                    elif p.get("type") == "tool" and p.get("tool"):
                        st = p.get("status")
                        cleaned_parts.append({"type": "tool", "id": str(p.get("id") or ""),
                            "tool": str(p.get("tool") or ""), "arg": str(p.get("arg") or ""),
                            "status": st if st in ("success", "error") else "success",
                            "summary": str(p.get("summary") or "")})
            entry = {**m, "content": cleaned, "tool_events": tool_events}
            entry.pop("parts", None)
            if cleaned_parts is not None:
                entry["parts"] = cleaned_parts
            sanitized.append(entry)
```
- [ ] **Step 4: 跑测试** → PASS / **Step 5: Commit** `git commit -am "feat(tool-parts): GET /conversation returns sanitized parts"`

---

## Task 4: 前端 `messageParts.js`（per-event 算子 + partsToText）

**Files:** Create `frontend/src/utils/messageParts.js`、`frontend/tests/messageParts.test.mjs`

> 先 read `frontend/src/utils/chatPresentation.js` 里 thinking 并块 helper 真实名/签名（计划记作 `appendThinkingEventContent(content, delta)→content`）。若名/签名不同，按真实的来。

- [ ] **Step 1: 写失败测试**
```js
import { test } from 'node:test'; import assert from 'node:assert/strict'
import { mutateCurrentTextPart, applyToolEventToParts, closePendingToolParts, appendErrorPart, partsToText } from '../src/utils/messageParts.js'

test('mutateCurrentTextPart：末尾是 text 续接，否则新建', () => {
  let p = mutateCurrentTextPart([], t => t + '你好')
  assert.deepEqual(p, [{ type: 'text', text: '你好' }])
  p = mutateCurrentTextPart(p, t => t + '世界')
  assert.deepEqual(p, [{ type: 'text', text: '你好世界' }])
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  p = mutateCurrentTextPart(p, t => t + '改完')   // 末尾是 tool → 新 text 段
  assert.deepEqual(p.map(x => x.type), ['text', 'tool', 'text'])
  assert.equal(p[2].text, '改完')
})

test('applyToolEventToParts：早发 pending→同 id full arg 原地更新、不产两个；result 按 id 收尾', () => {
  let p = mutateCurrentTextPart([], () => '准备')
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: '' })
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  p = applyToolEventToParts(p, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: '' })
  const tools = p.filter(x => x.type === 'tool')
  assert.equal(tools.length, 1)
  assert.deepEqual(tools[0], { type: 'tool', id: 'c1', tool: 'read_file', arg: 'a.md', status: 'success', summary: '' })
})

test('applyToolEventToParts：result 先到也建 tool 片段', () => {
  assert.equal(applyToolEventToParts([], { type: 'tool_result', id: 'z', tool: 'x', status: 'error', summary: 'e' })[0].status, 'error')
})

test('closePendingToolParts：pending→error，文本/已终态不动、不可变', () => {
  const p = [{ type: 'text', text: 'a' }, { type: 'tool', id: 'c1', tool: 't', arg: '', status: 'pending', summary: '' },
             { type: 'tool', id: 'c2', tool: 't2', arg: '', status: 'success', summary: 'ok' }]
  const out = closePendingToolParts(p, '已停止生成')
  assert.equal(out[1].status, 'error'); assert.equal(out[1].summary, '已停止生成')
  assert.equal(out[2].status, 'success'); assert.equal(out[0].text, 'a'); assert.notEqual(out, p)
})

test('appendErrorPart：追加报错文本段', () => {
  const p = appendErrorPart([{ type: 'tool', id: 'c1' }], '连接中断')
  assert.deepEqual(p[p.length - 1], { type: 'text', text: '连接中断' })
})

test('partsToText：拼 text 段', () => {
  assert.equal(partsToText([{ type: 'text', text: '一' }, { type: 'tool', id: 'c1' }, { type: 'text', text: '二' }]), '一二')
  assert.equal(partsToText(null), '')
})
```

- [ ] **Step 2: 跑测试确认失败** → FAIL

- [ ] **Step 3: 实现 `frontend/src/utils/messageParts.js`**
```js
// per-event 片段算子（无副作用、无 emoji）。各事件 handler 调对应算子建 parts，content 装配本身不改。
export function mutateCurrentTextPart(parts = [], fn) {
  const idx = parts.length - 1
  if (idx >= 0 && parts[idx].type === 'text') {
    const c = parts.slice(); c[idx] = { ...c[idx], text: fn(c[idx].text) }; return c
  }
  return [...parts, { type: 'text', text: fn('') }]
}
export function applyToolEventToParts(parts = [], event = {}) {
  const id = event.id
  if (!id) return parts
  const idx = parts.findIndex(p => p.type === 'tool' && p.id === id)
  if (event.type === 'tool_call') {
    if (idx === -1) return [...parts, { type: 'tool', id, tool: event.tool || '', arg: event.arg || '', status: 'pending', summary: '' }]
    const c = parts.slice(); c[idx] = { ...c[idx], tool: event.tool ?? c[idx].tool, arg: event.arg ?? c[idx].arg }; return c
  }
  if (event.type === 'tool_result') {
    if (idx === -1) return [...parts, { type: 'tool', id, tool: event.tool || '', arg: '', status: event.status || 'error', summary: event.summary ?? '' }]
    const c = parts.slice(); c[idx] = { ...c[idx], status: event.status || 'error', summary: event.summary ?? '' }; return c
  }
  return parts
}
export function closePendingToolParts(parts, summary = '') {
  if (!parts || !parts.length) return parts
  return parts.map(p => p.type === 'tool' && p.status === 'pending' ? { ...p, status: 'error', summary: p.summary || summary } : p)
}
export function appendErrorPart(parts = [], text = '') {
  if (!text) return parts
  return [...parts, { type: 'text', text }]
}
export function partsToText(parts) {
  if (!parts || !parts.length) return ''
  return parts.filter(p => p.type === 'text').map(p => p.text).join('')
}
```
（thinking 并块不在此文件实现——handler 用 `mutateCurrentTextPart(parts, t => appendThinkingEventContent(t, delta))` 复用 chatPresentation 的真实 helper。）

- [ ] **Step 4: 跑测试** → PASS / **Step 5: Commit** `git commit -am "feat(tool-parts): frontend per-event parts ops + partsToText"`

---

## Task 5: ChatPanel 旁建 msg.parts + reload + 复制（content 装配不动）

**Files:** Modify `frontend/src/components/ChatPanel.jsx`；Test `frontend/tests/chatPanelParts.source.test.mjs`（Create）+ 迁移 `chatPanelSseRouting.test.mjs`

> **read** ChatPanel 所有写 `msg.content` 的 SSE 分支（content / thinking / 诊断 type:"tool" / in-stream error / network catch / abort）+ tool_call/tool_result 分支。**这些分支的 content 写入保持原样**，每处**旁加** parts 算子。

- [ ] **Step 1: 各 handler 旁建 parts（R4 修正：写在真实点、用真实文案）**

> **R4-BLOCKER1：普通 content 的真实写入点是两个队列 flush 点，不是 `type==='content'` 分支。** read ChatPanel：流式文本经队列缓冲后在 **`flushStreamingQueueImmediately`**（约 L249，写 `content + pending`）和 **timer slice flush**（约 L283，写 `content + emitted`）两处真正落进 `msg.content`。**这两处都必须同步把同一段已 flush 文本 `mutateCurrentTextPart(m.parts||[], t => t + 该段)`**——尤其 `flushStreamingQueueImmediately`（tool_call 前调它把待发文本冲出），漏了它工具就会插到文本前、live 直接错。建议抽一个本地 helper 同时更新 content+parts，两 flush 点都调。

- **content（两 flush 点）**：在 L249 / L283 写 `msg.content` 的同一 `setMessages` 里，旁加 `parts: mutateCurrentTextPart(m.parts||[], t => t + 该 flush 段)`。
- **thinking 分支**：旁加 `parts: mutateCurrentTextPart(m.parts||[], t => appendThinkingEventContent(t, 增量))`（复用 chatPresentation 真实并块 helper、**非 blanket append**）。
- **诊断 `type:"tool"` 分支**：旁加 `parts: mutateCurrentTextPart(m.parts||[], t => <现有诊断写法>(t, ...))`（用现有把诊断写进 content 的同一算子）。
- **`tool_call`/`tool_result` 分支**（现 `reduceToolEvent`→`toolEvents`，**保留**）：旁加 `parts: applyToolEventToParts(m.parts||[], parsed)`。tool_call 时仍先 `flushStreamingQueueImmediately`（此前文本已进当前 text 段）再切段。early-pending 与 full-arg 同 id 经 `applyToolEventToParts` 原地更新、不产两个、不动已封口前段。

> **R4-BLOCKER2：error/network/abort 要 append「真实显示文本」、不是 pending summary。** 拆 `displayText`（进 `appendErrorPart`）与 `pendingSummary`（进 `closePendingToolParts`）：
- **in-stream `error`**：`displayText = \`错误: ${parsed.data}\``（对齐 L604 真实显示）；`pendingSummary = '生成出错'`。
- **network catch**：`displayText = \`API调用失败: ${error.message}\``（对齐 L639）；`pendingSummary = '连接中断'`。
- **abort**：现状是 `content: m.content || '已停止生成'`（**有内容则不追加**，L626）——parts 同样**仅当无可见文本时**才 `appendErrorPart(parts, '已停止生成')`（判 `partsToText(m.parts)` 或 `m.content` 是否为空）；`pendingSummary = '已停止生成'`。
- 三处统一：`parts: closePendingToolParts(appendErrorPart(m.parts||[], displayText 或 ''), pendingSummary)`（displayText 为空时 appendErrorPart 不追加）。**保持原 content 写入不变**，只旁加 parts。

顶部 import 全部算子 + `appendThinkingEventContent` + `partsToText`。

**行为锁测（chatPanelParts.source.test.mjs 或抽纯函数测）**：① pending content flush 后再 tool_call → parts 为 `text → tool`（锁 R4-1 切段正确）；② `thinking → tool_call → thinking` → parts 为 `text(含thinking) → tool → text(含thinking)`（R4-NIT 锁跨工具 thinking 分段）；③ in-stream error → 末段 text 为 `错误: ...`、pending 工具→error。

- [ ] **Step 2: reload 映射** `loadConversation` 映射处加 `parts: m.parts`（老消息无→undefined）。

- [ ] **Step 3: 复制走 parts + 既有 strip** 复制改 `copyMessage(msg.parts?.length ? <既有strip>(partsToText(msg.parts)) : msg.content)`（`<既有strip>` = ChatPanel 现用的 `getCopyableAssistantMessageText`/`stripToolLogComments`，read 确认）。

- [ ] **Step 4: source-guard（chatPanelParts.source.test.mjs）**
```js
import { test } from 'node:test'; import assert from 'node:assert/strict'; import { readFileSync } from 'node:fs'
const src = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')
test('ChatPanel：旁建 parts 各算子 + reload + 复制经 strip', () => {
  assert.match(src, /mutateCurrentTextPart\(m\.parts/)
  assert.match(src, /appendThinkingEventContent/)
  assert.match(src, /applyToolEventToParts\(m\.parts/)
  assert.match(src, /closePendingToolParts\(/)
  assert.match(src, /parts:\s*m\.parts/)
  assert.match(src, /(getCopyableAssistantMessageText|stripToolLogComments)\([^)]*partsToText/)
})
```

- [ ] **Step 5: 迁移既有 source 测试** read `chatPanelSseRouting.test.mjs` 现盯的字面（`toolEvents: reduceToolEvent(...)` 结构、`ThinkingBlock`、`block.content`）。本 task 把它们改成局部变量/抽到 assistantTextRender 后会失真——**就地更新这些断言**到新结构（不是删空），保留其防回归意图。

- [ ] **Step 6: 跑测试 + build** `cd frontend && node --test tests/ && npm run build` → PASS / 绿
- [ ] **Step 7: Commit** `git commit -am "feat(tool-parts): ChatPanel builds msg.parts per-event + reload + parts copy"`

---

## Task 6: 抽 renderAssistantText + MessageParts + 渲染分支

**Files:** Create `assistantTextRender.jsx`、`MessageParts.jsx`；Modify `ChatPanel.jsx`；Test 扩 `chatPanelParts.source.test.mjs` + `assistantTextRender.source.test.mjs`

- [ ] **Step 1: 抽 `renderAssistantText(text)`（B1+B4，R4-NIT：按真实渲染抽）** read ChatPanel 现有正文渲染真实写法：`stripToolLogComments(msg.content)` →（stage-ack strip）→ `splitAssistantMessageBlocks(clean)` → 逐 `block` 渲染——thinking block → ThinkingBlock；text block → **`<ReactMarkdown remarkPlugins={[remarkGfm]} components={assistantMarkdownComponents} ...>{block.content}</ReactMarkdown>`**（约 L1000，**真实是 `ReactMarkdown` 直用、不是 `MarkdownMessage`**；用 `block.content` 不是 `.text`）。把这整段**按真实写法**抽成 `assistantTextRender.jsx` 的 `renderAssistantText(text)`（连同 `remarkGfm`/`assistantMarkdownComponents`/className 一并搬，保等价），**保留 `stripToolLogComments` 前置**（B1）。加 `assistantTextRender.source.test.mjs` 断言含 `stripToolLogComments` + `splitAssistantMessageBlocks` + `block.content` + `ThinkingBlock` + `remarkGfm` + `assistantMarkdownComponents`（锁等价、防换皮丢插件/组件映射）。

- [ ] **Step 2: `MessageParts.jsx`（Create）**
```jsx
import React from 'react'
import ToolCallPill from './ToolCallPill'
import { renderAssistantText } from './assistantTextRender'
export default function MessageParts({ parts }) {
  if (!parts || !parts.length) return null
  return (
    <div className="flex flex-col items-stretch gap-[6px]">
      {parts.map((p, i) => p.type === 'tool'
        ? <div key={p.id || `t${i}`} className="flex"><ToolCallPill event={p} /></div>
        : <div key={`x${i}`}>{renderAssistantText(p.text)}</div>)}
    </div>
  )
}
```

- [ ] **Step 3: ChatPanel 渲染分支** read assistant 正文渲染（Task 7 放了 `<ToolCallList toolEvents={msg.toolEvents}/>` + 文本块）。改成：
```jsx
{msg.parts && msg.parts.length
  ? <MessageParts parts={msg.parts} />
  : (<><ToolCallList toolEvents={msg.toolEvents} />{renderAssistantText(msg.content)}</>)}
```
顶部 import `MessageParts`、`renderAssistantText`。老消息 fallback 文本改调 `renderAssistantText(msg.content)`（与抽取前等价）。

- [ ] **Step 4: 扩 source-guard** 断言 `<MessageParts parts=` + `renderAssistantText(msg.content)`（fallback 经抽出函数）。

- [ ] **Step 5: 跑测试 + build** `cd frontend && node --test tests/ && npm run build` → PASS / 绿（paletteGuard/darkClassGuard 不回归）
- [ ] **Step 6: Commit** `git commit -am "feat(tool-parts): extract renderAssistantText + MessageParts interleaved render"`

---

## Task 7: 收口验证 + 本地实例交付测试

- [ ] **Step 1: 后端全量** `.venv/bin/python -m pytest tests/ -q` → PASS（2 mac realpath 属环境差异）
- [ ] **Step 2: 前端全量 + build** `cd frontend && node --test tests/ && npm run build` → PASS / 绿
- [ ] **Step 3: 禁改区 + DeepSeek** `.venv/bin/python -m pytest tests/test_chat_runtime.py -k "deepseek or compat or tool_followup" -q` → PASS；`git diff <base> HEAD -- backend/chat.py | grep "persisted_content\|already_emitted_len\|remainder\|_to_provider_message\|_append_tool_log"` 确认未改；人工 diff `ChatPanel.jsx` 确认写 `msg.content` 的分支只**旁加** parts、未改原 content 写入。
- [ ] **Step 4: 真模型 GUI 自测（本地 run_web.py，交用户）** 多轮工具写作 → 文本/工具 pill **按时间线穿插**；thinking/诊断正常；刷新 → 穿插 + 中间叙述在（无 thinking 属预期）；复制干净顺序对；中止 → pending 变 error 不卡圈；老对话 → 现状分组、无崩。
- [ ] **Step 5: Commit（如有收口微调）**

---

## Self-Review

**Spec 覆盖：** 穿插→T5(per-event 建 parts)+T6(MessageParts)；reload→T2+T3+T5(映射)+T6；后端有序→T1；复制→T5(partsToText+strip)；thinking/诊断/error 保住→T5(per-event 算子)+T6(renderAssistantText)；中止→T5(closePendingToolParts)；compaction→T2(pop)；老消息 fallback→T6；content/provider/补尾不动→边界+T7。

**R1+R2+R3 闭合：** content 装配零改动（边界）；per-event 正确算子（非 blanket，R2）；无偏移量、工具切段不依赖位置（R3-2）；error 追加报错段非替换全部（R3-1）；`block.content` 非 `.text`（R3-4）；renderAssistantText 含 strip（R1/R3-1）；既有 source 测试就地迁移（R3-5）；两隔板常量（R2/R3-NIT）；端点 pop+仅 list（R2-B5）。

**类型一致性：** `Part {type,text}`/`{type,id,tool,arg,status,summary}` 在 `_build_message_parts`/`_sanitize_part_scalar`/端点/`applyToolEventToParts`/`MessageParts`/`ToolCallPill` 一致。

**Placeholder 扫描：** `_roundtrip_through_load`（T2）、`appendThinkingEventContent` 名（T4/T5）= 显式占位，实施 read 真实代码替换。

---

## 风险 / 实施注意

1. **content 装配零改动 = 硬约束**：T5 在每个写 content 的分支**旁加** parts 算子，**不改**原 content 写入（现有测试不破）。T7 Step3 人工 diff 守。
2. **per-event 算子必须对**：thinking 用 `appendThinkingEventContent` 并块（非 append）；诊断用现有诊断算子；error 追加报错段。错用 blanket append = R2 回归。
3. **renderAssistantText：strip 前置 + `block.content`**：`splitAssistantMessageBlocks` 不 strip tool-log、输出 `{type,content}`。抽出时两点都要对（R3-1/R3-4）。
4. **复制经既有 strip**：`partsToText` 含 thinking 标记 + tool-log，复制套 `getCopyableAssistantMessageText`/`stripToolLogComments`。
5. **持久化 part 必终态**：`_sanitize_part_scalar` + 端点 pending/未知→success，杜绝 reload 永久转圈。
6. **rare-path live/reload**：reload 无 thinking（不持久化）= 现状；`buffer_required_write_content` 抑制轮 / retry 路径 reload 文本与 live 可能略有出入（常见多轮场景正确）。Goal 不承诺逐路径字符级一致——thinking live-only + 抑制边界已记。彻底一致留「后端显式 render_parts accumulator」follow-up。
7. **DeepSeek / 独立审查 / 老对话**：parts 不进 provider；独立审查零改动；老消息无 parts → 现状分组 fallback。

## 后续（follow-up，非本计划）
- 后端显式 `render_parts` accumulator：消除 rare-path live/reload 出入。
- 持久化 content 改全轮（须连带补尾切片，R1 警示）以让 provider/压缩看全轮叙述。
- pre-existing 持久化 gap（配额中断轮 / 空文本轮）。
