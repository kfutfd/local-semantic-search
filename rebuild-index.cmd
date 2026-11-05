@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 还没有安装，请先双击 setup.cmd
    pause
    exit /b 1
)
echo 正在停止后台服务...
".venv\Scripts\python.exe" scripts\stop_daemon.py
echo.
echo [1/3] 抽取文本与切块...
".venv\Scripts\python.exe" scripts\ingest.py --from-settings
if errorlevel 1 goto :err
echo.
echo [2/3] 向量化...
".venv\Scripts\python.exe" scripts\embed_docs.py --model bge-small --device auto
if errorlevel 1 goto :err
echo.
echo [3/3] 重建合并索引...
".venv\Scripts\python.exe" scripts\build_all.py --model bge-small
if errorlevel 1 goto :err
echo.
echo 完成，正在重启后台服务...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\daemon.py"
pause
exit /b 0
:err
echo.
echo 出错了，请把上面的信息发我。
pause
exit /b 1
