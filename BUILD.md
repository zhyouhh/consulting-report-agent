# 打包部署指南

## Windows打包步骤

### 1. 准备环境
```bash
# 安装依赖
pip install -r requirements.txt
pip install pyinstaller

# 构建前端
cd frontend
npm install
npm run build
cd ..
```

### 2. 打包exe
```bash
pyinstaller build.spec
```

生成的exe位于 `dist/咨询报告助手/咨询报告助手.exe`

### 3. 首次运行
双击exe后，配置文件会自动创建在：
- Windows: `C:\Users\<用户名>\.consulting-report\config.json`

需要配置API Key后才能使用。

## 配置说明

默认配置：
- API提供商：硅基流动
- 模型：deepseek-chat
- API Base: https://api.siliconflow.cn/v1

可在应用内通过设置界面修改。

## 常见问题

**Q: 打包后体积太大？**
A: 使用UPX压缩（已在build.spec中启用）

**Q: 启动报错找不到文件？**
A: 检查frontend/dist是否存在，确保先运行npm run build

**Q: API调用失败？**
A: 检查API Key是否正确，网络是否正常
