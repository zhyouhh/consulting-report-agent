@echo off
chcp 65001 >nul
set TOKEN_FILE=managed_client_token.txt
set SEARCH_POOL_FILE=managed_search_pool.json
set GENERATED_MANAGED_TOKEN_FILE=0
set STAGED_SEARCH_POOL_FILE=0
set RESTORE_SEARCH_POOL_FILE=0
set SEARCH_POOL_BACKUP_FILE=%SEARCH_POOL_FILE%.bundle.bak
set MODELS_URL=https://newapi.z0y0h.work/client/v1/models
echo ========================================
echo 咨询报告助手 - Windows 打包脚本
echo ========================================
echo.

echo [1/9] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.11 或 3.12
    pause
    exit /b 1
)
python --version

echo.
echo [2/9] 安装后端依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo 错误：依赖安装失败
    pause
    exit /b 1
)

echo.
echo [3/9] 准备默认通道令牌...
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
echo [4/9] 准备内置搜索池配置...
if defined CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE (
    if not exist "%CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE%" (
        echo 错误：环境变量 CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE 指向的文件不存在
        call :cleanup_bundle_files
        pause
        exit /b 1
    )
    if exist "%SEARCH_POOL_FILE%" (
        copy /y "%SEARCH_POOL_FILE%" "%SEARCH_POOL_BACKUP_FILE%" >nul
        if errorlevel 1 (
            echo 错误：备份现有 %SEARCH_POOL_FILE% 失败
            call :cleanup_bundle_files
            pause
            exit /b 1
        )
        set RESTORE_SEARCH_POOL_FILE=1
    )
    copy /y "%CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE%" "%SEARCH_POOL_FILE%" >nul
    if errorlevel 1 (
        echo 错误：暂存内置搜索池配置失败
        call :cleanup_bundle_files
        pause
        exit /b 1
    )
    set STAGED_SEARCH_POOL_FILE=1
) else (
    if not exist "%SEARCH_POOL_FILE%" (
        echo 错误：发布包需要 %SEARCH_POOL_FILE% 或环境变量 CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE
        echo 提示：内置搜索池要开箱即用，就必须在打包时注入私有搜索池配置。
        call :cleanup_bundle_files
        pause
        exit /b 1
    )
)

echo.
echo [5/9] 验证默认通道客户端令牌...
python -c "from pathlib import Path; from build_support import validate_bundle_managed_client_token; validate_bundle_managed_client_token(Path(r'.'), r'%TOKEN_FILE%', r'%MODELS_URL%')"
if errorlevel 1 (
    echo 错误：默认通道令牌校验失败
    echo 提示：%TOKEN_FILE% 必须是 /client 的 client token，不是上游 API key
    call :cleanup_bundle_files
    pause
    exit /b 1
)

echo.
echo [6/9] 验证内置搜索池配置...
python -c "from pathlib import Path; from build_support import validate_bundle_managed_search_pool; validate_bundle_managed_search_pool(Path(r'.'), r'%SEARCH_POOL_FILE%')"
if errorlevel 1 (
    echo 错误：内置搜索池配置校验失败
    call :cleanup_bundle_files
    pause
    exit /b 1
)

echo.
echo [7/9] 构建前端...
cd frontend
call npm install
if errorlevel 1 (
    echo 错误：前端依赖安装失败
    cd ..
    call :cleanup_bundle_files
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo 错误：前端构建失败
    cd ..
    call :cleanup_bundle_files
    pause
    exit /b 1
)
cd ..

echo.
echo [8/9] 安装 PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo 错误：PyInstaller 安装失败
    call :cleanup_bundle_files
    pause
    exit /b 1
)

echo.
echo [9/9] 打包应用程序...
pyinstaller consulting_report.spec
if errorlevel 1 (
    echo 错误：打包失败
    call :cleanup_bundle_files
    pause
    exit /b 1
)

call :cleanup_bundle_files

echo.
echo ========================================
echo 打包完成！
echo 可执行文件位置：dist\咨询报告助手\咨询报告助手.exe
echo ========================================
pause

:cleanup_bundle_files
if "%GENERATED_MANAGED_TOKEN_FILE%"=="1" (
    if exist "%TOKEN_FILE%" del "%TOKEN_FILE%"
)
if "%RESTORE_SEARCH_POOL_FILE%"=="1" (
    copy /y "%SEARCH_POOL_BACKUP_FILE%" "%SEARCH_POOL_FILE%" >nul
    if exist "%SEARCH_POOL_BACKUP_FILE%" del "%SEARCH_POOL_BACKUP_FILE%"
) else (
    if "%STAGED_SEARCH_POOL_FILE%"=="1" (
        if exist "%SEARCH_POOL_FILE%" del "%SEARCH_POOL_FILE%"
    )
)
exit /b 0
