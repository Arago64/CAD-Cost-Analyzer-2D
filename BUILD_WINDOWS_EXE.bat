@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python for Windows not found. Install Python 3.11+ with the py launcher.
  pause
  exit /b 1
)
if not exist ".build_venv\Scripts\python.exe" (
  py -3 -m venv .build_venv
  if errorlevel 1 goto :failed
)
".build_venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".build_venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed
if exist "_exe_payload" rmdir /s /q "_exe_payload"
mkdir "_exe_payload"
for %%F in (*.py) do (
  if /I not "%%F"=="desktop_launcher.py" if /I not "%%F"=="silent_setup.py" copy /y "%%F" "_exe_payload\%%F" >nul
)
xcopy /e /i /q "examples" "_exe_payload\examples" >nul
".build_venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --onedir --windowed --name CADCostAnalyzer2D --collect-all streamlit --collect-all ezdxf --collect-all shapely --collect-all pyarrow --add-data "_exe_payload;app" desktop_launcher.py
if errorlevel 1 goto :failed
echo.
echo Built: dist\CADCostAnalyzer2D\CADCostAnalyzer2D.exe
echo Copy the ENTIRE dist\CADCostAnalyzer2D folder. Do not copy EXE alone.
if "%CI%"=="" pause
exit /b 0
:failed
echo Build failed. See the output above and check Python dependencies.
if "%CI%"=="" pause
exit /b 1
