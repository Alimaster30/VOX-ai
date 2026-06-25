@echo off
setlocal
title VOX - Production Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo VOX virtual environment was not found at .venv\Scripts\python.exe
  echo Create/install the environment first, then run this script again.
  exit /b 1
)

echo ============================================================
echo   VOX - Production Server
echo ============================================================
echo.

".venv\Scripts\python.exe" serve.py

endlocal
