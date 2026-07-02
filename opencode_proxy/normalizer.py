"""OpenCode Zen SSE 规范化核心（纯函数，只依赖 stdlib）。

背景：opencode.ai/zen 在 2026-07-01→07-02 间把流式响应改成了非标准形态——
把 `usage` 挂在带 `finish_reason` 的正文块（choices 非空）上，而不是 OpenAI 规范
要求的"单独一个 choices:[] 空块"。它真正的 choices:[] 空块里装的是 opencode 私有
字段（x-opencode-type=inference-cost / 只有 cost），且在 `data: [DONE]` 之后还多发
一块。new-api 的流式取 usage 逻辑只从"空 choices 的末块"里找 usage，于是抓不到 →
回退本地估 token → cache 归 0。

本模块把 opencode 当前的畸形流**还原成 OpenAI 标准流**：usage 落在 choices:[] 空块，
丢弃 opencode 私有块与 [DONE] 之后的内容。new-api 在 2026-07-01 已被生产证明能正确
解析这种标准形态（同一 opencode base_url、同一渠道），故还原后计费自动恢复正确。

设计原则：**最小介入 + 对两种格式都安全**。若 opencode 日后修回标准格式（usage 已在
空块），本模块原样透传、绝不重复注入——是个幂等的防御性 shim。
"""
from __future__ import annotations
import json
from typing import Iterable, Iterator


def _choices_nonempty(obj: dict) -> bool:
    ch = obj.get("choices")
    return isinstance(ch, list) and len(ch) > 0


def _has_usage(obj: dict) -> bool:
    u = obj.get("usage")
    return isinstance(u, dict) and len(u) > 0


def _split_usage_chunk(obj: dict) -> tuple[dict, dict]:
    """把"usage 挂在正文块"拆成 (去掉 usage 的正文块, 标准 choices:[] usage 空块)。
    空块复用源块的 id/object/created/model，最大程度贴合上游真实末块形态。"""
    usage = obj["usage"]
    content_chunk = {k: v for k, v in obj.items() if k != "usage"}
    std_usage_chunk = {k: obj[k] for k in ("id", "object", "created", "model") if k in obj}
    std_usage_chunk["choices"] = []
    std_usage_chunk["usage"] = usage
    return content_chunk, std_usage_chunk


def normalize_objects(objs: Iterable[dict]) -> Iterator[dict]:
    """把已解析的 data JSON 对象序列（不含 [DONE]）规范化为标准顺序。

    规则（按块判定，幂等）：
    - usage + choices 非空（opencode 畸形）→ 拆成 正文块 + 标准 usage 空块。
    - usage + choices 空（已是标准末块 / opencode 若修回）→ 原样透传。
    - 无 usage + choices 空（opencode 私有块 inference-cost / cost-only）→ 丢弃。
    - 无 usage + choices 非空（普通正文增量）→ 原样透传。
    重复 usage（防御）：仅采信首个 usage；其后再出现的 usage 只当正文/丢弃，不重复注入。
    """
    usage_emitted = False
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        has_usage = _has_usage(obj)
        nonempty = _choices_nonempty(obj)
        if has_usage and nonempty:
            content_chunk, std_usage_chunk = _split_usage_chunk(obj)
            yield content_chunk
            if not usage_emitted:
                yield std_usage_chunk
                usage_emitted = True
        elif has_usage and not nonempty:
            if not usage_emitted:
                yield obj
                usage_emitted = True
            # 已发过 usage → 丢弃这个多余的空 usage 块
        elif (not has_usage) and (not nonempty):
            # opencode 私有空块（inference-cost / {"choices":[],"cost":..}）→ 丢弃
            continue
        else:
            # 普通正文增量块 → 透传
            yield obj


def normalize_sse(lines: Iterable[str]) -> Iterator[str]:
    r"""输入 = 上游 SSE 的逐行文本（不含尾随换行）；输出 = 规范化后的 SSE 帧文本
    （每帧自带 `\n\n`）。流式逐块处理、不整段缓冲；遇 `data: [DONE]` 发出后即终止，
    自然丢弃 opencode 在 [DONE] 之后多发的私有块。非 data 行（SSE 注释/keepalive）忽略。
    """
    def _obj_stream() -> Iterator[dict]:
        # 内嵌生成器：把行流转成 data 对象流，遇 [DONE] 结束。
        for raw in lines:
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                continue
            if not line.startswith("data:"):
                continue  # SSE 注释/keepalive：本层忽略（new-api 不需要）
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                return
            try:
                obj = json.loads(payload)
            except Exception:
                continue  # 无法解析的 data 行：安全丢弃（不应出现）
            if isinstance(obj, dict):
                yield obj

    for out in normalize_objects(_obj_stream()):
        yield "data: " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n\n"
    yield "data: [DONE]\n\n"
