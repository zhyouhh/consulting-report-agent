@echo off
chcp 65001 >/dev/null
echo ========================================
echo 咨询报告助手 - Windows 打包脚本
echo ========================================
echo.

echo [1/5] 检查 Python 环境...
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.11 或 3.12
    pause
    exit /b 1
)
python --version

echo.
echo [2/5] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo 错误：依赖安装失败
    pause
    exit /b 1
)

echo.
echo [3/5] 构建前端...
cd frontend
call npm install
if errorlevel 1 (
    echo 错误：前端依赖安装失败
    cd ..
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo 错误：前端构建失败
    cd ..
    pause
    exit /b 1
)
cd ..

echo.
echo [4/5] 安装 PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo 错误：PyInstaller 安装失败
    pause
    exit /b 1
)

echo.
echo [5/5] 打包应用程序...
pyinstaller consulting_report.spec
if errorlevel 1 (
    echo 错误：打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo 打包完成！
echo 可执行文件位置：dist\咨询报告助手\咨询报告助手.exe
echo ========================================
pause
