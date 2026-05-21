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


Event = dict[str, object]


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

## 工作流

1. 先 read_file 读上述 6 个必读文件（按你认为合理的顺序）；如果 plan/research-plan.md 存在也读
2. 在脑中按 5 个维度做完整审查
3. 一次性 write_file 把完整报告写到 plan/independent-review.md
4. 报告写完即结束，不做任何其他动作

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

    def _serialize_assistant_tool_call_message(self, message) -> dict:
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None) or []
        msg_dict = {
            "role": "assistant",
            "content": content if isinstance(content, str) else ("" if content is None else str(content)),
        }
        if tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": getattr(tc, "id", "") or "",
                    "type": getattr(tc, "type", "function") or "function",
                    "function": {
                        "name": getattr(getattr(tc, "function", None), "name", "") or "",
                        "arguments": getattr(getattr(tc, "function", None), "arguments", "") or "",
                    },
                }
                for tc in tool_calls
            ]
        reasoning_content = self._extract_reasoning_content_from_message(message)
        if reasoning_content:
            msg_dict["reasoning_content"] = reasoning_content
        return msg_dict

    def run(self, project_id: str, draft_word_count: int | None = None) -> Iterator[Event]:
        word_count = draft_word_count
        if word_count is None:
            try:
                report_path = self.skill_engine.get_primary_report_path(project_id)
                report_text = Path(report_path).read_text(encoding="utf-8")
                word_count = self.skill_engine._count_words(report_text)
            except Exception as exc:
                yield {"type": "error", "detail": f"读取正文失败：{str(exc)}"}
                return

        if word_count > 30000:
            yield {
                "type": "error",
                "detail": f"正文超过 30k 字（当前 {word_count} 字），暂不支持自动审查。建议先精简正文或拆分章节单独审查。",
            }
            return

        client = self._build_client()
        model = self._resolve_model()
        messages = [{"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT}]
        review_written = False

        for iteration in range(1, self.MAX_ITERATIONS + 1):
            yield {"type": "progress", "step": "thinking", "detail": f"第 {iteration} 轮"}
            request_kwargs = {
                "model": model,
                "messages": messages,
                "tools": INDEPENDENT_REVIEW_TOOLS,
                "stream": False,
            }
            if self._should_send_explicit_tool_choice(model):
                request_kwargs["tool_choice"] = "auto"

            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                yield {"type": "error", "detail": f"模型调用失败：{str(exc)}"}
                return

            message = response.choices[0].message
            messages.append(self._serialize_assistant_tool_call_message(message))

            content = getattr(message, "content", None)
            if content:
                yield {"type": "content", "text": content}

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                if not review_written:
                    yield {"type": "error", "detail": "审查代理未生成报告，请重试"}
                    return
                review_error = self._verify_review_completeness(project_id)
                if review_error:
                    yield {"type": "error", "detail": review_error}
                    return
                yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH}
                return

            for tool_call in tool_calls:
                tool_name = getattr(getattr(tool_call, "function", None), "name", "") or ""
                try:
                    tool_args = json.loads(getattr(tool_call.function, "arguments", "") or "{}")
                except Exception:
                    tool_args = {}

                yield {"type": "tool_call", "tool": tool_name, "args": tool_args}
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
                        "tool_call_id": getattr(tool_call, "id", "") or "",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if (
                    tool_name == "write_file"
                    and result.get("status") == "success"
                    and tool_args.get("file_path") == CANONICAL_REVIEW_PATH
                ):
                    review_written = True

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
