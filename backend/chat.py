import base64
import ipaddress
import json
import re
import requests
import socket
import time
from typing import Dict, List
from html import unescape
from urllib.parse import urlparse

from openai import OpenAI

from .config import Settings
from .context_policy import ResolvedContextPolicy, resolve_context_policy
from .skill import SkillEngine

try:
    import tiktoken
    _encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    _encoding = None


IMAGE_TOKEN_COST = 1024
MAX_BUDGET_FIT_ATTEMPTS = 6
STREAM_CONNECT_TIMEOUT_SECONDS = 15.0
STREAM_WRITE_TIMEOUT_SECONDS = 30.0
STREAM_POOL_TIMEOUT_SECONDS = 30.0
MANAGED_STREAM_READ_TIMEOUT_SECONDS = 180.0
CUSTOM_STREAM_READ_TIMEOUT_SECONDS = 90.0
SLOW_MODEL_STREAM_READ_TIMEOUT_SECONDS = 180.0


class ChatHandler:
    """对话处理器"""

    INTERCEPT_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")
    FETCH_URL_MAX_BYTES = 600_000
    FETCH_URL_MAX_CHARS = 12_000
    FETCH_URL_ALLOWED_CONTENT_TYPES = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
    )
    BLOCKED_HOSTNAMES = {
        "localhost",
        "host.docker.internal",
    }
    FILE_UPDATE_VERBS = (
        "已更新",
        "已经更新",
        "已同步",
        "已经同步",
        "已写入",
        "已经写入",
        "已记录",
        "已经记录",
        "已入档",
        "已经入档",
    )
    MAX_MISSING_WRITE_RETRIES = 2

    NON_PLAN_WRITE_ALLOW_KEYWORDS = [
        "确认大纲",
        "按这个大纲",
        "就按这个",
        "继续写",
        "继续下一章",
        "开始正文",
        "开始写正文",
        "写第一章",
        "写第二章",
        "写执行摘要",
        "继续完善",
        "继续撰写",
    ]
    def __init__(self, settings: Settings, skill_engine: SkillEngine):
        self.settings = settings
        self.skill_engine = skill_engine
        self._turn_context = {
            "can_write_non_plan": True,
            "web_search_disabled": False,
        }
        import httpx

        http_client = httpx.Client(timeout=120.0)
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            http_client=http_client,
        )

    def _build_stream_timeout(self, active_model: str):
        import httpx

        read_timeout = (
            MANAGED_STREAM_READ_TIMEOUT_SECONDS
            if self.settings.mode == "managed"
            else CUSTOM_STREAM_READ_TIMEOUT_SECONDS
        )
        if "v3.2" in active_model.lower():
            read_timeout = max(read_timeout, SLOW_MODEL_STREAM_READ_TIMEOUT_SECONDS)

        return httpx.Timeout(
            connect=STREAM_CONNECT_TIMEOUT_SECONDS,
            read=read_timeout,
            write=STREAM_WRITE_TIMEOUT_SECONDS,
            pool=STREAM_POOL_TIMEOUT_SECONDS,
        )

    def _format_provider_error(self, error: Exception, *, stream: bool) -> str:
        raw_message = str(error or "").strip()
        lowered = raw_message.lower()
        timeout_markers = ("timed out", "timeout", "readtimeout")

        if any(marker in lowered for marker in timeout_markers):
            if stream and self.settings.mode == "managed":
                return (
                    "默认通道响应较慢，本轮在等待上游流式结果时超时了。"
                    "请稍后重试，或把问题拆短一些后分步发送。"
                )
            if stream:
                return "上游模型在返回流式结果时超时了，请稍后重试。"
            return "上游模型响应超时，请稍后重试。"

        if not raw_message:
            return "API调用失败"
        return f"API调用失败: {raw_message}"

    def _get_active_model_name(self) -> str:
        if self.settings.mode == "managed":
            return (self.settings.managed_model or self.settings.model or "").strip()
        if self.settings.mode == "custom":
            return (self.settings.custom_model or self.settings.model or "").strip()
        return (self.settings.model or self.settings.managed_model or self.settings.custom_model or "").strip()

    def _resolve_context_policy(self) -> ResolvedContextPolicy:
        custom_effective_limit = None
        if self.settings.mode == "custom":
            custom_effective_limit = self.settings.custom_context_limit_override
        return resolve_context_policy(
            self._get_active_model_name(),
            custom_effective_limit=custom_effective_limit,
        )

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """预估消息列表的token数"""
        total = 0
        for msg in messages:
            total += self._estimate_message_tokens(msg)
        return total

    def _estimate_message_tokens(self, message: Dict) -> int:
        total = self._estimate_content_tokens(message.get("content", ""))

        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            total += self._estimate_text_tokens(str(tool_call_id))

        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                total += self._estimate_text_tokens(json.dumps(tool_call, ensure_ascii=False))
                continue
            total += self._estimate_text_tokens(str(tool_call.get("id", "")))
            total += self._estimate_text_tokens(str(tool_call.get("type", "")))
            function = tool_call.get("function") or {}
            total += self._estimate_text_tokens(str(function.get("name", "")))
            total += self._estimate_text_tokens(str(function.get("arguments", "")))

        return total

    def _estimate_content_tokens(self, content) -> int:
        if isinstance(content, str):
            return self._estimate_text_tokens(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if not isinstance(item, dict):
                    total += self._estimate_text_tokens(json.dumps(item, ensure_ascii=False))
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    total += self._estimate_text_tokens(item.get("text", ""))
                    continue
                if item_type == "image_url":
                    total += IMAGE_TOKEN_COST
                    continue
                total += self._estimate_text_tokens(json.dumps(item, ensure_ascii=False))
            return total
        return self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))

    def _estimate_text_tokens(self, text: str) -> int:
        if _encoding:
            return len(_encoding.encode(text)) + 4
        return int(len(text) * 0.6)

    def _fit_conversation_to_budget(
        self,
        conversation: List[Dict],
    ) -> tuple[List[Dict], int, bool, ResolvedContextPolicy]:
        policy = self._resolve_context_policy()
        current_conversation = conversation
        current_tokens = self._estimate_tokens(current_conversation)
        compressed = False

        if current_tokens <= policy.compress_threshold:
            return current_conversation, current_tokens, compressed, policy

        previous_tokens = current_tokens
        for _ in range(MAX_BUDGET_FIT_ATTEMPTS):
            next_conversation = self._compress_conversation(current_conversation)
            compressed = True
            next_tokens = self._estimate_tokens(next_conversation)
            if next_tokens <= policy.compress_threshold:
                return next_conversation, next_tokens, compressed, policy
            if next_conversation == current_conversation or next_tokens >= previous_tokens:
                break
            current_conversation = next_conversation
            previous_tokens = next_tokens

        raise ValueError("当前消息或附带材料过大，超过模型上下文预算，请缩短输入或减少附件。")

    def _build_usage_payload(
        self,
        current_tokens: int,
        policy: ResolvedContextPolicy,
        compressed: bool,
        usage_mode: str,
    ) -> Dict[str, int | bool | str]:
        return {
            "current_tokens": current_tokens,
            "max_tokens": policy.effective_context_limit,
            "effective_max_tokens": policy.effective_context_limit,
            "provider_max_tokens": policy.provider_context_limit,
            "compressed": compressed,
            "usage_mode": usage_mode,
        }

    def _get_request_max_tokens(self, policy: ResolvedContextPolicy) -> int:
        return policy.reserved_output_tokens

    def _compress_conversation(self, conversation: List[Dict]) -> List[Dict]:
        """压缩对话历史：保留system + LLM摘要 + 最近N条消息"""
        keep_n = self.settings.keep_recent_messages
        if len(conversation) <= keep_n + 2:
            return conversation

        system_msg = conversation[0]
        recent_start = self._find_recent_start(conversation, keep_n)
        recent_msgs = conversation[recent_start:]
        old_msgs = conversation[1:recent_start]

        max_old_msgs = 50
        if len(old_msgs) > max_old_msgs:
            old_msgs = old_msgs[-max_old_msgs:]

        summary_prompt = [
            {"role": "system", "content": (
                "你是一个对话摘要助手。请将以下对话历史压缩为简洁摘要，必须保留：\n"
                "1. 项目名称、报告类型、主题\n"
                "2. 已完成的章节和内容要点\n"
                "3. 用户提出的修改意见和偏好\n"
                "4. 当前工作进度和下一步计划\n"
                "5. 所有精确的名称、路径、数据、引用来源\n"
                "只输出摘要内容，不要加前缀。"
            )},
            {"role": "user", "content": json.dumps(old_msgs, ensure_ascii=False)},
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self._get_active_model_name(),
                messages=summary_prompt,
                temperature=0.3,
                max_tokens=2000,
                timeout=30.0,
            )
            summary = resp.choices[0].message.content
        except Exception:
            return [system_msg] + recent_msgs

        return [
            system_msg,
            {"role": "assistant", "content": f"[对话摘要]\n{summary}"},
            *recent_msgs,
        ]

    def _find_recent_start(self, conversation: List[Dict], keep_n: int) -> int:
        start = max(1, len(conversation) - keep_n)
        while start < len(conversation) and conversation[start].get("role") == "tool":
            assistant_index = self._find_tool_call_assistant_index(conversation, start)
            if assistant_index is not None:
                return assistant_index
            start += 1
        return start

    def _find_tool_call_assistant_index(self, conversation: List[Dict], tool_index: int) -> int | None:
        tool_call_id = conversation[tool_index].get("tool_call_id")
        for index in range(tool_index - 1, 0, -1):
            message = conversation[index]
            role = message.get("role")
            if role == "assistant":
                if self._assistant_has_tool_call(message, tool_call_id):
                    return index
                return None
            if role == "user":
                return None
        return None

    def _assistant_has_tool_call(self, message: Dict, tool_call_id: str | None) -> bool:
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return False
        if not tool_call_id:
            return True
        return any(
            isinstance(tool_call, dict) and tool_call.get("id") == tool_call_id
            for tool_call in tool_calls
        )

    def _normalize_project_file_path(self, file_path: str) -> str:
        return file_path.replace("\\", "/").lstrip("/").strip()

    def _extract_successful_write_path(self, func_name: str, arguments: str, result: Dict) -> str | None:
        if func_name != "write_file" or result.get("status") != "success":
            return None
        try:
            payload = json.loads(arguments)
        except Exception:
            return None
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return None
        return self._normalize_project_file_path(file_path)

    def _looks_like_outline_draft(self, assistant_message: str) -> bool:
        text = (assistant_message or "").strip()
        if not text:
            return False
        if "大纲" not in text:
            return False
        chapter_hits = len(re.findall(r"第\s*[一二三四五六七八九十0-9]+\s*章", text))
        heading_hits = len(re.findall(r"^\s*(?:[#*]|[-])", text, flags=re.MULTILINE))
        return chapter_hits >= 2 or ("报告大纲" in text and heading_hits >= 3)

    def _message_mentions_file_update(self, assistant_message: str, keywords: tuple[str, ...]) -> bool:
        text = (assistant_message or "").strip()
        if not text:
            return False
        if not any(verb in text for verb in self.FILE_UPDATE_VERBS):
            return False
        return any(keyword in text for keyword in keywords)

    def _expected_plan_writes_for_message(self, assistant_message: str) -> set[str]:
        text = (assistant_message or "").strip()
        if not text:
            return set()

        expected: set[str] = set()
        normalized_text = text.replace("**", "")

        if any(verb in normalized_text for verb in self.FILE_UPDATE_VERBS):
            for raw_path in re.findall(r"`([^`]+)`", normalized_text):
                normalized_path = self._normalize_project_file_path(raw_path)
                if normalized_path.startswith("plan/") and normalized_path.endswith(".md"):
                    expected.add(normalized_path)

        if self._looks_like_outline_draft(normalized_text):
            expected.add("plan/outline.md")

        if self._message_mentions_file_update(normalized_text, ("plan/progress.md", "progress.md", "当前任务", "项目进度")):
            expected.add("plan/progress.md")

        if self._message_mentions_file_update(normalized_text, ("plan/notes.md", "notes.md", "项目笔记", "核心技术共识", "备注")):
            expected.add("plan/notes.md")

        return expected

    def _get_missing_expected_writes(self, assistant_message: str, successful_writes: set[str]) -> list[str]:
        expected = self._expected_plan_writes_for_message(assistant_message)
        return sorted(path for path in expected if path not in successful_writes)

    def _build_missing_write_feedback(self, missing_files: list[str]) -> str:
        joined = "、".join(f"`{path}`" for path in missing_files)
        return (
            f"你刚刚声称已更新或已经给出了需要入档的内容，但本轮并未成功调用 `write_file` 写入 {joined}。"
            "不要口头汇报，也不要继续推进下一阶段。"
            "请先用 `write_file` 完成这些文件落盘，再用一句话说明实际已写入哪些文件。"
        )

    def chat_stream(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        transient_attachments: List[Dict] | None = None,
        max_iterations: int = 10,
    ):
        """流式处理对话，yield 每个 chunk"""
        if len(user_message) > 10000:
            yield {"type": "error", "data": "消息过长，请控制在10000字符以内。"}
            return

        history = self._load_conversation(project_id)
        current_user_message = self._build_persisted_user_message(
            user_message=user_message,
            attached_material_ids=attached_material_ids or [],
        )
        self._turn_context = self._build_turn_context(project_id, user_message)
        conversation = self._build_provider_conversation(
            project_id,
            history,
            {
                **current_user_message,
                "transient_attachments": transient_attachments or [],
            },
        )
        active_model = self._get_active_model_name()

        total_tokens = 0
        iterations = 0
        missing_write_retries = 0
        assistant_message = ""
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()

        while iterations < max_iterations:
            try:
                conversation, _, iteration_compressed, policy = self._fit_conversation_to_budget(conversation)
                compressed = compressed or iteration_compressed
            except ValueError as exc:
                self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
                yield {"type": "error", "data": str(exc)}
                return

            for retry in range(2):
                try:
                    response = self.client.chat.completions.create(
                        model=active_model,
                        messages=conversation,
                        temperature=0.7,
                        max_tokens=self._get_request_max_tokens(policy),
                        tools=self._get_tools(),
                        tool_choice="auto",
                        timeout=self._build_stream_timeout(active_model),
                        stream=True,
                    )
                    break
                except Exception as e:
                    if retry < 1:
                        time.sleep(2)
                        continue
                    yield {"type": "error", "data": self._format_provider_error(e, stream=True)}
                    return

            collected_message = {"role": "assistant", "content": "", "tool_calls": []}
            try:
                for chunk in response:
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta
                    if delta.content:
                        collected_message["content"] += delta.content
                        yield {"type": "content", "data": delta.content}

                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            if tc_chunk.index >= len(collected_message["tool_calls"]):
                                collected_message["tool_calls"].append({
                                    "id": tc_chunk.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                })

                            tc = collected_message["tool_calls"][tc_chunk.index]
                            if tc_chunk.id:
                                tc["id"] = tc_chunk.id
                            if tc_chunk.function:
                                if tc_chunk.function.name:
                                    tc["function"]["name"] = tc_chunk.function.name
                                if tc_chunk.function.arguments:
                                    tc["function"]["arguments"] += tc_chunk.function.arguments
            except Exception as e:
                yield {"type": "error", "data": self._format_provider_error(e, stream=True)}
                return

            if collected_message["tool_calls"]:
                conversation.append(collected_message)
                for tool_call in collected_message["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    func_args = tool_call["function"]["arguments"]
                    yield {"type": "tool", "data": f"🔧 调用工具: {func_name}({func_args[:50]}...)"}

                    class ToolCall:
                        def __init__(self, data):
                            self.id = data["id"]
                            self.function = type("obj", (object,), {
                                "name": data["function"]["name"],
                                "arguments": data["function"]["arguments"],
                            })()

                    result = self._execute_tool(project_id, ToolCall(tool_call))
                    write_path = self._extract_successful_write_path(func_name, func_args, result)
                    if write_path:
                        successful_writes.add(write_path)
                    result_icon = "✅" if result.get("status") == "success" else "⚠️"
                    yield {"type": "tool", "data": f"{result_icon} 结果: {str(result)[:160]}..."}
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                iterations += 1
            else:
                candidate_message = collected_message["content"]
                missing_writes = self._get_missing_expected_writes(candidate_message, successful_writes)
                if missing_writes and missing_write_retries < self.MAX_MISSING_WRITE_RETRIES:
                    missing_write_retries += 1
                    yield {
                        "type": "tool",
                        "data": "⚠️ 检测到上条回复声称已更新文件但未实际写入，正在要求助手补做真实落盘。",
                    }
                    conversation.append({"role": "assistant", "content": candidate_message})
                    conversation.append({
                        "role": "user",
                        "content": self._build_missing_write_feedback(missing_writes),
                    })
                    continue
                assistant_message = candidate_message
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"
            yield {"type": "content", "data": assistant_message}

        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        if total_tokens == 0:
            total_tokens = self._estimate_tokens(conversation)

        yield {
            "type": "usage",
            "data": self._build_usage_payload(
                total_tokens,
                policy,
                compressed,
                usage_mode="estimated",
            ),
        }

    def chat(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        transient_attachments: List[Dict] | None = None,
        max_iterations: int = 5,
    ) -> dict:
        """处理对话，返回 {content, token_usage}"""
        if len(user_message) > 10000:
            return {"content": "消息过长，请控制在10000字符以内。", "token_usage": None}

        history = self._load_conversation(project_id)
        current_user_message = self._build_persisted_user_message(
            user_message=user_message,
            attached_material_ids=attached_material_ids or [],
        )
        self._turn_context = self._build_turn_context(project_id, user_message)
        conversation = self._build_provider_conversation(
            project_id,
            history,
            {
                **current_user_message,
                "transient_attachments": transient_attachments or [],
            },
        )
        active_model = self._get_active_model_name()

        total_tokens = 0
        iterations = 0
        missing_write_retries = 0
        assistant_message = ""
        usage_mode = "estimated"
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()
        while iterations < max_iterations:
            try:
                conversation, _, iteration_compressed, policy = self._fit_conversation_to_budget(conversation)
                compressed = compressed or iteration_compressed
            except ValueError as exc:
                self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
                return {"content": str(exc), "token_usage": None}

            for retry in range(2):
                try:
                    timeout = 120.0 if "v3.2" in active_model.lower() else 30.0
                    response = self.client.chat.completions.create(
                        model=active_model,
                        messages=conversation,
                        temperature=0.7,
                        max_tokens=self._get_request_max_tokens(policy),
                        tools=self._get_tools(),
                        tool_choice="auto",
                        timeout=timeout,
                    )
                    break
                except Exception as e:
                    if retry < 1:
                        time.sleep(2)
                        continue
                    return {"content": f"API调用失败: {str(e)}", "token_usage": None}

            if hasattr(response, "usage") and response.usage:
                total_tokens = getattr(response.usage, "total_tokens", 0) or 0
                usage_mode = "actual"
            else:
                total_tokens = 0
                usage_mode = "estimated"

            message = response.choices[0].message
            if message.tool_calls:
                try:
                    msg_dict = message.model_dump()
                except Exception:
                    msg_dict = {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                            }
                            for tc in message.tool_calls
                        ],
                    }
                conversation.append(msg_dict)
                for tool_call in message.tool_calls:
                    result = self._execute_tool(project_id, tool_call)
                    write_path = self._extract_successful_write_path(
                        tool_call.function.name,
                        tool_call.function.arguments,
                        result,
                    )
                    if write_path:
                        successful_writes.add(write_path)
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                iterations += 1
            else:
                candidate_message = message.content or ""
                missing_writes = self._get_missing_expected_writes(candidate_message, successful_writes)
                if missing_writes and missing_write_retries < self.MAX_MISSING_WRITE_RETRIES:
                    missing_write_retries += 1
                    conversation.append({"role": "assistant", "content": candidate_message})
                    conversation.append({
                        "role": "user",
                        "content": self._build_missing_write_feedback(missing_writes),
                    })
                    continue
                assistant_message = candidate_message
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"

        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        if total_tokens == 0:
            total_tokens = self._estimate_tokens(conversation)

        return {
            "content": assistant_message,
            "token_usage": self._build_usage_payload(
                total_tokens,
                policy,
                compressed,
                usage_mode=usage_mode,
            ),
        }

    def _build_provider_conversation(self, project_id: str, history: List[Dict], current_user_message: Dict) -> List[Dict]:
        conversation = [{"role": "system", "content": self._build_system_prompt(project_id)}]
        for message in history:
            provider_message = self._to_provider_message(project_id, message, include_images=False)
            if provider_message:
                conversation.append(provider_message)
        conversation.append(self._to_provider_message(project_id, current_user_message, include_images=True))
        return conversation

    def _build_persisted_user_message(self, user_message: str, attached_material_ids: List[str] | None = None) -> Dict:
        return {
            "role": "user",
            "content": user_message,
            "attached_material_ids": attached_material_ids or [],
        }

    def _to_provider_message(self, project_id: str, message: Dict, include_images: bool) -> Dict | None:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            return None
        if role == "assistant":
            return {"role": "assistant", "content": message.get("content", "")}

        attached_material_ids = message.get("attached_material_ids") or []
        transient_attachments = message.get("transient_attachments") or []
        if attached_material_ids or transient_attachments:
            return {
                "role": "user",
                "content": self._build_user_content(
                    project_id,
                    message.get("content", ""),
                    attached_material_ids,
                    transient_attachments=transient_attachments,
                    include_images=include_images,
                ),
            }
        return {"role": "user", "content": message.get("content", "")}

    def _build_user_content(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str],
        transient_attachments: List[Dict] | None = None,
        include_images: bool = True,
    ) -> List[Dict]:
        materials = [self.skill_engine.get_material(project_id, material_id) for material_id in attached_material_ids]
        note_lines = [user_message]
        if materials:
            note_lines.extend(["", "[本轮附带材料]"])
            for material in materials:
                note_lines.append(
                    f"- {material['id']} | {material['display_name']} | {material['source_type']} | {material['file_type']}"
                )
            note_lines.append("需要读取文本材料时，请调用 read_material_file。")

        content = [{"type": "text", "text": "\n".join(note_lines).strip()}]
        if include_images:
            for material in materials:
                if material["media_kind"] != "image_like":
                    continue
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": self._build_material_data_url(project_id, material["id"]),
                    },
                })
            for attachment in transient_attachments or []:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": attachment["data_url"],
                    },
                })
        return content

    def _build_material_data_url(self, project_id: str, material_id: str) -> str:
        material = self.skill_engine.get_material(project_id, material_id)
        material_path = self.skill_engine.get_material_path(project_id, material_id)
        encoded = base64.b64encode(material_path.read_bytes()).decode("ascii")
        mime_type = material.get("mime_type") or "application/octet-stream"
        return f"data:{mime_type};base64,{encoded}"

    def _get_tools(self):
        """定义可用工具"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入或更新项目文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径，如 plan/outline.md"},
                            "content": {"type": "string", "description": "文件内容"},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取项目文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径"},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_material_file",
                    "description": "读取项目材料的文本内容，适用于文档、表格、文本材料",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "material_id": {"type": "string", "description": "材料ID"},
                        },
                        "required": ["material_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索互联网获取最新信息、数据、案例等",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "读取指定网页正文内容，适合对搜索结果链接继续深入阅读",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "网页链接，必须是 http 或 https"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    def _execute_tool(self, project_id: str, tool_call):
        """执行工具调用"""
        import logging

        try:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if func_name == "write_file":
                if self._should_block_non_plan_write(project_id, args["file_path"]):
                    return {
                        "status": "error",
                        "message": "当前轮次还不能开始写正文，请先确认大纲或明确说“继续写正文”。",
                    }
                normalized_path = self.skill_engine.validate_plan_write(project_id, args["file_path"])
                self.skill_engine.write_file(project_id, normalized_path, args["content"])
                return {"status": "success", "message": f"已写入文件: {normalized_path}"}
            if func_name == "read_file":
                content = self.skill_engine.read_file(project_id, args["file_path"])
                return {"status": "success", "content": content}
            if func_name == "read_material_file":
                content = self.skill_engine.read_material_file(project_id, args["material_id"])
                return {"status": "success", "content": content}
            if func_name == "web_search":
                if self._turn_context.get("web_search_disabled"):
                    return {
                        "status": "error",
                        "message": "本轮 web_search 已因搜索服务错误被停用，请不要继续重试。",
                    }
                result = self._web_search(args["query"])
                if result.get("disable_for_turn"):
                    self._turn_context["web_search_disabled"] = True
                return {key: value for key, value in result.items() if key != "disable_for_turn"}
            if func_name == "fetch_url":
                return self._fetch_url(args["url"])
            return {"status": "error", "message": f"未知工具: {func_name}"}
        except json.JSONDecodeError as e:
            logging.error(f"工具参数解析失败: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": f"参数解析失败: {str(e)}"}
        except ValueError as e:
            logging.error(f"工具参数验证失败: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"工具执行异常: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": f"工具执行失败: {str(e)}"}

    def _web_search(self, query: str) -> Dict[str, str | bool]:
        """网络搜索（使用 SearXNG JSON API）"""
        import logging

        try:
            response = requests.get(
                self.settings.managed_search_api_url,
                params={"q": query, "format": "json"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"搜索服务暂时不可用（状态码：{response.status_code}）",
                    "disable_for_turn": True,
                }

            payload = response.json()
            raw_results = payload.get("results") or []
            if not isinstance(raw_results, list):
                return {
                    "status": "error",
                    "message": "搜索服务返回了不可识别的数据格式。",
                    "disable_for_turn": True,
                }

            results = []
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                title = self._strip_html(str(item.get("title", "")).strip())
                snippet = self._strip_html(str(item.get("content", "")).strip())
                url = str(item.get("url", "")).strip()
                if not title or not url:
                    continue
                results.append({
                    "title": title,
                    "snippet": snippet or "无摘要",
                    "url": url,
                })

            if not results:
                return {"status": "success", "results": "未找到相关信息"}

            output = "搜索结果：\n"
            for index, result in enumerate(results[:5], 1):
                output += (
                    f"{index}. {result['title']}\n"
                    f"{result['snippet'][:180]}\n"
                    f"链接: {result['url']}\n\n"
                )

            return {"status": "success", "results": output.strip()}
        except Exception as e:
            logging.error(f"搜索失败: {str(e)}")
            return {
                "status": "error",
                "message": "搜索功能暂时不可用，本轮已暂停继续搜索，请稍后重试。",
                "disable_for_turn": True,
            }

    def _fetch_url(self, url: str) -> Dict[str, str | bool]:
        parsed = self._validate_fetch_url(url)
        response = None

        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
                stream=True,
                allow_redirects=True,
            )

            final_url = response.url if isinstance(getattr(response, "url", None), str) else url
            self._validate_fetch_url(final_url)

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"网页抓取失败（状态码：{response.status_code}）",
                }

            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type and not any(token in content_type for token in self.FETCH_URL_ALLOWED_CONTENT_TYPES):
                return {
                    "status": "error",
                    "message": f"暂不支持读取该类型网页内容：{content_type}",
                }

            raw_text = self._read_response_text(response)
            if not raw_text.strip():
                return {"status": "error", "message": "网页内容为空，无法提取正文。"}

            title = self._extract_html_title(raw_text) or parsed.hostname or final_url
            if "text/plain" in content_type:
                content = raw_text.strip()
            else:
                content = self._extract_readable_text(raw_text)

            if not content.strip():
                return {"status": "error", "message": "网页正文提取失败。"}

            content, truncated = self._truncate_text(content)
            return {
                "status": "success",
                "title": title,
                "url": final_url,
                "content": content,
                "truncated": truncated,
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": f"网页抓取失败: {str(exc)}"}
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _validate_fetch_url(self, raw_url: str):
        parsed = urlparse((raw_url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("fetch_url 只支持 http/https 链接。")
        if not parsed.hostname:
            raise ValueError("链接无效，缺少主机名。")
        if parsed.username or parsed.password:
            raise ValueError("不支持带账号凭据的链接。")
        if parsed.hostname.lower() in self.BLOCKED_HOSTNAMES:
            raise ValueError("不允许访问本地或内网地址。")

        self._ensure_public_hostname(parsed.hostname)
        return parsed

    def _ensure_public_hostname(self, hostname: str):
        try:
            ip = ipaddress.ip_address(hostname)
            self._ensure_public_ip(ip)
            return
        except ValueError:
            pass

        try:
            resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"域名解析失败: {hostname}") from exc

        if not resolved:
            raise ValueError(f"域名解析失败: {hostname}")

        for entry in resolved:
            ip = ipaddress.ip_address(entry[4][0])
            if ip in self.INTERCEPT_PROXY_NETWORK:
                continue
            self._ensure_public_ip(ip)

    def _ensure_public_ip(self, ip):
        carrier_grade_nat = ip in ipaddress.ip_network("100.64.0.0/10")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or carrier_grade_nat
            or getattr(ip, "is_site_local", False)
        ):
            raise ValueError("不允许访问本地或内网地址。")

    def _read_response_text(self, response) -> str:
        chunks = []
        total = 0
        response_encoding = getattr(response, "encoding", None)
        encoding = response_encoding if isinstance(response_encoding, str) and response_encoding else "utf-8"

        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            remaining = self.FETCH_URL_MAX_BYTES - total
            if remaining <= 0:
                break
            piece = chunk[:remaining]
            chunks.append(piece)
            total += len(piece)
            if total >= self.FETCH_URL_MAX_BYTES:
                break

        return b"".join(chunks).decode(encoding, errors="ignore")

    def _extract_html_title(self, html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._strip_html(match.group(1))

    def _extract_readable_text(self, html_text: str) -> str:
        try:
            import trafilatura

            extracted = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=False,
                include_links=False,
                favor_precision=True,
            )
            if extracted:
                return extracted.strip()
        except Exception:
            pass

        return self._strip_html(html_text)

    def _truncate_text(self, text: str) -> tuple[str, bool]:
        normalized = text.strip()
        if len(normalized) <= self.FETCH_URL_MAX_CHARS:
            return normalized, False
        return normalized[: self.FETCH_URL_MAX_CHARS].rstrip(), True

    def _strip_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", "", value)
        text = unescape(text)
        return " ".join(text.split())

    def _load_conversation(self, project_id: str) -> List[Dict]:
        """加载对话历史。仅持久化 user/assistant 显示消息。"""
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return []

        conv_file = project_path / "conversation.json"
        if not conv_file.exists():
            return []

        raw_messages = json.loads(conv_file.read_text(encoding="utf-8"))
        normalized = []
        for message in raw_messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            normalized.append({
                "role": role,
                "content": self._extract_message_text(message.get("content", "")),
                "attached_material_ids": message.get("attached_material_ids", []),
            })
        return normalized

    def _save_conversation(self, project_id: str, conversation: List[Dict]):
        """保存对话历史"""
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return

        conv_file = project_path / "conversation.json"
        conv_file.write_text(
            json.dumps(conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _extract_message_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            return "\n".join(part for part in texts if part).strip()
        return json.dumps(content, ensure_ascii=False)

    def _build_system_prompt(self, project_id: str) -> str:
        """构建系统提示"""
        skill_prompt = self.skill_engine.get_skill_prompt()
        project_context = self.skill_engine.build_project_context(project_id)
        if self._turn_context.get("can_write_non_plan", True):
            turn_rule = "本轮如用户明确要求继续正文，可在更新 plan 后撰写正文。"
        else:
            turn_rule = (
                "本轮只能做两类事：1）继续问清关键信息；2）更新 `plan/` 内文件。"
                "在用户明确确认大纲或明确要求继续正文前，禁止写正文、章节草稿、report_draft 或最终报告。"
                "如果信息不足，提出问题后就停止本轮，不要擅自继续。"
            )
        return f"{skill_prompt}\n\n## 当前轮次约束\n{turn_rule}\n\n{project_context}"

    def _build_turn_context(self, project_id: str, user_message: str) -> Dict[str, bool]:
        return {
            "can_write_non_plan": self._should_allow_non_plan_write(project_id, user_message),
            "web_search_disabled": False,
        }

    def _should_allow_non_plan_write(self, project_id: str, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        if not normalized:
            return False

        if any(keyword in normalized for keyword in self.NON_PLAN_WRITE_ALLOW_KEYWORDS):
            return True

        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return False

        report_candidates = [
            project_path / "report_draft_v1.md",
            project_path / "content" / "report.md",
            project_path / "content" / "draft.md",
            project_path / "output" / "final-report.md",
        ]
        if any(path.exists() for path in report_candidates) and any(
            keyword in normalized for keyword in ["继续", "补充", "完善", "修改", "调整"]
        ):
            return True

        return False

    def _should_block_non_plan_write(self, project_id: str, file_path: str) -> bool:
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            return False
        return not normalized.startswith("plan/") and not self._turn_context.get("can_write_non_plan", True)
