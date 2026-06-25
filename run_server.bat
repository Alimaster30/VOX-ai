@echo off
title VOX - Voice Assistant
cd /d "%~dp0"

echo ============================================================
echo   VOX - Voice Assistant
echo ============================================================
echo.

:: ── Step 1: Start Ollama in background ──────────────────────
echo [1/3] Starting Ollama LLM server...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if %ERRORLEVEL% == 0 (
    echo       Ollama already running. Skipping.
) else (
    start /B "" ollama serve >NUL 2>&1
    echo       Ollama started.
    timeout /t 3 /nobreak >NUL
)

:: ── Step 2: Pull the model if not already downloaded ────────
echo [2/3] Checking Ollama model (qwen3.2:3b)...
ollama list 2>NUL | find "qwen3.2:3b" >NUL
if %ERRORLEVEL% == 0 (
    echo       Model already available. Skipping.
) else (
    echo       Pulling qwen3.2:3b - this may take a few minutes...
    ollama pull qwen3.2:3b
)

:: ── Step 3: Start development Flask app ──────────────────────
echo [3/3] Starting VOX development server...
echo.
echo       The web app starts first; models warm up in the background.
echo       For production, use run_production.bat instead.
echo       Open your browser and go to: http://localhost:5000
echo.
echo ============================================================
echo.

".venv\Scripts\python.exe" app.py

pause
