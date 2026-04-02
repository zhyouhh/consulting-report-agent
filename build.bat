@echo off
chcp 65001 >nul
set TOKEN_FILE=managed_client_token.txt
set GENERATED_MANAGED_TOKEN_FILE=0
set MODELS_URL=https://newapi.z0y0h.work/client/v1/models
echo ========================================
echo 咨询报告助手 - Windows 打包脚本
echo ========================================
echo.

echo [1/7] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.11 或 3.12
    pause
    exit /b 1
)
python --version

echo.
echo [2/7] 准备默认通道令牌...
if defined CONSULTING_REPORT_MANAGED_CLIENT_TOKEN (
    python -c "from pathlib import Path; import os; Path(r'%TOKEN_FILE%').write_text(os.environ['CONSULTING_REPORT_MANAGED_CLIENT_TOKEN'], encoding='utf-8')"
    if errorlevel 1 (
        echo 错误：写入默认通道令牌失败
        pause
        exit /b 1
    )
    set GENERATED_MANAGED_TOKEN_FILE=1
) else (
    if not exist "%TOKEN_FILE%" (
        echo 错误：发布包需要 %TOKEN_FILE% 或环境变量 CONSULTING_REPORT_MANAGED_CLIENT_TOKEN
        echo 提示：默认通道要开箱即用，就必须在打包时注入专用客户端令牌。
        pause
        exit /b 1
    )
)

echo.
echo [3/7] 验证默认通道客户端令牌...
python -c "from pathlib import Path; from build_support import validate_bundle_managed_client_token; validate_bundle_managed_client_token(Path(r'.'), r'%TOKEN_FILE%', r'%MODELS_URL%')"
if errorlevel 1 (
    echo 错误：默认通道令牌校验失败
    echo 提示：%TOKEN_FILE% 必须是 /client 的 client token，不是上游 API key
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)

echo.
echo [4/7] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo 错误：依赖安装失败
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)

echo.
echo [5/7] 构建前端...
cd frontend
call npm install
if errorlevel 1 (
    echo 错误：前端依赖安装失败
    cd ..
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo 错误：前端构建失败
    cd ..
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)
cd ..

echo.
echo [6/7] 安装 PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo 错误：PyInstaller 安装失败
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)

echo.
echo [7/7] 打包应用程序...
pyinstaller consulting_report.spec
if errorlevel 1 (
    echo 错误：打包失败
    if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"
    pause
    exit /b 1
)

if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" del "%TOKEN_FILE%"

echo.
echo ========================================
echo 打包完成！
echo 可执行文件位置：dist\咨询报告助手\咨询报告助手.exe
echo ========================================
pause
