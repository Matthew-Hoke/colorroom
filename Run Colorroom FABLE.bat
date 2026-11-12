@echo off
rem Double-click this file to start Colorroom FABLE from its source code.
rem You can also drag an image onto it to open that image.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The Python environment for Colorroom FABLE is missing.
    echo Open README.md and follow "Install".
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m colorroom %*
