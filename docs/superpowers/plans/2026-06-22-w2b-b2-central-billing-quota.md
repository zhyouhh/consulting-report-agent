# W2-B / B2 中央计费 + per-user 配额 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给已多租户化的应用加一层中央计费——所有 managed LLM/视觉调用经单一 `MeteredManagedClient` 出口，按 deepseek 缓存三档精确计价、整数微元累计进 `usage_daily`，并以 per-user ¥5/天金额配额做门禁（reserve→settle、usage 缺失 fail-closed）。

**Architecture:** 现实里 `ChatHandler`（`chat.py:408`）和 `IndependentReviewAgent`（`independent_review.py:246`）各自构造裸 `OpenAI` 客户端，全部 5 个 managed 调用点都是 `self.client.chat.completions.create(...)`。**核心做法**：managed 模式下把 `self.client` 整体换成 `MeteredManagedClient`（暴露同样的 `.chat.completions.create`、绑定 uid），custom 模式仍用裸 `OpenAI`。这样 5 个调用点**调用语法一行不改**，计费在 wrapper 内自动发生、custom 天然不计费、满足 spec「唯一出口 + source-guard」。计费**被动**：wrapper 只 reserve/读 usage/settle，**不注入** `include_usage`（保留 chat.py 既有的 include_usage 失败回退逻辑不被打架），由调用点自行声明 include_usage；provider 不给 usage 即 fail-closed 保守封顶。

**Tech Stack:** Python 3.11/3.12、FastAPI、SQLite(WAL)、OpenAI 兼容 SDK、`unittest`+`pytest`（mock 外部 HTTP）、前端 Node 原生 `node:test`。

**Spec:** `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（§5.4 / §6 / §7 me / §10 / §13 B2 验收门；4 轮 Codex APPROVED）。
**Branch:** `feat/w2b-multi-tenant-core`（B1 已 merge main `c62cd4d`；B2 在同分支续做或新开 `feat/w2b-b2-billing`，由执行者定）。

---

## 关键现状（B1 已落地，写代码前必读，避免照 spec 想象）

| spec 设想 | B1 实际 | B2 影响 |
|---|---|---|
| `usage_daily` 表（§5.4） | **不存在**（`accounts.py:init_db` 只建 users/sessions/app_config） | B2 新建 |
| `users` 表 cost/admin 列 | **已全有** `daily_cost_micro_yuan`(nullable)/`is_admin`/`must_change_password`/`disabled`（`accounts.py:48-52`） | 不改表结构，只读写 |
| 每模型单价表 | **不存在**（`config.py` 无任何 pricing 字段） | B2 新建常量 |
| `MeteredManagedClient` 唯一出口 | 现 2 个裸 `OpenAI`（chat + review）+ 视觉/压缩复用 chat 的 client | wrapper 包裹 |
| `/api/auth/me` 带 cost | **无** cost 字段（`main.py:247-255` 只返 uid/username/is_admin/must_change_password） | B2 加 `today_cost_yuan`/`daily_cap_yuan` |
| 主聊天读 usage | **已读**（`chat.py:2806` 条件 include_usage + `2866` 读 `chunk.usage`，有 `_should_retry_stream_without_usage` 回退） | wrapper 不得二次注入 include_usage |

**5 个 managed 调用点（metering 必须全覆盖）：**
1. `chat.py:2810` 主聊天流式（`stream=True`，已条件请求 include_usage）
2. `chat.py:3266` 主聊天 sync（`stream=False`）
3. `chat.py:850` 压缩摘要（`stream=False`，今天不读 usage）
4. `chat.py:446` 视觉转写 Qwen3-VL（`stream=False`，今天不读 usage）
5. `independent_review.py:482` 独立审查流式（`stream=True`，今天不读 usage）

全部 5 个都经各自的 `self.client`（chat 的 4 个 + review 的 1 个），故只需包两个 client 即全覆盖。

**DeepSeek 官渠硬约束（不得破，全程保留）：** 带 tools 不显式发 `tool_choice="auto"`（`_should_send_explicit_tool_choice`）；tool-call follow-up 回传非空 `reasoning_content`；不塞 null 字段；只 `role`+`content` 到 provider。B2 只加 reserve/settle + 被动读 usage，**不碰** provider message / tool-call 序列化。

---

## File Structure

**新建：**
- `backend/metering.py` — 中央计费叶子模块：纯函数（`price_micro_yuan` / `extract_billing_usage` / `today_shanghai`）+ 异常（`QuotaExceededError` / `ModelPausedError`）+ `MeteredManagedClient`（reserve/settle/fail-closed/miss-counter）+ `wrap_client_for_billing` 工厂。依赖 `accounts`、`config`，**绝不** import `chat`/`skill`/`main`/`independent_review`。
- `frontend/src/utils/quotaFormat.js` — 微元↔¥ 展示纯函数。
- `tests/test_metering.py`、`frontend/tests/quotaFormat.test.mjs`。

**修改：**
- `backend/config.py` — `DEFAULT_MANAGED_MODEL_PRICING`、`DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN`、fail-closed 常量。
- `backend/accounts.py` — `usage_daily` 表 + `get_usage_today` / `add_usage` / `get_effective_daily_cap_micro` + 全局 cap seed。
- `backend/chat.py` — `__init__` 包 client；主流式/sync 把 `QuotaExceededError`/`ModelPausedError` 转友好事件。
- `backend/independent_review.py` — 加 `uid`、包 client、流式请求 include_usage、传 store/settle。
- `backend/main.py` — `init_db` 续建表（自动）、endpoint 预检 + 友好配额响应、`/api/auth/me` 加 cost、review 端点传 `scope.uid`。
- `frontend/src/components/Sidebar.jsx` 账号块显示今日 ¥；`App.jsx` 消费 me 的 cost 字段。

---

## 设计约定（所有任务共享，先读）

- **金额单位**：全程**整数微元**（1 微元 = 1e-6 元）。展示层 `÷1e6`。单价单位 = **元/百万 token**。公式（spec §6.1）：
  ```
  cost_micro_yuan = round( hit×p_hit + miss×p_miss + completion×p_out )   # token×(元/百万token)=微元
  ```
  deepseek-v4-pro 三档：`p_hit=0.025 / p_miss=3 / p_out=6`。
- **usage 字段名**（deepseek 官渠，spec §6.1 实测）：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `completion_tokens`（已含 reasoning，不另算）。miss 缺失时回退 `prompt_tokens - hit`。
- **reserve（门禁）**：每次 `create()` 前查 `get_usage_today(uid).cost ≥ cap` → 抛 `QuotaExceededError`（不打断进行中调用、只挡下一次）。
- **settle（累计）**：拿到 usage 后算微元，`usage_daily` 原子累加。
- **fail-closed**：拿不到 usage（流式自然结束无 usage 末包 / provider 中途抛 / 消费方中断 / 多模态不给）→ 按**该模型 `resolve_context_policy(model).effective_context_limit` × p_miss** 保守上界计入（deepseek-v4-pro=256000，非硬编 128000）；维护 **per-(uid,model,day) 连续缺失计数**（day 入键 → 次日自动清零，避开「暂停后 reserve 先拦截致永不清零」），连续 3 次 → 抛 `ModelPausedError`。**流式 `finally` 恰好结算一次**：自然结束 / provider 异常 / 消费方中断(GeneratorExit，含 chat.py 处理 chunk 时抛) 一律按 last_usage 结算，缺失即 fail-closed（不留漏计后门）。
- **被动 include_usage**：wrapper 不注入；流式调用点自报。
- **跨日**：`day` 按 `Asia/Shanghai`，新日新行 = 自动重置。
- **custom 模式**：wrapper 工厂对 `mode=="custom"` 返回裸 client、不计费、不受 ¥ 门禁。**⚠️ 重要（✦ Codex BLOCKER）：B1/B2 production 仍 managed-forced**——`config.py:normalize_settings_payload` 在 `load_settings/save_settings` 路径**无条件**改 `mode="managed"`（config.py:325-328 两分支都强制），故生产中 custom 不可达。本 plan 的 custom 分支与单元测试是**对 wrapper 工厂逻辑的单元验证 + 为 B3 custom 激活预留**，**不是 B2 production 验收**；§6.4「custom 不计费」在 B2 仅单元级成立、生产路径不触发。custom 真正激活（+SSRF）归 B3。

---

## Task 1: Pricing 常量 + `price_micro_yuan` 纯函数

**Files:**
- Modify: `backend/config.py`（在 `DEFAULT_MANAGED_*` 常量区，约 `config.py:9-14` 之后）
- Create: `backend/metering.py`
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py
import unittest
from backend import metering
from backend.config import DEFAULT_MANAGED_MODEL_PRICING, DEFAULT_MANAGED_MODEL


class PriceTests(unittest.TestCase):
    def test_deepseek_three_tier_cost_matches_spec_numbers(self):
        # spec §6.1：p_hit=0.025 / p_miss=3 / p_out=6（元/百万token）
        # 非流式冷：hit=0 miss=1289 completion=500
        cost = metering.price_micro_yuan(DEFAULT_MANAGED_MODEL, hit=0, miss=1289, completion=500,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # 0*0.025 + 1289*3 + 500*6 = 3867 + 3000 = 6867 微元
        self.assertEqual(cost, 6867)

    def test_cache_hit_is_cheap(self):
        # 非流式热：hit=1280 miss=9 completion=500
        cost = metering.price_micro_yuan(DEFAULT_MANAGED_MODEL, hit=1280, miss=9, completion=500,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # round(1280*0.025) + 9*3 + 500*6 = 32 + 27 + 3000 = 3059
        self.assertEqual(cost, 3059)

    def test_unknown_model_uses_safe_fallback_pricing(self):
        cost = metering.price_micro_yuan("some/unknown-model", hit=0, miss=1000, completion=0,
                                         pricing=DEFAULT_MANAGED_MODEL_PRICING)
        # 未知模型 → fallback 三档（按 deepseek 价保守），1000*3 = 3000
        self.assertEqual(cost, 3000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metering.py::PriceTests -v`
Expected: FAIL（`backend.metering` 不存在 / `DEFAULT_MANAGED_MODEL_PRICING` 未定义）

- [ ] **Step 3: Add pricing constants to config.py**

在 `backend/config.py` 的模型常量区（`DEFAULT_MANAGED_VISION_MODEL = ...` 之后）加：

```python
# 每模型单价（元/百万 token）：(命中, 未命中, 输出)。spec §6.1 上机实测口径。
# vision 单价占位（按 deepseek 同档保守，可后填真实价）。
DEFAULT_MANAGED_MODEL_PRICING: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-pro": (0.025, 3.0, 6.0),
    "Qwen/Qwen3-VL-8B-Instruct": (0.025, 3.0, 6.0),
}
# 未知模型 fallback 单价（保守按 deepseek 三档）
FALLBACK_MODEL_PRICING: tuple[float, float, float] = (0.025, 3.0, 6.0)
# 全局默认日配额：¥5/天 = 5_000_000 微元
DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN: int = 5_000_000
# fail-closed：连续 N 次 usage 缺失 → 暂停该 (uid, model)
MAX_CONSECUTIVE_USAGE_MISS = 3
# fail-closed 保守封顶上下文上限（token）。✦ Codex BLOCKER：视觉模型不在 context_policy 的
# EXACT_MODEL_TIERS → 会落 UNKNOWN_FALLBACK_TIER（非「该模型上下文上限」）→ 显式给视觉模型定锚；
# 其余模型 _settle 回落 resolve_context_policy(model).effective_context_limit。
MANAGED_FAILCLOSED_CEILING: dict[str, int] = {
    "Qwen/Qwen3-VL-8B-Instruct": 32768,   # Qwen3-VL 基础上下文；× p_miss 作视觉调用 fail-closed 上界
}
```

- [ ] **Step 4: Create metering.py with the pure pricing function**

```python
# backend/metering.py
"""中央计费叶子模块（只依赖 accounts/config；绝不 import chat/skill/main/independent_review）。"""
from __future__ import annotations
from backend.config import FALLBACK_MODEL_PRICING


def price_micro_yuan(model: str, hit: int, miss: int, completion: int, pricing: dict) -> int:
    """token×(元/百万token)=微元；单价表缺该模型时用 FALLBACK_MODEL_PRICING 保守计价。"""
    p_hit, p_miss, p_out = pricing.get(model, FALLBACK_MODEL_PRICING)
    return round(hit * p_hit + miss * p_miss + completion * p_out)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::PriceTests -v`
Expected: PASS（3 用例）

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): per-model pricing constants + price_micro_yuan pure fn"
```

---

## Task 2: `extract_billing_usage` 纯函数（从 provider usage 取三档）

**Files:**
- Modify: `backend/metering.py`
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py（追加）
class _FakeUsage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class ExtractUsageTests(unittest.TestCase):
    def test_reads_deepseek_cache_fields(self):
        u = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                       prompt_cache_miss_tokens=1289, completion_tokens=500)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (0, 1289, 500))

    def test_hot_cache(self):
        u = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=1280,
                       prompt_cache_miss_tokens=9, completion_tokens=500)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (1280, 9, 500))

    def test_miss_falls_back_to_prompt_minus_hit_when_absent(self):
        u = _FakeUsage(prompt_tokens=1000, prompt_cache_hit_tokens=200, completion_tokens=50)
        bu = metering.extract_billing_usage(u)
        self.assertEqual((bu.hit, bu.miss, bu.completion), (200, 800, 50))

    def test_returns_none_when_usage_missing(self):
        self.assertIsNone(metering.extract_billing_usage(None))

    def test_returns_none_when_no_token_fields(self):
        self.assertIsNone(metering.extract_billing_usage(_FakeUsage(foo=1)))

    def test_accepts_dict_usage(self):
        bu = metering.extract_billing_usage(
            {"prompt_tokens": 100, "prompt_cache_hit_tokens": 10,
             "prompt_cache_miss_tokens": 90, "completion_tokens": 5})
        self.assertEqual((bu.hit, bu.miss, bu.completion), (10, 90, 5))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metering.py::ExtractUsageTests -v`
Expected: FAIL（`extract_billing_usage` 不存在）

- [ ] **Step 3: Implement extract_billing_usage**

在 `backend/metering.py` 顶部加 dataclass 与函数：

```python
from dataclasses import dataclass


@dataclass
class BillingUsage:
    hit: int
    miss: int
    completion: int


def _usage_get(usage, key: str):
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def extract_billing_usage(usage) -> BillingUsage | None:
    """从 provider usage（对象或 dict）取三档计费 token。无法识别 token 字段时返回 None（→ fail-closed）。"""
    if usage is None:
        return None
    prompt = _usage_get(usage, "prompt_tokens")
    completion = _usage_get(usage, "completion_tokens")
    hit = _usage_get(usage, "prompt_cache_hit_tokens")
    miss = _usage_get(usage, "prompt_cache_miss_tokens")
    if prompt is None and completion is None and hit is None and miss is None:
        return None
    hit = int(hit or 0)
    if miss is None:
        miss = max(int(prompt or 0) - hit, 0)
    else:
        miss = int(miss)
    return BillingUsage(hit=hit, miss=miss, completion=int(completion or 0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::ExtractUsageTests -v`
Expected: PASS（6 用例）

- [ ] **Step 5: Commit**

```bash
git add backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): extract_billing_usage from deepseek usage fields"
```

---

## Task 3: `today_shanghai` 日界 + `usage_daily` 表与 accounts 计费函数

**Files:**
- Modify: `backend/metering.py`（`today_shanghai`）
- Modify: `backend/accounts.py`（建表 + 函数）
- Test: `tests/test_metering.py`、`tests/test_accounts.py`

- [ ] **Step 1: Write the failing test (day boundary)**

```python
# tests/test_metering.py（追加）
import datetime as _dt


class DayBoundaryTests(unittest.TestCase):
    def test_today_shanghai_is_yyyy_mm_dd(self):
        s = metering.today_shanghai()
        _dt.datetime.strptime(s, "%Y-%m-%d")  # 不抛即合法
        self.assertEqual(len(s), 10)
```

- [ ] **Step 2: Write the failing test (accounts usage_daily)**

```python
# tests/test_accounts.py（追加；该文件已有 CRA_DATA_ROOT 隔离夹具，沿用其 setUp/tearDown 模式）
import importlib, os, tempfile, unittest


class UsageDailyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name
        import backend.config as config; importlib.reload(config)
        import backend.tenant as tenant; importlib.reload(tenant)
        global accounts
        import backend.accounts as accounts; importlib.reload(accounts)
        accounts.init_db()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CRA_DATA_ROOT", None)
        else:
            os.environ["CRA_DATA_ROOT"] = self._old
        self._tmp.cleanup()

    def test_add_usage_accumulates_atomically(self):
        accounts.add_usage("u1", "2026-06-22", 100, hit=1, miss=2, output=3)
        accounts.add_usage("u1", "2026-06-22", 50, hit=4, miss=5, output=6)
        row = accounts.get_usage_today("u1", "2026-06-22")
        self.assertEqual(row["cost_micro_yuan"], 150)
        self.assertEqual(row["cache_hit_tokens"], 5)
        self.assertEqual(row["cache_miss_tokens"], 7)
        self.assertEqual(row["output_tokens"], 9)

    def test_cross_day_separate_rows(self):
        accounts.add_usage("u1", "2026-06-22", 100, 0, 0, 0)
        accounts.add_usage("u1", "2026-06-23", 7, 0, 0, 0)
        self.assertEqual(accounts.get_usage_today("u1", "2026-06-22")["cost_micro_yuan"], 100)
        self.assertEqual(accounts.get_usage_today("u1", "2026-06-23")["cost_micro_yuan"], 7)

    def test_get_usage_today_zero_when_absent(self):
        self.assertEqual(accounts.get_usage_today("nobody", "2026-06-22")["cost_micro_yuan"], 0)

    def test_effective_cap_prefers_user_override_then_global_then_default(self):
        # 无 override、无 app_config → 默认 5_000_000
        uid = accounts.create_user("alice", "pw-strong-123")
        from backend.config import DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN)
        # 设全局 → 取全局
        accounts.set_config("global_daily_cap_micro_yuan", "2000000")
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 2000000)
        # 设 user override → 取 override
        accounts.set_user_daily_cap_micro(uid, 9000)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 9000)
        # override 清回 None → 退全局
        accounts.set_user_daily_cap_micro(uid, None)
        self.assertEqual(accounts.get_effective_daily_cap_micro(uid), 2000000)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_metering.py::DayBoundaryTests tests/test_accounts.py::UsageDailyTests -v`
Expected: FAIL（`today_shanghai` / `add_usage` / `usage_daily` 表 不存在）

- [ ] **Step 4: Add today_shanghai to metering.py**

```python
# backend/metering.py（追加）
from datetime import datetime, timezone, timedelta

_SHANGHAI_TZ = timezone(timedelta(hours=8))


def today_shanghai() -> str:
    """配额日界（spec §6.3）：Asia/Shanghai 的 YYYY-MM-DD。UTC+8 固定偏移（中国无夏令时）。"""
    return datetime.now(timezone.utc).astimezone(_SHANGHAI_TZ).strftime("%Y-%m-%d")
```

- [ ] **Step 5: Add usage_daily table + functions to accounts.py**

在 `accounts.py:init_db` 的 `executescript` 里、`app_config` 建表之后追加：

```sql
            CREATE TABLE IF NOT EXISTS usage_daily(
                uid TEXT NOT NULL, day TEXT NOT NULL,
                cost_micro_yuan INTEGER NOT NULL DEFAULT 0,
                cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(uid, day));
```

在文件末尾追加函数（`get_config`/`set_config` 之后）：

```python
def add_usage(uid, day, cost_micro_yuan, hit, miss, output) -> None:
    # 原子累加：首行 INSERT、已存在则 DO UPDATE 累加（spec §5.4）。
    with _db() as conn:
        conn.execute(
            "INSERT INTO usage_daily(uid,day,cost_micro_yuan,cache_hit_tokens,cache_miss_tokens,output_tokens)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(uid,day) DO UPDATE SET"
            "   cost_micro_yuan=cost_micro_yuan+excluded.cost_micro_yuan,"
            "   cache_hit_tokens=cache_hit_tokens+excluded.cache_hit_tokens,"
            "   cache_miss_tokens=cache_miss_tokens+excluded.cache_miss_tokens,"
            "   output_tokens=output_tokens+excluded.output_tokens",
            (uid, day, int(cost_micro_yuan), int(hit), int(miss), int(output)))


def get_usage_today(uid, day) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT cost_micro_yuan,cache_hit_tokens,cache_miss_tokens,output_tokens"
            " FROM usage_daily WHERE uid=? AND day=?", (uid, day)).fetchone()
    if row is None:
        return {"cost_micro_yuan": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "output_tokens": 0}
    return dict(row)


def set_user_daily_cap_micro(uid, cap_micro_yuan) -> None:
    """None = 清除 per-user override（退回全局/默认）。"""
    with _db() as conn:
        conn.execute("UPDATE users SET daily_cost_micro_yuan=? WHERE uid=?",
                     (None if cap_micro_yuan is None else int(cap_micro_yuan), uid))


def get_effective_daily_cap_micro(uid) -> int:
    from backend.config import DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN
    rec = _get_user_row("uid", uid)
    if rec is not None and rec.get("daily_cost_micro_yuan") is not None:
        return int(rec["daily_cost_micro_yuan"])
    g = get_config("global_daily_cap_micro_yuan")
    if g is not None:
        return int(g)
    return DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_metering.py::DayBoundaryTests tests/test_accounts.py::UsageDailyTests -v`
Expected: PASS（5 用例）

- [ ] **Step 7: Run full accounts suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS（既有 B1 账号用例不回归）

- [ ] **Step 8: Commit**

```bash
git add backend/metering.py backend/accounts.py tests/test_metering.py tests/test_accounts.py
git commit -m "feat(w2b-b2): usage_daily table + atomic add_usage/get_usage_today/cap resolution + today_shanghai"
```

---

## Task 4: `MeteredManagedClient` — 非流式（reserve + call + settle + fail-closed）

**Files:**
- Modify: `backend/metering.py`
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py（追加；沿用 Task 3 的 CRA_DATA_ROOT 隔离 setUp/tearDown）
class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeOpenAI:
    def __init__(self, response):
        self.chat = _FakeChat(response)


class _FakeResp:
    def __init__(self, usage):
        self.usage = usage
        self.choices = []


class MeteredNonStreamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name
        import backend.config as config; importlib.reload(config)
        import backend.tenant as tenant; importlib.reload(tenant)
        import backend.accounts as accounts; importlib.reload(accounts)
        accounts.init_db()
        self.accounts = accounts
        import backend.metering as m; importlib.reload(m)
        self.m = m

    def tearDown(self):
        if self._old is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._old
        self._tmp.cleanup()

    def _client(self, usage):
        raw = _FakeOpenAI(_FakeResp(usage))
        return self.m.MeteredManagedClient(raw, uid="u1", model_pricing=__import__(
            "backend.config", fromlist=["x"]).DEFAULT_MANAGED_MODEL_PRICING)

    def test_settles_cost_after_call(self):
        usage = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=1289, completion_tokens=500)
        c = self._client(usage)
        c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 6867)
        self.assertEqual(row["cache_miss_tokens"], 1289)

    def test_reserve_blocks_when_over_cap(self):
        self.accounts.set_config("global_daily_cap_micro_yuan", "100")
        self.accounts.add_usage("u1", self.m.today_shanghai(), 100, 0, 0, 0)  # 已达上限
        c = self._client(_FakeUsage(prompt_tokens=1, completion_tokens=1,
                                    prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1))
        with self.assertRaises(self.m.QuotaExceededError):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        self.assertEqual(len(c.chat.completions._raw_calls()), 0)  # reserve 在调用前，未触达 provider

    def test_fail_closed_when_usage_missing(self):
        c = self._client(None)  # provider 不返回 usage
        c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        # 保守封顶 = deepseek-v4-pro effective 上限(256000) × p_miss(3) = 768000 微元
        self.assertEqual(row["cost_micro_yuan"], 768000)

    def test_getattr_delegates_unknown_attrs_to_raw(self):
        raw = _FakeOpenAI(_FakeResp(None))
        raw.responses = "RAW_RESPONSES_SENTINEL"   # 模拟 .responses（原生搜索面）
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(raw, uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)
        self.assertEqual(c.responses, "RAW_RESPONSES_SENTINEL")  # 透传裸 client，不 AttributeError

    def test_vision_model_fail_closed_uses_explicit_ceiling(self):
        # ✦ Codex BLOCKER：视觉模型用显式锚（32768），不落 context_policy 未知 fallback。
        c = self._client(None)
        c.chat.completions.create(model="Qwen/Qwen3-VL-8B-Instruct", messages=[], stream=False)
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 32768 * 3)   # 32768 × p_miss(3) = 98304
```

> 注：`_raw_calls()` 是 wrapper 暴露给测试的薄访问器；见 Step 3 的 `MeteredCompletions`。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MeteredNonStreamTests -v`
Expected: FAIL（`MeteredManagedClient` / `QuotaExceededError` 不存在）

- [ ] **Step 3: Implement MeteredManagedClient (non-stream path)**

在 `backend/metering.py` 追加。结构：`MeteredManagedClient.chat.completions.create` 镜像 OpenAI 接口；`stream=False` 走本任务路径，`stream=True` 留 Task 5 实现（先抛 `NotImplementedError` 占位，Task 5 替换）。

```python
import threading
from backend import accounts
from backend.config import (DEFAULT_MANAGED_MODEL_PRICING, FALLBACK_MODEL_PRICING,
                            DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN, MAX_CONSECUTIVE_USAGE_MISS,
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
    def _settle(self, model: str, usage) -> None:
        day = today_shanghai()
        bu = extract_billing_usage(usage)
        if bu is None:
            # fail-closed（spec §6.3「该模型上下文上限 × 未命中价估上界」）：视觉等模型有显式锚（不在 context_policy
            # EXACT tier），否则按该模型 effective 上下文上限（app 压缩保证 prompt ≤ effective），deepseek-v4-pro=256000；
            # resolve_context_policy 对未知模型走 UNKNOWN_FALLBACK_TIER、不抛。连续缺失计数 +1。
            ceiling = (MANAGED_FAILCLOSED_CEILING.get(model)
                       or resolve_context_policy(model).effective_context_limit)
            _, p_miss, _ = self._pricing.get(model, FALLBACK_MODEL_PRICING)
            cost = round(ceiling * p_miss)
            accounts.add_usage(self.uid, day, cost, 0, ceiling, 0)
            _bump_miss(self.uid, model, day)
            return
        cost = price_micro_yuan(model, bu.hit, bu.miss, bu.completion, self._pricing)
        accounts.add_usage(self.uid, day, cost, bu.hit, bu.miss, bu.completion)
        _reset_miss(self.uid, model, day)

    def _create(self, raw_completions, **kwargs):
        model = kwargs.get("model", "")
        self._reserve(model)
        if kwargs.get("stream"):
            raise NotImplementedError("stream path implemented in Task 5")
        response = raw_completions.create(**kwargs)
        self._settle(model, getattr(response, "usage", None))
        return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MeteredNonStreamTests -v`
Expected: PASS（3 用例）

- [ ] **Step 5: Commit**

```bash
git add backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): MeteredManagedClient non-stream reserve/settle/fail-closed"
```

---

## Task 5: `MeteredManagedClient` — 流式（透传生成器 + 末包结算）

**Files:**
- Modify: `backend/metering.py`
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py（追加，在 MeteredNonStreamTests 同夹具风格下新建类）
class _Chunk:
    def __init__(self, usage=None):
        self.usage = usage
        self.choices = []


class MeteredStreamTests(MeteredNonStreamTests):  # 复用 setUp/tearDown/_FakeOpenAI 构造
    def _stream_client(self, chunks):
        class _StreamCompletions:
            def __init__(self): self.calls = []
            def create(self, **kwargs):
                self.calls.append(kwargs)
                return iter(chunks)
        class _Chat: 
            def __init__(self): self.completions = _StreamCompletions()
        class _Raw:
            def __init__(self): self.chat = _Chat()
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        return self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)

    def test_stream_passes_through_chunks_and_settles_on_completion(self):
        usage = _FakeUsage(prompt_tokens=1289, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=1289, completion_tokens=500)
        chunks = [_Chunk(), _Chunk(), _Chunk(usage=usage)]
        c = self._stream_client(chunks)
        out = list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        self.assertEqual(len(out), 3)  # 原样透传所有 chunk
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 6867)

    def test_stream_fail_closed_when_no_usage_chunk(self):
        c = self._stream_client([_Chunk(), _Chunk()])  # 无 usage 末包
        list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # 保守封顶 256000×3

    def test_stream_provider_error_midstream_fail_closed(self):
        # ✦ Codex BLOCKER：provider 流中途抛 → fail-closed，不当成主动中断而漏计。
        def _boom():
            yield _Chunk()
            raise RuntimeError("provider dropped mid-stream")
        class _SC:
            def __init__(self): self.calls = []
            def create(self, **kw): self.calls.append(kw); return _boom()
        class _Ch:
            def __init__(self): self.completions = _SC()
        class _Raw:
            def __init__(self): self.chat = _Ch()
        from backend.config import DEFAULT_MANAGED_MODEL_PRICING
        c = self.m.MeteredManagedClient(_Raw(), uid="u1", model_pricing=DEFAULT_MANAGED_MODEL_PRICING)
        with self.assertRaises(RuntimeError):
            list(c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True))
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # fail-closed 计入、错误再抛

    def test_stream_interrupt_before_usage_fail_closed(self):
        # ✦ Codex BLOCKER：消费方在第一个 chunk 后中断（GeneratorExit，含「处理 chunk 时抛异常」场景）
        # 且未见 usage → fail-closed，不漏计已起的 managed 流（spec §6.3）。
        usage = _FakeUsage(prompt_tokens=10, prompt_cache_hit_tokens=0,
                           prompt_cache_miss_tokens=10, completion_tokens=5)
        chunks = [_Chunk(), _Chunk(usage=usage)]   # usage 在第二个，中断时尚未读到
        c = self._stream_client(chunks)
        gen = c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=True)
        next(gen)          # 只取第一个就中断
        gen.close()        # GeneratorExit → finally fail-closed
        row = self.accounts.get_usage_today("u1", self.m.today_shanghai())
        self.assertEqual(row["cost_micro_yuan"], 768000)  # 未见 usage → fail-closed 保守封顶
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MeteredStreamTests -v`
Expected: FAIL（`NotImplementedError`）

- [ ] **Step 3: Implement the streaming wrapper**

替换 `MeteredManagedClient._create` 里 `if kwargs.get("stream"): raise NotImplementedError(...)` 为：

```python
        if kwargs.get("stream"):
            raw_stream = raw_completions.create(**kwargs)
            return self._metered_stream(model, raw_stream)
```

并新增方法：

```python
    def _metered_stream(self, model: str, raw_stream):
        """透传每个 chunk + 记最后带 usage 的 chunk。无论**自然结束 / provider 异常 / 消费方中断
        (GeneratorExit)**，`finally` 都结算恰好一次：last_usage 缺失 → fail-closed 保守封顶。
        ✦ Codex BLOCKER（R1+R2）：① provider 中途抛不得漏计；② **消费方在处理已 yield 的 chunk 时抛异常，
        经 gen.close()→GeneratorExit 到达此处**——与「用户主动放弃」无法从生成器内区分，故 spec §6.3
        「流式中断 + usage 缺失 = fail-closed」对 GeneratorExit 也一律 fail-closed（不留漏计后门）。"""
        last_usage = None
        try:
            for chunk in raw_stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    last_usage = u
                yield chunk
        finally:
            self._settle(model, last_usage)   # 自然结束 / provider 异常 / GeneratorExit 都恰好结算一次
            close = getattr(raw_stream, "close", None)   # 释放底层 provider 流（HTTP 连接）
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MeteredStreamTests -v`
Expected: PASS（3 用例）

- [ ] **Step 5: Run full metering suite**

Run: `.venv/bin/python -m pytest tests/test_metering.py -v`
Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): MeteredManagedClient streaming passthrough + settle-on-complete"
```

---

## Task 6: 连续缺失暂停（fail-closed 累犯 → ModelPausedError）

**Files:**
- Modify: `backend/metering.py`（逻辑已在 Task 4/5 落地；本任务补**断言行为**与边界，必要时小修）
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py（追加）
class MissCounterTests(MeteredNonStreamTests):
    def test_three_consecutive_misses_pause_model(self):
        c = self._client(None)  # 每次都缺 usage
        for _ in range(3):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 第 4 次：reserve 阶段即暂停
        with self.assertRaises(self.m.ModelPausedError):
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)

    def test_success_resets_miss_counter(self):
        miss_client = self._client(None)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 一次成功 settle 清零
        ok = self._client(_FakeUsage(prompt_tokens=1, prompt_cache_hit_tokens=0,
                                     prompt_cache_miss_tokens=1, completion_tokens=1))
        ok.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        # 再连续 2 次缺失仍不暂停（计数已清）
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        miss_client.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)  # 不抛

    def test_pause_is_per_model(self):
        c_miss = self._client(None)
        for _ in range(3):
            c_miss.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
        other = self._client(_FakeUsage(prompt_tokens=1, prompt_cache_hit_tokens=0,
                                        prompt_cache_miss_tokens=1, completion_tokens=1))
        other.chat.completions.create(model="Qwen/Qwen3-VL-8B-Instruct", messages=[], stream=False)  # 不抛

    def test_next_day_auto_resets_pause(self):
        # ✦ Codex BLOCKER：暂停后 reserve 在任何成功 settle 前就拦截 → 同键永不清零；
        # day 入键则次日自动清零。monkeypatch today_shanghai 模拟跨日（_reserve/_settle 均查模块级 today_shanghai）。
        c = self._client(None)
        orig = self.m.today_shanghai
        self.m.today_shanghai = lambda: "2026-06-22"
        try:
            for _ in range(3):
                c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
            with self.assertRaises(self.m.ModelPausedError):
                c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)
            self.m.today_shanghai = lambda: "2026-06-23"   # 次日
            c.chat.completions.create(model="deepseek-v4-pro", messages=[], stream=False)  # 不抛 = 自动清零
        finally:
            self.m.today_shanghai = orig
```

> 注：`_miss_counter` 是模块级全局；本测试类每个用例间需清零。在 `MissCounterTests.setUp` 末尾加 `self.m._miss_counter.clear()`（reload 已新建模块、但显式清零更稳）。

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MissCounterTests -v`
Expected: 多数应已 PASS（Task 4 已实现 `_bump_miss`/`_reset_miss`/reserve 暂停）；若 `test_success_resets` 因跨 client 共享计数未清而 FAIL，按 Step 3 修。

- [ ] **Step 3: Ensure reset semantics (if needed)**

确认 `_settle` 成功路径调用了 `_reset_miss(self.uid, model)`（Task 4 已含）。`MissCounterTests.setUp` 调用 `super().setUp()` 后追加 `self.m._miss_counter.clear()`。若 reset 缺失则补上。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::MissCounterTests -v`
Expected: PASS（3 用例）

- [ ] **Step 5: Commit**

```bash
git add backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): per-(uid,model) consecutive-miss pause + reset on success"
```

---

## Task 7: `wrap_client_for_billing` 工厂 + source-guard

**Files:**
- Modify: `backend/metering.py`
- Test: `tests/test_metering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metering.py（追加）
class WrapFactoryTests(MeteredNonStreamTests):
    def _settings(self, mode):
        class _S:  # 最小 settings 替身
            def __init__(self, mode): self.mode = mode
        return _S(mode)

    def test_managed_mode_wraps(self):
        raw = _FakeOpenAI(_FakeResp(None))
        wrapped = self.m.wrap_client_for_billing(raw, uid="u1", settings=self._settings("managed"))
        self.assertIsInstance(wrapped, self.m.MeteredManagedClient)

    def test_custom_mode_returns_raw_unwrapped(self):
        raw = _FakeOpenAI(_FakeResp(None))
        same = self.m.wrap_client_for_billing(raw, uid="u1", settings=self._settings("custom"))
        self.assertIs(same, raw)
```

- [ ] **Step 2: Write the source-guard test**

```python
# tests/test_metering.py（追加；锁死「managed 调用必经 MeteredManagedClient」）
import pathlib


class SourceGuardTests(unittest.TestCase):
    def _src(self, rel):
        return pathlib.Path(__file__).resolve().parent.parent.joinpath(rel).read_text(encoding="utf-8")

    def test_chat_handler_client_assigned_through_wrapper(self):
        # ✦ NIT：不只查字符串（死 import 也会过），断言 self.client 由 wrap_client_for_billing 赋值。
        src = self._src("backend/chat.py")
        self.assertRegex(src, r"self\.client\s*=\s*wrap_client_for_billing\(",
                         "ChatHandler.self.client 必须由 wrap_client_for_billing 赋值")

    def test_independent_review_client_returned_through_wrapper(self):
        src = self._src("backend/independent_review.py")
        self.assertRegex(src, r"return\s+wrap_client_for_billing\(",
                         "IndependentReviewAgent._build_client 必须 return wrap_client_for_billing(...)")
```

> 注：source-guard 在 Task 8/11 接线后才会真正变绿；本任务先让工厂用例通过，source-guard 两条预期此刻 FAIL，留作 Task 8/11 的验收锚点。可将其 `@unittest.expectedFailure` 临时标注，Task 8/11 完成后移除——或按执行顺序在 Task 8/11 step 内点名「source-guard 转绿」。

- [ ] **Step 3: Run factory test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metering.py::WrapFactoryTests -v`
Expected: FAIL（`wrap_client_for_billing` 不存在）

- [ ] **Step 4: Implement the factory**

```python
# backend/metering.py（追加）
def wrap_client_for_billing(raw_client, uid: str, settings):
    """managed → MeteredManagedClient（计费）；custom → 原样返回（不计费）。
    settings.mode 在 ChatHandler/Review 构造时已定（per-handler 固定）。"""
    if getattr(settings, "mode", "managed") == "managed":
        return MeteredManagedClient(raw_client, uid=uid)
    return raw_client
```

- [ ] **Step 5: Run factory test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metering.py::WrapFactoryTests -v`
Expected: PASS（2 用例）

- [ ] **Step 6: Commit**

```bash
git add backend/metering.py tests/test_metering.py
git commit -m "feat(w2b-b2): wrap_client_for_billing factory + source-guard anchors"
```

---

## Task 8: 接线 ChatHandler（包 client + 主聊天友好配额事件）

**Files:**
- Modify: `tests/test_chat_runtime.py:26`（`ChatRuntimeTests.setUp` —— 先于包裹做夹具适配）
- Modify: `backend/chat.py:405-412`（`__init__` 客户端构造）
- Modify: `backend/chat.py:2789`（include_usage managed-only）
- Modify: `backend/chat.py:2809-2828`（主流式 create 的异常处理）
- Modify: `backend/chat.py:3266` 附近（sync create 的异常处理）
- Test: `tests/test_chat_runtime.py`

> **⚠️ 为何 Step 0 必须先做（✦ Codex BLOCKER R1+R2+R3）**：现有 chat 测试用 `@mock.patch("backend.chat.OpenAI")`（patch **类**），包裹后 `self.client` = `MeteredManagedClient(mock, ...)`，于是 wrapper 的 reserve/settle 在**每个 managed chat 测试里都会跑**——撞三件事：① 访问 `accounts` 的 `usage_daily`/`users` 表，而 `ChatRuntimeTests.setUp` 从不 `init_db`（生产仅 `main.py:79` 建表）→ sqlite 报错；② mock 流无 usage → settle fail-closed + miss 计数 +1，**单个测试内 ≥3 次 usage-less create 即被 `ModelPausedError` 拦死**（Codex R3 实证 `test_system_notice_reset_between_turns` 有 4 次，`tests/test_chat_runtime.py:6796`，逐测枚举太脆）；③ 累积 fail-closed 成本可能撞默认 cap。**修法（robust 单点）**：base setUp 隔离 DB + init_db + **把 wrapper 的两道闸门在这些非计费单测里设为「永不触发」**——`mock.patch` `MAX_CONSECUTIVE_USAGE_MISS` 为巨大值 + 全局 cap 设巨大值。settle 仍真跑（写隔离 DB、无害），故 `_raw` 与 B2 settle 子类照常工作；**pause/quota 的真实行为仍由 `tests/test_metering.py` 覆盖**（独立 reload，不受此 patch 影响），无覆盖损失。这样**不必逐测改流、不碰任何现有 mock 流**。

- [ ] **Step 0: Prepare ChatRuntimeTests base fixtures (DB isolation + 闸门设为不触发)**

`tests/test_chat_runtime.py` 的 `ChatRuntimeTests.setUp`（现仅 patch curl_cffi）末尾追加：

```python
        import os, tempfile
        from backend import accounts
        self._billing_tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._billing_tmp.cleanup)
        self._prev_data_root = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._billing_tmp.name
        self.addCleanup(self._restore_data_root)
        accounts.init_db()                                   # 建 users/sessions/app_config/usage_daily
        accounts.set_config("global_daily_cap_micro_yuan", str(10**15))   # cap 巨大 → reserve 永不因成本拦
        import backend.metering as _m
        _m._miss_counter.clear(); self.addCleanup(_m._miss_counter.clear)
        self._pause_patch = mock.patch.object(_m, "MAX_CONSECUTIVE_USAGE_MISS", 10**9)  # 永不暂停
        self._pause_patch.start(); self.addCleanup(self._pause_patch.stop)

    def _restore_data_root(self):
        import os
        if self._prev_data_root is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._prev_data_root
```

（`data_root()`/`app_db_path()` 实时读 env，无需 reload；`_make_handler_with_project` 用显式 `projects_dir`、不读 data_root，故只迁移 accounts DB 落点。）跑全量确认基线仍绿：

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py -q`
Expected: PASS（包裹尚未做，仅加夹具、不应改变结果）。

- [ ] **Step 1: Write the failing test**

真实夹具（核对过 `tests/test_chat_runtime.py`）：`ChatRuntimeTests(unittest.TestCase)` 有 `_make_settings(**overrides)`（默认 `mode="managed"`、`managed_model="gemini-3-flash"`）、`_make_handler_with_project()`（建真 `SkillEngine` + `ChatHandler(settings, engine)`、置 `self.project_id`）、`_make_chunk(content=...)`、`_make_usage_chunk(**fields)`；文件顶部已 `from unittest import mock`。**子类化 `ChatRuntimeTests` 复用全部夹具**：

```python
# tests/test_chat_runtime.py（追加）
class B2BillingWiringTests(ChatRuntimeTests):
    def _make_custom_handler_with_project(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        projects_dir = Path(tmp.name) / "projects"
        engine = SkillEngine(projects_dir, self.repo_skill_dir)
        project = engine.create_project(
            name="demo", workspace_dir=str(Path(tmp.name) / "ws"),
            project_type="strategy-consulting", theme="t", target_audience="a",
            deadline="2026-04-01", expected_length="3000 words")
        handler = ChatHandler(
            self._make_settings(mode="custom", custom_api_base="https://api.example.com/v1",
                                custom_api_key="sk-x", custom_model="gpt-4o",
                                projects_dir=projects_dir),
            engine)
        self.project_id = project["id"]
        return handler

    def _capture_stream_kwargs(self, handler):
        captured = {}
        def _fake_create(**kwargs):
            captured.update(kwargs)
            return iter([self._make_chunk(content="完成")])
        with mock.patch.object(handler.client.chat.completions, "create", side_effect=_fake_create):
            list(handler.chat_stream(self.project_id, "你好", [], []))
        return captured

    def test_managed_client_is_metered(self):
        handler = self._make_handler_with_project()
        from backend.metering import MeteredManagedClient
        self.assertIsInstance(handler.client, MeteredManagedClient)

    def test_custom_client_is_raw(self):
        handler = self._make_custom_handler_with_project()
        from backend.metering import MeteredManagedClient
        self.assertNotIsInstance(handler.client, MeteredManagedClient)

    def test_managed_stream_requests_include_usage(self):
        handler = self._make_handler_with_project()
        captured = self._capture_stream_kwargs(handler)
        self.assertEqual(captured.get("stream_options"), {"include_usage": True})

    def test_custom_stream_omits_include_usage(self):
        # ✦ Codex BLOCKER：现 include_usage_requested 无条件 True，custom 也发 → 改 managed-only
        handler = self._make_custom_handler_with_project()
        captured = self._capture_stream_kwargs(handler)
        self.assertNotIn("stream_options", captured)

    def test_stream_quota_exceeded_yields_friendly_event(self):
        handler = self._make_handler_with_project()
        from backend import metering
        def _boom(**kwargs):
            raise metering.QuotaExceededError(used_micro=5_000_000, cap_micro=5_000_000)
        with mock.patch.object(handler.client.chat.completions, "create", side_effect=_boom):
            events = list(handler.chat_stream(self.project_id, "你好", [], []))
        self.assertTrue(any(e.get("type") == "error" and "额度" in str(e.get("data", ""))
                            for e in events))


# ✦ Codex NIT：子类化 ChatRuntimeTests 会继承其全部 test_*；按 repo 既有 pattern（test_chat_runtime.py:14364）
# 置空继承的 test_，避免 targeted class run 重跑整个父套件。
for _inh in dir(ChatRuntimeTests):
    if _inh.startswith("test_") and _inh not in B2BillingWiringTests.__dict__:
        setattr(B2BillingWiringTests, _inh, None)
del _inh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py::B2BillingWiringTests -v`
Expected: FAIL（client 未包 / custom 仍发 include_usage / 无友好事件）

- [ ] **Step 3: Wrap the client in ChatHandler.__init__**

`backend/chat.py:407-412`，把：

```python
        http_client = httpx.Client(timeout=120.0)
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            http_client=http_client,
        )
```

改为：

```python
        http_client = httpx.Client(timeout=120.0)
        raw_client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            http_client=http_client,
        )
        from backend.metering import wrap_client_for_billing
        # managed → MeteredManagedClient（reserve/settle/fail-closed）；custom → 原样。
        # 5 个调用点全用 self.client.chat.completions.create，包裹后零改动自动计费。
        self.client = wrap_client_for_billing(raw_client, uid=self.uid, settings=settings)
```

- [ ] **Step 3b: Gate include_usage on managed mode (✦ Codex BLOCKER)**

`backend/chat.py:2789`，现 `include_usage_requested = True` 对 custom 模式也发 `stream_options`，违反 spec §6.2「仅 managed」。改为：

```python
            include_usage_requested = self.settings.mode == "managed"
```

（被动计费下，include_usage 由调用点声明、wrapper 不注入；custom 不计费故不应请求 usage。`_should_retry_stream_without_usage` 失败回退逻辑不动。）

- [ ] **Step 4: Handle quota/pause errors in the streaming loop**

`backend/chat.py:2812-2818`，在主流式的 `except Exception as e:` 之前**先**截获配额类异常（不进重试、不当 provider error）。把 try 块改为：

```python
                try:
                    response = self.client.chat.completions.create(**request_kwargs)
                    break
                except (QuotaExceededError, ModelPausedError) as qe:
                    yield {"type": "error", "data": self._format_quota_error(qe)}
                    return
                except Exception as e:
                    if include_usage_requested and self._should_retry_stream_without_usage(e):
                        include_usage_requested = False
                        continue
                    if retry < 1:
                        time.sleep(2)
                        continue
                    # ...（原有 error yield 不变）
```

在 `chat.py` 顶部 import 区加：

```python
from backend.metering import QuotaExceededError, ModelPausedError
```

并加 helper（放在 ChatHandler 内，靠近 `_format_provider_error`）：

```python
    def _format_quota_error(self, err) -> str:
        from backend.metering import QuotaExceededError, ModelPausedError
        if isinstance(err, QuotaExceededError):
            used = err.used_micro / 1_000_000
            cap = err.cap_micro / 1_000_000
            return (f"今日额度已用尽（已用 ¥{used:.2f} / 上限 ¥{cap:.2f}），"
                    f"明日 0 点（北京时间）恢复。如需提额请联系管理员。")
        return "当前模型暂时不可用（多次未取得用量计数已保护性暂停），请稍后再试或联系管理员。"
```

- [ ] **Step 4c: Synchronous settle on early stream exit (✦ Codex BLOCKER)**

`backend/chat.py:2863` 的 `try: for chunk in response:`（含 `self._looks_like_self_correction_loop` 在 2879 提前 `break`）——提前 break/异常/return 都会**抛弃** wrapper 生成器，settle 只在 GC 时才跑 → **下一次 `create()` 的 reserve 在结算之前发生**（cumulative 少算）、且可能在错误时刻按该 uid 结算。给该 `try` 加 `finally` 同步关闭 `response`，触发 wrapper 的 `finally→settle`：

```python
            try:
                for chunk in response:
                    ...
            except Exception:
                ...                                  # 原有 provider error 处理不变
            finally:
                try:
                    response.close()                 # 任何退出都同步触发 wrapper settle（managed 下 response 是生成器、有 close）
                except Exception:
                    pass
```

（managed 模式 `response` = `MeteredManagedClient` 的 `_metered_stream` 生成器、有 `.close()`；custom 模式是 OpenAI `Stream`、也有 `.close()`；已结束的生成器再 close 是 no-op、不会二次 settle。）source-guard 锁死：

```python
# tests/test_chat_runtime.py（追加到 B2BillingWiringTests）
    def test_chat_stream_consumption_closes_response(self):
        # ✦ Codex NIT：不全局 grep `response.close()`（chat.py 别处已有无关 close，会假绿）；
        # 锚定主流式 `for chunk in response:` 后窗口内必须有 finally + response.close()。
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1].joinpath("backend/chat.py").read_text(encoding="utf-8")
        i = src.index("for chunk in response:")          # 主流式消费锚点（chat.py 仅此处用此短语）
        window = src[i:i + 3000]
        self.assertIn("finally:", window)
        self.assertIn("response.close()", window)
```

- [ ] **Step 5: Handle quota errors in the sync path**

`backend/chat.py:3266` 的 `response = self.client.chat.completions.create(**request_kwargs)` 外层包：

```python
                try:
                    response = self.client.chat.completions.create(**request_kwargs)
                except (QuotaExceededError, ModelPausedError) as qe:
                    # ✦ Codex BLOCKER：ChatResponse.system_notices 是 List[SystemNotice] 对象、非 List[str]；
                    # 友好提示放 content，notices 置 None（避 pydantic 校验失败）。
                    return {"content": self._format_quota_error(qe), "token_usage": None,
                            "system_notices": None}
```

（精确位置：找到 sync 路径 `sync_response`/`_chat_sync_unlocked` 内该 create 调用，按其局部返回结构对齐 `content`/`token_usage` 字段名。）

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py::B2BillingWiringTests -v`
Expected: PASS（5 用例）

- [ ] **Step 7: Run source-guard (now green for chat.py)**

Run: `.venv/bin/python -m pytest tests/test_metering.py::SourceGuardTests::test_chat_handler_client_assigned_through_wrapper -v`
Expected: PASS（移除该条的 `expectedFailure` 标注若有）

- [ ] **Step 8: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py tests/test_metering.py
git commit -m "feat(w2b-b2): wire ChatHandler to metered client + friendly quota events"
```

---

## Task 9: 验证压缩 + 视觉自动计费 + DeepSeek 兼容不破

**Files:**
- Test only: `tests/test_chat_runtime.py`（压缩 / 视觉 / DeepSeek 既有用例不回归）

> 压缩（`chat.py:850`）与视觉（`chat.py:446`）都走 `self.client.chat.completions.create`，Task 8 包裹后**自动计费**，无需改调用点。本任务**实证**这两条路径真的 settle 进 `usage_daily`（✦ Codex BLOCKER：不能只 isinstance，那是同义反复）——patch **底层 raw** `handler.client._raw.chat.completions.create` 返回带 usage 的响应，让 wrapper 的 settle 真实跑，断言 `usage_daily` 增量。

- [ ] **Step 1: Write the failing/locking test**

基类 `ChatRuntimeTests.setUp`（Task 8 Step 0 已加 `CRA_DATA_ROOT` 隔离 + `init_db`）→ 本类直接用 accounts。patch **底层 raw** `handler.client._raw.chat.completions.create`（`MeteredManagedClient` 暴露 `_raw`）让 wrapper 的 settle 真跑：

```python
# tests/test_chat_runtime.py（追加）
class B2BillingSettleTests(ChatRuntimeTests):
    def test_compaction_call_settles_usage(self):
        from types import SimpleNamespace
        from backend import accounts
        import backend.metering as m
        handler = self._make_handler_with_project()   # managed → client 是 MeteredManagedClient
        usage = SimpleNamespace(prompt_tokens=100, prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=100, completion_tokens=50)
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="摘要"))], usage=usage)
        with mock.patch.object(handler.client._raw.chat.completions, "create", return_value=resp):
            handler._summarize_messages([{"role": "user", "content": "x"}])
        row = accounts.get_usage_today(handler.uid, m.today_shanghai())
        self.assertEqual(row["cache_miss_tokens"], 100)   # 压缩真的 settle 了
        self.assertGreater(row["cost_micro_yuan"], 0)

    def test_vision_transcribe_settles_usage(self):
        from types import SimpleNamespace
        from backend import accounts
        import backend.metering as m
        handler = self._make_handler_with_project()
        handler.settings.vision_enabled = True
        usage = SimpleNamespace(prompt_tokens=200, prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=200, completion_tokens=30)
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="图片转述"))], usage=usage)
        with mock.patch.object(handler.client._raw.chat.completions, "create", return_value=resp):
            out = handler._vision_transcribe("data:image/png;base64,AAAA", "image/png")
        self.assertIn("图片转述", out)
        row = accounts.get_usage_today(handler.uid, m.today_shanghai())
        self.assertEqual(row["cache_miss_tokens"], 200)   # 视觉真的 settle 了


# ✦ Codex NIT：置空继承的 test_（pattern: test_chat_runtime.py:14364）
for _inh in dir(ChatRuntimeTests):
    if _inh.startswith("test_") and _inh not in B2BillingSettleTests.__dict__:
        setattr(B2BillingSettleTests, _inh, None)
del _inh
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py::B2BillingSettleTests -v`
Expected: PASS（Task 8 包裹后压缩/视觉自动经 wrapper settle）

- [ ] **Step 3: Run DeepSeek compatibility regression**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py -k "deepseek or tool_call or reasoning" -v`
Expected: PASS（metering 只加 reserve/settle，未碰 provider message/tool-call/`reasoning_content`/`tool_choice` 序列化）

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_runtime.py
git commit -m "test(w2b-b2): lock compaction+vision metered via wrapped client; deepseek compat green"
```

---

## Task 10: 接线 IndependentReviewAgent（加 uid + 包 client + 流式请求 include_usage + settle）

**Files:**
- Modify: `tests/test_independent_review.py:42`（`IndependentReviewAgentTests.setUp` —— 先于包裹做夹具适配）
- Modify: `backend/independent_review.py:242-262`（`__init__` 加 uid、`_build_client` 包裹、流式 create 加 include_usage）
- Modify: `backend/independent_review.py:469-487`（请求 kwargs + 配额异常）
- Modify: `backend/main.py:665`（review 端点构造 agent 时传 `scope.uid`）
- Test: `tests/test_independent_review.py`、`tests/test_main_api.py`

> **⚠️ 为何 Step 0 必须先做（✦ Codex BLOCKER）**：现有 review 测试用 `mock.patch("backend.independent_review.OpenAI")`（patch 类），包裹后 `_build_client()` 返 `MeteredManagedClient(mock,...)`，reserve/settle 在每个 managed review 测试里都跑。①`IndependentReviewAgentTests.setUp` 无 `init_db` → DB 报错；② `test_run_emits_progress_events` 在**一个测试里跑 5 条 mock 流**（`tests/test_independent_review.py:191`）、每条无 usage → 第 4 条 reserve 即 `ModelPausedError`。**修法同 Task 8 Step 0（robust 单点）**：base setUp 隔离 DB + init_db + 把两道闸门设为不触发（巨大 cap + 巨大 `MAX_CONSECUTIVE_USAGE_MISS`）。**不碰任何共享流构造器**（避免给 `_stream_*` 加 usage chunk 影响既有断言的风险）；settle 仍真跑、写隔离 DB 无害。

- [ ] **Step 0: Prepare IndependentReviewAgentTests base fixtures（DB 隔离 + 闸门不触发）**

`tests/test_independent_review.py` 的 `IndependentReviewAgentTests.setUp`（现仅设 `repo_skill_dir`）改为：

```python
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
        import os, tempfile
        from backend import accounts
        self._billing_tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._billing_tmp.cleanup)
        self._prev_data_root = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._billing_tmp.name
        self.addCleanup(self._restore_data_root)
        accounts.init_db()
        accounts.set_config("global_daily_cap_micro_yuan", str(10**15))
        import backend.metering as _m
        _m._miss_counter.clear(); self.addCleanup(_m._miss_counter.clear)
        self._pause_patch = mock.patch.object(_m, "MAX_CONSECUTIVE_USAGE_MISS", 10**9)
        self._pause_patch.start(); self.addCleanup(self._pause_patch.stop)

    def _restore_data_root(self):
        import os
        if self._prev_data_root is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._prev_data_root
```

跑全量确认基线仍绿（包裹尚未做，仅夹具变更）：

Run: `.venv/bin/python -m pytest tests/test_independent_review.py -q`
Expected: PASS

- [ ] **Step 1: Write the failing test**

真实夹具（核对过 `tests/test_independent_review.py`）：`IndependentReviewAgentTests(unittest.TestCase)` 有 `_make_engine_project_and_agent()`（返 `(engine, project, project_dir, agent)`，agent = `IndependentReviewAgent(skill_engine=engine, settings=settings)`）。**子类化复用**，`mode`/`uid` 在拿到 agent 后显式设（`_build_client` 在调用时才读 `self.settings.mode` / `self.uid`）：

```python
# tests/test_independent_review.py（追加）
class B2ReviewBillingTests(IndependentReviewAgentTests):
    def test_review_agent_managed_client_is_metered(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        agent.settings.mode = "managed"; agent.uid = "u1"
        from backend.metering import MeteredManagedClient
        self.assertIsInstance(agent._build_client(), MeteredManagedClient)

    def test_review_agent_custom_client_is_raw(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        agent.settings.mode = "custom"; agent.uid = "u1"
        from backend.metering import MeteredManagedClient
        self.assertNotIsInstance(agent._build_client(), MeteredManagedClient)

    def test_review_agent_accepts_uid_param(self):
        # __init__ 新增 uid（默认 "local" 向后兼容既有构造）
        from backend.independent_review import IndependentReviewAgent
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        a2 = IndependentReviewAgent(skill_engine=engine, settings=agent.settings, uid="u9")
        self.assertEqual(a2.uid, "u9")

    def test_review_run_meters_usage_and_requests_include_usage(self):
        # ✦ NIT：B2 验收门「审查」覆盖——run() 经 metered client、请求 include_usage、usage_daily 记**真** usage
        # （非 fail-closed 封顶）。复用 test_run_emits_progress_events 已验证的 5-response 成功路径，逐条补真 usage 末包。
        from types import SimpleNamespace
        from backend import accounts
        import backend.metering as m
        from backend.independent_review import CANONICAL_REVIEW_PATH
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        agent.settings.mode = "managed"; agent.uid = "u1"
        store, run_id = self._claim_store(project)

        def _with_usage(stream):   # builder 默认不带 usage → 补真 usage 末包（choices=[] 解析器透明跳过）
            chunks = list(stream)
            chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(
                prompt_tokens=10, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=10, completion_tokens=5)))
            return iter(chunks)

        responses = [_with_usage(s) for s in (
            self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "c1"),
            self._stream_single_tool_call("read_file", {"file_path": "plan/analysis-notes.md"}, "c2"),
            self._stream_single_tool_call("read_file", {"file_path": "content/report_draft_v1.md"}, "c3"),
            self._stream_single_tool_call("write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()}, "c4"),
            self._stream_text("审查完成"),
        )]
        with mock.patch("backend.independent_review.OpenAI") as mo:
            mo.return_value.chat.completions.create.side_effect = responses
            list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))
            kwargs = mo.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs.get("stream_options"), {"include_usage": True})
        row = accounts.get_usage_today("u1", m.today_shanghai())
        self.assertGreater(row["cache_miss_tokens"], 0)
        self.assertLess(row["cache_miss_tokens"], 256000)   # 真 usage，非 fail-closed 256k 封顶


# ✦ Codex NIT：置空继承的 test_（pattern: test_chat_runtime.py:14364）
for _inh in dir(IndependentReviewAgentTests):
    if _inh.startswith("test_") and _inh not in B2ReviewBillingTests.__dict__:
        setattr(B2ReviewBillingTests, _inh, None)
del _inh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_independent_review.py::B2ReviewBillingTests -v`
Expected: FAIL（`IndependentReviewAgent.__init__` 不收 uid / client 未包）

- [ ] **Step 3: Add uid + wrap client**

`backend/independent_review.py:242-257`：

```python
    def __init__(self, skill_engine: SkillEngine, settings: Settings, uid: str = "local"):
        self.skill_engine = skill_engine
        self.settings = settings
        self.uid = uid

    def _build_client(self):
        http_client = httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)
        )
        raw = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.api_base,
            http_client=http_client,
        )
        from backend.metering import wrap_client_for_billing
        return wrap_client_for_billing(raw, uid=self.uid, settings=self.settings)
```

- [ ] **Step 4: Request include_usage on the review stream**

`backend/independent_review.py:469-476` 的 `request_kwargs`，在 `stream: True` 后追加（被动计费需调用点自报）：

```python
        request_kwargs = {
            "model": model,
            "messages": messages,
            "tools": INDEPENDENT_REVIEW_TOOLS,
            "stream": True,
        }
        if self._should_send_explicit_tool_choice(model):
            request_kwargs["tool_choice"] = "auto"
        if self.settings.mode == "managed":
            request_kwargs["stream_options"] = {"include_usage": True}   # 计费末包取 usage
```

- [ ] **Step 5: Integrate quota into the EXISTING error path + thread uid at the endpoint (✦ Codex BLOCKER)**

现实 `backend/independent_review.py:481-487` 的 create 已有完整 errored 通道（`store.set_errored` + `yield {"type":"error","detail":...}` + `return`）。**复用它**——在通用 `except Exception` **之前**加专门的配额 except（不要新写一个 returning-only 的 `_emit_quota_notice`）。把：

```python
            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                if store is not None and run_id is not None:
                    store.set_errored(store_key, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": f"模型调用失败：{str(exc)}"}
                return
```

改为：

```python
            try:
                response = client.chat.completions.create(**request_kwargs)
            except (QuotaExceededError, ModelPausedError) as qe:
                if store is not None and run_id is not None:
                    store.set_errored(store_key, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": _quota_notice(qe)}
                return
            except Exception as exc:
                if store is not None and run_id is not None:
                    store.set_errored(store_key, run_id, snapshot_now(iteration))
                yield {"type": "error", "detail": f"模型调用失败：{str(exc)}"}
                return
```

在 `independent_review.py` 顶部 import 区加 `from backend.metering import QuotaExceededError, ModelPausedError`，并加**模块级纯文本** helper（非方法、无副作用）：

```python
def _quota_notice(err) -> str:
    from backend.metering import QuotaExceededError
    if isinstance(err, QuotaExceededError):
        return (f"今日额度已用尽（已用 ¥{err.used_micro/1_000_000:.2f} / "
                f"上限 ¥{err.cap_micro/1_000_000:.2f}），明日 0 点（北京时间）恢复。")
    return "审查模型暂时不可用（多次未取得用量计数已保护性暂停），请稍后再试。"
```

`backend/main.py:665` 现为 `agent = IndependentReviewAgent(scope.engine, load_settings(scope.uid))`，改为传 uid：

```python
                agent = IndependentReviewAgent(scope.engine, load_settings(scope.uid), uid=scope.uid)
```

（其 `run(...)` 已分离 `store_key=review_key` 与 canonical `project_id`，本改动只加 uid，不动 store_key/project_id 语义。）

- [ ] **Step 5b: Synchronous settle on early review-stream exit (✦ Codex BLOCKER 同 chat)**

`backend/independent_review.py:511` 的 `try: for chunk in response:`（含 `is_cancelled()` 在 513 提前 return）同样给 `try` 加 `finally` 关闭 `response`：

```python
            try:
                for chunk in response:
                    ...
            except Exception as exc:
                ...                                  # 原有 set_errored + yield error 不变
            finally:
                try:
                    response.close()
                except Exception:
                    pass
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_independent_review.py::B2ReviewBillingTests -v`
Expected: PASS（4 用例，含 run() 计费集成测试）

- [ ] **Step 7: Run source-guard (now green for independent_review.py)**

Run: `.venv/bin/python -m pytest tests/test_metering.py::SourceGuardTests -v`
Expected: PASS（两条均绿）

- [ ] **Step 8: Run review + main api regression**

Run: `.venv/bin/python -m pytest tests/test_independent_review.py tests/test_main_api.py -v`
Expected: PASS（B1 既有审查 / API 用例不回归；注意 `IndependentReviewAgent` 新 uid 参数默认 `"local"` 向后兼容既有构造）

- [ ] **Step 9: Commit**

```bash
git add backend/independent_review.py backend/main.py tests/test_independent_review.py
git commit -m "feat(w2b-b2): meter IndependentReviewAgent (uid + wrapped client + include_usage)"
```

---

## Task 11: Endpoint 预检 + 友好配额响应（/api/chat 非流式）

**Files:**
- Modify: `backend/main.py:465-491`（`POST /api/chat`）
- Test: `tests/test_main_api.py`

> 流式路径的配额已在 Task 8 转成 SSE 友好事件。本任务给**非流式** `/api/chat` 一个干净的预检（额度已尽时不半启动 turn），并把 `QuotaExceededError`/`ModelPausedError` 映射成友好 200（非 500）。

- [ ] **Step 1: Write the failing test**

真实夹具（核对过 `tests/test_main_api.py`）：`_LocalMockEngineMixin._install_mock_engine()` 装 MagicMock 引擎进 `_engines["local"]` + auth off + echo `get_project_record`；`self.client = TestClient(main_module.app)`，uid="local"。叠加 `CRA_DATA_ROOT` 隔离让 accounts 写隔离 DB：

```python
# tests/test_main_api.py（追加）
class B2ChatQuotaTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        import os, tempfile
        from backend import accounts
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self._prev_root = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name; self.addCleanup(self._restore_root)
        accounts.init_db()
        self.accounts = accounts
        self._install_mock_engine()
        self.client = TestClient(main_module.app)

    def _restore_root(self):
        import os
        if self._prev_root is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._prev_root

    def test_chat_returns_friendly_when_quota_exhausted(self):
        import backend.metering as metering
        self.accounts.set_config("global_daily_cap_micro_yuan", "100")
        self.accounts.add_usage("local", metering.today_shanghai(), 100, 0, 0, 0)  # 已达上限
        resp = self.client.post("/api/chat", json={"project_id": "demo", "message_text": "hi"})
        self.assertEqual(resp.status_code, 200)        # 友好返回、非 500、不 build handler
        self.assertIn("额度", resp.json()["content"])    # 提示在 content（system_notices 是对象模型、置 None）
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main_api.py::B2ChatQuotaTests -v`
Expected: FAIL（无预检 / 500）

- [ ] **Step 3: Add pre-check (BEFORE building handler) + error mapping in /api/chat**

`backend/main.py:465-491`。预检放在 `require_project` 之后、**`get_chat_handler` 之前**（额度尽时不建 handler、不半启动 turn；mid-turn 由 wrapper reserve 兜底）：

```python
@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat(request: Request, chat_request: ChatRequest, uid: str = Depends(get_current_uid)):
    scope = require_project(chat_request.project_id, uid)
    # 预检（建 handler 前）：仅 managed 模式做 ¥ 门禁——✦ Codex BLOCKER：custom 不计费/不受 ¥ 门禁（§6.4），
    # 不可因 managed 额度尽而拦 custom 聊天。B2 production 仍 managed-forced，此 gate 为正确性 + B3 预留。
    import backend.accounts as accounts
    from backend import metering
    cap = accounts.get_effective_daily_cap_micro(scope.uid)
    used = accounts.get_usage_today(scope.uid, metering.today_shanghai())["cost_micro_yuan"]
    if load_settings(scope.uid).mode == "managed" and used >= cap:
        # ✦ Codex BLOCKER：system_notices 是 List[SystemNotice]、非 List[str] → 置 None，提示放 content。
        return ChatResponse(
            content=(f"今日额度已用尽（已用 ¥{used/1_000_000:.2f} / 上限 ¥{cap/1_000_000:.2f}），"
                     f"明日 0 点（北京时间）恢复。"),
            token_usage=None, system_notices=None)
    handler = get_chat_handler(scope.uid, scope.project_id)
    try:
        result = await asyncio.to_thread(handler.chat, scope.project_id, chat_request.message_text,
                                         chat_request.attached_material_ids,
                                         [i.model_dump() for i in chat_request.transient_attachments],
                                         client_message_id=chat_request.client_message_id)
    except (metering.QuotaExceededError, metering.ModelPausedError) as qe:
        return ChatResponse(content=handler._format_quota_error(qe), token_usage=None,
                            system_notices=None)
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    return ChatResponse(content=result["content"], token_usage=result.get("token_usage"),
                        system_notices=result.get("system_notices"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main_api.py::B2ChatQuotaTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main_api.py
git commit -m "feat(w2b-b2): /api/chat quota pre-check + friendly exhausted response"
```

---

## Task 12: `GET /api/auth/me` 加 cost 字段

**Files:**
- Modify: `backend/main.py:247-255`
- Test: `tests/test_auth_api.py`

- [ ] **Step 1: Write the failing test**

真实夹具（核对过 `tests/test_auth_api.py`）：`AuthApiTestBase` setUp 用隔离 `CRA_DATA_ROOT` + `CRA_INVITE_CODE="JOIN"` reload(config/accounts/main)、`self.client = TestClient(m.app)`、auth_required=True。注册需 `invite_code="JOIN"`、密码下限同既有用例 `"pw-123456"`：

```python
# tests/test_auth_api.py（追加）
class MeCostFieldsTests(AuthApiTestBase):
    def test_me_includes_today_cost_and_cap(self):
        self.client.post("/api/auth/register",
                         json={"username": "bob", "password": "pw-123456", "invite_code": "JOIN"})
        self.client.post("/api/auth/login", json={"username": "bob", "password": "pw-123456"})
        import backend.accounts as accounts, backend.metering as metering
        uid = accounts.get_user_by_username("bob")["uid"]
        accounts.add_usage(uid, metering.today_shanghai(), 1_500_000, 0, 0, 0)  # ¥1.5
        body = self.client.get("/api/auth/me").json()
        self.assertAlmostEqual(body["today_cost_yuan"], 1.5, places=4)
        self.assertIn("daily_cap_yuan", body)
        self.assertGreater(body["daily_cap_yuan"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::MeCostFieldsTests -v`
Expected: FAIL（me 无 cost 字段）

- [ ] **Step 3: Add cost fields to /api/auth/me**

`backend/main.py:247-255`：

```python
@app.get("/api/auth/me")
def auth_me(request: Request, uid: str = Depends(get_current_uid)):
    import backend.accounts as accounts
    from backend import metering
    used = accounts.get_usage_today(uid, metering.today_shanghai())["cost_micro_yuan"]
    cap = accounts.get_effective_daily_cap_micro(uid)
    cost_fields = {"today_cost_yuan": round(used / 1_000_000, 4),
                   "daily_cap_yuan": round(cap / 1_000_000, 4)}
    if uid == "local" and not getattr(request.app.state, "auth_required", True):
        return {"uid": "local", "username": "本地用户", "is_admin": False,
                "must_change_password": False, **cost_fields}
    rec = accounts.get_user_by_uid(uid)
    if not rec:
        raise HTTPException(status_code=401, detail="未登录")
    return {"uid": uid, "username": rec["username"], "is_admin": rec["is_admin"],
            "must_change_password": rec["must_change_password"], **cost_fields}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py::MeCostFieldsTests -v`
Expected: PASS

- [ ] **Step 5: Run full auth api regression**

Run: `.venv/bin/python -m pytest tests/test_auth_api.py -v`
Expected: PASS（B1 既有 me/login/register 用例不回归——`local` 合成分支仍返回 username/is_admin）

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(w2b-b2): /api/auth/me returns today_cost_yuan + daily_cap_yuan"
```

---

## Task 13: 前端账号块显示今日额度

**Files:**
- Create: `frontend/src/utils/quotaFormat.js`
- Modify: `frontend/src/components/Sidebar.jsx`（账号块）+ `frontend/src/App.jsx`（消费 me 的 cost 字段）
- Test: `frontend/tests/quotaFormat.test.mjs` + Sidebar source-guard

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/tests/quotaFormat.test.mjs
import { test } from 'node:test';
import assert from 'node:assert';
import { formatYuan, quotaLabel, quotaRatio } from '../src/utils/quotaFormat.js';

test('formatYuan renders 2 decimals with ¥', () => {
  assert.equal(formatYuan(1.5), '¥1.50');
  assert.equal(formatYuan(0), '¥0.00');
});

test('quotaLabel shows used / cap', () => {
  assert.equal(quotaLabel(1.5, 5), '今日 ¥1.50 / ¥5.00');
});

test('quotaRatio clamps 0..1', () => {
  assert.equal(quotaRatio(2, 5), 0.4);
  assert.equal(quotaRatio(10, 5), 1);
  assert.equal(quotaRatio(1, 0), 0); // cap=0 防除零
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && node --test tests/quotaFormat.test.mjs`
Expected: FAIL（`quotaFormat.js` 不存在）

- [ ] **Step 3: Implement quotaFormat.js**

```javascript
// frontend/src/utils/quotaFormat.js
export function formatYuan(n) {
  const v = Number.isFinite(n) ? n : 0;
  return `¥${v.toFixed(2)}`;
}

export function quotaLabel(used, cap) {
  return `今日 ${formatYuan(used)} / ${formatYuan(cap)}`;
}

export function quotaRatio(used, cap) {
  if (!cap || cap <= 0) return 0;
  return Math.max(0, Math.min(1, used / cap));
}
```

- [ ] **Step 4: Wire into Sidebar account block + App me consumption**

`App.jsx` 起手 `GET /api/auth/me` 的 state 里保留 `today_cost_yuan` / `daily_cap_yuan`，透传给 `Sidebar`。`Sidebar.jsx` 账号块（用户名 + 登出处）加一行：

```jsx
import { quotaLabel } from '../utils/quotaFormat.js';
// ...账号块内：
{me && typeof me.daily_cap_yuan === 'number' && (
  <div className="text-xs text-gray-400 mt-1">
    {quotaLabel(me.today_cost_yuan ?? 0, me.daily_cap_yuan)}
  </div>
)}
```

- [ ] **Step 5: Write the source-guard test**

```javascript
// frontend/tests/sidebarQuota.source.test.mjs
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf-8');

test('Sidebar account block renders quota label', () => {
  assert.match(src, /quotaLabel/, 'Sidebar 账号块应显示今日额度');
});
```

- [ ] **Step 6: Run frontend tests to verify they pass**

Run: `cd frontend && node --test tests/quotaFormat.test.mjs tests/sidebarQuota.source.test.mjs`
Expected: PASS

- [ ] **Step 7: Build to confirm no breakage**

Run: `cd frontend && npm run build`
Expected: vite build 成功

- [ ] **Step 8: Commit**

```bash
git add frontend/src/utils/quotaFormat.js frontend/src/components/Sidebar.jsx frontend/src/App.jsx frontend/tests/quotaFormat.test.mjs frontend/tests/sidebarQuota.source.test.mjs
git commit -m "feat(w2b-b2): sidebar account block shows today quota (¥used / ¥cap)"
```

---

## Task 14: 全量回归 + cutover report + 文档同步

**Files:**
- Create: `docs/superpowers/cutover_report_2026-06-22_w2b-b2.md`
- Modify: `docs/current-worklist.md`、`CLAUDE.md`、`AGENTS.md`（若存在 W2-B 段）
- Test: 全套后端 + 前端

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（4 个已知 mac-realpath 环境差异用例可 FAIL，与 B2 无关；其余全绿）。记录实际 passed 数。

- [ ] **Step 2: Run the full frontend suite + build**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: PASS + build 绿

- [ ] **Step 3: Run targeted DeepSeek + metering + isolation regression**

Run: `.venv/bin/python -m pytest tests/test_metering.py tests/test_chat_runtime.py tests/test_independent_review.py tests/test_tenant_isolation.py tests/test_main_api.py tests/test_auth_api.py -q`
Expected: PASS（计费覆盖面 + 跨租户隔离 + DeepSeek 兼容 都绿）

- [ ] **Step 4: Write cutover report**

`docs/superpowers/cutover_report_2026-06-22_w2b-b2.md`，按既有 cutover 模板写：交付物（metering.py + usage_daily + 5 调用点覆盖 + me cost + 前端额度条）、关键设计（被动计费 wrapper / `finally` 同步结算 + `response.close()` / fail-closed 含视觉显式锚 / Asia-Shanghai 日界 / **custom B1/B2 managed-forced、wrapper 分支为 B3 预留**）、验收门对账（spec §13 B2：vision/压缩/审查/中断都计——逐条指向测试；custom 不计为单元级、production 不可达注明）、回归数据、已知限制（流式中断未见 usage = fail-closed 计入、从未消费的流不结算、reserve→settle 微 TOCTOU 软帽容忍、单进程 miss-counter）、未做（admin 调 cap 端点 + custom 真激活 = B3）。

- [ ] **Step 5: Update worklist + CLAUDE.md**

- `docs/current-worklist.md`：W2 条把 B2 状态从「下一步」改「✅ 实施完成」，下一步指向 B3。
- `CLAUDE.md` 加「## W2-B/B2 中央计费」段（metering.py 唯一出口、usage_daily、被动 include_usage、fail-closed、custom 不计、日界 Asia/Shanghai、cap 解析优先级、source-guard 锁死）。

- [ ] **Step 6: Commit**

```bash
git add docs/ CLAUDE.md AGENTS.md
git commit -m "docs(w2b-b2): cutover report + worklist/CLAUDE.md sync — central billing + per-user quota"
```

---

## Self-Review（写完后对 spec 复核）

**1. Spec 覆盖（§6 / §5.4 / §7 me / §13 B2 验收门）：**
- §6.1 三档计价 → Task 1（精确数值锁测）✓
- §6.2 中央 client 唯一出口 + 覆盖 chat/压缩/视觉/审查 → Task 4-10（+ source-guard）✓
- §6.2 流式 include_usage（仅 managed）→ 主聊天已有 + Task 10 给审查补 ✓（被动模式，调用点自报）
- §6.3 reserve/settle/fail-closed/连续 3 次缺失暂停/跨日 → Task 3-6 ✓
- §6.4 custom 不计费不门禁 → Task 7 工厂 + Task 8/10 mode 分支（**单元级**：B1/B2 production managed-forced，custom 生产不可达，为 B3 预留；Task 11 precheck 已 gate 在 `mode=="managed"`）⚠️
- §5.4 usage_daily 表 + 整数微元 + 原子累加 → Task 3 ✓
- §5.3/§7 me 返回 today_cost/daily_cap → Task 12 ✓
- §13 B2 验收门「vision/压缩/审查/中断 都计 + custom 不计」→ Task 5（中断）/9（vision+压缩）/10（审查）/7（custom）✓

**2. Placeholder 扫描：** 每个 code step 有完整代码；helper（`_format_quota_error`/`_emit_quota_notice`/`set_user_daily_cap_micro`）均给出实现。无 TBD。✓

**3. 类型一致性：** `price_micro_yuan(model,hit,miss,completion,pricing)`、`extract_billing_usage→BillingUsage(hit,miss,completion)`、`add_usage(uid,day,cost,hit,miss,output)`、`get_usage_today→dict[cost_micro_yuan,...]`、`MeteredManagedClient(raw,uid,model_pricing)`、`wrap_client_for_billing(raw,uid,settings)`、`QuotaExceededError(used_micro,cap_micro)`/`ModelPausedError(model)` —— 全任务签名一致。✓

**4. 风险提示（写进 cutover「已知限制」，非阻塞）：**
- 流式调用无论自然结束 / provider 异常 / 消费方中断(GeneratorExit) 都 `finally` 结算一次；中断前未见 usage = fail-closed 保守封顶计入（spec §6.3，不留漏计后门）。极端：create() 返回的流从未被迭代则不结算（reserve 已跑），属「从未消费」容忍边界。
- reserve→settle 间同 uid 并发可略超一轮（软帽容忍，spec §11）；
- `_miss_counter` 进程级 + day 入键（单进程部署成立；多 worker 需迁 DB——B3/部署期考虑）；
- **原生搜索 `self.client.responses.create`（chat.py:5414）经 wrapper `__getattr__` 透传裸 client、不计费**——但仅 `api.openai.com` + `gpt-*`（custom OpenAI）才触发，managed 模式根本不走该路径，故 B2 无实际计费缺口；列此为显式声明。
- admin 调 cap / 轮换全局 cap 的端点是 B3，本 plan 只读 `users.daily_cost_micro_yuan` + `app_config.global_daily_cap_micro_yuan`。

**5. Codex R1 修复对账（CHANGES-REQUESTED → 已全修）：**
- BLOCKER 流式 provider 异常未 fail-closed → Task 5 `_metered_stream` 改 `finally` 恰好结算一次（自然结束/provider 异常/GeneratorExit 一律 fail-closed）+ 新增 `test_stream_provider_error_midstream_fail_closed`（R2 进一步把 GeneratorExit 也并入 fail-closed，见下 R2/R3 段）。
- BLOCKER fail-closed 硬编 128000 → Task 4 改 `resolve_context_policy(model).effective_context_limit`（deepseek=256k），测试数值 384000→768000。
- BLOCKER `_miss_counter` 暂停后永不清零 → Task 4/6 day 入键 + 新增 `test_next_day_auto_resets_pause`。
- BLOCKER `include_usage_requested=True` 非 managed-only → Task 8 Step 3b 改 `= self.settings.mode=="managed"` + custom 流式无 stream_options 测试。
- BLOCKER review 配额仅 `_emit_quota_notice` 不落盘不 yield → Task 10 改用既有 `store.set_errored + yield {"type":"error","detail":...}` 通道 + 模块级 `_quota_notice`。
- BLOCKER 测试引用不存在 fixture → Task 8/9/10/11/12 全改真实 unittest 夹具（`_make_handler_with_project`/`_make_engine_project_and_agent`/`_LocalMockEngineMixin`/`AuthApiTestBase`）。
- BLOCKER 压缩/视觉同义反复测试 → Task 9 改 patch 底层 raw + 断言 `usage_daily` 真增量。
- NIT `.responses` 破裂 → Task 4 加 `__getattr__` 透传 + 测试；NIT source-guard 弱 → Task 7 改断言赋值/return 形态。

**6. Codex R2 修复对账（CHANGES-REQUESTED → 已全修）：**
- BLOCKER `system_notices=["quota_exceeded"]` 类型非法（`ChatResponse.system_notices` 是 `List[SystemNotice]` 对象，models.py:184/192）→ Task 8 sync + Task 11 endpoint 改 `system_notices=None`、提示放 `content`，测试断言改查 content。
- BLOCKER 包裹破坏既有 chat/review 测试（patch `OpenAI` 类 → wrapper 跑 reserve/settle → 无 init_db 报错 + usage-less 流累积 miss 致暂停）→ Task 8 Step 0 / Task 10 Step 0：base setUp 隔离 `CRA_DATA_ROOT`+`init_db`+清 `_miss_counter`；review 共享流构造器 `_stream_text`/`_stream_single_tool_call` 各补末尾 usage chunk（chat 测试每个 ≤2 create、清零即足）。
- BLOCKER 消费方异常经 `GeneratorExit` 到 wrapper 被当『不计费』漏掉 → Task 5 `_metered_stream` 改 `finally` 恰好结算一次（自然结束/provider 异常/GeneratorExit 一律 fail-closed）；`test_stream_interrupt_before_usage_fail_closed` 断言中断未见 usage = 768000。
- NIT review 计费无集成证明 → Task 10 加 `test_review_run_meters_usage_and_requests_include_usage`（run() 真跑 + 断言 `stream_options` + `usage_daily` 增量）。

**7. Codex R3 修复对账（CHANGES-REQUESTED → 已全修）：**
- BLOCKER「chat 测试每个 ≤2 create」claim 假（`test_system_notice_reset_between_turns` 有 4 次 usage-less create，`tests/test_chat_runtime.py:6796`，逐测清零不够）→ Task 8/10 Step 0 改 **robust 单点**：base setUp 把 wrapper 两道闸门设为不触发（`mock.patch.object(metering, "MAX_CONSECUTIVE_USAGE_MISS", 10**9)` + 全局 cap `10**15`），不必逐测改流；pause/quota 真行为仍由 `test_metering.py` 独立覆盖。同时**撤销**给 review `_stream_*` 加 usage chunk 的方案（避免影响既有断言）；Task 10 集成测试改在本测试内联补真 usage 末包 + 断言 `cache_miss_tokens < 256000`（证真 usage 非 fail-closed）。
- NIT 已知限制/R1 fix-log 残留「GeneratorExit 不计费」与 Task 5 新 `finally` 设计矛盾 → 已改为「中断未见 usage = fail-closed 计入」。

**8. Codex R4（全新独立线程 · 对抗式红队 · CHANGES-REQUESTED → 已全修）：**
- BLOCKER custom 生产不可达却claim §6.4 覆盖（`normalize_settings_payload` 无条件 `mode="managed"`，config.py:325-328）→ 全程改诚实框定：B1/B2 managed-forced、custom 分支为 B3 预留 + 单元验证（设计约定 + Self-Review §6.4 + cutover 均注明）。
- BLOCKER `/api/chat` precheck 无条件拦会误伤 custom（§6.4）→ Task 11 gate 在 `load_settings(scope.uid).mode=="managed"`。
- BLOCKER 提前 break（chat.py:2879 自纠正）抛弃 wrapper 生成器 → 下次 reserve 在结算前 → Task 8 Step 4c / Task 10 Step 5b：消费 `try` 加 `finally: response.close()` 同步触发结算；`_metered_stream` finally 也 close raw_stream；source-guard 锁死。
- BLOCKER 视觉模型 fail-closed 非模型专属（Qwen3-VL 落 unknown fallback）→ Task 1 加 `MANAGED_FAILCLOSED_CEILING`（Qwen3-VL=32768）+ Task 4 `_settle` 用之 + `test_vision_model_fail_closed_uses_explicit_ceiling`。
- NIT 子类继承父 test_ 致重跑整套 → 三个 B2 子类后加 repo 既有置空 pattern（test_chat_runtime.py:14364）。
- NIT cutover 文案残留「主动中断流不计费」→ 已改。

---

## Execution Handoff

详见执行选择（写完后由用户定）。
