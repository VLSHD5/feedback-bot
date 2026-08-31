#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

if [ ! -x venv/bin/python ]; then "$PYTHON" -m venv venv; fi
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env created. Enter BOT_TOKEN and ADMIN_USERNAMES, then run ./start.sh again."
  exit 0
fi

AI_ENABLED_VALUE="$(grep -E '^AI_ENABLED=' .env | tail -n1 | cut -d= -f2- | tr '[:upper:]' '[:lower:]')"
if [ "$AI_ENABLED_VALUE" = "true" ] || [ "$AI_ENABLED_VALUE" = "1" ]; then
  if ! command -v llama >/dev/null 2>&1 && ! command -v llama-server >/dev/null 2>&1; then
    if command -v curl >/dev/null 2>&1; then
      echo "llama.cpp not found; installing the official llama CLI..."
      curl -LsSf https://llama.app/install.sh | sh || true
      export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
    fi
  fi

  if command -v llama >/dev/null 2>&1; then
    echo "Starting Shieldstral: Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M"
    nohup llama serve -hf Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M --port 9931 > shieldstral.log 2>&1 &
  elif command -v llama-server >/dev/null 2>&1; then
    echo "Starting Shieldstral: Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M"
    nohup llama-server -hf Metabaron6/Shieldstral-1.0-3B-GGUF:Q4_K_M --port 9931 > shieldstral.log 2>&1 &
  else
    echo "Warning: llama.cpp unavailable. Bot will start, but Shieldstral moderation will be waiting."
  fi
fi

exec venv/bin/python bot.py
