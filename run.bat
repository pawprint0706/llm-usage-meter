@echo off
REM Double-click to (re)run LLM Usage Meter on Windows.
REM A running instance is stopped first (--replace), and the app is started
REM detached (pythonw = no console window).
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo No .venv found - run setup.bat first.
    pause
    exit /b 1
)

REM Logs: %USERPROFILE%\.llm-usage-meter\app.log
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe launch.py --replace
) else (
    start "" .venv\Scripts\python.exe launch.py --replace
)
