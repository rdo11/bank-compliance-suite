@echo off
rem Offline GDPR Clipboard Anonymizer - launcher with hidden console.
cd /d "%~dp0"

where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw app.py
    exit /b
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw app.py
    exit /b
)

where python >nul 2>&1
if %errorlevel%==0 (
    start "" python app.py
    exit /b
)

echo Python was not found. Install it from https://www.python.org
echo (tick "Add python.exe to PATH"), then double-click run.bat again.
pause