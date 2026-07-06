"""Provider 调用瞬态错误分类与退避（2026-07-06）。

叶子模块：只依赖 stdlib，供 chat.py / independent_review.py 共享——绝不 import
chat / skill / main / metering（计费异常由调用方在本分类器之前单独截获）。

背景：此前模型调用失败几乎直接把「API调用失败」抛给用户——初次请求只有一次固定
2s 重试（不分类错误），流中途断开（含「无首包」读超时）零重试。成熟产品
（OpenAI/Anthropic SDK、Claude Code 等）的通用做法：对瞬态错误（连接失败/超时/
429/5xx）做 2-3 次指数退避重试，并向用户反馈「正在重试」；对确定性客户端错误
（4xx）立即失败不浪费等待。
"""
from __future__ import annotations

# 值得重试的 HTTP 状态码：请求超时/太多请求/服务端错误/网关超时（含 Cloudflare 522/524）。
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

# 初次 create 请求的总尝试次数（1 次原始 + 2 次重试）。
CREATE_MAX_ATTEMPTS = 3
# 流中途断开的每轮对话（turn）重试总预算：只在「尚无任何用户可见输出」时使用。
STREAM_MAX_RETRIES = 3
# 指数退避基数与上限（秒）。
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 8.0


def is_retryable_provider_error(error: BaseException) -> bool:
    """瞬态错误判定。

    - 带 HTTP 状态码（openai.APIStatusError 的 ``status_code``）：仅 408/425/429/5xx
      算瞬态；其余 4xx 是确定性客户端错误（参数/鉴权/内容问题），重试只会重复失败。
    - 不带状态码：连接失败/DNS/读写超时/流中途断开（openai.APIConnectionError、
      httpx 超时、socket 错误等都在拿到 HTTP 响应前或流中途炸），一律按瞬态处理——
      与主流 SDK 的默认分类一致（未知网络层错误宁可有界重试，也不直接把偶发抖动
      变成用户可见失败）。
    """
    status = getattr(error, "status_code", None)
    if isinstance(status, bool):  # bool 是 int 子类，防误配
        return True
    if isinstance(status, int):
        return status in TRANSIENT_STATUS_CODES
    return True


def backoff_seconds(attempt: int) -> float:
    """第 ``attempt`` 次重试（1-based）前的等待秒数：2s → 4s → 8s（封顶）。"""
    if attempt < 1:
        attempt = 1
    return min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)


def retry_status_text(attempt: int, max_attempts: int) -> str:
    """展示给用户的重试状态行（反馈引导行动：让用户知道系统在自动处理、无需操作）。"""
    return f"（连接不稳定，正在自动重试 第 {attempt}/{max_attempts} 次…）"
