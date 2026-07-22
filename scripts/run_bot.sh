#!/bin/zsh
# Keep-alive wrapper pre bota: drží ho nažive, pri páde ho reštartne
# a pošle Telegram správu o reštarte.
#
# Spustenie:   ./scripts/run_bot.sh
# Zastavenie:  Ctrl-C (alebo kill procesu; deti sa ukončia tiež)
#
# Pre automatický štart po prihlásení použi launchd šablónu
# scripts/com.mv.trading-bot.plist (návod v jej hlavičke).

set -u
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

# Telegram kredenciály z .env (na správu o reštarte)
if [[ -f .env ]]; then
  export $(grep -E '^TELEGRAM_(BOT_TOKEN|CHAT_ID)=' .env | xargs) 2>/dev/null
fi

tg_notify() {
  [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]] || return 0
  curl -s -o /dev/null --max-time 10 \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" -d text="$1" || true
}

echo "[run_bot] štart $(date '+%F %T')"
first=1
backoff=15
crashes=0
while true; do
  started=$(date +%s)
  if [[ $first -eq 1 ]]; then
    first=0
    BOT_RESTARTED=0 "$PY" bot.py "$@"
  else
    BOT_RESTARTED=1 "$PY" bot.py "$@"
  fi
  code=$?
  # čisté ukončenie (0 = --run-minutes / plánovaný koniec, 130 = Ctrl-C)
  if [[ $code -eq 0 || $code -eq 130 ]]; then
    echo "[run_bot] bot skončil čisto (kód $code), končím."
    break
  fi
  ran=$(( $(date +%s) - started ))
  # bežal dlhšie než 5 min -> nová séria pádov, backoff od začiatku
  if [[ $ran -gt 300 ]]; then
    backoff=15
    crashes=0
  fi
  crashes=$((crashes + 1))
  # TG len pri prvom páde série a potom každom 20. (žiadny spam)
  if [[ $crashes -eq 1 || $((crashes % 20)) -eq 0 ]]; then
    tg_notify "♻️ Bot spadol (kód $code, ${crashes}. pád) — reštart o ${backoff}s."
  fi
  echo "[run_bot] bot spadol (kód $code), reštart o ${backoff} s…"
  sleep "$backoff"
  backoff=$(( backoff * 2 > 300 ? 300 : backoff * 2 ))
done
