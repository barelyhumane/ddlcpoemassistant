@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"

set "APP_FILE=%~dp0ddlc_poem_assistant.py"
set "VENV_PYTHON=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%VENV_PYTHON%" (
    echo First-time setup is needed. Starting the installer...
    call "%~dp0install_windows.bat"
    if errorlevel 1 exit /b 1
)

if not exist "%VENV_PYTHON%" (
    echo Setup did not create the local Python environment.
    pause
    exit /b 1
)

start "" "%VENV_PYTHON%" "%APP_FILE%"
exit /b 0
