@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m orbio.app
if errorlevel 1 pause
