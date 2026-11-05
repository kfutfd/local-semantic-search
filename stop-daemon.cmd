@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\stop_daemon.py
ping -n 3 127.0.0.1 >nul
