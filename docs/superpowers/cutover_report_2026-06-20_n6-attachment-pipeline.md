# N6 附件管线重做 — Cutover Report（2026-06-20）

**分支**：`feat/n6-attachment-pipeline`（从 `main` `b64f08d` 切；30 commits，27 文件，+3164/−103；未 push、未合并，等用户发话）
**Spec**：`docs/superpowers/specs/2026-06-20-n6-attachment-pipeline-design.md`
**Plan**：`docs/superpowers/plans/2026-06-20-n6-attachment-pipeline.md`
**实施方式**：subagent-driven（每 task 一个 Claude implementer agent + 每阶段末 Codex 红队 review，审→修→再审至 APPROVED）

## 交付了什么

把上传素材统一转成 markdown / 文本再喂模型，并顺手结清 #4（前端图片上传拦截）：

- **文档道**：docx/pptx/xlsx/pdf/csv/html 走 markitdown；老二进制 .doc/.ppt 走 LibreOffice headless 转现代格式再 markitdown，.xls 优先 markitdown（xlrd）失败回退 LibreOffice。内容 hash 缓存 + 失败 tombstone + 引用计数 GC（共享 hash 安全）。
- **图像道**：多模态主模型直喂 `image_url`；纯文本主模型走「视觉模型转写 → OCR 兜底 → 友好失败」，转写文本存消息独立字段 `attachment_transcripts`、**绝不混入 `content` 意图**，历史轮 cache-first（不发新视觉请求）。
- **薄网关**：废「强改写 model」，改「白名单透传 + SELECTABLE 子集」（new-api 按模型名路由），视觉模型可达但不进用户下拉；`/health` 暴露 allowed/selectable 供 ops preflight。
- **#4 结清**：`supportsImageAttachments` 恒 true，图片永远可上传（纯文本模型走转写）。
- **安全/限额/trust boundary**：transient 数量/解码字节/MIME 硬限额；持久上传流式 413 / 导入 400；附件派生文本一律框进 `ATTACHMENT_DATA` 数据块（哨兵消毒防越狱）；系统提示防注入规则；**压缩边界**——摘要前剥离/中和附件数据，恶意附件无法经 `[对话摘要]` 重生为裸指令。

## 阶段与 Codex review（全部 APPROVED）

| Phase | 内容 | Codex 红队结果 |
|---|---|---|
| A（A1-A6）| 转换服务 + 文档道 + 缓存/GC/size 守门 | 3 轮：首轮 4 BLOCKER → 修 soffice 隔离/xlrd/ZIP-guard tombstone → **TOCTOU 快照**（convert_document 先快照源文件再 hash+解析，关缓存投毒+size 绕过）→ APPROVED |
| B（B1-B3）| 薄网关透传 + vision Settings + capability resolver | 2 轮：selectable `[]` 泄露 vision 模型 + missing-model 兼容文档化 → APPROVED |
| C（C1-C5）| 图像道 + transcripts + 意图隔离 + retain/release | 3 轮（最硬）：sentinel 逃逸 / chat 路径漏 retain（改 live-hash key）/ data_url 崩溃 / 持久图 escape → APPROVED |
| D（D1-D2）| 结 #4 + 转换状态 UI + SSE | 3 轮：not_parsed chip / 历史 attachment_transcripts 渲染（spec §8）/ status 免重 hash / get_material 去递归 / 删文件→not_parsed → APPROVED |
| E（E1-E3）| 限额 + 防注入 + 收面 | 5 轮（最硬）：4 BLOCKER → 红队 4（fail-closed strip / data_url MIME / strict base64 / workspace size）→ strip 嵌套反序跨 part → dict-shape content → APPROVED |
| F1 | PyInstaller spec 打包依赖 | — |

## 依赖偏离 plan 的 pin（plan 已授权「核最新可用版本」）

| 包 | plan 写 | 实际用 | 原因 |
|---|---|---|---|
| markitdown | `0.0.1a3` | **`0.1.6`**（`[docx,pptx,xlsx,xls,pdf]`）| 0.0.1a3 的 `MarkItDown.__init__` 无 `enable_plugins` 参数，本实现全程 `MarkItDown(enable_plugins=False)` 会运行时 TypeError |
| onnxruntime | `1.19.2` | **`1.27.0`** | markitdown 0.1.6 升级链带入；numpy 2.4.6 下 rapidocr import 正常 |
| rapidocr-onnxruntime | `1.3.24` | `1.3.24` | 不变 |
| xlrd | （未列）| **`2.0.2`** | markitdown 的 `xls` extra 不拉 xlrd，.xls markitdown-first 需要它 |
| magika | （未列）| `0.6.3`（markitdown 传递）| markitdown 文件类型识别模型 |

`requirements.txt` 已记。mac 开发态用 `uv pip install --python .venv/bin/python ...`（venv 无 pip）。markitdown 0.1.6 对**损坏的 docx**不报错（直接把字节当文本返回），故 converter 加了 ZIP-magic（`PK\x03\x04`）文件头校验，损坏 ZIP 容器格式 → tombstone。

## 回归矩阵（mac 开发态，2026-06-20）

- **后端**：`.venv/bin/python -m pytest tests/` → **1190 passed, 13 skipped, 82 subtests passed, 4 failed**。
  - 4 个 failed 全是 **CLAUDE.md 已记录的 macOS `/var`→`/private/var` symlink 路径比对环境差异**（`test_skill_engine` 2 + `test_workspace_materials` 2：`test_create_project_*` / `test_primary_report_path_*` / `test_*_stores_workspace_metadata` / `test_import_material_copies_*`），**Windows 上通过**，非 N6 引入。
- **前端**：`cd frontend && node --test tests/` → **329 passed, 0 fail**。
- **构建**：`npm run build` → ✓（chunk >500KB 警告是既有债）。
- **打包 spec 门禁**：`test_packaging_spec.py` 9/9、`test_packaging_docs.py`+`test_build_support.py` 29/29。

**spec §12 测试矩阵覆盖**：文档转换/缓存/tombstone/GC（`test_material_conversion.py`、`test_skill_engine.py`）、薄网关透传/selectable/whitelist（`test_managed_proxy.py`）、vision/OCR/capability（`test_chat_runtime.py`）、transcripts/意图隔离/历史注入/DeepSeek 兼容（`test_chat_runtime.py`）、限额（`test_models.py`/`test_main_api.py`）、防注入+compaction 边界对抗（`test_chat_runtime.py`）、转换状态 API（`test_main_api.py`）、前端纯函数+source-guard（`frontend/tests/`）。零缺口。

**F3 全量回归亲自跑时逮到 1 个真回归**（早前 subagent 跑子集 + Codex code-level 审都漏了）：C5 按 plan 在 `[本轮附带材料]` 清单里跳过图片材料，破坏了 pre-existing 的 `test_chat_handler_builds_multimodal_user_message_for_attached_images`。已修：清单列出所有材料（含图片，更informative），且顺带把清单里的 `display_name`（用户可控文件名）也过哨兵消毒（堵上文档文件名同款注入洞）。

## 仍需手工接续（mac 做不了，留交接）

- **F2（Windows 打包 smoke + 删 legacy 解析器）**：`build.bat` 重打包 → 打包态启动 + 各格式（docx/pptx/xlsx/pdf/老 doc/图片转写降级）逐一验 + 体积量测；smoke 全过后删 `backend/skill.py` 里 feature-flag 期保留的 `_legacy_read_document`/`_read_docx`/`_read_xlsx`/`_read_pdf`，跑全量回归。**LibreOffice 不随包分发**——老 .doc/.ppt/.xls 在没装 LibreOffice 的目标机会友好失败（建议用户改存新版格式重传）。
- **F4（ops 薄网关上线，需用户在场）**：jp-app-01 上 `consulting-report-managed-proxy` 容器 env 加：
  - `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro,Qwen/Qwen3-VL-8B-Instruct`
  - `MANAGED_PROXY_SELECTABLE_MODELS=deepseek-v4-pro`
  - 重部署后 preflight：`curl https://<proxy>/health` 断言 `allowed_models` 含视觉模型、`selectable_models` 仅 `deepseek-v4-pro`；再经 App 冒烟一张图转写调通（new-api 渠道 60 已含 `Qwen/Qwen3-VL-8B-Instruct`）。**动线上前与用户确认。**

## 已知限制（v1，单用户桌面可接受）

- **workspace live 文件 TOCTOU/staleness**：workspace-select 材料是 live 引用（非拷贝）。文档道 `convert_document` 已快照（关投毒+size 绕过）；图像道 `transcribe_image` 未快照（同类 race，Codex 同意延后——单用户、图片不计 size、后果仅缓存错条）。retain/release 按当前文件内容算 key；文件在 retain 与删除之间被外部改写 → 旧 key 缓存 orphan（仅 ref 泄露非正确性）。
- **`remove_material` 在源文件已被外部删时漏 release** → 旧缓存 orphan（GC 永不归零，磁盘小泄露非正确性）。
- **历史重载的 transcripts 状态**：spec §8 的「已转写图片」指示已支持历史重放（`historyTranscriptIndicators`），但 transient 图的 `transientAttachments` 本身不回填（后端 `attachment_transcripts` 持久、LLM 历史注入不受影响，仅前端气泡 transient 指示限本轮）。
- **`tests/test_stream_api.py` 3 个 `@slow` 测试 baseline 就红**（fake_stream shim 不接 `system_trigger`/`trigger_metadata` kwargs + 缺 `id`），默认 run 已排除（`-m "not slow"`），非 N6 引入，留独立清理。
- **mac 4 个 symlink 测试**：见回归矩阵；需 mac 全绿要把临时路径断言改走 `os.path.realpath`/`.resolve()`（worklist N-section 独立小活）。

## 关键架构不变式（改 N6 代码前必读）

- `backend/material_conversion.py` 是 **DI 纯边界**，不 import chat（source-guard 测试锁死，连 docstring 都不能含 `import chat` 子串）。converter 不反向依赖 SkillEngine/project，只暴露纯函数 `cache_key_from_sha256` + 只读属性 `image_cache_extra` 供 SkillEngine 算 key。
- 缓存 key 公式三处必须一致：`_cache_key(path,extra)` == `_content_hash(path)+"-"+CONVERTER_VERSION+extra` == `cache_key_from_sha256(sha,extra)`。
- **trust boundary**：附件派生文本（图片转写 / read_material_file 文档正文）只经 `ATTACHMENT_DATA_OPEN/CLOSE` 数据块注入、且不可信片段（text/name/display_name/文档正文）先过 `_neutralize_attachment_data_markers`（破坏 `<<<`/`>>>` 定界符防越狱）；`content` 永远是 raw 用户意图；`_build_turn_context` 绝不收附件文本；`_summarize_messages` 摘要前剥离/中和附件数据（fail-closed：畸形框定从首个标记砍到 EOF；list 先 flatten；非 str/list shape 序列化后再 strip）。
- DeepSeek 官渠兼容：N6 只给 system prompt 追加文本 + 改 read_material_file 工具结果字符串 + 改摘要输入，**不碰** provider tool-call / `reasoning_content` / `tool_choice` 序列化。
- 限额常量集中在 `backend/material_limits.py`。
