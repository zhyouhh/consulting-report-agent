# Phase 2a Cutover Review

**总轮数**: 4
**决策一致率**: 50% (2/4)

## Cutover Metrics

| 指标 | 值 | 阈值 | 通过？ |
|---|---|---|---|
| 一致率 | 50% | ≥ 95% | 未达标 |
| 不一致 case | 2 | 全部需人工标注 | (人工 review) |
| blocked_missing_tag turn | 1 | 0 | ✗ |
| 受控 fallback case (append_report_draft) | 2 | (受控范畴，不计入 missing) | - |
| 异常数（new_channel_exception + draft_decision_exception） | 0 | 0 | ✓ |

## 不一致 case 详情

- turn_id=ae8cfd07-493c-446e-a88c-1e6960451636 | hash=5f553bb2 | old=no_write → new=require | reason=old.mode=no_write, new.mode=require
- turn_id=4deff3ce-9fb6-418b-8fcf-b500f5aee3db | hash=64f4f155 | old=no_write → new=require | reason=old.mode=no_write, new.mode=require

## Exception 详情

