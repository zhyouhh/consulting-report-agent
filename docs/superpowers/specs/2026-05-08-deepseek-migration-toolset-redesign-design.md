# DeepSeek V4 Pro Migration — Toolset Redesign + Guard Layer Simplification

**Date**: 2026-05-08
**Status**: Draft, awaiting codex spec review loop until APPROVED.
**Supersedes**: spec `2026-05-05-report-tools-redesign-design.md` 中的 "4 个专用写正文工具" 部分（在仅 3 天后被强模型迁移结果取代）。该 spec 中的 6 个 invariant helpers + claim-only retry 机制 + read-before-write+mtime 跟踪保留。
**Builds-on**: 2026-05-07 已完成的服务端 managed proxy `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro` 切换 + 本地代码 3 处默认模型名更新。

## TL;DR

在 managed channel 由 `gemini-3-flash` → `deepseek-v4-pro`（reasoning 模型）切换基础上，**做一次性的工具集 + guard 控制层简化**。核心思路：模型能力升级后，原本为 gemini 弱模型设计的"工具家族锁 + 关键词门禁 + 意图预测器" 不再必要；4 个写正文专用工具中的 3 个可以合并到通用 `edit_file` / `write_file`（加 path-based dispatcher），保留 `append_report_draft` 唯一 vertical specialty 守住"产出新内容" generative 路径不复述旧全文。

工具数从 10 → 7（删 3 旧专用 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`，增强 `edit_file` / `write_file` 加 canonical draft path-based dispatcher），guard 控制层 ~1024 行 → ~300 行（70% 缩减）。同时一次性吸收 2026-05-07 E2E 实测发现的 6 个产品/工程问题：`<think>` 折叠、S0 强制澄清门槛、search 配额放宽、mutation_limit 提升、context tier 固定 256k、打包基础设施（log/version/start menu）、test 分层。

净效果：删除幅度 ~700 行后端代码（不含测试，砍掉的旧逻辑量），含 add 后净行数 ~210；4 个独立 commit 拆分（Commit 0-3）。

## §1 背景

### 1.1 模型切换的实际触发

2026-05-07 五路 agent 调研 + 用户决策：把 managed 通道默认模型从 `gemini-3-flash` 切到 `deepseek-v4-pro`。当天完成：

- 服务器 managed proxy: `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro`
- 容器重建并验证 `/client/v1/models` 返回 `deepseek-v4-pro`
- `backend/config.py` `DEFAULT_MANAGED_MODEL = "deepseek-v4-pro"`
- `frontend/src/components/SettingsModal.jsx` 默认模型名更新
- `managed_proxy/app.py` `DEFAULT_ALLOWED_MODEL = "deepseek-v4-pro"`
- 文档同步（`CLAUDE.md`、`AGENTS.md`、`docs/managed-proxy-deployment.md`）

### 1.2 E2E 实测发现的真问题（2026-05-07 晚 → 2026-05-08）

切换后跑了一次 S0→S1 实测（项目 "DeepSeek-V4-Pro 实测"，主题 "中国新能源汽车出口现状与机会分析"），暴露 6 个非模型问题：

1. **打包态升级路径破洞**：老用户 `~/.consulting-report/config.json` 里 `managed_model="gemini-3-flash"` 被新版 exe 当作配置保留，第一条 chat 直接 400。
2. **`<think>` 标签泄露**：DeepSeek reasoning 模型在 `content` 里夹 `<think>...</think>`，前端直接渲染。违反 SKILL.md "不暴露 AI reference / 内部推理" 写作约束。
3. **per-turn 搜索配额太严**：`per_turn_searches=2` 是为 gemini 防滥用设的，DeepSeek reasoning 一轮典型 3 次（主搜 → 单市场 focus → 不同角度）就被掐死。
4. **S0 确认门槛被动触发**：用户首条说"按你判断推进"，model 第二轮试图写 outline.md 才被 SKILL §S0 挡死，reasoning 反复绕。
5. **打包态 stderr 完全吞没**：FastAPI 后台线程崩溃时用户只看到"Failed to fetch"，无 traceback 可查。
6. **packaged exe 元数据空**：FileDescription / ProductName / CompanyName 全空，Windows 搜不到、computer-use resolver 解析失败。

E2E 同时验证模型行为：DeepSeek V4 Pro 9 次工具调用 schema 100% 正确，包括 read_file / write_file / web_search / fetch_url 全对；流式响应稳定；reasoning 识别配额触底主动降级；上游 SSE 偶发的 malformed tool_calls 由 backend 已有兜底自愈。**模型本身没问题，问题全在产品/系统侧。**

### 1.3 4 工具设计的过时性

2026-05-05 的 4 工具 redesign 解决的是 gemini-3-flash 三个具体弱点：
- 无法复述 >1500 字章节原文
- 分不清章节边界
- 声称写了但没写

DeepSeek V4 Pro reasoning 能力把前 2 个弱点消除（实测），第 3 个仍存在但通过 turn-end claim retry 已经覆盖。**4 个工具中的 3 个（rewrite_section / replace_text / rewrite_draft）失去存在理由**——它们都是 mutating 操作的不同粒度，可以用通用 `edit_file(old, new)` + path-based dispatcher 表达。

`append_report_draft` 是**唯一例外**：它解决"续写下一章"时模型不需要 dump 旧全文（节省 5000+ tokens/章），是 generative mental model 的 vertical specialty，保留有据。

### 1.4 6 路 brainstorm 决策路径

2026-05-08 brainstorm 期间走过 B1（保 4 工具删 guard） → B4（合并 2 通用工具） → B5（3 工具 + smart dispatch） → B6（纯主流对齐 2 工具） → **B5'（最终采纳）**。3 路 agent + codex 独立挑战的关键反驳推动收敛：

- B6 派（mainstream alignment）：8/10 信心，但 codex 反驳指出"续写下一章"用 generic Write 要 dump 旧全文，token 经济学不成立
- B5'（vertical defender）：1 个 specialty + 通用 Edit/Write 是 vertical app 边际收益最高的切点
- Codex final verdict：B5' 7.2/10，理由是"为了工具洁癖砍掉唯一 generative affordance，不值"

最终采纳 B5'。

## §2 范围

### 2.1 In Scope（一次性交付）

| # | 项目 | 决策 |
|---|---|---|
| B | 工具集精简 | B5'：复用 `edit_file` + `write_file`（加 canonical draft dispatcher）+ 保留 `append_report_draft`，删 3 个旧专用工具 |
| C | S0 强制澄清门槛 | first-turn 硬 gate + 白名单工具集 |
| D | `<think>` 折叠 | backend stream channel 切分 + 前端折叠面板 |
| E | mutation_limit | 1 → 3 |
| F | guard 控制层删除 | ~700 行净删 + 完整无残留 |
| G | search 配额 | `per_turn_searches` 2 → 3 |
| I | context tier | 固定 256k effective（`tier_1m_eff_256k`） |
| J | 打包基础设施 | log file + version_info + start menu shortcut |
| K | test 分层 | slow markers + 默认串行 fast tests + 可选显式 pytest-xdist |

### 2.2 Out of Scope（推后处理）

- **UI 重构**：3 套设计稿在 `docs/design_UI.pdf`，等当前 spec 跑通流程后单独立项。`<think>` 折叠的视觉细节作为 UI 重构输入，不在本 spec 内细化。
- **`delete_section` / `move_section` 独立工具**：B5' 下用 `edit_file(old=锚点, new="")` 表达 delete、`mutation_limit=3` 的两步 edit_file 表达 move，不需要新工具。
- **stage-aware context tier**：固定 256k 已 covered。
- **图片附件能力按 managed_model 分流**（`docs/current-worklist.md` #4）：等 UI 重构一并做。
- **CLAUDE.md / AGENTS.md 加 test 约定**：明确否决；约定靠 plan 中每个 task 单独写"只跑必要 test"。

### 2.3 Goals / Non-goals

**Goals**:
1. 砍掉 ~700 行 gemini 时代的 guard 控制层债务
2. 工具集对齐 vertical 应用最优形态（1 specialty + 2 通用）
3. 一次性吸收 E2E 测出的 6 个真问题
4. 升级路径平滑（heal_stale_managed_model 防老用户撞 400）
5. AI 协作开发体验改善（test 分层、log 可见、start menu 注册）

**Non-goals**:
1. 不引入新工具名（复用 `edit_file` / `write_file`）
2. 不改 SKILL.md 整体阶段定义（只改 §S0 + 工具名引用）
3. 不动 6 个 invariant helpers 在 `report_writing.py` 的纯函数定义
4. 不动 `_finalize_assistant_turn` 的 claim-only retry 机制（保留）

## §3 详细设计

### §3.1 工具集（B / H）

#### 3.1.1 最终工具表

```
保留 / 增强（共 7 个工具）：

1. read_file(file_path)                              ← 不动
2. read_material_file(...)                           ← 不动
3. write_file(file_path, content)                    ← 增强：canonical draft 加 dispatcher
4. edit_file(file_path, old_string, new_string)      ← 增强：canonical draft 加 smart dispatcher
5. append_report_draft(content)                      ← 不动（唯一 vertical specialty）
6. web_search(query)                                 ← 不动
7. fetch_url(url)                                    ← 不动

删除（3 个旧专用工具）：
- rewrite_report_section
- replace_report_text
- rewrite_report_draft
```

模型视角：看不到任何 dispatcher 行为；只看到 `edit_file(old, new)` 的 string match 成功/失败。`canonical_action` 等 metadata 仅在 backend 审计用。

#### 3.1.2 `edit_file` canonical draft dispatcher

入口分派（`backend/chat.py:_execute_tool` edit_file 分支）：

```python
def _dispatch_edit_file(file_path, old_string, new_string, ...):
    canonical_path = "content/report_draft_v1.md"
    if file_path != canonical_path:
        return _generic_edit_file(file_path, old_string, new_string)

    # canonical draft 分支
    if not old_string or old_string.strip() == "":
        return reject(
            "edit_file 需要 old_string 锚点；"
            "新增内容请用 append_report_draft"
        )

    canonical_action = None
    if old_string.startswith("## "):
        # NEW helper: resolve_section_anchor (区别于 legacy resolve_section_target)
        # legacy 是从 user_message 抽 "第N章" prefix；新 helper 直接从 old_string 锚点定位。
        snapshot = resolve_section_anchor(old_string, draft)
        if snapshot is None:
            return reject("锚点章节未在 draft 中唯一匹配")
        canonical_action = "section_rewrite" if new_string else "section_delete"
        actual_old = snapshot
    elif old_string.startswith("# ") and old_string.strip() == draft.split("\n")[0].strip():
        # 整篇重写：old_string 必须等于 draft 第一行 h1 标题（精确匹配）
        # 防止 P2-10 误伤："把报告标题改成 X" 走这里就误判全文重写
        if not _user_message_has_full_rewrite_keyword(turn_context):
            return reject(
                "整篇重写需要用户明确说'整篇/推倒/全文重写'。"
                "局部修改请用 ## 锚点；新增请用 append_report_draft"
            )
        canonical_action = "full_rewrite"
        actual_old = draft
    else:
        # 包括 single-line h1（"# 旧标题" → "# 新标题"）走 text_replace 而不是 full_rewrite
        if draft.count(old_string) != 1:
            return reject("old_string 必须在 draft 中唯一出现")
        canonical_action = "text_replace" if new_string else "text_delete"
        actual_old = old_string

    # 统一 invariants（共享 helpers）
    err = run_canonical_invariants(turn_context, project_id, ...)
    if err:
        return reject(err)

    # post-hoc reverse intent check（§3.1.5 helper）
    if detect_user_message_intent(turn_context.user_message_text) == "generative":
        return reject(
            "用户消息看起来是想新增内容（起草/续写/写下一章），"
            "请用 append_report_draft；edit_file 是改已有内容"
        )

    # 写盘
    do_edit(actual_old, new_string)
    append_canonical_draft_mutation(turn_context, {  # §3.4.1 list
        "tool": "edit_file",
        "canonical_action": canonical_action,
        "target_label": _summarize_target(actual_old),
        "old_len": len(actual_old),
        "new_len": len(new_string),
        "mtime_after": stat(canonical_path).st_mtime,
        "ts": time.time(),
    })

    return {
        "status": "success",
        "canonical_action": canonical_action,
        "target_label": _summarize_target(actual_old),
        "old_len": len(actual_old),
        "new_len": len(new_string),
        ...
    }
```

#### 3.1.3 `write_file` canonical draft dispatcher

**采纳 P2-9 codex 建议**：canonical draft 路径 **永远拒绝** `write_file`。统一走 `append_report_draft` 做首次起草——保证 specialty 的 mental model 唯一性。

```python
def _dispatch_write_file(file_path, content, ...):
    canonical_path = "content/report_draft_v1.md"
    if file_path != canonical_path:
        return _generic_write_file(file_path, content)

    # canonical draft 路径无论 draft 是否存在都拒绝
    return reject(
        "正文草稿请用 append_report_draft（首次起草 / 续写）"
        "或 edit_file（章节重写 / 文字替换 / 整篇重写）。"
        "write_file 不接受 canonical draft 路径。"
    )
```

理由：
- 削弱 `write_file` 与 `append_report_draft` 在 generative 路径的重叠（codex P2-9）
- 简化 dispatcher 数量（少一个分支）
- `append_report_draft` 内部已支持"首次起草放宽 read-before-write"逻辑，无需在 write_file 重复

#### 3.1.4 `append_report_draft`（签名不变，内部适配 list）

工具签名不变（`content` 参数）；外部行为不变。**内部需要适配 §3.4 的 `canonical_draft_mutations` list 结构**：

```python
def append_report_draft(self, project_id, content):
    err = run_canonical_invariants(turn_context, project_id, ...)
    if err:
        return reject(err)

    # post-hoc reverse intent check（§3.1.5 helper）
    if detect_user_message_intent(turn_context.user_message_text) == "modify":
        return reject(
            "用户消息看起来是想改已有内容（把 X 改成 Y / 重写第N章），"
            "请用 edit_file；append 是新增内容"
        )

    do_append(content)
    append_canonical_draft_mutation(turn_context, {  # §3.4.1 list
        "tool": "append_report_draft",
        "canonical_action": "first_draft" if first_time else "append",
        "target_label": "first chapter" if first_time else "next paragraph/chapter",
        "old_len": 0 if first_time else len(prev_draft),
        "new_len": len(content),
        "mtime_after": stat(canonical_path).st_mtime,
        "ts": time.time(),
    })
    return {"status": "success", ...}
```

**首次起草特例**（draft 不存在时）：跳过 read-before-write 检查；其他 invariant 仍走。`canonical_action="first_draft"` 区别于 `"append"`，便于审计。

只调整 reject 引导文案，去掉对已删 3 个工具的 reference。

#### 3.1.5 共享 invariant helpers

`backend/report_writing.py` 中的 6 个 pure function helpers，**5 个签名/行为不变，1 个内部逻辑加 within-turn self-refresh（§3.4.3）**：

```python
check_stage_eq_s4(turn_context)                        # 不动
check_outline_confirmed(project_id)                    # 不动
check_no_mixed_intent(turn_context)                    # 不动
check_mutation_limit(turn_context)                     # 阈值 1 → 3，§3.4
check_read_before_write_mtime(turn_context, path)      # 加 within-turn self-refresh，§3.4.3
check_fetch_url_pending(turn_context)                  # 不动
```

新增 2 个 helper：

```python
def detect_user_message_intent(user_message: str) -> Literal["generative", "modify", "ambiguous"]:
    """Lightweight keyword-based intent classifier (~25 lines).

    "generative" markers: 起草 / 续写 / 写下一章 / 继续写 / 写下一段 / 帮我写
    "modify" markers: 把.*改成 / 重写第.*章 / 替换 / 修改 / 删掉 / 调整
    其他 → "ambiguous"（不阻拦）
    """

def resolve_section_anchor(anchor: str, draft: str) -> str | None:
    """从 draft 中按 h2 anchor 精确定位完整章节 snapshot。

    与 legacy `resolve_section_target` 的区别：
      - legacy: 从 user_message 中抽"第N章/节"前缀做模糊匹配
      - 新版: 直接拿 old_string ("## 章节标题..." prefix) 做精确 h2-label match
              然后展开到下一个同级 ## 之前的全部内容
    
    Args:
        anchor: 形如 "## 第二章 战略选择" 
                **明确语义：仅取首行（截断到第一个 \\n 之前）作为 h2-label 做匹配。
                anchor 中首行之后的正文内容被忽略——不参与一致性校验，不做内容比对。**
                这意味着模型可以传 "## 第二章" 单行，也可以传 "## 第二章\\n旧正文..."，
                两种都成功匹配章节，且 dispatcher 用 draft 中的实际章节 snapshot 作为 actual_old。
                这是设计的核心点之一：消除模型必须复述 1500 字章节原文的失败模式。
        draft: 完整草稿文本

    Returns:
        匹配的完整章节文本（含 "## ..." 行 + 正文，不含下一章 ## 行），或 None（label 不存在 / 多重匹配）
    """
```

`resolve_section_target`（legacy，从 user_message 提取章节前缀）保留**仅用于 deprecated 旧 codepath 兼容**，在 Commit 3 全部清掉。

### §3.2 S0 强制澄清门槛（C）

#### 3.2.1 触发条件

`s0_confirmation_completed: bool` 字段：
- 持久化到项目 `<project_dir>/.consulting-report/conversation_state.json`（**不**写 `chat-history.json`，后者只存 message 流不存 session-state）
- 项目首次创建时初始化 False（在 `SkillEngine.create_project` 写入 conversation_state.json）
- 每个 turn 起点 backend 从 conversation_state.json 加载到 `turn_context["s0_confirmation_completed"]`
- turn 结束时若 flag flip True，落盘到 conversation_state.json

**conversation_state.json schema 扩展**：当前 sidecar 已有 `_empty_conversation_state` / atomic save 的字段白名单（per existing code）。本 spec 扩展白名单加入 `s0_confirmation_completed`：

```python
# backend/skill.py 或 chat.py 的 conversation state schema
_CONVERSATION_STATE_FIELDS = {
    # ... 现有字段
    "s0_confirmation_completed": bool,  # NEW，default False
}
```

实施时：
- atomic save 时白名单需要明确包含新字段，否则 save 会丢弃
- load 时缺省值 = `True`（老项目兼容，§6.1）
- 测试 `S0FirstTurnGateStateRoundtripTests` 验证 save → load 不丢字段

#### 3.2.2 First-turn 工具白名单

```python
S0_FIRST_TURN_ALLOWED_TOOLS = {
    "read_file",
    "read_material_file",
    "web_search",
    "fetch_url",
}
```

`backend/chat.py:_execute_tool` 入口检查：

```python
if not turn_context.get("s0_confirmation_completed"):
    if tool_name not in S0_FIRST_TURN_ALLOWED_TOOLS:
        return reject(
            "首轮项目澄清。请先以纯文本输出 3-5 个针对 seed"
            "（项目主题/受众/范围/边界）的确认/补充问题，"
            "等用户回答后再使用其他工具。"
        )
```

#### 3.2.3 解锁逻辑（turn-end finalize）

```python
# in _finalize_assistant_turn
if not turn_context.get("s0_confirmation_completed"):
    non_whitelist_called = any(
        c.tool_name not in S0_FIRST_TURN_ALLOWED_TOOLS
        for c in turn_tool_calls
    )
    assistant_text_emitted = bool((assistant_message_text or "").strip())

    # 双条件：本轮没调过非白名单工具 AND model 输出过非空文本
    # 防止"模型只 web_search 后崩了/超时但什么都没说" 的情况错误解锁
    if not non_whitelist_called and assistant_text_emitted:
        turn_context["s0_confirmation_completed"] = True
        persist_conversation_state(project_id, turn_context)
```

**两个条件必须同时满足**：
- 没调过白名单外的工具（如 write_file / edit_file / append_report_draft）
- 模型输出过非空文本（即便仅仅是"我先了解一下背景"这种过渡话也算）

**用户 explicit 决策（2026-05-08 brainstorm）**：

> "1.不数吧，我觉得要给模型自由度"

不数问号、不解析问题质量。模型只 web_search 后输出文本（哪怕没问问题）也算完成（model 自治）。

Codex spec review R1/R2 提出更严约束（必须发出问题或用户已回答），本 spec 选择遵循用户原始决策。理由：
- 数问号是 fragile 启发式（误判中文标点、模型用列表代替"?"等）
- 严约束会撞 deepseek reasoning 模型多种自然回应方式（如"先复述理解 + 列 2 个待确认点"）
- model 即使没发问题也已经走完一轮（system prompt 强约束 + dispatcher 拒绝写工具），用户下一轮仍可自主决定要不要补充——not a hard 失败模式
- 真正的 false negative（model 完全没发任何问题就 ack 完成）实测概率低；上线后若高发，可单独加"问号 ≥ 1" 软提示（不阻断）

**反向场景**：模型试图调写工具被 dispatcher 拒绝 + 然后 fallback 输出文本——此时 `non_whitelist_called=True`（拒绝前的尝试也算调用过），所以 **不解锁**。模型必须下一轮（仍是 first turn 状态下）才能正确发问。

#### 3.2.4 system prompt 配套

在现有 `_build_system_prompt`（per-stage 部分）添加：

```
[首轮硬约束 — S0]
项目第一次响应：
1. 你可以先用 web_search/fetch_url 搜主题相关内容、用 read_file 读 seed 和已上传材料；
2. 然后必须以纯文本输出 3-5 个针对 seed（项目主题/受众/范围/边界）的确认/补充问题；
3. 不允许调用任何写工具（write_file / edit_file / append_report_draft）；
4. 即便用户首条说"直接推进 / 不用每步都问"，第一轮仍要发问 ——
   但格式可以轻：复述你的理解 + 1-2 个真正需要拍板的点。
```

#### 3.2.5 边界 case

| 场景 | 行为 |
|---|---|
| user 首条说"按你判断推进" | model 仍发问，但格式可轻 |
| user 首条很简短 | model 必须发问扩展 |
| user 答完后改主意补充 | 进入正常 SKILL 流，不再触发 |
| user 清空对话 | reset `s0_confirmation_completed = False` |
| model 第一轮调白名单工具后停下 | 仍 mark complete（model 自治） |
| model 第一轮试图调写工具 | 被 dispatcher 拒绝 + fallback 到文本 |

### §3.3 `<think>` 折叠（D）

#### 3.3.1 现状

DeepSeek V4 Pro `chat/completions` response 在 `message.content` 里输出形如：

```
<think>
The user is saying ...
Let me check ...
</think>

实际回复内容
```

前端目前直接渲染整个 `content`，`<think>` 标签明文出现。

#### 3.3.2 后端 stream 协议改造

在 `_stream_chat_completions` 流处理位置（`backend/chat.py` 流式分支），加 stateful 解析器：

```python
class ThinkingStreamParser:
    """States: NORMAL → INSIDE_THINK → NORMAL."""
    def feed(self, delta: str) -> list[StreamEvent]:
        # 检测 <think> / </think> 边界
        # 输出 events:
        #   {"type": "content_delta", "text": "..."}    # 正常内容
        #   {"type": "thinking_delta", "text": "..."}   # 推理内容
```

沿用现有 `/api/chat/stream` 的 type-based protocol（前端 parser 只解析 `data:` JSON，统一格式），新增 `type: "thinking_delta"`：

```
data: {"type": "content_delta", "text": "好"}

data: {"type": "thinking_delta", "text": "Let me check ..."}

data: {"type": "tool_call_start", "tool": "read_file", ...}
```

**不**改用 SSE `event:` field——会牵动 backend `_chat_stream_unlocked` flusher、frontend SSE parser、`tests/test_stream_api.py` 全部，scope 失控。type-based 单 channel 直接加新 type 是最小 diff。

#### 3.3.3 前端渲染

新组件 `ThinkingBlock.jsx`：

```jsx
<details className="thinking-block">  {/* HTML5 details 默认 closed */}
  <summary>推理过程</summary>
  <div className="thinking-content">{thinking_text}</div>
</details>
```

**默认状态：折叠（closed）**——用户必须主动点击 `<summary>` 才能展开看到 thinking 内容。CSS：max-height: 240px + overflow scroll；样式跟 `ToolCallBlock` 同款（中性色 + 等宽字体）。

#### 3.3.4 用户决策依据 + 项目约束解释

**用户 explicit 决策（2026-05-08 brainstorm）**：

> "可以把 think 的推理部分作为模型推理部分放进块里吗？就类似工具调用那种块，固定长度，有个下拉条，想看可以看的那样。"

——用户明确要求：跟 ToolCallBlock 同款的可折叠面板，用户可选择性展开。

**Codex spec review R1/R2 提出更激进建议**：完全从生产 UI 剥离 `<think>`，仅在 dev flag 下可见。本 spec 选择**遵循用户原始决策而非 codex 建议**，理由：

1. `skill/SKILL.md` 的写作约束 "不要暴露 'AI reference' '内部推理' '系统提示' 等后台术语"——该约束适用于**写作输出文本**（咨询报告草稿、回复正文），即模型应避免"基于我的推理"、"作为 AI 我..."等元话术混入正文。**不**适用于 UI 上展示模型 reasoning 过程的可选交互——这是产品交互设计层面，与正文写作约束正交。
2. 类比：tool_call 也是"内部"动作，UI 已展示在折叠面板内（同款 ToolCallBlock）；reasoning 折叠面板与之同构。如果 reasoning 必须剥离，那 tool_call 也该剥离——但用户从未提过 tool_call 折叠面板有问题。
3. 业界标杆：ChatGPT (o1)、Claude.ai (extended thinking)、Cursor 都展示推理过程（默认折叠或可关闭）。完全剥离 reasoning UI 违背 reasoning 模型的产品哲学。
4. **默认折叠**（HTML5 `<details>` 默认 closed）已经满足"不主动暴露"语义；用户需要时一键展开是 UX 必要性，不是规则违反。

如果上线后产品反馈强烈反对（同事看到展开内容觉得不专业），后续可单独立项加 "完全隐藏 reasoning" 的 user setting，不需要本 spec 提前优化。

#### 3.3.5 边界 case

- 中断的 stream（model 输出到一半 disconnect）：`</think>` 没匹配上 → 默认按 NORMAL 收尾（已收到的 thinking 文本仍展示在 ThinkingBlock）
- 嵌套 `<think>`：DeepSeek 不会嵌套；防御性按 outermost 处理
- 转义字符 `<think>` 出现在正文里（如用户引用某个 prompt）：当前不处理（reasoning 模型实测不会污染）

### §3.4 mutation_limit 1 → 3（E）

#### 3.4.1 数据结构改造

`turn_context["canonical_draft_mutation"]`（单 dict）→ `turn_context["canonical_draft_mutations"]`（list of dict）：

```python
turn_context["canonical_draft_mutations"] = []  # 默认空 list

# 写盘成功后 append 一项：
turn_context["canonical_draft_mutations"].append({
    "tool": "edit_file" | "append_report_draft",  # write_file canonical 永远拒绝（§3.1.3）
    "canonical_action": "section_rewrite" | "first_draft" | "append" |
                        "text_replace" | "section_delete" | "text_delete" |
                        "full_rewrite",
    "target_label": str,    # "## 第二章 ...", "first chapter", or "X → Y"
    "old_len": int,
    "new_len": int,
    "mtime_after": float,   # 写盘后的 mtime（用于 within-turn 自我刷新）
    "ts": float,            # epoch ms
})
```

#### 3.4.2 limit 检查 + 错误消息

```python
MAX_CANONICAL_MUTATIONS_PER_TURN = 3  # was 1

def check_mutation_limit(turn_context) -> str | None:
    mutations = turn_context.get("canonical_draft_mutations", [])
    if len(mutations) >= MAX_CANONICAL_MUTATIONS_PER_TURN:
        summary = "\n".join([
            f"  {i+1}. {m['canonical_action']} {m['target_label']} "
            f"(old={m['old_len']} → new={m['new_len']})"
            for i, m in enumerate(mutations)
        ])
        return (
            f"本轮已经成功修改正文草稿 {len(mutations)} 次，达到上限 "
            f"{MAX_CANONICAL_MUTATIONS_PER_TURN}。\n"
            f"已完成的修改：\n{summary}\n"
            f"请等用户回应再做下一次修改。"
        )
    return None
```

#### 3.4.3 Within-turn mtime self-refresh

第一次写入后 mtime 必然变化。第二次写入若仍走 read-before-write 检查会被自己制造的 stale snapshot 卡住。修法：**check_read_before_write_mtime 识别"上一次变化是自己造成的"并豁免**：

```python
def check_read_before_write_mtime(turn_context, draft_path) -> str | None:
    last_read_mtime = turn_context.get("last_read_mtime", {}).get(draft_path)
    current_mtime = stat(draft_path).st_mtime

    # 如果本轮已自己写过，比较 current_mtime 与最后一次自己写后的 mtime
    mutations = turn_context.get("canonical_draft_mutations", [])
    if mutations:
        last_self_mtime = mutations[-1]["mtime_after"]
        if current_mtime == last_self_mtime:
            return None  # 自己写后没人动过，跳过 read 要求

    # 否则走原检查
    if last_read_mtime is None:
        return "draft 已存在，请先 read_file 读最新内容"
    if last_read_mtime != current_mtime:
        return "草稿在你阅读后被修改，请先重新 read_file 再提交"
    return None
```

#### 3.4.4 理由

- DeepSeek reasoning 一轮内可能合理做"section_delete + append 新章节"（move 操作 = 2 步）
- 极端场景"删 + 改 + 新增"3 步可能（罕见但合理）
- 4+ 几乎一定是失控循环，3 是合理上限

### §3.5 guard 控制层删除清单（F）

#### 3.5.1 删除（删除量 ~1100 行；净值见 §B）

| # | 位置 | 内容 | 行数 |
|---|---|---|---|
| 1 | `backend/chat.py` | `_guard_canonical_draft_obligation_tool` 函数 | ~18 |
| 2 | `backend/chat.py` | `detect_canonical_draft_write_obligation` 旧版本 | ~56（替换为 `detect_user_message_intent` 25 行，净 -31） |
| 3 | `backend/chat.py` | `NON_PLAN_WRITE_ALLOW_KEYWORDS` / `FILE_UPDATE_VERBS` 常量 | ~10 |
| 4 | `backend/chat.py` | `_classify_canonical_draft_turn` 残留 body（如有） | ~350 |
| 5 | `backend/chat.py` | `_preflight_canonical_draft_check` 残留 body（如有） | ~150 |
| 6 | `backend/chat.py` | `_make_canonical_draft_decision` / `_empty_canonical_draft_decision` | ~80 |
| 7 | `backend/chat.py` | `_validate_append_turn_canonical_draft_write` | ~80 (逻辑 inline 迁移到 append_report_draft 入口) |
| 8 | `backend/chat.py` | `_validate_required_report_draft_prewrite` | ~85 (逻辑 inline 迁移到 edit_file/write_file dispatcher) |
| 9 | `backend/chat.py` | 3 个工具实现：`rewrite_report_section` / `replace_report_text` / `rewrite_report_draft` | ~450 |
| 10 | `backend/chat.py` | 3 工具的 schema 注册 + dispatch 路由 | ~50 |
| 11 | `tests/test_chat_runtime.py` | `RewriteReportSectionToolTests` / `ReplaceReportTextToolTests` / `RewriteReportDraftToolTests` 整类 | ~300 |
| 12 | `tests/test_*.py` | 旧 obligation detector 相关 ToolFamilyLockTests / KeywordGateTests | ~50 |
| 13 | `skill/SKILL.md` | 旧工具名引用（`rewrite_report_section` 等）的章节文案 | ~30 |
| 14 | `backend/chat.py` | turn_context 字段 `canonical_draft_decision` / `required_write_snapshots` / `draft_action_events` | ~50 |
| 15 | `backend/report_writing.py` | `resolve_section_target` legacy（从 user_message 抽 prefix）| ~50 (被 `resolve_section_anchor` 取代) |

#### 3.5.2 保留（5 项不动 + 2 项加内部逻辑）

| 项 | 位置 | 处理 | 备注 |
|---|---|---|---|
| `append_report_draft` 工具 | `backend/chat.py` | 不动 | B5' 唯一 vertical specialty |
| 5 个 invariant helpers（不含 mutation_limit / read-before-write）| `backend/report_writing.py` | 不动 | 跟工具数量解耦 |
| `check_mutation_limit` | `backend/report_writing.py` | **改阈值 1→3 + 适配 list 数据结构**（§3.4） | 行为大改 |
| `check_read_before_write_mtime` | `backend/report_writing.py` | **加 within-turn self-refresh**（§3.4.3） | 内部逻辑改 |
| `_finalize_assistant_turn` claim-only retry | `backend/chat.py` | 字段切到 `canonical_obligation`（§3.5.4）| 字段名改 |
| `read_file` mtime hook | `backend/chat.py` | 不动 | read-before-write 依赖 |

#### 3.5.3 新增（~600 行含测试）

| # | 内容 | 位置 | 行数 |
|---|---|---|---|
| 1 | `edit_file` canonical draft dispatcher 子分支 | `backend/chat.py` | ~120 |
| 2 | `write_file` canonical draft 处理 | `backend/chat.py` | ~50 |
| 3 | `detect_user_message_intent` 轻量 helper | `backend/report_writing.py` | ~25 |
| 4 | `s0_confirmation_completed` 字段 + 首轮 gate（C 项） | `backend/chat.py` | ~30 |
| 5 | `ThinkingStreamParser` + SSE channel split（D 项） | `backend/chat.py` | ~80 |
| 6 | 前端 `ThinkingBlock.jsx` 组件 + 样式 | `frontend/src/components/` | ~50 |
| 7 | 对应 unit tests | `tests/` + `frontend/tests/` | ~300 |

#### 3.5.4 字段重构（保留 retry 机制覆盖 generative + modify）

旧 `turn_context["canonical_draft_write_obligation"]` 是 4-class（per old tool family）。新结构是 2-class 但保留 retry 触发能力——**append 类需求也要进 claim-only 对账**（否则模型可能口头说"已写完第三章"但 0 mutation，无人纠正）：

```python
turn_context["canonical_obligation"] = {
    "intent": "generative" | "modify" | None,  # None = 没识别出明确意图
    "expected_action": "append" | "any_canonical_write" | None,
}
```

由 `detect_user_message_intent` 在 turn-start 写入：

| user message intent | canonical_obligation.intent | expected_action |
|---|---|---|
| 起草/续写/写下一章 | `"generative"` | `"append"` |
| 把 X 改成 Y / 重写第 N 章 / 替换 / 删掉 | `"modify"` | `"any_canonical_write"` |
| 其他（Q&A、调整大纲、补充材料）| `None` | `None` |

`_finalize_assistant_turn` 的 claim-only retry 检查：

```python
obligation = turn_context.get("canonical_obligation", {})
if obligation.get("intent") in ("generative", "modify"):
    mutations = turn_context.get("canonical_draft_mutations", [])
    if len(mutations) == 0 and assistant_text_claims_modification(...):
        # 注入 corrective user message + retry
        ...
```

**与 tool-family lock 的区别**：旧 lock 是"必须用工具 X"（强约束工具选择）；新 obligation 是"必须真实落盘任意 canonical write"（约束行为后果，不约束工具选择）。删除前者，保留后者。

### §3.6 search 配额（G）

当前 `managed_search_pool.json` 的 `limits.per_turn_searches = 2`（实地 grep 验证）。本 spec 改为 3：

```json
{
  "limits": {
    "per_turn_searches": 3,        // current 2 → 3
    "project_minute_limit": 10,    // unchanged
    "global_minute_limit": 20,     // unchanged
    "memory_cache_ttl_seconds": 21600,
    "project_cache_ttl_seconds": 86400
  }
}
```

理由：DeepSeek reasoning 一轮典型 3 次（主搜 → 单维度 focus → 不同角度），2 次掐死探索能力。`project_minute / global_minute` 是兜底防滥用保留不变。

**注**：`docs/current-worklist.md` 历史归档段（line ~257）写过"per_turn_searches: 2 → 4" 是 2026-04-22 旧调整记录（gemini 时代某次试探），与本 spec 无关；不视为不一致——本 spec 实施后即把当前值落到 3。

### §3.7 context tier（I）

保持当天上午加好的：

```python
# backend/context_policy.py（已实施）
TIER_LIMITS = {
    "tier_1m": (1_000_000, 200_000),
    "tier_1m_eff_256k": (1_000_000, 256_000),  # NEW
    ...
}

EXACT_MODEL_TIERS = {
    "gemini-3-flash": "tier_1m",
    "kimi-k2.5": "tier_256k",
    "deepseek-v4-pro": "tier_1m_eff_256k",  # NEW
}
```

stage-agnostic 256k effective。`reserved_output_tokens=8192`、`compress_threshold≈230k`。**不引入 stage-aware 复杂度**——理由见 brainstorm 记录：cap 是天花板不是分配，dynamic 边际收益小且毁 prompt cache。

### §3.8 打包基础设施（J）

#### 3.8.1 stderr → log file

`app.py` 启动时（`load_settings()` 之前），加一个 RotatingFileHandler：

```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def _setup_app_log():
    log_dir = Path.home() / ".consulting-report"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

_setup_app_log()
```

跟 `backend/main.py` 现有 `logging.basicConfig` 共存——root logger 多个 handler 即可。

PyInstaller windowed exe 下：原本 stderr 被吞，现在所有 log 走 FileHandler 落到 `~/.consulting-report/app.log`。崩溃 traceback、heal 通知、search 配额耗尽 warning 全部可见。

#### 3.8.2 PyInstaller version_info 块

`consulting_report.spec` 加 version block：

```python
# consulting_report.spec
exe = EXE(
    ...
    version='version_info.txt',
)
```

新建 `version_info.txt`：

```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 1, 0, 0),
    prodvers=(0, 1, 0, 0),
    OS=0x40004,
    fileType=0x1,
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'ZhYoU'),
        StringStruct('FileDescription', '咨询报告写作助手'),
        StringStruct('FileVersion', '0.1.0'),
        StringStruct('InternalName', 'consulting-report'),
        StringStruct('OriginalFilename', '咨询报告助手.exe'),
        StringStruct('ProductName', '咨询报告助手'),
        StringStruct('ProductVersion', '0.1.0'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])]),
  ]
)
```

打包后：
- Windows 任务栏右键、属性 → 详细信息可见
- computer-use resolver 能找到 app
- 任务管理器分组按 ProductName

#### 3.8.3 Start menu shortcut（可选）

`build.ps1` 在打包成功后，可选生成 Start menu shortcut（默认不开，命令行 `--install-shortcut` flag 开启）：

```powershell
$exe = "$dist\咨询报告助手\咨询报告助手.exe"
$startMenu = [Environment]::GetFolderPath("Programs")
$lnkPath = Join-Path $startMenu "咨询报告助手.lnk"
$sh = New-Object -ComObject WScript.Shell
$shortcut = $sh.CreateShortcut($lnkPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = (Split-Path $exe)
$shortcut.Save()
```

发版给同事时人工执行即可，不在主交付流程。

### §3.9 test 分层（K）

#### 3.9.1 加 slow markers

`tests/test_stream_api.py` 头部：

```python
@pytest.mark.slow
class StreamApiTests(unittest.TestCase):
    ...
```

`tests/smoke_packaged_app.py` 同样。

#### 3.9.2 pytest.ini 默认 fast

`pytest.ini`（新建）：

```ini
[pytest]
addopts = -m "not slow"
testpaths = tests
markers =
    slow: tests that need real uvicorn / packaged exe / minutes-long setup
```

效果：默认 `pytest tests/` 跳过 slow，且保持串行。这样牺牲自动并行速度，换取 Windows 开发机上的稳定性，避免 xdist worker 启动失败 / hang / OOM。`pytest-xdist` 依赖仍保留，后续在确认安全的机器上可显式追加 `-n auto --dist worksteal`。

#### 3.9.3 pytest-xdist 依赖

`requirements.txt` 加：

```
pytest-xdist>=3.2.1
```

#### 3.9.4 慢套件入口

跑全套（含 slow） commit 前：

```bash
pytest tests/ -m ""
```

或 CI：

```bash
pytest tests/ -m "" --strict-markers
```

#### 3.9.5 plan 中的 task 约定

写 implementation plan 时，每个 task 明确写 "本 task 只跑：`pytest tests/test_X.py -q`"，不让 agent 默认跑全套。

### §3.10 已完成的部分（10 项，整合到 Commit 0）

2026-05-08 早上已实施未 commit 的部分（在本 spec 实施时纳入第一个 commit）：

| 文件 | 改动 | 状态 |
|---|---|---|
| `backend/config.py` | 新增 `heal_stale_managed_model` + `_default_managed_models_fetch` 函数 | 写完 |
| `backend/main.py` | 启动时调 `heal_stale_managed_model` + `save_settings` 落盘 | 写完 |
| `backend/context_policy.py` | 新增 `tier_1m_eff_256k` tier + `deepseek-v4-pro` exact mapping | 写完 |
| `tests/test_config.py` | 7 个新 `HealStaleManagedModelTests` + 默认模型字符串更新 | 写完 |
| `tests/test_context_policy.py` | 新 `test_exact_match_for_managed_deepseek_uses_1m_provider_and_256k_effective` | 写完 |
| `frontend/src/utils/connectionMode.js` | fallback `gemini-3-flash` → `deepseek-v4-pro` | 写完 |
| `frontend/tests/connectionMode.test.mjs` | 4 处 fallback 字符串更新 | 写完 |
| `docs/current-worklist.md` | 加 #4「图片附件能力按 managed_model 分流」（推后处理）| 写完 |
| `docs/managed-proxy-deployment.md` | 默认模型名同步更新 | 写完（昨天） |
| `CLAUDE.md` / `AGENTS.md` | 默认模型名同步更新 | 写完（昨天） |

## §4 实施分阶段（3 commits + 1 整合 commit）

参考 2026-05-05 spec 的 5-step 节奏，本 spec 用 4-step：

### Commit 0 — 已完成工作的整合落地

把 §3.10 列出的所有"写完未 commit"改动合并打包，作为本次 spec 实施的基线 commit。包含：

- `heal_stale_managed_model` + tier mapping + frontend fallback
- 所有对应 test
- worklist 条目
- 文档同步

**commit message**:
```
feat(deepseek-migration): heal stale managed_model on startup + add tier_1m_eff_256k

- backend/config.py: heal_stale_managed_model auto-swaps managed_model
  via /v1/models when stored value not in proxy whitelist; best-effort
  on network failure
- backend/main.py: invoke heal at startup, persist + log
- backend/context_policy.py: tier_1m_eff_256k for deepseek-v4-pro
  (1M provider, 256k effective)
- frontend/src/utils/connectionMode.js: fallback gemini-3-flash → deepseek-v4-pro
- 7 new unit tests for heal_stale_managed_model
- 1 new context_policy test
- frontend tests updated
```

### Commit 1 — 新工具入口 + 增强 dispatcher + 首轮 gate + think 折叠

加新代码，**不删旧代码**，新旧并存：

- `backend/chat.py`: 加 `edit_file` / `write_file` canonical draft dispatcher 子分支
- `backend/chat.py`: 加 `s0_confirmation_completed` 字段 + 首轮 gate
- `backend/chat.py`: 加 `ThinkingStreamParser` + SSE channel split
- `backend/chat.py`: 加 `canonical_obligation` 字段（旧 `canonical_draft_write_obligation` 暂时并存）
- `backend/report_writing.py`: 加 `detect_user_message_intent` helper
- `backend/report_writing.py`: 改 `MAX_CANONICAL_MUTATIONS_PER_TURN = 3`
- `frontend/src/components/`: 加 `ThinkingBlock.jsx` + 样式
- `frontend/src/components/MessageRenderer.jsx`（或对应文件）: 渲染 thinking event
- `app.py`: 加 `_setup_app_log` rotating FileHandler
- `requirements.txt`: 加 `pytest-xdist>=3.2.1`
- `pytest.ini` (新建): default `-m "not slow"`；保留 pytest-xdist 供显式使用
- `tests/`: 新增对应单测（不删旧测试）

阶段完成：旧 4 工具 + 新 dispatcher 同时存在，model 仍可调旧工具（schema 注册保留）。

### Commit 2 — 切流量 + 删 schema 注册

让 model 走新路径：

- `backend/chat.py`: 删 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft` 的 schema 注册（model 不再可见）
- `backend/chat.py`: 删 dispatch 路由中对 3 个旧工具的入口
- `skill/SKILL.md` §S4: 改工具引用文案，去掉对 3 个旧工具名的提及
- `backend/chat.py:user_action` 等错误消息：去掉对 3 个旧工具的引用
- `tests/test_*.py`: 标记/删除 `RewriteReportSectionToolTests` 等整个 test class

阶段完成：model 在 chat 中只能选 `append_report_draft` / `edit_file` / `write_file`，但 callable 还在文件里。

### Commit 3 — 删旧 callable + guard 控制层 + 残留扫描

最大的删除 commit：

- 删 `_guard_canonical_draft_obligation_tool` / 旧版 `detect_canonical_draft_write_obligation` / `NON_PLAN_WRITE_ALLOW_KEYWORDS` / `FILE_UPDATE_VERBS`
- 删 `_classify_canonical_draft_turn` / `_preflight_canonical_draft_check` / `_make_canonical_draft_decision` / `_empty_canonical_draft_decision` 残留
- 删 `_validate_append_turn_canonical_draft_write` / `_validate_required_report_draft_prewrite`（逻辑已迁到工具入口）
- 删 3 个旧工具的 callable 实现
- 删旧 turn_context 字段 `canonical_draft_decision` / `required_write_snapshots` / `draft_action_events`
- 删 `tests/test_obligation_detector.py` / `tests/test_tool_family_lock.py` 等（如有独立文件）
- 删 `canonical_draft_write_obligation` 字段引用（已切到 `canonical_obligation`）

**Quality gate**：commit 前 grep 全仓 0 命中：

```bash
# 必须 0 命中
grep -rn "rewrite_report_section\|replace_report_text\|rewrite_report_draft" \
  backend/ tests/ skill/ docs/superpowers/specs/ frontend/src/

grep -rn "_guard_canonical_draft_obligation_tool\|_classify_canonical_draft_turn\|NON_PLAN_WRITE_ALLOW_KEYWORDS\|FILE_UPDATE_VERBS" \
  backend/ tests/

grep -rn "canonical_draft_write_obligation\|canonical_draft_decision\|required_write_snapshots\|draft_action_events" \
  backend/ tests/
```

允许有命中的位置：本 spec 自身（`docs/superpowers/specs/2026-05-08-...md`）、cutover report（追加性归档）。

## §5 测试计划

### 5.1 新增单测覆盖

| 模块 | test class | 覆盖点 |
|---|---|---|
| `tests/test_chat_runtime.py` | `EditFileCanonicalDispatcherTests` | 5 个 success path（section_rewrite / full_rewrite / text_replace / section_delete / text_delete）+ 5 个 form-detection reject（锚点不存在 / 锚点非唯一 / 整篇缺关键词 / old_string 空 / single-line h1 走 text_replace 不误判）|
| `tests/test_chat_runtime.py` | `EditFileGenericRegressionTests` | 非 canonical 路径行为不变（与 dispatcher 不互相影响） |
| `tests/test_chat_runtime.py` | `EditFileCanonicalInvariantRejectTests` | **6 个 invariant 各自独立 reject**：stage<S4 / outline 未确认 / mixed-intent / mutation_limit 已满 / read-before-write 缺失 / fetch_url pending；外加 within-turn self-refresh skip read 的 case |
| `tests/test_chat_runtime.py` | `WriteFileCanonicalDispatcherTests` | canonical draft 路径**永远 reject**（无论 draft 是否存在）；首次起草 must go through `append_report_draft` |
| `tests/test_chat_runtime.py` | `AppendReportDraftFirstDraftTests` | draft 不存在时跳过 read-before-write；`canonical_action="first_draft"` 落入 mutations list |
| `tests/test_chat_runtime.py` | `WriteFileGenericRegressionTests` | 非 canonical 路径不动 |
| `tests/test_chat_runtime.py` | `AppendReportDraftPostHocIntentTests` | "modify" intent + 调 append → reject；"generative" intent + 调 append → 正常 |
| `tests/test_chat_runtime.py` | `EditFilePostHocIntentTests` | "generative" intent + 调 edit_file → reject；"modify" intent + 调 edit_file → 正常 |
| `tests/test_chat_runtime.py` | `S0FirstTurnGateTests` | 白名单工具放行 / 写工具拒绝 / 解锁双条件（无非白名单 + 文本非空）/ 拒绝后留在 first-turn state / 清空对话 reset / state 落盘到 conversation_state.json |
| `tests/test_chat_runtime.py` | `S0FirstTurnGateStateRoundtripTests` | conversation_state.json save → load 不丢 `s0_confirmation_completed` 字段 / 老格式（不含字段）load 缺省 True / 写盘并发不破坏 atomic save / 写盘失败时优雅降级（log warn 不 raise） |
| `tests/test_chat_runtime.py` | `ThinkingStreamParserTests` | think 边界检测 / 中断流处理（截断 thinking 仍展示）/ 非 think 内容透传 / 嵌套 think 取 outermost |
| `tests/test_report_writing.py` | `DetectUserMessageIntentTests` | generative / modify / ambiguous 三类典型 + 中文 + 标点边界 |
| `tests/test_report_writing.py` | `ResolveSectionAnchorTests` | h2 唯一匹配成功 / label 不存在 / label 重复 / anchor prefix 后跟正文（仅取首行 label） |
| `tests/test_chat_runtime.py` | `MutationLimit3Tests` | 第 1-3 次成功 + 第 4 次拒绝 + 错误消息含 mutations list 摘要 + within-turn mtime self-refresh 不撞 read-before-write |
| `tests/test_chat_runtime.py` | `ClaimOnlyRetryWithObligationTests` | obligation.intent="generative" + 0 mutation + 文本声称已写 → 触发 retry；intent="modify" 同理；intent=None → 不触发 |
| `tests/test_chat_runtime.py` | `LegacyToolCleanupTests` | grep 全仓 0 命中 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft` / 旧 obligation 字段名 |
| `frontend/tests/thinkingBlock.test.mjs` | `ThinkingBlockTests` | HTML5 details 默认 closed / 点击展开 / 文本渲染 / 样式 |
| `tests/test_packaging_spec.py` | `VersionInfoTests` | spec 中 version_info 块存在 + FileDescription/ProductName/CompanyName 字段完整 |
| `tests/test_app_logging.py` (新) | `AppLogTests` | RotatingFileHandler 配置（5MB / 3 backups）/ log 落到 `~/.consulting-report/app.log` / format 正确 |

### 5.2 旧测试更新

`tests/test_chat_runtime.py` 中：

- 删 `RewriteReportSectionToolTests` / `ReplaceReportTextToolTests` / `RewriteReportDraftToolTests` 整个 class
- 改 `AppendReportDraftToolTests` 中对旧 obligation 字段的引用 → 改读 `canonical_obligation`
- 改 `_finalize_assistant_turn` 相关 test 中 obligation 字段名

### 5.3 集成测试

`tests/test_stream_api.py`（标记 `@pytest.mark.slow`）：

- 已有的 streaming 测试不动（验证 SSE 协议层 + thinking event 透传）

### 5.4 回归 baseline

第一阶段完成后跑：

```bash
pytest tests/ -m ""           # 全套（含 slow）
pytest tests/ -q              # 默认（fast）
pytest tests/ -q -n auto --dist worksteal  # 可选：安全机器上显式验证并行
```

baseline target：fast 集合在默认串行路径下可靠完成；不再把 xdist 作为默认门禁，避免 Windows/OOM worker instability。

### 5.5 desktop E2E smoke

每个 commit 后人工跑一次 packaged exe smoke：

1. 全新机器（删 `~/.consulting-report/`）首次启动 → managed_model auto-set 正确
2. 老机器（config.json 含 `managed_model="gemini-3-flash"`）启动 → heal 触发 + log 可见
3. 新建项目 → first turn 模型发问 + 不调写工具
4. 用户回应 → 进入 S1，model 调 web_search → 限额按 3 工作 → 写 outline.md + research-plan.md
5. 进入 S4 → `append_report_draft` 起草 → `edit_file(old="## 第二章", new=...)` 重写章节
6. 触发 reasoning model `<think>` 内容 → 前端折叠面板正确展示
7. mutation_limit=3 验证：模型一轮内 3 次成功 mutation 后第 4 次 reject

## §6 Migration / Rollback

### 6.1 用户数据兼容

`~/.consulting-report/config.json`：
- 老用户配置 `managed_model="gemini-3-flash"` → heal 自动切到 `deepseek-v4-pro`
- 老用户配置 `managed_model="deepseek-v4-pro"` → 不动
- 全新用户 → 用代码默认值

`projects/<id>/.consulting-report/conversation_state.json`：
- 老格式不含 `s0_confirmation_completed` 字段 → load 时缺省 True（不强制对老项目重走 S0 gate）
- 新格式含字段 → 正常加载
- 注：`chat-history.json` 只存 message 流不动。state 字段单独走 conversation_state.json sidecar（§3.2.1）

### 6.2 Commit 间回滚

每个 commit 独立可回滚：
- Commit 3 回滚 → 旧工具 callable 回归，但 schema 已删，model 仍只能用新工具（不影响功能）
- Commit 2 回滚 → schema 注册回归，model 可同时见新旧工具，schema 冲突会让 model 困惑（短期不推荐）
- Commit 1 回滚 → 退到 spec 实施前状态，新 dispatcher 全废

如果生产爆雷，最快恢复路径是 Commit 3 → Commit 2 → Commit 1 反向 revert。

### 6.3 紧急 fallback

**关键约束**：本 spec 删除的 guard 控制层正是 gemini-3-flash 弱模型需要的（章节边界 enforcement、tool family lock、4 个语义工具）。**仅切 env 回 gemini 不构成有效 fallback**——会回到 fix4 时代的失败循环。

完整 Gemini fallback 路径：

1. 部署侧设 `CR_MANAGED_MODEL_OVERRIDE=gemini-3-flash` + managed proxy 回退 `ALLOWED_MODELS=gemini-3-flash`
2. **必须配套 git revert 本 spec Commit 1-3（保留 Commit 0 的 heal_stale_managed_model + tier）**
3. 重新打包 + 分发 dist
4. user 重装

这是 **完整版本退回**，不是 hot fallback。如果 DeepSeek 长期不可用，应该重审是否切换到其他强模型（GPT-4.1-mini / Claude Haiku 等），而不是退回 gemini。

env override 机制保留为 emergency 短期措施（24-48h 应急窗口），但不是产品级 fallback 路径。

## §7 Risk

### 7.1 模型行为不可预测性

DeepSeek V4 Pro 实测稳定但 cheaper variant `deepseek-v4-flash` 可能弱化。本 spec 不覆盖 flash variant；如需切 flash，需要单独 spec 重审 invariant 强度。

### 7.2 dispatcher 启发式

`edit_file` 按 `old_string` 形态分派（`## ` / `# ` / 短串）是**启发式**：
- 模型如果给 `old_string="## 引言\n这是引言..."` 带正文 → 仍走 section_rewrite（`resolve_section_anchor` 仅取首行 h2 label 做匹配，忽略 prefix 之后正文）
- 模型如果给 `old_string="第二章"` 不带 `## ` → 进 text_replace 分支（如果在 draft 中唯一就成功；非唯一 reject）
- 模型如果给 `old_string="## "` 单独 → reject（无法 resolve 锚点）

启发式在边界 case 可能误派。Mitigation：metadata 暴露 `canonical_action`，cutover 后跑 packaged smoke 看实测分布。

### 7.3 search 配额放宽误用

`per_turn_searches=3` 给 reasoning 模型探索空间，但也给 model 滥用（如重复同类 query）的可能。Mitigation：
- search cache TTL 6h（同 query 不真打上游）
- `project_minute_limit=10` / `global_minute_limit=20` 兜底防失控

### 7.4 think 折叠流式中断

stream disconnect 时 `</think>` 没匹配上：
- 当前方案：UI 显示 ThinkingBlock 含已收到部分（不显示 fake `</think>`）
- 模型可能输出过 `<think>` 前的纯内容 → 当作 NORMAL 处理（正常）

不影响功能，仅影响 UI fidelity。

### 7.5 心智模型割裂

`edit_file` 在 canonical draft 路径有 dispatcher，在其他路径是 vanilla edit。开发者读 chat.py 时可能困惑。Mitigation：
- 在 chat.py 加显式 docstring 注明 path-based 行为
- 测试矩阵分两组：generic edit_file vs canonical edit_file
- §3.1.2 中的 dispatcher 函数命名清晰：`_dispatch_edit_file` vs `_generic_edit_file`

### 7.6 Partial obligation retry（已知 limitation，acknowledge）

`canonical_obligation` 的 retry 检查只看 `len(canonical_draft_mutations) == 0`，即"完全没做"才 retry：

```python
if obligation.intent in ("generative", "modify") and len(mutations) == 0 and assistant_text_claims_modification:
    inject_corrective_user_message_and_retry()
```

**漏检 case**：用户说"重写第二、三、四章"，model 改了第二章但口头说"三章都改完了"——本轮 mutations=[1 entry]，text claims 3 done，**当前不 retry**。

理由不再加复杂检测：
- 精确判断"用户期望修改次数 vs 实际"需要语义解析（model 输出结构化 claim count），过度复杂
- 实际危害有限：用户看到 draft 缺修改、下一轮告诉 model "你还差两章" 就能继续
- mutation_limit=3 + claim retry 的"完全没做"兜底已经覆盖最高频失败模式

接受这个 false negative。如果上线后高发，单独立项加 "model 自报本轮 mutations 数量 vs 实际" 校验。

### 7.7 老 spec 被本 spec supersede 后的引用

`docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md` + `docs/superpowers/cutover_report_2026-05-06_tools-redesign.md` 都引用 4 工具设计。Mitigation：
- 本 spec 头部 Status 明确 supersede
- cutover report 2026-05-06 是历史归档，不动
- 新 cutover report 2026-05-08 单独写，引用本 spec

## §8 Open Issues / 推后

不在本 spec 内解决，按 worklist 单独立项：

1. **UI 重构**（design_UI.pdf 三套稿）— 等 spec 跑通流程后立项；`ThinkingBlock` 的视觉细节作为输入
2. **图片附件按 managed_model 分流**（worklist #4）— 跟 UI 重构一并做
3. **`smoke_packaged_app.py` 性能优化** — 当前 ~minutes 级；不在本 spec 范围
4. **CLI 导出 / Markdown → Word 排版** — 当前承诺只到"可审草稿"，不变

## §A 附录：删除清单（grep -n 命令清单）

提供给实施 agent 的精确删除位置：

```bash
# Commit 3 实施前 grep 当前位置
cd D:/MyProject/CodeProject/consulting-report-agent

grep -n "_guard_canonical_draft_obligation_tool\|detect_canonical_draft_write_obligation\|_classify_canonical_draft_turn\|_preflight_canonical_draft_check\|_make_canonical_draft_decision\|_empty_canonical_draft_decision\|_validate_append_turn_canonical_draft_write\|_validate_required_report_draft_prewrite" \
  backend/chat.py | head -50

grep -n "NON_PLAN_WRITE_ALLOW_KEYWORDS\|FILE_UPDATE_VERBS" backend/chat.py

grep -n "rewrite_report_section\|replace_report_text\|rewrite_report_draft" \
  backend/ tests/ skill/ frontend/src/ | head -100

grep -n "canonical_draft_write_obligation\|canonical_draft_decision\|required_write_snapshots\|draft_action_events" \
  backend/ tests/
```

实施时按 grep 输出删除并验证 0 命中（除本 spec / cutover report 自身引用）。

## §B 附录：本 spec 规模估算

| 模块 | delete | add | net |
|---|---|---|---|
| `backend/chat.py` 主要逻辑 | -700 | +330 | -370 |
| `backend/report_writing.py` | 0 | +25 | +25 |
| `backend/main.py` | 0 | +15 | +15 |
| `app.py` | 0 | +20 | +20 |
| `frontend/src/components/` | 0 | +50 | +50 |
| `tests/` | -350 | +400 | +50 |
| `skill/SKILL.md` | -30 | +20 | -10 |
| `pytest.ini` (new) | 0 | +10 | +10 |
| **TOTAL（含测试）** | **~-1080** | **~+870** | **~-210** |
| 后端代码（不含测试）| ~-730 | ~+460 | **~-270** |

TL;DR 引述的"~700 行后端代码净删"指上表"后端代码（不含测试）" 行的删除部分（~730）；包含 add 部分的实际净删是 ~270。**两个口径都对**，区别在统计是否含 add：
- 删除幅度（spec 砍掉的旧逻辑）≈ 700-730 行
- 净行数（含新写代码）≈ 210-270 行

复杂度大幅下降（guard 控制层 1024 → 300 行）才是头条指标，行数只是侧标。
