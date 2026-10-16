@echo off
REM Open the live dashboard; it reloads itself after each update
cd /d "%~dp0"
call scripts\ensure_venv.bat || (pause & exit /b 1)
"%PY%" serve_dashboard.py %*
