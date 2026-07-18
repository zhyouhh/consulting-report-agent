# Cutover：拆除关键词意图门槛与草稿版本恢复

日期：2026-07-18

状态：已实现、全量回归通过、独立红队 `APPROVE`、已部署 `kr-web-01`

## 用户结果

- 用户可以用自然语言要求修改或整篇重写正文，不再需要复述“全文重写”等指定词语。
- 正文与其他正式内容只在 S4–S7 可写；S0–S3 保持只读，已归档项目需先撤销归档回到 S7。
- AI 写入、工作区手动保存和界面可编辑状态使用同一阶段判定，不存在旁路。
- 已有正文在覆盖前自动保留字节级版本；助手可以列出近期版本并恢复指定版本。
- 整篇重写使用稳定的 Markdown 结构锚点，不再要求模型把整份旧正文回贴成匹配字符串。

原计划：`docs/superpowers/plans/2026-07-18-remove-keyword-intent-gates-and-draft-versioning.md`。

## 实现摘要

### A. 单一阶段权限源

- `SkillEngine.formal_content_write_block_guidance()` 同时决定是否允许写入及被拒时的下一步指引。
- S4、S5、S6、S7 且已确认大纲时放行；S0–S3 和 `done` 按当前状态给出可执行指引。
- `ChatHandler._non_plan_write_block_guidance()` 与
  `report_writing.check_report_writing_stage()` 仅保留为共享判定源的薄 facade。
- HTTP 用户保存与工作区 `editable` 复用同一判定，九种状态组合有一致性回归测试。

### B. 删除关键词门槛

- 删除正文生成、修改、整篇重写、否定短语和混合意图的正则分类及消费路径。
- 删除基于当轮关键词、短确认或历史消息的写入授权，不再把用户意图建模为“说中口令”。
- 工具说明、系统指令和用户可见报错同步去除指定词语要求；用户要求暂停时仍由通用写入纪律约束。
- DeepSeek tools 序列化、`reasoning_content`、null 字段与重试边界未改。

### C. 可逆草稿写入

- `SkillEngine.write_file()` 和 `user_write_file()` 是正文覆盖的统一快照挂载点。
- 已有 `content/report_draft_v1.md` 在主写入前按原始 bytes 快照到
  `content/.draft_history/`；首次成稿不创建空版本。
- 快照失败时主写入 fail-closed；主写失败时清理本次快照并保留原异常；主写成功后
  best-effort 保留最近 40 份。
- 文件名使用 Windows 安全的 UTC 时间戳与定宽序号；版本 id 经严格解析，拒绝路径穿越。
- `.draft_history` 不进入工作区文件列表、审查、导出或字数统计。

### D. 恢复与整篇重写

- 新增 `restore_report_draft`：无参数列出版本，传入版本 id 后先快照当前正文，再原子恢复目标 bytes。
- 恢复写入和普通写入走同一阶段门、锁、快照、元数据与审计账本。
- 整篇重写以正文首个 H1 结构锚点识别；旧全文字节匹配仍兼容，形状错误只说明正确工具用法。

## 独立红队

独立只读红队共三轮：

1. 首轮发现 HTTP 手动保存正文仍可绕过 S0–S3/归档态阶段门，且工作区 `editable` 未同步。
   修复后补齐 HTTP、workspace 与 AI 工具的状态矩阵。
2. 第二轮发现 append/edit/restore 仍通过另一条阶段检查，存在未来判定漂移风险。修复为
   `SkillEngine` 单一真值，并增加 facade 哨兵与九态等价测试。
3. 第三轮复核无 BLOCKER、MAJOR 或 MINOR，结论为 `APPROVE`。

## 验证证据

- 后端全量：`1876 passed, 1 skipped, 4 deselected, 238 subtests passed`。
- 前端全量：`606 passed`，0 fail。
- 前端生产构建：通过；bundle 仍为 `index-DlBZZnSg.js` / `index-DKcAfNcX.css`。
- 独立红队末轮 targeted：`12 passed, 27 subtests passed`。
- Python `py_compile` 与 `git diff --check`：通过。

## 部署与冒烟

部署目标：`kr-web-01`，systemd `consulting-report.service`，单进程单 worker。

1. 备份五个运行文件到 `/opt/cra-rollback-20260718-182342-draft-versioning`，并生成
   `SHA256SUMS.before`。
2. 通过 file-push 上传 `backend/chat.py`、`backend/main.py`、
   `backend/report_writing.py`、`backend/skill.py`、`skill/SKILL.md` 到独立 staging。
3. staged Python 文件通过 `py_compile`；本地与远端五个 SHA-256 逐一一致。

   | 运行文件 | SHA-256 |
   |---|---|
   | `backend/chat.py` | `830e3b8edde42934b46f68d19f4d169b1aa62c174ce2af1182b534ef71efeea2` |
   | `backend/main.py` | `2d9c3a46c3ac81e8ebb64df316f78921a16f1bc153f232c5e6170b54b1ef33db` |
   | `backend/report_writing.py` | `861be67f9428ab987da44c33799c2bbc918b3f2d3f4893112a836a2598450e5f` |
   | `backend/skill.py` | `4571f966bdc557a1d7f05307a082028f99d11bd7b9fe837ff1154de92f70d234` |
   | `skill/SKILL.md` | `825aaf19835f6b4bb6147051ac533a5c816cd3652127cf91108114c5071e7608` |

4. 原子替换后重启服务；service active，`NRestarts=0`，单个 `run_web.py` 进程正常运行。
5. 本机与公网 `/api/health` 均为 200；SPA shell 为 `no-cache, must-revalidate`，hash asset
   为 immutable；未登录 `/api/projects` 与 admin API 均为 401。
6. 真实浏览器加载生产登录页，用户名、密码与登录按钮正常渲染，console error 为 0。
7. 使用的 E2E 测试凭据已失效；未改动或重置生产账号。这不影响未登录 GUI 与鉴权边界冒烟，
   后续需要鉴权内人工复验时应先更新受控测试凭据。

部署过程中第一次原子替换连接被关闭；只读审计确认运行文件尚未变化且 staging 完整后重试成功，
最终运行文件哈希与本地一致。运维日志已写入私有 VPS 项目。

## 已接受限制

- 快照锁遵循现有单进程单 worker 安全模型；扩 worker 前必须改为跨进程锁或共享状态。
- 本批不提供前端版本历史 UI，恢复通过对话工具完成。
- 无数据库迁移；frontend dist 未变化，因此没有执行 dist swap。
- GUI 冒烟未使用真实业务账号创建或修改项目；鉴权内写入行为由状态矩阵、全量回归和独立红队覆盖。

## 回滚

1. 从 `/opt/cra-rollback-20260718-182342-draft-versioning` 恢复五个运行文件。
2. 重启 `consulting-report.service`。
3. 复验 service、journal、本机/公网 health、SPA 缓存头与未登录鉴权。
