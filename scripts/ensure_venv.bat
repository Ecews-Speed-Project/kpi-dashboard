@echo off
REM Find (or, on first run on this computer, build) the pipeline's Python environment.
REM Sets PY to its python.exe. The venv lives outside OneDrive so its files don't sync.
set "VENV=%LOCALAPPDATA%\speed_kpi\.venv"
if exist "%VENV%\Scripts\python.exe" goto found
echo First run on this computer: creating %VENV% ...
set "BASE="
where py >nul 2>&1 && set "BASE=py -3"
if not defined BASE where python >nul 2>&1 && set "BASE=python"
if not defined BASE (
  echo Python 3.10+ not found. Install it, e.g.: winget install Python.Python.3.12
  exit /b 1
)
%BASE% -m venv "%VENV%" || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip -q
"%VENV%\Scripts\python.exe" -m pip install -q -r "%~dp0..\requirements.txt" || exit /b 1
:found
set "PY=%VENV%\Scripts\python.exe"
exit /b 0
