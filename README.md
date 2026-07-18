# 咨询报告写作助手

面向非技术同事的咨询报告 Agent。当前同时提供多用户 Web 站点和 Windows 桌面客户端，
把需求确认、研究、分析、写作、独立审查和可审草稿导出串成一套对话式工作流。

## 当前形态

- Web 试用站：`https://consulting.z0y0h.work`（账号隔离、金额配额、管理后台）
- Windows 桌面分发：`dist\咨询报告助手\` 完整目录，不是裸 exe
- 默认模型通道：managed `deepseek-v4-pro`；高级用户可激活自定义 OpenAI 兼容 API
- macOS 可用于 Web 开发，不承诺正式桌面分发

## 现在已经能做什么

- 新建咨询项目，按 S0–S7 逐步完成需求确认、框架、研究、分析与写作
- 同一浏览器会话中切换项目时，后台生成继续运行；每个项目保留独立聊天状态
- 上传并转换文档、表格、PDF 和图片，查看阶段、材料、文件树与可编辑预览
- 让助手生成咨询图表插入正文（柱/折线/饼/瀑布等数据图 + 2×2 矩阵/流程/组织架构等结构图），预览所见即所得、随导出嵌入 docx
- 修改或覆盖正文前自动保留近期版本；需要撤回时可直接让助手列出并恢复草稿版本
- 触发独立审查并按结果继续修改
- 导出带封面、目录、页眉页码、中文字体、表格和图表的 docx `可审草稿`

当前没有承诺：

- macOS 正式支持
- 最终排版完成的一键终稿交付

## 模型连接

### 试用通道

- 面向普通同事，仅供快速试用
- 客户端不保存真实上游 key
- 服务端通过薄中转注入专用 key
- 发布包通过 `managed_client_token.txt` 注入专用客户端令牌

### 自定义 API

- 面向高级用户
- 支持手动填写 OpenAI 兼容 `Base URL`、`API Key`、`Model`
- 可以作为试用通道的兜底方案

## 本地开发

推荐 Python 3.12 + Node 20 LTS。

### macOS / Linux Web 开发

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python run_web.py
```

浏览器访问 `http://127.0.0.1:8888`。Web 启动的鉴权/安全环境变量见
[系统实现参考](docs/architecture.md) 的 W2-B/W2-C 章节。

### Windows 桌面开发

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

cd frontend
npm install
cd ..
python app.py
```

### 验证

```bash
.venv/bin/python -m pytest -q tests/
cd frontend && node --test tests/ && npm run build
```

Windows 下把 Python 路径换成 `.venv\Scripts\python`。

## Windows 打包

```bash
build.bat
```

打包机需要提前准备 `managed_client_token.txt`、`managed_search_pool.json` 和可随包分发的 Pandoc。
打包产物在 `dist/咨询报告助手/`，其中包含 `_internal/pandoc.exe`。

## 相关文档

- Agent 规则：[CLAUDE.md](CLAUDE.md)
- 详细架构与不变式：[docs/architecture.md](docs/architecture.md)
- 当前待办：[docs/current-worklist.md](docs/current-worklist.md)
- Windows 打包说明：[WINDOWS_BUILD.md](WINDOWS_BUILD.md)
- 通用打包说明：[BUILD.md](BUILD.md)
- 试用通道薄中转部署说明：[managed-proxy-deployment.md](docs/managed-proxy-deployment.md)

## 项目结构

```text
├── backend/         # FastAPI 后端
├── frontend/        # React 前端
├── skill/           # 打包内置的咨询报告 skill 运行时资产
├── managed_proxy/   # 试用通道薄中转（CRA → new-api 上游）
├── opencode_proxy/  # opencode 渠道 SSE 规范化 sidecar（new-api → opencode，修缓存计费）
├── tests/           # Python 回归测试
└── app.py           # 桌面应用入口
```

## 技术栈

- 后端：FastAPI + OpenAI SDK
- 前端：React + Tailwind CSS
- 桌面：PyWebView
- 打包：PyInstaller
