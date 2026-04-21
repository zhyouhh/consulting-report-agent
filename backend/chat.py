import base64
import ipaddress
import json
import logging
import re
import requests
import socket
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List
from html import unescape
from urllib.parse import urljoin, urlparse

try:
    from curl_cffi import requests as curl_cffi_requests
except Exception:
    curl_cffi_requests = None

from openai import OpenAI

from .config import (
    Settings,
    get_search_cache_path,
    get_search_runtime_state_path,
    load_managed_search_pool_config,
)
from .context_policy import ResolvedContextPolicy, resolve_context_policy
from .models import SystemNotice
from .search_pool import SearchRouter
from .search_providers import (
    BraveProvider,
    ExaProvider,
    ProviderSearchResult,
    SearchItem,
    SerperProvider,
    TavilyProvider,
)
from .search_state import SearchStateStore
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
AUTO_COMPACT_TRIGGER_RATIO = 0.9
POST_COMPACT_SIDECAR_TARGET_BYTES = 24_000
_STAGE_ACK_MARKER = "<stage-ack"
_CONVERSATION_STATE_LOCKS: dict[str, threading.RLock] = {}
_CONVERSATION_STATE_LOCKS_GUARD = threading.Lock()
_PROJECT_REQUEST_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_REQUEST_LOCKS_GUARD = threading.Lock()
_SEARCH_ROUTER_SINGLETON: SearchRouter | None = None
_SEARCH_ROUTER_GUARD = threading.Lock()


def stream_split_safe_tail(buffer: str) -> tuple[str, str]:
    """Split buffer into (safe_to_emit_now, held_until_stream_close).

    Called by _chat_stream_unlocked after every new content delta is
    accumulated. Held portion must NOT be sent to the frontend until stream
    close and StageAckParser.strip() has scrubbed it.

    Rules:
      1. If "<stage-ack" occurs at position p, hold from p to end.
      2. Otherwise, if buffer's suffix is a prefix of "<stage-ack"
         (e.g., "<" / "<s" / "<stage-a"), hold that suffix.
      3. Otherwise, emit the whole buffer.

    Note: rule 1 uses `find`, not `rfind` - the earliest "<stage-ack"
    anchors the hold. Using rfind would match the '<' in a closing
    </stage-ack> and leak the opening "<stage-ack".
    """
    if not buffer:
        return "", ""

    idx = buffer.lower().find(_STAGE_ACK_MARKER)
    if idx != -1:
        return buffer[:idx], buffer[idx:]

    marker_len = len(_STAGE_ACK_MARKER)
    max_overlap = min(marker_len - 1, len(buffer))
    for overlap in range(max_overlap, 0, -1):
        suffix = buffer[-overlap:].lower()
        if _STAGE_ACK_MARKER.startswith(suffix):
            return buffer[:-overlap], buffer[-overlap:]

    return buffer, ""


def _get_project_request_lock(project_id: str) -> threading.RLock:
    """Module-level accessor for the per-project RLock."""
    lock_key = str(project_id or "")
    with _PROJECT_REQUEST_LOCKS_GUARD:
        lock = _PROJECT_REQUEST_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.RLock()
            _PROJECT_REQUEST_LOCKS[lock_key] = lock
    return lock


class ChatHandler:
    """对话处理器"""

    INTERCEPT_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")
    FETCH_URL_MAX_BYTES = 1_500_000
    FETCH_URL_MAX_CHARS = 12_000
    FETCH_URL_TIMEOUT_SECONDS = 15
    FETCH_URL_MAX_REDIRECTS = 5
    FETCH_URL_SUCCESS_CACHE_TTL_SECONDS = 900
    FETCH_URL_NEGATIVE_CACHE_TTL_SECONDS = 60
    FETCH_URL_CURL_CFFI_IMPERSONATE = "chrome"
    FETCH_URL_ALLOWED_CONTENT_TYPES = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
    )
    FETCH_URL_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
    }
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
    NON_PLAN_WRITE_ALLOWED_STAGE_CODES = {"S4", "S5", "S6", "S7", "done"}

    NON_PLAN_WRITE_ALLOW_KEYWORDS = [
        "确认大纲",
        "按这个大纲",
        "就按这个",
        "开始写",
        "开始写吧",
        "你开始写吧",
        "你开始写",
        "开始写报告",
        "开始起草",
        "继续写",
        "继续下一章",
        "开始正文",
        "开始写正文",
        "开始写正文吧",
        "写第一章",
        "写第二章",
        "写执行摘要",
        "继续完善",
        "继续撰写",
    ]
    NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS = [
        "继续",
        "补充",
        "完善",
        "修改",
        "调整",
        "接着",
        "扩写",
        "续写",
        "润色",
        "改写",
        "补写",
        "丰富",
    ]
    _S0_BLOCKED_PLAN_FILES = frozenset({
        "plan/outline.md",
        "plan/research-plan.md",
        "plan/data-log.md",
        "plan/analysis-notes.md",
    })
    _STRONG_ADVANCE_KEYWORDS = {
        "s0_interview_done_at": ["跳过访谈", "不用问了", "先写大纲吧", "够了开始吧", "直接开始"],
        "outline_confirmed_at": ["确认大纲", "大纲没问题", "按这个大纲写", "就这个大纲", "就按这个版本"],
        "review_started_at": ["开始审查", "进入审查", "可以审查了", "开始 review"],
        "review_passed_at": ["审查通过", "审查没问题", "报告可以交付"],
        "presentation_ready_at": ["演示准备好了", "演示准备完成", "PPT 完成", "讲稿完成"],
        "delivery_archived_at": ["归档结束项目", "项目交付完成", "交付归档"],
    }
    _ROLLBACK_KEYWORDS = {
        "outline_confirmed_at": ["大纲再改下", "大纲还要调整", "回去改大纲", "先别写了，大纲有问题"],
        "review_started_at": ["还要改报告", "再改改报告", "回到写作阶段", "暂停审查"],
        "review_passed_at": ["重新审查", "再看看", "审查没过"],
        "presentation_ready_at": ["演示再改", "讲稿还要调整"],
        "delivery_archived_at": ["还没归档", "撤回归档"],
    }
    _QUESTION_PATTERNS = [
        re.compile(r"(吗|么)[?？]?$"),
        re.compile(r"[?？]$"),
    ]
    _NEGATION_RE = re.compile(r"(不要|别|没|不是|不想|不|并非|非要|非得)[^。！？!?\n]{0,9}$")
    _NEGATION_WINDOW_CHARS = 10
    _STAGE_RANK = {
        "s0_interview_done_at": 0,
        "outline_confirmed_at": 1,
        "review_started_at": 2,
        "review_passed_at": 3,
        "presentation_ready_at": 4,
        "delivery_archived_at": 5,
    }

    def __init__(self, settings: Settings, skill_engine: SkillEngine):
        self.settings = settings
        self.skill_engine = skill_engine
        self._turn_context = self._new_turn_context(can_write_non_plan=True)
        self._fetch_url_cache: Dict[tuple[str, str, str], Dict[str, object]] = {}
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
        *,
        current_turn_start_index: int | None = None,
        return_current_turn_start_index: bool = False,
    ) -> tuple[List[Dict], int, bool, ResolvedContextPolicy] | tuple[List[Dict], int, bool, ResolvedContextPolicy, int]:
        policy = self._resolve_context_policy()
        current_conversation = conversation
        current_tokens = self._estimate_tokens(current_conversation)
        compressed = False

        effective_current_turn_start_index = self._get_budget_current_turn_start(
            current_conversation,
            minimum_index=1,
            current_turn_start_index=current_turn_start_index,
        )
        if current_tokens <= policy.compress_threshold:
            return self._fit_budget_result(
                current_conversation,
                current_tokens,
                compressed,
                policy,
                effective_current_turn_start_index,
                return_current_turn_start_index=return_current_turn_start_index,
            )

        system_message, summary_message, memory_items, visible_messages, current_turn_messages = (
            self._split_conversation_for_budget(
                current_conversation,
                current_turn_start_index=effective_current_turn_start_index,
            )
        )
        previous_tokens = current_tokens
        for _ in range(MAX_BUDGET_FIT_ATTEMPTS):
            next_visible_messages = visible_messages
            next_memory_items = memory_items
            if next_visible_messages:
                next_visible_messages = self._trim_oldest_visible_group(next_visible_messages)
            elif next_memory_items:
                next_memory_items = next_memory_items[1:]
            else:
                break

            next_conversation, effective_current_turn_start_index = self._compose_segmented_conversation(
                system_message,
                summary_message,
                next_memory_items,
                next_visible_messages,
                current_turn_messages,
            )
            compressed = True
            next_tokens = self._estimate_tokens(next_conversation)
            if next_tokens <= policy.compress_threshold:
                return self._fit_budget_result(
                    next_conversation,
                    next_tokens,
                    compressed,
                    policy,
                    effective_current_turn_start_index,
                    return_current_turn_start_index=return_current_turn_start_index,
                )
            if next_conversation == current_conversation or next_tokens >= previous_tokens:
                break
            current_conversation = next_conversation
            memory_items = next_memory_items
            visible_messages = next_visible_messages
            previous_tokens = next_tokens

        raise ValueError("当前消息或附带材料过大，超过模型上下文预算，请缩短输入或减少附件。")

    def _fit_budget_result(
        self,
        conversation: List[Dict],
        tokens: int,
        compressed: bool,
        policy: ResolvedContextPolicy,
        current_turn_start_index: int,
        *,
        return_current_turn_start_index: bool,
    ):
        if return_current_turn_start_index:
            return conversation, tokens, compressed, policy, current_turn_start_index
        return conversation, tokens, compressed, policy

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

    def _normalize_provider_usage(
        self,
        usage,
        policy: ResolvedContextPolicy,
        *,
        preflight_compaction_used: bool,
        post_turn_compaction_status: str = "not_needed",
    ) -> Dict[str, int | bool | str | dict | None]:
        raw_usage = self._usage_to_dict(usage)
        input_tokens = self._first_usage_value(raw_usage, "prompt_tokens", "input_tokens")
        output_tokens = self._first_usage_value(raw_usage, "completion_tokens", "output_tokens")
        total_tokens = self._first_usage_value(raw_usage, "total_tokens")
        cache_read_tokens = self._first_usage_value(
            raw_usage,
            "cache_read_tokens",
            "cached_tokens",
            nested_paths=(
                ("prompt_tokens_details", "cached_tokens"),
                ("input_tokens_details", "cached_tokens"),
            ),
        )
        cache_write_tokens = self._first_usage_value(
            raw_usage,
            "cache_write_tokens",
            nested_paths=(
                ("prompt_tokens_details", "cache_creation_input_tokens"),
                ("input_tokens_details", "cache_creation_input_tokens"),
            ),
        )
        reasoning_tokens = self._first_usage_value(
            raw_usage,
            "reasoning_tokens",
            nested_paths=(
                ("completion_tokens_details", "reasoning_tokens"),
                ("output_tokens_details", "reasoning_tokens"),
            ),
        )
        context_used_tokens = input_tokens if input_tokens is not None else total_tokens

        if input_tokens is not None:
            usage_source = "provider"
        elif total_tokens is not None:
            usage_source = "provider_partial"
        else:
            usage_source = "unavailable"

        return {
            "usage_source": usage_source,
            "context_used_tokens": context_used_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "reasoning_tokens": reasoning_tokens,
            "max_tokens": policy.effective_context_limit,
            "effective_max_tokens": policy.effective_context_limit,
            "provider_max_tokens": policy.provider_context_limit,
            "preflight_compaction_used": preflight_compaction_used,
            "post_turn_compaction_status": post_turn_compaction_status,
            "compressed": preflight_compaction_used,
            "raw_usage": raw_usage or None,
        }

    def _usage_to_dict(self, usage):
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return self._coerce_usage_value(usage.model_dump())
        if isinstance(usage, dict):
            return self._coerce_usage_value(usage)
        if hasattr(usage, "__dict__"):
            return self._coerce_usage_value(vars(usage))
        return {}

    def _coerce_usage_value(self, value):
        if isinstance(value, dict):
            return {key: self._coerce_usage_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._coerce_usage_value(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._coerce_usage_value(value.model_dump())
        if hasattr(value, "__dict__"):
            return self._coerce_usage_value(vars(value))
        return value

    def _first_usage_value(self, raw_usage: Dict, *keys: str, nested_paths: tuple[tuple[str, ...], ...] = ()):
        for key in keys:
            value = raw_usage.get(key)
            if value is not None:
                return value
        for path in nested_paths:
            value = self._read_nested_usage_value(raw_usage, path)
            if value is not None:
                return value
        return None

    def _read_nested_usage_value(self, raw_usage: Dict, path: tuple[str, ...]):
        current = raw_usage
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        return current

    def _should_retry_stream_without_usage(self, error: Exception) -> bool:
        message = str(error or "").lower()
        markers = (
            "stream_options",
            "include_usage",
            "unexpected keyword argument",
            "extra_forbidden",
            "unknown parameter",
            "unsupported parameter",
        )
        return any(marker in message for marker in markers)

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
        summary = self._summarize_messages(old_msgs)
        if not summary:
            return [system_msg] + recent_msgs

        return [
            system_msg,
            {"role": "assistant", "content": f"[对话摘要]\n{summary}"},
            *recent_msgs,
        ]

    def _summarize_messages(self, messages: List[Dict]) -> str | None:
        if not messages:
            return None
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
            {"role": "user", "content": json.dumps(messages, ensure_ascii=False)},
        ]
        try:
            resp = self.client.chat.completions.create(
                model=self._get_active_model_name(),
                messages=summary_prompt,
                temperature=0.3,
                max_tokens=2000,
                timeout=30.0,
            )
        except Exception:
            return None
        return (resp.choices[0].message.content or "").strip() or None

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

    def _empty_conversation_state(self) -> Dict:
        return {
            "version": 1,
            "events": [],
            "memory_entries": [],
            "compact_state": None,
        }

    def _get_conversation_state_path(self, project_id: str):
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return None
        return project_path / "conversation_state.json"

    def _get_compact_state_path(self, project_id: str):
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return None
        return project_path / "conversation_compact_state.json"

    def _rename_broken_sidecar(self, path):
        broken_path = path.with_name(
            f"{path.name}.broken-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )
        path.replace(broken_path)

    def _normalize_compact_state(self, payload: Dict | None) -> Dict | None:
        if not isinstance(payload, dict):
            return None

        summary_text = payload.get("summary_text")
        source_message_count = payload.get("source_message_count")
        if (
            not isinstance(summary_text, str)
            or not summary_text.strip()
            or not isinstance(source_message_count, int)
            or source_message_count < 0
        ):
            return None

        source_memory_entry_count = payload.get("source_memory_entry_count", 0)
        if not isinstance(source_memory_entry_count, int) or source_memory_entry_count < 0:
            source_memory_entry_count = 0

        normalized = dict(payload)
        normalized["source_memory_entry_count"] = source_memory_entry_count
        return normalized

    def _compact_state_is_drifted(
        self,
        compact_state: Dict,
        history: List[Dict] | None,
        memory_entries: List[Dict],
    ) -> bool:
        if history is not None and compact_state["source_message_count"] > len(history):
            return True
        return compact_state.get("source_memory_entry_count", 0) > len(memory_entries)

    def _load_legacy_compact_state_into_conversation_state(
        self,
        project_id: str,
        history: List[Dict] | None = None,
    ) -> Dict | None:
        lock = self._get_conversation_state_lock(project_id)
        with lock:
            compact_state_path = self._get_compact_state_path(project_id)
            if not compact_state_path or not compact_state_path.exists():
                return None

            try:
                payload = json.loads(compact_state_path.read_text(encoding="utf-8"))
            except Exception:
                self._rename_broken_sidecar(compact_state_path)
                return None

            compact_state = self._normalize_compact_state(payload)
            if not compact_state:
                self._rename_broken_sidecar(compact_state_path)
                return self._empty_conversation_state()

            state = self._empty_conversation_state()
            state["compact_state"] = compact_state
            if self._compact_state_is_drifted(compact_state, history, state["memory_entries"]):
                state["compact_state"] = None

            try:
                self._save_conversation_state_atomically(project_id, state)
            except Exception:
                return state

            compact_state_path.unlink(missing_ok=True)
            return state

    def _load_conversation_state(self, project_id: str, history: List[Dict] | None = None) -> Dict:
        lock = self._get_conversation_state_lock(project_id)
        with lock:
            state_path = self._get_conversation_state_path(project_id)
            empty_state = self._empty_conversation_state()
            if not state_path:
                return empty_state

            if not state_path.exists():
                migrated_state = self._load_legacy_compact_state_into_conversation_state(project_id, history)
                if migrated_state is not None:
                    return migrated_state
                return empty_state

            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                self._rename_broken_sidecar(state_path)
                return empty_state

            state = self._empty_conversation_state()
            if isinstance(payload, dict):
                if isinstance(payload.get("events"), list):
                    state["events"] = payload["events"]
                if isinstance(payload.get("memory_entries"), list):
                    state["memory_entries"] = payload["memory_entries"]
                state["compact_state"] = self._normalize_compact_state(payload.get("compact_state"))

            compact_state = state.get("compact_state")
            if compact_state and self._compact_state_is_drifted(compact_state, history, state["memory_entries"]):
                state["compact_state"] = None
                self._save_conversation_state_atomically(project_id, state)

            return state

    def _save_conversation_state_atomically(self, project_id: str, payload: Dict):
        state_path = self._get_conversation_state_path(project_id)
        if not state_path:
            return

        state = self._empty_conversation_state()
        if isinstance(payload, dict):
            if isinstance(payload.get("events"), list):
                state["events"] = payload["events"]
            if isinstance(payload.get("memory_entries"), list):
                state["memory_entries"] = payload["memory_entries"]
            state["compact_state"] = self._normalize_compact_state(payload.get("compact_state"))

        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = state_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(state_path)

    def _get_conversation_state_lock(self, project_id: str):
        lock_key = str(project_id or "")
        with _CONVERSATION_STATE_LOCKS_GUARD:
            lock = _CONVERSATION_STATE_LOCKS.get(lock_key)
            if lock is None:
                lock = threading.RLock()
                _CONVERSATION_STATE_LOCKS[lock_key] = lock
        return lock

    def _get_project_request_lock(self, project_id: str):
        return _get_project_request_lock(project_id)

    def _mutate_conversation_state(
        self,
        project_id: str,
        mutator: Callable[[Dict], Dict | None],
        history: List[Dict] | None = None,
    ) -> Dict:
        lock = self._get_conversation_state_lock(project_id)
        with lock:
            state = self._load_conversation_state(project_id, history)
            mutated = mutator(state)
            if mutated is None:
                mutated = state
            self._save_conversation_state_atomically(project_id, mutated)
            return mutated

    def _build_tool_persistence_metadata(
        self,
        project_id: str,
        func_name: str,
        args: Dict,
        result: Dict,
        extra: Dict | None = None,
    ) -> Dict | None:
        extra = extra or {}

        if func_name == "read_material_file":
            material_id = args.get("material_id")
            if not isinstance(material_id, str) or not material_id.strip():
                return None
            return {
                "category": "evidence",
                "source_key": f"material:{material_id}",
                "source_ref": material_id,
                "content": result.get("content"),
            }

        if func_name == "fetch_url":
            final_url = result.get("final_url") or result.get("url")
            if not isinstance(final_url, str) or not final_url.strip():
                return None
            metadata = {
                "category": "evidence",
                "source_key": f"url:{final_url}",
                "source_ref": final_url,
                "content": result.get("content"),
            }
            title = result.get("title")
            if isinstance(title, str) and title.strip():
                metadata["title"] = title
            return metadata

        if func_name == "read_file":
            normalized_path = extra.get("normalized_path")
            if not isinstance(normalized_path, str) or not normalized_path.strip():
                return None
            return {
                "category": "workspace",
                "source_key": f"file:{normalized_path}",
                "source_ref": normalized_path,
                "content": result.get("content"),
            }

        if func_name == "write_file":
            normalized_path = extra.get("normalized_path")
            if not isinstance(normalized_path, str) or not normalized_path.strip():
                return None
            return {
                "category": "workspace",
                "source_key": f"file:{normalized_path}",
                "source_ref": normalized_path,
                "content": args.get("content"),
            }

        return None

    def _build_tool_memory_entry(
        self,
        func_name: str,
        metadata: Dict,
        recorded_at: str,
    ) -> Dict | None:
        content = metadata.get("content")
        if not isinstance(content, str):
            return None
        if func_name == "fetch_url" and not content.strip():
            return None

        entry = {
            "category": metadata["category"],
            "source_key": metadata["source_key"],
            "content": content,
            "updated_at": recorded_at,
        }
        source_ref = metadata.get("source_ref")
        if isinstance(source_ref, str) and source_ref.strip():
            entry["source_ref"] = source_ref
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            entry["title"] = title
        return entry

    def _upsert_memory_entry(
        self,
        memory_entries: List[Dict],
        new_entry: Dict,
        *,
        covered_count: int = 0,
    ) -> List[Dict]:
        if covered_count < 0:
            covered_count = 0
        updated_entries = []
        for index, entry in enumerate(memory_entries):
            if (
                isinstance(entry, dict)
                and entry.get("category") == new_entry["category"]
                and entry.get("source_key") == new_entry["source_key"]
            ):
                if index < covered_count:
                    updated_entries.append(entry)
                continue
            updated_entries.append(entry)

        updated_entries.append(new_entry)
        return updated_entries

    def _persist_successful_tool_result(
        self,
        project_id: str,
        func_name: str,
        args: Dict,
        result: Dict,
        extra: Dict | None = None,
    ):
        if result.get("status") != "success":
            return

        metadata = self._build_tool_persistence_metadata(project_id, func_name, args, result, extra)
        if metadata is None:
            return

        try:
            recorded_at = datetime.now().isoformat(timespec="seconds")
            def mutate(state: Dict):
                event = {
                    "type": "tool_result",
                    "tool_name": func_name,
                    "category": metadata["category"],
                    "source_key": metadata["source_key"],
                    "recorded_at": recorded_at,
                }
                source_ref = metadata.get("source_ref")
                if isinstance(source_ref, str) and source_ref.strip():
                    event["source_ref"] = source_ref
                title = metadata.get("title")
                if isinstance(title, str) and title.strip():
                    event["title"] = title
                state["events"].append(event)

                memory_entry = self._build_tool_memory_entry(func_name, metadata, recorded_at)
                if memory_entry is not None:
                    compact_state = state.get("compact_state") or {}
                    covered_count = compact_state.get("source_memory_entry_count", 0)
                    state["memory_entries"] = self._upsert_memory_entry(
                        state["memory_entries"],
                        memory_entry,
                        covered_count=covered_count,
                    )
                return state

            self._mutate_conversation_state(project_id, mutate)
        except Exception:
            logging.warning(
                "工具成功结果写入 conversation_state 失败: project_id=%s tool=%s source_key=%s",
                project_id,
                func_name,
                metadata.get("source_key"),
                exc_info=True,
            )

    def _save_compact_state_atomically(
        self,
        project_id: str,
        payload: Dict,
        *,
        covered_event_count: int | None = None,
        covered_memory_entry_count: int | None = None,
    ):
        lock = self._get_conversation_state_lock(project_id)
        with lock:
            state = self._load_conversation_state(project_id)
            state["compact_state"] = self._normalize_compact_state(payload)
            if covered_event_count is not None or covered_memory_entry_count is not None:
                state = self._prune_compacted_sidecar_state(
                    state,
                    covered_event_count=covered_event_count or 0,
                    covered_memory_entry_count=covered_memory_entry_count or 0,
                )
            self._save_conversation_state_atomically(project_id, state)
            compact_state_path = self._get_compact_state_path(project_id)
            if compact_state_path and compact_state_path.exists():
                compact_state_path.unlink(missing_ok=True)

    def _prune_compacted_sidecar_state(
        self,
        state: Dict,
        *,
        covered_event_count: int,
        covered_memory_entry_count: int,
    ) -> Dict:
        remaining_memory_entries = (state.get("memory_entries") or [])[max(covered_memory_entry_count, 0):]
        slimmed_events = self._slim_compacted_events(
            state.get("events") or [],
            covered_event_count=covered_event_count,
        )
        compact_state = dict(state.get("compact_state") or {})
        compact_state["source_memory_entry_count"] = 0

        pruned_state = {
            **state,
            "events": self._trim_compacted_event_excerpts_if_needed(
                slimmed_events,
                compact_state=compact_state,
                remaining_memory_entries=remaining_memory_entries,
            ),
            "memory_entries": remaining_memory_entries,
            "compact_state": compact_state,
        }
        return pruned_state

    def _slim_compacted_events(self, events: List[Dict], *, covered_event_count: int) -> List[Dict]:
        if covered_event_count <= 0:
            return list(events)

        slimmed_events = []
        effective_covered_count = min(max(covered_event_count, 0), len(events))
        for index, event in enumerate(events):
            if index < effective_covered_count:
                slimmed_events.append(self._build_compacted_event_skeleton(event))
            else:
                slimmed_events.append(event)
        return slimmed_events

    def _build_compacted_event_skeleton(self, event) -> Dict:
        if not isinstance(event, dict):
            return {}

        skeleton = {}
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id.strip():
            skeleton["id"] = event_id
        else:
            recorded_at = event.get("recorded_at")
            if isinstance(recorded_at, str) and recorded_at.strip():
                skeleton["recorded_at"] = recorded_at

        event_kind = event.get("kind")
        if isinstance(event_kind, str) and event_kind.strip():
            skeleton["kind"] = event_kind

        event_type = event.get("type")
        if isinstance(event_type, str) and event_type.strip():
            skeleton["type"] = event_type

        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name.strip():
            skeleton["tool_name"] = tool_name

        source_key = event.get("source_key")
        if isinstance(source_key, str) and source_key.strip():
            skeleton["source_key"] = source_key

        source_ref = event.get("source_ref")
        if isinstance(source_ref, str) and source_ref.strip():
            skeleton["source_ref"] = source_ref

        title = event.get("title")
        if isinstance(title, str) and title.strip():
            skeleton["title"] = title

        excerpt = event.get("excerpt")
        if isinstance(excerpt, str) and excerpt:
            skeleton["excerpt"] = excerpt

        return skeleton

    def _trim_compacted_event_excerpts_if_needed(
        self,
        events: List[Dict],
        *,
        compact_state: Dict,
        remaining_memory_entries: List[Dict],
    ) -> List[Dict]:
        trimmed_events = list(events)
        if not trimmed_events:
            return trimmed_events

        state = {
            "version": 1,
            "events": trimmed_events,
            "memory_entries": remaining_memory_entries,
            "compact_state": compact_state,
        }
        if self._conversation_state_size_bytes(state) <= POST_COMPACT_SIDECAR_TARGET_BYTES:
            return trimmed_events

        for index, event in enumerate(trimmed_events):
            if not isinstance(event, dict) or "excerpt" not in event:
                continue
            next_event = dict(event)
            next_event.pop("excerpt", None)
            trimmed_events[index] = next_event
            state["events"] = trimmed_events
            if self._conversation_state_size_bytes(state) <= POST_COMPACT_SIDECAR_TARGET_BYTES:
                break
        return trimmed_events

    def _conversation_state_size_bytes(self, state: Dict) -> int:
        return len(json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))

    def _load_compact_state(self, project_id: str, history: List[Dict] | None = None) -> Dict | None:
        return self._load_conversation_state(project_id, history).get("compact_state")

    def _clear_compact_state(self, project_id: str):
        lock = self._get_conversation_state_lock(project_id)
        with lock:
            state_path = self._get_conversation_state_path(project_id)
            if state_path and state_path.exists():
                state = self._load_conversation_state(project_id)
                if state.get("compact_state") is not None:
                    self._mutate_conversation_state(
                        project_id,
                        lambda current_state: {**current_state, "compact_state": None},
                    )

            compact_state_path = self._get_compact_state_path(project_id)
            if compact_state_path and compact_state_path.exists():
                compact_state_path.unlink(missing_ok=True)

    def _clear_conversation_state_files(self, project_id: str):
        for path in (
            self._get_conversation_state_path(project_id),
            self._get_compact_state_path(project_id),
        ):
            if path and path.exists():
                path.unlink(missing_ok=True)

    def _finalize_post_turn_compaction(self, project_id: str, history: List[Dict], token_usage: Dict) -> Dict:
        if not token_usage:
            return token_usage

        context_used_tokens = token_usage.get("context_used_tokens")
        effective_max_tokens = token_usage.get("effective_max_tokens") or 0
        if context_used_tokens is None or effective_max_tokens <= 0:
            token_usage["post_turn_compaction_status"] = "skipped_unavailable"
            return token_usage

        if context_used_tokens / effective_max_tokens < AUTO_COMPACT_TRIGGER_RATIO:
            token_usage["post_turn_compaction_status"] = "not_needed"
            return token_usage

        summary_messages, state = self._build_memory_aware_history_messages(project_id, history)
        summary_text = self._summarize_messages(summary_messages)
        if not summary_text:
            token_usage["post_turn_compaction_status"] = "failed"
            return token_usage

        covered_event_count = len(state.get("events") or [])
        covered_memory_entry_count = len(state.get("memory_entries") or [])

        try:
            self._save_compact_state_atomically(
                project_id,
                {
                    "summary_text": summary_text,
                    "source_message_count": len(history),
                    "source_memory_entry_count": 0,
                    "last_compacted_at": datetime.now().isoformat(timespec="seconds"),
                    "post_turn_compaction_status": "completed",
                    "trigger_usage": {
                        "usage_source": token_usage.get("usage_source"),
                        "context_used_tokens": context_used_tokens,
                        "input_tokens": token_usage.get("input_tokens"),
                        "output_tokens": token_usage.get("output_tokens"),
                        "total_tokens": token_usage.get("total_tokens"),
                    },
                },
                covered_event_count=covered_event_count,
                covered_memory_entry_count=covered_memory_entry_count,
            )
        except Exception:
            token_usage["post_turn_compaction_status"] = "failed"
            return token_usage
        token_usage["post_turn_compaction_status"] = "completed"
        return token_usage

    def _build_compaction_summary_messages(self, project_id: str, history: List[Dict]) -> List[Dict]:
        summary_messages, _ = self._build_memory_aware_history_messages(project_id, history)
        return summary_messages

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

    def _is_first_data_log_write(self, project_id: str, normalized_path: str) -> bool:
        if normalized_path != "plan/data-log.md":
            return False
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return False
        data_log_path = project_path / "plan" / "data-log.md"
        if not data_log_path.exists():
            return True
        try:
            current_text = data_log_path.read_text(encoding="utf-8")
        except OSError:
            return False
        stripped = current_text.strip()
        if len(stripped) < 50:
            return True
        return self.skill_engine._is_template_content(current_text, "data-log.md")

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
                if self._is_expected_report_write_path(normalized_path):
                    expected.add(normalized_path)

        if self._looks_like_outline_draft(normalized_text):
            expected.add("plan/outline.md")

        if self._message_mentions_file_update(normalized_text, ("plan/progress.md", "progress.md", "当前任务", "项目进度")):
            expected.add("plan/progress.md")

        if self._message_mentions_file_update(normalized_text, ("plan/notes.md", "notes.md", "项目笔记", "核心技术共识", "备注")):
            expected.add("plan/notes.md")

        if self._message_mentions_file_update(normalized_text, ("plan/stage-gates.md", "stage-gates.md", "阶段门禁", "当前阶段")):
            expected.add("plan/stage-gates.md")

        if self._message_mentions_file_update(normalized_text, ("plan/tasks.md", "tasks.md", "任务清单", "阶段任务")):
            expected.add("plan/tasks.md")

        return expected

    def _is_expected_report_write_path(self, normalized_path: str) -> bool:
        return bool(
            re.fullmatch(r"report_draft_v\d+\.md", normalized_path)
            or re.fullmatch(r"(?:content|output)/[^/]+\.md", normalized_path)
        )

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

    def _chat_stream_unlocked(
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
        provider_user_message = {
            **current_user_message,
            "transient_attachments": transient_attachments or [],
        }
        active_model = self._get_active_model_name()

        iterations = 0
        missing_write_retries = 0
        assistant_message = ""
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()
        current_turn_messages: List[Dict] = []
        token_usage = self._normalize_provider_usage(
            None,
            policy,
            preflight_compaction_used=False,
        )

        while iterations < max_iterations:
            conversation, current_turn_start_index = self._build_provider_turn_conversation(
                project_id,
                history,
                provider_user_message,
                current_turn_messages=current_turn_messages,
                exclude_current_turn_memory=True,
            )
            try:
                conversation, _, iteration_compressed, policy, current_turn_start_index = self._fit_conversation_to_budget(
                    conversation,
                    current_turn_start_index=current_turn_start_index,
                    return_current_turn_start_index=True,
                )
                compressed = compressed or iteration_compressed
            except ValueError as exc:
                self._turn_context = self._new_turn_context(can_write_non_plan=True)
                yield {"type": "error", "data": str(exc)}
                return

            include_usage_requested = True
            for retry in range(2):
                request_kwargs = {
                    "model": active_model,
                    "messages": conversation,
                    "temperature": 0.7,
                    "max_tokens": self._get_request_max_tokens(policy),
                    "tools": self._get_tools(),
                    "tool_choice": "auto",
                    "timeout": self._build_stream_timeout(active_model),
                    "stream": True,
                }
                if include_usage_requested:
                    request_kwargs["stream_options"] = {"include_usage": True}
                try:
                    response = self.client.chat.completions.create(**request_kwargs)
                    break
                except Exception as e:
                    if include_usage_requested and self._should_retry_stream_without_usage(e):
                        include_usage_requested = False
                        continue
                    if retry < 1:
                        time.sleep(2)
                        continue
                    yield {"type": "error", "data": self._format_provider_error(e, stream=True)}
                    return

            collected_message = {"role": "assistant", "content": "", "tool_calls": []}
            known_tool_names = {tool["function"]["name"] for tool in self._get_tools()}
            announced_tool_call_indexes: set[int] = set()
            stream_usage = None
            accumulated = ""
            stream_buffer = ""
            try:
                for chunk in response:
                    if getattr(chunk, "usage", None) is not None:
                        stream_usage = chunk.usage
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta
                    if delta.content:
                        accumulated += delta.content
                        collected_message["content"] = accumulated
                        stream_buffer += delta.content
                        safe, held = stream_split_safe_tail(stream_buffer)
                        if safe:
                            yield {"type": "content", "data": safe}
                        stream_buffer = held

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
                                    tc["function"]["name"] += tc_chunk.function.name
                                if tc_chunk.function.arguments:
                                    tc["function"]["arguments"] += tc_chunk.function.arguments
                            if (
                                tc_chunk.index not in announced_tool_call_indexes
                                and tc["function"]["name"] in known_tool_names
                            ):
                                announced_tool_call_indexes.add(tc_chunk.index)
                                yield {"type": "tool", "data": f"🔧 准备调用工具: {tc['function']['name']}"}
            except Exception as e:
                yield {"type": "error", "data": self._format_provider_error(e, stream=True)}
                return

            if collected_message["tool_calls"]:
                current_turn_messages.append(collected_message)
                for index, tool_call in enumerate(collected_message["tool_calls"]):
                    func_name = tool_call["function"]["name"]
                    func_args = tool_call["function"]["arguments"]
                    tool_preview = f"🔧 调用工具: {func_name}"
                    if func_args:
                        tool_preview = f"{tool_preview}({func_args[:50]}...)"
                    yield {"type": "tool", "data": tool_preview}

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
                    for notice in self._turn_context.pop("pending_system_notices", []):
                        yield {
                            "type": "system_notice",
                            "category": notice["category"],
                            "path": notice.get("path"),
                            "reason": notice["reason"],
                            "user_action": notice["user_action"],
                        }
                    result_icon = "✅" if result.get("status") == "success" else "⚠️"
                    yield {"type": "tool", "data": f"{result_icon} 结果: {str(result)[:160]}..."}
                    current_turn_messages.append({
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
                    current_turn_messages.append({"role": "assistant", "content": candidate_message})
                    current_turn_messages.append({
                        "role": "user",
                        "content": self._build_missing_write_feedback(missing_writes),
                    })
                    continue
                assistant_message = candidate_message
                token_usage = self._normalize_provider_usage(
                    stream_usage,
                    policy,
                    preflight_compaction_used=compressed,
                )
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"
            yield {"type": "content", "data": assistant_message}
            accumulated = assistant_message
            stream_buffer = ""

        assistant_message = self._finalize_assistant_turn(project_id, assistant_message)
        already_emitted_len = len(accumulated) - len(stream_buffer)
        remainder = assistant_message[already_emitted_len:]
        if remainder:
            yield {"type": "content", "data": remainder}

        for notice in list(self._turn_context.get("pending_system_notices") or []):
            yield notice
        self._turn_context["pending_system_notices"] = []

        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        token_usage = self._finalize_post_turn_compaction(project_id, history, token_usage)
        self._turn_context = self._new_turn_context(can_write_non_plan=True)

        yield {
            "type": "usage",
            "data": token_usage,
        }

    def _chat_unlocked(
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
        provider_user_message = {
            **current_user_message,
            "transient_attachments": transient_attachments or [],
        }
        active_model = self._get_active_model_name()

        iterations = 0
        missing_write_retries = 0
        assistant_message = ""
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()
        current_turn_messages: List[Dict] = []
        token_usage = self._normalize_provider_usage(
            None,
            policy,
            preflight_compaction_used=False,
        )
        while iterations < max_iterations:
            conversation, current_turn_start_index = self._build_provider_turn_conversation(
                project_id,
                history,
                provider_user_message,
                current_turn_messages=current_turn_messages,
                exclude_current_turn_memory=True,
            )
            try:
                conversation, _, iteration_compressed, policy, current_turn_start_index = self._fit_conversation_to_budget(
                    conversation,
                    current_turn_start_index=current_turn_start_index,
                    return_current_turn_start_index=True,
                )
                compressed = compressed or iteration_compressed
            except ValueError as exc:
                self._turn_context = self._new_turn_context(can_write_non_plan=True)
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
                current_turn_messages.append(msg_dict)
                for tool_call in message.tool_calls:
                    result = self._execute_tool(project_id, tool_call)
                    write_path = self._extract_successful_write_path(
                        tool_call.function.name,
                        tool_call.function.arguments,
                        result,
                    )
                    if write_path:
                        successful_writes.add(write_path)
                    current_turn_messages.append({
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
                    current_turn_messages.append({"role": "assistant", "content": candidate_message})
                    current_turn_messages.append({
                        "role": "user",
                        "content": self._build_missing_write_feedback(missing_writes),
                    })
                    continue
                assistant_message = candidate_message
                token_usage = self._normalize_provider_usage(
                    getattr(response, "usage", None),
                    policy,
                    preflight_compaction_used=compressed,
                )
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"

        assistant_message = self._finalize_assistant_turn(project_id, assistant_message)
        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        token_usage = self._finalize_post_turn_compaction(project_id, history, token_usage)
        system_notices = [
            SystemNotice(
                category=notice["category"],
                path=notice.get("path"),
                reason=notice["reason"],
                user_action=notice["user_action"],
            )
            for notice in self._turn_context.pop("pending_system_notices", [])
        ]
        self._turn_context = self._new_turn_context(can_write_non_plan=True)

        return {
            "content": assistant_message,
            "token_usage": token_usage,
            "system_notices": system_notices or None,
        }

    def chat_stream(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        transient_attachments: List[Dict] | None = None,
        max_iterations: int = 10,
    ):
        request_lock = self._get_project_request_lock(project_id)
        with request_lock:
            yield from self._chat_stream_unlocked(
                project_id,
                user_message,
                attached_material_ids=attached_material_ids,
                transient_attachments=transient_attachments,
                max_iterations=max_iterations,
            )

    def chat(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        transient_attachments: List[Dict] | None = None,
        max_iterations: int = 5,
    ) -> dict:
        request_lock = self._get_project_request_lock(project_id)
        with request_lock:
            return self._chat_unlocked(
                project_id,
                user_message,
                attached_material_ids=attached_material_ids,
                transient_attachments=transient_attachments,
                max_iterations=max_iterations,
            )

    def _build_provider_conversation(self, project_id: str, history: List[Dict], current_user_message: Dict) -> List[Dict]:
        conversation, _ = self._build_provider_turn_conversation(
            project_id,
            history,
            current_user_message,
        )
        return conversation

    def _build_memory_aware_history_messages(
        self,
        project_id: str,
        history: List[Dict],
        *,
        exclude_memory_source_keys: set[str] | None = None,
    ) -> tuple[List[Dict], Dict]:
        history_messages: List[Dict] = []
        state = self._load_conversation_state(project_id, history)
        compact_state = state.get("compact_state")
        effective_history = history
        if compact_state:
            history_messages.append({
                "role": "assistant",
                "content": f"[对话摘要]\n{compact_state['summary_text']}",
            })
            effective_history = history[compact_state["source_message_count"]:]
        recent_memory_items = self._memory_items_from_state(
            state.get("memory_entries") or [],
            covered_count=compact_state.get("source_memory_entry_count", 0) if compact_state else 0,
            exclude_source_keys=exclude_memory_source_keys,
        )
        if recent_memory_items:
            history_messages.append(self._build_memory_block_message(recent_memory_items))
        for message in effective_history:
            provider_message = self._to_provider_message(project_id, message, include_images=False)
            if provider_message:
                history_messages.append(provider_message)
        return history_messages, state

    def _build_provider_turn_conversation(
        self,
        project_id: str,
        history: List[Dict],
        current_user_message: Dict,
        current_turn_messages: List[Dict] | None = None,
        *,
        exclude_current_turn_memory: bool = False,
    ) -> tuple[List[Dict], int]:
        conversation = [{"role": "system", "content": self._build_system_prompt(project_id)}]
        history_messages, _ = self._build_memory_aware_history_messages(
            project_id,
            history,
            exclude_memory_source_keys=(
                self._current_turn_successful_tool_source_keys(project_id, current_turn_messages)
                if exclude_current_turn_memory
                else None
            ),
        )
        conversation.extend(history_messages)
        conversation.append(self._to_provider_message(project_id, current_user_message, include_images=True))
        current_turn_start_index = len(conversation) - 1
        if current_turn_messages:
            conversation.extend(current_turn_messages)
        return conversation, current_turn_start_index

    def _memory_items_from_state(
        self,
        memory_entries: List[Dict],
        covered_count: int = 0,
        *,
        exclude_source_keys: set[str] | None = None,
    ) -> List[str]:
        if covered_count < 0:
            covered_count = 0
        excluded_source_keys = exclude_source_keys or set()
        items: List[str] = []
        for entry in memory_entries[covered_count:]:
            if not isinstance(entry, dict):
                continue
            source_key = entry.get("source_key")
            if isinstance(source_key, str) and source_key in excluded_source_keys:
                continue
            formatted = self._format_memory_entry_for_model(entry)
            if formatted:
                items.append(formatted)
        return items

    def _current_turn_successful_tool_source_keys(
        self,
        project_id: str,
        current_turn_messages: List[Dict] | None,
    ) -> set[str]:
        if not current_turn_messages:
            return set()

        tool_calls_by_id: dict[str, Dict] = {}
        for message in current_turn_messages:
            if message.get("role") != "assistant":
                continue
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    tool_calls_by_id[tool_call_id] = tool_call

        source_keys: set[str] = set()
        for message in current_turn_messages:
            if message.get("role") != "tool":
                continue

            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str):
                continue
            tool_call = tool_calls_by_id.get(tool_call_id)
            if not isinstance(tool_call, dict):
                continue

            function_payload = tool_call.get("function") or {}
            func_name = function_payload.get("name")
            arguments = function_payload.get("arguments")
            if not isinstance(func_name, str) or not func_name.strip():
                continue
            if not isinstance(arguments, str):
                continue

            try:
                args = json.loads(arguments)
                result = json.loads(message.get("content", ""))
            except Exception:
                continue
            if not isinstance(args, dict) or not isinstance(result, dict):
                continue
            if result.get("status") != "success":
                continue

            extra = None
            if func_name in {"read_file", "write_file"}:
                file_path = args.get("file_path")
                if not isinstance(file_path, str) or not file_path.strip():
                    continue
                try:
                    extra = {"normalized_path": self.skill_engine.normalize_file_path(project_id, file_path)}
                except ValueError:
                    continue

            metadata = self._build_tool_persistence_metadata(project_id, func_name, args, result, extra)
            source_key = metadata.get("source_key") if isinstance(metadata, dict) else None
            if isinstance(source_key, str) and source_key.strip():
                source_keys.add(source_key)

        return source_keys

    def _format_memory_entry_for_model(self, entry: Dict) -> str | None:
        content = entry.get("content")
        if not isinstance(content, str):
            return None
        content = content.strip()
        if not content:
            return None

        provenance = self._format_memory_entry_provenance(entry)
        if provenance:
            return f"来源: {provenance}\n{content}"
        return content

    def _format_memory_entry_provenance(self, entry: Dict) -> str | None:
        title = entry.get("title")
        source_ref = entry.get("source_ref")
        normalized_title = title.strip() if isinstance(title, str) and title.strip() else None
        normalized_source_ref = source_ref.strip() if isinstance(source_ref, str) and source_ref.strip() else None

        if normalized_title and normalized_source_ref and normalized_title != normalized_source_ref:
            return f"{normalized_title} | {normalized_source_ref}"
        return normalized_title or normalized_source_ref

    def _build_memory_block_message(self, memory_items: List[str]) -> Dict:
        return {
            "role": "assistant",
            "content": self._format_memory_block(memory_items),
        }

    def _format_memory_block(self, memory_items: List[str]) -> str:
        if not memory_items:
            return "[工作记忆]"
        return "[工作记忆]\n" + json.dumps(memory_items, ensure_ascii=False)

    def _is_summary_block_message(self, message: Dict) -> bool:
        return (
            message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message.get("content", "").startswith("[对话摘要]\n")
        )

    def _is_memory_block_message(self, message: Dict) -> bool:
        return (
            message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message.get("content", "").startswith("[工作记忆]")
        )

    def _split_memory_block_items(self, message: Dict) -> List[str]:
        content = message.get("content", "")
        if not isinstance(content, str):
            return []
        if content == "[工作记忆]":
            return []
        body = content[len("[工作记忆]"):].lstrip("\n").strip()
        if not body:
            return []
        try:
            payload = json.loads(body)
        except Exception:
            return [body]

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, str) and item]
        if isinstance(payload, dict):
            entries = payload.get("entries")
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, str) and item]
        return [body]

    def _split_conversation_for_budget(
        self,
        conversation: List[Dict],
        *,
        current_turn_start_index: int | None = None,
    ) -> tuple[Dict, Dict | None, List[str], List[Dict], List[Dict]]:
        system_message = conversation[0]
        index = 1
        summary_message = None
        if index < len(conversation) and self._is_summary_block_message(conversation[index]):
            summary_message = conversation[index]
            index += 1

        memory_items: List[str] = []
        if index < len(conversation) and self._is_memory_block_message(conversation[index]):
            memory_items = self._split_memory_block_items(conversation[index])
            index += 1

        current_turn_start = self._get_budget_current_turn_start(
            conversation,
            minimum_index=index,
            current_turn_start_index=current_turn_start_index,
        )
        visible_messages = conversation[index:current_turn_start]
        current_turn_messages = conversation[current_turn_start:]
        return system_message, summary_message, memory_items, visible_messages, current_turn_messages

    def _get_budget_current_turn_start(
        self,
        conversation: List[Dict],
        minimum_index: int,
        *,
        current_turn_start_index: int | None = None,
    ) -> int:
        candidate = current_turn_start_index
        if isinstance(candidate, int) and minimum_index <= candidate < len(conversation):
            return candidate

        for index in range(len(conversation) - 1, minimum_index - 1, -1):
            if conversation[index].get("role") == "user":
                return index
        return max(minimum_index, len(conversation) - 1)

    def _compose_segmented_conversation(
        self,
        system_message: Dict,
        summary_message: Dict | None,
        memory_items: List[str],
        visible_messages: List[Dict],
        current_turn_messages: List[Dict],
    ) -> tuple[List[Dict], int]:
        conversation = [system_message]
        if summary_message is not None:
            conversation.append(summary_message)
        if memory_items:
            conversation.append(self._build_memory_block_message(memory_items))
        conversation.extend(visible_messages)
        conversation.extend(current_turn_messages)
        return conversation, len(conversation) - len(current_turn_messages)

    def _trim_oldest_visible_group(self, visible_messages: List[Dict]) -> List[Dict]:
        if not visible_messages:
            return visible_messages

        first_message = visible_messages[0]
        if first_message.get("role") == "user":
            trim_end = 1
            while trim_end < len(visible_messages) and visible_messages[trim_end].get("role") != "user":
                trim_end += 1
            return visible_messages[trim_end:]

        if first_message.get("role") == "assistant" and first_message.get("tool_calls"):
            tool_call_ids = {
                tool_call.get("id")
                for tool_call in first_message.get("tool_calls") or []
                if isinstance(tool_call, dict) and tool_call.get("id")
            }
            trim_end = 1
            while (
                trim_end < len(visible_messages)
                and visible_messages[trim_end].get("role") == "tool"
                and (
                    not tool_call_ids
                    or visible_messages[trim_end].get("tool_call_id") in tool_call_ids
                )
            ):
                trim_end += 1
            return visible_messages[trim_end:]

        if first_message.get("role") == "tool":
            trim_end = 1
            while trim_end < len(visible_messages) and visible_messages[trim_end].get("role") == "tool":
                trim_end += 1
            return visible_messages[trim_end:]

        return visible_messages[1:]

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
                normalized_early = self.skill_engine._to_posix(
                    args["file_path"]
                ).lstrip("/")
                project_path = self.skill_engine.get_project_path(project_id)
                if (
                    project_path
                    and normalized_early in self._S0_BLOCKED_PLAN_FILES
                ):
                    stage_state = self.skill_engine._infer_stage_state(project_path)
                    if stage_state.get("stage_code") == "S0":
                        reason = (
                            "S0 阶段：请先对 seed 做一轮澄清，"
                            "再写大纲/研究计划/资料清单/分析笔记"
                        )
                        self._emit_system_notice_once(
                            category="s0_write_blocked",
                            path=normalized_early,
                            reason=reason,
                            user_action=(
                                "请先按 SKILL.md §S0 发一轮 3-5 个打包追问，"
                                "用户回答或跳过后再写正式产出文件。"
                            ),
                        )
                        return {"status": "error", "message": reason}
                if self._should_block_non_plan_write(project_id, args["file_path"]):
                    reason = "当前轮次还不能开始写正文，请先确认大纲或明确说“继续写正文”。"
                    self._emit_system_notice_once(
                        category="non_plan_write_blocked",
                        path=None,
                        reason=reason,
                        user_action="请先让用户确认大纲或明确要求继续正文后，再尝试写正式内容。",
                    )
                    return {
                        "status": "error",
                        "message": reason,
                    }
                if self._should_require_fetch_url_before_write(project_id, args["file_path"]):
                    reason = "本轮已经做过 web_search，但还没调用 fetch_url 阅读网页正文。请先对候选链接使用 fetch_url，再写正式文件。"
                    self._emit_system_notice_once(
                        category="fetch_url_gate_blocked",
                        path=None,
                        reason=reason,
                        user_action="请先读取候选网页正文，再把外部信息写入正式文件。",
                    )
                    return {
                        "status": "error",
                        "message": reason,
                    }
                normalized_path = self.skill_engine.validate_plan_write(project_id, args["file_path"])
                if self.skill_engine.is_protected_stage_checkpoints_path(normalized_path):
                    reason = (
                        "stage_checkpoints.json 是用户确认真值源，模型不能直接写入。"
                        "推进阶段需要用户点击右侧工作区对应按钮（例如\"确认大纲，进入资料采集\"）。"
                    )
                    self._emit_system_notice_once(
                        category="checkpoint_forge_blocked",
                        path=normalized_path,
                        reason=reason,
                        user_action="请告知用户需要他们点击工作区按钮来推进阶段；不要尝试直接写这个文件。",
                    )
                    return {"status": "error", "message": reason}
                project_path = self.skill_engine.get_project_path(project_id)
                checkpoints = self.skill_engine._load_stage_checkpoints(project_path) if project_path else {}
                signature_error = self.skill_engine.validate_self_signature(
                    normalized_path,
                    args["content"],
                    checkpoints,
                )
                if signature_error:
                    self._emit_system_notice_once(
                        category="write_blocked",
                        path=normalized_path,
                        reason=signature_error,
                        user_action="请联系用户在右侧工作区完成对应的确认后再写入",
                    )
                    return {"status": "error", "message": signature_error}
                should_emit_data_log_hint = self._is_first_data_log_write(project_id, normalized_path)
                self.skill_engine.write_file(project_id, normalized_path, args["content"])
                result = {"status": "success", "message": f"已写入文件: {normalized_path}"}
                if should_emit_data_log_hint:
                    self._emit_system_notice_once(
                        category="data_log_format_hint",
                        path=normalized_path,
                        reason=(
                            "data-log.md 每条事实必须写成 `### [DL-YYYY-NN] 事实标题`，"
                            "下方带 URL / `material:xxx` / `访谈:` / `调研:` 来源标记。"
                        ),
                        user_action="不要用 Markdown 表格记录事实；请拆成独立 DL-id 条目后继续写入。",
                    )
                self._persist_successful_tool_result(
                    project_id,
                    func_name,
                    args,
                    result,
                    {"normalized_path": normalized_path},
                )
                return result
            if func_name == "read_file":
                normalized_path = self.skill_engine.normalize_file_path(project_id, args["file_path"])
                content = self.skill_engine.read_file(project_id, normalized_path)
                result = {"status": "success", "content": content}
                self._persist_successful_tool_result(
                    project_id,
                    func_name,
                    args,
                    result,
                    {"normalized_path": normalized_path},
                )
                return result
            if func_name == "read_material_file":
                content = self.skill_engine.read_material_file(project_id, args["material_id"])
                result = {"status": "success", "content": content}
                self._persist_successful_tool_result(project_id, func_name, args, result)
                return result
            if func_name == "web_search":
                if self._turn_context.get("web_search_disabled"):
                    return {
                        "status": "error",
                        "message": "本轮 web_search 已因搜索服务错误被停用，请不要继续重试。",
                    }
                current_count = int(self._turn_context.get("web_search_count", 0) or 0)
                result = self._web_search(
                    args["query"],
                    project_id=project_id,
                    turn_search_count=current_count,
                )
                if result.get("disable_for_turn"):
                    self._turn_context["web_search_disabled"] = True
                if result.get("limit_scope") != "per_turn":
                    self._turn_context["web_search_count"] = current_count + 1
                if result.get("status") == "success":
                    self._turn_context["web_search_performed"] = True
                return {key: value for key, value in result.items() if key != "disable_for_turn"}
            if func_name == "fetch_url":
                result = self._fetch_url(project_id, args["url"])
                if result.get("status") == "success":
                    self._turn_context["fetch_url_performed"] = True
                    self._persist_successful_tool_result(project_id, func_name, args, result)
                return result
            return {"status": "error", "message": f"未知工具: {func_name}"}
        except json.JSONDecodeError as e:
            logging.error(f"工具参数解析失败: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": f"参数解析失败: {str(e)}"}
        except ValueError as e:
            logging.error(f"工具参数验证失败: {func_name}, 错误: {str(e)}")
            if func_name == "write_file":
                self._emit_system_notice_once(
                    category="write_blocked",
                    path=None,
                    reason=str(e),
                    user_action="请根据提示调整写入目标或内容后再重试。",
                )
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"工具执行异常: {func_name}, 错误: {str(e)}")
            if func_name == "write_file":
                self._emit_system_notice_once(
                    category="write_blocked",
                    path=None,
                    reason=f"工具执行失败: {str(e)}",
                    user_action="请检查写入条件是否满足，然后重试。",
                )
            return {"status": "error", "message": f"工具执行失败: {str(e)}"}

    def _get_search_router(self) -> SearchRouter:
        global _SEARCH_ROUTER_SINGLETON
        with _SEARCH_ROUTER_GUARD:
            if _SEARCH_ROUTER_SINGLETON is not None:
                return _SEARCH_ROUTER_SINGLETON

            search_config = load_managed_search_pool_config()
            providers: dict[str, object] = {}
            provider_factories = {
                "serper": SerperProvider,
                "brave": BraveProvider,
                "tavily": TavilyProvider,
                "exa": ExaProvider,
            }
            for provider_name, provider_config in search_config.providers.items():
                if not provider_config.enabled:
                    continue
                factory = provider_factories.get(provider_name)
                if factory is None:
                    continue
                providers[provider_name] = factory(api_key=provider_config.api_key)

            _SEARCH_ROUTER_SINGLETON = SearchRouter(
                config=search_config,
                state_store=SearchStateStore(
                    runtime_state_path=get_search_runtime_state_path(),
                    cache_path=get_search_cache_path(),
                ),
                providers=providers,
            )
            return _SEARCH_ROUTER_SINGLETON

    def _supports_native_web_search(self) -> bool:
        active_model = self._get_active_model_name().lower()
        base_url = ""
        if self.settings.mode == "custom":
            base_url = (self.settings.custom_api_base or self.settings.api_base or "").lower()
        else:
            base_url = (self.settings.api_base or self.settings.managed_base_url or "").lower()
        return "api.openai.com" in base_url and active_model.startswith("gpt-")

    def _search_with_native_provider(self, query: str) -> ProviderSearchResult | None:
        if not self._supports_native_web_search():
            return None
        response = self.client.responses.create(
            model=self._get_active_model_name(),
            input=query,
            tools=[{"type": "web_search"}],
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            return None
        return ProviderSearchResult(
            provider="native",
            items=[
                SearchItem(
                    title=f"Native web search: {query}",
                    snippet=output_text,
                    url="native://web-search",
                    domain="native",
                    score=1.0,
                )
            ],
            result_type="success",
        )

    def _web_search(
        self,
        query: str,
        *,
        project_id: str = "",
        turn_search_count: int = 0,
    ) -> Dict[str, object]:
        try:
            router = self._get_search_router()
            result = router.search(
                query,
                project_id=project_id or "__direct__",
                turn_search_count=turn_search_count,
                native_search=self._search_with_native_provider,
            )
            if result.get("status") == "error" and result.get("error_type") != "quota_exhausted":
                return {
                    **result,
                    "disable_for_turn": True,
                }
            return result
        except Exception as e:
            logging.error(f"搜索失败: {str(e)}")
            return {
                "status": "error",
                "error_type": "backend_error",
                "message": "搜索功能暂时不可用，本轮已暂停继续搜索，请稍后重试。",
                "disable_for_turn": True,
            }

    def _fetch_url(self, project_id: str, url: str) -> Dict[str, str | bool]:
        parsed = self._validate_fetch_url(url)
        original_url = parsed.geturl()
        primary_url = self._upgrade_fetch_url(original_url)
        current_cache_url = primary_url
        if parsed.scheme == "http":
            fallback_cache_hit = self._get_cached_fetch_result(project_id, original_url, "http_fallback")
            if fallback_cache_hit is not None:
                return fallback_cache_hit
        primary_cache_hit = self._get_cached_fetch_result(project_id, primary_url, "https_first")
        if primary_cache_hit is not None:
            return primary_cache_hit
        response = None
        final_url = original_url
        request_mode = "https_first"

        try:
            try:
                response, final_url = self._request_fetch_response(primary_url)
            except requests.exceptions.RequestException as exc:
                if primary_url != original_url and self._should_retry_fetch_over_http(exc):
                    fallback_cache_hit = self._get_cached_fetch_result(project_id, original_url, "http_fallback")
                    if fallback_cache_hit is not None:
                        return fallback_cache_hit
                    request_mode = "http_fallback"
                    current_cache_url = original_url
                    response, final_url = self._request_fetch_response(original_url)
                else:
                    raise

            content_type = self._normalize_fetch_content_type(response)
            if content_type and content_type not in self.FETCH_URL_ALLOWED_CONTENT_TYPES:
                result = self._build_fetch_error(
                    f"暂不支持读取该类型网页内容：{content_type}",
                    "unsupported_content_type",
                    original_url,
                    final_url,
                )
                self._store_fetch_error_cache(project_id, current_cache_url, request_mode, result)
                return result

            body, overflow = self._read_response_bytes(response)
            if overflow:
                result = self._build_fetch_error(
                    "网页响应过大，当前版本暂不支持读取。",
                    "response_too_large",
                    original_url,
                    final_url,
                )
                self._store_fetch_error_cache(project_id, current_cache_url, request_mode, result)
                return result

            raw_text = self._decode_response_bytes(response, body)
            if not raw_text.strip():
                return self._build_fetch_error(
                    "网页内容为空，无法提取正文。",
                    "empty_content",
                    original_url,
                    final_url,
                )

            classified_error = self._classify_fetch_page(response.status_code, response.headers, raw_text)
            if classified_error:
                result = self._build_fetch_error(
                    self._fetch_error_message_for_type(classified_error),
                    classified_error,
                    original_url,
                    final_url,
                )
                self._store_fetch_error_cache(project_id, current_cache_url, request_mode, result)
                return result

            title = self._extract_html_title(raw_text) or parsed.hostname or final_url
            if content_type == "text/plain":
                content = raw_text.strip()
            else:
                content = self._extract_readable_text(raw_text)

            if not content.strip():
                return self._build_fetch_error(
                    "网页正文提取失败。",
                    "non_readable_page",
                    original_url,
                    final_url,
                )

            content, truncated = self._truncate_text(content)
            result = {
                "status": "success",
                "title": title,
                "url": final_url,
                "final_url": final_url,
                "content": content,
                "content_type": content_type or "text/html",
                "truncated": truncated,
            }
            self._store_fetch_success_cache(project_id, current_cache_url, request_mode, result)
            return result
        except ValueError as exc:
            payload = self._parse_fetch_value_error(exc)
            result = self._build_fetch_error(
                payload["message"],
                payload["error_type"],
                original_url,
                payload.get("final_url") or final_url,
            )
            self._store_fetch_error_cache(
                project_id,
                current_cache_url,
                request_mode,
                result,
            )
            return result
        except requests.exceptions.Timeout:
            return self._build_fetch_error("网页抓取超时。", "timeout", original_url, final_url)
        except requests.exceptions.RequestException as exc:
            return self._build_fetch_error(f"网页抓取失败: {str(exc)}", "request_failed", original_url, final_url)
        except Exception as exc:
            return self._build_fetch_error(f"网页抓取失败: {str(exc)}", "request_failed", original_url, final_url)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _build_fetch_error(self, message: str, error_type: str, url: str, final_url: str | None = None):
        result: Dict[str, str] = {
            "status": "error",
            "message": message,
            "url": url,
            "error_type": error_type,
        }
        if final_url:
            result["final_url"] = final_url
        return result

    def _parse_fetch_value_error(self, exc: ValueError) -> Dict[str, str]:
        raw_message = str(exc)
        if raw_message.startswith("{"):
            try:
                payload = json.loads(raw_message)
                if isinstance(payload, dict) and payload.get("message") and payload.get("error_type"):
                    return payload
            except Exception:
                pass
        return {"message": raw_message, "error_type": "invalid_url"}

    def _get_cached_fetch_result(self, project_id: str, cache_url: str, request_mode: str):
        cache_key = (project_id, cache_url, request_mode)
        entry = self._fetch_url_cache.get(cache_key)
        if not entry:
            return None
        if float(entry["expires_at"]) <= time.time():
            self._fetch_url_cache.pop(cache_key, None)
            return None
        return dict(entry["result"])

    def _store_fetch_success_cache(self, project_id: str, cache_url: str, request_mode: str, result: Dict[str, str | bool]):
        self._fetch_url_cache[(project_id, cache_url, request_mode)] = {
            "expires_at": time.time() + self.FETCH_URL_SUCCESS_CACHE_TTL_SECONDS,
            "result": dict(result),
        }

    def _store_fetch_error_cache(self, project_id: str, cache_url: str, request_mode: str, result: Dict[str, str | bool]):
        if result.get("error_type") not in {
            "http_status_404",
            "redirect_blocked",
            "redirect_limit_exceeded",
            "unsupported_content_type",
            "response_too_large",
        }:
            return
        self._fetch_url_cache[(project_id, cache_url, request_mode)] = {
            "expires_at": time.time() + self.FETCH_URL_NEGATIVE_CACHE_TTL_SECONDS,
            "result": dict(result),
        }

    def _upgrade_fetch_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "http":
            return parsed.geturl()
        return parsed._replace(scheme="https").geturl()

    def _should_retry_fetch_over_http(self, exc: Exception) -> bool:
        return isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ConnectionError))

    def _request_fetch_response(self, start_url: str):
        current_url = start_url
        redirects = 0
        response = None

        while True:
            try:
                response = self._fetch_http_get(current_url)
            except Exception:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                raise

            final_url = response.url if isinstance(getattr(response, "url", None), str) else current_url
            self._validate_fetch_url(final_url)

            if not self._is_fetch_redirect(response.status_code):
                return response, final_url

            location = str(response.headers.get("Location", "")).strip()
            if not location:
                response.close()
                raise ValueError(json.dumps({"message": "网页跳转缺少目标地址。", "error_type": "redirect_missing_location", "final_url": final_url}, ensure_ascii=False))

            if redirects >= self.FETCH_URL_MAX_REDIRECTS:
                response.close()
                raise ValueError(json.dumps({"message": "网页跳转次数过多。", "error_type": "redirect_limit_exceeded", "final_url": final_url}, ensure_ascii=False))

            next_url = urljoin(current_url, location)
            next_parsed = self._validate_fetch_url(next_url)

            redirects += 1
            current_url = next_parsed.geturl()
            response.close()

    def _fetch_http_get(self, url: str):
        if curl_cffi_requests is not None:
            try:
                return curl_cffi_requests.get(
                    url,
                    timeout=self.FETCH_URL_TIMEOUT_SECONDS,
                    stream=True,
                    allow_redirects=False,
                    impersonate=self.FETCH_URL_CURL_CFFI_IMPERSONATE,
                )
            except Exception:
                pass
        return requests.get(
            url,
            headers=self.FETCH_URL_HEADERS,
            timeout=self.FETCH_URL_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=False,
        )

    def _is_fetch_redirect(self, status_code: int) -> bool:
        return status_code in {301, 302, 303, 307, 308}

    def _normalize_fetch_content_type(self, response) -> str:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if ";" in content_type:
            content_type = content_type.split(";", 1)[0].strip()
        return content_type

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

    def _read_response_bytes(self, response) -> tuple[bytes, bool]:
        chunks = []
        total = 0

        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            remaining = self.FETCH_URL_MAX_BYTES - total
            if remaining <= 0:
                return b"".join(chunks), True
            piece = chunk[:remaining]
            chunks.append(piece)
            total += len(piece)
            if len(chunk) > remaining or total > self.FETCH_URL_MAX_BYTES:
                return b"".join(chunks), True

        return b"".join(chunks), False

    def _decode_response_bytes(self, response, body: bytes) -> str:
        preferred_utf8_text = self._preferred_utf8_decode(response, body)
        if preferred_utf8_text is not None:
            return preferred_utf8_text

        candidates = self._candidate_fetch_encodings(response, body)
        best_text = ""
        best_score = float("-inf")

        for encoding in candidates:
            try:
                text = body.decode(encoding)
            except Exception:
                continue
            score = self._score_decoded_text(text)
            if score > best_score:
                best_text = text
                best_score = score

        if best_text:
            return best_text
        return body.decode("utf-8", errors="ignore")

    def _preferred_utf8_decode(self, response, body: bytes) -> str | None:
        try:
            utf8_text = body.decode("utf-8")
        except Exception:
            return None

        content_type = str(response.headers.get("Content-Type", ""))
        header_charset = self._extract_charset_from_content_type(content_type)
        meta_charset = self._extract_meta_charset(body)
        weak_charsets = {"latin1", "latin-1", "iso-8859-1", "windows-1252", "cp1252"}
        explicit_charset = meta_charset or header_charset
        if explicit_charset and explicit_charset not in weak_charsets and explicit_charset != "utf-8":
            return None
        if self._score_decoded_text(utf8_text) <= 0:
            return None
        return utf8_text

    def _candidate_fetch_encodings(self, response, body: bytes) -> List[str]:
        candidates: List[str] = []

        if body.startswith(b"\xef\xbb\xbf"):
            candidates.append("utf-8-sig")
        elif body.startswith(b"\xff\xfe"):
            candidates.append("utf-16")
        elif body.startswith(b"\xfe\xff"):
            candidates.append("utf-16")

        response_encoding = self._safe_get_fetch_response_attr(response, "encoding")
        if isinstance(response_encoding, str) and response_encoding:
            candidates.append(response_encoding)

        content_type = str(response.headers.get("Content-Type", ""))
        header_charset = self._extract_charset_from_content_type(content_type)
        if header_charset:
            candidates.append(header_charset)

        meta_charset = self._extract_meta_charset(body)
        if meta_charset:
            candidates.append(meta_charset)

        apparent = self._safe_get_fetch_response_attr(response, "apparent_encoding")
        if isinstance(apparent, str) and apparent:
            candidates.append(apparent)

        candidates.extend(["utf-8", "gb18030", "gbk", "gb2312"])
        return self._dedupe_preserve_order(candidates)

    def _safe_get_fetch_response_attr(self, response, attr_name: str):
        try:
            return getattr(response, attr_name, None)
        except Exception:
            return None

    def _extract_charset_from_content_type(self, content_type: str) -> str:
        match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
        if not match:
            return ""
        return match.group(1).strip().strip("\"'").lower()

    def _extract_meta_charset(self, body: bytes) -> str:
        head = body[:4096].decode("ascii", errors="ignore")
        direct_match = re.search(r"<meta[^>]+charset=['\"]?([A-Za-z0-9._-]+)", head, flags=re.IGNORECASE)
        if direct_match:
            return direct_match.group(1).strip().lower()
        equiv_match = re.search(
            r"<meta[^>]+content=['\"][^>]*charset=([A-Za-z0-9._-]+)",
            head,
            flags=re.IGNORECASE,
        )
        if not equiv_match:
            return ""
        return equiv_match.group(1).strip().lower()

    def _dedupe_preserve_order(self, items: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for item in items:
            normalized = (item or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _looks_like_garbled_text(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        replacement_ratio = stripped.count("\ufffd") / max(len(stripped), 1)
        if replacement_ratio > 0.02:
            return True
        return False

    def _score_decoded_text(self, text: str) -> float:
        stripped = text.strip()
        if not stripped:
            return float("-inf")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", stripped))
        ascii_readable = len(re.findall(r"[A-Za-z0-9\s.,;:!?()\"'\-_/]", stripped))
        replacement_count = stripped.count("\ufffd")
        suspicious_count = sum(
            stripped.count(token)
            for token in ("Ã", "Â", "Ð", "Ñ", "ä¸", "å", "æ", "ç", "ï¼", "â€", "â€™", "â€œ")
        )
        return (cjk_count * 4.0) + (ascii_readable * 0.05) - (replacement_count * 30.0) - (suspicious_count * 8.0)

    def _classify_fetch_page(self, status_code: int, headers, text: str) -> str | None:
        lowered_text = text.lower()
        lowered_title = self._extract_html_title(text).lower()
        header_value = str(headers.get("cf-mitigated", "")).lower()

        if header_value == "challenge" or "just a moment" in lowered_title or "cf challenge" in lowered_text:
            return "challenge_page"
        if (
            "百度安全验证" in text
            or "访问过于频繁" in text
            or "location.href='/index/'" in text
            or "location.href=\"/index/\"" in text
        ):
            return "non_readable_page"
        if self._looks_like_redirect_shell(text):
            return "non_readable_page"
        if status_code != 200:
            return f"http_status_{status_code}"
        return None

    def _fetch_error_message_for_type(self, error_type: str) -> str:
        if error_type == "challenge_page":
            return "目标网页返回了挑战页，当前 HTTP 抓取无法继续。"
        if error_type == "non_readable_page":
            return "抓到的是错误页或壳页，未提取到可用正文。"
        if error_type.startswith("http_status_"):
            status_code = error_type.rsplit("_", 1)[-1]
            return f"网页抓取失败（状态码：{status_code}）"
        return "网页抓取失败。"

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

        return self._extract_fallback_text(html_text)

    def _looks_like_redirect_shell(self, html_text: str) -> bool:
        lowered_html = html_text.lower()
        if not any(marker in lowered_html for marker in ("window.location", "redirecting...", "please wait", "location.replace(")):
            return False
        extracted = self._extract_fallback_text(html_text).lower()
        if len(extracted) >= 80:
            return False
        if "<article" in lowered_html or "<main" in lowered_html:
            return False
        return any(marker in extracted for marker in ("redirecting", "please wait", "loading", "login"))

    def _extract_fallback_text(self, html_text: str) -> str:
        cleaned = re.sub(
            r"<(script|style|noscript|template)[^>]*>.*?</\1>",
            " ",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for tag_name in ("article", "main", "body"):
            match = re.search(
                rf"<{tag_name}[^>]*>(.*?)</{tag_name}>",
                cleaned,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if match:
                candidate = self._strip_html(match.group(1))
                if candidate:
                    return candidate
        return self._strip_html(cleaned)

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
            turn_rule = (
                "本轮如用户明确要求继续正文，可在更新 plan 后撰写正文。"
                "像“你开始写吧”“开始写吧”“开始写正文吧”“继续写正文”都算明确授权。"
            )
        else:
            turn_rule = (
                "本轮只能做两类事：1）继续问清关键信息；2）更新 `plan/` 内文件。"
                "在用户明确确认大纲或明确要求继续正文前，禁止写正文、章节草稿、report_draft 或最终报告。"
                "如果信息不足，提出问题后就停止本轮，不要擅自继续。"
            )
        evidence_rule = (
            "如果本轮调用了 `web_search` 并准备把外部网页信息写进正式文件，"
            "必须先对候选链接调用 `fetch_url` 阅读正文；搜索结果摘要不能直接当作正式依据。"
        )
        return f"{skill_prompt}\n\n## 当前轮次约束\n{turn_rule}\n{evidence_rule}\n\n{project_context}"

    def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
        return {
            "can_write_non_plan": can_write_non_plan,
            "web_search_disabled": False,
            "web_search_performed": False,
            "fetch_url_performed": False,
            "web_search_count": 0,
            "system_notice_emitted": False,
            "pending_system_notices": [],
            "checkpoint_event": None,
            "pending_stage_keyword": None,
        }

    def _is_question(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._QUESTION_PATTERNS)

    def _phrase_hits(self, text: str, phrases: list[str]) -> bool:
        """Substring match with negation suppression. If any occurrence of the phrase
        has a clean (negation-free) preceding window of 10 chars, the phrase counts as
        a hit. Otherwise the match is suppressed."""
        for phrase in phrases:
            idx = text.find(phrase)
            while idx != -1:
                preceding = text[max(0, idx - self._NEGATION_WINDOW_CHARS): idx]
                if not self._NEGATION_RE.search(preceding):
                    return True
                idx = text.find(phrase, idx + 1)
        return False

    def _has_prior_s0_assistant_turn(self, project_id: str) -> bool:
        """Return True if the project's conversation history contains at
        least one role=='assistant' message.

        Per spec §3 S0 soft gate: s0_interview_done_at strong keyword /
        stage-ack tag only fires after the assistant has already delivered
        at least one turn (typically the mandatory S0 clarification block).
        Frontend-assembled welcome messages are role=user and don't count.
        Tool role also doesn't count.
        """
        if not project_id:
            return False
        try:
            conv = self._load_conversation(project_id)
        except Exception:
            return False
        return any(m.get("role") == "assistant" for m in conv)

    def _detect_stage_keyword(
        self,
        user_message: str,
        current_stage: str,
        project_id: str | None = None,  # For Task I S0 soft gate
    ) -> tuple[str, str] | None:
        if not user_message:
            return None
        trimmed = user_message.strip()
        if self._is_question(trimmed):
            return None

        rollback_hits = [
            key for key, phrases in self._ROLLBACK_KEYWORDS.items()
            if self._phrase_hits(trimmed, phrases)
        ]
        if rollback_hits:
            key = max(rollback_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
            return ("clear", key)

        advance_hits: list[str] = []
        for key, phrases in self._STRONG_ADVANCE_KEYWORDS.items():
            if self._phrase_hits(trimmed, phrases):
                advance_hits.append(key)

        if advance_hits:
            key = max(advance_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
            # S0 soft gate: reject s0 set unless at least one assistant turn exists
            if (
                key == "s0_interview_done_at"
                and not self._has_prior_s0_assistant_turn(project_id)
            ):
                return None
            return ("set", key)

        return None

    def _build_turn_context(self, project_id: str, user_message: str) -> Dict[str, object]:
        self._turn_context = self._new_turn_context(can_write_non_plan=False)
        project_path = self.skill_engine.get_project_path(project_id)
        if project_path:
            summary = self.skill_engine.get_workspace_summary(project_id)
            current_stage = summary.get("stage_code", "S0")
            detected = self._detect_stage_keyword(user_message, current_stage, project_id)
            if detected:
                action, key = detected
                if action == "clear":
                    try:
                        self.skill_engine.record_stage_checkpoint(project_id, key, action)
                    except ValueError as exc:
                        notice = self.skill_engine.get_stage_checkpoint_prereq_notice(key)
                        if notice:
                            self._emit_system_notice_once(
                                category="checkpoint_prereq_missing",
                                path=notice["path"],
                                reason=notice["reason"],
                                user_action=notice["user_action"],
                            )
                        else:
                            raise exc
                    else:
                        self._turn_context["checkpoint_event"] = {"action": action, "key": key}
                else:
                    self._turn_context["pending_stage_keyword"] = (action, key)
        self._turn_context["can_write_non_plan"] = self._should_allow_non_plan_write(project_id, user_message)
        return self._turn_context

    def _finalize_assistant_turn(self, project_id: str, full_content: str) -> str:
        """Resolve stage-ack tags and pending keyword fallback for one turn."""
        from backend.stage_ack import StageAckParser

        parser = StageAckParser()
        events = parser.parse(full_content)
        executable_events = [event for event in events if event.executable]
        pending = self._turn_context.get("pending_stage_keyword")

        lock = _get_project_request_lock(project_id)
        with lock:
            for event in events:
                if not event.executable:
                    logging.getLogger("backend.chat").warning(
                        "stage-ack tag ignored: key=%s action=%s reason=%s",
                        event.key,
                        event.action,
                        event.ignored_reason,
                    )

            if executable_events:
                self._turn_context["pending_stage_keyword"] = None
                for event in events:
                    if event.executable:
                        self._apply_stage_ack_event(project_id, event)
            elif pending:
                action, key = pending
                self._turn_context["pending_stage_keyword"] = None
                try:
                    self.skill_engine.record_stage_checkpoint(project_id, key, action)
                except ValueError:
                    notice = self.skill_engine.get_stage_checkpoint_prereq_notice(key)
                    if notice:
                        self._emit_system_notice_once(
                            category="stage_keyword_prereq_missing",
                            path=notice["path"],
                            reason=notice["reason"],
                            user_action=notice["user_action"],
                        )
                else:
                    self._turn_context["checkpoint_event"] = {"action": action, "key": key}

        return parser.strip(full_content)

    def _apply_stage_ack_event(self, project_id: str, event) -> None:
        if (
            event.key == "s0_interview_done_at"
            and event.action == "set"
            and not self._has_prior_s0_assistant_turn(project_id)
        ):
            self._emit_system_notice_once(
                category="s0_tag_soft_gate",
                path=None,
                reason=(
                    "S0 阶段第一轮必须先对 seed 做一轮打包追问，"
                    "再推进；本轮 tag 不执行。"
                ),
                user_action=(
                    "请模型按 SKILL.md §S0 先发 3-5 个澄清问题，"
                    "下一轮再发 tag。"
                ),
            )
            return

        try:
            self.skill_engine.record_stage_checkpoint(
                project_id, event.key, event.action
            )
        except ValueError:
            notice = self.skill_engine.get_stage_checkpoint_prereq_notice(event.key)
            if notice:
                self._emit_system_notice_once(
                    category="stage_ack_prereq_missing",
                    path=notice["path"],
                    reason=notice["reason"],
                    user_action=notice["user_action"],
                )
        else:
            self._turn_context["checkpoint_event"] = {
                "action": event.action,
                "key": event.key,
            }

    def _emit_system_notice_once(
        self,
        *,
        category: str,
        path: str | None = None,
        reason: str,
        user_action: str,
    ) -> None:
        if self._turn_context.get("system_notice_emitted"):
            return
        notice = {
            "type": "system_notice",
            "category": category,
            "path": path,
            "reason": reason,
            "user_action": user_action,
        }
        self._turn_context["system_notice_emitted"] = True
        queue = self._turn_context.setdefault("pending_system_notices", [])
        queue.append(notice)

    def _should_allow_non_plan_write(self, project_id: str, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        if not normalized:
            return False

        if self._is_non_plan_write_blocking_message(normalized):
            return False

        project_path = self.skill_engine.get_project_path(project_id)
        if project_path:
            checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
            if "outline_confirmed_at" in checkpoints:
                stage_state = self.skill_engine._infer_stage_state(project_path)
                if stage_state.get("stage_code") in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES:
                    return True

        if any(keyword in normalized for keyword in self.NON_PLAN_WRITE_ALLOW_KEYWORDS):
            # §7 patch: S0/S1 without outline_confirmed_at must not bypass via
            # generic "开始写" allow-keyword; otherwise user's innocuous
            # "开始写" would both set s0 and open non-plan writes, skipping
            # outline confirmation entirely.
            if project_path:
                stage_state = self.skill_engine._infer_stage_state(project_path)
                stage_code = stage_state.get("stage_code")
                if stage_code in {"S0", "S1"}:
                    checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
                    if "outline_confirmed_at" not in checkpoints:
                        return False
            return True

        if self._looks_like_follow_up_non_plan_request(normalized):
            history_permission = self._recent_history_allows_non_plan_write(project_id)
            if history_permission is not None:
                return history_permission

        if self._has_existing_report_draft(project_id) and any(
            keyword in normalized for keyword in self.NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS
        ):
            return True

        return False

    def _looks_like_follow_up_non_plan_request(self, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        return any(keyword in normalized for keyword in self.NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS)

    def _has_existing_report_draft(self, project_id: str) -> bool:
        try:
            self.skill_engine.get_primary_report_path(project_id)
            return True
        except ValueError:
            return False

    def _recent_history_allows_non_plan_write(self, project_id: str) -> bool | None:
        history = self._load_conversation(project_id)
        for message in reversed(history):
            if message.get("role") != "user":
                continue
            content = self._extract_message_text(message.get("content", "")).strip()
            if not content:
                continue
            if self._is_non_plan_write_blocking_message(content):
                return False
            if self._is_non_plan_write_approval_message(content):
                return True
        return None

    def _is_non_plan_write_approval_message(self, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        approval_keywords = [
            *self.NON_PLAN_WRITE_ALLOW_KEYWORDS,
            "继续吧",
            "继续哈",
            "继续写正文",
            "继续写报告",
            "继续正文",
            "大纲没问题",
            "扩写",
            "续写",
            "润色",
            "改写",
        ]
        return any(keyword in normalized for keyword in approval_keywords)

    def _is_non_plan_write_blocking_message(self, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        blocking_keywords = [
            "先别写正文",
            "不要写正文",
            "先不写正文",
            "别写正文",
            "先补计划",
            "先补大纲",
            "先别继续正文",
        ]
        return any(keyword in normalized for keyword in blocking_keywords)

    def _should_block_non_plan_write(self, project_id: str, file_path: str) -> bool:
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            return False
        return not normalized.startswith("plan/") and not self._turn_context.get("can_write_non_plan", True)

    def _should_require_fetch_url_before_write(self, project_id: str, file_path: str) -> bool:
        if not self._turn_context.get("web_search_performed"):
            return False
        if self._turn_context.get("fetch_url_performed"):
            return False

        try:
            normalized = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            return False

        evidence_paths = {
            "plan/references.md",
            "plan/outline.md",
            "plan/research-plan.md",
            "plan/data-log.md",
            "plan/analysis-notes.md",
            "report_draft_v1.md",
            "content/report.md",
            "content/draft.md",
            "content/final-report.md",
            "output/final-report.md",
        }
        return normalized in evidence_paths
