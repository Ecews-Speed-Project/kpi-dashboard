@echo off
REM Pull DHIS2 + central repository, apply the formulas, rebuild live\index.html
cd /d "%~dp0"
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set "STAMP=%%i"
call scripts\ensure_venv.bat > "logs\run_%STAMP%.log" 2>&1 || (
  echo Python environment missing - see logs\run_%STAMP%.log
  exit /b 1
)
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
"%PY%" run_pipeline.py run --publish %* >> "logs\run_%STAMP%.log" 2>&1
if errorlevel 1 (
  echo Update FAILED - see logs\run_%STAMP%.log
  exit /b 1
)
echo Dashboard updated: live\index.html
