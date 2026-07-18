# Cutover Report — 输出预算自适应 + 截断分诊 + 汇报轮疫苗（2026-07-19）

## 背景（生产事故 2026-07-18）

admin 项目「猪猪侠与喜羊羊管理制度」出现模型「说一句话就停」与「假报修改完成」。
调查结论（详见 memory `project-cra-incident-20260718-stall-hallucination`）：

- 直接原因：`context_policy` 旧公式 `min(8_192, 20%)` 让每次请求 `max_tokens=8_192`；
  整篇重写 `edit_file` 的参数（1 万+ 汉字 new_string，reasoning tokens 同计）撞上限被
  `finish_reason=length` 掐断在 JSON 字符串中间。newapi 计费日志两次实锤 completion 恰好
  = 8192（14:39:56 / 14:45:33 CST）。
- 误诊放大：后端从不读 `finish_reason`，把截断当「上游合并畸形条目」，corrective 让模型
  "每次只调一个工具"（答非所问）→ 模型反复被吞后假报成功/光说不做，坏范例自我强化。
- 次生问题：S5 汇报轮模型把注入的审查报告当「审查通过」，在用户从未表态时
  `advance_stage(review_passed_at)` 成功推进 S7。
- 排除项：newapi 网关与 Opencode GO 渠道无过错（实测 `max_tokens=131072` 放行、官渠也
  接受 51_200）；deepseek-v4-pro 流量 2026-07-08 起全走渠道 61 属刻意省成本路由，7-11/12
  同渠道运行良好。

## 变更

1. **输出预算自适应**（设计经三版演进：抬上限 → 白名单/能力位【codex 两轮否掉泄漏】→
   用户拍板「统一乐观 + 运行时自适应」终版）：
   - `context_policy.py`：`min(65_536, max(2_048, 20%))` 统一乐观；新
     `conservative_output_budget_policy()`（8_192 + compress_threshold 同步回落）。
   - `chat.py`：确定性 4xx 且高预算 → 降档 8_192 重试一次（流式/非流式同构）；
     **成功确认制**落进程级缓存（无关 4xx 连败不落缓存）；缓存键 managed 全局共享、
     custom 带 `uid + sha256(api_key)[:16]` 能力身份隔离；base `rstrip("/")` 归一。
2. **截断分诊**：流式循环逐 chunk 捕获 `finish_reason`（per-iteration 重建）；
   工具名合法 + JSON 断裂 + length → 专用隔板与「拆小修改分次提交」corrective；
   未知工具名优先维持合并畸形分支。`_SYNTHETIC_BARRIER_NOTES` 增至 3 条。
3. **汇报轮疫苗**：`independent_review_done` prompt 追加「报告不代表用户认可/本轮不得
   宣布通过或推进阶段/等用户明确表态」；关键词级测试锁定。
   `review_passed_at` 不收权（用户拍板保留说一句 OK 即推进的体验）。

## 验证

- 全量 backend：**1877 passed**，仅剩 7 个干净 HEAD 可复现的 Windows 环境固有失败
  （TocFixation×5 依赖 POSIX killpg/LibreOffice、SharedStateConcurrency×2 os.replace 语义）。
- 新回归：乐观矩阵/保守回落/length 分诊×跨迭代不残留/未知名优先/流式+非流式成功确认制/
  无关 400 防污染/缓存键隔离矩阵/4xx fail-fast 新契约/疫苗关键词。
- Codex（gpt-5.6-sol high，同线程 5 轮「审→修→再审」）：R1 BLOCKER=65_536 泄漏 custom →
  白名单；R2 BLOCKER=白名单仍按模型名泄漏 → 能力位；R3（设计切自适应后）BLOCKER=无关 400
  污染进程级缓存+无用户维度；R4 BLOCKER=custom 缓存键缺 uid/凭据指纹；R5 **APPROVED**
  （两条测试补强 NIT 当场补齐；「首轮拟合一次性偏差」「冷启动无 single-flight」注明接受）。
- 前端零改动，未跑前端链路。

## 部署（kr-web-01，2026-07-19 01:10 CST）

- 回滚点：`/opt/consulting-report-agent/.rollback-20260719-outputbudget/`（4 文件带属性）。
- file-push 4 文件（chat.py / context_policy.py / 两测试）SHA-256 双端对账一致 →
  远端 venv ast 语法预检 → 原子 mv → 清 `__pycache__` 对应 pyc → systemd 重启。
- 健康：服务 active、本机 `127.0.0.1:8888` 200、公网 `https://consulting.z0y0h.work` 200、
  journal 启动干净；生产语义探针：51_200/204_800 ↔ 降档 8_192/230_400、隔板 3 条、疫苗在位。
- 运维日志已记 `VPS-fix-private/notes/kr-web-01.md`。

## 遗留 / 未处理

- 「猪猪侠」报告本身：独立审查的 14 条意见一条未落（模型当时假报完成），阶段被误推进 S7；
  需用户在产品内让模型逐条修改（新代码下 edit_file 通路已验证可用），或手动回退 checkpoint。
- 官渠 57 与渠道 61 的 priority 路由维持现状（用户确认为刻意省成本）。
- 若弱端点冷启动并发探测成为可观测问题，再评估 per-key single-flight（worklist 触发条件项）。
