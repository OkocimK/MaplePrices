@echo off
rem Start trackera cen. Gra ma byc uruchomiona w oknie (nie pelny ekran) w 1920x1080.
rem F9 wlacza/wylacza zbieranie, Ctrl+F9 konczy. Podglad: http://localhost:8778/
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Najpierw uruchom install.bat
  pause
  exit /b 1
)
.venv\Scripts\python tracker.py %*
pause
