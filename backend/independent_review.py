"""Independent review agent for the S5 review flow."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterator

import httpx
from openai import OpenAI

from .config import Settings
from .skill import SkillEngine
from .stream_parsing import ThinkingStreamParser


Event = dict[str, object]


MAX_DRAFT_WORDS_FOR_REVIEW = 100000


INDEPENDENT_REVIEW_SYSTEM_PROMPT = """你是独立审查代理。你的任务是对咨询报告的草稿做独立、客观的审查，不参与写作、不修改任何文件以外的内容。

## 你将读取的文件

必读：
- plan/data-log.md — 资料与数据登记
- plan/analysis-notes.md — 分析沉淀
- content/report_draft_v1.md — 报告正文草稿
- plan/references.md — 引用清单
- plan/project-overview.md — 项目元信息（交付边界、报告类型）
- plan/outline.md — 报告大纲（核对结构与正文匹配）

可选（存在则读，不存在跳过）：
- plan/research-plan.md — 研究设计（核对分析路径与原计划匹配）

## 你的审查维度（5 条，缺一不可，**全部 5 个章节必须输出**）

### 1. 结论-证据一致性
每个核心结论是否能追溯到 data-log / analysis-notes 的具体支撑？引用的数据方向是否真的支持结论（不是相关词出现就当支持）？

### 2. 关键假设与逻辑链
问题→分析→结论→建议链条有无跳跃、断层？隐含假设是否被明确暴露给读者？如果某个关键假设不成立，结论会不会垮？

### 3. 数据口径一致性
同一指标（市场规模、增速、份额、政策口径等）在不同章节出现时是否口径统一、数字不打架？

### 4. 建议可执行性
每条建议是否回答了"谁来做、做什么、何时、优先级"？空话建议（"加强 X / 提升 Y / 推动 Z"）必须直接点名。

### 5. 语言专业性与去 AI 味
目标是**客观、克制、专业、第三人称**——不是"像真人聊天"。逐处标出下列 AI 写作痕迹（给位置 + 原文片段 + 命中类型 + 修改方向，不要替用户写成稿）：
- 空洞拔高 / 意义夸张：「标志着关键转折点」「彰显了其重要意义」「不断演变的格局」「奠定了基础」「深深植根于」→ 换具体事实/数字。
- 句尾空分词：「……，凸显了其重要性」「……，反映了深厚联系」→ 删尾巴或落到具体机制。
- 宣传/广告形容词：「充满活力的」「深刻的」「致力于」「令人叹为观止的」「开创性的」→ 删空形容词。
- 模糊归因：「专家认为」「行业报告显示」却不给出处 → 落到具体来源+年份（接 data-log）。
- AI 高频词机械堆砌：「此外」「至关重要」「深入探讨」「赋能/增强/培养（空动词）」→ 按机械堆砌/空泛搭配判，**不一刀切实词**（「关键路径」「竞争格局」是合法行话）。
- 回避系动词：「作为/充当……的存在」→ 复位为「是」。
- 否定式排比：「不仅……而且」「这不仅仅是 X，而是 Y」→ 直陈。
- 填充短语 / 叠加 hedging：「为了实现这一目标」「值得注意的是数据显示」「可能潜在地或许会产生一些影响」→ 删冗余、给有据判断。
- 通用积极结论：「前景光明」「激动人心的时代」「追求卓越的旅程」→ 落到具体行动/数字/时间表。
- 机械排版：揭示前破折号、机械加粗、`**小标题：**` 内联列表 → 收敛；emoji 装饰 → 禁。
- 凑数三段式：删为"显得全面"凑的三项；**保留**有内在逻辑的 MECE 并列（短期/中期/长期 等）。
- 术语不一致 / 同义词循环 → 固定称谓。
- 后台语气泄漏：「希望这对您有帮助」「当然！」「好问题」「根据我的训练」→ 删。
**反向拦截（命中即算问题，绝不当目标）**：注入个性/情绪自白（「这令人不安」）、第一人称「我」、跑题/半成型想法、把"客观中立第三人称的百科/新闻稿质感"当缺陷——这些会拉低咨询专业度。

## 输出格式

写一个 markdown 文件到 plan/independent-review.md。结构严格如下（标题层级、维度顺序、章节命名都不要改）：

# 独立审查报告

**审查时间**：[ISO 8601 当前时间]
**审查代理**：DeepSeek V4 Pro · independent-review
**审查范围**：data-log / analysis-notes / report_draft_v1 / references / outline (+ research-plan)

---

## 1. 结论-证据一致性

### 1.1 [一句话判断]
- **位置**：[报告第几章 / 第几段，越具体越好]
- **原文**：[引用 1-3 句]
- **问题**：[一句话分析]
- **修改方向**：[方向性建议，不要写具体句子]

（每个维度下 1-N 条 issue；如果该维度未发现问题，**仍要保留该维度的章节标题**，并在标题下写"未发现问题"）

## 2. 关键假设与逻辑链
...

## 3. 数据口径一致性
...

## 4. 建议可执行性
...

## 5. 语言专业性与去 AI 味
...

---

## 总体判断

[partner review 风格 1-2 段：判断报告整体可发可不发；如不可发，哪几个 issue 必须先改]

<!-- independent-review:complete -->

## 完成标记的硬性要求

报告**末尾必须**输出 `<!-- independent-review:complete -->` 这一行 HTML 注释。这是系统识别审查完成的契约。如果你写的报告里没有这行，系统会判定为不完整，用户会被要求重新审查。

5 个维度的 H2 章节标题（`## 1. 结论-证据一致性` 一直到 `## 5. 语言专业性与去 AI 味`）**必须全部出现**——即使某维度无问题也要写"## X. [维度名]\n\n未发现问题"，不能省略。

## 语气规则

- **直接、有据**，绝不出现"建议考虑""可以探讨""值得思考"这类模糊词
- 每个 issue 必须有原文位置和具体修改方向
- **宁少而精**：5 条维度里没问题的维度写"未发现问题"，不要为了凑数胡编

## 工作流（边审边说，不要闷头干）

1. 每次调 read_file 前，先用一句话说你要读什么、想确认什么（例：「先看正文草稿，核对结论有没有数据支撑」）。
2. 读完用一句话说你看到了什么关键信息（不下结论，只描述你读到的事实）。
3. 全部读完后，在脑中按 5 个维度完成审查。
4. 一次性 write_file 把完整报告写到 plan/independent-review.md。
5. 写完后说一句「审查完成，报告已生成」。

（必读 6 个文件：plan/data-log.md、plan/analysis-notes.md、content/report_draft_v1.md、plan/references.md、plan/project-overview.md、plan/outline.md；plan/research-plan.md 存在则读。读的顺序你自己定。）

## 过程发言规则（硬约束）

- 你在对话里说的话是"过程旁白"——告诉用户你正在做什么、读到什么。
- **绝对不要**在对话里罗列审查发现、列 issue、下结论、给评分。所有发现、issue、结论**只写进 plan/independent-review.md 报告**。对话里出现"发现/问题/建议清单"即违规。
- 写完报告只说一句「审查完成，报告已生成」，不要把报告内容复述到对话里。

## 工具集

你只有两个工具：
- read_file(file_path) — 读项目文件
- write_file(file_path, content) — 写文件，但只能写到 plan/independent-review.md，其他路径会被拒绝

其他工具不可用。不要尝试调用 edit_file / append_report_draft / advance_stage / web_search / fetch_url。"""


CANONICAL_REVIEW_PATH = "plan/independent-review.md"
INDEPENDENT_REVIEW_COMPLETION_MARKER = SkillEngine.INDEPENDENT_REVIEW_COMPLETION_MARKER
INDEPENDENT_REVIEW_ANCHORS = SkillEngine.INDEPENDENT_REVIEW_ANCHORS
INDEPENDENT_REVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目文件内容",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写文件，只允许写 plan/independent-review.md",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
    },
]


def extract_latest_review_candidate_from_messages(messages) -> str | None:
    """从 provider-valid messages 重建候选报告正文（resume 用，不读旧 canonical / 不依赖进程内 buffer）。

    扫 messages：对每个 `write_file(plan/independent-review.md)` 的 assistant tool_call，**按
    `tool_call_id` 配对其后续 `role=tool` result message**、解析 result JSON 取 `status == "success"`
    （tool_call 本身无成功态，codex R2 NIT 2），取**最后一个成功**的那次 tool_call arguments 里的
    `content` 作候选。无成功 write_file → None（codex R1 BLOCKER 4：snapshot 不单独存 candidate_text，
    候选确定性地从 messages 重建）。"""
    # tool_call_id -> result status（从 role=tool 消息收集）
    result_status_by_id: dict[str, str] = {}
    for m in messages:
        if m.get("role") != "tool":
            continue
        tc_id = m.get("tool_call_id")
        if not tc_id:
            continue
        try:
            parsed = json.loads(m.get("content") or "{}")
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            result_status_by_id[tc_id] = parsed.get("status", "")

    latest_candidate: str | None = None
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if (fn.get("name") or "") != "write_file":
                continue
            tc_id = tc.get("id") or ""
            if not tc_id or result_status_by_id.get(tc_id) != "success":
                continue
            try:
                tc_args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                continue
            if not isinstance(tc_args, dict):
                continue
            if tc_args.get("file_path") != CANONICAL_REVIEW_PATH:
                continue
            latest_candidate = tc_args.get("content", "")
    return latest_candidate


class IndependentReviewAgent:
    """Independent S5 reviewer. A caller should hold the per-project lock while running."""

    MAX_ITERATIONS = 15

    def __init__(self, skill_engine: SkillEngine, settings: Settings):
        self.skill_engine = skill_engine
        self.settings = settings

    def _build_client(self) -> OpenAI:
        # Structured timeout (C4 / spec §7): bound connect/read/write/pool separately so a
        # provider that never sends a first byte can't pin the worker (and the review lock it
        # holds) for the full 120s. read=60 caps the no-first-token window per request.
        http_client = httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)
        )
        return OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.api_base,
            http_client=http_client,
        )

    def _resolve_model(self) -> str:
        if self.settings.mode == "managed":
            return self.settings.managed_model
        return self.settings.model

    def _should_send_explicit_tool_choice(self, active_model: str) -> bool:
        # DeepSeek's official thinking-mode examples rely on the default auto behavior.
        # Sending tool_choice="auto" can be rejected by official reasoner routes.
        return "deepseek" not in (active_model or "").lower()

    def _extract_reasoning_content_from_message(self, message) -> str:
        reasoning_content = getattr(message, "reasoning_content", None)
        if isinstance(reasoning_content, str) and reasoning_content:
            return reasoning_content

        model_dump = getattr(message, "model_dump", None)
        if not callable(model_dump):
            return ""

        try:
            dumped = model_dump()
        except Exception:
            return ""

        if isinstance(dumped, dict):
            reasoning_content = dumped.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content:
                return reasoning_content
        return ""

    @staticmethod
    def _serialize_tool_call(tool_call) -> dict:
        # Mirrors chat._serialize_tool_call_for_provider: accept either an SDK
        # object (non-stream message) or an accumulated dict (stream collected).
        if isinstance(tool_call, dict):
            function = tool_call.get("function") or {}
            return {
                "id": tool_call.get("id") or "",
                "type": tool_call.get("type") or "function",
                "function": {
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "",
                },
            }
        function = getattr(tool_call, "function", None)
        return {
            "id": getattr(tool_call, "id", "") or "",
            "type": getattr(tool_call, "type", "function") or "function",
            "function": {
                "name": getattr(function, "name", "") or "",
                "arguments": getattr(function, "arguments", "") or "",
            },
        }

    def _serialize_assistant_tool_call_message(self, message) -> dict:
        # Accept both the SDK message object (non-stream) and the dict we
        # accumulate during streaming. The two paths must serialize identically
        # to chat's _assistant_tool_call_message_from_response /
        # _normalize_collected_assistant_tool_call_message respectively, so the
        # DeepSeek follow-up contract (non-empty reasoning_content, no null
        # fields, paired tool_calls) is preserved.
        if isinstance(message, dict):
            content = message.get("content", "")
            tool_calls = message.get("tool_calls") or []
            reasoning_content = message.get("reasoning_content")
        else:
            content = getattr(message, "content", "")
            tool_calls = getattr(message, "tool_calls", None) or []
            reasoning_content = self._extract_reasoning_content_from_message(message)

        msg_dict = {
            "role": "assistant",
            "content": content if isinstance(content, str) else ("" if content is None else str(content)),
            # Always emit tool_calls (even []), matching chat's
            # _build_assistant_tool_call_message so the two serializers are byte-equal
            # for both empty and non-empty tool_calls.
            "tool_calls": [self._serialize_tool_call(tc) for tc in tool_calls],
        }
        if isinstance(reasoning_content, str) and reasoning_content:
            msg_dict["reasoning_content"] = reasoning_content
        return msg_dict

    MAX_VERIFY_SELF_CORRECTS = 2
    # 自修 corrective 的稳定前缀：既用于 append，也用于 resume 时从 messages 统计已用自修次数
    # （codex C3-review BLOCKER 2：自修预算计入会话历史、resume 不重置）。改文案务必保持此前缀稳定。
    VERIFY_CORRECTIVE_PREFIX = "[自修] 上一版审查报告不合格："

    def run(
        self,
        project_id: str,
        draft_word_count: int | None = None,
        cancel_event: threading.Event | None = None,
        run_id: str | None = None,
        store: "ReviewSessionStore | None" = None,
        resume_snapshot: dict | None = None,
        supplement: str | None = None,
    ) -> Iterator[Event]:
        def is_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        # store + run_id 是 run() 的硬依赖（codex C3-review BLOCKER 1）：在进入 provider 循环、
        # 读正文、做任何 LLM/工具调用之前就 fail-fast，避免无 store 的调用白烧一整轮 token 才报错
        # （之前只在 _commit_verified_candidate 提交前才拒）。
        if store is None or run_id is None:
            yield {"type": "error", "detail": "审查存储未就绪，无法运行审查，请重试"}
            return

        # ---- candidate staging 状态：候选只活在 messages 的 write_file tool_call
        # arguments + 此局部变量；snapshot 绝不单独存 candidate_text（codex R1 BLOCKER 4）。
        candidate_text: str | None = None
        self_correct_count = 0

        def snapshot_now(iteration_value: int) -> dict:
            return self._build_provider_valid_snapshot(
                messages,
                iteration_value,
                candidate_text is not None,
            )

        def cancel_and_return(iteration_value: int) -> Iterator[Event]:
            # cancel 落档（Step 5.5 / codex R2 BLOCKER 2）：return 前 set_errored（CAS：被 discard
            # 已清 record 时 no-op；断连时翻 errored 带完整 provider-valid snapshot 供精确 resume）。
            if store is not None and run_id is not None:
                store.set_errored(project_id, run_id, snapshot_now(iteration_value))
            yield {"type": "cancelled", "data": "客户端断开，已取消审查"}

        if is_cancelled():
            # 初始 early-cancel（断连发生在 run() 恢复 resume_snapshot 之前）。
            # codex C3-review BLOCKER 4（取 option ①）：claim_resume 已把唯一 errored snapshot 清出
            # record 并翻 running，此刻它只在 resume_snapshot 参数里——若直接 return cancelled 不落档，
            # 唯一续审快照彻底丢失。故 resume 场景把 resume_snapshot CAS 写回 errored 再退（断连可续）。
            # discard 已 pop record → set_errored run_id 失配 no-op（不复活被丢弃的 run）；首次审查无快照可保。
            if resume_snapshot is not None and store is not None and run_id is not None:
                store.set_errored(project_id, run_id, resume_snapshot)
            yield {"type": "cancelled", "data": "客户端断开，已取消审查"}
            return

        # 续审恢复：resume_snapshot 非空 → 用其 messages/review_written 恢复后接着循环（不重头、
        # 不读旧 canonical）。candidate 从 messages 重建（最后一次成功 write_file 的 arguments.content）。
        if resume_snapshot is not None:
            messages = list(resume_snapshot.get("messages") or [])
            if not messages:
                messages = [{"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT}]
            start_iteration = int(resume_snapshot.get("iteration") or 1)
            candidate_text = extract_latest_review_candidate_from_messages(messages)
            # codex C3-review BLOCKER 2：自修预算计入会话历史 → resume 时从已 append 的 corrective
            # 条数恢复已用次数，使整个 run（含多次 resume 续跑）总自修 ≤ MAX_VERIFY_SELF_CORRECTS、不重置。
            self_correct_count = sum(
                1
                for m in messages
                if m.get("role") == "user"
                and (m.get("content") or "").startswith(self.VERIFY_CORRECTIVE_PREFIX)
            )
        else:
            word_count = draft_word_count
            if word_count is None:
                try:
                    report_path = self.skill_engine.get_primary_report_path(project_id)
                    report_text = Path(report_path).read_text(encoding="utf-8")
                    word_count = self.skill_engine._count_words(report_text)
                except Exception as exc:
                    yield {"type": "error", "detail": f"读取正文失败：{str(exc)}"}
                    return

            if word_count > MAX_DRAFT_WORDS_FOR_REVIEW:
                yield {
                    "type": "error",
                    "detail": f"正文超过 100k 字（当前 {word_count} 字），暂不支持自动审查。建议先精简正文或拆分章节单独审查。",
                }
                return
            messages = [{"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT}]
            start_iteration = 1

        # supplement（续审补充指令）：末尾已是 user/corrective 则合并进该条；否则在 provider-valid
        # 边界后追加一条独立 user（避免连续 user 角色交替触发官渠 400）。
        if supplement:
            if messages and messages[-1].get("role") == "user":
                existing = messages[-1].get("content") or ""
                messages[-1]["content"] = (existing + "\n\n补充：" + supplement) if existing else supplement
            else:
                messages.append({"role": "user", "content": "补充：" + supplement})

        if is_cancelled():
            yield from cancel_and_return(start_iteration)
            return

        client = self._build_client()
        model = self._resolve_model()

        for iteration in range(start_iteration, self.MAX_ITERATIONS + 1):
            if is_cancelled():
                yield from cancel_and_return(iteration)
                return
            yield {"type": "progress", "step": "thinking", "detail": f"第 {iteration} 轮"}
            request_kwargs = {
                "model": model,
                "messages": messages,
                "tools": INDEPENDENT_REVIEW_TOOLS,
                "stream": True,
            }
            if self._should_send_explicit_tool_choice(model):
                request_kwargs["tool_choice"] = "auto"

            if is_cancelled():
                yield from cancel_and_return(iteration)
                return
            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                if store is not None and run_id is not None:
                    store.set_errored(project_id, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": f"模型调用失败：{str(exc)}"}
                return

            # ---- 小型流式解析器（参照 chat.py 主循环 emit_parsed_stream_events，审查版）----
            # content 经 ThinkingStreamParser 剥 <think> 后逐段 yield content_delta；
            # thinking / reasoning_content 只收集进回传 message，绝不 yield（审查窗口不展示思维链）。
            known_tool_names = {t["function"]["name"] for t in INDEPENDENT_REVIEW_TOOLS}
            collected = {"role": "assistant", "content": "", "tool_calls": []}
            parser = ThinkingStreamParser()
            accumulated = ""

            def drain(parsed_events):
                nonlocal accumulated
                for ev in parsed_events:
                    etype, edata = ev.get("type"), ev.get("data")
                    if not isinstance(edata, str) or not edata:
                        continue
                    if etype == "thinking":
                        collected["reasoning_content"] = collected.get("reasoning_content", "") + edata
                        continue  # 审查窗口不展示思维链
                    if etype == "content":
                        accumulated += edata
                        collected["content"] = accumulated
                        yield {"type": "content_delta", "text": edata}

            try:
                for chunk in response:
                    if is_cancelled():
                        # 流式中断：collected 尚未 append 进 messages，snapshot 仍 provider-valid。
                        yield from drain(parser.flush())
                        yield from cancel_and_return(iteration)
                        return
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    # 官渠 reasoning 走独立字段 delta.reasoning_content：收集回传、但不 yield。
                    reasoning_delta = self._extract_reasoning_content_from_message(delta)
                    if isinstance(reasoning_delta, str) and reasoning_delta:
                        collected["reasoning_content"] = collected.get("reasoning_content", "") + reasoning_delta
                    if delta.content:
                        yield from drain(parser.feed(delta.content))  # content 里若混入 <think> 也剥
                    if delta.tool_calls:
                        for tcc in delta.tool_calls:
                            # while（非 if）：填占位支持 index 乱序 / 跳号。
                            while tcc.index >= len(collected["tool_calls"]):
                                collected["tool_calls"].append(
                                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                )
                            tc = collected["tool_calls"][tcc.index]
                            if tcc.id:
                                tc["id"] = tcc.id
                            if tcc.function:
                                if tcc.function.name:
                                    tc["function"]["name"] += tcc.function.name
                                if tcc.function.arguments:
                                    tc["function"]["arguments"] += tcc.function.arguments
            except Exception as exc:
                yield from drain(parser.flush())
                if store is not None and run_id is not None:
                    store.set_errored(project_id, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": f"模型调用失败：{str(exc)}"}
                return
            yield from drain(parser.flush())

            tool_calls = collected["tool_calls"]
            if tool_calls:
                # 上游偶发把畸形 tool_call 流式 chunk 塞回来（未知工具名 / 坏 JSON
                # arguments / 缺 id）。直接回传会破坏 provider-valid 序列、触发官渠 400，
                # 因此本轮作废、不落历史，让模型下一轮重发。用一条纯文本 assistant + 一条
                # user corrective 做"合规隔板"，保持 user/model 严格交替（绝不裸 append
                # user，避免连续 user 触发官渠角色交替 400）。缺 id 是上游异常 / custom 模式
                # 不规范上游的防御——正常情况下 id 在 tool_call 首片就到、最终非空。
                malformed_reasons: list[str] = []
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    fn_name = fn.get("name", "") or ""
                    fn_args = fn.get("arguments", "") or ""
                    if not (tc.get("id") or ""):
                        malformed_reasons.append(f"缺 id 的 tool_call: {fn_name!r}")
                    if fn_name not in known_tool_names:
                        malformed_reasons.append(f"未知工具名: {fn_name!r}")
                        continue
                    if fn_args:
                        try:
                            json.loads(fn_args)
                        except json.JSONDecodeError as exc:
                            malformed_reasons.append(f"{fn_name} 参数 JSON 异常: {exc.msg}")
                if malformed_reasons:
                    yield {
                        "type": "tool_result",
                        "tool": "",
                        "status": "error",
                        "summary": "工具调用格式异常，本轮作废并让模型重发",
                    }
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "（上条工具调用格式异常，已作废本轮调用。）",
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "刚才的 tool_calls 格式异常（"
                                + "；".join(malformed_reasons)
                                + "）。请重新发起：每次只调用一个工具，等该工具返回后再发下一个。"
                            ),
                        }
                    )
                    continue

                messages.append(self._serialize_assistant_tool_call_message(collected))
                for tc in tool_calls:
                    if is_cancelled():
                        # 中断点 tool_call 未配 result：先补齐 result 再落档（snapshot provider-valid）。
                        yield from cancel_and_return(iteration)
                        return
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "") or ""
                    try:
                        tool_args = json.loads(fn.get("arguments", "") or "{}")
                    except Exception:
                        tool_args = {}

                    yield {"type": "tool_call", "tool": tool_name, "args": tool_args}
                    if is_cancelled():
                        yield from cancel_and_return(iteration)
                        return
                    result = self._execute_tool(project_id, tool_name, tool_args)
                    yield {
                        "type": "tool_result",
                        "tool": tool_name,
                        "status": result.get("status", "error"),
                        "summary": result.get("summary", ""),
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", "") or "",
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if (
                        tool_name == "write_file"
                        and result.get("status") == "success"
                        and tool_args.get("file_path") == CANONICAL_REVIEW_PATH
                    ):
                        # candidate staging：不直写 canonical，把候选正文存进局部变量。
                        candidate_text = tool_args.get("content", "")
                continue

            # 无 tool_call：content 已在流式阶段逐段 yield 过，这里不再一次性 yield content。
            messages.append(self._serialize_assistant_tool_call_message(collected))
            if is_cancelled():
                yield from cancel_and_return(iteration)
                return
            if candidate_text is None:
                # 从未写过候选——这是模型问题，不进自修（自修只针对"写了但不完整"）。
                if store is not None and run_id is not None:
                    store.set_errored(project_id, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": "审查代理未生成报告，请重试"}
                return
            review_error = self._verify_review_completeness(candidate_text)
            if review_error:
                # 校验失败自修（B4）：同 run append corrective + 重试，上限 2 次；仍失败 → errored 落档。
                if self_correct_count < self.MAX_VERIFY_SELF_CORRECTS:
                    self_correct_count += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"{self.VERIFY_CORRECTIVE_PREFIX}{review_error}。"
                                "请补全缺失的完成标记 / 5 个维度章节标题 / 实质正文后，"
                                "再一次性 write_file 写完整报告到 plan/independent-review.md。"
                            ),
                        }
                    )
                    continue
                if store is not None and run_id is not None:
                    store.set_errored(project_id, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": review_error}
                return
            # 校验通过 → 成功路径：先锁外写 temp，再 store guard 内原子替换 + 写 tombstone。
            yield from self._commit_verified_candidate(
                project_id, run_id, store, candidate_text, snapshot_now(iteration)
            )
            return

        if store is not None and run_id is not None:
            store.set_errored(project_id, run_id, snapshot_now(self.MAX_ITERATIONS))
        yield {"type": "error", "detail": f"审查超时（超过 {self.MAX_ITERATIONS} 轮），请重试"}

    def _commit_verified_candidate(
        self,
        project_id: str,
        run_id: str | None,
        store: "ReviewSessionStore | None",
        candidate_text: str,
        errored_snapshot: dict,
    ) -> Iterator[Event]:
        """成功路径：候选已校验通过。**store + run_id 是必需依赖、无直写回退**（plan:644 / spec §70/§86：
        canonical 替换只能在 store guard 下原子完成，codex C3-review BLOCKER 1）——缺任一则 fail-fast
        error，绝不静默直写 canonical。流程：**先锁外** mkstemp(canonical 同目录) + 写 candidate + close →
        store.atomic_commit_report（guard 内只做 run_id + status==running + 未 cancel 校验 → os.replace →
        stat → tombstone）；返回 None（run_id 失配 / status 非 running / cancel / os.replace 失败）→ 删 temp
        + yield error 让用户续审重试（cancel / os.replace 失败时 store 已 CAS 降级 errored 带 snapshot 供 resume）。"""
        if store is None or run_id is None:
            # defense-in-depth：run() 入口已对 None store/run_id fail-fast（codex C3-review NIT），
            # 正常流程到不了这里；保留此守卫以防本方法被直接调用时静默走直写旁路（去 footgun）。
            yield {"type": "error", "detail": "审查存储未就绪，无法保存审查报告，请重试"}
            return
        # canonical 绝对路径：get_project_path 返回 Optional[Path]，None fail-fast（避免异常变 500）。
        project_path = self.skill_engine.get_project_path(project_id)
        if project_path is None:
            store.set_errored(project_id, run_id, errored_snapshot)
            yield {"type": "error", "detail": "项目路径不存在，无法保存审查报告，请重试"}
            return
        canonical_abs_path = str((project_path / CANONICAL_REVIEW_PATH).resolve())
        canonical_dir = os.path.dirname(canonical_abs_path)
        try:
            os.makedirs(canonical_dir, exist_ok=True)
            # 锁外写 temp：同目录（os.replace 跨卷会失败）+ close（勿用仍打开的句柄 replace）。
            fd, temp_abs_path = tempfile.mkstemp(dir=canonical_dir, prefix=".independent-review-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(candidate_text)
            except Exception:
                try:
                    os.unlink(temp_abs_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            store.set_errored(project_id, run_id, errored_snapshot)
            yield {"type": "error", "detail": f"写入审查报告失败：{str(exc)}"}
            return

        report_mtime_ns = store.atomic_commit_report(
            project_id, run_id, temp_abs_path, canonical_abs_path, errored_snapshot
        )
        if report_mtime_ns is None:
            # run_id 失配 / status 非 running / 已 cancel / os.replace 失败：删 temp（atomic_commit 失败时
            # 未消费它），yield error 让用户续审重试最终替换（cancel / os.replace 失败已在 store 内 CAS
            # 降级 errored 带 snapshot）。无直写回退。
            try:
                if os.path.exists(temp_abs_path):
                    os.unlink(temp_abs_path)
            except OSError:
                pass
            yield {"type": "error", "detail": "审查报告保存被取消或失败，请重试"}
            return
        yield {
            "type": "review-completed",
            "path": CANONICAL_REVIEW_PATH,
            "report_mtime_ns": report_mtime_ns,
        }

    def _execute_tool(self, project_id: str, tool_name: str, args: dict) -> dict:
        if tool_name == "read_file":
            try:
                content = self.skill_engine.read_file(project_id, args.get("file_path", ""))
                return {"status": "success", "content": content, "summary": f"读取 {len(content)} 字"}
            except Exception as exc:
                return {"status": "error", "detail": str(exc), "summary": "读取失败"}

        if tool_name == "write_file":
            file_path = args.get("file_path", "")
            if file_path != CANONICAL_REVIEW_PATH:
                return {
                    "status": "error",
                    "detail": f"独立审查代理只能写 {CANONICAL_REVIEW_PATH}，请求被拒",
                    "summary": "路径不允许",
                }
            # candidate staging：不再直写 canonical。候选正文只活在本次 write_file 的
            # tool_call arguments（已在 messages）+ run() 局部 candidate_text；最终成功时由
            # run() 锁外写 temp、store guard 内原子替换 canonical（codex R1 BLOCKER 4 / spec §3.2）。
            # 这里只校验路径并回成功，让 run() 把 arguments.content 收作候选。
            return {"status": "success", "summary": "审查报告已写入"}

        return {"status": "error", "detail": f"未知工具 {tool_name}", "summary": "未知工具"}

    def _verify_review_completeness(self, candidate_text: str | None) -> str | None:
        """校验 candidate（不读旧 canonical）：marker + 5 维度 anchor + 实质正文。"""
        if candidate_text is None:
            return "审查报告未完整生成：缺少候选报告，请重试"
        text = candidate_text
        if INDEPENDENT_REVIEW_COMPLETION_MARKER not in text:
            return "审查报告缺少完成标记，请重试"
        missing_anchors = [anchor for anchor in INDEPENDENT_REVIEW_ANCHORS if anchor not in text]
        if missing_anchors:
            return "审查报告未完整生成：缺少审查维度章节，请重试"
        if not self.skill_engine._has_substantive_body(text):
            return "审查报告未完整生成：正文为空，请重试"
        return None

    @staticmethod
    def _build_provider_valid_snapshot(messages, iteration, review_written) -> dict:
        """构造 errored 落档 snapshot：只存"可直接发 provider 的完整 message 序列"。
        中断点若 assistant tool_call 未配 tool result（cancel mid tool loop），补齐缺失的
        tool result stub，使续审首个请求即 provider-valid（codex Step 5；不裁剪/不存 candidate_text）。"""
        normalized = [dict(m) for m in messages]
        # 找尾部任何 assistant tool_call 缺失的 tool result，按 tool_call_id 补 stub。
        existing_tool_ids = {
            m.get("tool_call_id")
            for m in normalized
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        padding: list[dict] = []
        for m in normalized:
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls") or []:
                tc_id = tc.get("id") or ""
                if tc_id and tc_id not in existing_tool_ids:
                    padding.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps(
                                {"status": "error", "summary": "审查中断，工具结果未生成"},
                                ensure_ascii=False,
                            ),
                        }
                    )
                    existing_tool_ids.add(tc_id)
        normalized.extend(padding)
        return {
            "messages": normalized,
            "iteration": iteration,
            "review_written": bool(review_written),
        }


_INDEPENDENT_REVIEW_LOCKS: dict[str, threading.Lock] = {}
_INDEPENDENT_REVIEW_LOCKS_GUARD = threading.Lock()


def get_independent_review_lock(project_id: str) -> threading.Lock:
    with _INDEPENDENT_REVIEW_LOCKS_GUARD:
        if project_id not in _INDEPENDENT_REVIEW_LOCKS:
            _INDEPENDENT_REVIEW_LOCKS[project_id] = threading.Lock()
        return _INDEPENDENT_REVIEW_LOCKS[project_id]


class ReviewSessionStore:
    """进程内续审存档 + 防过期写入 + 成功证据。per-project 至多一条 record。
    两把锁拆清职责：review lock（_INDEPENDENT_REVIEW_LOCKS，运行/续审串行，长跑 worker 持有）与
    store guard（本类，极短临界区，保护 record 原子读写）。store 读写只用 store guard，
    绝不依赖 review lock（否则 worker 跑着时 discard 拿不到锁、无法取消）。"""

    def __init__(self):
        self._guard = threading.Lock()
        # project_id -> {run_id, status, snapshot, cancel_event, report_mtime_ns}
        self._records: dict[str, dict] = {}

    def claim_first(self, project_id: str, run_id: str, cancel_event: threading.Event) -> bool:
        """首次发起：CAS 写 running，覆盖旧 errored/done tombstone。
        已有 active running → False（并发首发被拒，调用方须 release review lock）。"""
        with self._guard:
            rec = self._records.get(project_id)
            if rec and rec.get("status") == "running":
                return False
            self._records[project_id] = {
                "run_id": run_id, "status": "running",
                "snapshot": None, "cancel_event": cancel_event, "report_mtime_ns": None,
            }
            return True

    def claim_resume(self, project_id: str, run_id: str, cancel_event: threading.Event):
        """续审分派（等 review lock 后在 store guard 下重读再调）：
        ('errored', snapshot)→CAS running 取 snapshot 续跑；('done', mtime_ns)→已成功信号；('reject', None)→无记录/run_id 不匹配/已被 claim。"""
        with self._guard:
            rec = self._records.get(project_id)
            if not rec or rec.get("run_id") != run_id:
                return ("reject", None)
            if rec["status"] == "errored":
                snapshot = rec["snapshot"]
                rec["status"] = "running"
                rec["cancel_event"] = cancel_event
                rec["snapshot"] = None
                return ("errored", snapshot)
            if rec["status"] == "done":
                return ("done", rec.get("report_mtime_ns"))
            return ("reject", None)  # running：已被他人 claim

    def set_errored(self, project_id: str, run_id: str, snapshot: dict) -> bool:
        """worker 落 errored：CAS run_id 仍当前 **且 status==running** 才写
        （防丢弃后复活 B2 + 防 late cancel/error 把已 done 的 tombstone 覆盖成 errored，codex R3 BLOCKER 1）。
        done / 已 errored / record missing / run_id 失配 → no-op。"""
        with self._guard:
            rec = self._records.get(project_id)
            if not rec or rec.get("run_id") != run_id or rec.get("status") != "running":
                return False
            rec["status"] = "errored"
            rec["snapshot"] = snapshot
            return True

    def atomic_commit_report(self, project_id, run_id, temp_abs_path, canonical_abs_path, errored_snapshot):
        """成功路径最后一步。**temp 已由调用方锁外写好并 close**（spec §86 / codex R3 NIT 1：内容/写 temp 在 guard 外，
        guard 临界区极短、不让 discard 等文件写完）。store guard 内只做：校验 run_id 匹配 + **status==running**
        + 未 cancel → os.replace(temp→canonical) → stat st_mtime_ns → 写 tombstone。
        返回 report_mtime_ns（opaque str）。各失败分支（调用方都删 temp）：
        - run_id 失配 / status!=running（已 done/errored）→ None，不动 record（CAS 拒绝复活、不覆盖 done tombstone，
          codex C3-review BLOCKER 3：成功路径也要 status==running 护栏，与 set_errored/finalize 一致）。
        - 已 cancel（断连/discard）→ **CAS（status==running 才）降级 errored 带 errored_snapshot** 返回 None
          （codex C3-review BLOCKER 5：断连场景须留 snapshot 可续审，而非让 worker finally 清档；discard 已 pop
          record / run_id 失配在前面就 return None，不受影响）。
        - os.replace 失败 → 同样 CAS 降级 errored 带 snapshot 返回 None（供 resume 重试最终替换）。"""
        with self._guard:
            rec = self._records.get(project_id)
            # CAS 护栏：仅 run_id 匹配且 status==running 才动 record（成功路径 / 失败降级一致），
            # 防 done/errored 的 tombstone 被同 run_id 的迟到 commit 复活或覆盖。
            if not rec or rec.get("run_id") != run_id or rec.get("status") != "running":
                return None
            ce = rec.get("cancel_event")
            if ce is not None and ce.is_set():
                # 断连/discard：不脏写 canonical；status==running 已在上面保证 → CAS 翻 errored 带 snapshot，
                # 使断连场景可续审（discard 已 pop record，到不了这里）。
                rec["status"] = "errored"
                rec["snapshot"] = errored_snapshot
                return None
            try:
                os.replace(temp_abs_path, canonical_abs_path)  # 原子；temp 同目录、已 close（勿用仍打开的句柄）
            except Exception:
                # status==running 已保证（上面 CAS）→ 翻 errored 带 snapshot，供 resume 重试最终替换。
                rec["status"] = "errored"
                rec["snapshot"] = errored_snapshot
                return None
            mtime_ns = str(os.stat(canonical_abs_path).st_mtime_ns)  # opaque string（避 JS 2^53）
            rec["status"] = "done"
            rec["snapshot"] = None
            rec["report_mtime_ns"] = mtime_ns
            rec["cancel_event"] = None
            return mtime_ns

    def discard(self, project_id: str, run_id: str) -> bool:
        """用户主动关窗：store guard 下 run_id 匹配才 invalidate + set cancel_event（不等 review lock）。
        不匹配 no-op（防旧窗口延迟 discard 误杀新 run）。worker 下个 chunk/迭代检查到 cancel 即退。"""
        with self._guard:
            rec = self._records.get(project_id)
            if not rec or rec.get("run_id") != run_id:
                return False
            ce = rec.get("cancel_event")
            if ce is not None:
                ce.set()
            self._records.pop(project_id, None)
            return True

    def get_done_mtime(self, project_id, run_id) -> str | None:
        """run-bound 注入校验用（C5）：done tombstone 且 run_id 匹配 → 返回 report_mtime_ns（opaque str）；否则 None。"""
        with self._guard:
            rec = self._records.get(project_id)
            if rec and rec.get("run_id") == run_id and rec.get("status") == "done":
                return rec.get("report_mtime_ns")
            return None

    def finalize_orphan_running(self, project_id, run_id, fallback_snapshot=None) -> bool:
        """worker 退出兜底（C4 run_worker finally，codex R2 BLOCKER 2+4）：worker 任何路径退出后，
        若 record 仍 running（agent 没来得及落档的极端：未捕获异常绕过 set_errored、断连 cancel 后没落 snapshot），
        CAS 收敛——有 fallback_snapshot → 翻 errored（可 resume）；无 → 清 record（让用户可 first-run 重启，不留死 running）。
        done / 已 errored / 被 discard 清（record None 或 run_id 失配）→ no-op。"""
        with self._guard:
            rec = self._records.get(project_id)
            if not rec or rec.get("run_id") != run_id or rec.get("status") != "running":
                return False
            if fallback_snapshot is not None:
                rec["status"] = "errored"
                rec["snapshot"] = fallback_snapshot
            else:
                self._records.pop(project_id, None)
            return True


_REVIEW_SESSION_STORE = ReviewSessionStore()
