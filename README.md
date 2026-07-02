# 咨询报告写作助手

Windows first 的咨询报告桌面客户端，目标是把你自用的咨询报告 skill 包成一个同事拿到就能用的桌面工具。

## 当前阶段

- 第一阶段只支持 Windows 正式分发
- 默认模式是 `试用通道`
- 默认入口是 `https://newapi.z0y0h.work/client/v1`
- 默认模型是 `deepseek-v4-pro`
- 同时保留 `自定义 API` 入口，供高级用户自行配置

## 现在已经能做什么

- 新建咨询项目，填写 V2 项目元数据
- 在桌面客户端里持续对话式推进报告写作
- 查看工作区阶段、阶段清单和文件预览
- 运行质量检查
- 导出 `可审草稿`（Windows 发布包内置 Pandoc，同事无需另装）

当前没有承诺：

- macOS 正式支持
- 最终排版完成的一键终稿交付

## 运行方式

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

### 1. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

cd frontend
npm install
cd ..
```

### 2. 构建前端

```bash
cd frontend
npm run build
cd ..
```

### 3. 启动桌面应用

```bash
python app.py
```

## Windows 打包

```bash
build.bat
```

打包机需要提前准备 `managed_client_token.txt`、`managed_search_pool.json` 和可随包分发的 Pandoc。
打包产物在 `dist/咨询报告助手/`，其中包含 `_internal/pandoc.exe`。

## 相关文档

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
