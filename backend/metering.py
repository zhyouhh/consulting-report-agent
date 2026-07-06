# backend/metering.py
"""中央计费叶子模块（只依赖 accounts/config；绝不 import chat/skill/main/independent_review）。"""
from __future__ import annotations
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from backend.config import FALLBACK_MODEL_PRICING

_SHANGHAI_TZ = timezone(timedelta(hours=8))


def today_shanghai() -> str:
    """配额日界（spec §6.3）：Asia/Shanghai 的 YYYY-MM-DD。UTC+8 固定偏移（中国无夏令时）。"""
    return datetime.now(timezone.utc).astimezone(_SHANGHAI_TZ).strftime("%Y-%m-%d")


@dataclass
class BillingUsage:
    hit: int
    miss: int
    completion: int


# 任何真实单次调用的 token 量级上界（10 亿，远超任何模型上下文，但远低于 float 计价/SQLite INTEGER 溢出点）。
# 超出即视为畸形 usage → fail-closed。
_MAX_PLAUSIBLE_TOKENS = 10 ** 9


def _usage_get(usage, key: str):
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


class _BadUsage(Exception):
    """单个 usage token 值 present-but-malformed（→ 整条 usage fail-closed）。"""


def _coerce_token(value) -> int:
    """单个 usage token 值规整为 >=0 整数。None（字段缺省）→ 0。
    present-but-malformed 一律 raise _BadUsage（→ fail-closed，绝不静默归零）：
    非 int/float（""/[]/{} 等）、bool（不是 token 计数）、inf/nan、负数、超真实量级。"""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _BadUsage
    if isinstance(value, float) and not math.isfinite(value):
        raise _BadUsage
    v = int(value)
    if v < 0 or v > _MAX_PLAUSIBLE_TOKENS:
        raise _BadUsage
    return v


def extract_billing_usage(usage) -> BillingUsage | None:
    """从 provider usage（对象或 dict）取三档计费 token。无法识别 token 字段、或任何字段
    present-but-malformed → 返回 None（→ fail-closed 保守封顶 + 缺失计数 +1）。"""
    if usage is None:
        return None
    prompt = _usage_get(usage, "prompt_tokens")
    completion = _usage_get(usage, "completion_tokens")
    hit = _usage_get(usage, "prompt_cache_hit_tokens")
    miss = _usage_get(usage, "prompt_cache_miss_tokens")
    if prompt is None and completion is None and hit is None and miss is None:
        return None
    # 防御性 fail-closed：present-but-malformed（非数值/bool/inf/nan/负/超量级）→ None，
    # 不静默归零——归零会假记 0 费用并复位缺失计数、绕过暂停保护。仅真正缺省(None)字段安全默认 0。
    try:
        hit_v = _coerce_token(hit)
        if miss is None:
            miss_v = max(_coerce_token(prompt) - hit_v, 0)  # 派生 miss：hit>prompt 的 provider 怪值钳 0
        else:
            miss_v = _coerce_token(miss)
        completion_v = _coerce_token(completion)
    except _BadUsage:
        return None
    return BillingUsage(hit=hit_v, miss=miss_v, completion=completion_v)


def price_micro_yuan(model: str, hit: int, miss: int, completion: int, pricing: dict) -> int:
    """token×(元/百万token)=微元；单价表缺该模型时用 FALLBACK_MODEL_PRICING 保守计价。"""
    p_hit, p_miss, p_out = pricing.get(model, FALLBACK_MODEL_PRICING)
    return round(hit * p_hit + miss * p_miss + completion * p_out)


# ── fail-closed 请求感知估算（2026-07-06）───────────────────────────────────────
# 背景：流在 usage 块到达前中断（用户点停止/手机切后台断 SSE/瞬态断流重试关旧流）时无法拿到真实
# usage。原实现按「模型上下文上限」（deepseek-v4-pro=256k）全额计 miss——每次 ¥0.768，实测 07-06
# 单日 7 次中断记了 ¥5.6 幽灵账（占当日 42%）。改为按本次请求实际内容估 token 上界：仍 fail-closed
# （字符系数取保守上界 + 15% margin，杜绝「中断逃单」），但典型请求从 256k 降到真实量级。
_CJK_CHAR_RE = re.compile("[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]")
_ASCII_CHAR_RE = re.compile("[\x00-\x7f]")
_ESTIMATE_MARGIN = 1.15          # 估算余量：覆盖 tokenizer 波动
_ESTIMATE_BASE_TOKENS = 2_000    # 每请求固定余量（消息结构开销）


def _estimate_text_tokens(text: str) -> int:
    """字符→token 三档保守系数：CJK 1/字（deepseek 实测 ~0.6）、ASCII 0.5/字符（散文实测 ~0.3；
    base64/代码等高密度形态实测 ~0.7，经 1.15 margin 后仍可能略低——可接受：中断者「逃掉」的差价
    远小于其自身配额消耗，经济上无利可图）、其余字符（emoji/其他文字）2/字符（emoji 实测 1-3）。
    诚实定性：这是「贴近真实、偏保守」的近似上界，不是任意 Unicode 的严格上界；严格上界 = 旧 256k
    封顶，对正常用户是数倍幽灵账（2026-07-06 实测占当日 42%），取舍偏向典型用户。"""
    cjk = len(_CJK_CHAR_RE.findall(text))
    ascii_n = len(_ASCII_CHAR_RE.findall(text))
    other = max(len(text) - cjk - ascii_n, 0)
    return cjk + math.ceil(ascii_n / 2) + other * 2


def estimate_request_tokens_upper_bound(request_kwargs) -> int | None:
    """按 create(**kwargs) 的 messages+tools 估算 prompt token 上界。

    返回 None = 无法可靠估算（无 messages / 消息含非文本 part（如 image_url）/ 序列化失败），
    调用方回落到模型上下文上限封顶。刻意只认识「纯 dict + str content」的 provider message
    形态（chat.py/_to_provider_message 的产物），其余 fail-open 到旧 ceiling——宁可多计不可少计。"""
    if not isinstance(request_kwargs, dict):
        return None
    messages = request_kwargs.get("messages")
    if not isinstance(messages, (list, tuple)) or not messages:
        return None
    total = 0
    try:
        for message in messages:
            if not isinstance(message, dict):
                return None
            content = message.get("content")
            if isinstance(content, str):
                total += _estimate_text_tokens(content)
            elif content is not None:
                return None   # 多模态/结构化 content → 交给模型 ceiling（视觉模型有显式锚）
            extras = {k: v for k, v in message.items() if k != "content"}
            if extras:
                total += _estimate_text_tokens(json.dumps(extras, ensure_ascii=False, default=str))
        tools = request_kwargs.get("tools")
        if tools is not None:
            total += _estimate_text_tokens(json.dumps(tools, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None
    return int(total * _ESTIMATE_MARGIN) + _ESTIMATE_BASE_TOKENS


def _field(obj, key):
    """对象属性 / dict 键双形态取值（provider chunk 在 SDK 里是对象、测试里常是 dict）。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _delta_text_len(part) -> int:
    """单个 choice 的 delta/message 里已产出的文本量（content + reasoning_content +
    tool_calls 的 name/arguments 字符串）。全程防御：任何异常形态计 0，绝不抛。"""
    n = 0
    for attr in ("content", "reasoning_content"):
        v = _field(part, attr)
        if isinstance(v, str):
            n += len(v)
    tool_calls = _field(part, "tool_calls")
    if isinstance(tool_calls, (list, tuple)):
        for tc in tool_calls:
            fn = _field(tc, "function")
            if fn is None:
                continue
            for attr in ("name", "arguments"):
                v = _field(fn, attr)
                if isinstance(v, str):
                    n += len(v)
    return n


def chunk_completion_chars(chunk) -> int:
    """流式 chunk 中本次新增的 completion 字符数（fail-closed 时据此补计已流出的输出）。"""
    try:
        choices = _field(chunk, "choices")
        if not isinstance(choices, (list, tuple)):
            return 0
        return sum(_delta_text_len(_field(c, "delta")) for c in choices)
    except Exception:
        return 0


def response_completion_chars(response) -> int:
    """非流式 response 的 completion 字符数（provider 返回完整回复但缺 usage 时补计输出）。"""
    try:
        choices = _field(response, "choices")
        if not isinstance(choices, (list, tuple)):
            return 0
        return sum(_delta_text_len(_field(c, "message")) for c in choices)
    except Exception:
        return 0


import logging
import sys
import threading
from backend import accounts

logger = logging.getLogger(__name__)
from backend.config import (DEFAULT_MANAGED_MODEL_PRICING, MAX_CONSECUTIVE_USAGE_MISS,
                            MANAGED_FAILCLOSED_CEILING)
from backend.context_policy import resolve_context_policy   # fail-closed 取该模型 effective 上下文上限


class QuotaExceededError(Exception):
    """今日 managed 额度已用尽。"""
    def __init__(self, used_micro: int, cap_micro: int):
        self.used_micro = used_micro
        self.cap_micro = cap_micro
        super().__init__(f"quota exceeded: {used_micro}/{cap_micro} micro-yuan")


class ModelPausedError(Exception):
    """连续 usage 缺失，暂停该 (uid, model) managed 调用。"""
    def __init__(self, model: str):
        self.model = model
        super().__init__(f"model paused after repeated missing usage: {model}")


# per-(uid, model, day) 连续缺失计数（进程级；单进程部署足够）。
# ✦ day 入键（Codex BLOCKER）：一旦暂停，reserve 在任何成功 settle 之前就拦截 → 同键永不清零；
#   day 入键则「次日 0 点自动清零」天然成立（新 day = 新键 = 计数 0），无需依赖一次成功 settle。
_miss_counter_lock = threading.Lock()
_miss_counter: dict[tuple[str, str, str], int] = {}


def _bump_miss(uid: str, model: str, day: str) -> int:
    with _miss_counter_lock:
        n = _miss_counter.get((uid, model, day), 0) + 1
        _miss_counter[(uid, model, day)] = n
        return n


def _reset_miss(uid: str, model: str, day: str) -> None:
    with _miss_counter_lock:
        _miss_counter.pop((uid, model, day), None)


class MeteredCompletions:
    def __init__(self, parent: "MeteredManagedClient"):
        self._parent = parent
        self._raw = parent._raw.chat.completions

    def _raw_calls(self):  # 测试访问器
        return getattr(self._raw, "calls", [])

    def create(self, **kwargs):
        return self._parent._create(self._raw, **kwargs)


class _MeteredChat:
    def __init__(self, parent): self.completions = MeteredCompletions(parent)


class MeteredManagedClient:
    """managed 调用唯一出口：reserve→call→settle，usage 缺失 fail-closed。
    暴露 .chat.completions.create(**kwargs) 与 OpenAI 接口同形，调用点零改动。"""
    def __init__(self, raw_client, uid: str, model_pricing: dict | None = None):
        self._raw = raw_client
        self.uid = uid
        self._pricing = model_pricing or DEFAULT_MANAGED_MODEL_PRICING
        self.chat = _MeteredChat(self)

    def __getattr__(self, name):
        # ✦ NIT：非 chat.completions.create 的属性（如 .responses 原生搜索）透传裸 client，
        # 避免包裹破坏其它调用面。仅 managed 模式才包裹，故 .responses 计费缺口不在 B2 scope（见 cutover 已知限制）。
        raw = self.__dict__.get("_raw")
        if raw is None:
            raise AttributeError(name)
        return getattr(raw, name)

    # --- 门禁 ---
    def _reserve(self, model: str):
        day = today_shanghai()
        cap = accounts.get_effective_daily_cap_micro(self.uid)
        used = accounts.get_usage_today(self.uid, day)["cost_micro_yuan"]
        if used >= cap:
            raise QuotaExceededError(used, cap)
        with _miss_counter_lock:
            paused = _miss_counter.get((self.uid, model, day), 0) >= MAX_CONSECUTIVE_USAGE_MISS
        if paused:
            raise ModelPausedError(model)

    # --- 累计 ---
    def _settle(self, model: str, usage, request_kwargs=None, bump_pause=True, completion_chars=0) -> None:
        # 计费是尽力而为的成本护栏：结算（SQLite 写等）失败只记日志、绝不向调用方抛——否则会破坏用户的
        # 聊天/摘要/审查操作（尤其 _summarize_messages 的宽 except 会把它静默吞成「摘要失败」→ 既不计费又不可观测）。
        # DB 写失败时无论如何都记不上这一次账；至少让它可观测（频繁出现可据日志排查）。stream/非流式语义一致。
        try:
            day = today_shanghai()
            bu = extract_billing_usage(usage)
            if bu is None:
                # fail-closed：优先按本次请求内容估 token 上界（2026-07-06——中断流按 256k 封顶
                # 会记出数倍幽灵账并污染命中率统计）；无法估算（多模态/形态异常）才回落
                # 「显式锚 or 模型 effective 上下文上限」（视觉模型不在 context_policy EXACT tier → 显式锚；
                # resolve_context_policy 对未知模型走 UNKNOWN_FALLBACK_TIER、不抛）。
                # 估算值恒 clamp 到 ceiling（估算只会更省，绝不比旧封顶更贵）。
                ceiling = (MANAGED_FAILCLOSED_CEILING.get(model)
                           or resolve_context_policy(model).effective_context_limit)
                estimate = estimate_request_tokens_upper_bound(request_kwargs)
                prompt_billed = min(estimate, ceiling) if estimate is not None else ceiling
                # 已流出的 completion（中断前用户实际看到的输出）按 1 token/字符 ×margin 上界补计，
                # 走输出价——只按 prompt 估算会漏掉「短 prompt + 长输出后断流」的输出成本（Codex BLOCKER）。
                completion_billed = math.ceil(max(int(completion_chars or 0), 0) * _ESTIMATE_MARGIN)
                p_hit, p_miss, p_out = self._pricing.get(model, FALLBACK_MODEL_PRICING)
                cost = round(prompt_billed * p_miss + completion_billed * p_out)
                billed = prompt_billed + completion_billed
                # failclosed 独立列：不进 cache_miss——幽灵 miss 会把管理面板命中率打烂（07-06 实测 -16pp）。
                accounts.add_usage(self.uid, day, cost, 0, 0, 0, failclosed=billed)
                # 暂停计数只针对「provider 真没报 usage」（provider 异常/自然结束无 usage）；
                # 消费方主动关流（用户停止/断连/瞬态重试关旧流 → GeneratorExit）不是 provider 异常，
                # 不计数——否则手机切后台 3 次就把该用户当日模型锁死。
                if bump_pause:
                    _bump_miss(self.uid, model, day)
                logger.warning(
                    "[metering] fail-closed settle: uid=%s model=%s billed_tokens=%d (prompt_est=%s completion_est=%d ceiling=%d bump_pause=%s)",
                    self.uid, model, billed, estimate, completion_billed, ceiling, bump_pause)
                return
            cost = price_micro_yuan(model, bu.hit, bu.miss, bu.completion, self._pricing)
            accounts.add_usage(self.uid, day, cost, bu.hit, bu.miss, bu.completion)
            _reset_miss(self.uid, model, day)
        except Exception:
            logger.warning("metered settle failed (uid=%s model=%s) — call left unbilled",
                           self.uid, model, exc_info=True)

    def _create(self, raw_completions, **kwargs):
        model = kwargs.get("model", "")
        self._reserve(model)
        if kwargs.get("stream"):
            raw_stream = raw_completions.create(**kwargs)
            return self._metered_stream(model, raw_stream, kwargs)
        response = raw_completions.create(**kwargs)
        self._settle(model, getattr(response, "usage", None), request_kwargs=kwargs,
                     completion_chars=response_completion_chars(response))
        return response

    def _metered_stream(self, model: str, raw_stream, request_kwargs=None):
        """透传每个 chunk + 记最后带 usage 的 chunk。无论**自然结束 / provider 异常 / 消费方中断
        (GeneratorExit)**，`finally` 都结算恰好一次：last_usage 缺失 → fail-closed 按请求估算计费。
        ✦ Codex BLOCKER（R1+R2）：① provider 中途抛不得漏计；② **消费方在处理已 yield 的 chunk 时抛异常，
        经 gen.close()→GeneratorExit 到达此处**——与「用户主动放弃」无法从生成器内区分，故 spec §6.3
        「流式中断 + usage 缺失 = fail-closed」对 GeneratorExit 也一律 fail-closed（不留漏计后门）；
        但 GeneratorExit 不计入暂停计数（消费方关流 ≠ provider 不报 usage，见 _settle 注释）。"""
        last_usage = None
        completion_chars = 0
        try:
            for chunk in raw_stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    last_usage = u
                completion_chars += chunk_completion_chars(chunk)   # 中断时据此补计已流出的输出
                yield chunk
        finally:
            # `_settle` 自身已 best-effort（内部吞 DB 写等失败、只记日志、不抛），故正常情况下这里不会捕到异常。
            # 下面的 pending / except 是**防御性**：万一 `_settle` 因意外（如被测试 monkeypatch、或 logger 异常）抛出，
            # 也不得遮蔽正在 unwinding 的原始异常（provider 抛 / GeneratorExit / 消费方异常）；close 用独立内层
            # finally 包住，保证底层 provider 流无论如何都被关闭。
            pending = sys.exc_info()[1]
            try:
                # 自然结束 / provider 异常 / GeneratorExit 都恰好结算一次
                self._settle(model, last_usage, request_kwargs=request_kwargs,
                             bump_pause=not isinstance(pending, GeneratorExit),
                             completion_chars=completion_chars)
            except Exception:
                if pending is None:
                    raise
                logger.warning("metered stream settle failed while unwinding %r", pending, exc_info=True)
            finally:
                close = getattr(raw_stream, "close", None)   # 释放底层 provider 流（HTTP 连接）
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass


def wrap_client_for_billing(raw_client, uid: str, settings):
    """managed → MeteredManagedClient（计费）；custom → 原样返回（不计费）。
    settings.mode 在 ChatHandler/Review 构造时已定（per-handler 固定）。"""
    if getattr(settings, "mode", "managed") == "managed":
        return MeteredManagedClient(raw_client, uid=uid)
    return raw_client
