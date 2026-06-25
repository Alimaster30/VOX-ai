@echo off
title VOX - Stopping...
echo Stopping VOX...

taskkill /F /IM python.exe /T >NUL 2>&1
echo Flask server stopped.

set /p STOP_OLLAMA="Stop Ollama too? (y/n): "
if /i "%STOP_OLLAMA%"=="y" (
    taskkill /F /IM ollama.exe /T >NUL 2>&1
    echo Ollama stopped.
)

echo Done.
pause
