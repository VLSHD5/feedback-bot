@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1 && set "PY=py" || set "PY=python"
if not exist "venv\Scripts\python.exe" %PY% -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo .env created. Enter BOT_TOKEN and ADMIN_USERNAMES, then run start.bat again.
  pause
  exit /b 0
)

for /f "usebackq tokens=1,* delims==" %%A in (".env") do if /I "%%A"=="AI_ENABLED" set "AI_ENABLED=%%B"
if /I "%AI_ENABLED%"=="true" goto START_AI
if /I "%AI_ENABLED%"=="1" goto START_AI
goto START_BOT

:START_AI
set "LLAMA_CMD="
where llama >nul 2>&1 && set "LLAMA_CMD=llama"
if not defined LLAMA_CMD where llama-server >nul 2>&1 && set "LLAMA_CMD=llama-server"

if not defined LLAMA_CMD (
  echo llama.cpp was not found.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo Install llama.cpp, then run start.bat again.
    echo Recommended: winget install llama.cpp
    goto START_BOT
  )
  echo Installing llama.cpp with WinGet...
  winget install llama.cpp --accept-source-agreements --accept-package-agreements
  where llama >nul 2>&1 && set "LLAMA_CMD=llama"
  if not defined LLAMA_CMD where llama-server >nul 2>&1 && set "LLAMA_CMD=llama-server"
)

if defined LLAMA_CMD (
  echo Starting Shieldstral: Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M
  if /I "%LLAMA_CMD%"=="llama" (
    start "Shieldstral" /min cmd /c "llama serve -hf Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M --port 9931"
  ) else (
    start "Shieldstral" /min cmd /c "llama-server -hf Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M --port 9931"
  )
  echo Waiting for local AI server...
  powershell -NoProfile -Command "$ok=$false; 1..60 | %% { try { Invoke-WebRequest http://127.0.0.1:9931/v1/models -UseBasicParsing -TimeoutSec 1 ^| Out-Null; $ok=$true; break } catch {}; Start-Sleep -Seconds 1 }; if(-not $ok){ exit 1 }"
  if errorlevel 1 echo Warning: Shieldstral server did not become ready in 60 seconds. The bot will still start.
) else (
  echo Warning: llama.cpp unavailable. Bot will start, but Shieldstral moderation will be waiting.
)

:START_BOT
venv\Scripts\python.exe bot.py
