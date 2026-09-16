@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "TESSERACT_DIR=%ProgramFiles%\Tesseract-OCR"
if not exist "%TESSERACT_DIR%\tesseract.exe" set "TESSERACT_DIR=%ProgramFiles(x86)%\Tesseract-OCR"

if not exist "%PYTHON%" (
    echo Could not find the project virtual environment.
    echo Run install_windows.bat first.
    pause
    exit /b 1
)

if not exist "%TESSERACT_DIR%\tesseract.exe" (
    echo Could not find Tesseract OCR.
    echo Install Tesseract first, then run this build script again.
    pause
    exit /b 1
)

echo Installing/updating PyInstaller in the project environment...
"%PYTHON%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo PyInstaller installation failed.
    pause
    exit /b 1
)

echo Building the bundled Windows executable...
"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "DDLC+ Poem Assistant" ^
    --distpath "%~dp0release" ^
    --workpath "%~dp0build\pyinstaller-work" ^
    --specpath "%~dp0build\pyinstaller-work" ^
    --add-data "%~dp0word_data.json;." ^
    --add-data "%~dp0Aller_Std_Rg.ttf;." ^
    --add-data "%TESSERACT_DIR%;tesseract" ^
    "%~dp0ddlc_poem_assistant.py"

if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo %~dp0release\DDLC+ Poem Assistant.exe
pause
