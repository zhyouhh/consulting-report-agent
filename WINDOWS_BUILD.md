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
- 推荐在项目根目录使用 `.venv`

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
2. `build.bat` 会自动创建并复用项目根目录的 `.venv`
3. 提前准备 `managed_client_token.txt`，或设置环境变量 `CONSULTING_REPORT_MANAGED_CLIENT_TOKEN`
   这个文件必须是 `/client` 使用的 client token，不是上游 API key
4. 提前准备 `managed_search_pool.json`，或设置环境变量 `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`
   这个文件是内置搜索池的私有配置，不应提交到 Git；如果走环境变量覆盖，`build.bat` 会在打包前临时复制它
5. 优先把这两个私有文件直接放在仓库根目录，避免误用残留环境变量
6. 等待脚本自动安装依赖、构建前端、执行 PyInstaller
7. 打包产物位于 `dist\咨询报告助手\`

### 方法二：手动打包

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install pyinstaller

copy D:\私有配置\managed_client_token.txt managed_client_token.txt
copy D:\私有配置\portable-search-pool.json managed_search_pool.json

cd frontend
npm install
npm run build
cd ..

.venv\Scripts\python -m PyInstaller consulting_report.spec
```

打包脚本会先请求 `https://newapi.z0y0h.work/client/v1/models` 预检 token；
如果这一步不通过，会直接拒绝继续打包。
它也会校验 `managed_search_pool.json` 的完整 schema，至少包括：

- provider 级字段：
  - `weight`
  - `minute_limit`
  - `daily_soft_limit`
  - `cooldown_seconds`
- limits 级字段：
  - `per_turn_searches`
  - `project_minute_limit`
  - `global_minute_limit`
  - `memory_cache_ttl_seconds`
  - `project_cache_ttl_seconds`

如果你不走 `build.bat`，而是直接运行 `pyinstaller consulting_report.spec`，
就必须先手工把私有搜索池文件放到仓库根目录，并命名为 `managed_search_pool.json`，
同时也建议使用项目自己的 `.venv`，不要直接用全局 Python 或 Anaconda 环境。

## 打包产物

```text
dist/
└── 咨询报告助手/
    ├── 咨询报告助手.exe
    └── _internal/
        ├── managed_client_token.txt
        ├── managed_search_pool.json
        ├── skill/
        ├── frontend/
        └── ...
```

PyInstaller 把所有 `datas`（skill、frontend/dist、私有文件）都收到 `_internal/` 下面，运行时通过 `sys._MEIPASS` 寻址。

## 分发说明

- 必须分发整个 `dist\咨询报告助手\` 文件夹。
- 建议压缩为 zip 再发给同事。
- 同事解压后直接双击 `咨询报告助手.exe`。
- 运行时动态状态与缓存会落到：
  - `%USERPROFILE%\.consulting-report\search_runtime_state.json`
  - `%USERPROFILE%\.consulting-report\search_cache.json`

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
curl https://newapi.z0y0h.work/client/v1/models -H "Authorization: Bearer 你的_client_token"
```

如果不通：

- 先确认服务端薄中转在线
- 再确认发布包里已经带上对应的 `managed_client_token.txt`
- 再确认这个 token 是 client token，不是上游 API key
- 临时切到 `自定义 API`

### 内置搜索池配置丢了

- 构建机上最重要的源文件是 `managed_search_pool.json`
- 打包后它会出现在 `dist\咨询报告助手\_internal\managed_search_pool.json`
- 这份文件里的搜索 provider 凭据会随包分发；它不是服务端薄中转令牌
- 这个文件不应该进 Git，但应该单独备份，方便你自己带走
- 如果你通过环境变量传入外部路径，`build.bat` 会把它临时 staging 成根目录的 `managed_search_pool.json`，打包结束后再清理或恢复

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
- 如果包体突然膨胀到数百 MB 甚至 1GB 以上，优先检查是不是误用了全局 Python/Anaconda，而不是项目 `.venv`

## 技术说明

- PyInstaller 负责打包 exe
- PyWebView 负责桌面窗口
- FastAPI 负责本地 API
- React 负责界面

程序运行时本质上是：

1. 本地启动一个 FastAPI 服务
2. PyWebView 打开内嵌窗口
3. LLM 请求默认走托管薄中转
