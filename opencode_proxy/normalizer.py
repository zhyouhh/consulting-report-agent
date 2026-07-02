"""OpenCode Zen SSE 规范化核心（自建字节级 SSE 组帧 + push 式规范化，只依赖 stdlib）。

背景：opencode.ai/zen 在 2026-07-01→07-02 间把流式响应改成了非标准形态——
把 `usage` 挂在带 `finish_reason` 的正文块（choices 非空）上，而不是 OpenAI 规范
要求的"单独一个 choices:[] 空块"；其真正的空块里是 opencode 私有字段
（x-opencode-type=inference-cost / 只有 cost），且在 `data: [DONE]` 之后还多发一块。
new-api 的流式取 usage 逻辑只从"空 choices 的末块"里找 usage，于是抓不到 → 回退本地
估 token → cache 归 0，下游按最贵档计费。

本模块把 opencode 畸形流**还原成 OpenAI 标准流**：usage 落在末尾 choices:[] 空块，丢弃
私有块与 [DONE] 之后内容。new-api 在 2026-07-01 已被生产证明能正确解析这种标准形态。

**为什么自己做 SSE 组帧（`_SseEventFramer`）而不用 requests/httpx 的 `iter_lines`**：
二者按 `str.splitlines()` 语义切行，会在 ` / //\v/\f` 等 Unicode 行边界
字符处切——而这些字符可合法出现在 JSON 字符串（模型正文）里，导致一条 `data:` 的 JSON 被
从中间切成两行、解析失败。本组帧只按 `\r`/`\n`/`\r\n` 切行、按空行分事件，` ` 等留在
data 值内不触发切分（实测 httpx 也会误切，故必须自建）。

规范化 fail-closed 优先（宁可当"无 usage"让下游保守多计费，绝不促成少计费）：
- **usage 候选严格化**：只接受**终态块**（choices 空，或非空但所有 choice 带 finish_reason）上的
  usage；正文增量块上的 usage 快照不作候选。多个终态 usage 取**最后一个**，且仅当它本身可计费
  （prompt/completion 为整数、非负、有限；cache hit/miss 若在须合法且不超 prompt）才发出。
- **见 [DONE] 立即定稿**：收到上游 `[DONE]` 当场发 usage（若可计费）+ `[DONE]`，不等 EOF。
- **截断 / 畸形一律 fail-closed**：未见 `[DONE]`（EOF 截断），或出现无法解析的 data 事件，一律不发
  usage、不发 `[DONE]`。
- **不吞错误**：只丢弃明确识别的 opencode 私有块；未知 / `{"error":...}` 对象原样透传。
"""
from __future__ import annotations
import codecs
import json
import math
from typing import Iterable, Iterator, List, Optional

_PRIVATE_CHUNK_ALLOWED_KEYS = frozenset({"choices", "cost", "normalizedUsage", "x-opencode-type"})


# ----------------------------- 字节级 SSE 组帧 -----------------------------

class _SseEventFramer:
    """把原始字节流按 SSE 规范组帧，产出每个事件**完整的 data 值**（多行 data 以 `\\n` 连接）。
    只按 `\\r`/`\\n`/`\\r\\n` 切行、按空行分事件——绝不在 `\\u2028` 等 Unicode 行边界字符处切。"""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._acc = ""            # 已规范换行、尚未成完整事件的文本
        self._pending_cr = False  # 上一 chunk 末尾悬挂的 '\r'（可能是跨 chunk 的 '\r\n'）

    def _absorb(self, text: str) -> None:
        if self._pending_cr:
            text = "\r" + text
            self._pending_cr = False
        if text.endswith("\r"):
            self._pending_cr = True
            text = text[:-1]
        self._acc += text.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _extract_data(event_text: str) -> Optional[str]:
        parts: List[str] = []
        for line in event_text.split("\n"):
            if line.startswith(":"):
                continue                       # 注释行
            if line.startswith("data:"):
                v = line[len("data:"):]
                if v.startswith(" "):
                    v = v[1:]                  # SSE 规范：去掉一个前导空格
                parts.append(v)
            # 其它字段（event:/id:/retry:）忽略
        if not parts:
            return None
        joined = "\n".join(parts)
        return joined if joined.strip() != "" else None

    def feed_bytes(self, chunk: bytes) -> List[str]:
        self._absorb(self._decoder.decode(chunk))
        out: List[str] = []
        while "\n\n" in self._acc:
            event_text, self._acc = self._acc.split("\n\n", 1)
            data = self._extract_data(event_text)
            if data is not None:
                out.append(data)
        return out

    def close(self) -> List[str]:
        self._absorb(self._decoder.decode(b"", final=True))
        self._pending_cr = False
        out: List[str] = []
        # 剩余可能是没有结尾空行的最后一个事件
        tail = self._acc.strip("\n")
        self._acc = ""
        if tail:
            data = self._extract_data(tail)
            if data is not None:
                out.append(data)
        return out


# ------------------------------- 规范化判定 -------------------------------

def _choices_nonempty(obj: dict) -> bool:
    ch = obj.get("choices")
    return isinstance(ch, list) and len(ch) > 0


def _has_usage(obj: dict) -> bool:
    u = obj.get("usage")
    return isinstance(u, dict) and len(u) > 0


def _is_terminal_block(obj: dict) -> bool:
    """终态块：choices 缺失/非 list（裸 usage 块）、choices 为空、或非空但每个 choice 都带 finish_reason。
    正文增量块（无 finish_reason）非终态——其 usage 快照不作候选。"""
    ch = obj.get("choices")
    if not isinstance(ch, list):
        return True
    if len(ch) == 0:
        return True
    return all(isinstance(c, dict) and c.get("finish_reason") is not None for c in ch)


def _is_token_count(v) -> bool:
    """合法 token 计数：非 bool、有限、非负、整数值（拒 nan/inf/负/小数）。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v >= 0
    if isinstance(v, float):
        return math.isfinite(v) and v >= 0 and v.is_integer()
    return False


def _usage_is_billable(usage) -> bool:
    """可计费 usage：prompt_tokens 与 completion_tokens 均为合法计数；cache hit/miss 若存在须
    合法且不超过 prompt_tokens（防 hit>prompt 之类怪值被下游按低价结算）。否则 fail-closed 不转正。"""
    if not isinstance(usage, dict):
        return False
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    if not (_is_token_count(pt) and _is_token_count(ct)):
        return False
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        v = usage.get(key)
        if v is not None and (not _is_token_count(v) or v > pt):
            return False
    return True


def _is_opencode_private_chunk(obj: dict) -> bool:
    """opencode 私有成本块：inference-cost（x-opencode-type）或仅含 cost 的空块。
    仅在 choices 为空且无 usage 时调用。带 `error` 等集外键 → 不判私有（透传，绝不吞错误）。"""
    if "error" in obj:
        return False
    if "x-opencode-type" in obj:
        return True
    return "cost" in obj and set(obj.keys()) <= _PRIVATE_CHUNK_ALLOWED_KEYS


def _frame(obj: dict) -> str:
    # ensure_ascii=True：把所有非 ASCII（含 / //  等会被下游行解析器
    # 误切的 Unicode 行边界字符）转义成 \uXXXX，杜绝把"正文里的行边界字符切断 JSON"这个问题传给
    # 下游 new-api。内部跳转体积略增（免费），内容经 json 往返无损。
    return "data: " + json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n\n"


class _SseNormalizer:
    """push 式规范化器：`feed_event(data)` 吃一个**完整事件的 data 值**（JSON 文本或 `[DONE]`），
    `close()` 收尾；`done` 在收到 `[DONE]` 后为真，调用方可据此停读并关上游。"""

    def __init__(self) -> None:
        self._done = False
        self._finalized = False
        self._malformed = False
        self._last_usage: Optional[dict] = None
        self._last_usage_template: dict = {}

    @property
    def done(self) -> bool:
        return self._done

    def _dispatch(self, obj) -> List[str]:
        if not isinstance(obj, dict):
            self._malformed = True             # 非对象 JSON（数组/数字等）→ 可疑
            return []
        if "usage" in obj:
            if _has_usage(obj) and _is_terminal_block(obj):
                self._last_usage = obj["usage"]      # 最后一个终态块 usage 胜出（可计费判定推迟到 finalize）
                self._last_usage_template = {k: obj[k] for k in ("id", "object", "created", "model")
                                             if k in obj}
            if _choices_nonempty(obj):
                return [_frame({k: v for k, v in obj.items() if k != "usage"})]
            return []
        if _choices_nonempty(obj):
            return [_frame(obj)]               # 普通正文增量 → 透传
        if _is_opencode_private_chunk(obj):
            return []                          # opencode 私有块 → 丢弃
        return [_frame(obj)]                   # 未知/error 对象 → 透传（不静默吞）

    def _finalize(self) -> List[str]:
        if self._finalized:
            return []
        self._finalized = True
        if self._malformed:
            return []                          # fail-closed：畸形流不发 usage、不发 [DONE]
        frames: List[str] = []
        if _usage_is_billable(self._last_usage):
            chunk = dict(self._last_usage_template)
            chunk["choices"] = []
            chunk["usage"] = self._last_usage
            frames.append(_frame(chunk))
        frames.append("data: [DONE]\n\n")
        return frames

    def feed_event(self, data: str) -> List[str]:
        if self._done:
            return []                          # [DONE] 之后一律忽略
        if data == "[DONE]":
            self._done = True
            return self._finalize()
        try:
            obj = json.loads(data)
        except Exception:
            self._malformed = True             # 无法解析的 data 事件 → fail-closed
            return []
        return self._dispatch(obj)

    def close(self) -> List[str]:
        """EOF。若已因 [DONE] 收尾 → 无。否则为**截断**：不发 usage、不发 [DONE]（fail-closed）。"""
        return []


# ------------------------------- 便捷封装（测试用） -------------------------------

def normalize_sse_bytes(chunks: Iterable[bytes]) -> Iterator[str]:
    """吃原始字节 chunk 流，吐规范化后的 SSE 帧文本。异步调用方请直接用
    `_SseEventFramer` + `_SseNormalizer` 驱动，并在 `.done` 后停读关上游。"""
    framer = _SseEventFramer()
    n = _SseNormalizer()
    for chunk in chunks:
        for data in framer.feed_bytes(chunk):
            for f in n.feed_event(data):
                yield f
            if n.done:
                return
    for data in framer.close():
        for f in n.feed_event(data):
            yield f
        if n.done:
            return
    for f in n.close():
        yield f


def normalize_sse_text(text: str) -> List[str]:
    """吃完整 SSE 文本（测试便捷）→ 规范化帧列表。"""
    return list(normalize_sse_bytes([text.encode("utf-8")]))
