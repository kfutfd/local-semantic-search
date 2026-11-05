@echo off
cd /d "%~dp0"
echo ============================================
echo    本地语义搜索 - 安装
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
    echo 没有找到 python。请先安装 Python 3.10 - 3.12，
    echo 安装时勾选 "Add Python to PATH"，然后重新运行本脚本。
    pause
    exit /b 1
)
echo [1/4] 创建虚拟环境 .venv ...
python -m venv .venv
if errorlevel 1 goto :err
echo.
echo [2/4] 安装依赖（约 250 MB，含 OCR，首次较慢）...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :err
echo.
echo [3/4] 生成配置 settings.json ...
".venv\Scripts\python.exe" scripts\setup_settings.py
if errorlevel 1 goto :err
echo.
echo [4/4] 建立首次索引（扫描桌面和下载，可能需要几分钟）...
".venv\Scripts\python.exe" scripts\ingest.py --from-settings
".venv\Scripts\python.exe" scripts\embed_docs.py --model bge-small --device auto
".venv\Scripts\python.exe" scripts\build_all.py --model bge-small
if errorlevel 1 goto :err
echo.
echo ============================================
echo  安装完成。
echo  双击 start-daemon.cmd 启动后台服务，
echo  然后按 Ctrl + Alt + Space 唤出搜索框。
echo.
echo  扫描版 PDF 会在后台自动 OCR，无需手动操作。
echo ============================================
pause
exit /b 0
:err
echo.
echo 安装失败，请把上面的报错信息保存下来。
pause
exit /b 1
