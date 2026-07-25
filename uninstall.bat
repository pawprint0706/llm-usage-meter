@echo off
REM Uninstall LLM Usage Meter (Windows): stop the app, remove the autostart
REM entry and the stored logins, then delete the app data and the virtualenv.
cd /d "%~dp0"

echo This will stop LLM Usage Meter and remove:
echo   - the "Start at login" registry entry
echo   - the Codex login and the OpenCode session key from Credential Manager
echo   - app data in "%USERPROFILE%\.llm-usage-meter"
echo   - the .venv folder in this project
set /p CONFIRM="Continue? [y/N] "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Stopping the app and removing logins and autostart...
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe launch.py --uninstall >nul 2>&1
)

REM Fallback: remove the Run key value directly, in case the .venv is gone.
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v LlmUsageMeter /f >nul 2>&1

echo Removing app data and .venv...
if exist "%USERPROFILE%\.llm-usage-meter" rmdir /s /q "%USERPROFILE%\.llm-usage-meter"
if exist .venv rmdir /s /q .venv

echo.
echo Done. LLM Usage Meter has been removed.
echo You can now delete this project folder if you want: %~dp0
pause
