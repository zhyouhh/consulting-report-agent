# Windows 打包指南

## 环境准备

### 1. 安装 Python

- 下载 Python 3.11 或 3.12（推荐 3.11）
- 官网：https://www.python.org/downloads/
- **重要**：安装时勾选 "Add Python to PATH"

### 2. 安装 Node.js

- 下载 Node.js 20.x LTS
- 官网：https://nodejs.org/
- 默认安装即可

### 3. 验证环境

打开命令提示符（cmd），运行：
```cmd
python --version
node --version
npm --version
```

确保都能正常显示版本号。

## 打包步骤

### 方法一：一键打包（推荐）

1. 双击运行 `build.bat`
2. 等待打包完成（约 5-10 分钟）
3. 打包完成后，可执行文件在 `dist\咨询报告助手\` 目录

### 方法二：手动打包

#### 步骤 1：安装 Python 依赖

```cmd
pip install -r requirements.txt
```

#### 步骤 2：构建前端

```cmd
cd frontend
npm install
npm run build
cd ..
```

#### 步骤 3：安装 PyInstaller

```cmd
pip install pyinstaller pywebview
```

#### 步骤 4：执行打包

```cmd
pyinstaller consulting_report.spec
```

## 打包产物

打包完成后，目录结构：
```
dist/
└── 咨询报告助手/
    ├── 咨询报告助手.exe    # 主程序
    ├── skill/              # Skill 定义文件
    ├── frontend/           # 前端静态文件
    └── _internal/          # 依赖库
```

## 分发说明

### 完整分发

将整个 `dist\咨询报告助手\` 文件夹打包成 zip，分发给用户。

用户解压后，双击 `咨询报告助手.exe` 即可使用。

### 注意事项

1. **不要单独分发 exe 文件**，必须包含整个文件夹
2. 首次运行可能被杀毒软件拦截，添加信任即可
3. 配置文件会自动创建在用户目录 `~\.consulting-report\`

## 常见问题

### Python 版本问题

**问题**：打包时报错 "Python 3.14 不支持"

**解决**：使用 Python 3.11 或 3.12，不要使用 3.14

### 依赖安装失败

**问题**：pip install 报错

**解决**：
```cmd
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 前端构建失败

**问题**：npm run build 报错

**解决**：
```cmd
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

### PyInstaller 打包失败

**问题**：找不到模块

**解决**：检查 `consulting_report.spec` 中的 `hiddenimports` 列表，添加缺失的模块。

## 测试打包结果

打包完成后，建议测试：

1. 运行 `dist\咨询报告助手\咨询报告助手.exe`
2. 创建一个测试项目
3. 发送几条消息，确认 AI 响应正常
4. 检查项目文件是否正确保存

## 更新打包

如果修改了代码，重新打包：

1. 如果只改了 Python 代码：直接运行 `pyinstaller consulting_report.spec`
2. 如果改了前端代码：先 `cd frontend && npm run build`，再打包
3. 如果改了依赖：先 `pip install -r requirements.txt`，再打包

## 技术说明

- **PyInstaller**：将 Python 程序打包成 exe
- **PyWebView**：创建桌面窗口，内嵌浏览器
- **FastAPI**：后端 API 服务
- **React**：前端界面

打包后的程序本质是：
1. 启动一个本地 FastAPI 服务（127.0.0.1:8080）
2. 用 PyWebView 创建窗口访问这个服务
3. 所有功能都在本地运行，无需联网（除了调用 LLM API）
