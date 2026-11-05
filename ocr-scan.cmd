@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行 setup.cmd
    pause
    exit /b 1
)
echo 后台每轮扫描会自动识别一部分扫描版 PDF，通常不需要运行本脚本。
echo 本脚本会一次性把剩下的全部识别完（插电运行，可随时中断续跑）。
echo.
".venv\Scripts\python.exe" scripts\ingest.py --from-settings >nul
".venv\Scripts\python.exe" scripts\ocr_auto.py --max-pages 100000 --max-seconds 86400 --workers 3 --threads 2
echo.
echo 完成后运行 rebuild-index.cmd 把它们并进索引。
pause
