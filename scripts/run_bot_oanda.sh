#!/bin/zsh
# Keep-alive wrapper pre Oanda bota (nezávislý od IBKR run_bot.sh).
set -u
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

if [[ -f .env ]]; then
  export $(grep -E '^(OANDA_)?TELEGRAM_(BOT_TOKEN|CHAT_ID)=' .env | xargs) 2>/dev/null
fi

tg_notify() {
  local token="${OANDA_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
  [[ -n "$token" && -n "${TELEGRAM_CHAT_ID:-}" ]] || return 0
  curl -s -o /dev/null --max-time 10 \
    "https://api.telegram.org/bot${token}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" -d text="[OANDA] $1" || true
}

echo "[run_bot_oanda] štart $(date '+%F %T')"
first=1
backoff=15
crashes=0
while true; do
  started=$(date +%s)
  if [[ $first -eq 1 ]]; then
    first=0
    BOT_RESTARTED=0 "$PY" bot_oanda.py "$@"
  else
    BOT_RESTARTED=1 "$PY" bot_oanda.py "$@"
  fi
  code=$?
  if [[ $code -eq 0 || $code -eq 130 ]]; then
    echo "[run_bot_oanda] bot skončil čisto (kód $code), končím."
    break
  fi
  ran=$(( $(date +%s) - started ))
  if [[ $ran -gt 300 ]]; then
    backoff=15
    crashes=0
  fi
  crashes=$((crashes + 1))
  if [[ $crashes -eq 1 || $((crashes % 20)) -eq 0 ]]; then
    tg_notify "♻️ Oanda bot spadol (kód $code, ${crashes}. pád) — reštart o ${backoff}s."
  fi
  echo "[run_bot_oanda] pád (kód $code), reštart o ${backoff}s…"
  sleep "$backoff"
  backoff=$(( backoff * 2 > 300 ? 300 : backoff * 2 ))
done
