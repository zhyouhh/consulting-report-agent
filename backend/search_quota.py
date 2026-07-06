"""搜索池用量记账 + 额度报告装配（叶子模块）。

依赖边界：只依赖 accounts / metering / config（类型）+ requests/stdlib，
绝不 import chat / skill / main / independent_review。

三类数据源，按 provider 各取所能（数据质量在报告里诚实标注 source）：
- tavily  → 官方 GET /usage 实时真值（用普通搜索 key 即可查，5 分钟 TTL 缓存）
- brave   → 搜索响应头 X-RateLimit-* 被动快照（router 记账时顺带落 app_config）
- serper/exa → 无任何查询手段，只能本地记账估算（总额静态声明在 managed_search_pool.json
  的 quota 块；估算不含其它部署的消耗，剩余偏乐观——展示层须标注）
"""
from __future__ import annotations

import json
import logging
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

# 31 天：既是趋势窗口，也保证「本月至今」子窗口完整（31 日大月的月初仍在窗内）
_HISTORY_WINDOW_DAYS = 31

_tavily_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_tavily_cache_lock = threading.Lock()


# ── 记账（SearchRouter 注入的 usage_recorder）───────────────────────────────


def _snapshot_config_key(provider: str, key_index: int) -> str:
    return f"search_quota_snapshot:{provider}:{key_index}"


def record_search_usage(
    *,
    provider: str,
    key_index: int | None,
    calls: int,
    units: float,
    errors: int,
    quota_snapshot: dict[str, Any] | None = None,
) -> None:
    """best-effort 记账：任何异常只打日志绝不上抛——记账故障不能影响搜索可用性。"""
    try:
        day = metering.today_shanghai()
        index = key_index if isinstance(key_index, int) and key_index >= 0 else -1
        accounts.add_search_usage(provider, index, day, calls=calls, units=units, errors=errors)
        if quota_snapshot:
            accounts.set_config(
                _snapshot_config_key(provider, index),
                json.dumps(quota_snapshot, ensure_ascii=False),
            )
    except Exception:
        logger.warning("[search-usage] 记账失败（忽略，不影响搜索）", exc_info=True)


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


def _key_label(index: int, api_key: str) -> str:
    tail = api_key[-4:] if len(api_key) >= 8 else ""
    return f"#{index + 1} ({tail})" if tail else f"#{index + 1}"


def _load_quota_snapshot(provider: str, key_index: int) -> dict[str, Any] | None:
    raw = accounts.get_config(_snapshot_config_key(provider, key_index))
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


def _estimated_used(
    pcfg: ManagedSearchProviderConfig,
    alltime: dict[str, float],
    month_to_date: dict[str, float],
) -> float:
    """本地估算口径。

    - 量的换算：usd 类（exa）按调用次数 × 单价；其余直接用 units 累计
      （serper 的 units 来自响应体 credits 真值，tavily/brave 为 1/调用）。
    - 窗口：monthly 额度按「本月至今」（月初重置，历史消耗无关，baseline 不适用）；
      one_time/未声明按全时段累计 + baseline_used（记账启用前的存量消耗）。
    """
    totals = month_to_date if pcfg.quota.model == "monthly" else alltime
    baseline = 0.0 if pcfg.quota.model == "monthly" else pcfg.quota.baseline_used
    if pcfg.quota.unit == "usd":
        return baseline + totals["calls"] * pcfg.quota.est_cost_per_call
    return baseline + totals["units"]


def build_search_quota_report(
    config: ManagedSearchPoolConfig,
    *,
    include_live: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    today = metering.today_shanghai()
    since = (date.fromisoformat(today) - timedelta(days=_HISTORY_WINDOW_DAYS - 1)).isoformat()
    history_rows = accounts.get_search_usage_history(since)
    alltime_rows = accounts.get_search_usage_totals()

    # provider×key 与 provider 级累计
    per_key_alltime: dict[tuple[str, int], dict[str, float]] = {}
    provider_alltime: dict[str, dict[str, float]] = {}
    for row in alltime_rows:
        key = (row["provider"], int(row["key_index"]))
        per_key_alltime[key] = {
            "calls": float(row["calls"] or 0),
            "units": float(row["units"] or 0),
            "errors": float(row["errors"] or 0),
        }
        bucket = provider_alltime.setdefault(row["provider"], {"calls": 0.0, "units": 0.0, "errors": 0.0})
        for field in ("calls", "units", "errors"):
            bucket[field] += float(row[field] or 0)

    # 窗口内 provider 级：今日调用 / 窗口期调用与失败 / 本月至今（monthly 估算窗口）
    month_start = f"{today[:7]}-01"
    calls_today: dict[str, float] = {}
    calls_30d: dict[str, float] = {}
    errors_30d: dict[str, float] = {}
    provider_month: dict[str, dict[str, float]] = {}
    history_by_day: dict[tuple[str, str], dict[str, float]] = {}
    for row in history_rows:
        name = row["provider"]
        calls = float(row["calls"] or 0)
        units = float(row["units"] or 0)
        errors = float(row["errors"] or 0)
        calls_30d[name] = calls_30d.get(name, 0.0) + calls
        errors_30d[name] = errors_30d.get(name, 0.0) + errors
        if row["day"] == today:
            calls_today[name] = calls_today.get(name, 0.0) + calls
        if row["day"] >= month_start:
            bucket = provider_month.setdefault(name, {"calls": 0.0, "units": 0.0, "errors": 0.0})
            bucket["calls"] += calls
            bucket["units"] += units
            bucket["errors"] += errors
        slot = history_by_day.setdefault(
            (row["day"], name), {"calls": 0.0, "units": 0.0, "errors": 0.0}
        )
        slot["calls"] += calls
        slot["units"] += units
        slot["errors"] += errors

    providers_out: list[dict[str, Any]] = []
    for name, pcfg in config.providers.items():
        totals = provider_alltime.get(name, {"calls": 0.0, "units": 0.0, "errors": 0.0})
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
            "observed_units_alltime": totals["units"],
            "keys": [],
        }

        key_rows: list[dict[str, Any]] = []
        for index, api_key in enumerate(pcfg.api_keys):
            observed = per_key_alltime.get((name, index), {"calls": 0.0, "units": 0.0, "errors": 0.0})
            key_rows.append({
                "label": _key_label(index, api_key),
                "used": None,
                "limit": None,
                "remaining": None,
                "observed_calls": observed["calls"],
                "observed_units": observed["units"],
                "observed_errors": observed["errors"],
            })

        if name == "tavily" and pcfg.enabled and include_live and pcfg.api_keys:
            live = fetch_tavily_usage(pcfg.api_keys, force_refresh=force_refresh)
            used_sum = 0.0
            limit_sum = 0.0
            any_ok = False
            for row, result in zip(key_rows, live):
                if result.get("ok"):
                    any_ok = True
                    used = result.get("plan_usage")
                    limit = result.get("plan_limit")
                    row["used"] = used
                    row["limit"] = limit
                    row["plan"] = result.get("plan") or ""
                    if used is not None and limit is not None:
                        row["remaining"] = max(0.0, limit - used)
                        used_sum += used
                        limit_sum += limit
                else:
                    row["error"] = result.get("error") or "查询失败"
            if any_ok:
                entry["source"] = "live"
                entry["total_quota"] = limit_sum
                entry["total_used"] = used_sum
                entry["total_remaining"] = max(0.0, limit_sum - used_sum)
        elif name == "brave":
            snapshots = [_load_quota_snapshot(name, index) for index in range(len(pcfg.api_keys))]
            remaining_sum = 0.0
            limit_sum = 0.0
            any_snapshot = False
            for row, snapshot in zip(key_rows, snapshots):
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

        # 无真值来源（serper/exa，或 brave 尚无快照）且声明了额度 → 本地估算
        if entry["source"] == "none" and pcfg.quota.per_key_quota > 0 and pcfg.api_keys:
            total_quota = pcfg.quota.per_key_quota * len(pcfg.api_keys)
            used = _estimated_used(
                pcfg,
                totals,
                provider_month.get(name, {"calls": 0.0, "units": 0.0, "errors": 0.0}),
            )
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
