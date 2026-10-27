@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (echo Fehler: Python 3.10+ installieren. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (echo Fehler bei der Paketinstallation. & pause & exit /b 1)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
