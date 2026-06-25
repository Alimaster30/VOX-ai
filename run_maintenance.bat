@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo VOX virtual environment was not found at .venv\Scripts\python.exe
  exit /b 1
)

".venv\Scripts\python.exe" maintenance.py %*

endlocal
