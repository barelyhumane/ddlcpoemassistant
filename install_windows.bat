@echo off
setlocal EnableExtensions DisableDelayedExpansion

title DDLC+ Poem Assistant - Windows Setup

cd /d "%~dp0"

set "TESSERACT_URL=https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe"
set "TESSERACT_INSTALLER=%TEMP%\ddlc-tesseract-5.5.3.exe"
set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

echo.
echo ================================================
echo   DDLC+ Poem Assistant - Windows Setup
echo ================================================
echo.

rem Prefer the Python launcher, then fall back to python.exe.
where py.exe >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
) else (
    where python.exe >nul 2>&1
    if errorlevel 1 goto :python_missing
    set "PYTHON_CMD=python"
)

echo Checking Python...
%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 goto :python_old
echo Python 3.9 or newer found.

if not exist "%VENV_PYTHON%" (
    echo.
    echo Creating a local Python environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_failed
)

echo.
echo Installing Python requirements...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :pip_failed
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :pip_failed

call :add_tesseract_path "%ProgramFiles%\Tesseract-OCR"
if not "%ProgramFiles(x86)%"=="" call :add_tesseract_path "%ProgramFiles(x86)%\Tesseract-OCR"
call :add_tesseract_path "%LOCALAPPDATA%\Programs\Tesseract-OCR"

where tesseract.exe >nul 2>&1
if not errorlevel 1 goto :tesseract_ready

echo.
echo Tesseract was not found. Downloading the OCR installer...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%TESSERACT_URL%' -OutFile '%TESSERACT_INSTALLER%'"
if errorlevel 1 goto :download_failed

echo Installing Tesseract OCR...
start "" /wait "%TESSERACT_INSTALLER%" /S
if errorlevel 1 goto :tesseract_failed

call :add_tesseract_path "%ProgramFiles%\Tesseract-OCR"
if not "%ProgramFiles(x86)%"=="" call :add_tesseract_path "%ProgramFiles(x86)%\Tesseract-OCR"
call :add_tesseract_path "%LOCALAPPDATA%\Programs\Tesseract-OCR"

where tesseract.exe >nul 2>&1
if errorlevel 1 goto :tesseract_not_found

:tesseract_ready
echo Tesseract found:
tesseract.exe --version 2>nul | findstr /b /c:"tesseract" >nul
if errorlevel 1 goto :tesseract_not_found
echo.
echo ================================================
echo Setup complete.
echo ================================================
echo.
echo You can also run it manually with:
echo     "%VENV_PYTHON%" ddlc_poem_assistant.py
echo.
pause
exit /b 0

:add_tesseract_path
if exist "%~1\tesseract.exe" (
    set "PATH=%~1;%PATH%"
)
exit /b 0

:python_missing
echo.
echo Python 3.9 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/ and run this file again.
pause
exit /b 1

:python_old
echo.
echo Python 3.9 or newer is required.
echo Please update Python and run this file again.
pause
exit /b 1

:venv_failed
echo.
echo Could not create the local Python environment.
echo Make sure Python was installed with the standard library and venv support.
pause
exit /b 1

:pip_failed
echo.
echo Python requirements could not be installed.
echo Check your internet connection and try again.
pause
exit /b 1

:download_failed
echo.
echo Could not download Tesseract from:
echo %TESSERACT_URL%
echo Check your internet connection and try again.
pause
exit /b 1

:tesseract_failed
echo.
echo The Tesseract installer did not complete successfully.
echo Try running the downloaded installer manually:
echo %TESSERACT_INSTALLER%
pause
exit /b 1

:tesseract_not_found
echo.
echo Tesseract installation finished, but tesseract.exe was not found.
echo Try opening a new Command Prompt and run this setup file again.
pause
exit /b 1
