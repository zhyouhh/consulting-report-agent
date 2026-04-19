# 打包部署指南

## 当前交付形态

- 第一阶段只支持 Windows 正式分发。
- 交付物是一个完整文件夹，不是单独一个裸 `exe`。
- 默认模式是 `默认通道`，指向 `https://newapi.z0y0h.work/client/v1`。
- 只要默认托管代理已经部署完成，同事拿到包后可以直接开箱即用。
- 仍然保留 `自定义 API` 入口，给有条件的高级用户自行配置。
- 当前导出能力是 `可审草稿`，不是最终排版完成的 Word/PDF 成品。

## Windows 打包步骤

### 1. 准备环境

```bash
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install pyinstaller

cd frontend
npm install
npm run build
cd ..
```

`build.bat` 现在也会自动创建并复用项目根目录的 `.venv`，
避免误用 Anaconda 之类的大环境把无关依赖一起打进包里。

### 2. 执行打包

先准备默认通道客户端令牌，二选一：

- 在项目根目录放一个不入库的 `managed_client_token.txt`
- 或设置环境变量 `CONSULTING_REPORT_MANAGED_CLIENT_TOKEN`

再准备内置搜索池配置：

- 在项目根目录放一个不入库的 `managed_search_pool.json`
- 或在运行 `build.bat` 前设置环境变量 `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`

`build.bat` 会在缺少这两者时直接失败，避免打出一个表面成功、实际不能开箱即用的包。
它还会在打包前请求 `https://newapi.z0y0h.work/client/v1/models` 做预检。
`managed_client_token.txt` 必须放的是 `/client` 使用的 client token，不是上游 API key。
`managed_search_pool.json` 必须包含完整的内置搜索池 schema，至少包括：

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

如果使用 `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`，
`build.bat` 会把任意源路径的私有文件临时复制成仓库根目录的 `managed_search_pool.json` 再打包，
打包结束后自动清理或恢复原文件。

```bash
.venv\Scripts\python -m PyInstaller consulting_report.spec
```

如果你跳过 `build.bat` 直接运行 `pyinstaller consulting_report.spec`，
那就必须先把私有搜索池文件放到仓库根目录，并命名为 `managed_search_pool.json`，
同时也建议使用项目自己的 `.venv`，不要直接用全局 Python 环境。

生成目录：

```text
dist/咨询报告助手/
  咨询报告助手.exe
  _internal/
    skill/
    frontend/dist/
    managed_client_token.txt
    managed_search_pool.json
    ...
```

PyInstaller 把所有 `datas` 收到 `_internal/` 下面，`sys._MEIPASS` 在运行时会指向这里。

### 3. 首次运行

双击 `dist/咨询报告助手/咨询报告助手.exe` 后：

- 程序会启动本地 FastAPI + PyWebView 窗口。
- 用户配置会自动写入 `C:\Users\<用户名>\.consulting-report\config.json`。
- 默认会使用 `默认通道`。
- 如果默认通道不可达，用户仍可在设置中切到 `自定义 API`。

## 配置说明

### 默认通道

- 面向普通同事，开箱即用。
- 默认模型：`gemini-3-flash`
- 默认地址：`https://newapi.z0y0h.work/client/v1`
- 客户端不保存真实上游 key，真实凭证只存在服务端薄中转。
- 发布包需要注入单独的客户端令牌文件 `managed_client_token.txt`。
- 打包前应先确认该 client token 能通过 `/client/v1/models` 验证。

### 内置搜索池

- 面向默认安装包，开箱即用。
- 构建机上的私有源文件是 `managed_search_pool.json`。
- 该文件不会入库，但会作为打包产物的一部分被复制到 `dist/咨询报告助手/_internal/managed_search_pool.json`。
- 这意味着内置搜索池凭据会随安装包一起分发；它不是像默认通道那样的服务端薄中转令牌。
- 你以后真正要“自己带着走”的就是这份私有配置文件。
- 运行时动态状态不写回包内，而是写到：
  - `C:\Users\<用户名>\.consulting-report\search_runtime_state.json`
  - `C:\Users\<用户名>\.consulting-report\search_cache.json`

### 自定义 API

- 面向高级用户。
- 通过设置弹窗填写自己的 OpenAI 兼容 `Base URL`、`API Key`、`Model`。
- 模型列表下拉会按用户填写的接口动态拉取。

## 分发建议

- 不要只发 `咨询报告助手.exe`，要发整个 `dist/咨询报告助手/` 文件夹。
- 建议压缩成 zip 后分发。
- 首次运行如果被杀软拦截，需要添加信任。

## 常见问题

**Q: 打包后同事还要自己构建前后端吗？**  
A: 不需要。分发的是打包后的完整文件夹，直接双击 `exe` 即可。

**Q: 默认通道不能用怎么办？**  
A: 先检查 `https://newapi.z0y0h.work/client/v1/models` 是否可访问；再确认 `managed_client_token.txt` 放的是 client token，而不是上游 API key；仍不通时可临时切到 `自定义 API`。

**Q: 内置搜索池文件放在哪里？**  
A: 构建机源文件放在项目根目录的 `managed_search_pool.json`；打包后副本会进入 `dist/咨询报告助手/_internal/managed_search_pool.json`；运行时状态和缓存写在用户目录下的 `search_runtime_state.json` 与 `search_cache.json`。

**Q: 现在导出是不是最终 Word/PDF？**  
A: 不是。当前是 `可审草稿` 导出，用于内部审阅和继续修改。
