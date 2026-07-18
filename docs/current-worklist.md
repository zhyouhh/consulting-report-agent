# Current Worklist

本文件只维护仍需行动的事项；完成记录归 `docs/worklist-history.md` 与
`docs/superpowers/cutover_report_*.md`。新增条目必须写清优先级或触发条件，完成后移出本文件。

最后核对：2026-07-18。

## 优先处理

1. **深度研究质量拉升专项**
   - 来源：谭进 2026-07-17 反馈 #3。
   - 目标：补强 S3 分析深度、S4 行业洞察与证据综合，并评估受控多轮 deepen。
   - 下一步：另立 spec；先拿真实报告做 baseline/对照，不直接堆 prompt。

2. **目录固化用户复验**
   - 用修复后的样例分别在 WPS 与 Word 验证：目录可点击跳转、无“无法打开指定文件”、
     目录后无空白页。
   - 代码与服务器结构/渲染级验证已通过；这里只差用户端应用复验。

3. **Windows 正式包烟测**
   - 重跑 `build.bat`，确认 `templates/docx/consulting_v1.docx` 进入 `_internal/templates/docx/`，
     并在打包态完成一次真实 docx 导出。

## 有触发条件再做

- **S4 图表真模型 GUI E2E**：让 `deepseek-v4-pro` 在真实 S4 分别调用一次
  `create_chart` / `create_diagram`；在下一次图表相关改动或用户报告失败时执行。
- **超长报告独立审查**：当前 >100k 字友好失败；真实长文需求重复出现后，再设计章节切片 +
  五维发现聚合，不提前做 map-reduce。
- **ChatPanel 池淘汰**：当前会话内常驻。只有单会话项目数增长造成可观测内存问题时，再设计 LRU。
- **计费 in-flight 预扣**：日额度当前是软帽；只有出现可观测并发超额时再引入 reserve。
- **多 worker**：生产保持单进程单 worker。扩 worker 前必须先完成跨进程锁/共享状态，覆盖
  registry、materials、conversion refs、review store、登录限流、runtime host 与 quota 状态。
- **上下文保留/压缩改造**：保留待定、未启动。现有 `conversation_state.json` 已把成功的
  read/fetch/write 结果作为去重工作记忆跨轮注入；不得再以“工具结果读完即弃”的错误前提立项。

## 工程债

- **阶段 checkpoint 事务性**：把 `outline_confirmed_at` + `__methodology_snapshot` 两阶段写改为
  一次原子 raw 写；backfill 增加窄粒度锁/CAS。当前单用户低概率 crash 窗口，非线上阻断。
- **stage-advance-gates G/H**：复核回退后残留 `content/*.md` 的一致性策略；补 S1 回退后的
  `next_stage_hint`，或证明新流程已覆盖。
- **前端/打包小债**：输入框 id/name 可访问性、`npm audit` high、Vite 大 chunk、
  PyInstaller conda warning、Pydantic v2 deprecation。拆包以不牺牲首屏/状态稳定为前提。
- **项目表单清理**：继续核对截止日期、材料/备注等字段是否真实被工作流消费；按“用户目标”删废项，
  不为保留字段而强行增加功能。
- **搜索池额度基线**：若监控需要更准确，再从 provider dashboard 把 Serper/Exa 启用记账前的
  累计用量填入 `quota.baseline_used`；不填时剩余额度偏乐观。
- **安全后置增强**：custom API 做 pinned-IP-with-SNI transport 以彻底收口 DNS rebinding；
  多 worker 后把 `_LOGIN_FAILS` / `_RUNTIME_ALLOWED_HOSTS` 等进程状态迁到共享存储。
- **导出/资产二期**：多套排版模板、签名版式、图表原地编辑、assets 进文件树，均需真实用户需求再立项。
- **draw.io skill 评估**：只有现有 `create_diagram` 无法覆盖明确场景时再评估，不单独为技术选型立项。
