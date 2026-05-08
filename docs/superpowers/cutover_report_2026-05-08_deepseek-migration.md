# Cutover Report — DeepSeek Migration + Toolset Redesign (2026-05-08)

**Status:** Implementation **DONE** on branch `codex/deepseek-migration-toolset-plan`. Commit 3 final quality review (Epicurus) approved with no Critical / Important / Minor findings.

## What shipped

DeepSeek V4 Pro Migration + Toolset Redesign 已完成三段式 cutover：

- 默认 managed 模型迁移到 `deepseek-v4-pro`，并补上 stale `managed_model` 自愈与 `tier_1m_eff_256k` 能力档位。
- Plan/spec 文档落地，明确迁移目标：把正文写作从旧 canonical draft obligation/control layer 迁到更直接的工具集路径。
- Commit 1 建立 migration toolset foundation。
- Commit 2 停止流量进入 legacy report draft tools。
- Commit 3 删除 legacy report draft control layer，并通过 7 类 grep gate 确认旧符号清理干净。

用户影响：默认模型路径切到 DeepSeek V4 Pro；正文写作链路减少旧控制层分支，降低小模型误入旧工具、旧 gate 或旧字段的概率。实现影响：旧 obligation detector / guard / section resolver 等控制面被移除，代码路径更短，但仍保留必要的测试与打包门禁。

## Commit chain

| Phase | Commits | Net |
|---|---|---|
| Commit 0 history | `06779b1` `0b8b968` `8b3ad16` | 默认模型 `deepseek-v4-pro`、stale `managed_model` heal、`tier_1m_eff_256k`、README + proxy contract 中最后两个默认模型引用清理 |
| Plan/spec docs | `d7afadb` `aafc225` | DeepSeek migration implementation plan + worklist sync |
| Commit 1 | `69730c7` | Add migration toolset foundation |
| Commit 2 | `118f383` | Cut traffic from legacy report draft tools |
| Commit 3 | `9a59955` | Delete legacy report draft control layer |

## Net change summary

| Range | Scope | Shortstat |
|---|---|---|
| `8b3ad16..9a59955` | Commit 0 后到 Commit 3，包含 plan/spec docs | **26 files changed, 8391 insertions(+), 2098 deletions(-)** |
| `69730c7^..9a59955` | Implementation Commit 1-3 | **25 files changed, 4611 insertions(+), 2078 deletions(-)** |

主要净变化：

- 新增/调整 migration toolset 相关实现与测试。
- 旧 report draft control layer 从运行路径和源码符号层面删除。
- 文档侧补齐 DeepSeek migration plan/spec/cutover 证据链。
- `dist\咨询报告助手\` 已通过 Windows build 重建。

## Test / build acceptance

| Suite | Result |
|---|---|
| Backend fast: `.venv\Scripts\python -m pytest tests/ -q` | **834 passed, 1 skipped, 3 deselected, 13 warnings, 22 subtests passed** in 630.01s (0:10:30) |
| Backend including slow: `.venv\Scripts\python -m pytest tests/ -m "" -q` | **837 passed, 1 skipped, 13 warnings, 22 subtests passed** in 644.32s (0:10:44) |
| Frontend: `cd frontend && node --test tests/` | **183 passed** |
| Windows build: `.\build.bat` | **completed successfully**; output directory `dist`, package `dist\咨询报告助手\` rebuilt |
| `git diff --check` | **passed**; only CRLF normalization warnings |

Build warnings observed, all non-blocking for this cutover:

- npm audit: 1 existing high severity warning.
- Vite: chunk size >500k warning.
- PyInstaller / Anaconda assumption warnings.
- `pycparser` hidden-import warnings.

## Legacy cleanup gate

Strict grep gates were run against 7 legacy tool/control categories. All had no output (`rg` exit 1):

| Category | Result |
|---|---|
| Old tool names | clean |
| Old guard functions | clean |
| `detect_canonical_draft_write_obligation` | clean |
| Legacy keyword constants | clean |
| Legacy `turn_context` fields | clean |
| Single-dict mutation field | clean |
| `resolve_section_target` / `_SECTION_PREFIX_RE` | clean |

Interpretation: Commit 3 did not merely stop using the old control path; the old symbols targeted by the cleanup gate are absent from the checked source surface.

## E2E / smoke status

验收状态：代码路径已有单测 / 集成 / 打包覆盖；完整桌面手工 E2E 留给 2026-05-09 手工验收。

What is covered by evidence:

- Backend fast and slow suites passed.
- Frontend Node test suite passed.
- Windows package rebuilt successfully through `.\build.bat`.
- Legacy cleanup grep gates passed.
- `git diff --check` passed.

What is **not** independently proven in this session:

- Packaged desktop UI manual click-through.
- Real `/api/chat` streaming behavior through the packaged UI.
- Full 7-scenario manual chat E2E in the rebuilt desktop app.

Important note: `tests/smoke_packaged_app.py` is a packaged API-level smoke script and explicitly does not call `/api/chat`; it should not be treated as proof that UI / streaming / real chat gates passed.

## Known limitations / follow-up

1. **Partial obligation retry limitation retained** — current cutover does not claim to solve the existing partial retry behavior completely.
2. **Manual packaged UI/chat smoke still pending** — the rebuilt package should be accepted only after user-facing desktop chat scenarios are run manually.
3. **Build warnings can be tracked later** — npm audit high severity warning and Vite chunk warning remain non-blocking but visible.

## Hand-off

- Branch: `codex/deepseek-migration-toolset-plan`
- Current HEAD: `9a59955 Delete legacy report draft control layer`
- Do not push automatically; push remains user-controlled.
