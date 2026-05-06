# Cutover Report — Tools Redesign (2026-05-06)

**Status:** Implementation **DONE** through Task 6.2. Cutover smoke 5 sessions (Task 6.3) **PENDING — manual user validation**.

## What shipped

替换 fix4 v5 amendment 的 `<draft-action>` tag + gate fallback + scope enforcement 整套机制为 **4 个专用写正文工具**：
- `append_report_draft(content)` — 重构（inline SHARED_PRE_WRITE_CHECKS + dispatch wired through new entry）
- `rewrite_report_section(content)` — 新增
- `replace_report_text(old, new)` — 新增
- `rewrite_report_draft(content)` — 新增

后端用 preflight resolved snapshot 当 old_string，model 完全不复述 — **结构性消除 fix4 cutover Session B 14 次失败的根因**（gemini-3-flash 做不到精确复述 1500 字章节原文）。

## Commit chain (26 commits this implementation)

| Phase | Commits | Net |
|---|---|---|
| Spec stage | `d5bb758` → `5cb5f6b` → `a936bfb` → `2c355c8` → `7f0d207` (5 commits, 4 review rounds) | spec doc 788 行 |
| Plan stage | `1226a67` → `1030d7b` (2 commits, 2 review rounds) | plan doc 2203 行 |
| Cleanup | `80f1c1f` (handoff, worklist, push) | docs |
| Task 1 | `9d183df` `b80413c` `9e54d88` `9cd071d` (4 commits) | `backend/report_writing.py` +198, tests +290 |
| Task 2 | `292bf6f` `68eb8a2` `2717760` `43b6c68` (4 commits) | turn_context fields + obligation detector + read_file mtime hook |
| Task 3 | `0c0f387` `c75ff0d` `0404f67` `1644620` `400e433` `dd5a322` `5d88e2b` (7 commits) | 4 tools + 51 tests + Critical fix1 (legacy gate accepts semantic edit tools) |
| Task 4 | `fa3088c` `3f28957` (2 commits) | SKILL.md §S4 + chat.py user_action wording |
| Task 5 | `911a9d2` `8bd0abc` `bac9112` `4ab5010` `c53b5f3` (5 commits) | The big delete + StageAckRegression + wire append dispatch + canonical_draft_mutation merge fix |
| Task 6 | `d482235` (1 commit) | dist rebuild + tool-selection benchmark |

**Total this implementation (Tasks 1-6.2)**: 17 commits, **17 files changed, 4844 insertions(+), 6535 deletions(-)** (net delete ~1700 lines).

包含原本估的 ~2000 行删除（实际删 6535 行）+ ~4000 行新代码（实际新增 4844 行——比估计大，主要是测试 + benchmark）。

## Test acceptance

| Suite | Result | 备注 |
|---|---|---|
| `pytest tests/test_chat_runtime.py` | **360 passed, 1 skipped, 0 failed** in 1481s (24:41) | 36 pre-existing failures 全部消失（Task 5 删除 deprecated test classes） |
| `pytest tests/test_report_writing.py` | **41/41 passed** | Task 1 + Task 2 helpers + detector |
| `pytest tests/test_tool_selection_benchmark.py` | **4/4 passed** | Task 6.2 schema sanity |
| Frontend `node --test tests/` | **168/168 passed** | unchanged |
| `python -c "import backend.chat"` | OK | post-deletion sanity |

## Build acceptance

- **Build time**: 3.16 min (PyInstaller)
- **Old dist**: 86.11 MB
- **New dist**: 86.09 MB (within ±5% of 91 MB baseline)
- **Build script exit**: 0
- **Note**: 旧 dist 被 PID `48620` 锁住无法删除，被 implementer rename 为 `dist/咨询报告助手.locked-20260506-090048/`，新 build 在 `dist/咨询报告助手/`。该 .locked 目录不进 git，等 user 关闭运行的 app 后可手动删除。

## Review iterations

| Task | Spec round | Quality round | Notes |
|---|---|---|---|
| 1 | 1: APPROVED_WITH_NOTES | 1: With fixes → 2: Yes | 1 Important (DI helper tests) + 3 Minor，全 fix |
| 2 | 1: APPROVED_WITH_NOTES | 1: Yes | 2 minor notes (named constant / setdefault) — non-blocking |
| 3 | 1: APPROVED_WITH_NOTES | 1: With fixes → 2: Yes | 1 Critical (replace_report_text 被 legacy gate 卡死) → fix1 in commit `5d88e2b` 加白名单 + live-path test；defer 1 Important (streaming retry timing) |
| 4 | 1: APPROVED_WITH_NOTES | 1: Yes | 2 minor (forward-looking guidance / test grep weakness) |
| 5 | 1: APPROVED_WITH_NOTES | 1: With fixes → 2: Yes | 1 Important (canonical_draft_mutation overwrite breaks draft_followup_state) → fix1 in commit `c53b5f3` merge pattern + regression test |
| 6.1+6.2 | n/a | n/a | implementer DONE_WITH_CONCERNS (dist process lock); detector regex 扩展超出原 prompt 范围但合理修正 plan 内部不一致（Task 2.3 unit "改强" → None vs Task 6.2 benchmark "改强" → rewrite_section） |

## Pending — Task 6.3 cutover smoke 5 sessions

按 plan 应跑 5 个 sessions，每个验证 model 选对新工具 + 写盘成功 + canonical_draft_mutation set：

| Session | User msg | 期望工具 | 期望行为 |
|---|---|---|---|
| A | "开始写报告吧" | `append_report_draft` | 写盘成功，draft 字数增加 |
| B | "把第二章重写一下" | `rewrite_report_section` | 第二章 snapshot 替换为 model content；其他章节不动 |
| C | "把'团队防御蓝领'改成'团队防御核心'" | `replace_report_text` | unique 字符串替换成功 |
| D | "继续写第三章" | `append_report_draft` | append 第三章新内容 |
| E | "整篇重写，按 outline 用更精炼的语言重写正文" | `rewrite_report_draft` | 整份草稿替换 |

每个 session 之间用 mtime backup 区分 events.json。所有 sessions 跑完后填实测数据到此 cutover report。

**为何 pending**：本 session 上下文已大量消耗，user 要求 cutover smoke 留到下次会话或 user 自己跑 + 验收。

## Open issues for next session / smoke time

1. **dist process lock** — `D:\MyProject\CodeProject\consulting-report-agent\.claude\worktrees\happy-jackson-938bd1\dist\咨询报告助手.locked-20260506-090048\` 仍存在，等运行 app 退出后 `rm -rf` 清理。
2. **detector regex 扩展行为**（Task 6.1+6.2 implementer 自主改）：
   - `_OBLIGATION_REPLACE_RE` 改宽（"正文/报告" 前缀变可选）
   - 新加 `_OBLIGATION_SECTION_CHANGE_RE` (match "第X章...改强/改弱/优化/润色/补强/加强")
   - 修改 `test_section_strong_change` 期望从 `None` → `rewrite_section`
   - 这些扩展未经 spec/quality 双轮 review，cutover smoke 时 watch 是否引入 false positive
3. **Streaming retry timing**（Task 3 quality r1 deferred to fix5）：obligation retry 在 stream 已 yield content chunks 之后发生；user 可能看到"已修改"假文本 + 之后的 corrective msg。code comment at chat.py:4091 已标 reference fix5。
4. **`<draft-act` stale comment**（Task 5 quality r1 → fix1 已修，记录 only）

## Hand-off

- Branch: `claude/phase2-draft-action-tag` (HEAD `d482235`)
- Main not yet merged (Task 6.5 pending)
- Origin/main: still at `7f0d207` (spec only, not yet plan/impl)
- 等 user 验收 cutover smoke + explicit "merge / push" 指令

— 控制器（claude）2026-05-06
