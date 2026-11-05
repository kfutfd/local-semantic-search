@echo off
cd /d "%~dp0"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LocalSearch.lnk"
if exist "%LINK%" (
    del /q "%LINK%"
    echo 已取消开机自启动。
) else (
    echo 当前没有开机自启动项。
)
pause
