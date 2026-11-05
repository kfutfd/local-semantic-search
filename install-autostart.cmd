@echo off
cd /d "%~dp0"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LocalSearch.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:LINK); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0scripts\daemon.py\"'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Description='本地语义搜索'; $s.Save(); Write-Host '已设置开机自启动'"
echo 下次登录 Windows 时会自动在后台启动。
pause
