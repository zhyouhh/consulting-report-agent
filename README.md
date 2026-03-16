# 咨询报告写作助手

基于学术写作skill改造的咨询报告写作智能体，支持专题研究、体系规划、实施方案、管理制度等报告类型。

## 快速开始

### 1. 安装依赖

```bash
# Python依赖
pip install -r requirements.txt

# 前端依赖
cd frontend
npm install
cd ..
```

### 2. 配置API

创建 `config.json` 文件：

```json
{
  "api_provider": "siliconflow",
  "api_key": "你的API密钥",
  "api_base": "https://api.siliconflow.cn/v1",
  "model": "deepseek-ai/DeepSeek-V3"
}
```

### 3. 构建前端

```bash
cd frontend
npm run build
cd ..
```

### 4. 启动应用

```bash
python app.py
```

双击启动后会自动打开桌面窗口。

## 项目结构

```
├── skill/              # Skill定义和模板
├── backend/            # Python后端
├── frontend/           # React前端
├── projects/           # 用户报告项目（运行时生成）
└── app.py             # 启动入口
```

## 使用流程

1. 新建报告项目
2. 与AI讨论需求，确认大纲
3. 分章节撰写内容
4. 审阅和修改
5. 导出Word/PDF

## 技术栈

- 后端：FastAPI + OpenAI SDK
- 前端：React + Tailwind CSS
- 桌面：PyWebView
- 打包：PyInstaller
