"""Generate Phase 2a cutover review markdown from conversation_state.json files.

Usage: python tools/draft_decision_compare_report.py state1.json state2.json ...
"""
import json
import sys
from pathlib import Path


def load_events(state_paths: list[Path]) -> list[dict]:
    events = []
    for p in state_paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            events.extend(data.get("events", []))
        except Exception as e:
            print(f"[warn] {p}: {e}", file=sys.stderr)
    return events


def generate_report(state_paths: list[Path]) -> str:
    events = load_events(state_paths)
    compare_events = [e for e in events if e.get("type") == "draft_decision_compare"]
    exception_events = [e for e in events if e.get("type") == "draft_decision_exception"]
    
    total = len(compare_events)
    if total == 0:
        return "# Cutover Review\n\n**没有 compare 事件**——是否启用了 Phase 2a 并行？"
    
    agreed = sum(1 for e in compare_events if e.get("agreement"))
    disagreed = total - agreed
    fallback_count = sum(1 for e in compare_events if e.get("fallback_used"))
    blocked_missing = sum(1 for e in compare_events if e.get("blocked_missing_tag"))
    inline_exceptions = sum(1 for e in compare_events if e.get("new_channel_exception"))
    standalone_exceptions = len(exception_events)
    total_exceptions = inline_exceptions + standalone_exceptions
    
    agreement_rate = (agreed / total) * 100 if total else 0
    
    lines = [
        "# Phase 2a Cutover Review",
        "",
        f"**总轮数**: {total}",
        f"**决策一致率**: {agreement_rate:.0f}% ({agreed}/{total})",
        "",
        "## Cutover Metrics",
        "",
        f"| 指标 | 值 | 阈值 | 通过？ |",
        f"|---|---|---|---|",
        f"| 一致率 | {agreement_rate:.0f}% | ≥ 95% | {'✓' if agreement_rate >= 95 else '未达标'} |",
        f"| 不一致 case | {disagreed} | 全部需人工标注 | (人工 review) |",
        f"| blocked_missing_tag turn | {blocked_missing} | 0 | {'✓' if blocked_missing == 0 else '✗'} |",
        f"| 受控 fallback case (append_report_draft) | {fallback_count} | (受控范畴，不计入 missing) | - |",
        f"| 异常数（new_channel_exception + draft_decision_exception） | {total_exceptions} | 0 | {'✓' if total_exceptions == 0 else '✗'} |",
        "",
        "## 不一致 case 详情",
        "",
    ]
    for e in compare_events:
        if not e.get("agreement"):
            lines.append(f"- turn_id={e.get('turn_id')} | hash={e.get('user_message_hash')[:8]} | old={e.get('old_decision', {}).get('mode')} → new={e.get('new_decision', {}).get('mode')} | reason={e.get('divergence_reason')}")
    
    lines.extend([
        "",
        "## Exception 详情",
        "",
    ])
    for e in compare_events:
        if e.get("new_channel_exception"):
            ex = e["new_channel_exception"]
            lines.append(f"- inline | turn={e.get('turn_id')} | stage={ex.get('stage')} | {ex.get('message')[:80]}")
    for e in exception_events:
        lines.append(f"- standalone | turn={e.get('turn_id')} | stage={e.get('stage')} | {e.get('exception_class')}: {e.get('exception_message')[:80]}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print("Usage: python tools/draft_decision_compare_report.py state1.json [state2.json ...]")
        sys.exit(1)
    print(generate_report(paths))
