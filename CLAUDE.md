# 咨询报告写作智能体

## 项目概述
为公司部门开发的咨询报告写作智能体，面向不太懂AI的同事使用。

## 项目目标
- 提供简单易用的界面（网站或桌面程序）
- 帮助同事快速生成专业的咨询报告
- 作为部门AI应用的成果展示

## 目标用户
- 公司部门同事
- AI使用经验：初级/无经验
- 需求：简单、直观、可靠

## 技术栈（已确定）

**后端**：
- Python 3.9+
- FastAPI（Web框架）
- OpenAI SDK（LLM调用，兼容硅基流动API）
- PyWebView（桌面客户端封装）

**前端**：
- React 18
- Tailwind CSS
- Axios（API调用）
- React Markdown（内容预览）

**部署**：
- PyWebView桌面客户端（Windows exe）
- 本地运行，无需服务器

## 已实现的功能

### 1. Skill定义系统 ✅
- 4种报告类型：专题研究、体系规划、实施方案、管理制度
- 流程门禁机制：项目初始化 → 大纲设计 → 分段撰写 → 质量审查 → 导出
- 去AI化规则：禁用"赋能、抓手、闭环"等词汇
- 数据真实性检查：严禁编造数据，必须标注来源
- 质量检查问答式：主动向用户确认关键信息

### 2. 后端API ✅
- Function Calling机制：LLM可调用write_file、read_file、web_search工具
- 项目管理：创建项目、保存对话历史、文件操作
- 安全加固：路径遍历防护、输入验证、循环调用限制
- 默认配置：硅基流动API + DeepSeek-V3.2模型

### 3. 前端界面 ✅
- 三栏布局：项目列表 | 对话区 | Markdown预览
- 类Notion设计：现代简约风格
- 实时预览：动态加载项目文件
- 已修复问题：key值、URL编码、select value

## 当前状态

**开发进度**：核心功能已完成，关键问题已修复（2026-03-16）

**已完成的修复**（2026-03-16）：
1. ✅ 前端消息列表key值问题 - 添加唯一id字段
2. ✅ 前端useEffect依赖项问题 - 使用useCallback优化
3. ✅ API Key安全存储 - 移至~/.consulting-report/config.json
4. ✅ 输入长度限制 - 添加消息验证和max_tokens=4096
5. ✅ 打包路径兼容 - 添加get_base_path()函数
6. ✅ 依赖版本统一 - 删除backend/requirements.txt
7. ✅ 静态文件服务 - 在main.py中添加挂载

**下一步**：
1. 构建前端（cd frontend && npm run build）
2. 本地测试运行（python app.py）
3. PyInstaller打包成exe
4. 内部试用收集反馈

## 开发注意事项
- 界面简洁直观，三栏布局清晰
- Skill流程引导用户逐步完成报告
- 错误提示友好（已添加try-catch）
- 本地部署，双击exe即可使用
- 配置文件位于用户目录：~/.consulting-report/
