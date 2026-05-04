import base64
import hashlib
import ipaddress
import json
import logging
import os
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
    PSEUDO_FILE_TOOL_CALL_RE = re.compile(
        r"\b(?:write_file|edit_file)\s*\(\s*file_path\s*=\s*(?P<quote>['\"])(?P<path>[^'\"]+)(?P=quote)",
        re.IGNORECASE,
    )
    INLINE_DATA_LOG_ENTRY_RE = re.compile(
        r"^#{3,4}\s*\*{0,2}\s*\[DL-[^\]]+\]",
        re.MULTILINE,
    )
    DEBUG_DATA_URL_BASE64_RE = re.compile(
        r"(?i)data:[^\s\"'<>)]*;base64,[A-Za-z0-9+/=_-]+"
    )
    DEBUG_LONG_BASE64_FRAGMENT_RE = re.compile(
        r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])"
    )
    SELF_CORRECTION_LOOP_MARKERS = (
        "（修正",
        "(修正",
        "（纠正",
        "(纠正",
        "停止自言自语",
    )
    MAX_MISSING_WRITE_RETRIES = 2
    MAX_SELF_CORRECTION_RETRIES = 1
    NON_PLAN_WRITE_ALLOWED_STAGE_CODES = {"S4", "S5", "S6", "S7", "done"}
    LEGACY_REPORT_DRAFT_PATHS = frozenset({
        "report_draft_v1.md",
        "content/report.md",
        "content/draft.md",
        "content/final-report.md",
        "output/final-report.md",
    })
    APPEND_REPORT_DRAFT_MIN_SUBSTANTIVE_CHARS = 80
    REPORT_BODY_EXPLICIT_WRITE_KEYWORDS = (
        "继续写",
        "继续写吧",
        "继续写报告",
        "继续写正文",
        "接着写",
        "补全剩余章节",
        "续写",
        "扩写正文",
        "写下一章",
        "补正文",
        "完善正文",
        "修改正文",
        "补一段报告",
        "重写结论",
    )
    REPORT_BODY_SHORT_CONTINUATION_KEYWORDS = ("继续", "可以继续", "接着", "往下写")
    REPORT_BODY_REVIEW_OR_DELIVERY_KEYWORDS = (
        "开始审查",
        "继续审查",
        "质量检查",
        "运行质量检查",
        "导出",
        "归档",
        "交付",
    )
    REPORT_BODY_PAUSE_KEYWORDS = (
        "先别写了",
        "不要写正文",
        "先不写正文",
        "暂停写作",
        "先别继续正文",
    )
    REPORT_BODY_FIRST_DRAFT_KEYWORDS = (
        "开始写正文",
        "开始写报告正文",
        "起草正文",
        "按大纲写初稿",
        "先写第一版",
        "继续写正文",
        "继续写报告正文",
    )
    REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS = (
        "继续写报告正文",
        "继续写正文",
        "扩写正文",
        "补全章节",
        "写下一章",
        "补正文",
    )
    REPORT_BODY_WHOLE_REWRITE_KEYWORDS = (
        "整篇重写",
        "全文重写",
        "推倒重写",
        "全部改写",
    )
    REPORT_BODY_SECTION_REWRITE_KEYWORDS = (
        "重写",
        "改写",
        "改强",
        "改得更强",
        "重做",
    )
    REPORT_BODY_FOLLOWUP_EXPANSION_SIGNALS = (
        "不够",
        "不足",
        "太单薄",
        "再展开",
        "再补一点",
        "再扩写",
    )
    REPORT_BODY_CONDITIONAL_TARGET_EXPANSION_KEYWORDS = (
        "扩到",
        "补到",
        "写到",
        "不够就继续写",
        "不够再扩写",
    )
    REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS = (
        "现在多少字",
        "字数多少",
        "当前多少字",
        "看看字数",
        "看看现在多少字",
    )
    REPORT_BODY_INSPECT_FILE_KEYWORDS = ("看看文件",)
    CANONICAL_DRAFT_NO_DRAFT_MESSAGE = (
        "当前还没有正文草稿，请先用 append_report_draft 起草第一版。"
    )
    CANONICAL_DRAFT_AMBIGUOUS_SECTION_MESSAGE = "请指明具体章节。"
    CANONICAL_DRAFT_STAGE_GATE_MESSAGE = (
        "当前轮次还不能开始写正文，请先确认大纲或明确说“继续写正文”。"
    )
    CANONICAL_DRAFT_SPLIT_TURN_MESSAGE = (
        "这个请求同时包含多个后续动作，请拆成多个回合分别处理："
        "先完成正文修改，再单独发起导出、质量检查或查看字数。"
    )
    REPORT_BODY_CHAPTER_WRITE_RE = re.compile(
        r"(?:继续|接着|补写|撰写|写|扩写|续写|重写|改写|修改|完善)\s*第?\s*[一二三四五六七八九十百\d]+\s*章"
    )
    REPORT_BODY_INLINE_EDIT_RE = re.compile(
        r"把[^\n。！？!?]{0,20}(?:报告|正文)[^\n。！？!?]{0,80}(?:改成|改为|替换成|换成)"
    )
    REPORT_BODY_REPLACE_TEXT_INTENT_RE = re.compile(
        r"把(?:报告|正文)(?:里的|中的|里|中)?"
        r"(?P<old_text>[^，,、。！？!?；;：:\n]{1,80}?)"
        r"\s*[，,、：:]?\s*"
        r"(?:改成|改为|替换成|换成)"
        r"\s*(?P<new_text>[^，,、。！？!?；;：:\n]{1,80})"
    )

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

    def _format_provider_error(
        self,
        error: Exception,
        *,
        stream: bool,
        request_kwargs: Dict | None = None,
    ) -> str:
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

        if request_kwargs is not None:
            raw_message = self._debug_redact_error_message(raw_message, request_kwargs)
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
            "draft_followup_state": None,
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
                draft_followup_state = payload.get("draft_followup_state")
                if isinstance(draft_followup_state, dict):
                    state["draft_followup_state"] = draft_followup_state

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
            draft_followup_state = payload.get("draft_followup_state")
            if isinstance(draft_followup_state, dict):
                state["draft_followup_state"] = draft_followup_state

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

        persistence_extra = dict(extra or {})
        metadata_func_name = persistence_extra.pop("metadata_func_name", func_name)
        metadata_args = persistence_extra.pop("metadata_args", args)
        persisted_via = persistence_extra.pop("persisted_via", None)
        metadata = self._build_tool_persistence_metadata(
            project_id,
            metadata_func_name,
            metadata_args,
            result,
            persistence_extra,
        )
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
                effective_persisted_via = persisted_via
                if (
                    not isinstance(effective_persisted_via, str)
                    and isinstance(metadata_func_name, str)
                    and metadata_func_name != func_name
                ):
                    effective_persisted_via = metadata_func_name
                if isinstance(effective_persisted_via, str) and effective_persisted_via.strip():
                    event["persisted_via"] = effective_persisted_via
                source_ref = metadata.get("source_ref")
                if isinstance(source_ref, str) and source_ref.strip():
                    event["source_ref"] = source_ref
                title = metadata.get("title")
                if isinstance(title, str) and title.strip():
                    event["title"] = title
                state["events"].append(event)

                memory_entry = self._build_tool_memory_entry(metadata_func_name, metadata, recorded_at)
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

        persisted_via = event.get("persisted_via")
        if isinstance(persisted_via, str) and persisted_via.strip():
            skeleton["persisted_via"] = persisted_via

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

    def _extract_successful_write_path(
        self,
        func_name: str,
        arguments: str,
        result: Dict,
        *,
        project_id: str | None = None,
    ) -> str | None:
        event = self._extract_successful_write_event(
            func_name,
            arguments,
            result,
            project_id=project_id,
        )
        if not event:
            return None
        path = event.get("path")
        return path if isinstance(path, str) else None

    def _extract_successful_write_event(
        self,
        func_name: str,
        arguments: str,
        result: Dict,
        *,
        project_id: str | None = None,
    ) -> dict | None:
        if func_name == "append_report_draft" and result.get("status") == "success":
            return {
                "path": self.skill_engine.REPORT_DRAFT_PATH,
                "tool": "append_report_draft",
                "arguments": {},
                "raw_arguments": arguments,
            }
        if func_name not in {"write_file", "edit_file"} or result.get("status") != "success":
            return None
        try:
            payload = json.loads(arguments)
        except Exception:
            return None
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return None
        return {
            "path": self._canonical_successful_write_path(file_path, project_id=project_id),
            "tool": func_name,
            "arguments": payload,
            "raw_arguments": arguments,
        }

    def _canonical_successful_write_path(
        self,
        file_path: str,
        *,
        project_id: str | None = None,
    ) -> str:
        if project_id:
            try:
                normalized = self.skill_engine.normalize_file_path(project_id, file_path)
            except ValueError:
                normalized = self._normalize_project_file_path(file_path)
        else:
            normalized = self._normalize_project_file_path(file_path)
        if self._is_canonical_report_draft_path(normalized):
            return self.skill_engine.REPORT_DRAFT_PATH
        return normalized

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

        expected.update(self._pseudo_file_tool_paths_for_message(normalized_text))

        if self.INLINE_DATA_LOG_ENTRY_RE.search(normalized_text):
            expected.add("plan/data-log.md")

        if self._message_mentions_file_update(normalized_text, ("plan/progress.md", "progress.md", "当前任务", "项目进度")):
            expected.add("plan/progress.md")

        if self._message_mentions_file_update(normalized_text, ("plan/notes.md", "notes.md", "项目笔记", "核心技术共识", "备注")):
            expected.add("plan/notes.md")

        if self._message_mentions_file_update(normalized_text, ("plan/stage-gates.md", "stage-gates.md", "阶段门禁", "当前阶段")):
            expected.add("plan/stage-gates.md")

        if self._message_mentions_file_update(normalized_text, ("plan/tasks.md", "tasks.md", "任务清单", "阶段任务")):
            expected.add("plan/tasks.md")

        return expected

    def _pseudo_file_tool_paths_for_message(self, assistant_message: str) -> set[str]:
        expected: set[str] = set()
        for match in self.PSEUDO_FILE_TOOL_CALL_RE.finditer(assistant_message or ""):
            normalized_path = self._normalize_project_file_path(match.group("path"))
            if normalized_path.startswith("plan/") and normalized_path.endswith(".md"):
                expected.add(normalized_path)
            if self._is_expected_report_write_path(normalized_path):
                expected.add(normalized_path)
        return expected

    def _looks_like_self_correction_loop(self, assistant_message: str) -> bool:
        text = assistant_message or ""
        marker_hits = sum(text.count(marker) for marker in self.SELF_CORRECTION_LOOP_MARKERS)
        return marker_hits >= 3

    def _is_expected_report_write_path(self, normalized_path: str) -> bool:
        return self._normalize_project_file_path(normalized_path).lower() == self.skill_engine.REPORT_DRAFT_PATH

    def _is_canonical_report_draft_path(self, normalized_path: str) -> bool:
        return self._normalize_project_file_path(normalized_path).lower() == self.skill_engine.REPORT_DRAFT_PATH

    def _is_noncanonical_report_draft_path(self, normalized_path: str) -> bool:
        candidate = self._normalize_project_file_path(normalized_path).lower()
        if candidate == self.skill_engine.REPORT_DRAFT_PATH:
            return False
        if candidate in self.LEGACY_REPORT_DRAFT_PATHS:
            return True
        return bool(
            re.fullmatch(r"report_draft_v\d+\.md", candidate)
            or re.fullmatch(r"content/report_draft_v\d+\.md", candidate)
        )

    def _build_report_draft_path_error(self, normalized_path: str) -> str:
        return (
            f"报告正文草稿路径已统一为 `{self.skill_engine.REPORT_DRAFT_PATH}`，"
            f"不要写入旧路径 `{normalized_path}`。"
        )

    def _message_has_report_body_write_intent(
        self,
        project_id: str,
        user_message: str,
        stage_code: str,
    ) -> bool:
        decision = self._classify_canonical_draft_turn(
            project_id,
            user_message,
            stage_code=stage_code,
        )
        if decision.get("mode") == "require":
            return True
        return (
            stage_code in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES
            and decision.get("fixed_message") == self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE
        )

    def _empty_canonical_draft_decision(self, *, stage_code: str) -> dict[str, object]:
        return {
            "mode": "no_write",
            "priority": "P10",
            "stage_code": stage_code,
            "expected_tool_family": None,
            "required_edit_scope": None,
            "rewrite_target_snapshot": None,
            "rewrite_target_label": None,
            "fixed_message": None,
            "mixed_intent_secondary_family": None,
            "effective_turn_target_count": None,
            "intent_kind": None,
            "old_text": None,
            "new_text": None,
        }

    def _make_canonical_draft_decision(
        self,
        *,
        stage_code: str,
        mode: str,
        priority: str,
        expected_tool_family: str | None = None,
        required_edit_scope: str | None = None,
        rewrite_target_snapshot: str | None = None,
        rewrite_target_label: str | None = None,
        fixed_message: str | None = None,
        mixed_intent_secondary_family: str | None = None,
        effective_turn_target_count: int | None = None,
        intent_kind: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
    ) -> dict[str, object]:
        decision = self._empty_canonical_draft_decision(stage_code=stage_code)
        decision.update(
            {
                "mode": mode,
                "priority": priority,
                "expected_tool_family": expected_tool_family,
                "required_edit_scope": required_edit_scope,
                "rewrite_target_snapshot": rewrite_target_snapshot,
                "rewrite_target_label": rewrite_target_label,
                "fixed_message": fixed_message,
                "mixed_intent_secondary_family": mixed_intent_secondary_family,
                "effective_turn_target_count": effective_turn_target_count,
                "intent_kind": intent_kind,
                "old_text": old_text,
                "new_text": new_text,
            }
        )
        return decision

    def _classify_canonical_draft_turn(
        self,
        project_id: str,
        user_message: str,
        *,
        stage_code: str | None = None,
    ) -> dict[str, object]:
        normalized_stage = (stage_code or "").strip()
        if not normalized_stage:
            project_path = self.skill_engine.get_project_path(project_id)
            if project_path:
                normalized_stage = (
                    self.skill_engine._infer_stage_state(project_path).get("stage_code", "S0")
                )
            else:
                normalized_stage = "S0"

        decision = self._empty_canonical_draft_decision(stage_code=normalized_stage)
        text = (user_message or "").strip()
        if not text:
            return decision

        project_path = self.skill_engine.get_project_path(project_id)
        default_target_count = self._project_default_report_target_count(project_path)
        draft_snapshot = self._snapshot_project_file(project_id, self.skill_engine.REPORT_DRAFT_PATH)
        draft_exists = bool(draft_snapshot.get("exists"))
        current_count = int(draft_snapshot.get("word_count") or 0)
        draft_text = (
            self._read_project_file_text(project_id, self.skill_engine.REPORT_DRAFT_PATH) or ""
        )
        followup_threshold_count = self._resolve_followup_threshold_count(
            project_id,
            project_path,
            default_target_count,
        )
        secondary_families = self._secondary_action_families_in_message(text)
        has_distinct_non_expansion_action = self._message_has_distinct_non_expansion_action(text)
        replace_text_intent = self._parse_report_body_replacement_intent(text)
        section_match = self._resolve_section_rewrite_targets(text, draft_text)
        matched_section_nodes = list(section_match.get("nodes") or [])
        section_ambiguity = bool(section_match.get("ambiguous"))
        section_label = (
            str(matched_section_nodes[0].get("label"))
            if len(matched_section_nodes) == 1 and isinstance(matched_section_nodes[0], dict)
            else None
        )
        section_snapshot = (
            str(matched_section_nodes[0].get("section_snapshot"))
            if len(matched_section_nodes) == 1 and isinstance(matched_section_nodes[0], dict)
            else None
        )
        section_rewrite_request = self._looks_like_section_rewrite_request(text)
        multi_section_rewrite_intent = bool(
            section_rewrite_request and len(matched_section_nodes) > 1
        )
        section_rewrite_intent = bool(
            draft_exists
            and section_label
            and section_snapshot
            and self._phrase_hits(text, list(self.REPORT_BODY_SECTION_REWRITE_KEYWORDS))
        )
        whole_rewrite_intent = self._phrase_hits(text, list(self.REPORT_BODY_WHOLE_REWRITE_KEYWORDS))
        first_draft_intent = self._phrase_hits(text, list(self.REPORT_BODY_FIRST_DRAFT_KEYWORDS))
        explicit_continuation_intent = self._phrase_hits(
            text,
            list(self.REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS),
        )
        conditional_target_expansion_request = self._message_has_conditional_target_expansion_intent(text)
        explicit_target_count = self._extract_explicit_word_target_count(text)
        implicit_followup_append_candidate = (
            normalized_stage == "S4"
            and draft_exists
            and current_count < followup_threshold_count
            and self._draft_followup_state_allows_implicit_append(project_id)
            and self._message_has_followup_expansion_signal(text)
        )

        explicit_mutation_decision: dict[str, object] | None = None
        if replace_text_intent:
            if not draft_exists:
                explicit_mutation_decision = self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="reject",
                    priority="P1",
                    fixed_message=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
                )
            else:
                explicit_mutation_decision = self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="require",
                    priority="P1",
                    expected_tool_family="edit_file",
                    required_edit_scope="replacement",
                    intent_kind="replace_text",
                    old_text=str(replace_text_intent.get("old_text") or ""),
                    new_text=str(replace_text_intent.get("new_text") or ""),
                )
        elif section_rewrite_request and section_ambiguity:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="reject",
                priority="P2_AMBIGUOUS",
                fixed_message=self.CANONICAL_DRAFT_AMBIGUOUS_SECTION_MESSAGE,
            )
        elif section_rewrite_intent:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="require",
                priority="P2",
                    expected_tool_family="edit_file",
                    required_edit_scope="section",
                    rewrite_target_snapshot=section_snapshot,
                    rewrite_target_label=section_label,
                )
        elif multi_section_rewrite_intent:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="require",
                priority="P2_MULTI_SECTION",
                expected_tool_family="edit_file",
                required_edit_scope="full_draft",
                rewrite_target_snapshot=draft_text or None,
            )
        elif section_rewrite_request and not draft_exists:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="reject",
                priority="P2",
                fixed_message=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
            )
        elif whole_rewrite_intent:
            if not draft_exists:
                explicit_mutation_decision = self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="reject",
                    priority="P3",
                    fixed_message=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
                )
            else:
                explicit_mutation_decision = self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="require",
                    priority="P3",
                    expected_tool_family="edit_file",
                    required_edit_scope="full_draft",
                    rewrite_target_snapshot=draft_text or None,
                )
        elif not draft_exists and first_draft_intent:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="require",
                priority="P4",
                expected_tool_family="append_report_draft",
            )
        elif explicit_continuation_intent:
            explicit_mutation_decision = self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="require",
                priority="P9",
                expected_tool_family="append_report_draft",
            )

        if len(secondary_families) > 1 and (
            conditional_target_expansion_request
            or explicit_mutation_decision is not None
            or implicit_followup_append_candidate
        ):
            return self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="reject",
                priority="P5_MULTI",
                fixed_message=self.CANONICAL_DRAFT_SPLIT_TURN_MESSAGE,
            )

        if len(secondary_families) == 1 and conditional_target_expansion_request:
            effective_target_count = explicit_target_count or followup_threshold_count
            if not draft_exists:
                if (
                    isinstance(explicit_mutation_decision, dict)
                    and explicit_mutation_decision.get("priority") == "P4"
                    and explicit_mutation_decision.get("mode") == "require"
                ):
                    return self._apply_stage_gate_to_canonical_draft_decision(explicit_mutation_decision)
                return self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="reject",
                    priority="P5A",
                    fixed_message=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
                )
            needs_more_writing = current_count < effective_target_count
            if not needs_more_writing:
                return self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="no_write",
                    priority="P5A",
                    mixed_intent_secondary_family=secondary_families[0],
                    effective_turn_target_count=effective_target_count,
                )
            return self._apply_stage_gate_to_canonical_draft_decision(
                self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="require",
                    priority="P5A",
                    expected_tool_family="append_report_draft",
                    mixed_intent_secondary_family=secondary_families[0],
                    effective_turn_target_count=effective_target_count,
                )
            )

        if len(secondary_families) == 1 and explicit_mutation_decision is not None:
            if explicit_mutation_decision.get("mode") != "require":
                return explicit_mutation_decision
            mixed_intent_decision = dict(explicit_mutation_decision)
            mixed_intent_decision["priority"] = "P5B"
            mixed_intent_decision["mixed_intent_secondary_family"] = secondary_families[0]
            return self._apply_stage_gate_to_canonical_draft_decision(mixed_intent_decision)

        if explicit_mutation_decision is not None:
            return self._apply_stage_gate_to_canonical_draft_decision(explicit_mutation_decision)

        if self._message_matches_priority6_non_write(text):
            return self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="no_write",
                priority="P6",
            )

        if self._message_matches_priority7_inspect(text):
            return self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="no_write",
                priority="P7",
            )

        if (
            implicit_followup_append_candidate
            and not has_distinct_non_expansion_action
        ):
            return self._apply_stage_gate_to_canonical_draft_decision(
                self._make_canonical_draft_decision(
                    stage_code=normalized_stage,
                    mode="require",
                    priority="P8",
                    expected_tool_family="append_report_draft",
                )
            )

        return decision

    def _apply_stage_gate_to_canonical_draft_decision(
        self,
        decision: dict[str, object],
    ) -> dict[str, object]:
        if decision.get("mode") != "require":
            return decision
        stage_code = str(decision.get("stage_code") or "")
        if stage_code in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES:
            return decision
        blocked = dict(decision)
        blocked["mode"] = "reject"
        blocked["fixed_message"] = self.CANONICAL_DRAFT_STAGE_GATE_MESSAGE
        return blocked

    def _project_default_report_target_count(self, project_path) -> int:
        if not project_path:
            return 3000
        targets = self.skill_engine._resolve_length_targets(project_path)
        return int(targets.get("expected_length", 3000) or 3000)

    def _resolve_followup_threshold_count(
        self,
        project_id: str,
        project_path,
        default_target_count: int,
    ) -> int:
        draft_followup_state = self._load_draft_followup_state(project_id)
        if isinstance(draft_followup_state, dict):
            continuation_threshold_count = draft_followup_state.get("continuation_threshold_count")
            if isinstance(continuation_threshold_count, int) and continuation_threshold_count > 0:
                return continuation_threshold_count
        return default_target_count

    def _load_draft_followup_state(self, project_id: str) -> dict | None:
        state = self._load_conversation_state(project_id)
        draft_followup_state = state.get("draft_followup_state")
        if not isinstance(draft_followup_state, dict):
            return None
        return draft_followup_state

    def _draft_followup_state_allows_implicit_append(self, project_id: str) -> bool:
        draft_followup_state = self._load_draft_followup_state(project_id)
        if not isinstance(draft_followup_state, dict):
            return False
        return bool(
            draft_followup_state.get("reported_under_target")
            or draft_followup_state.get("asked_continue_expand")
        )

    def _empty_draft_followup_flags(self) -> dict[str, object]:
        return {
            "reported_under_target": False,
            "asked_continue_expand": False,
            "continuation_threshold_count": None,
        }

    def _normalize_draft_followup_flags(
        self,
        flags: dict | None,
        *,
        default_target_count: int | None = None,
    ) -> dict[str, object]:
        normalized = self._empty_draft_followup_flags()
        if not isinstance(flags, dict):
            return normalized
        normalized["reported_under_target"] = bool(flags.get("reported_under_target"))
        normalized["asked_continue_expand"] = bool(flags.get("asked_continue_expand"))
        continuation_threshold_count = flags.get("continuation_threshold_count")
        if isinstance(continuation_threshold_count, int) and continuation_threshold_count > 0:
            if default_target_count is None or continuation_threshold_count > default_target_count:
                normalized["continuation_threshold_count"] = continuation_threshold_count
        return normalized

    def _snapshot_continuation_threshold_count(
        self,
        snapshot: dict | None,
        *,
        default_target_count: int | None = None,
    ) -> int | None:
        if not isinstance(snapshot, dict):
            return None
        candidate = snapshot.get("effective_turn_target_count")
        if not isinstance(candidate, int) or candidate <= 0:
            candidate = snapshot.get("turn_target_count")
        if not isinstance(candidate, int) or candidate <= 0:
            return None
        if default_target_count is not None and candidate <= default_target_count:
            return None
        return candidate

    def _current_turn_effective_target_count(
        self,
        *,
        default_target_count: int | None = None,
    ) -> int | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("priority") != "P5A":
            return None
        candidate = decision.get("effective_turn_target_count")
        if not isinstance(candidate, int) or candidate <= 0:
            return None
        if default_target_count is not None and candidate <= default_target_count:
            return None
        return candidate

    def _current_turn_carried_followup_target_count(
        self,
        project_id: str,
        *,
        default_target_count: int | None = None,
    ) -> int | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("priority") not in {"P7", "P8"}:
            return None
        carried_target_count = self._resolve_followup_threshold_count(
            project_id,
            None,
            default_target_count or 0,
        )
        if default_target_count is not None and carried_target_count <= default_target_count:
            return None
        return carried_target_count

    def _current_turn_requested_target_count(self) -> int | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("priority") != "P5A":
            return None
        candidate = decision.get("effective_turn_target_count")
        if not isinstance(candidate, int) or candidate <= 0:
            return None
        return candidate

    def _set_turn_draft_followup_flags(
        self,
        *,
        reported_under_target: bool,
        asked_continue_expand: bool,
        continuation_threshold_count: int | None = None,
    ) -> None:
        self._turn_context["draft_followup_flags"] = {
            "reported_under_target": bool(reported_under_target),
            "asked_continue_expand": bool(asked_continue_expand),
            "continuation_threshold_count": (
                continuation_threshold_count
                if isinstance(continuation_threshold_count, int) and continuation_threshold_count > 0
                else None
            ),
        }

    def _derive_structured_turn_draft_followup_flags(
        self,
        project_id: str,
        *,
        user_message: str | None = None,
        default_target_count: int | None = None,
    ) -> dict[str, object] | None:
        mutation = self._successful_canonical_draft_mutation()
        if isinstance(mutation, dict) and mutation.get("tool") == "append_report_draft":
            snapshot = mutation.get("progress_snapshot")
            if isinstance(snapshot, dict):
                turn_target_count = int(snapshot.get("turn_target_count") or 0)
                if turn_target_count <= 0 and isinstance(default_target_count, int):
                    turn_target_count = default_target_count
                continuation_threshold_count = self._snapshot_continuation_threshold_count(
                    snapshot,
                    default_target_count=default_target_count,
                )
                report_progress = snapshot.get("report_progress")
                current_count = (
                    int(report_progress.get("current_count") or 0)
                    if isinstance(report_progress, dict)
                    else 0
                )
                if current_count < turn_target_count:
                    return {
                        "reported_under_target": True,
                        "asked_continue_expand": False,
                        "continuation_threshold_count": continuation_threshold_count,
                    }
                return self._empty_draft_followup_flags()

        decision = self._turn_context.get("canonical_draft_decision")
        if not (
            isinstance(decision, dict)
            and decision.get("priority") == "P7"
            and self._phrase_hits(
                user_message or "",
                list(self.REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS),
            )
        ):
            return None

        snapshot = self._canonical_draft_progress_snapshot(project_id)
        if not isinstance(snapshot, dict):
            return None

        turn_target_count = int(snapshot.get("turn_target_count") or 0)
        if turn_target_count <= 0 and isinstance(default_target_count, int):
            turn_target_count = default_target_count
        continuation_threshold_count = self._snapshot_continuation_threshold_count(
            snapshot,
            default_target_count=default_target_count,
        )
        report_progress = snapshot.get("report_progress")
        current_count = (
            int(report_progress.get("current_count") or 0)
            if isinstance(report_progress, dict)
            else 0
        )
        if current_count < turn_target_count:
            return {
                "reported_under_target": True,
                "asked_continue_expand": False,
                "continuation_threshold_count": continuation_threshold_count,
            }
        return self._empty_draft_followup_flags()

    def _finalize_turn_draft_followup_flags(
        self,
        project_id: str,
        *,
        user_message: str | None = None,
    ) -> dict[str, object]:
        project_path = self.skill_engine.get_project_path(project_id)
        default_target_count = (
            self._project_default_report_target_count(project_path) if project_path else None
        )
        existing_flags = self._turn_context.get("draft_followup_flags")
        if isinstance(existing_flags, dict):
            normalized = self._normalize_draft_followup_flags(
                existing_flags,
                default_target_count=default_target_count,
            )
        else:
            normalized = self._normalize_draft_followup_flags(
                self._derive_structured_turn_draft_followup_flags(
                    project_id,
                    user_message=user_message,
                    default_target_count=default_target_count,
                ),
                default_target_count=default_target_count,
            )
        self._turn_context["draft_followup_flags"] = normalized
        return normalized

    def _persist_draft_followup_state_for_turn(
        self,
        project_id: str,
        assistant_message: str,
        *,
        user_message: str | None = None,
    ) -> None:
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return

        default_target_count = self._project_default_report_target_count(project_path)
        del assistant_message
        flags = self._finalize_turn_draft_followup_flags(
            project_id,
            user_message=user_message,
        )
        followup_active = bool(flags.get("reported_under_target")) or bool(
            flags.get("asked_continue_expand")
        )
        continuation_threshold_count = (
            flags.get("continuation_threshold_count")
            if followup_active
            else None
        )
        threshold_count = (
            int(continuation_threshold_count)
            if isinstance(continuation_threshold_count, int) and continuation_threshold_count > 0
            else default_target_count
        )

        current_text = self._read_project_file_text(project_id, self.skill_engine.REPORT_DRAFT_PATH)
        payload = None
        if current_text is not None:
            current_count = self.skill_engine._count_words(current_text)
            is_under_target = current_count < threshold_count
            reported_under_target = is_under_target and bool(flags.get("reported_under_target"))
            asked_continue_expand = is_under_target and bool(flags.get("asked_continue_expand"))
            if reported_under_target or asked_continue_expand:
                payload = {
                    "reported_under_target": reported_under_target,
                    "asked_continue_expand": asked_continue_expand,
                    "current_count": current_count,
                    "target_word_count": default_target_count,
                    "continuation_threshold_count": continuation_threshold_count,
                }

        self._mutate_conversation_state(
            project_id,
            lambda state: {**state, "draft_followup_state": payload},
        )

    def _secondary_action_families_in_message(self, user_message: str) -> list[str]:
        families: list[str] = []
        mappings = (
            ("quality_check", ("质量检查", "运行质量检查")),
            ("export", ("导出",)),
            ("inspect_file", self.REPORT_BODY_INSPECT_FILE_KEYWORDS),
            ("inspect_word_count", self.REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS),
        )
        for family, keywords in mappings:
            if self._phrase_hits(user_message, list(keywords)):
                families.append(family)
        return families

    def _message_matches_priority6_non_write(self, user_message: str) -> bool:
        return self._phrase_hits(
            user_message,
            [
                *self.REPORT_BODY_REVIEW_OR_DELIVERY_KEYWORDS,
                *self.REPORT_BODY_PAUSE_KEYWORDS,
            ],
        )

    def _message_matches_priority7_inspect(self, user_message: str) -> bool:
        return self._phrase_hits(
            user_message,
            [
                *self.REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS,
                "现在写到哪了",
                "写到哪了",
                *self.REPORT_BODY_INSPECT_FILE_KEYWORDS,
            ],
        )

    def _message_has_conditional_target_expansion_intent(self, user_message: str) -> bool:
        if self._match_explicit_word_target(user_message):
            return True
        return self._phrase_hits(
            user_message,
            ["不够就继续写", "不够再扩写"],
        )

    def _extract_explicit_word_target_count(self, user_message: str) -> int | None:
        match = self._match_explicit_word_target(user_message)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _match_explicit_word_target(self, user_message: str):
        return re.search(r"(?:扩到|补到|写到)\s*(\d{3,6})\s*字", user_message or "")

    def _message_has_followup_expansion_signal(self, user_message: str) -> bool:
        return self._phrase_hits(user_message, list(self.REPORT_BODY_FOLLOWUP_EXPANSION_SIGNALS))

    def _message_has_distinct_non_expansion_action(self, user_message: str) -> bool:
        if self._secondary_action_families_in_message(user_message):
            return True

        edit_markers = ["改一下", "修改", "改成", "改为", "替换", "换成", "调整"]
        if self._phrase_hits(user_message, ["封面标题", "封面", "标题页"]) and self._phrase_hits(
            user_message,
            edit_markers,
        ):
            return True

        if "顺便" in (user_message or "") and self._phrase_hits(user_message, edit_markers):
            return True

        return False

    def _looks_like_section_rewrite_request(self, user_message: str) -> bool:
        return self._phrase_hits(user_message, list(self.REPORT_BODY_SECTION_REWRITE_KEYWORDS))

    def _resolve_section_rewrite_targets(self, user_message: str, draft_text: str) -> dict[str, object]:
        if not user_message or not draft_text:
            return {"nodes": [], "ambiguous": False}

        heading_nodes = self._extract_markdown_heading_nodes(draft_text)
        if not heading_nodes:
            return {"nodes": [], "ambiguous": False}

        label_hits = {
            str(node.get("label"))
            for node in heading_nodes
            if isinstance(node, dict)
            and isinstance(node.get("label"), str)
            and len(str(node.get("label"))) >= 2
            and str(node.get("label")) in user_message
        }
        if not label_hits:
            return {"nodes": [], "ambiguous": False}

        matched_nodes: list[dict] = []
        ambiguous = False
        for label in sorted(label_hits, key=len, reverse=True):
            candidates = [
                node for node in heading_nodes
                if isinstance(node, dict) and node.get("label") == label
            ]
            if len(candidates) == 1:
                matched_nodes.extend(candidates)
                continue

            scored = []
            for node in candidates:
                ancestor_path = tuple(node.get("ancestor_path") or ())
                score = sum(1 for ancestor_label in ancestor_path if ancestor_label in label_hits)
                scored.append((score, node))
            max_score = max(score for score, _ in scored)
            narrowed = [node for score, node in scored if score == max_score]
            if max_score > 0 and len(narrowed) == 1:
                matched_nodes.extend(narrowed)
                continue

            matched_nodes.extend(narrowed)
            ambiguous = True

        unique_nodes: list[dict] = []
        seen_keys: set[tuple[int, int]] = set()
        for node in matched_nodes:
            key = (int(node.get("start", -1)), int(node.get("end", -1)))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_nodes.append(node)

        collapsed_nodes = [
            node for node in unique_nodes
            if not any(
                self._heading_node_is_ancestor(node, other)
                for other in unique_nodes
                if node is not other
            )
        ]

        label_counts: dict[str, int] = {}
        for node in collapsed_nodes:
            label = str(node.get("label") or "")
            label_counts[label] = label_counts.get(label, 0) + 1
        if any(count > 1 for count in label_counts.values()):
            ambiguous = True

        return {
            "nodes": collapsed_nodes,
            "ambiguous": ambiguous,
        }

    def _heading_node_is_ancestor(self, ancestor: dict, descendant: dict) -> bool:
        ancestor_path = tuple(ancestor.get("path") or ())
        descendant_path = tuple(descendant.get("path") or ())
        if len(ancestor_path) >= len(descendant_path):
            return False
        return descendant_path[:len(ancestor_path)] == ancestor_path

    def _extract_markdown_heading_nodes(self, draft_text: str) -> list[dict]:
        if not draft_text:
            return []

        heading_re = re.compile(r"(?m)^(?P<indent>[ \t]{0,3})(?P<hashes>#{1,6})\s*(?P<label>.+?)\s*$")
        matches = list(heading_re.finditer(draft_text))
        if not matches:
            return []

        nodes: list[dict] = []
        stack: list[dict] = []
        for match in matches:
            level = len(match.group("hashes"))
            label = re.sub(r"[*_`#]+", "", match.group("label")).strip()
            if not label:
                continue
            while stack and int(stack[-1]["level"]) >= level:
                stack.pop()
            ancestor_path = tuple(str(node["label"]) for node in stack)
            path = ancestor_path + (label,)
            node = {
                "label": label,
                "level": level,
                "start": match.start(),
                "end": len(draft_text),
                "ancestor_path": ancestor_path,
                "path": path,
                "section_snapshot": "",
            }
            nodes.append(node)
            stack.append(node)

        for index, node in enumerate(nodes):
            end = len(draft_text)
            for next_node in nodes[index + 1:]:
                if int(next_node["level"]) <= int(node["level"]):
                    end = int(next_node["start"])
                    break
            node["end"] = end
            node["section_snapshot"] = draft_text[int(node["start"]):end].rstrip()

        return nodes

    def _match_existing_draft_section_labels(self, user_message: str, draft_text: str) -> list[str]:
        resolved = self._resolve_section_rewrite_targets(user_message, draft_text)
        labels: list[str] = []
        for node in resolved.get("nodes") or []:
            if isinstance(node, dict):
                label = node.get("label")
                if isinstance(label, str):
                    labels.append(label)
        return labels

    def _match_existing_draft_section_label(self, user_message: str, draft_text: str) -> str | None:
        labels = self._match_existing_draft_section_labels(user_message, draft_text)
        if len(labels) != 1:
            return None
        return labels[0]

    def _extract_markdown_heading_labels(self, draft_text: str) -> list[str]:
        return [str(node.get("label")) for node in self._extract_markdown_heading_nodes(draft_text)]

    def _extract_markdown_section_snapshot(self, draft_text: str, label: str | None) -> str | None:
        if not draft_text or not label:
            return None

        resolved = self._resolve_section_rewrite_targets(label, draft_text)
        nodes = resolved.get("nodes") or []
        if len(nodes) != 1 or not isinstance(nodes[0], dict):
            return None
        snapshot = nodes[0].get("section_snapshot")
        if isinstance(snapshot, str) and snapshot:
            return snapshot
        return None

    def _build_section_rewrite_new_string_scope_message(
        self,
        rewrite_target_label: str | None,
    ) -> str:
        label_hint = (
            f"`{rewrite_target_label}`"
            if isinstance(rewrite_target_label, str) and rewrite_target_label
            else "目标章节"
        )
        return (
            f"本轮只允许改写 {label_hint}，`edit_file.new_string` 必须只包含该目标章节的完整替换内容。"
            "可以保留该章节下的 `###` 等子标题，但不要提交整份草稿或多个同级章节，"
            "避免超出目标章节的局部范围。"
        )

    def _validate_section_rewrite_new_string_scope(
        self,
        expected_section: str,
        new_string: str,
        *,
        rewrite_target_label: str | None,
    ) -> str | None:
        if not isinstance(new_string, str) or not new_string.strip():
            return self._build_section_rewrite_new_string_scope_message(rewrite_target_label)

        expected_nodes = self._extract_markdown_heading_nodes(expected_section or "")
        new_nodes = self._extract_markdown_heading_nodes(new_string)
        if not expected_nodes or not new_nodes:
            return self._build_section_rewrite_new_string_scope_message(rewrite_target_label)

        expected_root = expected_nodes[0]
        new_root = new_nodes[0]
        if new_string[: int(new_root.get("start", 0))].strip():
            return self._build_section_rewrite_new_string_scope_message(rewrite_target_label)

        expected_level = int(expected_root.get("level") or 0)
        expected_label = str(expected_root.get("label") or "")
        new_level = int(new_root.get("level") or 0)
        new_label = str(new_root.get("label") or "")
        if new_level != expected_level or new_label != expected_label:
            return self._build_section_rewrite_new_string_scope_message(rewrite_target_label)

        if any(int(node.get("level") or 0) <= expected_level for node in new_nodes[1:]):
            return self._build_section_rewrite_new_string_scope_message(rewrite_target_label)

        return None

    def _has_explicit_report_body_write_intent(self, user_message: str) -> bool:
        if self._phrase_hits(user_message, list(self.REPORT_BODY_EXPLICIT_WRITE_KEYWORDS)):
            return True
        return self._regex_has_clean_report_body_intent(user_message)

    def _regex_has_clean_report_body_intent(self, user_message: str) -> bool:
        for pattern in (
            self.REPORT_BODY_CHAPTER_WRITE_RE,
            self.REPORT_BODY_INLINE_EDIT_RE,
        ):
            for match in pattern.finditer(user_message):
                preceding = user_message[max(0, match.start() - self._NEGATION_WINDOW_CHARS): match.start()]
                if not self._NEGATION_RE.search(preceding):
                    return True
        return False

    def _is_short_report_body_continuation(self, user_message: str) -> bool:
        compact = re.sub(r"[\s。！？!?，,、；;：:]+", "", user_message or "")
        return compact in self.REPORT_BODY_SHORT_CONTINUATION_KEYWORDS

    def _recent_assistant_prompted_report_body_continuation(self, project_id: str) -> bool:
        writing_action_markers = (
            "我将补全剩余章节",
            "将补全剩余章节",
            "我会补全剩余章节",
            "会补全剩余章节",
            "补全剩余章节",
            "继续写报告",
            "继续写正文",
            "继续撰写",
            "补全报告正文",
            "将补全报告正文",
            "会补全报告正文",
            "完成剩余章节",
            "将完成剩余章节",
            "会完成剩余章节",
            "撰写报告",
            "撰写正文",
            "写报告",
            "写正文",
            "开始撰写正文",
            "继续撰写正文",
        )
        try:
            history = self._load_conversation(project_id)
        except Exception:
            return False

        for message in reversed(history):
            if message.get("role") != "assistant":
                continue
            text = self._extract_message_text(message.get("content", "")).strip()
            if not text:
                continue
            has_writing_action = self._text_has_non_past_marker(
                text,
                writing_action_markers,
            )
            has_review_or_delivery = self._phrase_hits(
                text,
                list(self.REPORT_BODY_REVIEW_OR_DELIVERY_KEYWORDS),
            )
            if has_review_or_delivery and not has_writing_action:
                return False
            return has_writing_action
        return False

    def _text_has_non_past_marker(self, text: str, markers: tuple[str, ...]) -> bool:
        past_marker_re = re.compile(r"(已经|已|刚刚|刚|完成|写完|生成)[^。！？!?\n]{0,8}$")
        for marker in markers:
            idx = text.find(marker)
            while idx != -1:
                preceding = text[max(0, idx - 12): idx]
                if not past_marker_re.search(preceding):
                    return True
                idx = text.find(marker, idx + 1)
        return False

    def _required_write_paths_for_turn(self, project_id: str, user_message: str) -> set[str]:
        decision = self._classify_canonical_draft_turn(project_id, user_message)
        if decision.get("mode") != "require":
            return set()
        return {self.skill_engine.REPORT_DRAFT_PATH}

    def _build_required_write_snapshots(self, project_id: str, user_message: str) -> dict[str, dict]:
        decision = self._classify_canonical_draft_turn(project_id, user_message)
        snapshots = {
            path: self._snapshot_project_file(project_id, path)
            for path in self._required_write_paths_for_turn(project_id, user_message)
        }
        replacement_intent = None
        if decision.get("intent_kind") == "replace_text":
            replacement_intent = {
                "intent_kind": "replace_text",
                "old_text": str(decision.get("old_text") or ""),
                "new_text": str(decision.get("new_text") or ""),
            }
        if replacement_intent:
            for path, snapshot in snapshots.items():
                if self._is_canonical_report_draft_path(path):
                    snapshot.update(replacement_intent)
                    before_text = self._read_project_file_text(project_id, path)
                    snapshot["old_text_present"] = (
                        before_text is not None
                        and replacement_intent["old_text"] in before_text
                    )
        if decision.get("required_edit_scope") in {"section", "full_draft"}:
            for path, snapshot in snapshots.items():
                if self._is_canonical_report_draft_path(path):
                    snapshot["required_edit_scope"] = decision.get("required_edit_scope")
                    snapshot["rewrite_target_snapshot"] = decision.get("rewrite_target_snapshot")
                    snapshot["rewrite_target_label"] = decision.get("rewrite_target_label")
        return snapshots

    def _parse_report_body_replacement_intent(self, user_message: str) -> dict[str, str] | None:
        match = self.REPORT_BODY_REPLACE_TEXT_INTENT_RE.search(user_message or "")
        if not match:
            return None
        old_text = self._clean_inline_replacement_text(match.group("old_text"))
        new_text = self._clean_inline_replacement_text(match.group("new_text"))
        if not old_text or not new_text:
            return None
        return {
            "intent_kind": "replace_text",
            "old_text": old_text,
            "new_text": new_text,
        }

    def _clean_inline_replacement_text(self, value: str) -> str:
        return (value or "").strip().strip("`'\"“”‘’《》")

    def _snapshot_project_file(self, project_id: str, normalized_path: str) -> dict:
        normalized = self._normalize_project_file_path(normalized_path)
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return {
                "path": normalized,
                "exists": False,
                "sha256": None,
                "word_count": 0,
                "mtime": None,
            }

        try:
            normalized = self.skill_engine.normalize_file_path(project_id, normalized)
        except ValueError:
            return {
                "path": normalized,
                "exists": False,
                "sha256": None,
                "word_count": 0,
                "mtime": None,
            }
        if self._is_canonical_report_draft_path(normalized):
            normalized = self.skill_engine.REPORT_DRAFT_PATH

        target = project_path / normalized
        if not target.exists() or not target.is_file():
            return {
                "path": normalized,
                "exists": False,
                "sha256": None,
                "word_count": 0,
                "mtime": None,
            }

        try:
            raw = target.read_bytes()
        except OSError:
            return {
                "path": normalized,
                "exists": False,
                "sha256": None,
                "word_count": 0,
                "mtime": None,
            }
        text = raw.decode("utf-8", errors="ignore")
        stat = target.stat()
        return {
            "path": normalized,
            "exists": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "word_count": self.skill_engine._count_words(text),
            "mtime": stat.st_mtime,
        }

    def _required_writes_satisfied(
        self,
        project_id: str,
        snapshots: dict[str, dict],
        successful_write_events: dict[str, list[dict]] | None = None,
    ) -> tuple[bool, list[str]]:
        successful_write_events = successful_write_events or {}
        missing: list[str] = []
        for path, before in snapshots.items():
            current = self._snapshot_project_file(project_id, path)
            if before.get("intent_kind") == "replace_text":
                if self._required_replacement_write_satisfied(
                    project_id,
                    path,
                    before,
                    current,
                    successful_write_events,
                ):
                    continue
                missing.append(path)
                continue
            before_exists = bool(before.get("exists"))
            current_exists = bool(current.get("exists"))
            if self._required_existing_write_satisfied(
                project_id,
                path,
                before,
                current,
            ):
                continue
            if (
                not before_exists
                and current_exists
                and self._project_file_has_substantive_required_write(project_id, path)
            ):
                continue
            missing.append(path)
        return not missing, missing

    def _required_existing_write_satisfied(
        self,
        project_id: str,
        normalized_path: str,
        before: dict,
        current: dict,
    ) -> bool:
        if not (
            before.get("exists")
            and current.get("exists")
            and current.get("sha256") != before.get("sha256")
        ):
            return False
        if not self._is_canonical_report_draft_path(normalized_path):
            return True

        before_word_count = int(before.get("word_count") or 0)
        current_word_count = int(current.get("word_count") or 0)
        if (
            current_word_count < before_word_count
            and not self._current_canonical_draft_allows_shrinkage()
        ):
            return False
        return self._project_file_has_substantive_required_write(project_id, normalized_path)

    def _current_canonical_draft_allows_shrinkage(self) -> bool:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return False
        if decision.get("expected_tool_family") != "edit_file":
            return False
        return decision.get("required_edit_scope") in {"section", "full_draft"}

    def _canonical_edit_missing_old_string_guidance(
        self,
        project_id: str,
        file_path: str,
    ) -> str | None:
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            return None
        if not self._is_canonical_report_draft_path(normalized):
            return None
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("expected_tool_family") != "edit_file":
            return None
        rewrite_scope = decision.get("required_edit_scope")
        if rewrite_scope not in {"section", "full_draft"}:
            return None
        if rewrite_scope == "full_draft":
            return (
                "本轮要全文重写报告正文。请先用 `read_file` 读取当前整份草稿，"
                "再用 `edit_file` 把完整旧稿作为 old_string、重写后的整份新稿作为 new_string。"
            )
        return (
            "本轮要改写现有章节。请先用 `read_file` 读取当前草稿，"
            "再用 `edit_file` 把目标章节完整原文作为 old_string、改写后的章节内容作为 new_string。"
        )

    def _required_replacement_write_satisfied(
        self,
        project_id: str,
        normalized_path: str,
        before: dict,
        current: dict,
        successful_write_events: dict[str, list[dict]],
    ) -> bool:
        canonical_path = self._canonical_successful_write_path(normalized_path, project_id=project_id)
        if canonical_path != self.skill_engine.REPORT_DRAFT_PATH:
            return False
        if not (
            before.get("exists")
            and current.get("exists")
            and current.get("sha256") != before.get("sha256")
        ):
            return False
        old_text = str(before.get("old_text") or "")
        new_text = str(before.get("new_text") or "")
        if not old_text or not new_text:
            return False
        if before.get("old_text_present") is False:
            return False
        matching_event = self._last_successful_write_is_matching_replacement_edit(
            project_id,
            successful_write_events,
            canonical_path,
            old_text,
            new_text,
        )
        if not isinstance(matching_event, dict):
            return False
        current_text = self._read_project_file_text(project_id, normalized_path)
        if current_text is None:
            return False
        event_old_string = matching_event.get("old_string")
        event_new_string = matching_event.get("new_string")
        if not isinstance(event_old_string, str) or not isinstance(event_new_string, str):
            return False
        if new_text not in current_text:
            return False
        if event_new_string not in current_text:
            return False
        return self._replacement_old_string_only_survives_inside_new_string(
            current_text,
            event_old_string,
            event_new_string,
        )

    def _last_successful_write_is_matching_replacement_edit(
        self,
        project_id: str,
        successful_write_events: dict[str, list[dict]],
        canonical_path: str,
        old_text: str,
        new_text: str,
    ) -> dict | None:
        events = successful_write_events.get(canonical_path) or []
        if not isinstance(events, list):
            return None
        if not events:
            return None
        last_event = events[-1]
        if not isinstance(last_event, dict):
            return None
        if last_event.get("path") != self.skill_engine.REPORT_DRAFT_PATH:
            return None
        if last_event.get("tool") != "edit_file":
            return None
        arguments = last_event.get("arguments")
        if not isinstance(arguments, dict):
            return None
        file_path = arguments.get("file_path")
        if not isinstance(file_path, str):
            return None
        if (
            self._canonical_successful_write_path(file_path, project_id=project_id)
            != self.skill_engine.REPORT_DRAFT_PATH
        ):
            return None
        old_string = arguments.get("old_string")
        new_string = arguments.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if not (
            self._text_contains_meaningful_reference(old_string, old_text)
            and new_text in new_string
        ):
            return None
        return {
            "old_string": old_string,
            "new_string": new_string,
        }

    def _replacement_old_string_only_survives_inside_new_string(
        self,
        current_text: str,
        old_string: str,
        new_string: str,
    ) -> bool:
        if not old_string or not new_string:
            return False
        old_spans = list(self._iter_substring_spans(current_text, old_string))
        if not old_spans:
            return True
        new_spans = list(self._iter_substring_spans(current_text, new_string))
        if not new_spans:
            return False
        for old_start, old_end in old_spans:
            if not any(
                new_start <= old_start and old_end <= new_end
                for new_start, new_end in new_spans
            ):
                return False
        return True

    def _iter_substring_spans(self, text: str, needle: str):
        if not needle:
            return
        start = text.find(needle)
        while start != -1:
            yield start, start + len(needle)
            start = text.find(needle, start + 1)

    def _text_contains_meaningful_reference(self, container: str, needle: str) -> bool:
        if needle in container:
            return True
        compact_container = re.sub(r"\s+", "", container or "")
        compact_needle = re.sub(r"\s+", "", needle or "")
        return bool(compact_needle and compact_needle in compact_container)

    def _read_project_file_text(self, project_id: str, normalized_path: str) -> str | None:
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return None
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, normalized_path)
        except ValueError:
            return None
        if self._is_canonical_report_draft_path(normalized):
            normalized = self.skill_engine.REPORT_DRAFT_PATH
        target = project_path / normalized
        if not target.exists() or not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    def _project_file_has_substantive_required_write(self, project_id: str, normalized_path: str) -> bool:
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return False
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, normalized_path)
        except ValueError:
            return False
        if self._is_canonical_report_draft_path(normalized):
            normalized = self.skill_engine.REPORT_DRAFT_PATH
        target = project_path / normalized
        if not target.exists() or not target.is_file():
            return False
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        if self._is_canonical_report_draft_path(normalized):
            return (
                self._count_report_append_substantive_chars(text)
                >= self.APPEND_REPORT_DRAFT_MIN_SUBSTANTIVE_CHARS
            )
        return self.skill_engine._has_substantive_body(text)

    def _get_missing_expected_writes(self, assistant_message: str, successful_writes: set[str]) -> list[str]:
        expected = self._expected_plan_writes_for_message(assistant_message)
        return sorted(path for path in expected if path not in successful_writes)

    def _build_missing_write_feedback(self, missing_files: list[str]) -> str:
        joined = "、".join(f"`{path}`" for path in missing_files)
        return (
            f"你刚刚声称已更新或已经给出了需要入档的内容，但本轮并未成功调用真实文件工具写入 {joined}。"
            "不要口头汇报，也不要继续推进下一阶段。"
            "请先用真实文件工具完成这些文件落盘：新建或整体重写用 `write_file`，局部追加或替换用 `edit_file`。"
            "不要把 `edit_file(...)` 或 `write_file(...)` 写在聊天正文里，那不是工具调用。"
            "落盘成功后，再用一句话说明实际已写入哪些文件。"
        )

    def _build_required_write_feedback(self, missing_paths: list[str]) -> str:
        joined = "、".join(f"`{path}`" for path in missing_paths)
        decision = self._turn_context.get("canonical_draft_decision")
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "edit_file"
            and decision.get("required_edit_scope") == "full_draft"
        ):
            return (
                f"用户本轮要求重写整份报告正文，因此必须真实更新 {joined}。"
                "刚才未检测到该文件按用户意图完成更新。"
                "请先用 `read_file` 读取当前草稿，再用 `edit_file` 把 old_string 设为当前整份正文、"
                "new_string 设为重写后的完整草稿。"
                "不要只口头说明已完成。"
            )
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "edit_file"
        ):
            return (
                f"用户本轮要求修改报告正文，因此必须真实更新 {joined}。"
                "刚才未检测到该文件按用户意图完成更新。"
                "请先用 `read_file` 读取当前草稿，再用 `edit_file` 做对应修改。"
                "不要只口头说明已完成，也不要把工具调用写在聊天正文里。"
            )
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "append_report_draft"
        ):
            return (
                f"用户本轮要求继续补写报告正文，因此必须真实更新 {joined}。"
                "刚才未检测到该文件按用户意图完成更新。"
                "请直接调用 `append_report_draft` 继续补全正文。"
                "不要只口头说明已完成，也不要把工具调用写在聊天正文里。"
            )
        return (
            f"用户本轮要求更新报告正文，因此必须真实更新 {joined}。"
            "刚才未检测到该文件按用户意图完成更新。"
            "请根据用户意图选择真实文件工具："
            "`append_report_draft` 用于继续撰写、补全或新增章节；"
            "`read_file` + `edit_file` 用于替换、改写或修正现有报告中的已有文字。"
            "不要只口头说明已完成，也不要把工具调用写在聊天正文里。"
        )

    def _build_required_write_failure_message(self, missing_paths: list[str]) -> str:
        joined = "、".join(f"`{path}`" for path in missing_paths)
        decision = self._turn_context.get("canonical_draft_decision")
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "edit_file"
            and decision.get("required_edit_scope") == "full_draft"
        ):
            return (
                f"这轮没有检测到报告草稿 {joined} 被实际重写。"
                "请重新发送重写正文的请求；我会要求模型先 `read_file` 当前草稿，"
                "再用 `edit_file` 对整份内容做全文替换。"
            )
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "edit_file"
        ):
            return (
                f"这轮没有检测到报告草稿 {joined} 被实际修改。"
                "请重新发送修改正文的请求；我会要求模型先 `read_file` 当前草稿，"
                "再用 `edit_file` 做对应修改。"
            )
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "append_report_draft"
        ):
            return (
                f"这轮没有检测到报告草稿 {joined} 被实际补写。"
                "请重新发送补写正文的请求；我会要求模型使用 `append_report_draft` 继续落盘。"
            )
        return (
            f"这轮没有检测到报告草稿 {joined} 被实际更新。"
            "请重新发送更新报告正文的请求；我会要求模型按意图选择真实文件工具："
            "续写或新增章节用 `append_report_draft`，已有文字修改用 `read_file` + `edit_file`。"
        )

    def _build_self_correction_loop_feedback(self) -> str:
        return (
            "你刚刚进入了反复“修正/纠正”的自我循环。"
            "停止输出反思过程，也不要继续重复确认。"
            "请只执行下一步真实动作：如果资料不足就调用搜索/读取/文件工具补齐；"
            "如果需要用户操作，只用一句话说明用户下一步要做什么。"
        )

    def _chat_stream_unlocked(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        transient_attachments: List[Dict] | None = None,
        max_iterations: int = 20,
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
        immediate_reject_message = self._immediate_canonical_draft_reject_message()
        if immediate_reject_message:
            assistant_message, token_usage, system_notices = self._finalize_early_assistant_message(
                project_id,
                history,
                current_user_message,
                immediate_reject_message,
            )
            yield {"type": "content", "data": assistant_message}
            for notice in system_notices:
                yield {
                    "type": "system_notice",
                    "category": notice.category,
                    "path": notice.path,
                    "reason": notice.reason,
                    "user_action": notice.user_action,
                    "surface_to_user": notice.surface_to_user,
                }
            yield {
                "type": "usage",
                "data": token_usage,
            }
            return
        immediate_guidance_message = self._immediate_canonical_draft_guidance_message(project_id)
        if immediate_guidance_message:
            assistant_message, token_usage, system_notices = self._finalize_early_assistant_message(
                project_id,
                history,
                current_user_message,
                immediate_guidance_message,
            )
            yield {"type": "content", "data": assistant_message}
            for notice in system_notices:
                yield {
                    "type": "system_notice",
                    "category": notice.category,
                    "path": notice.path,
                    "reason": notice.reason,
                    "user_action": notice.user_action,
                    "surface_to_user": notice.surface_to_user,
                }
            yield {
                "type": "usage",
                "data": token_usage,
            }
            return
        provider_user_message = {
            **current_user_message,
            "transient_attachments": transient_attachments or [],
        }
        active_model = self._get_active_model_name()
        required_write_snapshots = self._build_required_write_snapshots(project_id, user_message)
        self._turn_context["required_write_snapshots"] = required_write_snapshots

        iterations = 0
        missing_write_retries = 0
        required_write_retries = 0
        self_correction_retries = 0
        assistant_message = ""
        buffer_required_write_content = bool(required_write_snapshots)
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()
        successful_write_events: dict[str, list[dict]] = {}
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
                self._debug_dump_request(request_kwargs, label="stream", note=f"iteration={iterations}")
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
                    self._debug_dump_request(request_kwargs, label="stream", error=e, note=f"iteration={iterations}")
                    yield {
                        "type": "error",
                        "data": self._format_provider_error(
                            e,
                            stream=True,
                            request_kwargs=request_kwargs,
                        ),
                    }
                    return

            collected_message = {"role": "assistant", "content": "", "tool_calls": []}
            known_tool_names = {tool["function"]["name"] for tool in self._get_tools()}
            announced_tool_call_indexes: set[int] = set()
            stream_usage = None
            accumulated = ""
            stream_buffer = ""
            self_correction_loop_detected = False
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
                        if not buffer_required_write_content:
                            safe, held = stream_split_safe_tail(stream_buffer)
                            if safe:
                                yield {"type": "content", "data": safe}
                            stream_buffer = held
                        if self._looks_like_self_correction_loop(accumulated):
                            self_correction_loop_detected = True
                            break

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
                self._debug_dump_request(request_kwargs, label="stream-iter", error=e, note=f"iteration={iterations}")
                yield {
                    "type": "error",
                    "data": self._format_provider_error(
                        e,
                        stream=True,
                        request_kwargs=request_kwargs,
                    ),
                }
                return

            if collected_message["tool_calls"]:
                guidance_override_message = None
                # 上游（newapi → Gemini OpenAI 兼容层）偶发会把并行 functionCall 的流式
                # chunk 全部塞到 index=0，导致 name/arguments 被首尾拼接成
                # "write_filewrite_file" + "{...}{...}"。直接回传给上游会触发 400
                # INVALID_ARGUMENT（工具名不在声明列表），因此本轮作废、不落入历史，
                # 让模型在下一轮重新发起。
                malformed_reasons: List[str] = []
                for tc in collected_message["tool_calls"]:
                    fn = tc.get("function") or {}
                    fn_name = fn.get("name", "") or ""
                    fn_args = fn.get("arguments", "") or ""
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
                        "type": "tool",
                        "data": "⚠️ 上条 tool_calls 被上游合并成畸形条目，本轮作废并让模型重发。",
                    }
                    # 用一条纯文本 assistant + 一条 user 反馈做"合规隔板"，保持 user/model
                    # 严格交替——直接 append 一条 user 会导致连续两条 user（前面本轮原始
                    # 用户消息），触发 Gemini 的角色交替校验 400。
                    current_turn_messages.append({
                        "role": "assistant",
                        "content": "（上条工具调用被上游合并成畸形条目，已作废本轮调用。）",
                    })
                    current_turn_messages.append({
                        "role": "user",
                        "content": (
                            "刚才的 tool_calls 格式异常（"
                            + "；".join(malformed_reasons)
                            + "）。请重新发起：每次只调用一个工具，等该工具返回后再发下一个。"
                        ),
                    })
                    iterations += 1
                    continue

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
                    write_event = self._extract_successful_write_event(
                        func_name,
                        func_args,
                        result,
                        project_id=project_id,
                    )
                    if write_event:
                        write_path = write_event["path"]
                        successful_writes.add(write_path)
                        successful_write_events.setdefault(write_path, []).append(write_event)
                    for notice in self._turn_context.pop("pending_system_notices", []):
                        yield {
                            "type": "system_notice",
                            "category": notice["category"],
                            "path": notice.get("path"),
                            "reason": notice["reason"],
                            "user_action": notice["user_action"],
                            "surface_to_user": notice["surface_to_user"],
                        }
                    result_icon = "✅" if result.get("status") == "success" else "⚠️"
                    yield {"type": "tool", "data": f"{result_icon} 结果: {str(result)[:160]}..."}
                    current_turn_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    guidance_override_message = (
                        guidance_override_message
                        or self._mixed_intent_guidance_after_canonical_mutation(project_id)
                    )
                    if guidance_override_message:
                        break
                if guidance_override_message:
                    assistant_message = guidance_override_message
                    token_usage = self._normalize_provider_usage(
                        stream_usage,
                        policy,
                        preflight_compaction_used=compressed,
                    )
                    break
                iterations += 1
            else:
                candidate_message = collected_message["content"]
                if (
                    (self_correction_loop_detected or self._looks_like_self_correction_loop(candidate_message))
                    and self_correction_retries < self.MAX_SELF_CORRECTION_RETRIES
                ):
                    self_correction_retries += 1
                    yield {
                        "type": "tool",
                        "data": "⚠️ 检测到助手进入自我修正循环，正在要求它停止反思文本并重试。",
                    }
                    current_turn_messages.append({"role": "assistant", "content": candidate_message})
                    current_turn_messages.append({
                        "role": "user",
                        "content": self._build_self_correction_loop_feedback(),
                    })
                    continue
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
                required_satisfied, missing_required_writes = self._required_writes_satisfied(
                    project_id,
                    required_write_snapshots,
                    successful_write_events,
                )
                if not required_satisfied:
                    if required_write_retries < self.MAX_MISSING_WRITE_RETRIES:
                        required_write_retries += 1
                        yield {
                            "type": "tool",
                            "data": "⚠️ 本轮要求更新报告正文，但未检测到草稿文件变化，正在要求助手调用文件工具重试。",
                        }
                        current_turn_messages.append({"role": "assistant", "content": candidate_message})
                        current_turn_messages.append({
                            "role": "user",
                            "content": self._build_required_write_feedback(missing_required_writes),
                        })
                        continue
                    assistant_message = self._build_required_write_failure_message(missing_required_writes)
                    accumulated = ""
                    stream_buffer = ""
                    token_usage = self._normalize_provider_usage(
                        stream_usage,
                        policy,
                        preflight_compaction_used=compressed,
                    )
                    break
                assistant_message = candidate_message
                token_usage = self._normalize_provider_usage(
                    stream_usage,
                    policy,
                    preflight_compaction_used=compressed,
                )
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"
            if buffer_required_write_content:
                accumulated = ""
                stream_buffer = ""
            else:
                yield {"type": "content", "data": assistant_message}
                accumulated = assistant_message
                stream_buffer = ""

        assistant_message = self._finalize_assistant_turn(project_id, assistant_message)
        self._persist_draft_followup_state_for_turn(
            project_id,
            assistant_message,
            user_message=str(current_user_message.get("content") or ""),
        )
        # 上游偶尔会返回只有空白的 assistant（stream 截断、tag strip 后变空等），
        # 原样落盘会在下一轮产生 parts=[] 的 model turn，Gemini 拒收。用占位文本保底。
        if not assistant_message.strip():
            assistant_message = "（本轮无回复）"
        already_emitted_len = (
            0
            if buffer_required_write_content
            else len(accumulated) - len(stream_buffer)
        )
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
        immediate_reject_message = self._immediate_canonical_draft_reject_message()
        if immediate_reject_message:
            assistant_message, token_usage, system_notices = self._finalize_early_assistant_message(
                project_id,
                history,
                current_user_message,
                immediate_reject_message,
            )
            return {
                "content": assistant_message,
                "token_usage": token_usage,
                "system_notices": system_notices or None,
            }
        immediate_guidance_message = self._immediate_canonical_draft_guidance_message(project_id)
        if immediate_guidance_message:
            assistant_message, token_usage, system_notices = self._finalize_early_assistant_message(
                project_id,
                history,
                current_user_message,
                immediate_guidance_message,
            )
            return {
                "content": assistant_message,
                "token_usage": token_usage,
                "system_notices": system_notices or None,
            }
        provider_user_message = {
            **current_user_message,
            "transient_attachments": transient_attachments or [],
        }
        active_model = self._get_active_model_name()
        required_write_snapshots = self._build_required_write_snapshots(project_id, user_message)
        self._turn_context["required_write_snapshots"] = required_write_snapshots

        iterations = 0
        missing_write_retries = 0
        required_write_retries = 0
        self_correction_retries = 0
        assistant_message = ""
        compressed = False
        policy = self._resolve_context_policy()
        successful_writes: set[str] = set()
        successful_write_events: dict[str, list[dict]] = {}
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
                timeout = 120.0 if "v3.2" in active_model.lower() else 30.0
                request_kwargs = {
                    "model": active_model,
                    "messages": conversation,
                    "temperature": 0.7,
                    "max_tokens": self._get_request_max_tokens(policy),
                    "tools": self._get_tools(),
                    "tool_choice": "auto",
                    "timeout": timeout,
                    "stream": False,
                }
                self._debug_dump_request(request_kwargs, label="nostream", note=f"iteration={iterations}")
                try:
                    response = self.client.chat.completions.create(**request_kwargs)
                    break
                except Exception as e:
                    if retry < 1:
                        time.sleep(2)
                        continue
                    self._debug_dump_request(
                        request_kwargs,
                        label="nostream",
                        error=e,
                        note=f"iteration={iterations}",
                    )
                    return {
                        "content": self._format_provider_error(
                            e,
                            stream=False,
                            request_kwargs=request_kwargs,
                        ),
                        "token_usage": None,
                    }

            message = response.choices[0].message
            if message.tool_calls:
                guidance_override_message = None
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
                    write_event = self._extract_successful_write_event(
                        tool_call.function.name,
                        tool_call.function.arguments,
                        result,
                        project_id=project_id,
                    )
                    if write_event:
                        write_path = write_event["path"]
                        successful_writes.add(write_path)
                        successful_write_events.setdefault(write_path, []).append(write_event)
                    current_turn_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    guidance_override_message = (
                        guidance_override_message
                        or self._mixed_intent_guidance_after_canonical_mutation(project_id)
                    )
                    if guidance_override_message:
                        break
                if guidance_override_message:
                    assistant_message = guidance_override_message
                    token_usage = self._normalize_provider_usage(
                        getattr(response, "usage", None),
                        policy,
                        preflight_compaction_used=compressed,
                    )
                    break
                iterations += 1
            else:
                candidate_message = message.content or ""
                if (
                    self._looks_like_self_correction_loop(candidate_message)
                    and self_correction_retries < self.MAX_SELF_CORRECTION_RETRIES
                ):
                    self_correction_retries += 1
                    current_turn_messages.append({"role": "assistant", "content": candidate_message})
                    current_turn_messages.append({
                        "role": "user",
                        "content": self._build_self_correction_loop_feedback(),
                    })
                    continue
                missing_writes = self._get_missing_expected_writes(candidate_message, successful_writes)
                if missing_writes and missing_write_retries < self.MAX_MISSING_WRITE_RETRIES:
                    missing_write_retries += 1
                    current_turn_messages.append({"role": "assistant", "content": candidate_message})
                    current_turn_messages.append({
                        "role": "user",
                        "content": self._build_missing_write_feedback(missing_writes),
                    })
                    continue
                required_satisfied, missing_required_writes = self._required_writes_satisfied(
                    project_id,
                    required_write_snapshots,
                    successful_write_events,
                )
                if not required_satisfied:
                    if required_write_retries < self.MAX_MISSING_WRITE_RETRIES:
                        required_write_retries += 1
                        current_turn_messages.append({"role": "assistant", "content": candidate_message})
                        current_turn_messages.append({
                            "role": "user",
                            "content": self._build_required_write_feedback(missing_required_writes),
                        })
                        continue
                    assistant_message = self._build_required_write_failure_message(missing_required_writes)
                    token_usage = self._normalize_provider_usage(
                        getattr(response, "usage", None),
                        policy,
                        preflight_compaction_used=compressed,
                    )
                    break
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
        self._persist_draft_followup_state_for_turn(
            project_id,
            assistant_message,
            user_message=str(current_user_message.get("content") or ""),
        )
        if not assistant_message.strip():
            assistant_message = "（本轮无回复）"
        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        token_usage = self._finalize_post_turn_compaction(project_id, history, token_usage)
        system_notices = [
            SystemNotice(
                category=notice["category"],
                path=notice.get("path"),
                reason=notice["reason"],
                user_action=notice["user_action"],
                surface_to_user=notice["surface_to_user"],
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
        max_iterations: int = 20,
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

            if func_name == "append_report_draft":
                source_keys.add(f"file:{self.skill_engine.REPORT_DRAFT_PATH}")
                continue

            extra = None
            metadata_func_name = func_name
            if func_name in {"read_file", "write_file", "edit_file"}:
                file_path = args.get("file_path")
                if not isinstance(file_path, str) or not file_path.strip():
                    continue
                try:
                    extra = {"normalized_path": self.skill_engine.normalize_file_path(project_id, file_path)}
                except ValueError:
                    continue
                if func_name == "edit_file":
                    metadata_func_name = "write_file"

            metadata = self._build_tool_persistence_metadata(project_id, metadata_func_name, args, result, extra)
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
            content = message.get("content", "") or ""
            # 历史里可能残留 content="" 的 assistant（早期版本无兜底时落盘过）。
            # Gemini 对空 parts 的 model turn 会拒 400，这里统一兜底占位。
            if not content.strip():
                content = "（本轮无回复）"
            return {"role": "assistant", "content": content}

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
                    "description": (
                        "整文件覆盖写入。已有文件先 `read_file` 再写。"
                        "不要对 `content/report_draft_v1.md` 使用 `write_file`。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径，如 plan/outline.md"},
                            "content": {"type": "string", "description": "文件全量内容"},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": (
                        "对已存在文件做精确字符串替换。已有文件先 `read_file`。"
                        "正文草稿的局部修改用 `edit_file`，不要用 `write_file` 覆盖。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "已存在文件的路径，如 plan/data-log.md"},
                            "old_string": {"type": "string", "description": "要被替换的原字符串片段，必须在文件中唯一存在"},
                            "new_string": {"type": "string", "description": "替换成的新字符串；如果是追加条目，这里放 '原 old_string + 新内容'"},
                        },
                        "required": ["file_path", "old_string", "new_string"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "append_report_draft",
                    "description": (
                        "用于正文首次成稿或续写：`content/report_draft_v1.md` 不存在就创建，存在就追加到末尾。"
                        "正文已有文字要修改时改用 `read_file` + `edit_file`。"
                        "混合意图里的导出、质量检查、看文件、看字数本轮只给下一步提示。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "要追加到报告草稿末尾的新正文，必须是完整 Markdown 段落或章节，不要只写摘要。",
                            },
                        },
                        "required": ["content"],
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
                return self._execute_plan_write(
                    project_id,
                    file_path=args["file_path"],
                    content=args["content"],
                    source_tool_name="write_file",
                    source_tool_args=args,
                    persist_func_name="write_file",
                    persist_args=args,
                )
            if func_name == "edit_file":
                file_path = args.get("file_path", "")
                old_string = args.get("old_string", "")
                new_string = args.get("new_string", "")
                if not file_path:
                    return {"status": "error", "message": "缺少 file_path 参数。"}
                if not old_string:
                    canonical_edit_guidance = self._canonical_edit_missing_old_string_guidance(project_id, file_path)
                    if canonical_edit_guidance:
                        return {
                            "status": "error",
                            "message": canonical_edit_guidance,
                        }
                    return {
                        "status": "error",
                        "message": "old_string 不能为空；新建文件或整体重写请改用 write_file。",
                    }
                try:
                    normalized_read = self.skill_engine.normalize_file_path(project_id, file_path)
                    current_content = self.skill_engine.read_file(project_id, normalized_read)
                except (ValueError, FileNotFoundError) as exc:
                    return {
                        "status": "error",
                        "message": f"读取失败：{str(exc)}（edit_file 要求目标文件已存在；新建用 write_file）",
                    }
                count = current_content.count(old_string)
                if count == 0:
                    return {
                        "status": "error",
                        "message": "old_string 在文件中未找到，请先 read_file 核对原文后重试。",
                    }
                if count > 1:
                    return {
                        "status": "error",
                        "message": f"old_string 在文件中出现 {count} 次，不唯一；请在 old_string 前后补更多上下文让它唯一。",
                    }
                updated = current_content.replace(old_string, new_string, 1)
                return self._execute_plan_write(
                    project_id,
                    file_path=file_path,
                    content=updated,
                    source_tool_name="edit_file",
                    source_tool_args=args,
                    persist_func_name="write_file",
                    persist_args={"file_path": file_path, "content": updated},
                )
            if func_name == "append_report_draft":
                return self._execute_append_report_draft(project_id, args.get("content", ""))
            if func_name == "read_file":
                normalized_path = self.skill_engine.normalize_file_path(project_id, args["file_path"])
                content = self.skill_engine.read_file(project_id, normalized_path)
                result = {"status": "success", "content": content}
                self._record_turn_read_file_path(normalized_path)
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
            if func_name in {"write_file", "edit_file"}:
                self._emit_system_notice_once(
                    category="write_blocked",
                    path=None,
                    reason=str(e),
                    user_action="请根据提示调整写入目标或内容后再重试。",
                    surface_to_user=False,
                )
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"工具执行异常: {func_name}, 错误: {str(e)}")
            if func_name in {"write_file", "edit_file"}:
                self._emit_system_notice_once(
                    category="write_blocked",
                    path=None,
                    reason=f"工具执行失败: {str(e)}",
                    user_action="请检查写入条件是否满足，然后重试。",
                    surface_to_user=False,
                )
            return {"status": "error", "message": f"工具执行失败: {str(e)}"}

    def _execute_append_report_draft(self, project_id: str, content: object) -> Dict:
        if not isinstance(content, str):
            return {"status": "error", "message": "content 必须是字符串。"}

        append_content = content.strip()
        substantive_chars = self._count_report_append_substantive_chars(append_content)
        if substantive_chars < self.APPEND_REPORT_DRAFT_MIN_SUBSTANTIVE_CHARS:
            return {
                "status": "error",
                "message": (
                    "追加报告正文内容过短；去除常见 Markdown 标记和空白后，"
                    f"至少 {self.APPEND_REPORT_DRAFT_MIN_SUBSTANTIVE_CHARS} 个有效字符。"
                ),
            }

        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return {"status": "error", "message": f"项目 {project_id} 不存在"}

        draft_path = project_path / self.skill_engine.REPORT_DRAFT_PATH
        existing_content = ""
        if draft_path.exists():
            existing_content = draft_path.read_text(encoding="utf-8")

        combined_content = self._join_report_draft_append(existing_content, append_content)
        result = self._execute_plan_write(
            project_id,
            file_path=self.skill_engine.REPORT_DRAFT_PATH,
            content=combined_content,
            source_tool_name="append_report_draft",
            source_tool_args={"content": append_content},
            persist_func_name="write_file",
            persist_args={
                "file_path": self.skill_engine.REPORT_DRAFT_PATH,
                "content": combined_content,
            },
        )
        if result.get("status") != "success":
            return result

        targets = self.skill_engine._resolve_length_targets(project_path)
        report_word_floor = int(targets.get("report_word_floor", 0) or 0)
        report_progress = result.get("report_progress") if isinstance(result.get("report_progress"), dict) else {}
        word_count = int(report_progress.get("current_count") or 0)
        if word_count <= 0:
            word_count = self.skill_engine._count_words(combined_content)
        result.update(
            {
                "path": self.skill_engine.REPORT_DRAFT_PATH,
                "appended_chars": len(append_content),
                "word_count": word_count,
                "report_word_floor": report_word_floor,
                "report_ready": self.skill_engine._has_effective_report_draft(
                    project_path,
                    min_words=report_word_floor,
                ),
            }
        )
        return result

    def _validate_append_turn_canonical_draft_write(
        self,
        project_id: str,
        normalized_path: str,
        content: str,
        *,
        source_tool_name: str,
    ) -> str | None:
        if not self._is_canonical_report_draft_path(normalized_path):
            return None
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("expected_tool_family") != "append_report_draft":
            return None
        if source_tool_name == "append_report_draft":
            return None

        current_text = self._read_project_file_text(project_id, normalized_path) or ""
        append_retry_guidance = (
            "本轮要求继续补写报告正文，不能用整份覆盖或改写已有草稿来替代追加。"
            "续写或新增章节请用 `append_report_draft`；"
            "若本轮要改写已有内容，请先 `read_file` 再用 `edit_file` 处理对应范围。"
        )
        if source_tool_name in {"write_file", "edit_file"}:
            return append_retry_guidance
        return None

    def _count_report_append_substantive_chars(self, content: str) -> int:
        text = content or ""
        text = re.sub(r"```[^\n]*", "", text)
        text = text.replace("```", "")
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
        text = re.sub(r"[*_~`]+", "", text)
        text = re.sub(r"!?\[|\]\(|\)|\[|\]", "", text)
        text = re.sub(r"[\s\u3000]+", "", text)
        return len(text)

    def _join_report_draft_append(self, existing_content: str, append_content: str) -> str:
        existing = (existing_content or "").rstrip()
        addition = (append_content or "").strip()
        if not existing:
            return addition
        return f"{existing}\n\n{addition}"

    def _execute_plan_write(
        self,
        project_id: str,
        *,
        file_path: str,
        content: str,
        source_tool_name: str,
        persist_func_name: str,
        persist_args: Dict,
        source_tool_args: Dict | None = None,
    ) -> Dict:
        """write_file / edit_file 共享的写入门禁链。入参已经是"最终要落盘的完整内容"。"""
        normalized_early = self.skill_engine._to_posix(file_path).lstrip("/")
        project_path = self.skill_engine.get_project_path(project_id)
        try:
            normalized_preview = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            normalized_preview = normalized_early
        if self._is_noncanonical_report_draft_path(normalized_preview):
            reason = self._build_report_draft_path_error(normalized_preview)
            self._emit_system_notice_once(
                category="report_draft_path_blocked",
                path=normalized_preview,
                reason=reason,
                user_action=f"请改写到 `{self.skill_engine.REPORT_DRAFT_PATH}` 后再继续。",
                surface_to_user=True,
            )
            return {"status": "error", "message": reason}
        if project_path and normalized_early in self._S0_BLOCKED_PLAN_FILES:
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
                    surface_to_user=True,
                )
                return {"status": "error", "message": reason}
        non_plan_write_block_reason = self._non_plan_write_block_reason(project_id, file_path)
        if non_plan_write_block_reason:
            reason = non_plan_write_block_reason
            self._emit_system_notice_once(
                category="non_plan_write_blocked",
                path=None,
                reason=reason,
                user_action="请先让用户确认大纲或明确要求继续正文后，再尝试写正式内容。",
                surface_to_user=True,
            )
            return {"status": "error", "message": reason}
        if self._should_require_fetch_url_before_write(project_id, file_path):
            reason = "本轮已经做过 web_search，但还没调用 fetch_url 阅读网页正文。请先对候选链接使用 fetch_url，再写正式文件。"
            self._emit_system_notice_once(
                category="fetch_url_gate_blocked",
                path=None,
                reason=reason,
                user_action="请先读取候选网页正文，再把外部信息写入正式文件。",
                surface_to_user=False,
            )
            return {"status": "error", "message": reason}
        normalized_path = self.skill_engine.validate_plan_write(project_id, file_path)
        if self._is_canonical_report_draft_path(normalized_path):
            normalized_path = self.skill_engine.REPORT_DRAFT_PATH
        if source_tool_name == "write_file":
            canonical_write_file_error = self._build_canonical_draft_write_file_block_message(
                project_id,
                normalized_path,
                source_tool_args=source_tool_args,
            )
            if canonical_write_file_error:
                self._emit_system_notice_once(
                    category="report_draft_destructive_write_blocked",
                    path=normalized_path,
                    reason=canonical_write_file_error,
                    user_action=(
                        "正文首次成稿或续写请用 `append_report_draft`；"
                        "修改已有正文请先 `read_file`，再用 `edit_file`。"
                    ),
                    surface_to_user=True,
                )
                return {"status": "error", "message": canonical_write_file_error}
        mutation_limit_error = self._validate_canonical_draft_turn_mutation_limit(
            normalized_path,
        )
        if mutation_limit_error:
            self._emit_system_notice_once(
                category="report_draft_destructive_write_blocked",
                path=normalized_path,
                reason=mutation_limit_error,
                user_action="请基于当前已落盘的正文结果直接向用户汇报，本轮不要继续改动正文。",
                surface_to_user=True,
            )
            return {"status": "error", "message": mutation_limit_error}
        read_before_write_error = self._validate_existing_file_read_before_write(
            project_id,
            normalized_path,
            source_tool_name=source_tool_name,
        )
        if read_before_write_error:
            self._emit_system_notice_once(
                category="write_blocked",
                path=normalized_path,
                reason=read_before_write_error,
                user_action="请先读取目标文件最新内容，再重新提交写入。",
                surface_to_user=False,
            )
            return {"status": "error", "message": read_before_write_error}
        destructive_write_error = self._validate_required_report_draft_prewrite(
            project_id,
            normalized_path,
            content,
            source_tool_name=source_tool_name,
            source_tool_args=source_tool_args,
        )
        if destructive_write_error:
            self._emit_system_notice_once(
                category="report_draft_destructive_write_blocked",
                path=normalized_path,
                reason=destructive_write_error,
                user_action=(
                    "续写或新增章节请用 `append_report_draft`；"
                    "改写已有正文请先 `read_file`，再用 `edit_file` 处理对应范围。"
                ),
                surface_to_user=True,
            )
            return {"status": "error", "message": destructive_write_error}
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
                surface_to_user=True,
            )
            return {"status": "error", "message": reason}
        project_path = self.skill_engine.get_project_path(project_id)
        checkpoints = self.skill_engine._load_stage_checkpoints(project_path) if project_path else {}
        signature_error = self.skill_engine.validate_self_signature(
            normalized_path,
            content,
            checkpoints,
        )
        if signature_error:
            self._emit_system_notice_once(
                category="write_blocked",
                path=normalized_path,
                reason=signature_error,
                user_action="请联系用户在右侧工作区完成对应的确认后再写入",
                surface_to_user=True,
            )
            return {"status": "error", "message": signature_error}
        analysis_refs_error = self._validate_analysis_notes_refs_for_write(
            project_id,
            normalized_path,
            content,
        )
        if analysis_refs_error:
            self._emit_system_notice_once(
                category="analysis_refs_missing",
                path=normalized_path,
                reason=analysis_refs_error,
                user_action=(
                    "请在每条关键发现后补充明确的 data-log 引用，"
                    "例如 `[DL-2026-01]` 或 `[DL-2026-01/06]`，再重新写入。"
                ),
                surface_to_user=False,
            )
            return {"status": "error", "message": analysis_refs_error}
        should_emit_data_log_hint = self._is_first_data_log_write(project_id, normalized_path)
        self.skill_engine.write_file(project_id, normalized_path, content)
        result = {"status": "success", "message": f"已写入文件: {normalized_path}"}
        canonical_progress_snapshot = None
        if self._is_canonical_report_draft_path(normalized_path):
            canonical_progress_snapshot = self._canonical_draft_progress_snapshot(project_id)
            result["path"] = self.skill_engine.REPORT_DRAFT_PATH
            result.update(
                self._canonical_draft_progress_response_payload(canonical_progress_snapshot)
            )
            progress_message = self._build_canonical_draft_write_success_message(
                canonical_progress_snapshot
            )
            if progress_message:
                result["message"] = progress_message
        if should_emit_data_log_hint:
            self._emit_system_notice_once(
                category="data_log_format_hint",
                path=normalized_path,
                reason=(
                    "data-log.md 每条事实必须写成 `### [DL-YYYY-NN] 事实标题`，"
                    "下方带 URL / `material:xxx` / `访谈:` / `调研:` 来源标记。"
                ),
                user_action="不要用 Markdown 表格记录事实；请拆成独立 DL-id 条目后继续写入。",
                surface_to_user=False,
            )
        self._persist_successful_tool_result(
            project_id,
            source_tool_name,
            source_tool_args or {},
            result,
            {
                "normalized_path": normalized_path,
                "metadata_func_name": persist_func_name,
                "metadata_args": persist_args,
            },
        )
        self._record_successful_canonical_draft_mutation(
            source_tool_name=source_tool_name,
            normalized_path=normalized_path,
            progress_snapshot=canonical_progress_snapshot,
        )
        return result

    def _validate_required_report_draft_prewrite(
        self,
        project_id: str,
        normalized_path: str,
        content: str,
        *,
        source_tool_name: str,
        source_tool_args: Dict | None = None,
    ) -> str | None:
        if not self._is_canonical_report_draft_path(normalized_path):
            return None

        snapshots = self._turn_context.get("required_write_snapshots")
        if not isinstance(snapshots, dict):
            return None
        if self.skill_engine.REPORT_DRAFT_PATH not in snapshots:
            return None

        snapshot = snapshots.get(self.skill_engine.REPORT_DRAFT_PATH)
        if isinstance(snapshot, dict) and snapshot.get("intent_kind") == "replace_text":
            return self._validate_replace_text_report_draft_prewrite(
                project_id,
                snapshot,
                source_tool_name=source_tool_name,
                source_tool_args=source_tool_args,
            )

        append_turn_error = self._validate_append_turn_canonical_draft_write(
            project_id,
            normalized_path,
            content,
            source_tool_name=source_tool_name,
        )
        if append_turn_error:
            return append_turn_error

        decision = self._turn_context.get("canonical_draft_decision")
        rewrite_scope = None
        rewrite_target_snapshot = None
        rewrite_target_label = None
        if (
            isinstance(decision, dict)
            and decision.get("expected_tool_family") == "edit_file"
        ):
            rewrite_scope = decision.get("required_edit_scope")
            rewrite_target_snapshot = decision.get("rewrite_target_snapshot")
            rewrite_target_label = decision.get("rewrite_target_label")
        if isinstance(snapshot, dict):
            rewrite_scope = snapshot.get("required_edit_scope") or rewrite_scope
            rewrite_target_snapshot = snapshot.get("rewrite_target_snapshot") or rewrite_target_snapshot
            rewrite_target_label = snapshot.get("rewrite_target_label") or rewrite_target_label

        if rewrite_scope in {"section", "full_draft"}:
            if source_tool_name == "append_report_draft":
                if rewrite_scope == "full_draft":
                    return (
                        "本轮用户要求全文重写报告正文，不能用 `append_report_draft` 追加内容来替代重写。"
                        "请先用 `read_file` 读取当前草稿，再用 `edit_file` 把整份旧稿替换为重写后的完整新稿。"
                    )
                return (
                    "本轮用户要求改写现有章节，不能用 `append_report_draft` 追加内容来替代章节修改。"
                    "请先用 `read_file` 读取当前草稿，再用 `edit_file` 修改目标章节。"
                )
            if source_tool_name == "write_file":
                if rewrite_scope == "full_draft":
                    return (
                        "本轮用户要求全文重写报告正文，请先用 `read_file` 读取当前草稿，"
                        "再用 `edit_file` 把整份旧稿替换为重写后的完整新稿。"
                    )
                return (
                        "本轮用户要求改写现有章节，请先用 `read_file` 读取当前草稿，"
                        "再用 `edit_file` 修改目标章节。"
                    )
            if source_tool_name == "edit_file":
                if not isinstance(source_tool_args, dict):
                    return "本轮正文改写必须通过 `read_file` 后再用 `edit_file` 提交完整 old_string/new_string。"
                old_string = source_tool_args.get("old_string")
                new_string = source_tool_args.get("new_string")
                if not isinstance(old_string, str) or not old_string:
                    return self._canonical_edit_missing_old_string_guidance(project_id, normalized_path)
                current_text = self._read_project_file_text(project_id, normalized_path)
                if current_text is None:
                    return "读取当前草稿失败。请先用 `read_file` 读取正文，再重新提交 `edit_file`。"
                if rewrite_scope == "full_draft":
                    if old_string != current_text:
                        return (
                            "本轮要求全文重写报告正文，`edit_file.old_string` 必须等于当前整份草稿。"
                            "请先 `read_file` 读取完整正文，再把整份旧稿作为 old_string 提交。"
                        )
                    return None

                expected_section = str(rewrite_target_snapshot or "")
                if old_string == expected_section and expected_section:
                    new_string_scope_error = self._validate_section_rewrite_new_string_scope(
                        expected_section,
                        str(new_string or ""),
                        rewrite_target_label=rewrite_target_label,
                    )
                    if new_string_scope_error:
                        return new_string_scope_error
                    return None
                heading_count = len(self._extract_markdown_heading_labels(old_string))
                if old_string == current_text:
                    return (
                        "本轮只允许改写目标章节，不能用覆盖整份草稿的 `edit_file.old_string`。"
                        "请先 `read_file`，再只提交目标章节的完整原文。"
                    )
                if heading_count > 1:
                    return (
                        "本轮只允许改写单个目标章节，`edit_file.old_string` 不能同时覆盖多个标题段。"
                        "请先 `read_file`，再只提交目标章节的完整原文。"
                    )
                if not expected_section:
                    return "当前缺少目标章节快照。请先 `read_file` 读取正文，再重新提交章节改写。"
                if old_string != expected_section:
                    label_hint = f"`{rewrite_target_label}`" if isinstance(rewrite_target_label, str) and rewrite_target_label else "目标章节"
                    return (
                        f"本轮要求改写 {label_hint}，`edit_file.old_string` 必须等于该章节的完整原文。"
                        "请先 `read_file` 读取正文，再只提交目标章节的完整原文。"
                    )
                return None

        if source_tool_name not in {"write_file", "edit_file"}:
            return None

        current = self._snapshot_project_file(project_id, self.skill_engine.REPORT_DRAFT_PATH)
        if not current.get("exists"):
            return None

        current_word_count = int(current.get("word_count") or 0)
        new_word_count = self.skill_engine._count_words(content or "")
        if current_word_count <= new_word_count:
            return None

        return (
            "本轮要求更新报告正文，但当前提交的最终内容比现有草稿更短，"
            "可能覆盖并丢失已有正文。"
            "续写或新增章节请用 `append_report_draft`；"
            "若本轮要改写已有内容，请先 `read_file` 再用 `edit_file` 处理对应范围。"
        )

    def _validate_replace_text_report_draft_prewrite(
        self,
        project_id: str,
        snapshot: dict,
        *,
        source_tool_name: str,
        source_tool_args: Dict | None,
    ) -> str | None:
        if source_tool_name == "write_file":
            return (
                "本轮用户要求对报告正文做局部替换，不能用 `write_file` 覆盖 "
                f"`{self.skill_engine.REPORT_DRAFT_PATH}`。"
                "请改用 `edit_file`，通过 old_string/new_string 做精确替换，"
                "避免覆盖整份草稿。"
            )

        if source_tool_name == "append_report_draft":
            return (
                "本轮用户要求对报告正文做局部替换，不能用 `append_report_draft` 追加 "
                f"`{self.skill_engine.REPORT_DRAFT_PATH}`。"
                "请改用 `edit_file`，通过 old_string/new_string 对目标文字做精确替换。"
            )

        if source_tool_name != "edit_file":
            return (
                "本轮用户要求对报告正文做局部替换，只能用 `edit_file` "
                "通过 old_string/new_string 做精确替换。"
            )

        if not isinstance(source_tool_args, dict):
            return (
                "本轮用户要求对报告正文做局部替换，但未检测到 `edit_file` 的 "
                "old_string/new_string 参数。请重新用 `edit_file` 精确替换目标文字。"
            )

        old_text = str(snapshot.get("old_text") or "")
        new_text = str(snapshot.get("new_text") or "")
        old_string = source_tool_args.get("old_string")
        new_string = source_tool_args.get("new_string")
        if not (
            old_text
            and new_text
            and isinstance(old_string, str)
            and isinstance(new_string, str)
        ):
            return (
                "本轮用户要求对报告正文做局部替换，`edit_file` 必须提供有效的 "
                "old_string/new_string，并包含用户指定的新旧文字。"
            )

        if old_text not in old_string:
            return (
                "本轮用户要求替换的旧文字未出现在 `edit_file.old_string` 中。"
                "请先 read_file 核对原文，再用包含该旧文字的 old_string 精确替换。"
            )
        if new_text not in new_string:
            return (
                "本轮用户要求替换成的新文字未出现在 `edit_file.new_string` 中。"
                "请用 new_string 明确包含用户指定的新文字。"
            )

        current_text = self._read_project_file_text(
            project_id,
            self.skill_engine.REPORT_DRAFT_PATH,
        )
        current_length = len(current_text or "")
        old_string_length = len(old_string)
        old_string_limit = max(len(old_text) + 80, int(0.15 * current_length))
        if old_string_length > old_string_limit:
            return (
                "`edit_file.old_string` 覆盖范围过大，像是在重写整份报告草稿。"
                "本轮是局部替换，请只保留目标文字及必要上下文后重试。"
            )
        new_string_limit = max(len(new_text) + 80, int(0.15 * current_length))
        if len(new_string) > new_string_limit:
            return (
                "`edit_file.new_string` 覆盖范围过大，像是在重写整份报告草稿。"
                "本轮是局部替换，请只保留目标文字及必要上下文后重试。"
            )

        return None

    def _validate_analysis_notes_refs_for_write(
        self,
        project_id: str,
        normalized_path: str,
        content: str,
    ) -> str | None:
        if normalized_path != "plan/analysis-notes.md":
            return None
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return None
        targets = self.skill_engine._resolve_length_targets(project_path)
        if not self.skill_engine._has_enough_data_log_sources(project_path, targets["data_log_min"]):
            return None
        if not self.skill_engine._has_substantive_body(content):
            return None
        if self.skill_engine._count_analysis_refs_in_text(project_path, content) > 0:
            return None
        return (
            "analysis-notes.md 中的关键发现需要显式引用 data-log 条目，"
            "例如 `[DL-2026-01]` 或 `[DL-2026-01/06]`。"
            "当前写入内容没有任何可统计的 DL 引用，请补上证据引用后再写入。"
        )

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
        from backend.stage_ack import StageAckParser

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
        parser = StageAckParser()
        sanitized = []
        for message in normalized:
            role = message.get("role")
            content = message.get("content", "") or ""
            if role == "assistant" and "<stage-ack" in content.lower():
                new_message = dict(message)
                new_message["content"] = parser.strip(content)
                sanitized.append(new_message)
            else:
                sanitized.append(message)
        return sanitized

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

    # 调试辅助：仅在显式启用时 dump 已脱敏的上游请求元数据。
    # 启用方式：CONSULTING_REPORT_DEBUG_DUMP=1。
    def _debug_dump_request(
        self,
        request_kwargs: Dict,
        *,
        label: str,
        error: object | None = None,
        note: str | None = None,
    ) -> None:
        if os.environ.get("CONSULTING_REPORT_DEBUG_DUMP") != "1":
            return
        try:
            from pathlib import Path

            debug_dir = Path.home() / ".consulting-report" / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "label": label,
                "ts": datetime.utcnow().isoformat() + "Z",
                "model": request_kwargs.get("model"),
                "temperature": request_kwargs.get("temperature"),
                "max_tokens": request_kwargs.get("max_tokens"),
                "tool_choice": request_kwargs.get("tool_choice"),
                "stream": request_kwargs.get("stream"),
                "tools": self._debug_tool_names(request_kwargs.get("tools")),
                "messages": self._debug_redact_messages(request_kwargs.get("messages")),
            }
            if note is not None:
                snapshot["note"] = note
            if error is not None:
                snapshot["error"] = self._debug_summarize_error(error, request_kwargs)
            (debug_dir / "payload-latest.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if error is not None:
                stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                (debug_dir / f"error-{stamp}-{label}.json").write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def _debug_summarize_error(self, error: object, request_kwargs: Dict) -> dict:
        message = self._debug_redact_error_message(str(error), request_kwargs)
        return {
            "type": type(error).__name__,
            "message": message,
        }

    def _debug_redact_error_message(self, message: str, request_kwargs: Dict) -> str:
        sanitized = message or ""
        sanitized = self.DEBUG_DATA_URL_BASE64_RE.sub("[redacted]", sanitized)
        for fragment in self._debug_sensitive_request_fragments(request_kwargs):
            sanitized = sanitized.replace(fragment, "[redacted]")
        sanitized = self.DEBUG_LONG_BASE64_FRAGMENT_RE.sub("[redacted]", sanitized)
        sanitized = re.sub(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
            "Bearer [redacted]",
            sanitized,
        )
        sanitized = re.sub(
            r"(?i)\b(api[_-]?key|token|password|secret)(\s*[:=]\s*)(['\"]?)[^'\"\s,}]{4,}\3",
            r"\1\2[redacted]",
            sanitized,
        )
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        if len(sanitized) > 240:
            sanitized = sanitized[:237].rstrip() + "..."
        return sanitized

    def _debug_sensitive_request_fragments(self, request_kwargs: Dict) -> list[str]:
        fragments: set[str] = set()
        messages = request_kwargs.get("messages")
        if not isinstance(messages, list):
            return []

        for message in messages:
            if not isinstance(message, dict):
                continue
            if "content" in message:
                content = message.get("content")
                self._debug_add_sensitive_fragment(
                    fragments,
                    self._extract_message_text(content),
                )
                self._debug_collect_message_image_url_fragments(content, fragments)
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    continue
                self._debug_add_sensitive_fragment(fragments, arguments)
                try:
                    parsed_arguments = json.loads(arguments)
                except Exception:
                    continue
                self._debug_collect_sensitive_strings(parsed_arguments, fragments)

        return sorted(fragments, key=len, reverse=True)

    def _debug_collect_message_image_url_fragments(self, content, fragments: set[str]) -> None:
        if not isinstance(content, list):
            return
        for item in content:
            image_url = self._debug_get_value(item, "image_url")
            if isinstance(image_url, str):
                url = image_url
            else:
                url = self._debug_get_value(image_url, "url")
            if not isinstance(url, str):
                continue
            self._debug_add_sensitive_fragment(fragments, url)
            self._debug_add_data_url_payload_fragment(fragments, url)

    def _debug_add_data_url_payload_fragment(self, fragments: set[str], url: str) -> None:
        marker = ";base64,"
        marker_index = url.lower().find(marker)
        if not url.lower().startswith("data:") or marker_index == -1:
            return
        self._debug_add_sensitive_fragment(fragments, url[marker_index + len(marker):])

    def _debug_get_value(self, value, key: str):
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    def _debug_collect_sensitive_strings(self, value, fragments: set[str]) -> None:
        if isinstance(value, str):
            self._debug_add_sensitive_fragment(fragments, value)
            return
        if isinstance(value, dict):
            for item in value.values():
                self._debug_collect_sensitive_strings(item, fragments)
            return
        if isinstance(value, list):
            for item in value:
                self._debug_collect_sensitive_strings(item, fragments)

    def _debug_add_sensitive_fragment(self, fragments: set[str], value: str) -> None:
        text = (value or "").strip()
        if len(text) < 4:
            return
        fragments.add(text)
        for ensure_ascii in (False, True):
            encoded = json.dumps(text, ensure_ascii=ensure_ascii)[1:-1]
            if encoded != text:
                fragments.add(encoded)

    def _debug_tool_names(self, tools) -> list[str]:
        names: list[str] = []
        if not isinstance(tools, list):
            return names
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def _debug_redact_messages(self, messages) -> list[dict]:
        if not isinstance(messages, list):
            return []
        redacted: list[dict] = []
        for message in messages:
            if not isinstance(message, dict):
                redacted.append({"type": type(message).__name__})
                continue
            item: dict = {}
            role = message.get("role")
            if isinstance(role, str):
                item["role"] = role
            if "content" in message:
                content_text = self._extract_message_text(message.get("content"))
                item["content"] = "[redacted]" if content_text else ""
                item["content_length"] = len(content_text)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                item["tool_calls"] = self._debug_redact_tool_calls(tool_calls)
            redacted.append(item)
        return redacted

    def _debug_redact_tool_calls(self, tool_calls: list) -> list[dict]:
        redacted: list[dict] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                redacted.append({"type": type(tool_call).__name__})
                continue
            item: dict = {}
            call_id = tool_call.get("id")
            if isinstance(call_id, str):
                item["id"] = call_id
            call_type = tool_call.get("type")
            if isinstance(call_type, str):
                item["type"] = call_type
            function = tool_call.get("function")
            if isinstance(function, dict):
                redacted_function: dict = {}
                name = function.get("name")
                if isinstance(name, str):
                    redacted_function["name"] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    redacted_function["arguments"] = "[redacted]"
                    redacted_function["arguments_length"] = len(arguments)
                item["function"] = redacted_function
            redacted.append(item)
        return redacted

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
                "本轮如用户明确要求正文，可在更新 plan 后处理正文草稿。"
                f"正文草稿统一写入 `{self.skill_engine.REPORT_DRAFT_PATH}`。"
            )
        else:
            turn_rule = (
                "本轮只能做两类事：1）继续问清关键信息；2）更新 `plan/` 内文件。"
                "在用户明确确认大纲或明确要求继续正文前，禁止写正文、章节草稿、report_draft 或最终报告。"
                f"允许写正文后，报告正文草稿也只能写入 `{self.skill_engine.REPORT_DRAFT_PATH}`。"
                "如果信息不足，提出问题后就停止本轮，不要擅自继续。"
            )
        draft_rule_block = (
            "正文草稿规则：\n"
            "- 已有文件先 `read_file`，再用 `write_file` / `edit_file`\n"
            "- 正文首次成稿或续写 -> `append_report_draft`\n"
            "- 正文局部修改 -> `edit_file`\n"
            f"- 不要对 `{self.skill_engine.REPORT_DRAFT_PATH}` 使用 `write_file`\n"
            "- mixed-intent 的导出 / 质量检查 / 看文件 / 看字数只给下一步提示，不要同轮执行"
        )
        evidence_rule = (
            "如果本轮调用了 `web_search` 并准备把外部网页信息写进正式文件，"
            "必须先对候选链接调用 `fetch_url` 阅读正文；搜索结果摘要不能直接当作正式依据。"
        )
        # 管理型通道（newapi → Gemini）流式 tool_call chunk 偶发会把并行调用的 index 合并到 0，
        # 导致 name/arguments 被首尾拼接成 `web_searchweb_search` 等畸形条目，后端只能作废本轮并请模型重发。
        # 强制一次只发一个 tool_call，从源头规避该合并 bug。
        concurrency_rule = (
            "每轮消息只发一个 tool_call，等该工具返回结果后再发下一个；"
            "不要在一条消息里并行发起多个工具调用。"
        )
        return (
            f"{skill_prompt}\n\n## 当前轮次约束\n{turn_rule}\n{draft_rule_block}\n"
            f"{evidence_rule}\n{concurrency_rule}\n\n{project_context}"
        )

    def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
        return {
            "can_write_non_plan": can_write_non_plan,
            "generic_non_plan_write_allowed": can_write_non_plan,
            "web_search_disabled": False,
            "web_search_performed": False,
            "fetch_url_performed": False,
            "web_search_count": 0,
            "user_notice_emitted": False,
            "internal_notice_emitted": False,
            "pending_system_notices": [],
            "required_write_snapshots": {},
            "canonical_draft_decision": None,
            "draft_followup_flags": None,
            "read_file_paths": set(),
            "canonical_draft_mutation": None,
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
                                surface_to_user=True,
                            )
                        else:
                            raise exc
                    else:
                        self._turn_context["checkpoint_event"] = {"action": action, "key": key}
                else:
                    self._turn_context["pending_stage_keyword"] = (action, key)
        canonical_draft_decision = self._classify_canonical_draft_turn(project_id, user_message)
        generic_non_plan_write_allowed = self._should_allow_generic_non_plan_write(project_id, user_message)
        self._turn_context["canonical_draft_decision"] = canonical_draft_decision
        self._turn_context["generic_non_plan_write_allowed"] = generic_non_plan_write_allowed
        self._turn_context["can_write_non_plan"] = (
            generic_non_plan_write_allowed
            or canonical_draft_decision.get("mode") == "require"
        )
        return self._turn_context

    def _immediate_canonical_draft_reject_message(self) -> str | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("mode") != "reject":
            return None
        fixed_message = decision.get("fixed_message")
        if not isinstance(fixed_message, str) or not fixed_message.strip():
            return None
        return fixed_message

    def _turn_read_file_paths(self) -> set[str]:
        paths = self._turn_context.get("read_file_paths")
        if isinstance(paths, set):
            return paths
        normalized_paths = set()
        if isinstance(paths, (list, tuple, set)):
            normalized_paths = {
                str(path).strip()
                for path in paths
                if isinstance(path, str) and str(path).strip()
            }
        self._turn_context["read_file_paths"] = normalized_paths
        return normalized_paths

    def _canonicalize_turn_file_path(self, normalized_path: str) -> str:
        if self._is_canonical_report_draft_path(normalized_path):
            return self.skill_engine.REPORT_DRAFT_PATH
        return normalized_path

    def _record_turn_read_file_path(self, normalized_path: str) -> None:
        if not isinstance(normalized_path, str) or not normalized_path.strip():
            return
        self._turn_read_file_paths().add(
            self._canonicalize_turn_file_path(normalized_path)
        )

    def _has_same_turn_read_file(self, normalized_path: str) -> bool:
        if not isinstance(normalized_path, str) or not normalized_path.strip():
            return False
        canonicalized = self._canonicalize_turn_file_path(normalized_path)
        return canonicalized in self._turn_read_file_paths()

    def _validate_existing_file_read_before_write(
        self,
        project_id: str,
        normalized_path: str,
        *,
        source_tool_name: str,
    ) -> str | None:
        if source_tool_name not in {"write_file", "edit_file"}:
            return None
        current = self._snapshot_project_file(project_id, normalized_path)
        if not current.get("exists"):
            return None
        if self._has_same_turn_read_file(normalized_path):
            return None
        return (
            f"本轮要修改的文件 `{normalized_path}` 已存在。"
            "请先调用 `read_file` 读取最新内容，再执行写入或替换。"
        )

    def _successful_canonical_draft_mutation(self) -> dict | None:
        mutation = self._turn_context.get("canonical_draft_mutation")
        if isinstance(mutation, dict):
            return mutation
        return None

    def _record_successful_canonical_draft_mutation(
        self,
        *,
        source_tool_name: str,
        normalized_path: str,
        progress_snapshot: dict | None = None,
    ) -> None:
        if not self._is_canonical_report_draft_path(normalized_path):
            return
        mutation = {
            "tool": source_tool_name,
            "path": self.skill_engine.REPORT_DRAFT_PATH,
        }
        if isinstance(progress_snapshot, dict):
            mutation["progress_snapshot"] = progress_snapshot
        self._turn_context["canonical_draft_mutation"] = mutation

    def _canonical_draft_progress_snapshot(
        self,
        project_id: str,
        *,
        effective_turn_target_count: int | None = None,
    ) -> dict | None:
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return None

        draft_text = self._read_project_file_text(project_id, self.skill_engine.REPORT_DRAFT_PATH)
        if draft_text is None:
            return None

        default_target_count = self._project_default_report_target_count(project_path)
        requested_target_count = effective_turn_target_count
        if requested_target_count is None:
            requested_target_count = self._current_turn_requested_target_count()
        carried_target_count = self._current_turn_carried_followup_target_count(
            project_id,
            default_target_count=default_target_count,
        )
        if requested_target_count is None and carried_target_count is not None:
            requested_target_count = carried_target_count
        effective_target_count = self._current_turn_effective_target_count(
            default_target_count=default_target_count
        )
        if effective_target_count is None:
            effective_target_count = carried_target_count
        if isinstance(requested_target_count, int) and requested_target_count > default_target_count:
            effective_target_count = requested_target_count
        current_count = self.skill_engine._count_words(draft_text)
        turn_target_count = (
            requested_target_count
            if isinstance(requested_target_count, int) and requested_target_count > 0
            else default_target_count
        )
        turn_target_met = current_count >= turn_target_count
        snapshot = {
            "path": self.skill_engine.REPORT_DRAFT_PATH,
            "report_progress": {
                "current_count": current_count,
                "target_word_count": default_target_count,
                "meets_target": current_count >= default_target_count,
            },
            "turn_target_count": turn_target_count,
            "turn_target_met": turn_target_met,
        }
        if isinstance(effective_target_count, int) and effective_target_count > default_target_count:
            snapshot["effective_turn_target_count"] = effective_target_count
            snapshot["effective_turn_target_met"] = current_count >= effective_target_count
            snapshot["turn_target_count"] = effective_target_count
            snapshot["turn_target_met"] = current_count >= effective_target_count
        return snapshot

    def _canonical_draft_progress_response_payload(self, snapshot: dict | None) -> dict:
        if not isinstance(snapshot, dict):
            return {}
        payload = {
            "report_progress": dict(snapshot.get("report_progress") or {}),
        }
        if "effective_turn_target_count" in snapshot:
            payload["effective_turn_target_count"] = snapshot["effective_turn_target_count"]
            payload["effective_turn_target_met"] = snapshot["effective_turn_target_met"]
        return payload

    def _build_canonical_draft_write_success_message(self, snapshot: dict | None) -> str | None:
        if not isinstance(snapshot, dict):
            return None
        report_progress = snapshot.get("report_progress")
        if not isinstance(report_progress, dict):
            return None
        current_count = int(report_progress.get("current_count") or 0)
        turn_target_count = int(snapshot.get("turn_target_count") or 0)
        if turn_target_count <= 0:
            turn_target_count = int(report_progress.get("target_word_count") or 0)
        status_text = "已达到本轮目标。" if snapshot.get("turn_target_met") else "仍需继续补全。"
        return (
            f"已写入 {self.skill_engine.REPORT_DRAFT_PATH}；"
            f"当前 {current_count}/{turn_target_count} 字，{status_text}"
        )

    def _validate_canonical_draft_turn_mutation_limit(
        self,
        normalized_path: str,
    ) -> str | None:
        if not self._is_canonical_report_draft_path(normalized_path):
            return None
        mutation = self._successful_canonical_draft_mutation()
        if not isinstance(mutation, dict):
            return None
        tool_name = mutation.get("tool")
        tool_hint = (
            f"`{tool_name}`"
            if isinstance(tool_name, str) and tool_name.strip()
            else "前一个工具"
        )
        return (
            f"本轮已经成功通过 {tool_hint} 修改了 `{self.skill_engine.REPORT_DRAFT_PATH}`。"
            "请基于当前落盘结果直接向用户汇报，不要继续修改该文件。"
        )

    def _build_canonical_draft_write_file_block_message(
        self,
        project_id: str,
        normalized_path: str,
        *,
        source_tool_args: Dict | None = None,
    ) -> str | None:
        if not self._is_canonical_report_draft_path(normalized_path):
            return None
        specific_error = self._validate_required_report_draft_prewrite(
            project_id,
            normalized_path,
            str((source_tool_args or {}).get("content") or ""),
            source_tool_name="write_file",
            source_tool_args=source_tool_args,
        )
        if specific_error:
            return specific_error
        return (
            f"不要对 `{self.skill_engine.REPORT_DRAFT_PATH}` 使用 `write_file`。"
            "首次成稿或续写请用 `append_report_draft`；"
            "修改已有正文请先 `read_file`，再用 `edit_file`。"
        )

    def _mixed_intent_secondary_action_label(self, family: str | None) -> str | None:
        mapping = {
            "export": "导出",
            "quality_check": "质量检查",
            "inspect_file": "看看文件",
            "inspect_word_count": "看看现在多少字",
        }
        return mapping.get(family or "")

    def _build_mixed_intent_guidance_message(self, project_id: str) -> str | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        priority = str(decision.get("priority") or "")
        family = self._mixed_intent_secondary_action_label(
            decision.get("mixed_intent_secondary_family")
        )
        if family is None:
            return None

        progress_snapshot = self._canonical_draft_progress_snapshot(project_id)
        if not isinstance(progress_snapshot, dict):
            return None
        report_progress = progress_snapshot.get("report_progress") or {}
        current_count = int(report_progress.get("current_count") or 0)
        threshold_count = int(progress_snapshot.get("turn_target_count") or 0)

        if priority == "P5A":
            if progress_snapshot.get("turn_target_met"):
                self._set_turn_draft_followup_flags(
                    reported_under_target=False,
                    asked_continue_expand=False,
                )
                return (
                    f"当前正文约 {current_count}/{threshold_count} 字，已达到本轮目标。"
                    f"本轮不执行“{family}”；如需{family}，请下一轮单独发起。"
                )
            self._set_turn_draft_followup_flags(
                reported_under_target=True,
                asked_continue_expand=True,
                continuation_threshold_count=progress_snapshot.get("effective_turn_target_count"),
            )
            return (
                f"当前正文约 {current_count}/{threshold_count} 字，仍未达到本轮目标。"
                f"本轮不执行“{family}”；请下一轮继续扩写正文。"
            )

        if priority == "P5B":
            self._set_turn_draft_followup_flags(
                reported_under_target=False,
                asked_continue_expand=False,
            )
            return (
                "正文修改已落盘。"
                f"本轮不执行“{family}”；如需{family}，请下一轮单独发起。"
            )

        return None

    def _immediate_canonical_draft_guidance_message(self, project_id: str) -> str | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("priority") != "P5A" or decision.get("mode") != "no_write":
            return None
        return self._build_mixed_intent_guidance_message(project_id)

    def _mixed_intent_guidance_after_canonical_mutation(self, project_id: str) -> str | None:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            return None
        if decision.get("priority") not in {"P5A", "P5B"}:
            return None
        if decision.get("mode") != "require":
            return None
        if not isinstance(self._successful_canonical_draft_mutation(), dict):
            return None
        return self._build_mixed_intent_guidance_message(project_id)

    def _finalize_early_assistant_message(
        self,
        project_id: str,
        history: List[Dict],
        current_user_message: Dict,
        assistant_message: str,
    ) -> tuple[str, Dict | None, list[SystemNotice]]:
        assistant_message = self._finalize_assistant_turn(project_id, assistant_message)
        self._persist_draft_followup_state_for_turn(
            project_id,
            assistant_message,
            user_message=str(current_user_message.get("content") or ""),
        )
        if not assistant_message.strip():
            assistant_message = "（本轮无回复）"
        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        token_usage = self._finalize_post_turn_compaction(project_id, history, None)
        system_notices = [
            SystemNotice(
                category=notice["category"],
                path=notice.get("path"),
                reason=notice["reason"],
                user_action=notice["user_action"],
                surface_to_user=notice["surface_to_user"],
            )
            for notice in self._turn_context.pop("pending_system_notices", [])
        ]
        self._turn_context = self._new_turn_context(can_write_non_plan=True)
        return assistant_message, token_usage, system_notices

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
                            surface_to_user=True,
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
                surface_to_user=True,
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
                    surface_to_user=True,
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
        surface_to_user: bool,
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

    def _should_allow_non_plan_write(self, project_id: str, user_message: str) -> bool:
        decision = self._turn_context.get("canonical_draft_decision")
        if not isinstance(decision, dict):
            decision = self._classify_canonical_draft_turn(project_id, user_message)
        if decision.get("mode") == "require":
            return True
        return self._should_allow_generic_non_plan_write(project_id, user_message)

    def _should_allow_generic_non_plan_write(self, project_id: str, user_message: str) -> bool:
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
        return self._non_plan_write_block_reason(project_id, file_path) is not None

    def _non_plan_write_block_reason(self, project_id: str, file_path: str) -> str | None:
        try:
            normalized = self.skill_engine.normalize_file_path(project_id, file_path)
        except ValueError:
            return None
        if self._is_canonical_report_draft_path(normalized):
            decision = self._turn_context.get("canonical_draft_decision")
            if isinstance(decision, dict):
                if decision.get("mode") == "require":
                    return None
                stage_code = str(decision.get("stage_code") or "")
                if stage_code not in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES:
                    return self.CANONICAL_DRAFT_STAGE_GATE_MESSAGE
                fixed_message = decision.get("fixed_message")
                if isinstance(fixed_message, str) and fixed_message.strip():
                    return fixed_message
                return "本轮用户没有要求修改正文草稿，请不要改动 `content/report_draft_v1.md`。"
            if not self._turn_context.get("can_write_non_plan", True):
                return self.CANONICAL_DRAFT_STAGE_GATE_MESSAGE
            return None
        generic_allowed = self._turn_context.get(
            "generic_non_plan_write_allowed",
            self._turn_context.get("can_write_non_plan", True),
        )
        if not normalized.startswith("plan/") and not generic_allowed:
            return self.CANONICAL_DRAFT_STAGE_GATE_MESSAGE
        return None

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
            self.skill_engine.REPORT_DRAFT_PATH,
        }
        return normalized.lower() in evidence_paths
