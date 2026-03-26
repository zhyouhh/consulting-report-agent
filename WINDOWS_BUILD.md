# Windows 打包指南

## 支持范围

- 第一阶段只支持 Windows。
- macOS 兼容不在当前正式交付范围内。
- 当前定位是桌面客户端，不要求同事本地手动起前后端。

## 运行模式

### 默认通道

- 面向普通同事，开箱即用。
- 默认地址：`https://newapi.z0y0h.work/client/v1`
- 默认模型：`gemini-3-flash`
- 前提：服务端薄中转已经部署完成。

### 自定义 API

- 面向高级用户。
- 通过设置页面自行填写 OpenAI 兼容接口参数。
- 可用于兜底或个人私有模型接入。

## 环境准备

### 1. 安装 Python

- 推荐 Python 3.11 或 3.12
- 安装时勾选 `Add Python to PATH`

### 2. 安装 Node.js

- 推荐 Node.js 20.x LTS

### 3. 验证环境

```cmd
python --version
node --version
npm --version
```

## 打包步骤

### 方法一：一键打包

1. 双击运行 [build.bat](D:/Codex/CodexProjects/Consulting-report-agent/.worktrees/client-v2/build.bat)
2. 提前准备 `managed_client_token.txt`，或设置环境变量 `CONSULTING_REPORT_MANAGED_CLIENT_TOKEN`
3. 等待脚本自动安装依赖、构建前端、执行 PyInstaller
4. 打包产物位于 `dist\咨询报告助手\`

### 方法二：手动打包

```cmd
pip install -r requirements.txt
pip install pyinstaller

set CONSULTING_REPORT_MANAGED_CLIENT_TOKEN=你的专用客户端令牌

cd frontend
npm install
npm run build
cd ..

pyinstaller consulting_report.spec
```

## 打包产物

```text
dist/
└── 咨询报告助手/
    ├── 咨询报告助手.exe
    ├── skill/
    ├── frontend/
    └── _internal/
```

## 分发说明

- 必须分发整个 `dist\咨询报告助手\` 文件夹。
- 建议压缩为 zip 再发给同事。
- 同事解压后直接双击 `咨询报告助手.exe`。

## 首次使用体验

1. 程序启动后默认显示 `默认通道`
2. 如果托管代理在线，可直接新建项目并开始使用
3. 若默认通道不可用，可在设置中切到 `自定义 API`
4. 导出能力当前是 `可审草稿`，不是最终排版完成稿

## 建议的打包后检查

1. 运行 `dist\咨询报告助手\咨询报告助手.exe`
2. 确认设置里默认显示 `默认通道`
3. 新建一个测试项目，填写 V2 字段
4. 发起一轮聊天，确认能正常响应
5. 打开右侧工作区，确认阶段和文件预览正常
6. 点击质量检查和导出，确认返回结果清晰

## 常见问题

### 默认通道调用失败

先检查：

```cmd
curl https://newapi.z0y0h.work/client/v1/models -H "Authorization: Bearer managed"
```

如果不通：

- 先确认服务端薄中转在线
- 再确认发布包里已经带上对应的 `managed_client_token.txt`
- 临时切到 `自定义 API`

### 前端构建失败

```cmd
cd frontend
rmdir /s /q node_modules
del package-lock.json
npm install
npm run build
```

### PyInstaller 打包失败

- 优先使用 [consulting_report.spec](D:/Codex/CodexProjects/Consulting-report-agent/.worktrees/client-v2/consulting_report.spec)
- 确认 `frontend/dist` 和 `skill/` 已存在

## 技术说明

- PyInstaller 负责打包 exe
- PyWebView 负责桌面窗口
- FastAPI 负责本地 API
- React 负责界面

程序运行时本质上是：

1. 本地启动一个 FastAPI 服务
2. PyWebView 打开内嵌窗口
3. LLM 请求默认走托管薄中转
