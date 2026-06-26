# Current Worklist

候选方向（2026-06-24 提出 → **同日查证后置为「保留待定、未启动」**）：**上下文保留 + 压缩**。⚠️ **本条原始描述的根因已于 2026-06-24 被实证推翻**（拉 opencode/codex 源码 + 通读 `backend/chat.py` + 实测部署机 admin 的「美羊羊大战蓝兔」项目）——纠正见本条末「【2026-06-24 实证更正】」段，下方原文保留作历史。原文：**上下文从「工具结果读完即弃」改为「session 内保留 + 压缩」**（对齐 CC/Codex/OpenCode 等成熟 agent harness）。**动机（用户拍板的核心理由）**：多材料综合推理 + 跨轮连贯 → 报告更不像拼凑——咨询报告的核心价值是「综合」，现状「读完即弃」让模型跨轮"忘掉"早先读过的材料/自己定的术语口径，长报告越写越像拼接，可能在主动损害产品核心质量。**Claude 判断：lean yes**（之前倾向不做是把成本/复杂度权重打高、质量上行打低，对 report 产品权重反了，已修正）。**成本**：涨幅温和——DeepSeek 官渠缓存命中 0.025 vs 未命中 3 元/M（差 120×），稳定前缀重发近乎免费，大头输出 token（6 元/M）与保留无关；caveat=压缩事件改写历史打断缓存前缀 + 缓存 TTL 分钟级会冷掉。**现有设施（关键：不是重写）**：压缩引擎已在且两段式（preflight + post-turn，`preflight_compaction_used`/`post_turn_compaction_status`）+ tiktoken 计数 + 上下文限额策略 + `_summarize_messages`（含附件净化）+ `_pair_tool_calls_with_results` 按 tool_call_id 配对 + DeepSeek 兼容 tool-call 序列化——但因 `_load_conversation` 只持久化 user/assistant、tool 结果跨轮丢弃，这套压缩**几乎从未被触发**〔❌ 2026-06-24 实证更正：此句错——`conversation_state.json` 另有工作记忆旁路，tool 全文实际跨轮保留；见本条末更正段〕。**改动面（中等）**：翻持久化边界（`_load_conversation`/`_save_conversation` 也存 tool_call + tool 结果）+ 把历史 tool 结果喂进下轮上下文（现 `visible_messages` 只装 user/assistant）+ 让已有压缩接管变大的历史。**spec 必须红队的 3 风险点**：① **DeepSeek 官渠回放历史 tool-call** 的硬约束（非空 `reasoning_content` / 不塞 null 字段 / 不显式发 `tool_choice`）现仅对本轮 tool-call 生效，跨轮持久化后每轮要重序列化从盘读回的历史 tool-call、同样要满足，最易出 400；② **附件信任边界跨轮持久**（`read_material_file`/图片转写结果现读完即弃、恶意正文不残留；持久化后未压缩的近期历史会跨轮带它，压缩层虽净化但整套防注入故事要重审）；③ **压缩路径首次被真正大量使用**易暴露潜伏 bug + read-before-write mtime 门禁语义变化（模型"自以为记得"、mtime 门禁仍能拦改到过期内容但交互语义变）。~~**建议起点**：先做**分级保留**~~（此建议已作废——见下方更正）。详见 memory [[context-retention-direction]]。

**【2026-06-24 实证更正】**（拉 opencode/codex 源码 + 通读 `backend/chat.py` + 实测部署机美羊羊项目，三方印证）：
- **原前提错**：CRA **不是**「工具结果读完即弃」。`conversation_state.json` 有一套工作记忆旁路——成功的 `read_material_file`/`fetch_url`/`read_file`/`write_file` 把**全文**按 `source_key` **去重**存成 `memory_entry`（重读=覆盖刷新），每轮经 `_build_memory_aware_history_messages`（主循环 live 路径）拼成 `[工作记忆]` 块注回上下文；压缩引擎接通（90% 阈值）但因保留内容通常不撑到阈值而**少触发**（"压缩没触发"是真，但因为没撑到、不是因为没东西）。
- **实测（美羊羊大战蓝兔，market-research，已完稿）**：17 条 memory_entry——7 个信源全文（36氪 4805 字 / 虎嗅 3652 / 授权展 2460 …）+ 正文草稿 7529 字 + 全部 plan/notes，共 ~35k 字每轮注回，`compact_state: NONE`（**从未压缩、零信息丢失**）。模型写报告时材料全在手，**并非失忆拼凑**。
- **结论**：①「报告像拼凑」**未在真实输出观测到**（用户确认无实例）；② 这套自制 memory 对**报告 agent 合理**——去重证据库 + 落盘 plan/notes/draft 当持久记忆，比 codex/opencode「整条 thread 全留」**更省窗口、避免有损压缩**；代价=过程/`reasoning` 不跨轮留 + `[工作记忆]` 块靠前致**缓存前缀易失（密度换缓存）**。③ 真实小缺口仅 `web_search` 结果列表 + `reasoning_content` 不跨轮留，**低危、非「读完即弃」**。
- **决策**：用户决定**先不杀、也不启动**（保留待定）。若日后重启，聚焦真实缺口 + 密度/缓存取舍，**别再按「读完即弃」错误前提做、也别做已作废的「分级保留」**。
- **调研旁证**：opencode（`sst/opencode`）+ codex（`openai/codex`）都是「整条 thread 全留 + 溢出（~90%/上限）才压缩、无按消息类型分级保留」；且**信任边界 CRA 比两家都严**（两家主循环不隔离工具输出，CRA 有 `ATTACHMENT_DATA_*` 包裹 + 中和器）——若重启**勿 regress** 这层。

最后更新：2026-06-26（**redesign 三处原型差距 follow-up ✅ 完成 + 前端+后端部署上线 kr-web-01**——分支 `feat/frontend-redesign-followups`，commit `8e14eab`，**待 merge main + push**）：用户本地对照原型 `design_handoff_frontend_redesign/` 发现翻新后 3 处没改完：① **用户管理表格**（`AdminPanel` `<table>`→5 列 grid `1.6/1/1/1/1.3fr` + `bg-card2` 表头 11px/600 + 状态色标[正常 `text-success`/已禁用 `text-warn`] + 操作右对齐 + 等宽数字；**保留可编辑额度 input**[用户硬要求]，补 ARIA `role=table/row/columnheader/cell` + input `aria-label`）；② **侧栏副标题**改「报告类型 · 阶段名」（`Sidebar` 用 `getStageName`，活动项目用实时 `workspace.stage_code`、其余用 `list_projects` 新返回的 `stage_code`、都缺只显示类型；**后端 `skill.py:list_projects` 加 `stage_code`＝本批唯一后端改动**，与 `get_workspace_summary` 同源 `_infer_stage_state`、advisory 降级 None；`App.jsx` `currentStageCode` 由 `workspaceProjectId === currentProjectId` 守卫，防切项目时旧阶段瞬时覆盖[codex 红队挖的真 bug]）；③ **新建报告弹窗**补标题「新建报告」+ 副标题 + 四字段 `<label htmlFor>`+id。Codex **spec+quality 双轨独立审 + 对抗式红队轮 全 APPROVED**（红队挖 3 a11y/正确性 NIT 全修）；前端 **418 测试 + build 绿**（后端 2 个 mac realpath 失败属环境差异、非本批）。**部署**：前端 dist（bundle `index-C7_xlMbU.js`）+ **kr-web-01 首次后端 redeploy**（file-push `backend/skill.py` + 重启 systemd 单 worker；服务器 git 原在 `8a4e042`、dist 早被换成 redesign bundle，本批 skill.py 唯一后端 delta 干净 file-push，**服务器工作树现领先 HEAD，待 push 后 `git reset --hard origin/main` realign**），公网 `https://consulting.z0y0h.work` 经 CF 验证 200 + served 新 bundle + no-cache shell + journal 无错。架构/不变式记项目 CLAUDE.md「## redesign 三处原型差距 follow-up」段。见 memory [[frontend-redesign-status]] + [[w2c-deploy-status]]。

上一次更新：2026-06-26（**前端 UX 翻新完成 + 每批及整分支 Codex 双轨审全 APPROVED + 用户本地终验过 + merge main `d794ae0`（--no-ff）+ push origin `968b2eb` + ✅ frontend-only 部署上线 kr-web-01（bundle `index-BQ_h6RjI.js`，公网 `https://consulting.z0y0h.work` 经 CF 验证 served + health + SPA shell no-cache；数据零改、只换 dist、systemd 未重启、`dist.old` 留回滚）**——分支 `feat/frontend-redesign` 保留）：全部 8 批次（0 地基 → 1 App 外壳/主题 → 2 Login/ForcePasswordChange → 3 Sidebar → 4 MarkdownMessage/ThinkingBlock/ChatPanel → 5 WorkspacePanel/StagePanel/StageAdvanceControl/FilePreviewPanel/RollbackMenu/ConfirmDialog → 6 ProjectCreateModal/SettingsModal/AdminPanel/IndependentReviewDrawer → 7 整分支验收）换皮完成。**通道 token 单一真值源**（`index.css` `:root/.dark` + `tailwind.config.js` 语义类 + `utils/theme.js` + `index.html` 防闪 bootstrap + `components/icons.jsx` 线性 SVG + 自托管 woff2 Hanken/IBM Plex）；FilePreviewPanel 代码高亮改 token 双主题、KaTeX 随主题。**护栏**：`paletteGuard`(ALLOW_PENDING **已清空**=全量迁移)/`tokenContract`/`darkClassGuard`(仅许 `dark:bg-scrim/N`)/`themeBootstrap`/`theme.test` 全绿；前端 **414 测试全绿 + `npm run build` 绿**。**追加（用户本轮要求，非原 plan，已记 plan「## 实施期范围增补」）**：① 左侧栏加宽 248→264px + 可整列收起/展开（开关在聊天 header 左侧、与右侧工作区开关镜像图标 IconSidebar↔IconPanelRight、showSidebar 持久化 + 重夹拖宽）；② **材料 tab 直接上传**（复用聊天回形针同一 `/materials/upload` 端点，上传即入库；忙态按 projectId 作用域；project-switch + unmount + StrictMode 守卫齐全）。**Codex 双轨审（用户重登后补齐，全 APPROVED）**：批次 0–5 backfill（spec 1 轮 / quality **4 轮**——挖出并修一串上传守卫真 bug 含 unmount/StrictMode 静默失效、stepper report-only S7 116% 溢出、S2/S3 进度条误标「正文字数」、材料 tab 工作目录展示行被换皮漏掉=功能回退、删除按钮丢 aria-label）+ 批次 6（spec 1 轮——修 IndependentReviewDrawer 把 422 数组 detail 直接进渲染态的崩溃隐患，抽 `normalizeApiErrorDetail` 共享归一；quality 1 轮 clean）+ **整分支双轨终审 APPROVED**（仅 NIT：prose-invert 浅主题反色风险/auth 卡片任意 shadow→shadow-popover/bootstrap catch 兜底去 .dark/上传忙态项目作用域，已全修）。每批 commit 后即审（项目约定，[[per-batch-codex-review]]）。本地隔离实例 `run_web.py`（`CRA_DATA_ROOT=<scratchpad>/cra-data`，admin/邀请码自配）`localhost:8888` 已起、serves 最新 build。用户本地终验后又提两个小修（已修+审）：① ChatPanel 上下文额度条 flex-1 随聊天窗口伸缩（去 max-w + modeTag 去 ml-auto，数字贴近 Provider 标签）；② **材料 tab 删掉绝对 workspace_dir 路径展示**（web 部署会泄露 VPS 文件路径——产品/安全决策，覆盖早期「工作目录全保」复审结论）。**架构已记项目 CLAUDE.md「## 前端 UX 翻新：海军蓝双主题设计系统」段**（token/主题/字体/护栏/不变式/follow-up）。**部署**：frontend only（`npm run build` → tar → `VPS-fix-private/.push-file.py kr-web-01` → 解 dist.new + chmod + 原子 swap，无须重启 systemd、`dist.old` 留回滚），**只换 dist、数据零改**。见 memory [[frontend-redesign-status]] + [[w2c-deploy-status]]。

**前端 follow-up：工具调用卡片重设计 + 去 emoji —— ✅ plan 定稿 Codex APPROVED（4 轮对抗红队），待新会话实施**：`docs/superpowers/plans/2026-06-26-tool-call-pill-redesign.md`（v4，9 task，subagent-driven，commit `7b94ea7` 本地 main 未 push）。**最终方案**：后端正常工具 call/result 发结构化 `tool_call`/`tool_result`（带 `id` 配对，独立审查也发 id）；**reload 持久（用户拍板入范围）走 `conversation.json` assistant 的 `tool_events` 并列字段**——`_build_tool_events` 写 + `_load_conversation` 保留（白名单重建须显式留，否则下轮 re-save 抹掉）+ `GET /conversation` 直返；`_to_provider_message`(4036) 只回 `{role,content}` 天然丢之、不泄漏 provider。**不嵌 HTML 注释**（规避 `-->`/`]` 容器截断）、**不动 `_format_tool_pair_line`/tool-log 注释机制**（保 provider 历史 + 老断言）。前端**完全不解析文本、无 emoji 字符**：live 从 SSE `reduceToolEvent`、reload 从端点字段，统一 `msg.toolEvents`，共享 `ToolCallPill`（IconTool + 工具名 mono + 参数淡 + 成功/失败/进行中图标 + **摘要 click-to-expand**[用户拍板③]）成组渲染正文上方（`ToolCallList`）；主聊天与独立审查统一。诊断类 `type:"tool"` 文本事件保持现状（用户拍板②、ChatPanel legacy 分支不删）；`_sanitize_message_for_summary` pop tool_events（compaction 信任边界）。**非目标**：老对话（改动前）reload 不显 pill（无字段→[]、无回归）。**红队 4 轮收敛史（防重蹈）**：R1 初稿误删诊断事件 + 重构 `_format_tool_pair_line` 破持久化（`assertIn` 锁不住）→ R2 reload 没那么「顺手」（`GET /conversation` 端点**服务端就 strip 了**工具数据、前端收不到）→ R3 把 JSON 塞 HTML 注释会被含 `-->`/`]` 的查询/报错截断漏坏进正文 → R4 改 sibling 并列字段 APPROVED。现状（背景）：工具渲染成两 pill/工具的 emoji 日志（写死绿勾、emoji 来自后端 SSE 文本故 paletteGuard 扫不到）。**下一步：新会话 subagent-driven 逐 task 实施（每 task 完 Codex 双轨审）。**

上一次更新：2026-06-25（**前端 UX 整体翻新 spec + plan ✅ Codex 审 APPROVED、待执行**——分支 `feat/frontend-redesign`，本次只产出规划文档、**未动代码**）：把 web 前端从「深紫黑 `#0f0f23` + 薄荷绿 `#64ffda` + emoji 的 AI 味」翻新为「海军蓝 `#1B2A4A` + 浅/深双主题可切换 + 线性 SVG 图标 + 自托管字体（Hanken Grotesk + IBM Plex Mono，中文走系统栈苹方/雅黑）」。设计来自 claude design 交付包 `design_handoff_frontend_redesign/`（已 gitignore 当本地参考，含 README 逐屏规格 + token 表 + `Prototype-standalone.html` 高保真 mock）。**核心原则**：纯前端零后端、业务逻辑只换皮、**功能零退化**（原型是视觉 mock 会画丢真实控件——如 AdminPanel 可编辑额度列、StagePanel 7/8 段、ChatPanel 停止/材料 chips/粘贴——一律以生产代码为准保留）。token 用**通道形式** CSS 变量（`--x: R G B`）做单一真值源 + Tailwind `rgb(var(--x)/<alpha-value>)` 语义类；主题靠 `<html>.dark` + localStorage + index.html head 同步 bootstrap 防 FOUC。**审查**：spec `docs/superpowers/specs/2026-06-25-frontend-redesign-design.md`（Codex 双轨审 5 轮 APPROVED，BLOCKER 12→5→2→0）、plan `docs/superpowers/plans/2026-06-25-frontend-redesign.md`（Codex 合并单轨审 2 轮 APPROVED，7 批次：0 地基精确代码 → 1 外壳 → 2 认证 → 3 侧栏 → 4 对话 → 5 工作区 → 6 弹窗 → 7 收尾）。分支 4 commits（spec / gitignore / plan / plan-review）。**下一步：subagent-driven 逐 task 执行 plan，从批次 0 地基起**（执行铁律见 plan §「每组件换皮标准流程」+ spec §5 不变式清单）。详见 memory [[frontend-redesign-status]]。

上一次更新：2026-06-25（**两个前端 UX 修复 + 部署 kr-web-01 + push**）：① **输入框乐观清空**——原 `ChatPanel.sendMessage` 的 `setInput('')` 放在 `startStream` 末尾（流式整轮结束才清），导致消息发出后仍滞留输入框直到回答完（同事也反馈）；改为点发送即 `setInput('')`（chatbox 风格），失败/上传失败/中止经 `restoreInputForRetry()` **双重守卫**回填原文（① 发送序号 `sendSeqRef` 未变 ② 输入框仍空），既防 abort 后覆盖用户新打的字、也防旧的被中止发送盖回已被下一条发送清空的输入框。② **中右分栏可拖动**——`WorkspacePanel` 原写死 `w-[28rem]`，改为 `App.jsx` 持 `workspaceWidth` state + 竖向拖动条（`cursor-col-resize`，沿用 `FilePreviewPanel` 上下拖动模式）+ localStorage 持久化；宽度数学抽纯函数 `frontend/src/utils/workspaceResize.js`。**关键不变式（codex 双轨 + 2 轮红队挖出的 5 BLOCKER）**：容器 ref 挂在**排除固定 Sidebar 的内层 wrapper**（`<div ref={setContainerRef} className="flex flex-1 min-w-0">`）——否则 clamp 把整窗宽（含 Sidebar）算进去会把聊天区挤到 ~100px；存储宽度经 callback ref 在挂载时按真实容器**重夹一次**（防存的宽超出当前窗口）；`clampWorkspaceWidth` 负宽 floor 0；中间列 `ChatPanel`(flex-1) + 输入框/用量框靠 flexbox 自动重排，**无需手动同步宽度**。纯前端、后端/DeepSeek/信任边界/租户隔离零改动；TDD 前端 **397 全绿** + build 绿；Codex spec+quality 双轨独立审 + 2 轮对抗红队 → 双 APPROVED。**部署**：前端 only → 本地 build dist → `.push-file.py` 推 tar → 服务器 `dist.new` 解压 + 原子 `mv` swap（**无须重启** systemd，StaticFiles 按请求读盘；`dist.old` 留作回滚），新 bundle `index-D9CspGtr.js`，公网 `https://consulting.z0y0h.work` 验证 served + health + shell no-cache。真值源=项目 CLAUDE.md「## 中右分栏拖动 + 输入框乐观清空」段。**本地联调 LLM 失败的诊断（非本次改动、不修）**：本机 Clash/Surge 类代理 fake-ip 模式把 `newapi.z0y0h.work` 解析成 `198.18.0.236`（保留段），撞 B3 SSRF 防护（`url_guard.assert_public_ip` 判私有 → `不允许访问本地或内网地址`；managed 侧表现为 `Connection error`）；线上解析真实公网 IP 故正常。本地要联调 LLM：代理对该域名设直连/关 fake-ip。见 memory [[local-dev-llm-fakeip-ssrf]]。

上一次更新：2026-06-24（**上下文保留候选方向查证 + neat-freak 修文档事实 + push**）：用户质疑「为何不对标前沿 coding agent」→ 拉 opencode/codex 源码 + 通读 `backend/chat.py` + 实测部署机 admin 的「美羊羊大战蓝兔」项目，**推翻顶部候选方向原记的「工具结果读完即弃 / 压缩从未触发」前提**——CRA 有 `conversation_state.json` 工作记忆旁路（成功的 read/fetch/write 全文按 `source_key` 去重存 `memory_entry`、每轮注回上下文），压缩引擎接通（90%）但保留内容通常不撑到阈值故少触发；美羊羊实测 17 条 memory_entry ~35k 字/轮、`compact_state:NONE`（零信息丢失）→ 模型写报告材料全在手、非失忆拼凑。「拼凑」未在真实输出观测到、自制 memory 对报告 agent 合理 → **方向保留待定、未启动**（详见顶部候选方向条 + memory [[context-retention-direction]]）。本次仅修文档事实、**不动代码**；并修正 2 处 stale「未 push」（Part C 后 3 笔小修早已 push，origin/main=`e85d18f` 含之）。

上一次更新：2026-06-23（**Part C 上线后 3 笔小修，均 Codex APPROVED + commit 本地 main（`c30b903` 额度 / `d552579` 缓存 / `3bff742` 登录白屏），✅ 2026-06-24 核实已全部 push（origin/main=`e85d18f` 含此 3 commit）；均部署 kr-web-01 + 实地验证**）：① **前端额度实时化 + 进度条**——sidebar 今日额度原只登录拉一次 `/me`→陈旧（与 admin 面板对不上）；改为每轮聊天结束（`handleProjectMutated`，ChatPanel+WorkspacePanel 共用）+ 窗口聚焦刷 `refreshAuthQuota`（**三重守卫**：`quotaRefreshSeqRef` 序号防同 uid 乱序覆盖 + `r.data.uid===prev.uid` 防跨用户串号 + `axios {skipUnauthedHandler:true}` 背景轮询不触发全局 401 登出），额度行换成 `quotaRatio` 驱动的进度条（overCap 红 100%）；**关键 React 坑**：init effect 依赖收窄到 `[authUser?.uid, must_change_password]`（非整 `authUser`），否则额度刷新每轮重挂 ChatPanel→黑屏闪 + 聊天/工具调用记录丢。Codex 双轨审 4 BLOCKER+4 NIT 全修 APPROVED。② **后端 SPA shell no-cache 修「部署后空白页」**——`StaticFiles` 不发 Cache-Control→浏览器启发式缓存旧 index.html→部署原子 swap 删旧 hash bundle→旧 shell 指向 404 bundle→空 `#root` 满屏深色空白（控制台静默 404、UI 无报错）。**⚠️ 这是一个真实的潜在隐患修复，但 _不是_ 用户当时报的那个空白页的真因——真因是 ③ 的 422 白屏；我当时误判成了缓存（后被无痕窗口仍白屏否证、nginx 日志 422 锁定真因）**。新 `backend/main.py:_SPAStaticFiles` 给 shell 发 `no-cache, must-revalidate`（**按规范化路径判定**，200+304 都覆盖）、`assets/*` immutable。Codex 审挖出 304 漏头 BLOCKER（条件请求命中返 304 无 content-type→学不到 no-cache）已修、APPROVED。**SPA shell 现 no-cache → 旧『每次部署后须硬刷新』gotcha 自此自愈**（存量陈旧标签仍需硬刷一次）。回归：后端 `tests/test_static_cache_headers.py`(6，含 304)、前端 `apiUnauthed.source.test.mjs`+扩 `sidebarQuota`/`appInitGating`。③ **登录页白屏（= 用户报的真因，`3bff742`）**——短用户名(<3)/短密码(<6) → 后端 **422** 校验错误，其 `detail` 是数组 `[{loc,msg,type}]`（非字符串）；旧 `Login.jsx` `setErr(detail)` → 渲染 `{err}` 触发 React「Objects are not valid as a React child」→ 登录页（App 早返回分支、**不在**内层 ErrorBoundary 里）整树卸载 → 空 `#root` 白屏（长密码走 401 字符串故不白屏，这也是我误判缓存的原因）。3 层防御：`frontend/src/utils/authError.js:normalizeAuthError`（任何 detail 恒归一成字符串）+ `Login.jsx` 提交前客户端校验长度&提交 trim 用户名 + 抽出 `components/ErrorBoundary.jsx` 在 `main.jsx` 包住整个 `<App/>`（auth 屏渲染崩溃降级为 fallback、不白屏）。Codex 审 4 NIT（3 修 1 defer：`IndependentReviewDrawer` 同类写法当前不可达 + 已被新全局 ErrorBoundary 兜底）→ APPROVED。回归 `authError.test.mjs`(6)+`loginErrorHandling.source.test.mjs`(3)。真值源=项目 CLAUDE.md「## 前端额度实时化 + SPA 缓存头」段。**（✅ 已 push origin，2026-06-24 核实 origin/main=`e85d18f` 含此 3 commit。）**）

上一次更新：2026-06-23（**W2-C Part C ✅ 部署完成上线——站点 `https://consulting.z0y0h.work` 已对同事开放，8 步 smoke 全 PASS**）：kr-web-01（腾讯云轻量首尔，`43.131.242.15`）反代 nginx + Cloudflare 橙云 + systemd **单 worker**（consulting 用户、`CRA_DATA_ROOT=/var/lib/consulting-report`）。验收：health/SPA 登录门/邀请注册（错码 403）/CSRF 403/admin 登录+must_change+守卫 403/建项目/**managed deepseek-v4-pro 真链路聊天通（usage 落库计费）**/独立审查 SSE/**导出真实下载 docx（PK magic+word/document.xml+Content-Disposition）**/跨租户 u2→u1 workspace·chat·download 全 404/**真实 IP 透传（sessions.created_ip=真用户 IP 非 CF/nginx）** 全过。部署中解决三坑：① 腾讯云轻量**「防火墙」需手开 443**（ufw/nginx 都对、云安全组没放行；用户已开）；② **uv 托管 Python 落 `/root` 致 consulting 无法执行**（203/EXEC）→迁 `/opt/uv-python` + chmod a+rX 重建 venv；③ TLS=**源站自签 15y + CF Page Rule 给 `consulting` 单设 SSL=full**（zone 对其它子域仍 strict；自签 15y 免续期；MCP token 签不了 Origin CA/写不了 Configuration Rule，故走 Page Rule）。**W2 全线收口（B1/B2/B3 + Part A+B + Part C 上线）。** 已做硬化：✅ **origin 443 仅放行 CF IP 段（藏源站，2026-06-23——非 CF IP 直连已挡、经 CF 仍 200；CF 新增 IP 段需手补，多年才变一次）**。可选后续硬化（非阻塞）：用 Origin CA Key 升 Full-strict、Page Rule→Configuration Rule。连机经 `VPS-fix-private/.run-remote.py kr-web-01`（root key-auth）。详见 memory [[w2c-deploy-status]] + 下方 W2 簇。**交付密钥**（邀请码 + admin 临时密码，首登强制改）见私有 memory `w2c-deploy-status` / 已私下交付——**不入公开仓库**。下方为 Part A+B 实施快照——）

上一次更新：2026-06-23（**W2-C Part A+B ✅ 实施完成（本地全绿 + Codex 4-cluster 双轨审 APPROVED + 整分支自审 SHIP-READY）+ ✅ merge main + push origin + neat-freak 收尾（Part C 已部署完成，见顶部）**——分支 `feat/w2c-de-windows-export`（**✅ merge main `ebd5312` + push origin + neat-freak `c6de4d1`，origin/main==HEAD 已核实**，15 commits 含 cutover + BLOCKER 修复）。subagent-driven 8 task TDD（实施派 Claude sonnet/opus、review 派 Codex），按耦合聚成 4 cluster 双轨独立审：**R1**(T1-3 导出后端：纯 Python pandoc `_resolve_pandoc` 平台守卫+temp/`os.replace` 原子发布+端点 sync def 不阻塞 loop+`GET .../export-draft/download` FileResponse 确定文件名+穿越守卫+属主隔离)→**R2**(T4-5 前端 exportDraft 判 status+anchor 触发下载 / 退役 `export_draft.{ps1,sh}`+去引用 skill 模块·BUILD.md+删 `get_script_path`)→**R3**(T6-7 run_web env+`proxy_headers`+`CRA_FORWARDED_ALLOW_IPS` / SSE 双流周期心跳 `_sse_with_heartbeat` 线程多路复用，锁释放全路径保住+保住 R3 用户写 CAS 不变式)→**R4**(T8 N6 F2 删 4 legacy 解析器+无 converter raise)。验证：后端 **1438 passed**(4 failed 全已知 mac-realpath、Windows 绿;1 skipped)+前端 **363 passed**+`npm run build` 绿+DeepSeek 兼容 5 passed+禁改区 `chat.py`/`independent_review.py` 零改动。Codex 双轨挖出并修的真问题：R1 原子测试半假绿(补断言 -o=temp 路径)、R3 eager-drain 丢背压(核 reachability 判 trial accepted-risk+后置硬化)+fut 异常静默吞(补日志)+call_soon 未守 loop-close(补 try/except)、R4 缺错误映射测试(补)。**Trial accepted-risk/后置硬化**：eager-drain 背压(慢客户端缓存整轮+烧自己配额、锁释放更早)→背压保持版重写；8-pump-worker 单 worker 并发悬崖；FileResponse stat/open TOCTOU(自愈重下、pin fd 后置)；symlinked output 需服务器 FS 访问前提(web 用户不可达)。真值源=项目 CLAUDE.md「## W2-C」段+spec+`docs/superpowers/cutover_report_2026-06-23_w2c-de-windows-export-and-deploy-prep.md`。**整分支对抗式红队终审已跑（新 codex 线程）：挖出 1 BLOCKER——`clear_conversation` async def 在事件循环上持 request RLock 冻结 loop 掐心跳，已修（改 sync def 离 loop + 前端 loading 禁清空按钮 + handler 早返 + route-guard）+ 1 NIT（aclose 测试加强）；2 Important（cutover 缺失→已写、worklist drift→本次修）已闭环**。**Part C 已于 2026-06-23 部署完成上线（详见顶部最新更新条），经 `VPS-fix-private/.run-remote.py kr-web-01`（root key-auth `43.131.242.15:2233`）连机部署。**）

上一次更新：2026-06-23（**W2-C 部署上线 + 去 Windows 化导出 spec + plan ✅ Codex 循环审 APPROVED，待实施**——把 W2-B 多租户引擎真正部署到试用机让同事用。spec `docs/superpowers/specs/2026-06-23-w2c-deploy-and-de-windows-design.md`（codex-server 线程 `019ef0be-7c00-7783-a609-1270555e9875`，5 轮：R1 5BLOCKER+3NIT→R2 3+1→R3 2+1→R4 1+1→**R5 APPROVED**，共修 11 BLOCKER+6 NIT）、plan `docs/superpowers/plans/2026-06-23-w2c-de-windows-export-and-deploy-prep.md`（8 task TDD，线程 `019ef0f8-2ccb-7780-b95c-81ce0c2ad84c`，4 轮：R1 8BLOCKER+2NIT→R2 3+2→R3 1+3→**R4 APPROVED**，共修 12 BLOCKER+7 NIT）。**范围**：① **Part A 去 Windows 化导出**（删 `export_draft.ps1`/`.sh` 脚本层 + `_run_powershell`，`report_tools.py` 纯 Python 调 pandoc[解析守卫 frozen/Windows，否则 Linux 误 exec .exe] + temp.docx→`os.replace` **原子发布** + 端点改 sync def **不阻塞事件循环** + 锁外读[依赖 R3 原子写、绝不取整轮持有的 request lock]）+ **web 下载契约**（新 `GET .../export-draft/download` FileResponse 确定文件名+穿越守卫，现状前端只回显服务器路径、web 用户根本下载不到 docx）+ **SSE 空闲心跳**（CF ~100s 切空闲流：审查流周期心跳 + 聊天流线程多路复用 `_sse_with_heartbeat`[stop_event+gen.close 断连释放锁]）+ `run_web.py` host/port env 化 + uvicorn `proxy_headers`（真实 IP）；② **Part B N6 F2 收口**（删 4 个 legacy 解析器 `_legacy_read_document`/`_read_docx`/`_read_xlsx`/`_read_pdf`、无-converter 改报错+测试注入假 converter）；③ **Part C 部署 runbook**（**不入 TDD plan、交互式执行**）：kr-web-01 反代+CF（`consulting.z0y0h.work`，Origin Cert+橙云）+ nginx[SSE 关 buffering + `set_real_ip_from` CF 段 `real_ip_header CF-Connecting-IP`] + systemd **单 worker**（B2/B3 进程内状态）+ env[`CRA_DATA_ROOT=/var/lib/consulting-report`/`CRA_INVITE_CODE`/`CRA_ALLOWED_ORIGIN`/bootstrap admin] + 装 pandoc+libreoffice。**关键产品决策**：部署机=kr-web-01（与 LLM 基建 jp-app-01 分机隔离）、HTTPS=标准反代（用户不熟 tunnel）、子域名=`consulting.z0y0h.work`、F2 并入收口。**下一步：subagent-driven 实施 Part A+B（先代码测试绿+merge）→ 再交互式执行 Part C 部署**。详见下方 W2 簇。**风险**：kr-web-01 非自有账号，`managed_client_token`+搜索池凭据落非自有机，转生产换实例时轮换。**）

上一次更新：2026-06-23（**W2-B / B3 admin 面板 + 安全硬化 + custom 真激活 ✅ 实施完成 + merge main `450acba`（--no-ff）+ push origin**——分支 `feat/w2b-b3-admin-security-hardening` 保留，5 实施 Phase subagent-driven（**每 Phase Codex spec/quality 双轨独立 review 审→修→再审到 APPROVED + 红队对抗**）。交付：新叶子模块 `backend/url_guard.py`（只 httpx+stdlib，绝不 import chat/skill/main/config/accounts）= public-IP 校验 + **三层域名白名单**（内置 ∪ env `CRA_CUSTOM_API_ALLOWED_HOSTS` ∪ app_config 运行时项[admin 面板增删、无需重启]）+ guarded transport（`trust_env=False`/`follow_redirects=False`）+ `validate_custom_api_base`（https+白名单+解析公网+拒 userinfo/坏端口）；CSRF Origin/Referer 中间件（web 态，缺 Origin 退 Referer，生产不信 loopback 源）+ CORS 从 `["*"]` 收紧到 allowlist + web cookie_secure；**throttle-first** per-username 登录限流（reserve-before-verify 单锁原子 + 精确 key + deque maxlen + `_MAX_TRACKED_LOGIN_KEYS=4096` 有界）；8 个 `/api/admin/*`（用户列表/改密/调 cap/禁用/邀请码轮换/允许域名维护）全 `Depends(get_current_admin)` + `admin_set_user_disabled` 的 `BEGIN IMMEDIATE` 原子守卫（活跃 admin 绝不归零）+ cap Decimal 限长限幅(¥1e6)→400 不 500；`require_password_current` 三层覆盖（require_project 默认 + get_current_admin + 显式 8 端点含桌面桥 2 个，桌面短路、豁免精确 {me,change-password,logout,health}）；**custom 真激活**——`normalize_settings_payload` 非 legacy honor mode（legacy `config_version<4` 仍强制 managed）+ **`mode` 现持久化**（之前被当派生字段剔除致 custom 活不过一个请求）+ `managed_base_url` 服务端只读；前端 AdminPanel + Sidebar 入口 + ForcePasswordChange + App.jsx gating + SettingsModal custom UI/去 managed_base_url + raw fetch credentials。验证：后端 **1419 passed**（4 failed 全是已知 mac-realpath 环境差异、Windows 绿）+ 前端 **357 passed** + `npm run build` 绿 + DeepSeek 官渠兼容 18 passed + compat-helpers-match 1 passed + 跨租户隔离绿。安全验收门 5 项（CSRF/SSRF/越权/custom 激活+持久化/must_change_password）各映射到通过的测试类。**用户拍板的限流取舍**：throttle-first（桶满直接 429 不验密=真封顶撞库）优于 verify-first（架空撞库防护）——接受被攻击时该用户 5min 临时锁定（自动恢复，业界标准）。**已知限制**：DNS rebinding TOCTOU 未彻底防（白名单=安全边界、非连接层 pin IP；pinned-IP-with-SNI 后置增强）/ `_LOGIN_FAILS`+`_RUNTIME_ALLOWED_HOSTS` 单进程（多 worker 需共享/广播）/ 账户锁定 DoS（≤5min、用户接受）/ custom_api_key 明文存 per-uid config.json（既有设计，custom 现激活使其生效）/ 软帽非原子（B2 沿用）。cutover `docs/superpowers/cutover_report_2026-06-23_w2b-b3.md`。**已 merge main `450acba` + push origin（W2-B 三段 B1/B2/B3 全部落地合并）。运维 web 须设 `CRA_INVITE_CODE` + `CRA_ALLOWED_ORIGIN`（后者缺失 → 生产 cookie_secure 态所有写请求 fail-closed 403）。后续 backlog 见下方 W2 簇。**）

上一次更新：2026-06-22（**W2-B / B3 admin+安全硬化+custom 激活 plan ✅ 已写 + Codex 4 轮 APPROVED**——`docs/superpowers/plans/2026-06-22-w2b-b3-admin-security-hardening.md`（6 Phase / 19 TDD task，复用 W2-B 总 spec §7/§8/§13，无单独 spec）。codex-server 线程 `019eef21-100e-7952-87f0-8f46aa6ca006`：R1 5BLOCKER+4NIT → R2 6+4 → R3 1+4 → **R4 APPROVED**（3 轮红队对抗、非诱导式秒过，共修 12 BLOCKER+12 NIT）。范围：admin 面板（调 cap/禁用/改密/轮换邀请码/**白名单管理**）+ CSRF Origin 中间件 + CORS 收紧 + per-username 登录限流 + **custom 真激活**（解锁 `normalize_settings_payload:config.py:344-345`）+ `must_change_password` 路由级强制 + SSRF（新 `backend/url_guard.py`：**admin 维护域名白名单=安全边界 + 请求时 public-IP 校验**；**非通用防 DNS rebinding**——白名单未 pin IP、仍有 TOCTOU，pinned-IP-with-SNI transport 列后置增强）。**关键产品决策（用户拍板，覆盖旧「custom 后置/managed-forced」）**：custom 是主路径（同事自带 api），¥5/天 managed 只是试用引子。Codex R1/R2 挖出的真坑：managed_base_url 仅删端点赋值不够（normalize setdefault 留污染）/ must_change_password 漏 body-project 路由（chat·chat_stream·models-list 手动调 require_project 覆盖不到）/ models-list 400 被宽 except 吞 500 / per-username 限流测试被现有 per-IP slowapi 假通过 / 一批测试 body 字段名对不上真 pydantic schema（message_text 非 message、SettingsUpdate 必填 managed_model、AdminCapBody=str）。**B2 已 merge main（`c2916b1`）+ push origin**（早记的「未 merge/未 push」已过时）；桌面 local cap 用户决定**保持 ¥5/天默认**。**下一步：subagent-driven 实施，按 Phase 推（Phase 1 SSRF+custom 是护栏地基先做且最该红队），每 Phase 隔离 worktree + 实施 + Codex 双轨审。**）

上一次更新：2026-06-22（**W2-B / B2 中央计费 + per-user 配额 ✅ 实施完成 + merge main `c2916b1` + push origin**——`feat/w2b-b2-billing` 分支保留，5 实施簇 subagent-driven（Claude opus/sonnet 实施 + **每簇 Codex spec/quality 双轨独立 review 审→修→再审到 APPROVED + 收尾全分支综合审**）。交付：`backend/metering.py:MeteredManagedClient` 单出口（managed 模式整体换 `self.client`，5 调用点零改动；custom 裸 client）+ 被动 reserve→settle + `finally` 同步结算 + chat/review 消费 `response.close()`（防提前 break 漏计）+ usage **fail-closed**（present-but-malformed[非数值/bool/inf/nan/负/超1e9]一律 None→保守封顶，`MANAGED_FAILCLOSED_CEILING` Qwen3-VL=32768 / 否则 deepseek 256k×p_miss）+ per-(uid,model,**day**) 连续 3 缺失暂停 + `usage_daily` 整数微元原子 upsert + cap 解析（user override→全局 app_config→默认 ¥5）+ `/api/auth/me` 加 today_cost_yuan/daily_cap_yuan + 前端 Sidebar 账号块显额度（**含 local**）。验证：后端 **1347 passed**（4 failed 全是已知 mac-realpath 环境差异、Windows 绿）+ 前端 **342 passed** + `npm run build` 绿 + DeepSeek 官渠兼容回归绿。**红队双轨挖出并修的真问题**：负/falsey/inf/超大 usage 绕过暂停计数（假记 0+复位）→ `_coerce_token` 统一 fail-closed；settle 抛错遮蔽 provider/GeneratorExit 异常+底层流不关 → 嵌套 finally+`sys.exc_info()` 保留在途异常；真 reserve 路径未被测（原测试 patch create 绕过）→ 补集成测试；**local 看不到额度**（被 `uid!=='local'` 整块吃掉、而 local 真受 cap）→ 与登出门解耦；**`quotaRatio(NaN)` 返 NaN** → finite 归一；**收尾全量回归暴露：跨文件 `importlib.reload(metering)` 致 wrapper 抛活类、except 持陈旧拷贝类 isinstance 失配、配额异常漏成 generic error** → 模块限定 metering 引用（**per-cluster 隔离跑照不出、收尾全量回归才暴露**）。cutover `docs/superpowers/cutover_report_2026-06-22_w2b-b2.md`。（此条为 B2「实施完成」快照；其后续 merge `c2916b1`+push origin、桌面 local cap 决定保持 ¥5/天、B3 plan 进展均见顶部最新更新。）)

上一次更新：2026-06-22（**W2-B / B2 中央计费 plan ✅ 已写 + Codex APPROVED**——`docs/superpowers/plans/2026-06-22-w2b-b2-central-billing-quota.md`（14 TDD task；spec 复用 W2-B 总 spec §6/§5.4/§13，无单独 spec）。Codex 5 轮 review：旧线程 R1 7BLOCKER+2NIT→R2 3+1→R3 1+1（额度耗尽）→ **换 API 拉全新线程对抗式红队轮 R4 4BLOCKER+2NIT→复审 APPROVED**。核心架构：managed 模式把 `ChatHandler.self.client`/`IndependentReviewAgent._build_client()` 整体换成 `backend/metering.py:MeteredManagedClient`（镜像 `.chat.completions.create`，5 调用点零改动、custom 走裸 client），被动 reserve→settle + `finally` 同步结算 + chat/review 消费 `response.close()`（防提前 break 漏计）+ fail-closed（`MANAGED_FAILCLOSED_CEILING` Qwen3-VL=32768 / 否则 deepseek effective 256k × p_miss）+ per-(uid,model,**day**) 连续 3 缺失暂停 + `usage_daily` 整数微元原子 upsert + `/api/auth/me` 加 cost + 前端账号块显额度。红队挖出并修的真缺陷：fail-closed 硬编 128k、miss-counter 暂停死锁、include_usage 非 managed-only、`SystemNotice` 非 List[str]、**包裹破坏既有测试**（patch OpenAI 类→wrapper 跑→无 init_db + miss 暂停，修法=测试 base 隔离 DB+闸门设不触发）、**custom 生产不可达却 claim §6.4**（B1 managed-forced，改诚实框定 B3 预留+单元级）、**提前 break 抛弃 wrapper 生成器漏结算**。**下一步：subagent-driven 实施 B2**（详见下方 W2 段）。）

上一次更新：2026-06-22（**W2-B / B1 多租户基座 ✅ 实施完成 + merge main（`c62cd4d`）+ push origin + 真实 GUI E2E 通过**——18 task subagent-driven，**每 commit 走 Codex spec/quality 双轨独立 review（铁律不合并）审→修→再审到 APPROVED + 收尾全分支综合审 SHIP-READY 零 finding**。交付：用户名+密码 + 邀请码自助注册（fail-closed + env 权威）+ httpOnly cookie 服务端会话 + `<data-root>/users/<uid>/` 分目录 + 统一 `require_project(uid,ref)` 归属卡点（canonicalize、查不到即 404=天然隔离）+ 复合键 `tenant_project_key(uid,cid)` 贯彻全部进程内锁/store/搜索 project 级状态 + per-uid settings 隔离存储 + 服务端分配工作区拒客户端路径 + bootstrap admin + 启动安全门（桌面 loopback、web 须 CRA_INVITE_CODE）。**做完即「A 用户碰不到 B 用户的任何数据」**。验证四重：后端 `pytest` **1272 passed**（4 failed 全是已知 mac-realpath 环境差异、Windows 绿）+ 前端 `node --test` **334 passed** + `npm run build` 绿 + **真实 HTTP curl smoke** + **chrome-devtools 真实 GUI E2E**（登录门/邀请注册/自动登录/账号块+登出/建项目服务端分配工作区/第二账号按 id+名称都 404 验隔离）全通过。**红队双轨挖出并修的真问题**（逐 task）：路径穿越(含 Windows 盘符)/password_hash 外泄/停用用户签发会话/审查侧键 standalone 不一致(→Option C：审查侧键迁移整体延后 T11 原子做)/`change-me` 开放注册洞/logout 不幂等/超大密码 argon2 DoS/`/api/chat` 404→500 吞契约/**`store_key` vs `project_id` 混用致审查保存失败(深 CRITICAL)**/邀请码随机码持久 footgun/桌面登出困死。cutover `docs/superpowers/cutover_report_2026-06-22_w2b-b1.md`。**下一步 B2 中央计费**（`MeteredManagedClient` + per-user ¥5/天金额配额按 deepseek 缓存三档精确计费）；**B3** admin 面板 + CSRF/SSRF/CORS 硬化 + custom 模式激活 + `must_change_password` 强制 + per-username 限流。分支 `feat/w2b-multi-tenant-core` 保留。**运维须知：web 启动须设 `CRA_INVITE_CODE`（含 mac 本地 `run_web.py`）；custom 模式 B1 仍不可用（managed-forced）**。详见下方 🟢 服务器化 簇 + cutover。）

上一次更新：2026-06-21（**W2-B 多租户化：spec + B1 plan 全部 Codex APPROVED**——W2 web 化 brainstorm 拍板**「真多用户产品」**（**推翻旧『仅熟人/不做开放注册』scope**，见下方 🟢 段订正）：用户名+密码、邀请码自助注册、per-user 密码/API 隔离、后台管理面板、per-user **¥5/天金额配额**按 deepseek 缓存三档（命中0.025/未命中3/输出6 元每百万token）精确计费——**已上机 jp-app-01 实测**：deepseek-v4-pro 经真实路径（公网→薄网关→new-api→官渠）端到端返回 `prompt_cache_hit/miss_tokens`、流式加 `stream_options.include_usage` 也拿得到、官渠未拒。**spec** `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（Codex 4 轮 APPROVED：R1 8 BLOCKER+R2 1+R3 红队 1）+ **B1 plan** `docs/superpowers/plans/2026-06-21-w2b-b1-tenant-base-auth.md`（18 TDD task，Codex **5 轮 APPROVED**：R1 8+R2 4+R3 1+R4 红队 2→R5；红队抓出 get_chat_handler 漏迁移点/independent-review 裸变量等执行即崩真洞）。分支 `feat/w2b-multi-tenant-core`（HEAD `d80d13a`，**未 push/未 merge**）。**下一步：用户定执行方式（subagent-driven 推荐）后实施 B1**；B2 中央计费 / B3 admin+安全硬化 plan 待 B1 实施后写。详见下方 🟢 服务器化 簇。）

上一次更新：2026-06-21（**N7 统一审查 + 去 AI 味（= W2-A）✅ 实施完成 + 已 merge/push**——两审查合并成一个「独立审查」+ 维度⑤语言专业性·去 AI 味（吸收 Humanizer-zh 可迁移规则）+ 占位符纯 Python 扫描 grounding + 删整条 lint/PowerShell 审查路径，审查路径自此纯 Python、零 PowerShell；**subagent-driven 实现，每 commit 后 Codex review + 收尾期 spec/quality 双轨独立 review（铁律「不合并」）全 APPROVED**（含 Task 5 计划内偏离被 Codex 独立判定 valid & necessary、Task 7+8 原子删 41 文件 −1797 行；收尾 spec 轨挖出维度⑤黑名单被压缩、已忠实转写 spec §3.3 全 literal）；后端 **1209 passed / 4 mac-realpath 环境差异**、前端 **327 passed**、`npm run build` 绿；**已 merge main（merge commit `f0de6be`）+ push origin（已核实 local==origin/main）**。cutover：`docs/superpowers/cutover_report_2026-06-21_n7-unified-review-deai.md`。（W2-B 已起 spec+B1 plan，见顶部）详见下方 N7 条。

上一次更新：2026-06-21（**N6 + W1 技术标 + R5 硬卡修复 均已 merge main + push origin（HEAD `06eec26`，已核实 local==origin/main）；轮 1 全收口**——W1 subagent-driven（预检砍掉 N6 已覆盖的 Task 5/6，10→8 task）+ Codex 双轨独立 + 整 branch 综合审全 APPROVED（quality 红队挖出 Task7 半假绿并修）；**真模型 GUI E2E（deepseek-v4-pro）亲跑通过**：bid 方法论注入/RFP 评分点结构/后置两表/字数护栏全落地，并暴露一个 pre-existing R5 硬卡（方法论声明放 `## 确认状态` 之下 parser 扫不到 → 确认大纲门对全 7 类硬卡），**同分支已修**（outline 模板内置声明槽位 + 指令点明位置，不碰 trust-boundary parser；Codex APPROVED）。后端 1207 passed / 4 mac-realpath 环境差异、前端 331 passed、token 注入块 1712≤2000。已 **merge main（`9e9a869` --no-ff，分支 `feat/w1-technical-bid-type` 保留）+ push origin（HEAD `06eec26`）**。N6 详情见下——N6 走 subagent-driven 实施，A–E+F1+F3 每阶段 codex/opus 红队 review 全 APPROVED + 整 branch 综合审 APPROVED（红队累计挖出 sentinel 越狱/压缩边界泄漏/缓存投毒 TOCTOU/客户端 id 注入等真安全洞，全修）；F4 薄网关白名单透传 + 视觉模型已上线 jp-app-01 并实测转写 200。分支 `feat/n6-attachment-pipeline` 已 **merge main（`95949ab`）+ push origin**；仅剩 F2（Windows 打包 smoke + 删 legacy 解析器；**2026-06-21 用户决定推迟到 W2 服务器化一起做**——W2 去 Windows 化本就要改解析层）。（W2 web 化后续已推进到 spec + B1 plan APPROVED，见顶部）详见下方 N6/W1 条。
> 设计期历史（2026-06-20）：N6 spec(5 轮)+plan(7 轮·红队)、W1 spec(4 轮)+plan(2 轮·红队) 均 codex APPROVED；上机只读核实 jp-app-01 拓扑后把 N6 proxy 设计从「自建 per-model 路由」简化为「透传+SELECTABLE_MODELS」。）

上一次更新：2026-06-15（**新方向：服务器化 + 多用户 Web 部署 + 标书模板** —— 2026-06-15 给领导汇报后，领导明确要求「迁服务器 / 做网页给人用 / 加用户系统」，= 上方原记的产品化方向 **b 解 park**。已定：**不拆仓库**（web 作本仓库「运行模式」、引擎共用）、范围＝**公网熟人 + 登录与工作区隔离**、试用机 **kr-web-01**[腾讯云首尔，登记在 VPS-fix 库]、模型**留 `deepseek-v4-pro` 别降级**。标书模板＝引擎级前置特性、建议先做。两者**均待设计**（下一步 brainstorm→plan）。详见下方「🟢 服务器化…」簇。R1–R5 整改簇此前已全部闭环合 main `0162ef1`。）

上一次更新：2026-06-11（**批 3 R4+R5 ✅ 全完成并合并 main `0162ef1` + 重打干净包**——subagent-driven 11 task[A1+B1-B10] + codex 双轨/红队逐 task APPROVED，commit `584abb6`→`86b6b24`，skill_engine 210/packaging 14/前端 299/build 绿；红队审实现挖出 plan 文档级没暴露的 trust boundary/并发真洞[B3 5轮分隔符绕过→归一化根治、B4 半提交→自愈+temp竞态、B7 DeepSeek 5攻击面守住]；cutover `cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md`、CLAUDE.md/AGENTS.md 已同步 R4/R5 硬约束段；GUI E2E ✅（2026-06-11 fable5 真模型 deepseek-v4-pro 全程：R4 52 条 data-log 色点[赛迪挂 news.cn 仍 🟡/财联社转述 ⚪ 印证按机构性质]+R5 大纲首行声明/章节内化/`__methodology_snapshot` 与 outline_confirmed_at 同刻冻结 全 PASS，14 commit `584abb6`→`deed618`）→ merge main `0162ef1`(--no-ff) + 清 dist/build 重打干净包(build.ps1 exit 0、前端 vite 重构、307MB) + push origin；剩 follow-up[checkpoint 写事务化/backfill 窄锁，桌面单用户低优先级]。— 批 2 R3 已 merge main `53c52fd`+push；批 1 R1+R2 已闭环 main `f111f0e`+push。整个 R1-R5 整改簇至此全部实施闭环。）

上一次更新：2026-06-09（批 2 R3 已实施 + codex **双轨独立** review 全 APPROVED[spec 轨后端/前端 SPEC-COMPLIANT + quality 轨后端/前端 APPROVED]；**应用户纠正：spec/quality 别合并塞一个 prompt**——quality 轨独立审挖出合并审漏的 2 真 BLOCKER[后端 `chat_stream` 同步 generator 跨 anyio 线程重入绕 CAS→专用 executor `8f06c81` / 前端进入编辑异步竞态→`loadFile` 同步提交选择 `d420dff`]；后端 281 passed、前端 296 pass。commits `336504a`→`ec42369`，cutover `cutover_report_2026-06-09_r3-file-tree-editing.md`。）

## 当前未解决 / 待验证

### 🟢 服务器化 + 多用户 Web 部署 + 标书模板（2026-06-15 起 · = 原产品化方向 b 解 park · 新最高优先）

来源：2026-06-15 给领导汇报后，领导明确要求「迁到服务器、做成网页给人用、加用户系统」（动因：同事都用 Mac，桌面版只 Windows 分发、用不了）。即上方原记的产品化方向 **b（部门内部共享部署）**——此前 park，现领导亲自提出，**解 park、定为下一个方向**。

**执行顺序（更新 2026-06-20）**：**轮 1 = W1 标书 + N6 附件**（两个独立引擎特性，各自 spec/plan，**不合并成一个 plan**——体量/风险差太多、稀释 review）。**N6 先做**（拥有材料层 + W1 依赖的强安全边界；且 N6 §5 接管文件 size 守门后，W1 那点后端守门退化为纯 prompt+配置）→ **W1 后**（更小）。W2 web 化排在轮 1 之后。
- **轮 1 进度（更新 2026-06-21）**：**N6 ✅ merge main + push origin + F4 上线**（F2 推迟 W2）；**W1 ✅ 实施 + 真模型 GUI E2E 通过 + R5 硬卡同分支修复，已 merge main + push origin（`9e9a869` → HEAD `06eec26`）**（分支 `feat/w1-technical-bid-type` 保留）。**轮 1（N6+W1）全收口（代码 + push）**。下一步：**W2 web 化——spec + B1 plan 已 Codex APPROVED，待实施**（见下方 W2 条）。

**已定决策（避免重新讨论）**：
- **不拆仓库**：web 是本仓库的「运行模式」（`run_web.py` 已存在 + `app.py` 桌面入口），引擎共用，web 特有的登录/多租户放界限清楚的新模块（`backend/auth/` 等）+ mode 开关隔开。曾一度建过独立仓库 `consulting-report-agent-web` 又删了。**两线都长期活跃才抽共享包 `consulting-report-core`，现在抽是过度设计**。
- **范围（2026-06-21 brainstorm 订正——推翻旧『仅熟人 / 不做开放注册』）**：**真多用户产品**——用户名+密码、**邀请码自助注册**（admin 可轮换、群发）、per-user 密码 / API / 工作区**完全隔离**、**后台管理面板**（用户列表 / 今日花费 / 改密码 / 调配额 / 禁用 / 轮换邀请码）。仍不做：角色权限体系、计费（成本靠 per-user ¥/天金额配额控）。
- **模型**：试用期默认留 `deepseek-v4-pro`，**不要降级 v4flash**（试用是证明报告质量、降模型本末倒置；成本靠 per-user 日配额控）。
- **服务器**：试用机 `kr-web-01`（腾讯云轻量首尔 2C2G+swap+40G，Debian 13，SSH 2233，已 fail2ban+ufw+komari），登记在 VPS-fix 库 `notes/kr-web-01.md`（运维不在本项目重复）。渠道商代购、**非自有账号、仅试用、转生产换自有/公司账号正经实例**。Linux 部署、不 PyInstaller、venv 跑 uvicorn。
- **成本**：先垫钱、推广试用后报销。

**W1. 标书（技术标）报告类型模板** — 状态：`✅ 完成并 merge main + push origin 2026-06-21（分支 feat/w1-technical-bid-type 保留，8 commits → merge 9e9a869 → HEAD 06eec26）；subagent-driven 实施 + Codex 双轨独立 review（spec+quality 不合并）+ 整 branch 综合审全 APPROVED；真模型 GUI E2E 通过`
- **实施记录**：cutover `docs/superpowers/cutover_report_2026-06-20_w1-technical-bid.md`。**开工前预检砍掉 Task 5/6**（plan 原「N6 落地前降级 size 守门」已被 N6 在 main 上超额实现：`size_bytes`/`content_sha256` + `material_limits.py` 的 `MAX_HEAVY_MATERIAL_BYTES` 25MB 覆盖 docx/doc/pdf/pptx/ppt/xlsx/xls）→ 10 task 实做 8。回归：后端 1204 passed / 4 failed（全 mac realpath 环境差异、实证与 W1 无关、Windows 绿）、前端 331 passed、DeepSeek 10 passed、token 注入块实测 **1694 ≤ 2000**。Codex quality 红队挖到并修了 1 真 BLOCKER（Task 7 两表落点锁测「半假绿」：只验末行、没验旧稿保留 → 强内容断言修复 `10b6ece`）。
- spec：`docs/superpowers/specs/2026-06-20-w1-technical-bid-type-design.md`、plan：`docs/superpowers/plans/2026-06-20-w1-technical-bid-type.md`（实施真值源，原 10 TDD task，实做 8）。
- **plan 关键决策（偏离 spec §3.1，已用户拍板）**：technical-bid **不注入通用 `FRAMEWORK_MENU`**——实测「骨架+RFP+后置+护栏全塞进『## 二』」叠加 531-token 菜单会爆 token≤2k 预算（最坏 2128>2000），且通用分析框架菜单对 RFP 驱动的技术标本就误导。plan Task 1 引 `_framework_menu_for_type` seam（bid 返 ""，其余 6 类不变），跳过后最坏 1679、余量 321。参考骨架据 `bid reference/` 真实样本校准（理论政策依据升格独立块前移、重难点两段式、实施管理五件套、人员附佐证清单）；模块 RFP 段强制「拟好结构先讲给用户确认/调整再展开正文」（用户：参考骨架只能参考、最终结构由人拍板）。
- **范围已定**（brainstorm 拍板）：只做**技术标主体**，主要用于**副标**（替别家公司写的陪标，内容与主标相近、字数可少、质量松——用户："更多时候只是字数差别"），质量按主标看齐做。参考样本在仓库 `bid reference/`（1 主标+3 副标真实 docx，广西电网数据资源入表；写 plan 时据此精修参考骨架）。
- **关键设计**：① 新第 7 个 `project_type=technical-bid` + `METHODOLOGY_TONE` 新腔调 `bid`，接 R5 `build_methodology_block`；② 骨架是**参考非模板**——结构由本次招标文件/技规评分点决定（RFP 驱动，参考骨架兜底）；③ 评分索引表 + 点对点应答**后置生成**（正文 append 完再 append 在草稿末尾，「写最前」交导出排版期；不用 edit_file——撞 generative-intent 拦截/cap）；④ 字数复用「预期篇幅」、不加主/副开关；⑤ 含一点轻量后端（`size_bytes` 守门，但 N6 先做后这块被 N6 吸收）。
- 危险词坑：方法论声明行框架名避开 `覆盖`/`推进` 等（在 `_METHODOLOGY_DANGER_SUBSTRINGS`，命中判 malformed），用「评分点对标/点对点应答/WBS/重难点对策」。
- 涉及：`backend/skill.py`（`TYPE_SKELETON_MAP`+`METHODOLOGY_TONE`+`_declare_and_invite_instruction` bid 分支）、新 `skill/modules/technical-bid.md`、前端 `ProjectCreateModal.jsx` 下拉、`skill/plan-template/project-overview.md` 旧类型清单同步。

**W2. 服务器化 + 多用户 Web 化** — 状态：`✅ 全线收口上线——W2-B 三段全 merge main；W2-C Part A+B merge/push + Part C ✅ 部署完成（站点 https://consulting.z0y0h.work 已对同事开放、8 步 smoke 全过）`。**W2-B**：`B1 ✅ merge（c62cd4d）+push；B2 ✅ merge（c2916b1）+push origin；B3 ✅ merge（450acba，--no-ff）+push origin`——三段（B1 租户基座 / B2 中央计费 / B3 admin+安全硬化+custom 激活）全部实施完成并合并 main，6 Phase subagent-driven + 每 Phase Codex 双轨独立 review（含红队）+ 收尾综合审 SHIP-READY。**W2-C（2026-06-23 顶部最新更新为详情真值源）**：把多租户引擎真正部署到 kr-web-01（`consulting.z0y0h.work`，反代+CF）。Part A 去 Windows 化导出（纯 Python 调 pandoc + 原子发布 + web 下载契约 + SSE 心跳 + 入口 env 化）+ Part B N6 F2 收口 + Part C 部署 runbook（交互式）。spec `specs/2026-06-23-w2c-deploy-and-de-windows-design.md`（5 轮 APPROVED）、plan `plans/2026-06-23-w2c-de-windows-export-and-deploy-prep.md`（8 task，4 轮 APPROVED）、cutover `cutover_report_2026-06-23_w2c-de-windows-export-and-deploy-prep.md`。**Part A+B ✅ merge main（ebd5312）+ push origin；Part C ✅ 部署完成上线（2026-06-23，详情见 worklist 顶部最新更新条 + memory [[w2c-deploy-status]]）：站点 `https://consulting.z0y0h.work`，kr-web-01 反代+CF 橙云+systemd 单 worker，8 步 smoke 全 PASS（managed 真链路/导出下载 docx/跨租户 404/真实 IP 透传）。交付密钥（邀请码 + admin 临时密码）见私有 memory `w2c-deploy-status`，**不入公开仓库**。** 部署后可选硬化（非阻塞）：origin 443 仅放行 CF IP 段、Origin CA Key 升 Full-strict、Page Rule→Configuration Rule、cert/凭据转生产换实例时轮换。原 backlog（非 W2-B/W2-C 范围）：原子 reserve / 多 worker 共享 `_LOGIN_FAILS`·`_RUNTIME_ALLOWED_HOSTS`·`_miss_counter` / pinned-IP-with-SNI transport（彻底防 DNS rebinding）/ CAPTCHA·MFA（彻底解登录限流三难）/ 原生 fetch SSE 401 统一跳登录。
- **B2 ✅ 实施完成 + merge main（`c2916b1`）+ push origin（2026-06-22，feat/w2b-b2-billing 分支保留）**：cutover `docs/superpowers/cutover_report_2026-06-22_w2b-b2.md`。5 实施簇 subagent-driven + 每簇 Codex 双轨独立 review 全 APPROVED + 收尾全分支综合审。落地真值源：`backend/metering.py`（`MeteredManagedClient` 单出口 + `price_micro_yuan`/`extract_billing_usage`/`_coerce_token`/`today_shanghai`/`wrap_client_for_billing`/`_miss_counter`）、`backend/accounts.py`（`usage_daily` 表 + `add_usage`/`get_usage_today`/`get_effective_daily_cap_micro`/`set_user_daily_cap_micro`）、`backend/config.py`（单价/cap/fail-closed 常量）、chat.py/independent_review.py/main.py 接线、前端 `quotaFormat.js`+Sidebar。**关键约束（CLAUDE.md「## W2-B/B2」段为长期真值源）**：5 调用点经 wrapper 自动计费、被动 include_usage（managed-only）、`finally`+`response.close()` 同步结算、usage present-but-malformed→fail-closed、metering 引用须模块限定（reload 安全）。**已知限制**：桌面 local 受 ¥5/天默认 cap（**用户 2026-06-22 决定保持默认、不豁免**）；软帽非原子 reserve；custom 生产不可达（managed-forced，**B3 激活——见上方最新更新**）；`.responses` 透传不计费（managed 不走）。
- **B2 plan（2026-06-22 已写 + Codex APPROVED）**：`docs/superpowers/plans/2026-06-22-w2b-b2-central-billing-quota.md`（14 TDD task）。Codex 5 轮 review：旧线程 R1 7BLOCKER+2NIT → R2 3+1 → R3 1+1（额度耗尽）→ **换 API 拉全新线程对抗式红队轮 R4 4BLOCKER+2NIT → 复审 APPROVED**。关键设计：managed 模式把 `ChatHandler.self.client`/`IndependentReviewAgent._build_client()` 整体换成 `backend/metering.py:MeteredManagedClient`（镜像 `.chat.completions.create`，5 调用点零改动），被动计费 reserve→settle、`finally` 同步结算 + `response.close()`、fail-closed（deepseek=256k effective、Qwen3-VL 显式锚 32768、未见 usage 保守封顶）、per-(uid,model,day) 连续 3 缺失暂停、`usage_daily` 整数微元原子累加、`/api/auth/me` 加 cost、前端账号块显额度。红队挖出并修的真缺陷：fail-closed 硬编 128k、miss-counter 暂停死锁（day 入键）、include_usage 非 managed-only、`SystemNotice` 类型（非 List[str]）、**包裹破坏既有测试**（patch OpenAI 类→wrapper 跑→无 init_db + miss 暂停，修法=测试 base 隔离 DB + 把闸门设不触发）、**custom 生产不可达却 claim §6.4**（B1 managed-forced，改诚实框定为 B3 预留+单元级）、**提前 break 抛弃 wrapper 生成器漏结算**（response.close() 同步结算）、视觉 fail-closed 非模型专属。**下一步：subagent-driven 实施。**
- **spec**：`docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（4 轮 APPROVED）。**实施拆 3 plan**：**B1** 租户基座+鉴权+创建闭环（`plans/2026-06-21-w2b-b1-tenant-base-auth.md`，18 TDD task，5 轮含 2 轮红队 APPROVED）**✅ 已实施落地**（subagent-driven + 每 commit Codex 双轨独立 review + 全分支综合审 SHIP-READY；cutover `docs/superpowers/cutover_report_2026-06-22_w2b-b1.md`；后端 1272 passed/4 mac-realpath、前端 334、真实 HTTP smoke + chrome GUI E2E 全过；merge `c62cd4d` + push origin；分支保留）/ **B2** 中央计费 `MeteredManagedClient`+per-user ¥配额（**✅ 实施完成 + merge `c2916b1` + push origin**）/ **B3** admin 面板 + CSRF/SSRF/CORS 硬化 + custom 激活 + must_change_password 强制 + per-username 限流（**✅ 实施完成 + merge main `450acba`（--no-ff）+ push origin**——分支 `feat/w2b-b3-admin-security-hardening` 保留，6 Phase subagent-driven + 每 Phase Codex 双轨独立 review 全 APPROVED + 红队对抗 + 全分支综合审 SHIP-READY；新叶子 `backend/url_guard.py`（三层域名白名单 SSRF）+ CSRF 中间件 + throttle-first 登录限流 + 8 admin 端点（`admin_set_user_disabled` BEGIN IMMEDIATE 原子守卫）+ `require_password_current` 三层覆盖 + custom 真激活（`mode` 现持久化）；后端 1419 passed/4 mac-realpath、前端 357、build 绿、DeepSeek 兼容+跨租户隔离绿；cutover `docs/superpowers/cutover_report_2026-06-23_w2b-b3.md`）。**W2 状态：B1✅merge / B2✅merge / B3✅merge（450acba）+push origin。**
- **B1 已落地的多租户运行时（实施真值源=cutover + CLAUDE.md「W2-B」段）**：`backend/tenant.py`（路径 + `tenant_project_key` 无损键 + `_safe_path_component`）、`backend/accounts.py`（SQLite + argon2 + sessions + app_config）、`config.data_root()`、main.py per-uid 工厂 + `require_project`/`ProjectScope` + `/api/auth/*`。**custom 模式 B1 仍 managed-forced**（激活归 B3）。**web 启动须设 `CRA_INVITE_CODE`**；admin bootstrap 用 `CRA_BOOTSTRAP_ADMIN_USERNAME/PASSWORD`。
- **已定关键设计（spec/plan 为真值源，实施必读）**：服务端 session+httpOnly cookie；`<data-root>/users/<uid>/` 分目录 + per-uid 引擎实例工厂；统一卡点 `require_project(uid,ref)` canonicalize 到 `rec["id"]`、查不到即 404=天然归属；进程锁/store/搜索 project 级状态全改复合键 `tenant_project_key(uid,cid)`（**含 skill.py `record_stage_checkpoint`**）；配额=金额制 ¥5/天按 deepseek 缓存三档精确计费（中央计费客户端覆盖 chat/压缩/vision/审查、整数微元、usage 缺失 fail-closed）；web 创建项目服务端分配工作区拒客户端路径；桌面 uid=local 硬绑 loopback、否则拒启动。
- 迁移工作项（设计已定，下为概览）：
  - **数据多租户隔离（最大头）**：现全挤在单一 `~/.consulting-report/` → `<data-root>/users/<uid>/projects/...`，所有经 `get_base_path()` 的寻址加 uid 层。
  - **登录 + 鉴权**：登录页 + 服务端 session（**已定 httpOnly cookie，非 JWT**）；每接口经 `require_project` 校验归属；**邀请码自助注册**（admin 可轮换，非「管理员建号」）。
  - **进程内单例 / 锁按 uid 键化**：`_SEARCH_ROUTER_SINGLETON`、`ReviewSessionStore`、各 RLock（现为单用户假设）。
  - **文件导入**：`DesktopBridge` 原生选择器（web 模式本就 503）→ 浏览器上传。
  - **去 Windows 化**：`export_draft.ps1` 改 Python（或 Linux 跑 pwsh），`pandoc.exe` 换 Linux 版（`quality_check.ps1` 部分已由 N7 删除/合并进独立审查维度⑤，审查路径已纯 Python）。
  - **账号存储**：元数据 SQLite 起步；**项目工作区仍走文件系统**（按 uid 隔离），不把文件驱动引擎搬进 DB。
  - **部署**：venv 跑 uvicorn + Cloudflare 域名 / HTTPS。
- **安全红线（web 新增威胁面，桌面版只绑 127.0.0.1 没有）**：① 每接口校验资源归属（A 不能读 B）；② `custom` 模式自填 API base 堵 SSRF；③ LLM / 搜索 per-user 日配额防滥用；④ 上传文件类型 / 大小 / 内容校验；⑤ 沿用报告内容 trust boundary（数据非指令）。
- ~~待设计开放问题~~ **已定（见 spec/B1 plan）**：会话=httpOnly cookie session；数据根=`<data-root>/users/<uid>/`；归属=统一 `require_project` 卡点（canonicalize）；并发键化=复合键 `tenant_project_key(uid,cid)` 全覆盖（锁/store/搜索 project 级状态/handler）。

### ✅ R5 方法论声明硬卡已修（2026-06-21，W1 GUI E2E 发现并当场修复，影响全 7 类）

**已修**（2026-06-21，分支 `feat/w1-technical-bid-type`）：选了**不动 parser（trust boundary）**的方案——②`skill/plan-template/outline.md` 内置方法论声明槽位（`**方法论框架**：` + 引导注释，放在 `# 报告大纲` 与 `## 确认状态` 之间＝parser 唯一会扫的顶部区）让模型镜像模板就填对位置；③`_declare_and_invite_instruction` 把「outline.md 顶部」改成「第一行（在 `## 确认状态` 等二级标题之前）」消歧义。**没碰 parser 的 head 窗 / 净化逻辑**（改 break 级会破红队 `test_parse_methodology_ignores_declaration_below_body` 的 H2-章节语义、弱化「正文里声明不算」保护——故走模板+指令）。token 注入块 1694→1712 ≤2000。回归：3 新测试 + 25 个 parse 红队测试全绿。**根因复盘留档于下**：



来源：2026-06-21 W1 技术标 GUI E2E（真模型 deepseek-v4-pro，web 模式）。现象：S1 阶段 agent 写出**有效**方法论声明行（`**方法论框架**：评分点对标响应法、WBS分解法、重难点对策分析法`，bid 框架名正确），但放在 outline.md 的 `## 确认状态` H2 段**之后**。`parse_and_sanitize_methodology` 只扫**首个 `## ` 标题之前**的 head（H1 不算），故扫不到 → 返回 `missing` → `methodology_declared` flag = False → 前端「确认大纲」按钮禁用 + 提示「请在大纲顶部补一行方法论声明（如『方法论框架：SWOT、波特五力』）」（连示例都是 SWOT，对技术标不贴）；且 `_validate_stage_checkpoint_transition` 的 `outline_confirmed_at` 分支对 `project_type in TYPE_SKELETON_MAP`（含全 7 类）**硬要求 `parse=='parsed'`** → 即便绕过前端按钮，checkpoint 也会 400。**用户在 S1 被硬卡、确认不了大纲**。
- **根因实证**（2026-06-21）：把声明行挪到首个 H2 之前（无论是否 `**加粗**`）→ parse 立即 `parsed`（3 个 bid 框架名全认）；放在 `## 确认状态` 之后 → `missing`。即纯**位置**问题，非框架名、非加粗、非 W1。
- **非 W1 引入**：parser 头窗策略 + outline 模板（`plan-template/outline.md` 首个 H2 是 `## 确认状态`）+ deepseek 摆放习惯三者交互；6 个老类型同样在 TYPE_SKELETON_MAP、同样会中招。R5 当初 E2E 用 fable5（声明放首行）没暴露；**这是首次真 deepseek E2E 走到 S1 确认**才浮现。
- **W1 本身无辜且已验证**：bid 方法论注入 + RFP 驱动评分点结构 + bid 声明腔调（评分点对标响应法/WBS/重难点对策，非 SWOT）+ 后置两表 + 先讲结构请确认 + 字数复用预期篇幅，真模型全部正确落地（见 W1 cutover「GUI E2E」段）。
- **修法候选**（待 brainstorm 选一）：① parser 放宽 head 窗口——不在 `## 确认状态` 处截断，扫到 `## 大纲结构` 前或前 N 行都收（最小改、根治）；② outline 模板 `plan-template/outline.md` 内置方法论声明占位行（在 `## 确认状态` 之前或独立 slot），引导模型填对位置；③ R5/bid 指令显式要求「方法论框架声明行必须是 outline.md 第一行正文、在任何 `## ` 之前」。建议 ①+② 组合。回归须扩 `test_skill_engine.py` 的 parse 用例覆盖「声明在 `## 确认状态` 后」。

### 🟡 mac 迁移后新一批想法（2026-06-19 · 待逐条定优先级）

来源：2026-06-19 迁到 mac、web 模式开发环境跑通后用户口述的一批想法。**多数是 W2 产品化/web 化的前置松绑或既有债的细化**，已就地查实现状，记此防遗忘。环境现状：`uv` 托管 Python 3.12 建 `.venv`（系统 3.14 太新装不了依赖）、`run_web.py` → `http://localhost:8888` 可用、私有文件就位。

**进度（2026-06-19）**：N1–N4 四个 quick win **已全部实施 + codex review APPROVED**（首轮挖 1 BLOCKER[N3 target_audience=None → `str.replace(None)` 崩]+1 NIT[N4 拖动监听卸载泄漏]，均已修 + 回归测试，二轮 APPROVED）。后端 1089 pass / 前端 305 pass / vite build 绿。N5–N7 仍待设计。
- ⚠️ **macOS 测试坑（预存、非本批回归）**：`test_skill_engine.py`/`test_workspace_materials.py` 有 **4 个用例在 mac 上失败**，根因是 macOS `tempfile` 给 `/var/folders/…` 但系统解析成 `/private/var/folders/…`（symlink），测试拿未解析的临时路径比对已解析路径 → `relative_to`/相等失败；**Windows 上通过**。属测试夹具 realpath 问题，要 mac 测试全绿需把这些用例的临时路径断言改走 `os.path.realpath`/`.resolve()`（独立小任务，未做）。
- ⚠️ **改 `managed_search_pool.json` 后需重启服务**：路由单例加载后不热重载（`_SEARCH_ROUTER_SINGLETON`），N1 限额 + N2 新 key 要重启 `run_web.py` 才生效。

**N1. 放开搜索/写作额度（quick win，纯参数）** — 状态：`✅ 完成（搜索 5/30/60 + 写作 MAX_CANONICAL_MUTATIONS_PER_TURN 10；含 test_report_writing/test_chat_runtime 同步 + CLAUDE/AGENTS doc）`
- 搜索：`managed_search_pool.json` `limits` 段现 `per_turn_searches=2 / project_minute_limit=10 / global_minute_limit=20`，**改 JSON 即生效**（路由单例加载后不热重载，要重启）。建议 `5 / 30 / 60`。
- 写正文：`backend/report_writing.py:119 MAX_CANONICAL_MUTATIONS_PER_TURN`，一行常量，**已 3→10（用户定）**。与 `342d439`（stream max_iterations 20→50）同思路。
- 动因：web/产品化不能保留单机这么紧的限制；桌面单用户时代的保守值。

**N2. ADDPOOL 搜索 key 接入做轮询（small code change）** — 状态：`✅ 完成（方案 B）`
- 落地：`config.py` `ManagedSearchProviderConfig` 加 `api_keys: tuple[...]` + `__post_init__` 与单 `api_key` 互相回填；`_parse_provider_api_keys` 兼容 `api_keys` 列表与 `api_key` 单值；`search_providers.py` `BaseSearchProvider` 加 `_next_api_key()` 线程安全轮转、`_request_payload(query, api_key)` 4 provider 统一改签名按 key 发请求；`chat.py` 工厂传 `api_keys`。ADDPOOL 5 个 key 已并入 `managed_search_pool.json`（serper 2/tavily 3/exa 3/brave 1），**daily_soft_limit 按 key 数缩放**（serper 5000/tavily 3000/exa 3000）让轮询有实际余量。`ADDPOOL.txt` 已并入并删除、且已 gitignore。
- 测试：`test_config`（多 key 解析 + 单 key 回填）、`test_search_providers`（4 次轮转 a/b/c/a + 单 key 兼容 + header 断言）。
- 原现状实锤（保留备查）：旧 schema 一 provider 一个 `api_key`、不支持同 provider 多 key（`config.py:120`）；ADDPOOL 是另一项目 env 格式（`SEARCH_*_KEYS=k1,k2`）。

**N3. 删「报告面向对象/目标读者」字段（高层/中层/执行）** — 状态：`✅ 完成`
- 落地：前端移除选择器 + initialForm 字段 + `projectCreatePayload` 不再带 + `chatPresentation` 欢迎语不再显示目标读者。后端 `models.py` 字段改**可选 default ""**（非删，向后兼容旧客户端/老项目）；`skill.py` normalize **显式归一 None/空白→""**（codex BLOCKER：原 setdefault 兜不住显式 None → `str.replace(None)` 崩）+ `_populate_v2_plan_files` 防御性归一 + 目标读者留空时项目目标句省略「面向…」避病句。独立审查维度「## 5. 目标读者匹配」保留（概念独立、报告仍有读者）。
- 测试：新增 `test_create_project_without_target_audience_does_not_crash`；前端 projectCreateModal/chatPresentation source-guard + payload 断言同步。
- 备注：HTTP 显式传 `target_audience: null` 仍会 422（schema 是 `str` default 非 `str|None`）——省略字段才是正路，前端已省略，非崩溃路径，不处理。接既有待办 #7。

**N4. 文件栏布局调整（small frontend）** — 状态：`✅ 完成`
- 落地：① 文件树高度由 `treePct` 状态驱动、**默认三七分**（30/70），去掉固定 `max-h-64`；② 新增可拖动分隔条（`startTreeResize` window 级监听 + 卸载兜底清理，codex NIT：防中途卸载泄漏）；③ `fileTree.js` **当前阶段所在分组置顶**（splice+unshift，其余保持 GROUP_ORDER 流水线序 → 上一阶段自然紧随）。拖动数学抽 `utils/filePanelLayout.js`（`clampTreePct`/`computeTreePct` 纯函数 + 单测，无 jsdom）。
- 测试：`filePanelLayout.test.mjs`（clamp/compute 边界）、`fileTree.test.mjs`（当前组置顶 + 已置顶时保序）、`filePreviewPanel.source.test.mjs`（state 高度 + 拖动 + 卸载清理 source-guard）。

**N5. 用户系统 × 自定义 API 兼容（W2 子题）** — 状态：`待设计`
- 开放问题：登录后多租户下，`custom` 模式（用户自填 OpenAI 兼容 key/base）与 managed 模式怎么并存？per-user 存各自 custom 配置？custom 模式的 SSRF/配额口子（W2 安全红线②③）。并入 W2 spec 一起设计。

**N6. 附件机制 + 图片分流（接既有 #4 债）** — 状态：`✅ 实施完成并 merge main（`95949ab`，2026-06-21）+ push origin（分支 feat/n6-attachment-pipeline 保留）；A–E+F1+F3 subagent-driven 实施、每阶段 codex/opus 红队 review 全 APPROVED + 整 branch 综合审 APPROVED；F4 薄网关+视觉已上线 jp-app-01；仅剩 F2（Windows 打包 smoke + 删 legacy 解析器，需 Windows 机；**2026-06-21 用户决定推迟到 W2 服务器化一起做**）`
- **实施记录**：cutover `docs/superpowers/cutover_report_2026-06-20_n6-attachment-pipeline.md`（回归：后端 1195 passed/4 mac-symlink、前端 329 passed、vite build ✓）。**依赖偏离 plan pin**：markitdown **0.1.6**（plan 写 0.0.1a3 无 `enable_plugins` 会崩）+ onnxruntime 1.27 + xlrd 2.0.2。**F3 全量回归亲跑逮到 1 个真回归**（图片漏出素材清单 + 文件名未消毒，已修）。F4 上线见 `docs/managed-proxy-deployment.md`「N6 视觉转写」段 + `VPS-fix-private/notes/jp-app-01.md`。
- spec：`docs/superpowers/specs/2026-06-20-n6-attachment-pipeline-design.md`；plan：`docs/superpowers/plans/2026-06-20-n6-attachment-pipeline.md`（A–F 六阶段 ~25 TDD task）。
- **关键设计**：① 文档 **markitdown 全替换**（删 `_read_docx/_xlsx/_pdf`，新增 pptx/老 .doc/.ppt 经 LibreOffice headless）；② 图片**三级降级**——多模态主模型直喂 / 否则**视觉模型转写**（`Qwen/Qwen3-VL-8B-Instruct`）/ OCR（RapidOCR）兜底 / 友好失败；统一覆盖**持久图片材料 + transient 两路**，结清 #4（删前端 `supportsImageAttachments` 拦截）；③ 新模块 `backend/material_conversion.py:MaterialConverter`（依赖注入、不反向 import chat.py）；④ 缓存内容 hash + refcount + 原子写 + tombstone；transient 转写存消息独立字段 `attachment_transcripts`、不污染意图、防注入数据块包裹 + 压缩边界。
- **薄网关已上机核实拓扑（2026-06-20 SSH 只读）**：薄网关 = `consulting-report-managed-proxy` 容器在 **jp-app-01**（`43.153.168.175:2233`，连接走 VPS-fix 库 `notes/jp-app-01.md`），上游指**本地 new-api**（`127.0.0.1:3000`）；**new-api 已按模型名路由**，**硅基流动渠道 id 60 已配已启用、含 Qwen3-VL 一批**。故 proxy 改 = **白名单透传 + 新 `MANAGED_PROXY_SELECTABLE_MODELS`**（`/v1/models` 仍只露 deepseek-v4-pro），**不需自建 per-model upstream**。**✅ F4 ops 已完成 2026-06-21**：proxy env 加 `ALLOWED_MODELS`(含 Qwen3-VL)+`SELECTABLE_MODELS`+重建容器；并发现 new-api 前置坑（上游 token model_limits 只准 deepseek + 渠道 60 group/abilities 缺 ds）一并配通；视觉转写 200 实测过。备份+回滚见 `VPS-fix-private/notes/jp-app-01.md`。
- **N6 先于 W1**：N6 接管材料层 + size 守门 + 材料 trust boundary（W1 安全强边界依赖它）。

**N7. S5 质量审查重做：合并成一个 LLM 独立审查 + 去 AI 味（吸收 Humanizer-zh）** — 状态：`✅ 实施完成（2026-06-21，subagent-driven，每 commit 后 Codex review + 收尾期 spec/quality 双轨独立 review 全 APPROVED；后端 1209 passed/4 mac-realpath、前端 327 passed、build 绿；**已 merge main（`f0de6be`）+ push origin**）`（属 W2 服务器化的 **W2-A**，先于 W2-B 多租户、W2-C 部署+剩余去 Windows 化）
- **决策（brainstorm 定）**：两审查合并成**一个「独立审查」按钮**——独立审查 5 维度删「目标读者匹配」（N3 已删该输入字段、维度悬空）、加第 5 维「语言专业性与去 AI 味」（誊入 Humanizer-zh 18 类✅可迁移规则 + 黑名单，prompt 写死排除❌口语/第一人称/情绪自白）；删 `quality_check.{ps1,sh}` lint 脚本 + lint-report 路径；加十几行纯 Python 占位符扫描作 grounding 注入（首轮、对用户隐形）。审查路径自此纯 Python、零 PowerShell。
- **关键设计**：扩展 `IndependentReviewAgent`（不新建）；抽 `backend/trust_boundary.py`（解循环导入 + 新 `UNTRUSTED_DATA_*` marker）+ 新 `backend/report_quality.py`（占位符扫描）；门禁 `review_passed_at` 改单报告；S5 checklist 3→2 + cascade；Task 7+8 删 lint 路径为**一个原子 commit**（删接口与删消费者不可分）。
- spec：`docs/superpowers/specs/2026-06-21-n7-unified-review-deai-design.md`；plan：`docs/superpowers/plans/2026-06-21-n7-unified-review-deai.md`（9 TDD task）；**cutover：`docs/superpowers/cutover_report_2026-06-21_n7-unified-review-deai.md`**。分支 `feat/n7-unified-review-deai`。
- **实施记录**：trust_boundary.py 叶子模块（解循环导入 + UNTRUSTED_DATA marker）+ report_quality.py 占位符扫描已落地；门禁/checklist/cascade 全改单报告；lint 全路径（后端/前端/脚本/模板/9 处文档/根 CLAUDE+AGENTS.md）原子删除。残留 grep 仅退役条目 + 负向守卫，无 live 引用。
- Humanizer-zh（op7418）实证为**纯 prompt skill**（无脚本/词表），可借的就是 prompt 内容本身。
- 关联：W2-C 去 Windows 化只剩 `export_draft.ps1` + Linux pandoc（quality_check 部分本 N7 已删/合并）。

### 🔴 领导评审反馈整改（2026-06-05，高优先簇 · 整体高于下方「UI 重构」一档）

来源：报奖后领导评审反馈 + demo 现场暴露的问题，逐条 brainstorm 后落项。产品化定位决策：**a（更多同事各自在本机用）优先**，但 a 当前无真实痛点、本机桌面即最合适形态，暂不动；**b（部门内部共享部署）是领导真正想要的方向**——此前 park，**2026-06-15 领导亲自提出、已解 park 并定为下一个方向，单列于上方「🟢 服务器化 + 多用户 Web 部署」簇**；**c（对外产品）不做**。故本（R1–R5）簇仍不含产品化排期，产品化排在新簇。

**执行策略（2026-06-06 敲定 · 分 3 批，不一次性）**：批 1 = R1+R2（同在 S5 审查/chat 触发链路；R2 注入修法是 R1 触发注入的子集，合一个 plan）→ 批 2 = R3（工作区前端重构 + 后端写接口，独立子系统）→ 批 3 = R4+R5（标注/显示 + 提示词，轻）。**批 1 先走**（最高优先 + 是 demo 现场领导亲见的硬伤，观感边际收益最高）。一次性全做不可取：各批子系统不重叠，且 R1 断点续审有架构不确定性，绑一起会稀释 review。**动机分层**：批 1/批 2 是用户自驱的痛点（自己想改）；批 3 才是领导提的点（主要应答领导），R4/R5 具体形态到批 3 阶段再定（见各条「补充思路」）。

R1. **S5 子代理「独立审查」重做为迷你聊天界面 + 断点续审**
- 状态：`✅ 已闭环并合入 main（C1-C6 全绿，codex 三轨 APPROVED；已 merge f111f0e+push origin，唯一剩余＝用户手工 GUI E2E·非阻塞）`（plan：`docs/superpowers/plans/2026-06-07-s5-review-mini-chat-and-resume.md`；spec v16：`docs/superpowers/specs/2026-06-06-s5-review-mini-chat-and-resume-design.md`；cutover：`docs/superpowers/cutover_report_2026-06-07_s5-review-mini-chat.md`）
- 实施记录（feat 分支 `feat/s5-review-mini-chat-and-resume` 已 merge 进 main `f111f0e` 并 push origin、本地分支已删；subagent-driven + 每 commit codex review）：
  - ✅ C1 R2 触发注入（0ec2e13+7f8b9d4+276b7c8）· ✅ C2 流式 agent + ThinkingStreamParser→stream_parsing.py（ddba13f+4e20a9b）· ✅ C3 ReviewSessionStore 两锁/CAS/锁内原子替换/candidate staging/自修≤2/resume（7fea285+abed413+448b265）· ✅ C4 POST/resume/discard endpoint + ChatRequest metadata（2fc16b4+456373b）— C1-C4 全双轨 APPROVED
  - ✅ C5 用户可见 cutover（前端 ReviewChatWindow + run-bound 注入）：`67158f8` cutover → `b2063c6` 双轨 fix（SSE EOF 可续 + supplement 输入 + run-bound 锁外 yield）→ `1360c3e` 红队 B1+B2（切项目孤儿 + stale pending）→ `d9fe6c9` 红队 B3（Starlette pre-stream disconnect 致 review lock 永久泄漏：worker 创建移出 generate 到 endpoint 函数体）。**codex 三轨 APPROVED（spec/quality/红队）；红队挖出并修复 3 个真 BLOCKER（非诱导式秒过）**。
  - ✅ C6 回归矩阵核对 spec §6 零缺口（codex R1 必补三类 os.replace/staged-write-resume/mtime 大整数全已有测试）+ cutover report
  - 测试全绿：后端 test_main_api+independent_review+skill_engine **253 pass**、chat_runtime `-k` targeted **31 pass**、前端 `node --test` **253 pass**、vite build 通过。mtime/run_id 全程 str 无 Number 强转。
  - **剩**：真实 GUI 手工 E2E（用户·非阻塞，见 cutover report「手工验收待办」：流式旁白 / 模拟断连续审 / 点审查后立刻切项目不卡死该项目审查）。merge+push 已完成（f111f0e，origin/main==main），本地 feat 分支已删。
  - ~~预存无关 bug（收尾后查）：`tests/test_lint_report.py` 截断测试在本机 PowerShell 即失败~~ → **已被 N7 消解**：`tests/test_lint_report.py` 与 `skill/scripts/quality_check.ps1` 均在 N7 整删（lint 路径合并进独立审查），该 bug 不再存在。
- 现象（demo 暴露）：① 审查跑很久时只能看到子代理"调用了哪些工具"，**看不到它输出的文字**，体感像死机；② 后端一抖/断连就报错，抽屉 3 秒自动关，**活全丢、无法从断处继续**；③ 抽屉只能 ESC 关，不能拖/缩/关，无进度。
- 目标形态：把 `IndependentReviewDrawer` 重做成**迷你版主代理聊天界面**——实时显示子代理文字输出 + 工具调用（复用主聊天面板的渲染）。
- 交互：正常审查时**输入框锁定**（自动跑）；**仅报错/断开时解锁输入框**，用户在断掉处输入、让子代理**带累计上下文从断处继续审**（非重头跑）。
- 技术要点：(a) 审查 LLM 调用 `stream=False`→流式，并把 content 增量作为 SSE 事件推给抽屉渲染（现 `independent_review.py` 非流式、且只 forward 工具/进度事件，是"看不到文字"的根因）；(b) 续审需**持久化/保留审查会话状态**，断后能继续（现完全无 resume/checkpoint）；(c) 抽屉换成可关/可缩/带进度的面板，报错不再自动消失、给"继续/重试"。
- 涉及文件：`backend/independent_review.py`、`backend/main.py`(SSE endpoint + 锁)、`frontend/src/components/IndependentReviewDrawer.jsx`、`ChatPanel.jsx`(复用渲染)。

R2. **S5「AI 味自查」主代理答非所问修复**
- 状态：`✅ 已实施闭环（= C1，codex 双轨 APPROVED）`（R2 注入修复就是 C1：报告全文作 user/context 数据注入[非 system，trust boundary] + 汇报轮禁工具[请求层 pop tools + 响应层硬拦截 _execute_tool] + 注入前 ready fail-fast；commits 0ec2e13+7f8b9d4+276b7c8）
- 现象：AI 味脚本跑完 → 主代理那一轮有时不针对 `plan/lint-report.md` 内容回复（非网络问题）。
- 根因：触发那轮只给"请用 read_file 读报告"的指令，**读不读全靠模型自觉**，无保证它真读了报告才开口。
- 修法：不依赖模型自读，**直接把 lint 报告内容/结构化摘要注入那一轮上下文**；R1 独立审查触发同理。
- 涉及文件：`backend/chat.py`(`system_trigger` 分支)。

R3. **工作区文件栏重做 + 预览框可编辑（＝UI 重构第一落地块）**
- 状态：`✅ 完成 — 用户 E2E 通过（跑通真报告）+ 已合并 main（merge 53c52fd，--no-ff）+ 重打干净包，待 push origin` — 9 task subagent-driven 落地（后端 T1-T4 + 前端 T5-T8），commits `336504a`→`ec42369`（14 个）+ merge `53c52fd`。**双轨独立 review**（应用户要求 spec/quality 分开做、不合并）：spec 轨后端+前端均 `SPEC-COMPLIANT`；quality 轨后端+前端均 `APPROVED`，挖出合并审漏掉的 2 真 BLOCKER——①后端 `chat_stream` 同步 generator 跨 anyio 线程持 RLock、保存 `run_in_threadpool` 复用 owner 线程→重入绕 CAS→保存改专用 `ThreadPoolExecutor`（`8f06c81`）；②前端进入编辑异步竞态（切文件本身异步、currentFile 滞后，epoch 仍漏）→`loadFile` 同步提交选择消除 pending 窗口（`d420dff`）。cutover：`docs/superpowers/cutover_report_2026-06-09_r3-file-tree-editing.md`。plan：`docs/superpowers/plans/2026-06-08-workspace-file-tree-and-editing.md`、spec：`docs/superpowers/specs/2026-06-08-workspace-file-tree-and-editing-design.md`。9 task、TDD、后端先于前端、只读先于可写。
- **范围（已定稿，brainstorm 4 轮拍板）**：现框架内小切口（D1，**不换肤**——3 套 `design_UI.pdf` 是整体视觉探索、作独立后续项目，仅借鉴稿3「按阶段分组」IA）+ 第一批做 **①+②**（D2）：①文件栏分层中文名+当前阶段置顶高亮 ②预览框可编辑+后端用户写接口。③图片附件分流/新建项目表单整理 → 后置 **R3③**。
- **关键设计（spec 是真值源，写 plan 前必读 spec 全文）**：
  - **权限边界（硬骨头）**：新增 `validate_user_write` **独立白名单门禁**，**不复用** LLM 的 `validate_plan_write`（后者带 outline `_requires_pre_outline_evidence` gate，且 independent-review/lint-report 的拒写在 chat.py 工具层、HTTP 写接口走不到）。**8 个可编辑**：`content/report_draft_v1.md` + `plan/{outline,research-plan,notes,references,data-log,analysis-notes,presentation-plan}.md`；其余只读（`project-overview`[D3 只读]/审查报告/`stage-gates·progress·tasks`/`delivery-log`/`review.md`）；退役不显示。白名单比对用 `_canonical_user_path` 对整路径 casefold（**不复用**只处理 plan 的 `_canonicalize_plan_markdown_path`）。
  - **后端**：`GET /files` 返结构化 `[{path,group,stage,editable,mtime_ns}]`（`FILE_SEMANTICS` 键=**完整相对路径**[非 basename，否则 materials/imported/outline.md 误判 S1]、stage 文件级[S1 outline/S2 data-log/S3 analysis-notes/S6 presentation-plan/S7 delivery-log]、跳过 `materials/`）；`GET /files/{path}` 增返 `{content,mtime_ns,editable}`；`POST /files/{path}` **全段持 `_get_project_request_lock`**（与 chat.py:3216 同锁，防 TOCTOU）+ mtime CAS(409) + 原子写 os.replace；mtime_ns 全程 **opaque str**；异常 PermissionError→403/ValueError→400·404。
  - **D6 review_stale**（红队 BLOCKER 修正）：改正文后两份报告存在且 `draft_mtime>min(报告mtime)` 即标 `review_stale` advisory（**不 gate 在 `review_passed_at`**[覆盖「报告生成→改正文→还没点通过」窗口]、不强制清 checkpoint、不硬阻 S6/S7）。
  - **D7 CSRF**：记既有债（allow_origins=*），R3 不解决全局，白名单限可写面到 8 个用户内容文件。
  - **前端**：文件树分组+中文名(`FILE_DISPLAY_NAMES` 按完整 path)+置顶高亮；预览/编辑双模式 **textarea raw**（富文本 v2）；dirty **`guardLeave`** 覆盖所有离开路径（切文件/项目/tab/刷新/PyWebView 关窗/saving 期间禁离开）。无 jsdom → 新 `utils/{fileTree,fileEditState}` 纯函数测 + source-guard。
- **实施切分（spec §12，6 步，后端先于前端、只读先于可写）**：① `FILE_SEMANTICS`+`is_user_editable`+`GET /files` 改造(纯只读零风险) ② `validate_user_write`+`POST /files`(锁+CAS+原子写) ③ `review_stale`+workspace flag ④ 前端文件树 ⑤ 前端编辑双模式+`guardLeave` ⑥ 回归+cutover。
- **用户已接受 D6/D7**。涉及文件：`backend/skill.py`(语义+门禁+原子 write_file，`validate_user_write`/`FILE_SEMANTICS`/`_canonical_user_path`)、`backend/chat.py`(canonical draft `edit_file` 直写改走 write_file，`:4238`)、`backend/main.py`(GET 改造+POST 写接口+锁)、`frontend/src/components/{WorkspacePanel,FilePreviewPanel}.jsx`+`App.jsx` + 新 `utils/{fileTree,fileEditState}`。
- **plan codex review 改进（vs 原 spec，已同步进 plan + spec）**：① **GET `/files/{path}` 不持锁 + stat-before-read**（实测 `chat_stream` `chat.py:3217` 整轮持锁，GET 进锁会冻结预览整轮）；② **`write_file` 原子化**（temp+os.replace；含 canonical draft `edit_file` 直写 `chat.py:4238` 改走 write_file——append/edit/write 三条 AI 写路径单点原子化，消除 torn read，GET 不持锁才成立）；③ **review_stale gate 在 `_has_effective_review_reports`**（非仅「存在」——避开 create_project scaffold 的 independent-review/lint-report 模板误判）〔**N7 已改单报告 `_has_effective_independent_review`、lint-report 路径整删**〕；④ **脏离开 v1 三按钮「保存/放弃修改/取消」**（延后动作模式，应用户要求做进 v1，非 spec 初版二选一；beforeunload 受原生限制仍二选一）；⑤ 守卫接口统一为 `attemptLeave(action)` 贯通 FilePreviewPanel→WorkspacePanel→App。
- 参考设计稿 `docs/design_UI.pdf`（3 套，借鉴稿3按阶段分组 IA）。

R4. **资料来源可信度：内置三档 + data-log 色点标注 + S2 阶段小结（advisory，不门禁）**
- 状态：`✅ 完成（2026-06-11）——R4 全在 A1 一个 commit 584abb6（三档+色点+S2 小结+守护测试，纯 prompt 改 SKILL.md，codex 单轨 APPROVED）；cutover docs/superpowers/cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md；GUI E2E ✅ 覆盖（2026-06-11 fable5 真模型：52 条 data-log 每条「来源」行带色点，赛迪研究院挂 news.cn 域名仍 🟡、财联社转述企业高管 ⚪，实测印证「按机构性质非域名」）`
- spec：`docs/superpowers/specs/2026-06-10-r4-source-credibility-annotation-design.md`
- 背景：领导担心 AI 找的资料可信度不高；现状只校验"有没有来源"（data-log 有效来源计数放行 S2→S3），不校验"可不可信"。
- 2026-06-09 数据论证（否掉软白名单）：两份真实报告每份仅 3-4 域名、7 源 6 高可信 → 软白名单"确认一句"打断≈0 但价值也弱 → **回归纯标注**。
- 决策（v1，纯 prompt 改 `skill/SKILL.md`，不动 backend）：① 内置**三档**（按机构性质非域名——data-log 来源含 material/访谈/调研，一半无域名）：🟢 高=政府/官方统计/国家级权威机构、🟡 中高=有公信力媒体与研究机构、⚪ **其他**=兜底（中性≠差，含企业官网/财报等可靠一手）；② 模型在 **data-log 每条 `**来源**` 行**标色点，references 顺带、analysis/正文靠 `[DL-id]` 继承不重标；③ **S2 采集告一段落报分布小结**（🟢X/🟡Y/⚪Z + 低质点名 + 补源建议）；④ 全程 advisory。
- 风险提醒口径：挂**低质特征**（个人博客/营销软文/内容农场/来源不明），**不挂"档位低"**（防误报一手官方源）；"其他"配 ⚪ 非 🔴。
- v2/后路：后端按域名**确定性盖章**（骑 `_count_valid_data_log_sources` 解析）+ 精确分布、一手/二手维度、HTML badge（预览 rehypeRaw 已支持）、常驻证据统计条。

R5. **方法论路由接回 + 显性化 + S1 软确认/可换**（原"可见不可选"，2026-06-10 摸清现状后重定范围）
- 状态：`✅ 完成（2026-06-11）——subagent-driven + codex 双轨/红队逐 task 审到 APPROVED：A1✅(584abb6) / B1✅(2f47a4d 删死码) / B2✅(5e26607 骨架) / B3✅(e6b4d8a 净化三态，红队 5 轮挖分隔符/checkpoint 拆词/零宽绕过→归一化根治) / B4✅(681a085 快照持久化，红队挖 KeyError半提交→自愈/固定temp→mkstemp) / B5✅(996137b 确认门前置+legacy不规退) / B6✅(facdb44 build_methodology_block 装配+三腔调+token≤2k) / B7✅(e0b4ae8 chat 接入+methodology_declared flag，DeepSeek 兼容守住) / B8✅(09a01f5 SKILL.md 路由改写) / B9✅(b3467a5 前端确认按钮) / B10✅(回归+cutover)；测试基线 skill_engine 210 / packaging_docs 14 / chat_runtime targeted 12(含 DeepSeek) / 前端 299 / build✅；cutover docs/superpowers/cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md；GUI E2E ✅（2026-06-11 fable5 真模型：大纲首行方法论声明 + 章节内化四框架[PEST/波特五力/TAM-SAM-SOM/影响-可行] + `__methodology_snapshot` 与 outline_confirmed_at 同刻冻结 + 危险声明净化判 malformed/确认按钮禁用 全 PASS）+ merge main `0162ef1`；剩 follow-up（checkpoint 写事务化/backfill 窄锁，桌面单用户低优先级）`
- spec：`docs/superpowers/specs/2026-06-10-r5-methodology-routing-and-visibility-design.md`
- **重大现状发现（2026-06-10 摸清，源码实锤）**：方法论路由在 canonical skill（`D:\MyProject\CodeProject\consulting-report-skill`）里**本来设计过**（`docs/module-routing.md` + `evals/capability-map.json`，机制是模型 read_file 自取），但嵌进 app 后**断了**——`get_skill_prompt`(`skill.py:2315`) 只注入 SKILL.md + consulting-lifecycle.md（无类型分支）、`read_file`(`skill.py:1079`) 锁工作区够不到 skill 目录、`get_template`(`skill.py:2326`) 死代码；**17 模块里 16 个从不加载**（用户那份"很完美"的真实报告对话记录里全程零 `modules/`）。即领导"没让用户选方法论"的真问题不是"选不选"，是**根本没按类型用方法论**。
- **重定设计（spec）**：① 代码注入（push）替失效的模型自取：后端按 `project_type` 注入"类型骨架"（仅 S1–S4）；② 框架（SWOT/BCG/金字塔…）拆出做**共享菜单**（横向不绑类型，菜单一行 + 模型自有知识，~300-400 token）；③ 显性化＝S1 大纲声明所用框架，**按类型三腔调**（分析型 SWOT/BCG / 文体方案型 SMART·RACI·章-条-款-项 / 专项研究条件）；④ S1 软确认/可换骑现有"确认大纲"门，**确认时快照**框架（`stage_checkpoints.json` 保留键 `__methodology_snapshot` + cascade 保留）跨轮稳、legacy 不规退；⑤ **不新增模型工具**（图表维持脚本交付，自动渲染 out-of-scope）；删 `get_template`/`templates`。
- 涉及文件：`backend/skill.py`(`build_methodology_block`/`load_type_skeleton`/快照读写/`FRAMEWORK_MENU`)、`backend/chat.py`(`_build_system_prompt` 装配)、`skill/SKILL.md`、前端近零改(`methodology_declared` flag + S1 确认按钮)。
- **A/B 验证（诚实声明，非阻塞）**：模块内容质量从未被验证（连 canonical evals 也只验路由格式不验质量、`run_evals` 是 schema 校验）；落地后建议同选题"裸跑 vs 注入"比一版，几乎无差则缩为"仅声明 + 删死模块"。
- 历史背景（原决策已被现状发现部分推翻）：原拟"不做让用户选 + S1 点明框架"；摸清后发现框架根本没注入，遂升级为"先接回路由再谈可见"。涉及文件原列的"4~6 套模板"= `skill/templates/`（死代码，spec 决定删）。
- **R5 实施期红队挖的 follow-up（非阻塞，B4/B5 红队 APPROVED 前提，记此防遗忘；合并为"stage_checkpoints 写事务性强化"，桌面单用户低优先级）**：① **checkpoint 写事务化**——`record_stage_checkpoint` set 的 `outline_confirmed_at` + `__methodology_snapshot` 两阶段写改一次原子 raw 写（消除崩溃半提交：crash 落两写之间 + 用户随后改坏 outline 声明 → 原确认快照永久丢失；危害仅退 missing 兜底、非 BLOCKER）；② **backfill 窄粒度锁/CAS**——`_backfill_stage_checkpoints_if_missing` 无锁与 record 并发理论 TOCTOU（pre-existing，已加"写前重读 PRESERVED 合并"纵深防御，不用 request_lock 避卡 summary）；③ backfill PRESERVED 合并"有则覆盖"不 pop（latest 已删 snapshot 时不复活，仅损坏 raw 边界）。

### 既有待办（原 P1–P10，优先级低于上方整改簇）

1. **P1：managed 真实模型长链路 timeout / 无首包**
- 状态：`暂不处理（用户已切官渠绕过）`
- 现象：2026-05-19 实测中，真实模型 S0 首轮和一次 `advance_stage` 可工作，但后续请求出现上游 timeout / 长时间无首包；确定性打包态 S0-S7 阶段机已通过
- 用户决策（2026-05-21）：暂时切换到 DeepSeek 官渠绕过，本条不影响 S5 redesign 推进
- 后续可选：区分网关/渠道问题与应用重试 UX 问题，必要时增加 no-first-byte 观测日志和用户可理解的恢复提示

2. **P2：打包 / 前端小债**
- 状态：`待清理`
- 当前明确项：`favicon.ico` 404、输入框缺少 `id` 或 `name` 的可访问性提示、`npm audit` high、Vite chunk warning、PyInstaller conda warning

3. **P3：v1 chunk fallback（超 100k 字 map-reduce 重审）**
- 状态：`低优先级`
- 背景：S5 Independent Review Redesign v0 对 100k 字以上正文采用 friendly fail，提示用户精简后重试；这是有意保守策略，避免在 cutover 期引入 chunk 聚合复杂度。
- 目标：后续如真实长文需求频繁出现，再设计 map-reduce 重审：按章节切片审查、合并 5 维度发现、保留来源章节定位，并继续保证 `plan/independent-review.md` 只有独立审查代理可写。
- 关联：S5 cutover report [docs/superpowers/cutover_report_2026-05-22_s5-redesign.md](superpowers/cutover_report_2026-05-22_s5-redesign.md)

4. **图片附件能力按 managed_model 分流**（与 DeepSeek Migration 同期发现）— ✅ **由 N6 结清 2026-06-21**
- 解法不是「前端拦截/分流」而是 **N6 转写管线**：纯文本主模型（如 deepseek-v4-pro）传图时后端走视觉模型转写/OCR/友好失败，故图片**永远可上传**（`supportsImageAttachments` 恒 true，删了拦截分支）。多模态主模型仍直喂 `image_url`。详见 N6 条 + cutover。

5. **UI 重构**
- 状态：`待立项`
- 设计稿：`docs/design_UI.pdf`（用户用 Claude design 做的 3 套初步设计稿）
- 触发条件：当前打包 GUI 已恢复可打开；后续如立项 UI 重构，先单独定范围，不要把渠道稳定性和打包小债混进大重构里

6. **stage-advance-gates Bug G/H 低优先级待复核**
- 状态：`低优先级待复核`
- Bug G：回退 checkpoint 后 `content/*.md` 仍存在，状态可能不自洽；复核时决定级联清理还是 UI 标红提示。
- Bug H：S1 回退后 UI「下一步建议」显示"暂无"，`next_stage_hint` S1 分支缺；复核时补齐提示或确认新版流程已绕开。

7. **新建项目表单与废 UI 整理**（待 UI 重构时并入/评估）
- 状态：`待 UI 重构时评估`
- 目标：清理"填了像没填"的字段、重复输入项和旧流程遗留 UI，包括截止日期控件、材料/备注语义重叠、项目类型/主题/目标读者/篇幅字段利用率。
- 关联：Task 7 的 `length_fallback` chip 目前只是非交互提示；如做项目表单 edit 模式，可顺便让 chip 点击打开编辑面板。

8. **`draw.io skill` 评估**
- 状态：`待评估`
- 目标：判断它对咨询报告场景是否真有价值，还是只会增加复杂度。

9. **前端生产包优化**
- 状态：`待优化`
- 现状：`vite build` 已通过，但主 JS chunk 仍接近 `1 MB`。
- 目标：在不引入复杂度失控的前提下做基本拆包，降低首屏和构建产物压力。

10. **技术债清理**
- 状态：`待清理`
- 当前明确项：`pydantic` deprecation warning、打包依赖排除空间。

## 已解决记录

0g. **S5 Independent Review Redesign（2026-05-22）**
- 状态：**已解决（2026-05-22）**
- 修复：旧 `review-checklist.md` 模型自评路径退出生产推进路径，S5 改为用户主动触发「独立审查」与「AI 味自查」；两份报告分别落 `plan/independent-review.md` 与 `plan/lint-report.md`，主代理只读报告并与用户讨论修改。
- 兼容性：旧项目里的 `review-checklist.md` 保留为用户数据；`_has_effective_review_checklist` 保留为 backwards-compat helper，但 `review_passed_at` 生产路径改为校验新两份报告。老项目升级后不会阻断 S0-S4，若 S5 推进缺报告，错误消息会引导用户点击新按钮。
- 验证与限制：自动化收尾覆盖 packaged smoke 断言、endpoint lock/SSE/summary 测试和文档同步；`build.bat` 重建、piggy-v2 GUI E2E、打包内容复验仍需用户手工执行。
- Cutover report：[docs/superpowers/cutover_report_2026-05-22_s5-redesign.md](superpowers/cutover_report_2026-05-22_s5-redesign.md)

0f. **Packaged QA 前四个阻断/一致性问题修复（2026-05-13）**
- 状态：`已修复并重打包验证`
- 修复：
  - GUI 首屏崩溃：`supportsImageAttachments(settings)` 兼容启动期 `settings === null`，消除 `Cannot read properties of null (reading 'mode')`
  - `quality_check.ps1`：Windows PowerShell 脚本改为 UTF-8 with BOM，并补直接执行 smoke
  - `export-draft`：用户决策为随 Windows 包带 Pandoc；`consulting_report.spec` 将 `pandoc.exe` 打入 `_internal`，导出脚本优先使用包内 Pandoc
  - checkpoint API 越级推进：`record_stage_checkpoint()` set 下游 checkpoint 时校验前序 checkpoint 链，报告+演示模式下归档仍要求 `presentation_ready_at`
- 验证：
  - frontend `node --test tests\`: 184 passed
  - frontend `npm run build`: passed（仍有既有主 chunk 过大 warning）
  - backend `.venv\Scripts\python.exe -m pytest tests -q -n 8`: 852 passed / 1 skipped / 20 warnings / 22 subtests passed
  - PyInstaller 重建 `dist\咨询报告助手\` 成功；包内 `pandoc.EXE` 存在，当前包体积约 307 MB
  - packaged smoke：exe 启动、`/api/health`、项目脚手架、`quality-check`、`export-draft` 全通过
  - 浏览器打开 `http://127.0.0.1:8080/` 首屏正常渲染，不再显示「应用出错」

0f. **stage conductor v0 阶段推进清理（2026-05-19）**
- 状态：`已解决；打包态确定性 S0-S7 与 Markdown 表格渲染已验证`
- 覆盖问题：checkpoint API 越级推进、写作阶段与 checkpoint desync、legacy `<stage-ack>` 被误当作运行时推进信号、`settings.mode=null` GUI 启动崩溃、Windows PowerShell 脚本源码/输出编码、聊天 Markdown 表格原样显示。
- 当前规则：阶段推进 / 回退只能通过 `advance_stage(checkpoint_key, action, reason)`，并由 `SkillEngine.record_stage_checkpoint()` 统一校验前序阶段、实质文件和质量门禁；`POST /api/projects/{id}/checkpoints/{name}` 也委派同一服务，不能绕过前序阶段。
- 已关闭的旧路径：用户强关键词不再触发 checkpoint side effect；legacy `<stage-ack>` 只作为历史残留做后端 / 前端剥离，不再设置 checkpoint。若畸形 legacy tag 未被 sanitizer 命中，残留风险只剩可见文本污染，不再是阶段推进风险。
- 回归入口：`tests/test_skill_engine.py` 覆盖 transition validation；`tests/test_chat_runtime.py` 覆盖 `advance_stage`、强关键词无副作用、legacy tag 无 checkpoint；`tests/test_main_api.py` 覆盖 checkpoint endpoint；`tests/test_packaging_docs.py` 锁定 `skill/SKILL.md` 不再含 stage-ack 指令。
- 打包态记录：[2026-05-19 stage conductor packaged QA](superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md)

0e. **DeepSeek 官渠 tool-call 400 根治 + 打包态后端 S0-S7 QA（2026-05-13）**
- 状态：`代码已修复；打包态后端生命周期已跑到 done；当时遗留的 GUI 启动崩溃已在 2026-05-19 关闭`
- 根因：
  - DeepSeek 官渠 reasoner route 会拒绝显式 `tool_choice="auto"`
  - thinking/tool-call follow-up 需要把非空 `reasoning_content` 随 assistant tool-call message 回传
  - OpenAI SDK `model_dump()` 可能携带 `reasoning_content: null` / `audio: null` 等字段，官渠会拒
- 修复：
  - DeepSeek 模型请求保留 `tools`，但不显式传 `tool_choice`
  - stream / non-stream tool-call follow-up 保留非空 `reasoning_content`
  - assistant tool-call message 改为只序列化 provider 需要的字段，丢掉 null SDK dump 字段
- 验证：
  - targeted regressions 通过
  - `tests/test_chat_runtime.py -q -n 8`: 430 passed / 1 skipped / 20 warnings / 17 subtests passed
  - `tests -q -n 8 --ignore=tests/test_chat_runtime.py`: 409 passed / 8 warnings / 5 subtests passed
  - source canary：真实 `deepseek-v4-pro` 完成 `read_file → tool result → final reply`，0 error
  - packaged canary：`dist\咨询报告助手\` 完成同一 tool-call round trip，0 error
  - packaged S0-S7 API lifecycle：最终 `done / 已归档`
- Handoff：[2026-05-13 packaged S0-S7 QA handoff](superpowers/handoffs/2026-05-13-packaged-s0-s7-qa.md)

0d. **DeepSeek Migration Commit 1-3 + cutover report 完成（2026-05-09）**
- 状态：`已完成；packaged QA 后续已转入 0e，并在 2026-05-19 完成 stage conductor 打包态复测`
- Commit chain：`69730c7 Add migration toolset foundation` → `118f383 Cut traffic from legacy report draft tools` → `9a59955 Delete legacy report draft control layer`
- Cutover report：[docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md](superpowers/cutover_report_2026-05-08_deepseek-migration.md)（commit `a5f1cd1 docs(deepseek-migration): add cutover report`）
- 验证：backend fast `834 passed, 1 skipped, 3 deselected, 13 warnings, 22 subtests passed`；backend including slow `837 passed, 1 skipped, 13 warnings, 22 subtests passed`；frontend node tests `183 passed`；Windows build `build.bat` 成功并重建 `dist\咨询报告助手\`；legacy grep gates 7 类 clean
- 注意：原 Task 38 packaged UI/chat manual E2E 已在 2026-05-13 复测；当时 GUI 阻断已在 2026-05-19 关闭

0c. **DeepSeek Migration Commit 0 + spec + plan 全 APPROVED（2026-05-07~08）**
- 状态：`Commit 0 已 ship 3 commits + spec 3 轮 codex review APPROVED + plan 4 轮 codex review APPROVED`
- Spec：`docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md`
- Plan：`docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md`（HEAD `d7afadb`）
- Commit 0 已 ship：
  - 服务器 managed proxy: `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro` + 容器重建（2026-05-07）
  - `06779b1` rename default managed model gemini-3-flash → deepseek-v4-pro（含 AGENTS.md / CLAUDE.md / docs/managed-proxy-deployment.md / SettingsModal.jsx / managed_proxy/app.py 5 处）
  - `0b8b968` heal stale managed_model on startup + tier_1m_eff_256k tier mapping + connectionMode fallback + 7 个 HealStaleManagedModelTests + 1 个 context_policy test + APPROVED design spec + UI 设计稿
  - `8b3ad16` catch the last two gemini-3-flash refs in README + proxy contract
- 默认模型名同步覆盖了原 worklist item #3"默认渠道文案与默认模型决策"，整体合入 DeepSeek Migration
- 2026-05-08 E2E 实测：DeepSeek V4 Pro 9 次工具调用 schema 100% 正确，模型行为本身没问题；6 个产品/工程问题（`<think>` 标签泄露 / S0 门槛被动 / per-turn search 配额过严 / packaged stderr 吞没 / version_info 空 / 老用户 config heal）作为本轮 plan 的 in-scope items 一次性处理
- 后续实施已完成，见 0d 与 cutover report；packaged UI/chat 手工验收在 2026-05-13 与 2026-05-19 分阶段完成

0b. **S4 mutation-limit 二次写入真实字数提示（2026-05-07）**
- 状态：`已修复并打包验证`
- 问题：模型同一轮第二次调用 `append_report_draft` 被“一轮一改”guard 拦截后，旧错误只说“本轮已经修改过”，没有带真实字数；后续 assistant free text 可能自行估算并误称已达标。
- 修复：4 个 canonical draft 工具在 mutation-limit error 上统一回传 `report_progress`，错误文案包含“当前真实字数：x/y”和是否仍需补全，明确要求模型下一轮继续，不得声称达标。
- 验证：4 个 targeted regression passed；semantic draft tools + obligation guard 扩展回归 `82 passed, 1 warning`；`build.bat` 重打包成功；新 exe 通过 mock OpenAI `/api/chat/stream` smoke（`read_file → append 成功 → 第二次 append 被 guard 拦截`，stream 与完整 tool payload 均含真实进度）。

0a. **流式输出体感 + open issues 关闭（2026-05-06）**
- 状态：`已关闭`
- 流式体感：前端 flush 修复 + 读流超时友好报错已生效，用户确认当前体感正常
- Streaming retry timing / detector regex 扩展：打包态 5-session smoke 未触发 false positive，旧 app 残留进程不影响功能，整体关闭
- Push to origin/main：已完成

0. **Tools redesign 实施完成并通过打包态 6.3 smoke（2026-05-06）**
- 状态：`Tasks 1-6.2 全部 codex spec+quality 双轮 review APPROVED；2026-05-06 补 obligation tool-family guard；根目录 dist 重建；打包态 Task 6.3 五轮 smoke 全绿；本地 main 已 push origin`
- 实施 commits（17 commits this implementation phase，全在 local branch `claude/phase2-draft-action-tag`）：
  - **Task 1**（4 commits）：`9d183df` `b80413c` `9e54d88` `9cd071d` — `backend/report_writing.py` + 41 helper tests
  - **Task 2**（4 commits）：`292bf6f` `68eb8a2` `2717760` `43b6c68` — turn_context fields + obligation detector + read_file mtime hook
  - **Task 3**（7 commits 含 fix1）：`0c0f387` `c75ff0d` `0404f67` `1644620` `400e433` `dd5a322` + fix1 `5d88e2b` — 4 tools impl + 51 ToolTests + retry hook + Critical fix (legacy gate accepts semantic edit tools)
  - **Task 4**（2 commits）：`fa3088c` `3f28957` — SKILL.md §S4 重写 + chat.py user_action wording
  - **Task 5**（5 commits 含 fix1）：`911a9d2` `8bd0abc` `bac9112` `4ab5010` + fix1 `c53b5f3` — the big delete (-6594 lines) + legacy tag regression + wire append dispatch (Task 3 deferral 关闭) + canonical_draft_mutation merge fix
  - **Task 6.1+6.2**（1 commit）：`d482235` — dist rebuild 86.09MB / 3.16 min + tool-selection benchmark schema sanity
- Net diff：17 files changed, **+4844 / -6535 = -1691 lines net**
- Test acceptance：
  - `pytest tests/test_chat_runtime.py`: **360 passed, 1 skipped, 0 failed** in 1481s（之前 36 pre-existing fails 全部在 Task 5 删除的 deprecated test classes 里，自然消失）
  - `tests/test_report_writing.py`: 41/41
  - `tests/test_tool_selection_benchmark.py`: 4/4
  - 2026-05-06 post-smoke focused regression：`96 passed, 1 warning`（canonical draft obligation + 4 semantic tools + report_writing + benchmark）
  - frontend `node --test tests/`: 168/168 unchanged
- Review iterations: Task 3 + Task 5 各走 2 轮 quality review (r1 With fixes → fix1 → r2 Yes)；其他 task 一轮 APPROVED_WITH_NOTES
- Build：根目录 `dist/咨询报告助手/` 重建成功（`咨询报告助手.exe` 14,069,486 bytes，2026-05-06 22:00）
- Packaged smoke 6.3（evidence: `reality_test/smoke_backups/6-3-packaged-20260506-221248/summary.json`）：
  - A "开始写报告吧" → `append_report_draft` ✅ draft appended
  - B "把第二章重写一下" → `rewrite_report_section` ✅ only 第二章 changed
  - C "把'团队防御蓝领'改成'团队防御核心'" → `replace_report_text` ✅ unique phrase replaced
  - D "继续写第三章" → wrong semantic tools blocked, then `append_report_draft` ✅
  - E "整篇重写，按 outline 用更精炼的语言重写正文" → generic `write_file` / `edit_file` blocked, then `rewrite_report_draft` ✅
- 详见 [cutover report](superpowers/cutover_report_2026-05-06_tools-redesign.md)

0a. **Tools redesign spec + plan review 通过（2026-05-05 深夜，已 superseded by 0 entry above）**
- 状态：`spec + plan 全套通过 codex 双轮 review`（HEAD `1030d7b` plan v2）
- spec stage：4 commits 4 轮 review（d5bb758 → 5cb5f6b → a936bfb → 2c355c8 → 7f0d207），最终 APPROVED_WITH_NOTES
- plan stage：2 commits 2 轮 review（1226a67 → 1030d7b），最终 APPROVED
- 本会话整体输出：spec 788 行 + plan 2203 行 + 5 个 reviewer prompts

1. **Phase 2a fix4 完整集合 — section/replace keyword fallback 实施 + 双轮 review APPROVED + cutover smoke 验证（2026-05-05 17:00-19:00，已被 redesign 取代）**
- 状态：`已合 main + 已 push origin`（main HEAD `07a8269`）
- 16 commits total this Phase 2a 集合（fix4 三轮叠加在 13 commits 之上）：
  - `ec0b327 feat(rollout): section/replace keyword fallback (spec §4.12 v5 fix4)` — Path A 实施：spec §4.12 amendment + chat.py preflight Step 1.5 + gate edit_file fallback + SKILL.md §S4 fallback note + 11 tests
  - `70ec0ba fix(rollout): address fix4 round 1 rejections (Bugs 1-5 + test tightening)` — fix1：(1) `改为` 关键词补齐, (2) `_SECTION_PREFIX_RE` negative-lookahead 防 `第二章节` overmatch, (3) `_preflight_resolve_section_target` 多 prefix dedup, (4) zero_candidates / multi_candidate test 拆分, (5) 防御 test 取值集合放宽至 5 元集
  - `07a8269 fix(rollout): close fix4 round 2 safety holes (Bugs 7-8 partial multi-prefix + snapshot inject)` — fix2：(7) 任意 prefix unresolved → fail-fast None, (8) `_required_write_paths_for_turn` + `_build_required_write_snapshots` 优先读 cached `turn_context["canonical_draft_decision"]`，inject 同时 promote `mode="no_write"→"require"` 让 snapshot/scope 路径生效
- 双轮 review (spec + quality codex reviewer)：r1 REJECTED Bugs 1-5 → fix1; r2 REJECTED Bugs 7-8 → fix2; **r3 BOTH APPROVED**
- 测试：41/41 PreflightCheck + Gate pytest pass; wider sanity 87/0
- Cutover smoke 4 sessions plan reduced to 2 actionable runs (A begin + B section)：
  - Session A "开始写报告吧" ✅ begin fallback fired（regression 保护，fix3 同款行为）, draft 2549→3677 字
  - Session B "把第二章重写一下" ✅ section fallback fired 14 次 + scope enforcement active（vs fix3 19 次 gate-block dead-loop）— **fix4 设计层面工作正确**；模型未能缩窄 new_string 是独立 model-behavior 问题（见 0a）
  - Session C "把'X'改成'Y'" — 模型在 reasoning 阶段 hung 8 min 没 emit tool_call，无 events 数据；inconclusive
  - Session D continue — skipped（A 同类 regression 已覆盖）
- 详见 [cutover report](superpowers/cutover_report_2026-05-05_fix4.md) + [handoff doc (final)](superpowers/handoffs/2026-05-05-phase2a-fully-done-phase3-ready.md)

1. 二轮重打包已完成，主链路已跑完
- 状态：`已走二轮 smoke（暴露新 3 bug，见 1b；后续已全部修复）`
- 二轮重点验回顾：
  - Bug A/B/D/F 修复在新包里都生效（data-log.md 已按 `### [DL-YYYY-NN]` 格式写；非 plan 写入阶段门禁生效）
  - 聊天气泡 + 文件预览原生框选复制可用
- 二轮新暴露问题见 1b（已修）

1a. **[BUG 串] stage-advance-gates 实机链条性失效 — A/B/C/D/F 已修，G/H 移入当前待办**
- 状态：`A/B/C/D/F 已修；G/H 已移入上方 stage-advance-gates Bug G/H 待复核项`（2026-04-21 3 路并行 codex + general-purpose 派活，全部合 main；C 后续被 S0 interview 实施覆盖，详见 1d）
- 关联 plan：`docs/superpowers/plans/2026-04-21-smoke-test-bugfix.md`
- 测试基线：403 passed / 1 skipped（基线 397 → 403，加 6 条新测试）

**Bug A ✅** — `backend/chat.py` `_should_allow_non_plan_write` 已叠加阶段校验，仅在推断阶段 ≥ S4 时放行非 plan 写入。commit `cb15e4c fix(chat): gate non-plan writes by stage`。

**Bug B ✅** — `backend/skill.py:record_stage_checkpoint` 在 `set` 前校验对应 plan 文件有效存在（outline/report_draft/review_checklist/presentation_plan/delivery_log），缺文件 raise ValueError。commit `7e262cf fix(skill): validate stage checkpoint prerequisites`。

**Bug C ✅** — 已先由 S0 interview + legacy stage signal 实施覆盖（spec/plan APPROVED 后 19 个 task 全套合 main），后由 2026-05-19 stage conductor v0 收敛为 `advance_stage`。`stage_zero_complete` 不再依赖 `project_overview_ready`；当前必须由 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")` 成功落 checkpoint 才推进。详见 1d 与 0f。

**Bug D ✅** — `skill/SKILL.md` §S2 明确 `### [DL-YYYY-NN]` 格式 + 完整示例，并写明"表格形式不会被识别"；首次写 `plan/data-log.md` 时通过 `_emit_system_notice_once` 注入格式提示。commits `7a50bb3` / `88f10d7` / `4a6a7da`。

**Bug E ✅** — Bug A+D 修好后自消，不再独立追踪。

**Bug F ✅** — `backend/chat.py:_expected_plan_writes_for_message` 白名单从硬编码 5 条路径改成正则匹配 `report_draft_v\d+\.md` 和 `(content|output)/*.md`，`_is_expected_report_write_path` 方法抽出可复用。+28 行测试。commit `1e180cc fix(chat): detect versioned report draft claims`。

**Bug G ↗** — 回退 checkpoint 后 `content/*.md` 仍存在，状态不自洽；已移入当前 stage-advance-gates Bug G/H 待复核项。

**Bug H ↗** — S1 回退后 UI「下一步建议」显示"暂无"，`next_stage_hint` S1 分支缺；已移入当前 stage-advance-gates Bug G/H 待复核项。

~~**Bug I**~~ — 已排除，黄色警告是当轮新触发。

**派活记录**（作为项目默认工作法参考）：
- 3 路并行：task-4（codex exec, Bug A+B+F）+ task-5（codex exec, Bug D）+ frontend-copy（general-purpose + sonnet, worklist #8）
- 两个 codex 共享 main working tree，Bug F 先手被 task-4 commit，task-5 跑完看到存在不覆盖，零冲突
- 监控从 30 min cron → 5 min cron（监控到 task-5 越界迹象）→ 20 min cron（兜底挂掉），bash 完成靠系统 notification，无需频繁自查

1b. **[二轮 smoke] 新发现三处问题 — 全部已修**
- 状态：`三处全修，已合 main`（2026-04-21 二次 smoke 发现，2026-04-21~04-24 修复）
- 测试项目：`D:\MyProject\CodeProject\JustTest\.consulting-report\`

**新 Bug 1 ✅（S0 门槛回归，关联旧 1a#Bug C）** — 图5
- 原现象：填完新建项目表单 → 右侧「已完成」直接四项全勾，对话一句没说
- 修法：S0 interview 全套 19 个 task 实施完毕（spec/plan APPROVED 后），`stage_zero_complete` 改成必须落 `s0_interview_done_at` checkpoint 才推进。2026-05-19 后当前落点是 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")`；legacy tag 只做 sanitizer。`backend/skill.py` 不再用 `stage_zero_complete = project_overview_ready` 短路。详见 1d 与 0f。
- 关键 commits：`3817c43` / `aca1350` / `916f135` / `0ab565c` / `8f63570`（当时更新 S0 访谈规则，当前已被 `advance_stage` 口径取代）

**新 Bug 2 ✅（tool 结果气泡吞 assistant 正文）** — 图6
- 现象：`✅ 结果: {...}` 气泡把紧跟的 assistant 正文首段一起吞入同一个气泡
- 根因：`frontend/src/components/ChatPanel.jsx:509` 流式拼接 tool 事件时只在前面加 `\n`、尾部不加；后续 `content` 块直接 append 同一行；`utils/chatPresentation.js:64` `splitAssistantMessageBlocks` 按行识别整行以 `✅ 结果:` 开头为 tool block → 把吞进去的正文也算 tool
- 修法：抽 `appendToolEventContent(prev, toolText)` 纯函数（chatPresentation.js），自动补尾 `\n`；ChatPanel.jsx 调用
- commit：`73b345d fix(chat): preserve text after tool events`；前端测试 139→140 passed，`npm run build` 零错
- 附带：codex 多加了 `frontend/tests/index.js`（为让 `node --test tests/` 做显式目录入口，可保留）

**新 Bug 3 ✅（口头"确认"不推进阶段）** — 图8
- 原现象：用户回"确认"（响应模型"请回复'确认大纲'或'按此大纲执行'"），`stage_checkpoints.json` 未写入 `outline_confirmed_at`
- 修法：当时选了决策点 (b) 中期重构，删除 `_WEAK_ADVANCE_BY_STAGE` 弱关键词表，并短期引入 legacy stage tag 解析来替代口头强关键词 fallback。2026-05-19 stage conductor v0 已进一步收敛：模型必须调用 `advance_stage`，legacy tag 只剥离、不落 checkpoint。详见 1d 与 0f。

1c. **[归档] Gemini-era 模型行为硬伤 — 主体修复已合 main，复测路径已被 DeepSeek Migration 取代**
- 状态：`核心兜底全部落地；Gemini reality_test 复测路径已停止推进`
- 测试项目：`D:\MyProject\CodeProject\consulting-report-agent\reality_test\.consulting-report\`（替代旧的 `D:\CodexProject\test\`）
- 模型约束：`gemini-3-flash`（免费批量渠道限制，无法更换）
- 归档说明：不再按 Gemini reality_test 复测路径推进；当前验收以 DeepSeek packaged UI/chat E2E 为准。

**2026-04-24 已落地（α/β/γ/δ 全套）**：
- `content/report_draft_v1.md` 成为正文草稿唯一规范路径；首次成稿/续写走 `append_report_draft`，修改已有正文走 `read_file + edit_file`，禁止用 `write_file` 直接覆盖正文草稿（**δ + 问题 3 修法**）
- 所有已有文件通用要求同一轮先 `read_file`，再 `write_file` / `edit_file`，降低模型拿旧上下文覆盖新文件的概率
- 正文写入工具回传真实落盘字数进度，`append_report_draft` 事件保留真实 tool name，`draft_followup_state` 改成结构化状态，不再从 assistant 文案反推（**β + 问题 1 修法**）
- 混合意图（如"写够 5000 字再导出/质量检查/看文件/看字数"）改为本轮只完成正文写入并给下一步提示，后续动作下一轮单独处理
- 章节改写新增范围校验：`edit_file.new_string` 不能把整篇草稿或多个同级章节塞进单章节替换里
- **反思循环兜底**（**γ 修法，commit `6883bfa fix: require real report draft writes`**）：流式层加 `SELF_CORRECTION_LOOP_MARKERS = ("（修正", "(修正", "（纠正", "(纠正", "停止自言自语")` 累积检测，命中 ≥3 次实时 break；完整 candidate_message 也再检一次；命中后 `MAX_SELF_CORRECTION_RETRIES=1` 给一次重试机会，feedback 让模型停止反思继续真实动作。代码位置 `backend/chat.py:171/1543/3202/3346`

**2026-05-04 reality_test 进展**：
- reality_test 项目走完 S0 interview 后，第一轮收尾撞 `max_iterations=10` 上限，模型刚 fetch_url 第 1 个百科就被截断，references.md 还是空模板
- 系统化调查：单轮内做了 6 次成功 tool 调用 + 1 次失败 write（fetch_url 前置门禁挡的），assistant 输出**零** SELF_CORRECTION_LOOP_MARKERS 命中——撞顶不是病理性循环，是真实工作密度
- 根因：当前架构（先读后写 + fetch_url 前置 + Gemini 3 Flash 串行 tool call）下，单轮"完成 S0 收尾 + 补全 plan + 抓 1-2 条引用"实际需要 11-13 轮，10 不够
- 修复：`max_iterations` 默认值 10 → 20（commit `ec976b8 fix(chat): raise stream max_iterations from 10 to 20`），`_chat_stream_unlocked` + `chat_stream` 两处。非流式 `chat()` 仍 5（仅测试用）。test_chat_runtime 342 passed / 1 skipped 零回归
- 当时重打包已完成（2026-05-04，dist 104 MB / exe 14 MB）；后续不再以 Gemini reality_test 作为当前验收路径。

1d. **[已完成 / 已被 0f supersede] S0 interview + legacy stage signal 19 个 task 全套实施**
- 状态：`全部合 main；阶段推进运行时已在 2026-05-19 被 stage conductor v0 收敛到 advance_stage`
- 关联文档：`docs/superpowers/specs/2026-04-21-s0-interview-and-stage-ack-design.md` / `docs/superpowers/plans/2026-04-21-s0-interview-and-stage-ack-impl.md` / `docs/superpowers/handoffs/2026-04-21-s0-impl-handoff.md`
- 覆盖范围：
  - **S0 硬门禁**（解 1a Bug C / 1b Bug 1）：`stage_zero_complete` 不再依赖 `project_overview_ready`，必须 `s0_interview_done_at` checkpoint 才推进。`backend/skill.py` 新增 `s0_interview_done_at` infra（commit `3817c43`）+ gating（`aca1350`）；`backend/chat.py` 加 S0 软门禁阻挡 LLM 在访谈未完成时直接写 outline / report-draft（commits `0ab565c` / `216f5f1` / `167e10f`）。当前推进方式是 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")`。
  - **legacy stage signal**（解 1b Bug 3 的历史实现）：删除整张 `_WEAK_ADVANCE_BY_STAGE` 弱关键词表（`916f135`），短期引入 assistant 尾部控制 tag 解析、流式 tail guard、历史消息 sanitize 和兜底 strip。2026-05-19 后这些 tag 只作为历史残留清理对象，不再触发 checkpoint side effect。
  - **路由 + 配套**：新增 `POST /api/projects/{id}/checkpoints/s0-interview-done`（`504801f`，`action=set` 直接 400）；`workspaceSummary` 暴露 `s0InterviewDone` flag（`31dc7cf`）；`SKILL.md` 当前写明 S0 强制访谈与 `advance_stage` 规则；S2+ 增加"重置 S0"高级回退选项（`2332822`）
  - **migration**：增量 schema 迁移（`cf26609`），legacy 项目不会被新判据推回 S0
- 测试基线：spec 5 轮 / plan 3 轮 codex review；实施期 19 个 task 各 commit 跑 review
- 结论：1a Bug C ✅ / 1b Bug 1 ✅ / 1b Bug 3 ✅ 全部由本块覆盖，无需独立追踪

8. ~~聊天与文件预览复制体验~~ — ✅ 已修，commit `341de44`。根因：PyWebView 的 WebView2 在 Win 下对非输入元素默认禁选；通过 `.selectable-content` 工具类（`-webkit-user-select: text` + `*` 子选择器）在 ChatPanel 气泡 + FilePreviewPanel 预览区放开。右上角复制按钮保留。已进"已解决记录"。

## 历史已解决

0. ⭐ **context-signal-and-intent-tag Phase 2a 实施完成（2026-05-05，13 commits 已合 main）**
- 状态：`Phase 2a 13/13 task done + 5 fix（reviewer catch 真问题）；后续 fix4 / cutover / 删除旧链路已被 Tools redesign 覆盖`
- 关联文档：
  - spec [2026-05-04-context-signal-and-intent-tag-design.md](superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md)（5 轮 APPROVED）
  - plan [2026-05-04-context-signal-and-intent-tag.md](superpowers/plans/2026-05-04-context-signal-and-intent-tag.md)（6 轮 APPROVED）
  - handoff [2026-05-05-phase2-section-replace-pending.md](superpowers/handoffs/2026-05-05-phase2-section-replace-pending.md)（下次 session cold-start brief）
  - cutover artifact [cutover_report_2026-05-05_fix3.md](superpowers/cutover_report_2026-05-05_fix3.md)
- Phase 2a 实施 task：
  - Task 15-22：13 commits（parser module / tail-guard / preflight 并行 / validate-apply / gate / compare event / report 脚本 / SKILL §S4）
  - Task 19 fix1/2/3 + Task 18 fix1 + Task 20 fix1：5 个 fix 都修了 reviewer catch 的真问题
- 测试基线：GateCanonicalDraftToolCallTests 17/17 + 70 wider sanity 0 failed
- 关键 commits：`8940d70` parser → `234c0fb` tail-guard → `dda3aef` preflight → `1a15b12+6e956fb` validate → `dc2a321+d603042` gate → `cf445e2+ab91fda` compare event → `5a6a5b8` script → `f6ed0e9` SKILL → `a89b081` fix2 → `6112a75` fix3
- Cutover smoke 实测：begin/continue Bug A 修复（fallback work），section/replace 暴露架构缺口（见 0a）
- **归档说明**：fix4、cutover 重测、旧链路删除均已在 2026-05-06 Tools redesign 中完成或取代；本块不再发起后续任务。

1. ⭐ **context-signal-and-intent-tag Phase 1 实施完成（2026-05-04，16 commits 在 `claude/happy-jackson-938bd1`）**
- 状态：`Phase 1 13/13 task done；后续验证和 Phase 2/3 路线已被 Tools redesign / DeepSeek Migration 覆盖`
- 关联文档：
  - spec `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md`（5 轮 review APPROVED）
  - plan `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md`（6 轮 review APPROVED）
  - handoff `docs/superpowers/handoffs/2026-05-04-phase1-impl-handoff.md`（cold-start 下个 session 用）
- 5 reality_test bug 状态：
  - **Bug A**（门禁误判）↗ 后由 Phase 2 `<draft-action>` tag 路线处理，最终被 Tools redesign 取代
  - **Bug B**（黄框污染）✅ A1 修：`SystemNotice.surface_to_user` 必填 + `_emit_system_notice_once` 双 dedupe + 服务端过滤
  - **Bug C**（阈值黑盒）✅ A2 修：`_render_progress_markdown` 渲染 `**质量进度**: 5/7 条 有效来源` + tool_result 追加 `quality_hint`
  - **Bug D**（兜底黑洞）✅ A3 修：`_finalize_empty_assistant_turn` helper（永不持久化空 assistant）+ `_coalesce_consecutive_user_messages` + 三层 sanitize（provider build / GET /conversation / 前端）
  - **Bug E**（工具历史零记忆）✅ C1 修：`<!-- tool-log -->` HTML 注释嵌入 assistant content（模型看，前端 strip）
- 编排器：`_finalize_assistant_turn` 重构成 7 步顺序（Task 13），3 个 caller（stream / non-stream / early-finalize）统一调
- 测试基线：pytest 713 passed / 1 skipped / 0 failed（21 min）；frontend 168 passed；dist/咨询报告助手/ 91 MB
- 派活节奏（实施统计参考）：
  - 13 task × ~30-45 min/task ≈ 6-7 小时（含 spec/quality 两阶段 review）
  - 全程 codex exec gpt-5.4 xhigh + PowerShell tool inline env 注入 + 20 min 静默 cron
  - Task 13 编排器整合是最贵的——3 commit（实施 + return value fix1 + 14 旧测试断言修 fix2）
  - chat_runtime suite 11k 行是 pytest 全套主时间瓶颈，reviewer prompt 必须 narrow scope
- **归档说明**：reality_test、Phase 2/3、cutover compare、重打包与文档同步路线已被后续 Tools redesign / DeepSeek Migration 完成或取代；本块只保留历史背景。

1. ⭐ **400 死循环根因清理 + edit_file 工具 + debug dump 转正（2026-04-22）**
- 状态：`已完成`（claude 侧自改自测，未派 codex；测试 509 passed / 1 skipped / 0 failed）
- 根因：`newapi → Gemini` OpenAI 流式兼容层偶发把并行 `functionCall` 的 chunk `index` 合并到 0，导致我方累积层把多个 tool_call 的 `name` 和 `arguments` 首尾拼接成 `"write_filewrite_file"` + `"{...}{...}"`，上游拒收 `400 INVALID_ARGUMENT`
- 代码改动全部在 `backend/chat.py`：
  - **Fix A**（畸形 tool_calls 拦截）：`if collected_message["tool_calls"]:` 分支开头校验每个 tool_call 的 `name in known_tool_names` 且 `arguments` 是合法 JSON；任一畸形 → 本轮作废，append `assistant 占位 + user 反馈` 对子做合规隔板（**单独 append user 反馈会造成连续两条 user → Gemini 角色交替校验 400，踩过一次**），`iterations += 1; continue`
  - **Fix B**（当轮空 content 兜底）：流式和非流式两条 `_finalize_assistant_turn` 之后都加 `if not assistant_message.strip(): assistant_message = "（本轮无回复）"`，避免空 parts 的 assistant 进历史
  - **Fix C**（历史回放兜底）：`_to_provider_message` 对 `role=assistant` 且 `content=""` 的老残迹同样兜底，不依赖干净历史
  - **Fix D**（system prompt 约束）：加 `concurrency_rule`「每轮只发一个 tool_call」—— 实测 Gemini 3 Flash 基本无视，但 Fix A 能兜底合并畸形
- 新工具 `edit_file(file_path, old_string, new_string)`：精确字符串替换，要求 `old_string` 唯一存在；`write_file` 和 `edit_file` 共用抽出来的 `_execute_plan_write(project_id, *, file_path, content, persist_func_name, persist_args)` 方法跑完整 gate 链（S0 block / non-plan-write / fetch-url gate / path normalize / signature / data-log-hint / persist）。`skill/SKILL.md` 新增「文件工具选择」章节，明确 data-log.md / analysis-notes.md 追加条目一律 `edit_file`，`write_file` 只用于新建或整体重写
- 配置：`managed_search_pool.json` `per_turn_searches: 2 → 4`（仍受 `project_minute_limit: 10` / `global_minute_limit: 20` 保护）
- debug dump 转正：`_debug_dump_request` 方法从临时调试代码改成持久辅助工具。路径从 `D:/consulting-debug/` 挪到 `~/.consulting-report/debug/`（跨平台 + 和其他用户数据同目录），每次请求写 `payload-latest.json`（覆盖），失败时另存 `error-{UTC}-{label}.json`（保留）。`label` ∈ `{stream, stream-iter, nostream}`，`note` 字段带 `iteration=N`
- 关键证据：`~/.consulting-report/debug/error-20260422T132039Z-stream.json`（最初定位到 `write_filewrite_file` 畸形 payload）、`error-20260422T135150Z-stream.json`（Fix A 早期实现引入的"连续两条 user"回归证据）
- 后续模型行为问题曾转入 Gemini-era 修复链路；该路径现已归档，当前验收以 DeepSeek packaged UI/chat E2E 为准。

1. ⭐ **stage-advance-gates smoke-test bugfix（Bug A/B/D/F + 前端复制）**
- 状态：`已完成`（2026-04-21 3 路并行派活，全部合 main）
- 5 个 commit：`cb15e4c` / `7e262cf` / `1e180cc`（task-4 Bug A/B/F）+ `4a6a7da` / `88f10d7` / `7a50bb3`（task-5 Bug D）+ `341de44`（frontend-copy 复制体验）
- 测试：后端 403 passed（397→403，+6 新测试）；前端 139 passed；`npm run build` 零错
- 详情见已解决 1a；G/H 已移入当前 stage-advance-gates Bug G/H 待复核项。
- 归档说明：二轮 smoke 与重打包后续已完成；新暴露问题已归入 1b / 1d / 当前 stage-advance-gates Bug G/H 待复核项，不再从本历史块发起 smoke。

1. ⭐ **阶段推进门禁重构（stage-advance-gates，Task 1-8 全闭环）**
- 状态：`已完成`（2026-04-21 分支 `feat/stage-advance-gates` 合 main）
- 关联文档：`docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`、`docs/superpowers/plans/2026-04-17-stage-advance-gates.md`
- 覆盖：
  - Task 1/2 — stage_checkpoints.json storage + length target + quality gate helpers（含 regex 加固）
  - Task 3a/3b/3c — 重写 `_infer_stage_state`（三条件投影）+ migration cascade + `get_workspace_summary` 扩 `checkpoints` / `length_targets` / `quality_progress` / `flags` / `next_stage_hint` / `stalled_since` / `word_count` / `delivery_mode` / `length_fallback_used`
  - Task 4 — `POST /api/projects/{id}/checkpoints/{name}` endpoint + legacy keyword checkpoint detector（strong / weak S4 排除 / rollback / negation 抑制 / `非常同意` 不误伤 / tie-break；2026-05-19 已由 `advance_stage` 取代）+ `_should_allow_non_plan_write` blocking-first 优先级 + 两轮 follow-up（`checkpoint_event` 字段 / OK/ok 大小写 spec 同步 / `SkillEngine.record_stage_checkpoint` 解耦 `backend.main` / 4 张 checkpoint 表 invariant test）
  - Task 5 — `write_file` 自签名拦截 + `system_notice` 三段链路（`_emit_system_notice_once` + stream pop drain + `ChatResponse.system_notices`）
  - Task 6 — `skill/SKILL.md` 阶段推进与工具错误规则
  - Task 7 — 前端 `StageAdvanceControl` + `RollbackMenu` + `ConfirmDialog` + `WorkspacePanel` chip + `ChatPanel` `system_notice` 渲染 + `workspaceSummary` 契约映射 + 7 fix round（`flags.outline_ready` 字段名 / length_fallback chip 非交互 / `delivery_mode` 中文字面量 / "调整大纲"触发 prompt / `next_stage_hint` 消费守护 / checkpoint 错误反馈 + `pending` 态 / ConfirmDialog a11y / 隐藏后台阶段码 / `length_targets.report_word_floor` 契约对齐）
  - Task 8 — 新包 91 MB（dist/咨询报告助手/）
  - Final cross-task review — APPROVED（见 `.codex-run/final-rereview-last.txt`）
- 测试基线（合并前）：后端 397 passed / 1 skipped / 0 failed；前端 139 pass / 0 fail；`npm run build` 零错。
- 派发规则（已成为项目默认）：
  - 实施任务（`--write`）→ 裸 `codex exec`（插件不稳定）；前端 `general-purpose` agent 配 `model: sonnet`
  - Review（read-only）→ 裸 `codex exec`（GPT-5.4 xhigh）
  - 裸 exec 模板：`codex exec --cd "..." --color never --output-last-message .codex-run/X-last.txt < .codex-run/X-prompt.md > .codex-run/X-full.log 2>&1`，bash 传 `run_in_background: true`
  - 30 min cron (`7,37 * * * *`) 做活性自查，完成后自动 `CronDelete`

3. 内置搜索池主链路
- 状态：`已完成`
- 结论：`managed_search_pool.json` 打包注入、运行时状态/缓存、四家 provider 适配器、分层路由、native fallback、chat runtime 接线都已落地。

4. 1.29 GB 异常大包
- 状态：`已完成`
- 根因：之前在 Anaconda 大环境里打包，PyInstaller 把大量无关科学计算/Notebook 依赖一起卷进包。
- 结论：已切到项目 `.venv` 打包，最新包体积约 `91 MB`（含 Task 4/7 新增代码）。

5. 打包脚本不稳
- 状态：`已完成`
- 结论：`build.bat` 已改为薄入口，实际逻辑迁到 `build.ps1`；默认走项目 `.venv`，不再依赖脏全局环境。

6. 前端依赖漏洞
- 状态：`已完成`
- 结论：已升级前端依赖，当前 `npm audit` 为 `0 vulnerabilities`。

7. 阶段事实源与工作流对齐
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-01-stage-facts-and-phase-alignment-design.md`
- 结论：`project-info.md` 已退出正式工作流；阶段推断、正式 plan 文件和门禁规则已对齐。

8. Session memory 重构
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-14-session-memory-rearchitecture-design.md`
- 结论：`conversation_state.json`、memory entries、post-turn compaction 和 provider 上下文顺序已完成重构。

## 已取代 / 废弃

1. Web Search 相关性加固（针对 SearXNG 单后端）
- 状态：`已被取代（Superseded）`
- 关联文档：`docs/superpowers/specs/2026-04-15-web-search-relevance-hardening-design.md`（顶部已加 Superseded banner）
- 取代原因：项目走了**管理型搜索池**路线（`managed-search-pool` 已完成，见"已解决记录"第 3 条），四家 provider + 分层路由，从根本上绕过了 SearXNG 召回质量问题。
- 不要再按这份 spec 落地。保留文档是因为它记录的 SearXNG 实测问题可作为未来搜索策略调整的参考。

## 使用约定

- 只在本文件维护"仍需要行动"的事项。
- 已解决但值得保留上下文的内容，放到"已解决记录"。
- 历史调试记录归档到 `docs/debug-backlog.md`，不再作为当前事实源。
