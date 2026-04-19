# Desktop Debug Backlog

最后更新：2026-04-17

## 归档说明

- 从 `2026-04-17` 开始，本文件不再维护“当前待解决问题”。
- 当前仍需行动的事项，统一维护在 [current-worklist.md](D:/CodexProject/Consult%20report/consulting-report-agent/docs/current-worklist.md)。
- 本文件只保留历史调试脉络，避免和当前 worklist 双份漂移。

## 历史问题去向

1. 聊天正文对用户而言几乎不是流式输出
- 去向：已转入 [current-worklist.md](D:/CodexProject/Consult%20report/consulting-report-agent/docs/current-worklist.md) 的“流式输出体感”
- 当前口径：代码层修复已完成，等待新包实机验证

2. `web_search` 工具不可用 / `401`
- 去向：已关闭
- 结论：旧 Tavily 依赖路径已退出；当前正式路径是内置 managed search pool

3. Skill 门禁不够硬，会擅自继续推进
- 去向：已关闭
- 结论：后端正式写入门禁、证据门槛和正文写入限制已补齐

4. 右侧阶段不同步
- 去向：已关闭
- 结论：`stage-gates.md` 缺失回填与模板 `outline` 误判问题都已修复

5. 新建项目表单信息利用率不足
- 去向：已转入 [current-worklist.md](D:/CodexProject/Consult%20report/consulting-report-agent/docs/current-worklist.md) 的“新建项目表单与废 UI 整理”

## 备注

- 如果后面再出现新的纯调试线索，先记到这里；
- 但一旦确认是正式待办，就转移到 `current-worklist.md`，不要双写。
