# 2026-05-04 — 上下文信号与意图 tag 架构修复

## Context

reality_test 一轮真实写报告会话（项目 `proj-0750fc3f5ea0`，对应 [conversation.json](../../reality_test/.consulting-report/conversation.json)）暴露五处链条性问题，背后是同一类设计反模式的不同表现：

> **后端与模型之间的"信号通道"分层错位**——
> 该让模型自己判断的事，后端用关键词遍历硬猜；
> 该回喂给模型的状态，没回喂；
> 该只给模型的内部纠错，被原样渲染给用户；
> 该让用户看到下一步的兜底文案，又被持久化回灌给模型自己看。

具体五个症状：

1. **Bug A（门禁误判）**：用户说"开始写报告吧"，[chat.py:1642 `_classify_canonical_draft_turn`](../../backend/chat.py:1642) 280 行关键词遍历挡住，模型工具被 reject。但模型最终文本回了"已为您起草执行摘要与第一章"——`content/` 目录实际为空。两套关键词表 [`NON_PLAN_WRITE_ALLOW_KEYWORDS`](../../backend/chat.py:300) 与 [`REPORT_BODY_FIRST_DRAFT_KEYWORDS`](../../backend/chat.py:222) 内容重叠但行为不一致：前者允许"开始写报告"，后者强制要求"正文"二字——用户说"开始写报告吧"被挡。

2. **Bug B（黄框污染）**：每个用户回合里模型第一次 `write_file`/`edit_file` 已存在文件，[chat.py:6109](../../backend/chat.py:6109) 返回"本轮要修改的文件 X 已存在。请先调用 read_file..."。这条**给模型的内部纠错文本**通过 [chat.py:4604 `_emit_system_notice_once`](../../backend/chat.py:4604) 同时塞给前端，[ChatPanel.jsx:676](../../frontend/src/components/ChatPanel.jsx:676) 渲染成黄色警告框。模型每轮都先 write 一次，每轮都触发——用户视野里塞满"产品在自我纠错"的噪音。

3. **Bug C（阈值黑盒）**：项目 5000 字 → [skill.py:372](../../backend/skill.py:372) 算出 `data_log_min = 7`。模型只录 5 条 data-log 就汇报"已超过系统阈值"。根因在 [skill.py:1240 `_render_progress_markdown`](../../backend/skill.py:1240) 完全没渲染 `quality_progress` 字段——模型每轮看到的 progress.md 只有阶段/状态/已完成/下一步，没有任何 `5/7` 数字。模型不是笨，是没数据。

4. **Bug D（兜底黑洞 + 回灌污染）**：会话第 7 条 assistant 内容是字面 `"（本轮无回复）"`——[chat.py:3426](../../backend/chat.py:3426) 兜底文案，触发条件是流截断/tag strip 后变空/只输出 tool_call 没 final text。用户被迫追问"什么？"。同一份字符串还会被 [chat.py:4080](../../backend/chat.py:4080) 在下一轮重建 history 时回喂给模型——模型看到自己上轮说"无回复"会困惑，可能加剧幻觉。

5. **Bug E（模型对自己上轮的工具调用零记忆）**：[chat.py:3440](../../backend/chat.py:3440) 只 extend 用户消息和最终 assistant 文本进 history，中间所有 tool_call / tool_result 全部丢弃。下一轮 `_build_provider_conversation` 拼 history，模型只看到文本对，**完全不知道自己上轮调过哪些工具、哪些成了哪些失败了**。这是 Bug A 中"撒谎"现象的直接放大器：模型上轮工具失败，文本里写了"已起草第一章"——下一轮重读自己文本，没法对照工具记录纠偏，幻觉被自我加强。

---

[2026-04-21 stage-ack spec §Context](2026-04-21-s0-interview-and-stage-ack-design.md) 已经定性过同源反模式：

> 两 bug 同源：阶段推进信号识别层级错位——后端用正则硬猜 LLM 本来能判的意图，列表永远覆盖不全，加词越多误伤越多（**第一性：意图识别应交给回路内的 LLM**）。

那次的修法是 LLM 输出 `<stage-ack>KEY</stage-ack>` 结构化 tag，后端只解析 + 校验前置条件。意图识别层级从"后端正则猜中文"提升到"LLM 输出可控 tag"。

**Bug A 是同款反模式的复发**——`_classify_canonical_draft_turn` 280 行关键词遍历，每加一条用词都是补漏，下次还会漏。但 stage-ack 是"回复结束后的 checkpoint side effect"，正文写入决策却是"回复**开始前**就要算出来"驱动 `can_write_non_plan` / system prompt / immediate reject / required write snapshot / 流式缓冲——所以 draft-action tag**不能完全替代** preflight。本 spec §4 的设计是：preflight 保留做"粗粒度门禁"（决定本轮 turn-context 状态），draft-action tag 做"细粒度意图分类"（决定 begin/continue/section/replace），两者分工明确而不是替代。

---

## Goals

1. **A1 黄框分层** — `system_notice` 区分"用户该看的阻塞性指引"vs"模型该看的内部纠错"，前端只渲染前者；`surface_to_user` 改必填参数 + dedupe 拆"可见 / 内部"双套，避免隐藏 notice 抢占可见 notice
2. **A2 阈值可见** — `progress.md` / `tool_result` 双通道把 `quality_progress` 数值（如 `5/7 条`）回喂给模型；target=0 / quality_progress=None 时静默
3. **A3 兜底分层** — assistant 空回复对用户给"下一步行动"文案，对模型完全不持久化空 assistant；连续两条 user role 在 provider build 时合并（防 Gemini 角色交替校验 400）；三层 sanitize（GET /conversation API + 前端 history loader + provider build）
4. **B1 意图 tag** — 引入 `<draft-action>` 系列 tag 做正文意图细分；保留 preflight `_classify_canonical_draft_turn`（rename 为 `_preflight_canonical_draft_check`）做粗粒度 turn-context 决策；删除大段精细关键词常量，但保留必要的"通用许可词"给 `_should_allow_generic_non_plan_write` 等独立 caller
5. **C1 工具历史可见** — 上一轮的 `tool_call` 名称 + 成败状态进入下一轮 history；选 HTML 注释格式 + 三层 sanitize（render / copy / GET /conversation）

## Non-Goals

- **不动 `memory_entries` 累积上限 / 淘汰策略**（gemini-3-flash effective 200k 上限，180k 触发摘要，当前 25k 远未触顶；独立优化项进 worklist）
- **不动 `max_iterations`**（已 10→20，reality_test 当前会话未撞顶）
- **不重写撒谎防御**（Bug A 模型撒谎部分是 flash 行为问题，C1 缓解但不根除；进 worklist 长期跟踪）
- **不动 `_finalize_post_turn_compaction` 摘要逻辑**（与本 spec 解耦，独立项）
- **不引入 OpenAI/Anthropic SDK 标准 tool_call 格式持久化**（C1 选项 A 已否，见 §Alternatives）
- **不删除 preflight `_classify_canonical_draft_turn`**（v1 spec 误以为可以删；v2 修正：preflight 决定 turn-start 状态、tag 决定 turn-during 细分动作，互补不替代）

## Design

### 1. system_notice 分层（A1）

**当前问题**：[`_emit_system_notice_once`](../../backend/chat.py:6455) 的所有 notice 都通过 SSE `system_notice` 事件流到前端 [ChatPanel.jsx:676](../../frontend/src/components/ChatPanel.jsx:676) 渲染成黄框，没有区分"用户必须看到的"vs"模型内部纠错"。同时 [chat.py:6463](../../backend/chat.py:6463) 是整轮全局 dedupe（`system_notice_emitted` 一旦 True 就 return），如果先触发一个内部纠错 notice，后续真正该给用户看的阶段门禁 notice 会被吞掉。

**目标分类**（按是否需要用户决策）：

| 类型 | 例子 | surface_to_user |
|---|---|---|
| 阶段不对 | "S0 阶段：请先对 seed 做一轮澄清，再写大纲" | True |
| 路径错了 | "report_draft 必须写 `content/report_draft_v1.md`" | True |
| 大纲未确认 | "本轮还不能开始写正文，请先确认大纲" | True |
| 阶段 ack 前置 | "checkpoint X 前置文件 Y 缺失" | True |
| 破坏性写阻拦 | "本轮不要继续改动正文" | True |
| read 再写 | "本轮要修改的文件 X 已存在，请先 read_file" | False（模型流程纠错） |
| fetch_url 缺失 | "本轮已 web_search 但未 fetch_url" | False |
| 工具参数验证失败 | "field X is missing" | False |
| 工具执行异常（默认） | "API timeout" | False（除非严重到用户必须介入） |

#### 1.1 SystemNotice model 改造

[`models.py SystemNotice`](../../backend/models.py) 新增 `surface_to_user` 字段：

```python
class SystemNotice(BaseModel):
    category: str
    path: str | None = None
    reason: str
    user_action: str
    surface_to_user: bool   # 必填，无默认值（强制 audit 全部 call site）
```

**故意不给默认值**——v1 spec 用 `surface_to_user=True` 默认会让"忘记标记的旧 notice 继续漏到用户"，依赖人工 audit 不靠谱。改必填后，编译/类型检查阶段就会暴露所有遗漏的 call site。

#### 1.2 _emit_system_notice_once 拆 dedupe

[chat.py:6455](../../backend/chat.py:6455) 现有实现：

```python
def _emit_system_notice_once(self, *, category, path, reason, user_action):
    if self._turn_context.get("system_notice_emitted"):
        return
    ...
```

改为按"可见 / 内部"两套独立 dedupe：

```python
def _emit_system_notice_once(
    self,
    *,
    category: str,
    path: str | None,
    reason: str,
    user_action: str,
    surface_to_user: bool,  # 必填
) -> None:
    flag_key = "user_notice_emitted" if surface_to_user else "internal_notice_emitted"
    if self._turn_context.get(flag_key):
        return
    notice = {
        "type": "system_notice",
        "category": category,
        "path": path,
        "reason": reason,
        "user_action": user_action,
        "surface_to_user": surface_to_user,
    }
    self._turn_context[flag_key] = True
    queue = self._turn_context.setdefault("pending_system_notices", [])
    queue.append(notice)
```

`_new_turn_context` 同步把 `system_notice_emitted` 字段拆成 `user_notice_emitted` / `internal_notice_emitted` 两个。

**效果**：先发的隐藏 notice 不会吞掉后续可见 notice；同一类（可见或内部）仍然 per-turn 一次去重。

#### 1.3 服务端只发 surface_to_user=True

v1 spec 原方案是"后端发全部 notice 到前端，前端按字段过滤"。codex 评审指出这不是真正分层，只是前端遮罩；流式路径（[ChatPanel.jsx:518-534](../../frontend/src/components/ChatPanel.jsx:518)）和非流式 API（[main.py:258-262](../../backend/main.py:258)）仍然把内部纠错塞进前端消息数组。

v2 改为**服务端就过滤**：
- SSE `system_notice` 事件只对 `surface_to_user=True` 的 notice 推送
- 非流式 chat endpoint 返回的 `system_notices` 字段只含 `surface_to_user=True`
- 内部纠错 notice 通过两条独立路径仍能流转：(a) tool_result 的 `{"status": "error", "message": reason}` → 模型立刻收到并自我纠正；(b) 后端日志 `logging.info` 记录内部 notice 用于调试
- 可选：增加 dev-only 环境变量 `EXPOSE_INTERNAL_NOTICES=1` 让开发者临时打开看全量 notice，默认关

#### 1.4 完整 audit 表（Appendix C）

落地时 audit 全部 `_emit_system_notice_once` call site 并标 `surface_to_user`。Appendix C 给出完整表（v2 已补 codex 评审中漏列的 7 处）。

### 2. progress.md / tool_result 阈值回喂（A2）

**当前问题**：[skill.py:1150 `_build_quality_progress`](../../backend/skill.py:1150) 算出 `{label: "有效来源条目", current: 5, target: 7}`，但 [skill.py:1240 `_render_progress_markdown`](../../backend/skill.py:1240) 只渲染阶段/状态/已完成/下一步，**完全没用 `quality_progress` 字段**。模型每轮看到的 progress.md 没有数字。

#### 2.1 progress.md 持续可见

`_render_progress_markdown` 接收完整 `stage_state` 对象（不只是 `stage_code`），仅当 `quality_progress` 非空且 `target > 0` 时在"## 当前状态"块下面追加一行：

```md
## 当前状态
**阶段**: S2
**状态**: 进行中
**当前任务**: ...
**质量进度**: 5/7 条 有效来源     ← 仅 S2/S3 阶段、target>0 时显示
**更新日期**: 2026-05-04
```

渲染条件（精确）：

```python
qp = stage_state.get("quality_progress")
if qp and isinstance(qp.get("target"), int) and qp["target"] > 0:
    label = qp["label"]
    current = qp.get("current", 0)
    target = qp["target"]
    lines.append(f"**质量进度**: {current}/{target} {label}")
```

`target=0` / `quality_progress=None` / `stage_state` 缺字段都直接跳过该行（避免 `0/0` 噪音）。

`stage_state` 参数加在已有签名后面：

```python
def _render_progress_markdown(
    self,
    stage_code: str,
    status: str,
    next_actions: list[str],
    completed_items: list[str],
    *,
    stage_state: dict | None = None,  # 新增可选参数，向后兼容
) -> str:
```

[`_sync_stage_tracking_files`](../../backend/skill.py:1083) 内部调用全部传 `stage_state=stage_state`。其他外部调用方暂不强制传。

#### 2.2 tool_result 即时反馈

[`_persist_successful_tool_result`](../../backend/chat.py:1092) 之后、tool_result 写入 `current_turn_messages` 之前，对 `write_file` / `edit_file` 写入路径为 `plan/data-log.md` 或 `plan/analysis-notes.md` 的成功 result 追加一段提示：

```python
result["quality_hint"] = "当前 5/7 条 有效来源，还差 2 条满足 S2 进 S3 门槛"
```

**关键事实**：当前 tool_result 走 [chat.py:3323-3327](../../backend/chat.py:3323) / [3602-3606](../../backend/chat.py:3602) 用 `json.dumps(result, ensure_ascii=False)` 序列化为 `role="tool"` 消息内容塞回模型。**`quality_hint` 字段会原样进入这个 JSON 字符串**——provider 不消费结构化字段、不需要改 parser、不需要改 schema。模型从 raw JSON 里读出来理解。

**条件**：
- 仅当 `stage_code` 为 `S2` 或 `S3` 时附加
- 仅当 `quality_progress.target > 0` 时附加
- 写其他 plan 文件不附加（避免无关噪音）
- 写 `content/report_draft_v1.md` 不附加（draft 进度有专属 followup state）

### 3. assistant 空回复处理（A3）

**当前问题**：[chat.py:3426](../../backend/chat.py:3426) `assistant_message = "（本轮无回复）"` 兜底文案被两次使用：（a）流式响应时直接 yield 给前端展示给用户，（b）持久化进 [conversation.json](../../reality_test/.consulting-report/conversation.json) 下一轮喂给模型。第二次使用是污染。同时空 assistant 兜底逻辑分散在三处：[chat.py:3423-3442](../../backend/chat.py:3423) 流式、[chat.py:3681-3700](../../backend/chat.py:3681) 非流式、[chat.py:6355-6369](../../backend/chat.py:6355) early finalize——v1 spec 只说"改文案"远远不够。

#### 3.1 抽 helper 统一三处持久化

新增 [`backend/chat.py`](../../backend/chat.py) 私有方法：

```python
USER_VISIBLE_FALLBACK = (
    "（这一轮我没有产出可见回复，可能是处理过程中断了。"
    "请把刚才的需求换个说法再发一次。）"
)

def _finalize_empty_assistant_turn(
    self,
    project_id: str,
    history: List[Dict],
    current_user_message: Dict,
    *,
    diagnostic: str = "stream_truncated",
) -> str:
    """
    当 assistant 文本最终为空时统一调用：
    1. 不持久化空 assistant 进 history（避免污染）
    2. user message 持久化（否则下轮少一条）
    3. conversation_state.json 记录 empty_assistant_event 用于可观测
    Returns: USER_VISIBLE_FALLBACK 字符串供调用方 yield 给用户面
    """
    history.append(current_user_message)
    self._save_conversation(project_id, history)
    self._record_empty_assistant_event(project_id, diagnostic)
    return USER_VISIBLE_FALLBACK
```

三处持久化点全部改为：

```python
if not assistant_message.strip():
    fallback_text = self._finalize_empty_assistant_turn(
        project_id, history, current_user_message, diagnostic=...
    )
    yield {"type": "content", "data": fallback_text}
    yield {"type": "usage", "data": token_usage}
    return
```

#### 3.2 连续 user role 处理（关键修正）

**v1 spec 错了**——[chat.py:3267-3270 已有注释](../../backend/chat.py:3267)证明 Gemini 不接受连续两条 user：

> 用一条纯文本 assistant + 一条 user 反馈做"合规隔板"，保持 user/model 严格交替——直接 append 一条 user 会导致连续两条 user（前面本轮原始用户消息），触发 Gemini 的角色交替校验 400。

修正：在 `_to_provider_message` 之后、provider conversation 拼好之前加**相邻 user 合并步骤**：

```python
def _coalesce_consecutive_user_messages(self, conversation: List[Dict]) -> List[Dict]:
    """合并相邻的 user role 消息为一条（用 \n\n 连接 content）。
    防止 Gemini 角色交替 400。Conversation.json 持久化结构不变。"""
    def _normalize_content(c) -> str | list:
        # 防御非 str/list 输入（理论上 _to_provider_message 只产 str / list，
        # 但 sanitize 链路如果出 bug 可能塞 None / dict，需兜底）
        if c is None:
            return ""
        if isinstance(c, str) or isinstance(c, list):
            return c
        return str(c)

    coalesced = []
    for msg in conversation:
        if (
            coalesced
            and msg.get("role") == "user"
            and coalesced[-1].get("role") == "user"
        ):
            prev = coalesced[-1]
            prev_content = _normalize_content(prev.get("content"))
            new_content = _normalize_content(msg.get("content"))
            if isinstance(prev_content, str) and isinstance(new_content, str):
                # 两边都是纯文本 → 字符串拼接
                prev["content"] = (prev_content + "\n\n" + new_content).strip("\n") if prev_content or new_content else ""
            else:
                # 至少一边是 multipart array → 合并为单个 array，保持顺序
                prev_parts = prev_content if isinstance(prev_content, list) else (
                    [{"type": "text", "text": prev_content}] if prev_content else []
                )
                new_parts = new_content if isinstance(new_content, list) else (
                    [{"type": "text", "text": new_content}] if new_content else []
                )
                prev["content"] = prev_parts + new_parts
        else:
            coalesced.append(dict(msg))  # shallow copy 防止改原 history
    return coalesced
```

调用点：[`_build_provider_turn_conversation`](../../backend/chat.py:3777) 拼完整 conversation 后、return 前过一遍 `_coalesce_consecutive_user_messages`。**conversation.json 持久化层不动**——history 里仍然是连续两条 user role（用户视角看清楚自己说过什么）；只在喂给 LLM 之前合并。

#### 3.3 三层 sanitize 清理历史污染

reality_test 现有 conversation.json 已经有 `"（本轮无回复）"` 残留（如第 7 条）。需要三层清理：

| 层 | 位置 | 行为 |
|---|---|---|
| Layer 1: GET /conversation API | [main.py:368-377](../../backend/main.py:368) | 返回前过滤 content == 任一历史 fallback 字面量的 assistant message（直接从返回值剔除） |
| Layer 2: 前端 history loader | [ChatPanel.jsx:121-132](../../frontend/src/components/ChatPanel.jsx:121) | API 返回后再 filter 一次（兜底） |
| Layer 3: provider build | [chat.py:_to_provider_message](../../backend/chat.py:4072) | 喂给 LLM 时跳过这种 assistant message |

历史 fallback 字面量集合：

```python
LEGACY_EMPTY_ASSISTANT_FALLBACKS = frozenset({
    "（本轮无回复）",
    USER_VISIBLE_FALLBACK,
    # 未来如果改文案，旧版本要并入这里
})
```

sanitize 仅作用于 `role="assistant"` 且 content **完全等于**集合中任一字面量的 message；user role 完全不动。

#### 3.4 conversation_state.json 可观测性

`_record_empty_assistant_event(project_id, diagnostic)`:

```python
state["events"].append({
    "type": "empty_assistant",
    "diagnostic": diagnostic,  # "stream_truncated" / "tool_only_no_text" / "tag_strip_emptied" / ...
    "recorded_at": timestamp,
})
```

不影响模型行为，仅可观测。Diagnostic 字符串由调用方传入区分根因。

### 4. 正文意图：preflight 粗粒度 + draft-action 细粒度（B1）

**当前问题**：[chat.py:1642 `_classify_canonical_draft_turn`](../../backend/chat.py:1642) 用 280 行关键词遍历同时做两件事：(a) **回合开始前**判定本轮 turn-context（`can_write_non_plan` / `mixed_intent` / `required_write_snapshot` / `immediate_reject`），(b) **回合开始前**判定意图细分（begin/continue/section/replace）。两套关键词列表互不一致——Bug A 的根因。

**v1 spec 误以为可以全部用 `<draft-action>` tag 替代——错了**。codex 评审指出：tag 是 LLM 回复内的输出，而 preflight 必须在 LLM 调用之前完成（要驱动 system prompt 的 `turn_rule` / `can_write_non_plan` / required write snapshot / 流式缓冲决策）。两者时序根本不同。

**v2 设计**：分工清晰，preflight 保留但**只做粗粒度**；tag 做**细粒度**。

#### 4.1 时序图

```
turn start
 │
 ▼
[preflight 粗粒度] _preflight_canonical_draft_check(user_message, stage_code)
 │   - 用户消息中是否有"正文意图信号"（极短关键词列表）？
 │   - mixed-intent split？（保留现有逻辑）
 │   - stage gate？（S0/S1 + 含正文意图 → immediate reject + system_notice）
 │   - outline_confirmed_at gate？（未确认 + 含正文意图 → reject + system_notice）
 │   - 设 turn_context.can_write_non_plan / mixed_intent_secondary_family / immediate_reject_message
 │
 ▼
[system prompt 渲染] turn_rule 按 can_write_non_plan 选模板
 │
 ▼
[LLM 流式生成] tail guard 扫描 <draft-action> + <stage-ack> 前缀
 │   - 模型在回复中输出 <draft-action>begin/continue/section:X/replace>
 │
 ▼
[draft-action parser] 流结束后 parse(content) → DraftActionEvent[]
 │   - 校验位置（tail / independent line / not in fenced/inline/blockquote）
 │   - 校验前置：stage_code / outline / draft 文件存在性 / section 可定位 / replace 唯一
 │   - 通过的事件 → 写入 turn_context.draft_action_decision
 │
 ▼
[模型调正文工具] append_report_draft / edit_file content/report_draft_v1.md
 │   - 工具放行检查：preflight 已 can_write_non_plan + 本轮存在合法 draft-action event ⇒ pass
 │   - 缺 tag → reject + system_notice "请先发 <draft-action> tag 声明动作类型"
 │
 ▼
turn end → strip tags + tool-log → persist
```

#### 4.2 preflight 粗粒度判定（保留 + 简化）

[chat.py:1642 `_classify_canonical_draft_turn`](../../backend/chat.py:1642) **保留并 rename** 为 `_preflight_canonical_draft_check`。功能简化为只回答三个问题：

1. **本轮用户消息中是否含正文意图？**——用一组**极短关键词列表**判定（见 §4.7）；命中 = "可能要写正文"
2. **如果含正文意图，当前 stage_code / checkpoint 是否允许？**——不允许 → 设 `immediate_reject_message` + 当前 turn 不放行任何 content 写入
3. **是否 mixed intent 需要 split-turn？**——保留现有 `_secondary_action_families_in_message` / `_message_has_distinct_non_expansion_action` / `_message_has_conditional_target_expansion_intent` 逻辑

**不再做**：begin/continue/section/replace 的细分（这部分由 §4.3 draft-action tag 决定）。

返回结构（沿用现有 `_make_canonical_draft_decision` dict 模板）：

```python
{
    "mode": "require" | "no_write" | "reject",
    "priority": "P_PREFLIGHT_*",
    "stage_code": str,
    "fixed_message": str | None,  # immediate_reject_message
    "mixed_intent_secondary_family": str | None,
    "expected_tool_family": None,  # tag 决定
    "required_edit_scope": None,   # tag 决定
    "preflight_keyword_intent": "begin" | "continue" | None,  # 新增（v4）：仅当 _DRAFT_INTENT_PREFLIGHT_KEYWORDS 命中时填写，作为 §4.8 tool-only fallback 的唯一信号源；不要把 section/replace 偷偷塞进来
    # ... 其他字段保留兼容现有下游
}
```

**`preflight_keyword_intent` 字段约束（v4 关键）**：
- 取值仅限 `"begin"` / `"continue"` / `None` —— 严禁出现 `"section"` / `"replace"`
- 仅由 §4.7 的 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 列表命中时设置
- "begin" 类关键词命中 → `"begin"`；"continue" 类命中 → `"continue"`；都没命中 → `None`
- 这是 preflight **唯一**输出的细分信号，专门为 §4.8 tool-only fallback 设计；不允许在其他地方使用
- 测试硬约束：`preflight_keyword_intent` 永远不能等于 `"section"` 或 `"replace"`

**v5 amendment**: 取值放宽至 section/replace，详见 §4.12。

#### 4.3 draft-action tag 语法（细粒度）

> **⚠️ SUPERSEDED**: §4.3 - §4.12 (含 v5 amendment) 已被 `2026-05-05-report-tools-redesign-design.md` 替代。tag-based 架构（含 fix4 v5 §4.12）整套删除，改用 4 个专用工具（spec §2.1-§2.4）。本节保留作为历史 context。新代码请参考 redesign spec。

四种意图，三种语法形式：

```
<draft-action>begin</draft-action>                 # 首次起草正文
<draft-action>continue</draft-action>              # 续写
<draft-action>section:第二章</draft-action>         # 重写指定章节
<draft-action-replace>                              # 替换：嵌套子节点（不用 | 分隔符）
  <old>原文片段</old>
  <new>新文本</new>
</draft-action-replace>
```

`replace` 用嵌套 XML 因为 `|` 在正文中常见（codex 评审指出 v1 的 `OLD|NEW` 设计自相矛盾，Open Question 已转 Resolved）。

**正则**（两套独立扫描，因为语法形式不同）：

```python
DRAFT_ACTION_SIMPLE_RE = re.compile(
    r'<draft-action>(begin|continue|section:[^<\n]{1,80})</draft-action>',
    re.IGNORECASE,
)

DRAFT_ACTION_REPLACE_RE = re.compile(
    r'<draft-action-replace>\s*'
    r'<old>([\s\S]{1,1000}?)</old>\s*'
    r'<new>([\s\S]{1,1000}?)</new>\s*'
    r'</draft-action-replace>',
    re.IGNORECASE,
)
```

`section:LABEL` 中 LABEL 长度上限 80 字符，禁止换行/`<`。replace 的 OLD/NEW 长度各上限 1000 字符（覆盖一段 / 一个小节）。

#### 4.4 位置约束 & 流式 tail guard

复用 [stage-ack 设计](2026-04-21-s0-interview-and-stage-ack-design.md#L82) 的位置约束：

- 必须在回复尾部（tail anchor 之后）
- 必须独立一行（前后只能有空白）
- 必须在 Markdown fence / inline code / blockquote 之外
- 不满足任一约束 → 识别但 `executable=False, ignored_reason=...`，仍从 content 剥离

`<draft-action-replace>` 是多行 block，"独立一行"的判定改为"block 起始行独立 + 终止行独立"。

**流式 tail guard**：扫描表加入 `<draft-action` 和 `<draft-action-replace` 两个前缀子串。tail guard 检测到任一前缀子串时暂停 flush，流关闭后统一 parse + strip。

#### 4.5 后端模块 `backend/draft_action.py`

结构对齐 [stage_ack.py](../../backend/stage_ack.py)：

```python
@dataclass
class DraftActionEvent:
    raw: str
    intent: Literal["begin", "continue", "section", "replace"]
    section_label: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    start: int
    end: int
    executable: bool = True
    ignored_reason: str | None = None

class DraftActionParser:
    def parse(self, content: str) -> list[DraftActionEvent]: ...
    def strip(self, content: str) -> str: ...   # 剥所有 simple + replace tag
    def parse_raw(self, content: str) -> list[DraftActionEvent]: ...  # 不做位置校验
```

#### 4.6 前置校验

| 校验项 | 不通过 |
|---|---|
| preflight 已经把当前轮判为 `mode=reject`（stage / outline 不对） | tag 全部 `executable=False, ignored_reason="preflight_blocked"`；不重复发 system_notice（preflight 已发） |
| stage_code ∉ {S4, S5, S6, S7, done} | `executable=False, ignored_reason="stage_too_early"`，发 notice "S0/S1 阶段不能写正文，请先确认大纲"（surface_to_user=True） |
| `outline_confirmed_at` 未 set | `executable=False, ignored_reason="outline_not_confirmed"`，发 notice "请先在工作区确认大纲"（surface_to_user=True） |
| `intent="continue"` 时 `content/report_draft_v1.md` 不存在 | 自动降级为 `intent="begin"`（不 fail，记 warning） |
| `intent="section"` 时 `content/report_draft_v1.md` 不存在 | `executable=False, ignored_reason="no_draft"`，发 [`CANONICAL_DRAFT_NO_DRAFT_MESSAGE`](../../backend/chat.py:1765-1771)（surface_to_user=True）。**不要**降级为 "heading not found"——空草稿要给"先起草"指引而非"核对标题"指引 |
| `intent="section"` 时 draft 存在但 `section_label` 找不到匹配 | `executable=False, ignored_reason="section_not_found"`，发 notice "找不到章节 'X'，请先 read_file 核对章节标题"（surface_to_user=True） |
| `intent="replace"` 时 `content/report_draft_v1.md` 不存在 | `executable=False, ignored_reason="no_draft"`，发 `CANONICAL_DRAFT_NO_DRAFT_MESSAGE`（surface_to_user=True） |
| `intent="replace"` 时 `old_text` 在 draft 中不唯一/未找到 | `executable=False, ignored_reason="replace_target_invalid"`，发 notice "替换源文本未找到/不唯一"（surface_to_user=True） |

**section LABEL 匹配规则**（v1 没定义，codex 评审要求明确）：

复用 [`_resolve_section_rewrite_targets`](../../backend/chat.py:2269-2341) 的算法，但**改 query 来源**：原算法从 user message 抽 LABEL 候选，新规改为**直接用 tag 内的 `section_label` 字符串**作为唯一候选去匹配 draft 的 heading。匹配优先级（保留 `_resolve_section_rewrite_targets` 现有的优先级）：

1. **完整 heading exact match**（如 tag = `第二章 战力演化`，draft 里有 `## 第二章 战力演化`）→ unique match
2. **heading 编号前缀 match**（如 tag = `第二章`，draft 里 `## 第二章 战力演化` / `## 第二章附录` 都命中）→ ambiguous → reject 让模型补全
3. **heading 标题前缀 match**（如 tag = `战力演化`，draft 里 `## 第二章 战力演化` / `## 第三章 战力演化补遗` 都命中）→ ambiguous → reject

唯一命中 → 通过；ambiguous → executable=False + notice "章节 X 不唯一，请用完整 heading 定位"。

#### 4.7 强关键词兜底（极短列表）

LLM 漏发 tag 时的兜底——v1 没给字面值，v2 给定（codex 评审要求）：

```python
_DRAFT_INTENT_PREFLIGHT_KEYWORDS = (
    # begin 类
    "开始写报告",
    "开始写正文",
    "开始起草",
    "起草报告",
    "写第一版",
    # continue 类
    "继续写",
    "继续写报告",
    "继续写正文",
    "接着写",
    "写下一章",
    "写下一段",
)
```

**只用于 preflight 粗粒度判定**（"用户是不是在谈正文"）。**不直接放行写工具**——放行仍要求模型在回复里发 tag。如果模型发了 tag → 走 §4.6 校验；如果模型既没发 tag 又调写工具 → reject + notice "请先发 `<draft-action>` tag 声明动作类型"。

不兜 `section` / `replace`——这两类用户表达更复杂，缺 tag 时直接拒绝让模型重发。

**v5 amendment**: section/replace 在能 resolve 唯一目标时也走 keyword fallback，详见 §4.12。

#### 4.8 tool-only turn 的 fallback（v3 收紧）

[chat.py:3423-3426](../../backend/chat.py:3423) 已经承认存在"只出 tool_call 没 final text"的 turn——这种 turn 里 LLM 没有 content 可放 tag。

**v2 原方案过宽**（codex round-2 评审指出）：v2 说"preflight `can_write_non_plan=True` + tool-only turn 允许写正文工具"，但 preflight 已粗化为不输出 `expected_tool_family` / `required_edit_scope`，下游精细约束依赖这些字段（[`chat.py:2556-2584`](../../backend/chat.py:2556), [`chat.py:4751-4820`](../../backend/chat.py:4751)）。这意味着无 tag 的 tool-only `edit_file` 会绕过 section/replace 精细约束。

**v3 收紧规则**——tool-only fallback **仅放行**以下严格场景：

| 工具 | 无 tag 是否放行 |
|---|---|
| `append_report_draft`（首次起草 / 续写） | ✓ 放行，前提是 preflight `can_write_non_plan=True` 且 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 命中了 begin/continue 意图 |
| `edit_file` 写 `content/report_draft_v1.md`（任何 section / 全文 / replace） | ✗ **必须**带 tag（draft-action section / draft-action-replace），否则 reject + notice "请先发 `<draft-action>` tag 声明改动类型" |
| `write_file` 写 `content/report_draft_v1.md` | ✗ 永久禁止（已有规则，不变） |
| `write_file` / `edit_file` 写 `plan/*` | ✓ 放行（不属于正文写工具，不受 draft-action 约束） |

**v5 amendment**: edit_file 也允许 keyword fallback，前提是 preflight 已 resolve 目标，详见 §4.12。

实现：[`_validate_canonical_draft_write_call`](../../backend/chat.py)（新增 / 沿用现有命名）在工具放行检查时区分两类目标：

```python
def _gate_canonical_draft_tool_call(
    self,
    project_id: str,                    # v5: caller 必须传入（_record_tagless_fallback_event 需要）
    tool_name: str,
    tool_args: dict,
    decision: dict,                     # _make_canonical_draft_decision 返回的 dict
    tags: list[DraftActionEvent],       # v5: 明确为 DraftActionParser.parse() 的输出，已过 §4.6 前置校验
) -> str | None:
    """
    返回 None = 放行；返回 str = block 原因（用作 system_notice reason）。

    Caller contract（v5 明确）：
    - `project_id`：当前 turn 的 project id；必须传入（用于 _record_tagless_fallback_event 写 conversation_state）
    - `tags`：本轮 LLM 回复经过 DraftActionParser.parse() 解析 + §4.6 前置校验后的事件列表。
      不是从 turn_context 反查、不是 raw 字符串扫描；caller（即 _finalize_assistant_turn 编排器）
      负责把已校验的 tags 列表传进来。
    - `decision`：本轮 _preflight_canonical_draft_check 的输出；必须含 `preflight_keyword_intent` 字段

    fallback 信号源约束（v4 + v5 强化）：唯一允许的 tag-less fallback 信号是
    decision["preflight_keyword_intent"]（"begin" / "continue" / None），
    严禁通过任何其他字段（如 expected_tool_family / intent_kind / required_edit_scope）
    推断 begin/continue/section/replace 的细分意图。
    """
    target_path = tool_args.get("file_path")
    if not self._is_canonical_report_draft_path(target_path):
        return None  # 不属于正文写，不管

    # 收集本轮已 executable 的 tag 意图集合
    tag_intents = {t.intent for t in tags if t.executable}
    keyword_intent = decision.get("preflight_keyword_intent")  # 唯一 fallback 信号源

    if tool_name == "append_report_draft":
        # 放行条件：本轮有 begin/continue tag，OR preflight 关键词命中 begin/continue
        if tag_intents & {"begin", "continue"}:
            return None
        if keyword_intent in {"begin", "continue"}:
            self._record_tagless_fallback_event(
                project_id, fallback_tool="append_report_draft",
                fallback_intent=keyword_intent,
            )
            return None
        return self.CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE

    if tool_name == "edit_file":
        # 必须有 executable section / replace tag；keyword_intent 不参与（v4 强制）
        if tag_intents & {"section", "replace"}:
            return None
        return self.CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE

    return None
```

**`_record_tagless_fallback_event(project_id, fallback_tool, fallback_intent)`**（v4 新增）：写入 `conversation_state.json` 的 `events` 数组，type=`tagless_draft_fallback`，含 `{turn_id, fallback_tool, fallback_intent, recorded_at}`。同时 `logging.warning("draft_write without explicit tag, fallback path used")` 兼容现有日志习惯。

**为什么强制 `preflight_keyword_intent` 是唯一信号源**：v3 评审发现伪代码用 `decision.get("intent_kind")` 但 §4.2 简化版 preflight 不再产 `intent_kind`——这种"字段名不一致"会诱导实施者把细分类逻辑悄悄塞回 preflight 扩大其职责。v4 用一个**专用且约束明确**的字段名 `preflight_keyword_intent` 同时解决两件事：(a) 给 fallback 一个稳定信号源；(b) 通过命名 + 注释 + 测试硬约束防止它被滥用为通用细分通道。

#### 4.9 mixed-intent 保留

[chat.py:1804-1857](../../backend/chat.py:1804) 现有 mixed-intent 拆轮逻辑（`mixed_intent_secondary_family` / `effective_turn_target_count`）继续由 preflight 处理。draft-action tag 不参与 mixed-intent 判定。下游 [chat.py:6272-6340](../../backend/chat.py:6272) 的 guidance 路径完全保留。

#### 4.10 删除范围（缩小 + 明确）

v1 spec 说"删除 280 行 + 大量常量"——v2 修正：

**删除（确认无外部 caller）**：
- 在 `_preflight_canonical_draft_check`（rename 后的 `_classify_canonical_draft_turn`）内部删除 begin/continue/section/replace 的细分逻辑（约 150 行）；改为只输出粗粒度三问的判定结果
- `REPORT_BODY_FIRST_DRAFT_KEYWORDS` / `REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS` / `REPORT_BODY_WHOLE_REWRITE_KEYWORDS` 常量（仅 `_classify_canonical_draft_turn` 内部用）
- **`REPORT_BODY_CHAPTER_WRITE_RE` / `REPORT_BODY_INLINE_EDIT_RE` / `REPORT_BODY_REPLACE_TEXT_INTENT_RE` 正则 + 配套 dead helper**：v3 修订——这三个正则被 [`_regex_has_clean_report_body_intent`](../../backend/chat.py:2477-2485) 使用，而该 helper 又被 `_has_explicit_report_body_write_intent` 使用。当前**这两个 helper 都没有外部 caller**（仅 `_classify_canonical_draft_turn` 间接路径用），所以删常量必须**同时删这两个 helper**，否则会留悬空死代码。落地清单：
  - 删 `REPORT_BODY_CHAPTER_WRITE_RE` / `REPORT_BODY_INLINE_EDIT_RE` / `REPORT_BODY_REPLACE_TEXT_INTENT_RE` 常量
  - 删 `_regex_has_clean_report_body_intent()` helper
  - 删 `_has_explicit_report_body_write_intent()` helper
  - 落地前必须 `grep -n` 三个常量 + 两个 helper 的全部 caller 确认无外部引用（仅在彼此和 `_classify_canonical_draft_turn` 内部出现才能删）
- `_parse_report_body_replacement_intent` / `_looks_like_section_rewrite_request` 等 helper（如果**仅被** `_classify` 调用，按上面同样 caller-grep 流程确认）

**保留（被其他 caller 引用）**：
- `NON_PLAN_WRITE_ALLOW_KEYWORDS` / `NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS`：被 [`_should_allow_generic_non_plan_write`](../../backend/chat.py:6484) / [`_looks_like_follow_up_non_plan_request`](../../backend/chat.py) / [`_is_non_plan_write_approval_message`](../../backend/chat.py) 调用 — 这些是**通用 non-plan write 审批**路径，跟正文细分意图无关
- `REPORT_BODY_SECTION_REWRITE_KEYWORDS`：被 [`_looks_like_section_rewrite_request`](../../backend/chat.py:2266) 调用 — 用于通用 section rewrite 检测
- `_make_canonical_draft_decision` / `_apply_stage_gate_to_canonical_draft_decision` / `_empty_canonical_draft_decision` 工厂函数（draft-action 决策仍走相同 dict 结构）
- `CANONICAL_DRAFT_*_MESSAGE` 系列常量（fixed_message 仍用，含新引用的 `CANONICAL_DRAFT_NO_DRAFT_MESSAGE` §4.6 用）
- `_phrase_hits` 通用工具函数

**实施顺序**：
1. 用 `grep -n` 在 `backend/chat.py` 全文搜每个**待删常量 + 待删 helper** 的引用
2. 仅当所有 caller 都在删除范围内（或 `_classify_canonical_draft_turn` 即将删的细分代码内）才能删
3. 出现外部 caller 必须先重构 caller，否则该常量/helper 保留
4. 完成后在 PR description 列出"已 grep 确认无悬空引用"

#### 4.11 SKILL.md 改造

[skill/SKILL.md](../../skill/SKILL.md) §S4 报告撰写节大改，新增子节"draft-action 标签规范"（结构对齐"stage-ack 标签规范"附录）：

```md
### S4 正文写作标签

当用户表达想要起草/续写/修改正文时（如"开始写报告""继续写""把第二章重写""把 X 改成 Y"），
你在调用 `append_report_draft` / `edit_file` **之前**必须先在回复中输出对应 draft-action tag：

| 用户意图 | 你发的 tag |
|---|---|
| 想看正文初稿 / "开始写报告" / "起草" | `<draft-action>begin</draft-action>` |
| 想继续 / 续写 / 写下一段或下一章 | `<draft-action>continue</draft-action>` |
| 想重写某一节（如"第二章重写"） | `<draft-action>section:第二章 战力演化</draft-action>`（用完整 heading 定位） |
| 想替换具体文字（如"把 X 改成 Y"） | `<draft-action-replace><old>X</old><new>Y</new></draft-action-replace>` |

tag 必须独立一行、在回复尾部、代码块外。系统检测到合法 tag 后才会放行写正文工具。
不发 tag 直接调写正文工具会被拒绝。

模型不需要遍历用户中文表达——只要能从用户消息中识别出"用户希望我做正文动作"，就发对应 tag；
不确定时不发 tag，先问用户澄清。

如果只调工具不输出文本（少见情况），系统会按本轮 preflight 推断的意图兜底放行。
```

#### 4.12 v5 amendment — section/replace keyword fallback (fix4)

**背景**：Phase 2a cutover smoke (4 reality_test sessions) 发现 model 在 section/replace 路径**几乎从不发 tag**（B/C 两节 0 hit），gate 死循环 19 次到 max_iter。旧通道 `_resolve_section_rewrite_targets` 因为"完整 heading label 是 user_message 子串"硬约束也几乎不 work，所以新通道比旧通道更差（dead loop vs. fail fast）。详见 `docs/superpowers/cutover_report_2026-05-05_fix3.md`、`docs/superpowers/handoffs/2026-05-05-phase2-section-replace-pending.md`。

**修订**（v5 替代 v4 §4.2 / §4.7 / §4.8 的严格表述）：

1. **`preflight_keyword_intent` 取值放宽**：从 `{"begin", "continue", None}` 扩展为 `{"begin", "continue", "section", "replace", None}`。
2. **严格安全契约**：preflight 输出 `"section"` / `"replace"` 时**必须已 resolve 出唯一目标**——否则保持 `None`，gate 仍然 block（fail-fast，UX ≥ 旧通道）。
3. **target resolve 规则**：
   - **replace**：复用 `_parse_report_body_replacement_intent`（已有，正则 `REPORT_BODY_REPLACE_TEXT_INTENT_RE`）抽 `old_text`/`new_text`；要求 draft 存在且 `draft_text.count(old_text) == 1`；满足则填 `preflight_keyword_intent="replace"`、`old_text`、`new_text`。
   - **section**：在 user_message 中抽**章节数字前缀**（正则 `r"第([一二三四五六七八九十百千万0-9]+)(?:[章节]|部分)"`），用 `label.startswith(prefix)` 在 draft heading nodes 中找候选；唯一命中 → 填 `preflight_keyword_intent="section"`、`rewrite_target_label`、`rewrite_target_snapshot`；多个或 0 个候选 → 保持 `None`。
4. **gate edit_file 分支放行**：当 `tag_intents` 为空且 `preflight_keyword_intent ∈ {"section", "replace"}` 时，记录 `tagless_draft_fallback` 事件后放行。其他情况仍按 v4 §4.8 执行（必须 tag 或 begin/continue keyword）。
5. **测试硬约束更新**：`preflight_keyword_intent` 不能等于除上述五种之外的任何值；`begin`/`continue` 优先级仍高于 `section`/`replace`（dict 顺序保留）。

**为什么是"严格安全 fallback"而非"弱兜底"**：弱兜底（仅看关键词不看目标）会让 gate 在 model 改错章节/改错字符串时也 pass；严格 fallback 要求 preflight 自己能 resolve target，确保 fallback pass 时目标信息跟"有 tag"等价，安全性不降级。

**SKILL.md 更新**：§S4 保留 tag 鼓励，但说明缺 tag 时只要意图明确（指定章节/old-new 配对）系统也能 fallback；不要教用户依赖 fallback。

**实施引用**：
- `_preflight_canonical_draft_check` 扩展（`backend/chat.py`）
- `_preflight_resolve_section_target`（新增 helper，heading 数字前缀匹配）
- `_gate_canonical_draft_tool_call` edit_file 分支扩 fallback
- 完整 commit/test 见实施记录。

### 5. 工具调用可见化（C1）

**当前问题**：[chat.py:3440](../../backend/chat.py:3440) 只 extend `current_user_message` 和最终 `assistant_message` 进 history。中间所有 tool_call/tool_result 在 `current_turn_messages` 里活过当前轮就丢失。下一轮 `_build_provider_conversation` 拼出的 history 不含工具流转——模型对自己上轮调过哪些工具零记忆，是 Bug A 撒谎现象的放大器。

**实现选择**：HTML 注释格式 + 三层 sanitize（保留 v1 选 B 方案的核心，补 codex 评审的细节）。

#### 5.1 摘要格式

assistant message 持久化前，在 content 末尾追加（在 stage-ack/draft-action tag **之前**）：

```
<!-- tool-log
- web_search('猪猪侠 超人强 战力') ✓ 8 results
- fetch_url('https://baike.baidu.com/item/超人强...') ✓ 3.2 KB
- write_file('plan/data-log.md') ✗ read_file 未在本轮先调用
- read_file('plan/data-log.md') ✓
- edit_file('plan/data-log.md') ✓ 7/7 条达标
-->
```

格式约定见 [Appendix B](#appendix-b--tool-log-摘要格式参考)。

#### 5.2 配对算法（v2 新增明确）

[`current_turn_messages`](../../backend/chat.py:3170-3389) 不只是"assistant(tool_calls) + tool result"——还混入：
- malformed tool-call 重试隔板（[chat.py:3267-3281](../../backend/chat.py:3267) 的 assistant + user 隔板对）
- 自我修正反馈
- 缺写入反馈

简单的"前一条 assistant 配下面所有 tool 消息"会错配。明确算法：

```python
def _pair_tool_calls_with_results(self, current_turn_messages: List[Dict]) -> List[ToolPair]:
    """
    按 tool_call_id 严格匹配。算法：
    1. 遍历 current_turn_messages
    2. 遇到 role="assistant" 且有 tool_calls：记录每个 tool_call.id → tool_call meta
    3. 遇到 role="tool"：用 tool_call_id 查找对应的 tool_call meta，配对
    4. role="user" 的 retry 隔板、纯文本 assistant 跳过（不参与配对）
    """
    pending_calls: dict[str, dict] = {}  # tool_call_id → {name, args}
    pairs: list[ToolPair] = []
    for msg in current_turn_messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                tc_id = tc.get("id")
                if tc_id:
                    pending_calls[tc_id] = {
                        "name": tc.get("function", {}).get("name"),
                        "args": tc.get("function", {}).get("arguments"),
                    }
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            meta = pending_calls.pop(tc_id, None)
            if meta is None:
                continue  # 配不上就跳过
            try:
                result = json.loads(msg.get("content") or "{}")
            except json.JSONDecodeError:
                result = {"status": "error", "raw": msg.get("content")}
            pairs.append(ToolPair(name=meta["name"], args=meta["args"], result=result))
    return pairs
```

跳过纯文本 assistant 和 retry user 隔板（它们既无 tool_calls 也无 tool_call_id）。

#### 5.3 _insert_before_tail_tags 算法（v2 新增明确）

v1 spec 说"插入位置在 stage-ack/draft-action tag 之前"但没给算法。明确：

```python
def _insert_before_tail_tags(self, content: str, block_to_insert: str) -> str:
    """
    在 content 尾部所有连续 stage-ack / draft-action / draft-action-replace tag block 之前插入。
    算法：
    1. 用 stage_ack.StageAckParser._tail_anchor() 找到 last non-tag non-whitespace position
       （已有实现：stage_ack.py:166-190）
    2. 同样的 _tail_anchor 算法，scan_re 改为同时匹配 stage-ack + draft-action + draft-action-replace
    3. 在 tail_anchor 位置插入 block（用换行包裹）
    4. 如果 tail_anchor == len(content)（没有任何尾部 tag），直接 append 到末尾
    """
```

复用 stage-ack 的 `_tail_anchor` 思路，scan_re 改为联合匹配三种 tag。具体实现复用 [`stage_ack.py:166-190`](../../backend/stage_ack.py:166)。

边界 case 测试：
- 0 个尾部 tag → 直接 append
- 1 个 stage-ack → 在 stage-ack 之前
- 1 个 draft-action → 在 draft-action 之前
- 1 个 draft-action-replace（多行 block）→ 在 block 起始之前
- stage-ack + draft-action 混合 → 在两者之前
- 尾部只有空白 → 在最后非空内容之后

#### 5.4 三层 sanitize（v2 新增）

v1 spec 只说"前端 ReactMarkdown 注释自动隐藏"——codex 评审指出 ChatPanel 用裸 ReactMarkdown（[ChatPanel.jsx:718-720](../../frontend/src/components/ChatPanel.jsx:718)）行为不能保证；复制按钮（[ChatPanel.jsx:727-733](../../frontend/src/components/ChatPanel.jsx:727)）会把原始 content 直接复制；GET /conversation API 直接返回 raw content。

明确三层 sanitize：

| 层 | 位置 | 操作 |
|---|---|---|
| Layer 1: GET /conversation API | [main.py:368-377](../../backend/main.py:368) | 返回前 strip `<!-- tool-log...-->` HTML 注释（仅 assistant role） |
| Layer 2: 前端 render | [ChatPanel.jsx:718-720](../../frontend/src/components/ChatPanel.jsx:718) | 渲染前用同样 regex strip（兜底） |
| Layer 3: 前端 copy 按钮 | [ChatPanel.jsx:727-733](../../frontend/src/components/ChatPanel.jsx:727) | 复制前 strip（同 regex） |

**不需要** Layer for provider build——tool-log 注释**就是要喂给模型**。模型从历史里看到自己上轮调过什么工具。

抽共享 helper（v3 修订：兼容未闭合）：

```python
# backend
TOOL_LOG_COMMENT_RE = re.compile(
    r'<!--\s*tool-log'
    r'(?:[\s\S]*?-->|[\s\S]*$)',  # 闭合 -->，否则吞到字符串末尾
    re.IGNORECASE,
)

def strip_tool_log_comments(content: str) -> str:
    return TOOL_LOG_COMMENT_RE.sub("", content).rstrip()
```

```javascript
// frontend (utils/chatMaterials.mjs or similar)
const TOOL_LOG_COMMENT_RE = /<!--\s*tool-log(?:[\s\S]*?-->|[\s\S]*$)/gi
export function stripToolLogComments(content) {
  return content.replace(TOOL_LOG_COMMENT_RE, '').trimEnd()
}
```

**Regex 关键点**（v3 修订，对齐 codex round-2 评审）：
- 主分支 `[\s\S]*?-->` 匹配标准闭合的多行注释
- 备用分支 `[\s\S]*$` 匹配未闭合（流截断时 tool-log 起始但 `-->` 还没产生就被切断）→ 把从 `<!-- tool-log` 到字符串末尾全部吞掉
- 备用分支也覆盖 HTML 规范禁止但模型可能写出的"嵌套 `--`"——只要后面找到 `-->` 立刻闭合（`*?` 非贪婪），找不到就走备用分支吞到末尾

测试覆盖见 §Test Plan，包括：well-formed / multi-line / unclosed (truncated stream) / containing nested `--` 四种情况。

#### 5.5 跨多轮 / 撞 max_iterations 处理

**跨多轮**：tool-log 只追加**当前轮**的 pairs；多轮 tool-log 自然分布在多条 assistant 消息上，不累积。

**撞 max_iterations（20 条工具）**：附加全部 20 条到 assistant content（每条约 50 token，共约 1k token，可接受）。从 reality_test 数据看 20 条是 max_iterations 上限，不会超过。撞顶 turn 的 assistant 文本本身就是"工具调用轮次过多"兜底文案——附加 tool-log 帮助下一轮模型理解上轮为什么停了。

#### 5.6 A3 / C1 / stage-ack 副作用 — 完整执行顺序（v3 修订）

A3 要"空 assistant 不持久化"，C1 要"给 assistant 追加 tool-log"，stage-ack 要"在持久化前 parse + apply checkpoint side effect"。三者顺序必须明确，否则会出现：
- 先 append tool-log 再判空 → A3 永远不触发
- 先判空再 parse stage-ack → "只回 `<stage-ack>...`" 的 turn 被判空走 A3，**checkpoint 永久丢失**（codex round-2 评审指出的关键漏洞）
- 先持久化再 strip → conversation.json 留下控制 tag 污染

**v3 明确顺序**（六步）：

```python
# 1. 先 parse 所有控制 tag（不剥离，只识别）
stage_ack_events = stage_ack_parser.parse(assistant_message)
draft_action_events = draft_action_parser.parse(assistant_message)

# 2. 立刻执行 stage-ack 副作用（必须在判空之前）
#    即使 turn 只回了一条 <stage-ack>...</stage-ack>，checkpoint 也要正常落戳
for event in stage_ack_events:
    if event.executable:
        self._apply_stage_ack_event(project_id, event)
    # executable=False 的 tag 也要在下面 strip 掉，但不应用副作用

# 3. 同样执行 draft-action 副作用（写入 turn_context.draft_action_decision）
for event in draft_action_events:
    if event.executable:
        self._apply_draft_action_event(project_id, event)

# 4. strip 所有控制 tag，得到"用户可见正文"
visible_content = stage_ack_parser.strip(assistant_message)
visible_content = draft_action_parser.strip(visible_content)
visible_content = visible_content.strip()

# 5. 判断"用户可见正文"是否为空
if not visible_content:
    # 走 A3：不持久化空 assistant 文本；user message 入 history
    # 注意：stage-ack / draft-action 副作用已在步骤 2-3 执行，不丢
    fallback = self._finalize_empty_assistant_turn(...)
    yield {"type": "content", "data": fallback}
    return

# 6. 不空 → 追加 tool-log（在所有 tail tag 之前 — tag 已被 strip 但下面持久化用 stripped 版本）
#    注意：persisted content 是 stripped 版本（不含控制 tag），tool-log 直接追加到末尾即可
persisted_content = visible_content  # tag 已剥离
if current_turn_messages:
    persisted_content = self._append_tool_log_to_assistant(
        persisted_content, current_turn_messages
    )

# 7. 持久化（conversation.json 不含控制 tag，含 tool-log 注释）
history.extend([current_user_message, {"role": "assistant", "content": persisted_content}])
```

**关键设计要点**：
- stage-ack/draft-action 副作用先于空判断（修复 v2 漏洞）
- A3 判空基于"控制 tag 已剥离后的 visible content"
- tool-log 追加到 stripped 版本（无尾部 tag → §5.3 `_insert_before_tail_tags` 算法的"无 tail tag → 直接 append"分支生效）
- conversation.json 持久化的 content 是"无控制 tag + 含 tool-log 注释"——前端通过 §5.4 三层 sanitize 把 tool-log 注释吞掉

**实施说明（v4 新增，对齐 codex round-3 P2）**：当前 `_finalize_assistant_turn()`（[chat.py:6372-6414](../../backend/chat.py:6372)）只处理 stage-ack 一类。本 spec 落地时**必须把它扩展为统一编排器**，按上面 7 步顺序处理 stage-ack + draft-action + strip + empty check + tool-log + 持久化。这是结构性重构而不只是局部修补——实施 PR 应明确把 `_finalize_assistant_turn` 视为本节伪代码的实现归宿，避免"只在 spec 里写顺序、实施时分散到三处函数"的退化。

**关于 "`_insert_before_tail_tags` 在 stripped 版本上是 no-op tail tag" 的说明**：
v2 §5.3 算法是为"未 strip 的原始 assistant_message"设计的——那时 tail 还有 stage-ack/draft-action tag。v3 顺序把 strip 提前，所以 tool-log append 时尾部已无控制 tag，`_insert_before_tail_tags` 直接走 "no tail tags → append at end" 分支。算法本身不变，调用语义更简单。

**结论**：
- "只回 `<stage-ack>...`" 的 turn：stage-ack 副作用执行 → visible_content="" → 走 A3（不持久化空 assistant 文本，但 checkpoint 已落戳）
- "只回 `<draft-action>begin</draft-action>` 然后调 `append_report_draft`" 的 turn：draft-action 写入 turn_context → visible_content="" → 走 A3。下一轮模型看不到这条 turn 的 assistant 文本，但工具已经在本轮调过、tool-log 通过 turn 内 `current_turn_messages` 传给后续 iteration（不丢）
- "tool-only turn 完全不发 tag"：preflight 决策已落 turn_context；按 §4.8 v3 收紧规则，只允许 `append_report_draft` begin/continue；走 A3 不持久化

---

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| **A1**：`surface_to_user` 必填后所有现有 call site 都要改，落地工作量大 | Appendix C 列完整表 + 类型检查强制（pydantic 没有默认值会运行时报错）；测试覆盖每个 category 的渲染期望 |
| **A1**：dedupe 拆双套后某些 turn 可能同时发可见 + 隐藏两条 notice | 明确允许这种情况——可见 notice 给用户看决策、隐藏 notice 给模型纠错；测试覆盖"先发隐藏后发可见两条都出"的场景 |
| **A2**：`_render_progress_markdown` 接收 `stage_state` 后破坏现有调用方 | 改成可选参数 `stage_state=None`，缺省时降级为旧行为；[`_sync_stage_tracking_files`](../../backend/skill.py:1083) 内部调用全部传新参数 |
| **A2**：`quality_progress.target=0` 渲染 0/0 噪音 | 严格 `target>0` 才渲染，否则跳过；测试覆盖 |
| **A2**：模型把 `quality_hint` JSON 字段当噪音忽略 | quality_hint 是独立字段进 tool message JSON 字符串，模型看不看都不影响其他字段解析；progress.md 持续可见是双保险 |
| **A3**：连续两条 user role 在 Gemini 拒收 | provider build 时 `_coalesce_consecutive_user_messages` 合并；conversation.json 持久化层不动；测试用 mock Gemini provider 校验 |
| **A3**：合并 user 时 multipart content（image）混合纯文本处理错 | 算法显式处理 str+str → str；str/list 混合 → 全转 multipart array；测试覆盖三种组合 |
| **A3**：sanitize 误吞用户**真的写了**"（本轮无回复）"的消息 | sanitize 仅作用于 `role="assistant"` 且 content **完全等于**集合中字面量；user role 完全不动；测试覆盖 |
| **A3**：三层 sanitize 漏一层导致历史污染 | Layer 1/2/3 各有独立测试；reality_test 跑一遍对比清理前后 |
| **B1**：preflight 与 draft-action tag 决策冲突（preflight 说不能写、tag 说写） | preflight 优先级高于 tag——preflight reject 时 tag 全部 `executable=False, ignored_reason="preflight_blocked"` |
| **B1**：模型漏发 tag | preflight 兜底放行（has intent keyword + stage ok）；缺 tag 时调写工具走 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 兜底；前端按钮（如"开始写报告"）也是兜底 |
| **B1**：模型乱发 tag 误触发写正文 | 前置校验硬闸（stage / outline / 文件存在）+ tag 位置约束 + preflight reject 时 tag 整体作废 |
| **B1**：流式 tag 泄漏到前端 | 复用 stage-ack tail guard 策略，扫描表加入 `<draft-action` / `<draft-action-replace` 前缀 |
| **B1**：删除某常量后发现还有 caller | Phase 2 实施前必须 `grep -n CONSTANT_NAME backend/` 列全 caller 再删；保留清单见 §4.10 |
| **B1**：tool-only turn 缺 tag → 漏放行 | preflight `can_write_non_plan=True` + tool-only turn 兜底放行（记 warning） |
| **B1**：`section:LABEL` 模型用部分匹配（如"第二章"匹配"## 第二章 战力演化"）造成 ambiguous 反复重试 | SKILL.md 明确要求"用完整 heading 定位"+ ambiguous 时给 notice "请用完整 heading"+ 测试覆盖 |
| **B1**：mixed-intent 拆轮逻辑在 preflight 重构时退化 | 保留 `_secondary_action_families_in_message` / `_message_has_distinct_non_expansion_action` 等独立 helper；preflight 显式调用 |
| **C1**：tool-log HTML 注释被某些 markdown 解析器渲染显示 | 三层 sanitize（API + render + copy）+ 测试覆盖 ReactMarkdown 行为 |
| **C1**：复制按钮把 tool-log 复制出去 | Layer 3 sanitize 在复制 handler 显式调用；测试 |
| **C1**：tool-log 配对算法配错（malformed retry 隔板干扰） | `_pair_tool_calls_with_results` 严格按 tool_call_id 配对，跳过无 id 的消息；测试覆盖 retry 场景 |
| **C1**：`_insert_before_tail_tags` 在多种 tag 组合下插错位 | 复用 stage-ack `_tail_anchor` 思路；测试覆盖 6+ 种 tag 组合 |
| **C1**：模型把 tool-log 当作"我上轮做完了"的证据继续撒谎 | tool-log 包含 ✗ 失败明确告知；SKILL.md "工具错误处理"节加强"看到 ✗ 必须告知用户失败原因" |
| **跨 spec**：A1 / A3 / B1 / C1 实施顺序影响 | Rollout 分两个 PR + 各 PR 内部分步骤；Phase 2 灰度并行验证后再删 |
| **跨 spec**：A3 与 C1 顺序冲突（先空判断还是先 append tool-log） | §5.6 明确顺序：先 strip tag 判空 → 空走 A3，不空才 append tool-log |

---

## Rollout

**Phase 1 — A 批 + C1 一起合**（预计 2-3 天）

A1/A2/A3/C1 改动相对独立、测试可控，作为一个 PR：

1. A1 SystemNotice 加 `surface_to_user`（必填），audit 全部 call site；`_emit_system_notice_once` dedupe 拆双；服务端按 surface 过滤
2. A2 `_render_progress_markdown` 接收 stage_state；S2/S3 渲染质量进度行（target>0 才显示）；tool_result 追加 quality_hint
3. A3 抽 `_finalize_empty_assistant_turn` helper；三处持久化点统一调用；`_coalesce_consecutive_user_messages` 在 provider build；三层 sanitize（API + 前端 + provider）
4. C1 `_pair_tool_calls_with_results` + `_append_tool_log_to_assistant` + `_insert_before_tail_tags`（复用 stage_ack `_tail_anchor`）；三层 strip helper（backend + 前端 render + copy）；A3/C1 顺序明确
5. 跑全套测试（后端 + 前端）；确认无回归
6. reality_test 跑一遍验证：黄框消失、progress.md 显示 5/7、空回复给用户友好提示、tool-log 在 history 里能看到、复制不带 tool-log

**Phase 2 — B1 单独 PR**（预计 3-4 天）

B1 涉及 preflight 重构 + 新模块 + SKILL.md 改造 + 流式逻辑变更，独立验证。**分两步灰度**：

**Step 2a — 并行期（新旧并存，不删旧）**：
1. 新建 `backend/draft_action.py`（parser + 测试）
2. 改 SKILL.md §S4 + 附录（让 LLM 开始发 tag）
3. chat.py 改流式 tail guard（扫描表加 draft-action / draft-action-replace 前缀）
4. chat.py 加 draft-action event 解析 + 前置校验 + turn_context 写入
5. **保留** `_classify_canonical_draft_turn` 完整旧逻辑同时跑——但只用作"对照组日志"：
   - 新通道（preflight + tag）做实际决策
   - 旧通道结果**结构化落入** `conversation_state.json` 的 `events` 数组（type=`draft_decision_compare`），完整 schema（v4 扩展，能直接计算所有 cutover 指标）：
     ```json
     {
       "type": "draft_decision_compare",
       "turn_id": "<uuid>",
       "user_message_hash": "<sha1>",
       "old_decision": {"mode": "...", "priority": "...", ...},
       "new_decision": {"mode": "...", "priority": "...", "preflight_keyword_intent": "...", ...},
       "agreement": true,
       "divergence_reason": null,
       "tag_present": {"begin": false, "continue": true, "section": false, "replace": false},
       "fallback_used": false,
       "fallback_tool": null,
       "fallback_intent": null,
       "blocked_missing_tag": false,
       "blocked_tool": null,
       "new_channel_exception": null,
       "recorded_at": "<iso8601>"
     }
     ```
     字段说明：
     - `tag_present`：本轮是否出现 executable 的 begin/continue/section/replace tag（each bool）
     - `fallback_used`：本轮是否使用了 §4.8 tag-less fallback（即 `_record_tagless_fallback_event` 触发）
     - `fallback_tool` / `fallback_intent`：若 `fallback_used=true`，记录哪个工具走 fallback、preflight 推断的 intent
     - `blocked_missing_tag`：本轮是否因为缺 tag 被 `_gate_canonical_draft_tool_call` reject
     - `blocked_tool`：若 `blocked_missing_tag=true`，记录被 block 的工具名
     - `new_channel_exception`：v5 新增。新通道（preflight + parser + gate）抛出的 unexpected exception 摘要 `{stage: "preflight" | "parser" | "gate" | "side_effect", message: "<truncated>"}`；若新通道顺利跑完则为 `null`。**专为 cutover 表"新通道意外异常 = 0"指标设计**——脚本可直接 `count(events where new_channel_exception is not null)` 得到指标值，不再依赖后端日志 grep
   - **额外事件 type**：v5 新增 `draft_decision_exception`，仅当**新通道在 compare 事件还未生成前**就崩了（如 preflight 第一行抛 KeyError，导致旧通道也没机会跑）才记录。schema：`{type: "draft_decision_exception", turn_id, stage, exception_class, exception_message, recorded_at}`。脚本计算异常指标时数 `draft_decision_compare.new_channel_exception != null` + `draft_decision_exception` 事件总数。
   - **`tagless_draft_fallback` 独立事件**：§4.8 的 `_record_tagless_fallback_event` 仍然写一条独立事件（细粒度可观测），**但 cutover 指标不依赖它**——`draft_decision_compare.fallback_used` 已是真值源。独立事件仅供调试/未来分析。脚本不需要 join 两类事件即可计算所有 cutover 指标
   - 同时 `logging.info("[draft-decision-compare] turn=X agreement=Y")` 写后端日志便于 grep
6. **新建** `tools/draft_decision_compare_report.py` 脚本（gate 必需工件，单独 task）：
   - 输入：reality_test 项目的 `conversation_state.json` 列表
   - 输出：markdown 汇总表，至少包含：
     * 每条 `draft_decision_compare` 事件的关键字段（turn_id / agreement / divergence / 各 tag_present / fallback_used / blocked_missing_tag）
     * 五个 cutover 指标的实际计算值（agreement rate / 不一致 case 数 / "old better" case 数 / 受控之外 missing-tag turn 数 / 异常数）
     * 每个不一致 case 的 user_message + old_decision + new_decision 三栏对照
   - 必须能从结构化 schema 完全推导所有指标，无需查后端日志或人工记忆
7. 跑测试 + reality_test 跑 5 个真实会话，触发 begin/continue/section/replace + S0/S1 误触发场景

**Step 2a → Step 2b 切主条件**（v3 改可执行）：

人工 review 一份汇总 artifact（即 reality_test 那 5+ 个会话的 `conversation_state.json` 中所有 `draft_decision_compare` 事件），同时满足：

| 指标 | 阈值 | 来源 |
|---|---|---|
| 决策一致率 | ≥ 95% | `agreement=True` 占比 |
| 不一致 case 全数审查 | 100% | 每条 `agreement=False` 必须有人工评注：标记"new better" / "old better" / "tie" |
| "old better" case 数量 | 0 | 不允许新通道反退化 |
| 受控 tool-only fallback 之外的 missing-tag turn | 0 | 排除 §4.8 v3 收紧规则允许的 `append_report_draft` begin/continue 无 tag 场景；其余任何 `edit_file` / section / replace 无 tag 都算系统性 missing-tag，必须为 0。**计算口径**：从 `draft_decision_compare` artifact 数 `blocked_missing_tag=true` 的事件总数，应为 0。`fallback_used=true && fallback_tool="append_report_draft"` 的事件不计入（受控范畴） |
| 新通道意外异常 | 0 | preflight / parser / gate / side_effect 抛 unexpected exception 数量。**计算口径**（v5 明确）：从 artifact 数 `count(draft_decision_compare events where new_channel_exception != null) + count(draft_decision_exception events)`，应为 0。`new_channel_exception` 是 compare 事件的字段（新通道跑完了但中途有异常）；`draft_decision_exception` 是独立事件类型（新通道崩到没产出 compare 事件）。两者并列、互不重叠 |

**Artifact 形式**：跑完 5 会话后用脚本 `tools/draft_decision_compare_report.py`（本 spec 实施时新增）从 conversation_state.json 抽 `draft_decision_compare` 事件、生成 markdown 汇总表给人工 review；review 结论手动记录为本 spec PR 的一段 comment。

**通过 review 后才允许进入 Step 2b**。

**Step 2b — 切主 + 删除**：
1. 把 `_classify_canonical_draft_turn` rename 为 `_preflight_canonical_draft_check`，删除内部细分逻辑
2. 删除 §4.10 列出的常量和 helper
3. 跑测试 + reality_test 跑三轮验证：用户说"开始写报告吧"/"继续写"/"把第二章重写"/"把 X 改成 Y" 四种表达都正确触发
4. 验证 SKILL.md 教导效果：模型在这四种表达下都自发发 tag

**Phase 3 — 重打包 + 三轮 smoke test**

- `build.ps1` 产出 `dist\咨询报告助手\`
- reality_test 走完 S0 → S7 一遍
- 从干净项目重新跑一次，验证用户体验

**回滚预案**：
- A 批：每个改动有独立 feature flag（如 `SYSTEM_NOTICE_SURFACE_TO_USER_ENFORCED`），回滚改 flag 即可
- C1：三层 sanitize 各层独立，单层关掉不影响其他
- B1 Step 2a：旧逻辑还在，新通道关掉即可回滚
- B1 Step 2b：删除是危险操作，必须等 Step 2a 切主条件全满足；万一删后出问题，git revert Step 2b commit

---

## Test Plan

**`tests/test_chat_runtime.py`**：

A1 SystemNotice 分层（v2 补全）：
- `surface_to_user is required parameter` — 不传抛 TypeError
- `existing notice categories all explicitly tagged surface_to_user` — 跑遍全部 call site
- `read_before_write notice has surface_to_user=False`
- `web_search_without_fetch notice has surface_to_user=False`
- `stage_blocked notice has surface_to_user=True`
- `s0_write_blocked notice has surface_to_user=True`
- `non_plan_write_blocked notice has surface_to_user=True`
- `report_draft_path_blocked notice has surface_to_user=True`
- `report_draft_destructive_write_blocked notice has surface_to_user=True`
- `checkpoint_prereq_missing notice has surface_to_user=True`
- `stage_keyword_prereq_missing notice has surface_to_user=True`
- `s0_tag_soft_gate notice has surface_to_user=True`
- `stage_ack_prereq_missing notice has surface_to_user=True`
- `checkpoint_forge_blocked notice has surface_to_user=True`
- `analysis_refs_missing notice has surface_to_user=False`
- `data_log_format_hint notice has surface_to_user=False`
- `tool_param_validation_error has surface_to_user=False by default`
- `tool_execution_error has surface_to_user=False by default`
- `dedup splits user vs internal — both can fire same turn`
- `dedup user notice not blocked by prior internal notice`
- `dedup internal notice not blocked by prior user notice`
- `same-class internal notice still deduped to one per turn`
- `same-class user notice still deduped to one per turn`
- `SSE stream filters surface_to_user=False from frontend events`
- `non-stream chat endpoint response filters surface_to_user=False`

A3 空 assistant 兜底（v2 新增/修订）：
- `_finalize_empty_assistant_turn does not append assistant to history`
- `_finalize_empty_assistant_turn appends user message to history`
- `_finalize_empty_assistant_turn returns USER_VISIBLE_FALLBACK`
- `_finalize_empty_assistant_turn records empty_assistant event with diagnostic`
- `streaming path uses _finalize_empty_assistant_turn helper`
- `non-streaming path uses _finalize_empty_assistant_turn helper`
- `early finalize path uses _finalize_empty_assistant_turn helper`
- `_coalesce_consecutive_user_messages merges two str into one with \n\n`
- `_coalesce_consecutive_user_messages handles str + multipart array`
- `_coalesce_consecutive_user_messages handles two multipart arrays`
- `_coalesce_consecutive_user_messages does not modify original history` — defensive copy
- `_coalesce_consecutive_user_messages applied in _build_provider_turn_conversation`
- `legacy 本轮无回复 in conversation.json sanitized in GET /conversation response`
- `legacy 本轮无回复 in conversation.json sanitized in provider build`
- `legacy USER_VISIBLE_FALLBACK in conversation.json sanitized in GET /conversation`
- `sanitize does not affect user role messages with same content`
- `consecutive empty assistants do not break next turn API call`

B1 draft_action（v2 大改）：
- parser 基础：begin / continue / section / replace 四种 intent 解析
- parser 基础：未知 intent 忽略 + warning
- parser 基础：multi tag 顺序保留
- 位置约束：fenced / inline / blockquote / non-tail / non-independent-line 各自 ignored_reason 正确
- 位置约束：尾部空白容忍
- 位置约束：draft-action-replace 多行 block 起止行独立才合法
- 流式 tail guard：单 simple tag 前缀不泄漏
- 流式 tail guard：单 replace tag 前缀不泄漏
- 流式 tail guard：multi tag 尾部块（包括 stage-ack + draft-action 混合）总长 >128 字节不泄漏
- 强关键词兜底：begin / continue 命中时 preflight `can_write_non_plan=True`
- 强关键词兜底：section / replace 不在兜底列表（确认）
- 前置校验：S0/S1 stage tag 全部 `executable=False, ignored_reason="stage_too_early"` + system_notice
- 前置校验：outline 未确认 → tag 全部失效 + notice
- 前置校验：continue 时 draft 不存在 → 自动降级为 begin
- 前置校验：section 时 LABEL 在 draft 找到唯一 heading → executable
- 前置校验：section 时 LABEL 部分匹配两个 heading → ambiguous → executable=False + notice
- 前置校验：section 时 LABEL 完全找不到 → executable=False + notice
- 前置校验：replace 时 OLD 唯一存在 → executable
- 前置校验：replace 时 OLD 不存在 → executable=False + notice
- 前置校验：replace 时 OLD 在 draft 中出现多次 → executable=False + notice
- preflight 主逻辑：
  - "开始写报告吧" + S4 → preflight `can_write_non_plan=True`
  - "开始写报告吧" + S0 → immediate_reject + notice
  - "继续写" + S4 + outline 已确认 → can_write
  - "你好" → preflight 不触发
  - mixed intent "继续写并导出" → split-turn message
- 端到端：用户"开始写报告吧" → 模型发 begin tag → 工具放行
- 端到端：用户"继续写第二章" → 模型发 section tag → 工具放行
- 端到端：用户"把 X 改成 Y" → 模型发 replace tag → 工具放行
- 端到端：模型只调工具不输出文本（tool-only turn）+ `append_report_draft` begin → preflight 兜底放行 + warning log
- **端到端（v3 收紧）：tool-only turn 不发 tag 调 `edit_file content/...` → reject + notice "请先发 draft-action tag"**
- **端到端（v3 修订）：模型发 `<draft-action>section:第二章</draft-action>` 但 draft 文件不存在 → reject + `CANONICAL_DRAFT_NO_DRAFT_MESSAGE`（不是 "heading not found"）**
- 端到端：模型发 malformed tag（`<draft-action>begin\n` 截断） → parser 不识别 → 不执行
- 端到端：模型发 well-formed tag 但 stage 不对 → reject + notice
- **端到端（v3 新增）：assistant 只回 `<stage-ack>outline_confirmed_at</stage-ack>` 一条 tag → checkpoint 落戳 + 走 A3 不持久化空 assistant 文本（验证 §5.6 顺序）**
- 端到端：旧 classifier vs 新 tag 决策对照（Phase 2a 灰度期）— 一致 / 不一致 case 各覆盖
- **结构化对照日志（v3 新增）：每个 turn 在 `conversation_state.json` 写一条 `draft_decision_compare` 事件，含完整对照字段**
- 流缓冲回归：现有 `required_write_snapshots` 在 preflight 拒绝时仍能正确缓冲（防回归）
- 删除范围：`grep` 静态测试确认 `REPORT_BODY_FIRST_DRAFT_KEYWORDS` 删除后无 caller
- **死代码清理（v3 新增）：`grep` 静态测试确认 `_regex_has_clean_report_body_intent` / `_has_explicit_report_body_write_intent` / 三个 regex 常量删除后无悬空引用**
- **fallback 信号源约束（v4 新增）：`_gate_canonical_draft_tool_call` 的 `append_report_draft` no-tag fallback 仅依赖 `decision["preflight_keyword_intent"]`；通过 monkeypatch 把 preflight 偷偷加回 `intent_kind="section"` 字段时，gate 仍然 reject——验证 fallback 不会通过其他字段渗入**
- **`preflight_keyword_intent` 字段约束（v4 新增）：`_preflight_canonical_draft_check` 输出的 `preflight_keyword_intent` 永远 ∈ {"begin", "continue", None}；遍历 100+ 条用户消息样本（含 section / replace 表达），确认没有泄漏 "section" / "replace" 值**
- **artifact 自洽（v4 新增）：用最小 `conversation_state.json` fixture（含 `draft_decision_compare` 事件）跑 `tools/draft_decision_compare_report.py`，验证生成的 markdown 表能直接读出五个 cutover 指标的精确值，不依赖任何外部数据源**
- **脚本 smoke test（v4 新增）：`tools/draft_decision_compare_report.py` 接 1-2 份典型 `conversation_state.json` 输入能稳定输出汇总 markdown；包含 `agreement=False` / `fallback_used=True` / `blocked_missing_tag=True` 三种事件的混合场景**
- **异常指标可推导（v5 新增）：fixture 含 `draft_decision_compare.new_channel_exception != null` 事件 + 独立 `draft_decision_exception` 事件，脚本输出的"异常数"指标值精确等于两类总和**
- **gate 接口契约（v5 新增）：`_gate_canonical_draft_tool_call` caller 必须传入 `project_id` 和已 parse + 校验后的 `DraftActionEvent` 列表；签名缺参数时类型检查报错；`tags` 传入未校验事件（`executable=False`）时不应放行

C1 工具历史（v2 补全）：
- `_pair_tool_calls_with_results` basic — 一对 tool_call/tool_result 配对
- `_pair_tool_calls_with_results` skips text-only assistant
- `_pair_tool_calls_with_results` skips retry user barrier
- `_pair_tool_calls_with_results` skips tool message with no matching id
- `_pair_tool_calls_with_results` handles malformed JSON tool result
- `_pair_tool_calls_with_results` empty current_turn_messages → empty pairs (no-op)
- `_pair_tool_calls_with_results` 多组 retry + 真实 call 混合配对正确
- `_append_tool_log_to_assistant` ✓/✗ 符号正确
- `_append_tool_log_to_assistant` truncates long args to 80 chars
- `_append_tool_log_to_assistant` does not leak full result content
- `_insert_before_tail_tags` no tail tags → append at end
- `_insert_before_tail_tags` only stage-ack → before stage-ack
- `_insert_before_tail_tags` only draft-action simple → before tag
- `_insert_before_tail_tags` only draft-action-replace → before block start
- `_insert_before_tail_tags` mixed stage-ack + draft-action → before both
- `_insert_before_tail_tags` only trailing whitespace → after last non-whitespace
- A3/C1 ordering: empty visible content → A3 path, no tool-log append
- A3/C1 ordering: non-empty content → tool-log appended
- A3/C1 ordering: content with only stage-ack tag → strip tag, judge as empty → A3 path
- next turn provider conversation contains tool log in previous assistant content
- frontend `stripToolLogComments` strips well-formed comment
- frontend `stripToolLogComments` strips multi-line comment
- **frontend `stripToolLogComments` handles unclosed comment (truncated stream)** — 验证 §5.4 v3 备用分支吞到末尾
- frontend `stripToolLogComments` handles nested -- inside comment
- backend `strip_tool_log_comments` matches frontend behavior on all 4 cases
- GET /conversation strips tool-log from assistant message
- copy button strips tool-log before writing to clipboard
- ReactMarkdown render strips tool-log via remark plugin (or pre-strip)

**`tests/test_skill_engine.py`**：

A2 progress.md 质量进度：
- `s2 progress renders 5/7 quality progress with target>0`
- `s3 progress renders analysis ref count`
- `s0 / s1 / s4 / s5 / s6 / s7 progress does not render quality progress line`
- `quality progress not rendered when target = 0`
- `quality progress not rendered when stage_state is None`
- `quality progress not rendered when quality_progress field absent`

A2 tool_result quality_hint：
- `write data-log appends quality_hint when stage S2`
- `edit data-log appends quality_hint when stage S2`
- `write analysis-notes appends quality_hint when stage S3`
- `write data-log does NOT append quality_hint when stage S4`
- `quality_hint absent when target=0`
- `write other plan file does not append quality_hint`
- `write content/report_draft_v1.md does not append quality_hint`

**`frontend/tests/`**：
- `chatPresentation.test.mjs`：
  - `system_notice with surface_to_user=false not rendered`
  - `system_notice with surface_to_user=true rendered as yellow box`
  - `tool-log HTML comment stripped from assistant content`
  - `legacy 本轮无回复 not displayed`
  - `copy button output strips tool-log`
  - `assistant content with quality_progress in progress.md rendered`

**回归基线**：现有后端测试 + 前端测试全部应 pass；新增测试预计 ~120 条（A1: 25, A2: 13, A3: 17, B1: 40, C1: 25, frontend: 6）。

---

## Alternatives Considered

1. **B1 用 OpenAI function-calling tool 替代 XML tag**
   - 模型调 `start_draft(intent, section?, old?, new?)` 工具
   - 优点：tool schema 可校验，evidence 可审
   - 缺点：(a) gemini-3-flash 在 newapi 中转通道下并行 tool_call 会被合并（[chat.py:5913 注释](../../backend/chat.py:5913)），增加 tool 数量提高踩坑概率；(b) 与 stage-ack 不一致，模型需要学两套机制；(c) 工具数量越多模型挑工具越蠢
   - 已否

2. **B1 完全删除 `_classify_canonical_draft_turn`，纯靠 tag**（v1 spec 原方案）
   - 优点：删 280 行，看着干净
   - 缺点：tag 在 LLM 回复内输出，但 turn_rule / can_write_non_plan / required_write_snapshots / immediate_reject / 流式缓冲都是 turn 开始前要决定的——tag 来不及驱动这些
   - 已否，v2 改"preflight 粗 + tag 细"

3. **A3 持久化 hidden assistant barrier 占位**（避免连续 user）
   - 优点：history 结构上严格交替
   - 缺点：要新增 metadata 字段、要在所有读取点 sanitize、conversation.json schema 改动大
   - 已否，选 v2 的"persist 时不动、provider build 时合并"方案

4. **C1 持久化完整 SDK tool_calls + tool 消息**
   - history 直接走 OpenAI 标准格式
   - 优点：跟 SDK 对齐
   - 缺点：(a) tool_result 全文（web_search 整页）token 爆炸；(b) conversation.json schema 大改，老历史迁移复杂；(c) gemini-3-flash 对纯文本理解优于结构化 tool_call 重放
   - 已否，选 B（HTML 注释摘要）

5. **C1 在 system prompt 加"上轮工具记录"块**
   - 优点：完全隔离不动 history
   - 缺点：(a) 跨多轮维护难；(b) system prompt 已经 12KB，再加 section 推爆"系统提示越长指令权重越低"问题；(c) 多轮工具流转无法自然对应到对应的 assistant 文本
   - 已否

6. **C1 把 tool-log 放进 conversation_state.json sidecar 而不是 assistant content**
   - 优点：不污染 conversation.json 的"用户对话"含义
   - 缺点：(a) provider build 时要把 sidecar 数据 inject 回历史，新增数据流复杂度；(b) 三层 sanitize 已经能解决 user-visible 污染
   - 已否，选 v2 的"assistant content + 三层 strip"

7. **A2 阈值通过独立 `<system-status>` tag 注入**
   - 优点：结构化、明确
   - 缺点：增加新 tag 通道，与 stage-ack/draft-action 三套并存增加复杂度；progress.md 已经在 system prompt 里，加一行更直接
   - 已否

8. **A1 删掉 read-before-write notice（既然 SKILL.md 已说）**
   - 优点：改动最小
   - 缺点：模型从 tool_result 收到的 reason 文本也没了 → 模型没法立刻自我纠正 → 多耗一两轮才修对；SKILL.md 是"应然"约束，notice 是"实然"反馈，两者互补
   - 已否，选 A1 的"分层"方案

9. **A1 `surface_to_user` 给默认值 True 兼容旧 call site**（v1 原方案）
   - 优点：旧代码不用改
   - 缺点：依赖人工 audit 不靠谱；codex 评审实测漏列 7 处 call site；改必填后类型检查/运行时报错强制 audit
   - 已否，v2 改必填

---

## Resolved Questions

v1 三个 Open Questions 在 v2 全部消解：

**Q1: `<draft-action>replace>` 的 OLD/NEW 用什么分隔？**
- **决定**：嵌套 XML 子节点 `<draft-action-replace><old>...</old><new>...</new></draft-action-replace>`，不用 `|` 分隔符
- **理由**：`|` 在正文中常见，转义地狱；嵌套 XML 跟 stage-ack 一样属于"模型已经熟练的语法"；OLD/NEW 长度可放宽到 1000 字符（小节级替换）

**Q2: tool-log 撞 max_iterations 上限时是否仍附加？**
- **决定**：仍附加全部（每行截断）
- **理由**：异常 turn 反而更需要日志帮助下一轮模型理解；20 条 × 50 token ≈ 1k token 可接受；现有 max_iterations 已封顶在 20，不会爆炸

**Q3: A3 sanitize 是否需要扫 `memory_entries`？**
- **决定**：不扫
- **理由**：memory_entries 来自 _persist_successful_tool_result 的 metadata content，不含 assistant 聊天文本；真正污染面是 conversation.json 的历史 assistant message → §3.3 三层 sanitize（GET /conversation + 前端 + provider build）覆盖

---

## References

- [docs/superpowers/specs/2026-04-21-s0-interview-and-stage-ack-design.md](2026-04-21-s0-interview-and-stage-ack-design.md) — 本 spec 直接借鉴 stage-ack 模式
- [docs/superpowers/specs/2026-04-23-report-draft-write-reliability-design.md](2026-04-23-report-draft-write-reliability-design.md) — 当前 `_classify_canonical_draft_turn` 的设计源头
- [docs/superpowers/specs/2026-04-24-report-draft-tool-contract-design.md](2026-04-24-report-draft-tool-contract-design.md) — `append_report_draft` 工具契约
- [docs/current-worklist.md](../../current-worklist.md) — 本 spec 解决的五个 Bug 的来源
- reality_test 项目 `proj-0750fc3f5ea0` 2026-05-04 会话 — 五个 Bug 的现场证据
- memory `feedback-minimal-ai-questioning.md` — 影响 §Risk Mitigation 中"用户行动指引"措辞设计
- Gemini API multi-turn 文档：https://ai.google.dev/api#multi-turn-conversations — 角色交替强制要求

---

## Appendix A — draft-action tag 完整规范

**Simple 形式**（intent ∈ {begin, continue, section}）：

```
<draft-action>begin</draft-action>                        # 首次起草
<draft-action>continue</draft-action>                     # 续写
<draft-action>section:第二章 战力演化</draft-action>        # 重写指定章节（用完整 heading 定位）
```

**Replace 形式**（嵌套 XML 子节点）：

```
<draft-action-replace>
  <old>原文片段</old>
  <new>新文本</new>
</draft-action-replace>
```

**KEY 取值**：
- `begin` — 模型即将首次调用 `append_report_draft` 创建草稿
- `continue` — 模型即将调用 `append_report_draft` 在现有草稿末尾追加（draft 不存在自动降级 begin）
- `section:LABEL` — 模型即将调用 `edit_file` 重写指定章节（LABEL 必须能在 draft 中唯一找到 heading）
- `replace` — 模型即将调用 `edit_file` 做精确替换（OLD 必须在 draft 中唯一存在）

**位置 / 剥离规则**：完全沿用 [stage-ack 设计 §2](2026-04-21-s0-interview-and-stage-ack-design.md#L65)。replace 的多行 block 要求"起始行独立 + 终止行独立"。

**剥离正则**（合并 simple + replace）：

```python
DRAFT_ACTION_STRIP_RE = re.compile(
    r'<draft-action>[^<]*</draft-action>'
    r'|<draft-action-replace>[\s\S]*?</draft-action-replace>',
    re.IGNORECASE,
)
```

---

## Appendix B — tool-log 摘要格式参考

每条工具调用的摘要格式：

```
- TOOL_NAME(SHORT_ARGS) ✓ SUMMARY        # 成功
- TOOL_NAME(SHORT_ARGS) ✗ ERROR_BRIEF    # 失败
```

**SHORT_ARGS 截断规则**：
- 路径类参数：取相对路径全文（`plan/data-log.md`）
- 搜索词：截断到 30 字符（`'猪猪侠 超人强 战力对比...'`）
- URL：取 host + 路径前 40 字符（`baike.baidu.com/item/超人强...`）
- 多参数：仅显示第一个主参数

**SUMMARY 格式**：
- `web_search`：`N results`
- `fetch_url`：`X.Y KB`
- `write_file` / `edit_file` / `append_report_draft`：路径，可选附 quality_hint（`5/7 条达标`）
- `read_file`：路径
- `read_material_file`：material id

**ERROR_BRIEF 格式**：error message 截断到 60 字符，去除技术细节（栈帧、内部字段名）。

**长度控制**：每行硬上限 120 字符（含 `- ` 前缀和 ✓/✗）；超长用 `...` 省略。

---

## Appendix C — `surface_to_user` audit 完整表（v3 按 call site）

落地时每条 `_emit_system_notice_once` 调用必须显式标 `surface_to_user`（v2 改必填）。下表是 v3 按 `grep -n "_emit_system_notice_once" backend/chat.py` 当前 18 个 call site 完整列出（v1/v2 表只到 category 级，会漏掉同 category 不同 call site 的判断）：

| chat.py 行 | category | 调用语境 | surface_to_user | 理由 |
|---|---|---|---|---|
| 4379 | `write_blocked` | tool 参数验证 ValueError | False | 模型自己改参数即可 |
| 4389 | `write_blocked` | 工具执行 Exception 兜底 | False | 默认隐藏；严重 error（API key 失效等）由 caller 显式开 True |
| 4524 | `report_draft_path_blocked` | 写错路径（非 canonical draft path） | True | 用户该知道模型走错路径 |
| 4538 | `s0_write_blocked` | S0 阶段写禁止文件 | True | 用户决策"是否跳过访谈" |
| 4551 | `non_plan_write_blocked` | 非 plan 写但未授权 | True | 用户决策"是否确认大纲 / 继续" |
| 4560 | `fetch_url_gate_blocked` | web_search 后未 fetch_url | False | 模型流程纠错 |
| 4577 | `report_draft_destructive_write_blocked` | 破坏性写（followup mutation_limit） | True | 用户该知道破坏性操作被拦 |
| 4591 | `report_draft_destructive_write_blocked` | 破坏性写（mutation limit） | True | 同上 |
| 4604 | `write_blocked` | read_before_write 提示 | False | 模型流程纠错 |
| 4619 | `report_draft_destructive_write_blocked` | 破坏性写（destructive_write_error） | True | 同上 |
| 4634 | `checkpoint_forge_blocked` | 试图直接写 stage_checkpoints.json | True | 阶段 checkpoint 篡改尝试被拒，用户需要看到（指引去点工作区按钮） |
| 4649 | `write_blocked` | self_signature 校验失败 | True | reason 文本要求"请联系用户在右侧工作区完成对应的确认"——必须给用户看 |
| 4662 | `analysis_refs_missing` | analysis-notes.md 缺 DL 引用 | False | 模型补引用即可 |
| 4688 | `data_log_format_hint` | data-log.md 格式不对 | False | 模型按格式纠正即可 |
| 6030 | `checkpoint_prereq_missing` | 阶段前置文件缺 | True | 阶段前置文件缺失，用户需要补 |
| 6405 | `stage_keyword_prereq_missing` | 强关键词推进但前置不通 | True | 用户需要决策 |
| 6422 | `s0_tag_soft_gate` | S0 软门槛拒绝 tag | True | 用户需要决策"是否真的跳过访谈" |
| 6443 | `stage_ack_prereq_missing` | stage-ack 前置文件缺 | True | 用户需要补 |

**Note**：上表行号会随实施而漂移。落地时**实施步骤**：
1. 跑 `grep -n "_emit_system_notice_once(" backend/chat.py` 重新生成行号
2. 对照本表逐行确认每个 call site 加上 `surface_to_user=...`
3. 任何**新增** `_emit_system_notice_once` 调用必须在 PR 中 explicit 标 surface 值（必填参数会强制 type check 报错）

**v3 关键修正**（v1/v2 漏判）：
- `4649` 是 `write_blocked` category 但**必须 surface=True**（self_signature 校验失败要引导用户去工作区点确认）；不能因为它是 `write_blocked` category 就当成内部纠错
- 这就是为什么本表按 call site 列，不按 category 列——同 category 不同 call site 可以有不同 surface 决定
