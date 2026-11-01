@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: setting up MusicMatch...
    python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
rem Install requirements on first run and whenever requirements.txt changes (after a git pull).
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 || (
    echo Installing MusicMatch's requirements...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)
if not exist "MusicMatch.lnk" powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%~dp0MusicMatch.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='-m musicmatch.gui'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\MusicMatch.ico,0'; $s.Description='MusicMatch'; $s.Save()" >nul 2>&1
start "" ".venv\Scripts\pythonw.exe" -m musicmatch.gui
