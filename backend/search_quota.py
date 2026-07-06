"""搜索池用量记账 + 额度报告装配（叶子模块）。

依赖边界：只依赖 accounts / metering / config（类型）+ requests/stdlib，
绝不 import chat / skill / main / independent_review。

三类数据源，按 provider 各取所能（数据质量在报告里诚实标注 source）：
- tavily  → 官方 GET /usage 实时真值（用普通搜索 key 即可查，5 分钟 TTL 缓存；
  plan_usage/plan_limit 是账号级字段，多 key 同账号会重复计数 → 按指纹元组去重）
- brave   → 搜索响应头 X-RateLimit-* 被动快照（成功与 4xx/5xx 响应都观测——429 时
  headers 恰恰带着 remaining=0 的关键信号）
- serper/exa → 无任何查询手段，只能本地记账估算（总额静态声明在 managed_search_pool.json
  的 quota 块；估算不含其它部署的消耗，剩余偏乐观——展示层须标注）

两条硬约束：
- 记账绝不阻塞搜索路径：搜索侧只 enqueue（有界队列，满即丢 + 日志），落库在
  单一后台 daemon 线程；SQLite busy 等待再久也只卡 worker 不卡搜索。
- key 身份 = sha256 指纹前 12 位（非机密、不可逆、跨配置重排/换 key 稳定），
  绝不用列表下标当持久身份（重排 key 会把旧账记到新 key 头上），
  也绝不在 API 响应里回显 key 的任何原文片段（含尾 4 位）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import requests

from . import accounts, metering
from .config import ManagedSearchPoolConfig, ManagedSearchProviderConfig

logger = logging.getLogger(__name__)

TAVILY_USAGE_ENDPOINT = "https://api.tavily.com/usage"
TAVILY_USAGE_TIMEOUT_SECONDS = 6
TAVILY_USAGE_CACHE_TTL_SECONDS = 300

# 趋势/统计窗口：31 天保证「本月至今」子窗口完整（31 日大月的月初仍在窗内）；
# 对外口径「近 30 日」的统计另用 30 天截点单独累计，不混用。
_HISTORY_WINDOW_DAYS = 31
_STATS_WINDOW_DAYS = 30

UNKNOWN_KEY_ID = "unknown"

_tavily_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_tavily_cache_lock = threading.Lock()


def key_fingerprint(api_key: str) -> str:
    """key 的持久非机密身份：sha256 前 12 hex。空 key → UNKNOWN_KEY_ID。"""
    raw = str(api_key or "")
    if not raw:
        return UNKNOWN_KEY_ID
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ── 记账（搜索侧 enqueue，后台线程落库）─────────────────────────────────────

_USAGE_QUEUE_MAX = 512
_usage_queue: queue.Queue = queue.Queue(maxsize=_USAGE_QUEUE_MAX)
_usage_worker_lock = threading.Lock()
_usage_worker: threading.Thread | None = None


def _snapshot_config_key(provider: str, key_id: str) -> str:
    return f"search_quota_snapshot:{provider}:{key_id}"


def record_search_usage(
    *,
    provider: str,
    key_id: str | None,
    calls: int,
    units: float,
    errors: int,
    quota_snapshot: dict[str, Any] | None = None,
) -> None:
    """同步落库（后台 worker 与测试用）：任何异常只打日志绝不上抛。"""
    try:
        day = metering.today_shanghai()
        kid = str(key_id) if key_id else UNKNOWN_KEY_ID
        accounts.add_search_usage(provider, kid, day, calls=calls, units=units, errors=errors)
        if quota_snapshot:
            accounts.set_config(
                _snapshot_config_key(provider, kid),
                json.dumps(quota_snapshot, ensure_ascii=False),
            )
    except Exception:
        logger.warning("[search-usage] 记账失败（忽略，不影响搜索）", exc_info=True)


def _usage_worker_loop() -> None:
    while True:
        item = _usage_queue.get()
        try:
            record_search_usage(**item)
        except Exception:
            # record_search_usage 自身已兜底；这里只防御 kwargs 形状意外，worker 永不退出
            logger.warning("[search-usage] worker 处理异常（忽略）", exc_info=True)
        finally:
            _usage_queue.task_done()


def _ensure_usage_worker() -> None:
    global _usage_worker
    if _usage_worker is not None and _usage_worker.is_alive():
        return
    with _usage_worker_lock:
        if _usage_worker is not None and _usage_worker.is_alive():
            return
        _usage_worker = threading.Thread(
            target=_usage_worker_loop, name="search-usage-recorder", daemon=True
        )
        _usage_worker.start()


def enqueue_search_usage(**kwargs) -> None:
    """搜索路径的记账入口：非阻塞 enqueue，队列满直接丢（丢监控数据 > 卡用户搜索）。"""
    _ensure_usage_worker()
    try:
        _usage_queue.put_nowait(kwargs)
    except queue.Full:
        logger.warning("[search-usage] 记账队列已满，丢弃一条（provider=%s）", kwargs.get("provider"))


def wait_for_usage_idle(timeout: float = 5.0) -> bool:
    """等后台记账清空（测试/优雅收尾用）；超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _usage_queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _usage_queue.unfinished_tasks == 0


# ── tavily 实时用量 ─────────────────────────────────────────────────────────


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fetch_tavily_key_usage(api_key: str) -> dict[str, Any]:
    try:
        response = requests.get(
            TAVILY_USAGE_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TAVILY_USAGE_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return {"ok": False, "error": f"http {response.status_code}"}
        payload = response.json()
        if not isinstance(payload, dict):
            return {"ok": False, "error": "invalid payload"}
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        key = payload.get("key") if isinstance(payload.get("key"), dict) else {}
        return {
            "ok": True,
            "plan": str(account.get("current_plan") or ""),
            "plan_usage": _coerce_number(account.get("plan_usage")),
            "plan_limit": _coerce_number(account.get("plan_limit")),
            "key_usage": _coerce_number(key.get("usage")),
            "key_limit": _coerce_number(key.get("limit")),
        }
    except Exception as exc:  # 网络/JSON 异常统一降级为该 key 的错误行
        return {"ok": False, "error": str(exc) or "request failed"}


def fetch_tavily_usage(
    api_keys: tuple[str, ...] | list[str],
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """逐 key 查询 tavily /usage，带进程内 TTL 缓存；返回与 api_keys 对齐的列表。"""
    keys = [str(k) for k in api_keys if str(k)]
    now = time.time()
    results: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []
    with _tavily_cache_lock:
        for key in keys:
            cached = _tavily_cache.get(key)
            if not force_refresh and cached and now - cached[0] < TAVILY_USAGE_CACHE_TTL_SECONDS:
                results[key] = cached[1]
            elif key not in to_fetch:
                to_fetch.append(key)
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(4, len(to_fetch))) as pool:
            fetched = list(pool.map(_fetch_tavily_key_usage, to_fetch))
        with _tavily_cache_lock:
            for key, payload in zip(to_fetch, fetched):
                results[key] = payload
                # 失败结果不进缓存：下次打开面板重试，而不是把错误钉住 5 分钟。
                if payload.get("ok"):
                    _tavily_cache[key] = (time.time(), payload)
    return [results.get(key, {"ok": False, "error": "not fetched"}) for key in keys]


# ── 报告装配（admin 端点消费）───────────────────────────────────────────────


def _key_label(index: int, key_id: str) -> str:
    """展示标签：序号 + 指纹前 6 位。指纹是 sha256 派生，不含 key 原文任何片段。"""
    if key_id and key_id != UNKNOWN_KEY_ID:
        return f"#{index + 1} · {key_id[:6]}"
    return f"#{index + 1}"


def _load_quota_snapshot(provider: str, key_id: str) -> dict[str, Any] | None:
    raw = accounts.get_config(_snapshot_config_key(provider, key_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _layer_of(name: str, config: ManagedSearchPoolConfig) -> str:
    if name in config.routing.primary:
        return "primary"
    if name in config.routing.secondary:
        return "secondary"
    return "unrouted"


_ZERO = {"calls": 0.0, "units": 0.0, "errors": 0.0}


def _estimated_used(
    pcfg: ManagedSearchProviderConfig,
    current_alltime: dict[str, float],
    current_month: dict[str, float],
) -> float:
    """本地估算口径（只统计**当前配置的 key 指纹**——换掉的 key 不再拖累新 key 的估算）。

    - 量的换算：usd 类（exa）按调用次数 × 单价；其余直接用 units 累计
      （serper 的 units 来自响应体 credits 真值，tavily/brave 为 1/调用）。
    - 窗口：monthly 额度按「本月至今」（月初重置，历史消耗无关，baseline 不适用）；
      one_time/未声明按全时段累计 + baseline_used（记账启用前的存量消耗）。
    """
    totals = current_month if pcfg.quota.model == "monthly" else current_alltime
    baseline = 0.0 if pcfg.quota.model == "monthly" else pcfg.quota.baseline_used
    if pcfg.quota.unit == "usd":
        return baseline + totals["calls"] * pcfg.quota.est_cost_per_call
    return baseline + totals["units"]


def _sum_buckets(buckets: list[dict[str, float]]) -> dict[str, float]:
    out = dict(_ZERO)
    for bucket in buckets:
        for field in ("calls", "units", "errors"):
            out[field] += float(bucket.get(field, 0) or 0)
    return out


def build_search_quota_report(
    config: ManagedSearchPoolConfig,
    *,
    include_live: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    today = metering.today_shanghai()
    since = (date.fromisoformat(today) - timedelta(days=_HISTORY_WINDOW_DAYS - 1)).isoformat()
    stats_since = (date.fromisoformat(today) - timedelta(days=_STATS_WINDOW_DAYS - 1)).isoformat()
    month_start = f"{today[:7]}-01"
    history_rows = accounts.get_search_usage_history(since)
    alltime_rows = accounts.get_search_usage_totals()

    # (provider, key_id) → 全时段累计
    per_key_alltime: dict[tuple[str, str], dict[str, float]] = {}
    for row in alltime_rows:
        per_key_alltime[(row["provider"], str(row["key_id"]))] = {
            "calls": float(row["calls"] or 0),
            "units": float(row["units"] or 0),
            "errors": float(row["errors"] or 0),
        }

    # 窗口内累计：近 30 日统计（对外口径）/ 本月至今 per-key（monthly 估算窗口）/ 逐日趋势
    calls_today: dict[str, float] = {}
    calls_30d: dict[str, float] = {}
    errors_30d: dict[str, float] = {}
    per_key_month: dict[tuple[str, str], dict[str, float]] = {}
    history_by_day: dict[tuple[str, str], dict[str, float]] = {}
    for row in history_rows:
        name = row["provider"]
        kid = str(row["key_id"])
        calls = float(row["calls"] or 0)
        units = float(row["units"] or 0)
        errors = float(row["errors"] or 0)
        if row["day"] >= stats_since:
            calls_30d[name] = calls_30d.get(name, 0.0) + calls
            errors_30d[name] = errors_30d.get(name, 0.0) + errors
        if row["day"] == today:
            calls_today[name] = calls_today.get(name, 0.0) + calls
        if row["day"] >= month_start:
            bucket = per_key_month.setdefault((name, kid), dict(_ZERO))
            bucket["calls"] += calls
            bucket["units"] += units
            bucket["errors"] += errors
        slot = history_by_day.setdefault((row["day"], name), dict(_ZERO))
        slot["calls"] += calls
        slot["units"] += units
        slot["errors"] += errors

    providers_out: list[dict[str, Any]] = []
    for name, pcfg in config.providers.items():
        key_ids = [key_fingerprint(k) for k in pcfg.api_keys]
        current_alltime = _sum_buckets([per_key_alltime.get((name, kid), _ZERO) for kid in key_ids])
        current_month = _sum_buckets([per_key_month.get((name, kid), _ZERO) for kid in key_ids])
        entry: dict[str, Any] = {
            "name": name,
            "enabled": pcfg.enabled,
            "layer": _layer_of(name, config),
            "weight": pcfg.weight,
            "key_count": len(pcfg.api_keys),
            "quota_model": pcfg.quota.model,
            "quota_unit": pcfg.quota.unit,
            "per_key_quota": pcfg.quota.per_key_quota,
            "source": "none",
            "total_quota": None,
            "total_used": None,
            "total_remaining": None,
            "observed_calls_today": calls_today.get(name, 0.0),
            "observed_calls_30d": calls_30d.get(name, 0.0),
            "observed_errors_30d": errors_30d.get(name, 0.0),
            "observed_units_alltime": current_alltime["units"],
            "note": None,
            "keys": [],
        }

        key_rows: list[dict[str, Any]] = []
        for index, kid in enumerate(key_ids):
            observed = per_key_alltime.get((name, kid), _ZERO)
            key_rows.append({
                "label": _key_label(index, kid),
                "used": None,
                "limit": None,
                "remaining": None,
                "observed_calls": observed["calls"],
                "observed_units": observed["units"],
                "observed_errors": observed["errors"],
            })

        if name == "tavily" and pcfg.enabled and include_live and pcfg.api_keys:
            live = fetch_tavily_usage(pcfg.api_keys, force_refresh=force_refresh)
            any_ok = False
            # plan_usage/plan_limit 是**账号级**字段：多 key 属同一账号时逐 key 求和会翻倍。
            # 以 (plan, plan_usage, plan_limit) 元组近似账号身份去重（同账号在同一报告内
            # 拿到的元组必然一致；两个全新零用量账号会误合并——宁可少报不虚报，note 说明）。
            seen_accounts: set[tuple] = set()
            used_sum = 0.0
            limit_sum = 0.0
            deduped = False
            for row, result in zip(key_rows, live):
                if not result.get("ok"):
                    row["error"] = result.get("error") or "查询失败"
                    continue
                any_ok = True
                used = result.get("plan_usage")
                limit = result.get("plan_limit")
                row["used"] = used
                row["limit"] = limit
                row["plan"] = result.get("plan") or ""
                if used is None or limit is None:
                    continue
                row["remaining"] = max(0.0, limit - used)
                account_identity = (result.get("plan"), used, limit)
                if account_identity in seen_accounts:
                    deduped = True
                    continue
                seen_accounts.add(account_identity)
                used_sum += used
                limit_sum += limit
            if any_ok:
                entry["source"] = "live"
                entry["total_quota"] = limit_sum
                entry["total_used"] = used_sum
                entry["total_remaining"] = max(0.0, limit_sum - used_sum)
                if deduped:
                    entry["note"] = "检测到疑似同账号 key，总额已去重合并"
        elif name == "brave":
            remaining_sum = 0.0
            limit_sum = 0.0
            any_snapshot = False
            for row, kid in zip(key_rows, key_ids):
                snapshot = _load_quota_snapshot(name, kid)
                if not snapshot:
                    continue
                remaining = _coerce_number(snapshot.get("month_remaining"))
                limit = _coerce_number(snapshot.get("month_limit"))
                observed_at = _coerce_number(snapshot.get("observed_at"))
                # brave 文档：月配额段 0 = unlimited（计量计费档），视为无月度信号
                if limit is not None and limit <= 0:
                    continue
                any_snapshot = True
                row["remaining"] = remaining
                row["limit"] = limit
                row["observed_at"] = observed_at
                if remaining is not None and limit is not None:
                    remaining_sum += remaining
                    limit_sum += limit
                    row["used"] = max(0.0, limit - remaining)
            if any_snapshot:
                entry["source"] = "observed"
                entry["total_quota"] = limit_sum or None
                entry["total_remaining"] = remaining_sum
                if limit_sum:
                    entry["total_used"] = max(0.0, limit_sum - remaining_sum)

        # 无真值来源（serper/exa，或 brave 尚无快照 / tavily 全 key 查询失败）
        # 且声明了额度 → 本地估算（只按当前配置 key 的指纹归集）
        if entry["source"] == "none" and pcfg.quota.per_key_quota > 0 and pcfg.api_keys:
            total_quota = pcfg.quota.per_key_quota * len(pcfg.api_keys)
            used = _estimated_used(pcfg, current_alltime, current_month)
            entry["source"] = "estimated"
            entry["total_quota"] = total_quota
            entry["total_used"] = used
            entry["total_remaining"] = max(0.0, total_quota - used)

        entry["keys"] = key_rows
        providers_out.append(entry)

    history_out = [
        {"day": day, "provider": name, "calls": slot["calls"], "units": slot["units"], "errors": slot["errors"]}
        for (day, name), slot in sorted(history_by_day.items())
    ]
    return {
        "generated_at": time.time(),
        "today": today,
        "since": since,
        "providers": providers_out,
        "history": history_out,
    }
