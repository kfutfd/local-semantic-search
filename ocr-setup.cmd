@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行 setup.cmd
    pause
    exit /b 1
)
echo OCR 已随 setup.cmd 一起安装，平时不需要再运行本脚本。
echo 只有 OCR 报错（日志里出现 rapidocr unavailable）时，才用它修复安装。
echo.
echo 重新安装 OCR 依赖...
".venv\Scripts\python.exe" -m pip install -r requirements-ocr.txt
if errorlevel 1 goto :err
echo 完成。
pause
exit /b 0
:err
echo 安装失败。
pause
exit /b 1
