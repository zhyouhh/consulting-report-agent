"""OpenCode Zen SSE 规范化核心（纯函数 / push 式解析，只依赖 stdlib）。

背景：opencode.ai/zen 在 2026-07-01→07-02 间把流式响应改成了非标准形态——
把 `usage` 挂在带 `finish_reason` 的正文块（choices 非空）上，而不是 OpenAI 规范
要求的"单独一个 choices:[] 空块"。它真正的 choices:[] 空块里装的是 opencode 私有
字段（x-opencode-type=inference-cost / 只有 cost），且在 `data: [DONE]` 之后还多发
一块。new-api 的流式取 usage 逻辑只从"空 choices 的末块"里找 usage，于是抓不到 →
回退本地估 token → cache 归 0。

本模块把 opencode 当前的畸形流**还原成 OpenAI 标准流**：usage 落在末尾 choices:[] 空块，
丢弃 opencode 私有块与 [DONE] 之后的内容。new-api 在 2026-07-01 已被生产证明能正确
解析这种标准形态（同一 opencode base_url、同一渠道），故还原后计费自动恢复正确。

设计要点：
- **push 式**：`_SseNormalizer.feed(line)` + `close()`，可被同步或异步调用方逐行驱动。
- **fail-closed**：宁可当"未命中/无 usage"让下游保守多计费，绝不凭空造缓存少计费。
  · usage 缺 `prompt_tokens`/`completion_tokens` → 不转正（下游走缺 usage 保守路径）。
  · 上游截断（没等到 `[DONE]`）→ 不合成 `[DONE]`，让下游感知异常。
  · 只丢弃**明确识别**的 opencode 私有块（x-opencode-type / 仅 cost）；未知对象（如
    `{"error":...}`）原样透传，绝不静默吞成功。
- **last-usage-wins**：剥掉所有正文块上的 usage，只缓存最后一个"可计费" usage，在 `[DONE]`
  前作为唯一的 choices:[] 空块发出（防 opencode 发"中间 usage + 最终 usage"时误用 partial）。
- **幂等**：opencode 若修回标准（usage 已在空块）→ 归一到同一末块形态，无重复注入。
- **多行 event**：按 SSE 规范累积多行 `data:`；单行 JSON 立即解析，跨行 JSON 累积到可解析为止。
"""
from __future__ import annotations
import json
from typing import Iterable, Iterator, Optional


def _choices_nonempty(obj: dict) -> bool:
    ch = obj.get("choices")
    return isinstance(ch, list) and len(ch) > 0


def _has_usage(obj: dict) -> bool:
    u = obj.get("usage")
    return isinstance(u, dict) and len(u) > 0


def _is_int_like(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _usage_is_billable(usage: dict) -> bool:
    """只有同时带数值型 prompt_tokens 与 completion_tokens 的 usage 才转正。
    否则不转正 → 下游（new-api 本地估算 / CRA 缺 usage 保守路径）fail-closed。"""
    return (isinstance(usage, dict)
            and _is_int_like(usage.get("prompt_tokens"))
            and _is_int_like(usage.get("completion_tokens")))


def _is_opencode_private_chunk(obj: dict) -> bool:
    """opencode 私有块：inference-cost（x-opencode-type）或仅含 cost 的空块。
    只对 choices 为空且无 usage 的块判定为可安全丢弃。"""
    return ("x-opencode-type" in obj) or ("cost" in obj)


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n\n"


class _SseNormalizer:
    """push 式 SSE 规范化器。`feed(line)`/`close()` 都返回待发出的帧字符串列表。"""

    def __init__(self) -> None:
        self._buf: list[str] = []          # 跨行 data event 累积
        self._saw_done = False
        self._pending_usage: Optional[dict] = None
        self._usage_template: dict = {}

    def _dispatch(self, obj) -> list[str]:
        if not isinstance(obj, dict):
            return []
        has_usage = _has_usage(obj)
        nonempty = _choices_nonempty(obj)
        if has_usage:
            usage = obj["usage"]
            if _usage_is_billable(usage):
                # last-usage-wins：缓存最后一个可计费 usage + 其块模板（id/model 等）
                self._pending_usage = usage
                self._usage_template = {k: obj[k] for k in ("id", "object", "created", "model")
                                        if k in obj}
            if nonempty:
                # 正文块：剥掉 usage 后透传（usage 统一到末尾空块发）
                return [_frame({k: v for k, v in obj.items() if k != "usage"})]
            # 纯 usage 空块：被 pending_usage 吸收，此处不发
            return []
        if nonempty:
            return [_frame(obj)]                       # 普通正文增量 → 透传
        if _is_opencode_private_chunk(obj):
            return []                                  # opencode 私有块 → 丢弃
        return [_frame(obj)]                           # 未知/error 对象 → 透传（不静默吞）

    def _flush_buf(self) -> list[str]:
        if not self._buf:
            return []
        joined = "\n".join(self._buf)
        self._buf = []
        try:
            obj = json.loads(joined)
        except Exception:
            return []                                  # 不可解析的残片 → 安全丢弃
        return self._dispatch(obj)

    def feed(self, line) -> list[str]:
        if line is None or self._saw_done:
            return []                                  # [DONE] 之后一律忽略（含 opencode 后置私有块）
        s = line.strip()
        if s == "":
            return self._flush_buf()                   # 空行 = SSE event 边界 → 结算累积
        if not s.startswith("data:"):
            return []                                  # 注释/其它 SSE 字段（event:/id:）忽略
        payload = s[len("data:"):].lstrip()
        if payload == "[DONE]":
            self._saw_done = True
            return []
        self._buf.append(payload)
        joined = "\n".join(self._buf)
        try:
            obj = json.loads(joined)
        except Exception:
            return []                                  # 可能是跨行 JSON 续行，继续累积
        self._buf = []
        return self._dispatch(obj)

    def close(self) -> list[str]:
        frames = self._flush_buf()
        if self._pending_usage is not None:
            chunk = dict(self._usage_template)
            chunk["choices"] = []
            chunk["usage"] = self._pending_usage
            frames.append(_frame(chunk))
        if self._saw_done:
            frames.append("data: [DONE]\n\n")          # 仅在确实收到上游 [DONE] 时才补发
        return frames


def normalize_sse(lines: Iterable[str]) -> Iterator[str]:
    r"""同步便捷封装：吃逐行文本、吐规范化后的 SSE 帧（每帧自带 `\n\n`）。
    异步调用方请直接用 `_SseNormalizer.feed()/close()` 驱动。"""
    n = _SseNormalizer()
    for line in lines:
        for f in n.feed(line):
            yield f
    for f in n.close():
        yield f
