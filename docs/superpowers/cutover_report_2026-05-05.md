# Phase 2a Cutover Review

**总轮数**: 4
**决策一致率**: 25% (1/4)

## Cutover Metrics

| 指标 | 值 | 阈值 | 通过？ |
|---|---|---|---|
| 一致率 | 25% | ≥ 95% | 未达标 |
| 不一致 case | 3 | 全部需人工标注 | (人工 review) |
| blocked_missing_tag turn | 3 | 0 | ✗ |
| 受控 fallback case (append_report_draft) | 0 | (受控范畴，不计入 missing) | - |
| 异常数（new_channel_exception + draft_decision_exception） | 0 | 0 | ✓ |

## 不一致 case 详情

- turn_id=317203b1-34a6-4786-8f7c-7d93bd2e7f1c | hash=5f553bb2 | old=no_write → new=require | reason=old.mode=no_write, new.mode=require
- turn_id=2d2193d3-f721-4ecc-8eb8-8e43c20e8807 | hash=cc500a48 | old=reject → new=no_write | reason=old.mode=reject, new.mode=no_write
- turn_id=bff24782-8ba9-4c22-a46f-ca5ac0ac3899 | hash=64f4f155 | old=no_write → new=require | reason=old.mode=no_write, new.mode=require

## Exception 详情

