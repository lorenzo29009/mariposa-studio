@echo off
REM Windows launcher for Mariposa Studio - mirror of "Mariposa Studio.command".
REM Double-click to run. cd /d "%~dp0" makes the repo root the working dir, so
REM src/studio.py resolves APP_DIR to this folder (tools/, exports/, venv/).
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ====================================================
    echo  Mariposa Studio is not installed yet.
    echo ====================================================
    echo.
    echo  Run  install-windows.bat  first ^(double-click it^).
    echo.
    pause
    exit /b 1
)

REM pythonw.exe runs the GUI without keeping a console window open.
start "" "venv\Scripts\pythonw.exe" "src\studio.py"
