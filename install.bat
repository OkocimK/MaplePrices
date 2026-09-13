@echo off
rem Jednorazowa instalacja trackera cen: tworzy .venv obok tego pliku i instaluje biblioteki.
rem Wymaga Pythona 3.12 z python.org (przy instalacji zaznaczyc "Add python.exe to PATH").
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Brak Pythona 3.12. Pobierz z https://www.python.org/downloads/ i zaznacz "Add to PATH".
  pause
  exit /b 1
)
if not exist .venv (
  echo Tworze srodowisko .venv...
  py -3.12 -m venv .venv || (echo venv nie powstal & pause & exit /b 1)
)
echo Instaluje biblioteki (chwile potrwa)...
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet -r requirements.txt || (echo instalacja nie przeszla & pause & exit /b 1)
echo.
echo Gotowe. Teraz uruchom run.bat, przy pierwszym starcie poprosi o token i nick.
pause
