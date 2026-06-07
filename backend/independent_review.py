"""Independent review agent for the S5 review flow."""

from __future__ import annotations

import json
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
- plan/project-overview.md — 项目元信息（含目标读者、交付边界）
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

### 5. 目标读者匹配
术语密度、论证深度、前提假设是否匹配 project-overview 里写明的目标读者？

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

## 5. 目标读者匹配
...

---

## 总体判断

[partner review 风格 1-2 段：判断报告整体可发可不发；如不可发，哪几个 issue 必须先改]

<!-- independent-review:complete -->

## 完成标记的硬性要求

报告**末尾必须**输出 `<!-- independent-review:complete -->` 这一行 HTML 注释。这是系统识别审查完成的契约。如果你写的报告里没有这行，系统会判定为不完整，用户会被要求重新审查。

5 个维度的 H2 章节标题（`## 1. 结论-证据一致性` 一直到 `## 5. 目标读者匹配`）**必须全部出现**——即使某维度无问题也要写"## X. [维度名]\n\n未发现问题"，不能省略。

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

其他工具不可用。不要尝试调用 edit_file / append_report_draft / advance_stage / web_search / fetch_url / quality_check。"""


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


class IndependentReviewAgent:
    """Independent S5 reviewer. A caller should hold the per-project lock while running."""

    MAX_ITERATIONS = 15

    def __init__(self, skill_engine: SkillEngine, settings: Settings):
        self.skill_engine = skill_engine
        self.settings = settings

    def _build_client(self) -> OpenAI:
        http_client = httpx.Client(timeout=120.0)
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

    def run(
        self,
        project_id: str,
        draft_word_count: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[Event]:
        def is_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        def cancelled_event() -> Event:
            return {"type": "cancelled", "data": "客户端断开，已取消审查"}

        if is_cancelled():
            yield cancelled_event()
            return

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

        if is_cancelled():
            yield cancelled_event()
            return

        client = self._build_client()
        model = self._resolve_model()
        messages = [{"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT}]
        review_written = False

        for iteration in range(1, self.MAX_ITERATIONS + 1):
            if is_cancelled():
                yield cancelled_event()
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
                yield cancelled_event()
                return
            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as exc:
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
                        yield cancelled_event()
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
                        yield cancelled_event()
                        return
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "") or ""
                    try:
                        tool_args = json.loads(fn.get("arguments", "") or "{}")
                    except Exception:
                        tool_args = {}

                    yield {"type": "tool_call", "tool": tool_name, "args": tool_args}
                    if is_cancelled():
                        yield cancelled_event()
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
                        review_written = True
                continue

            # 无 tool_call：content 已在流式阶段逐段 yield 过，这里不再一次性 yield content。
            messages.append(self._serialize_assistant_tool_call_message(collected))
            if is_cancelled():
                yield cancelled_event()
                return
            if not review_written:
                yield {"type": "error", "detail": "审查代理未生成报告，请重试"}
                return
            review_error = self._verify_review_completeness(project_id)
            if review_error:
                yield {"type": "error", "detail": review_error}
                return
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH}
            return

        yield {"type": "error", "detail": f"审查超时（超过 {self.MAX_ITERATIONS} 轮），请重试"}

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
            try:
                self.skill_engine.write_file(project_id, file_path, args.get("content", ""))
                return {"status": "success", "summary": "审查报告已写入"}
            except Exception as exc:
                return {"status": "error", "detail": str(exc), "summary": "写入失败"}

        return {"status": "error", "detail": f"未知工具 {tool_name}", "summary": "未知工具"}

    def _verify_review_completeness(self, project_id: str) -> str | None:
        try:
            text = self.skill_engine.read_file(project_id, CANONICAL_REVIEW_PATH)
        except Exception:
            return "读取审查报告失败，请重试"
        if INDEPENDENT_REVIEW_COMPLETION_MARKER not in text:
            return "审查报告缺少完成标记，请重试"
        missing_anchors = [anchor for anchor in INDEPENDENT_REVIEW_ANCHORS if anchor not in text]
        if missing_anchors:
            return "审查报告未完整生成：缺少审查维度章节，请重试"
        if not self.skill_engine._has_substantive_body(text):
            return "审查报告未完整生成：正文为空，请重试"
        return None


_INDEPENDENT_REVIEW_LOCKS: dict[str, threading.Lock] = {}
_INDEPENDENT_REVIEW_LOCKS_GUARD = threading.Lock()


def get_independent_review_lock(project_id: str) -> threading.Lock:
    with _INDEPENDENT_REVIEW_LOCKS_GUARD:
        if project_id not in _INDEPENDENT_REVIEW_LOCKS:
            _INDEPENDENT_REVIEW_LOCKS[project_id] = threading.Lock()
        return _INDEPENDENT_REVIEW_LOCKS[project_id]
