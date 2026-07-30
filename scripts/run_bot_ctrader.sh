#!/bin/zsh
# Keep-alive wrapper pre cTrader bota (nezávislý od IBKR/Oanda wrapperov).
#
# ⛔ PRODUKCIA BEŽÍ NA HETZNERI, NIE TU. Od 30. 7. 2026 hostí bota
# systemd unit ctrader-bot.service na 62.238.48.134 (viď scripts/ctrader-bot.service).
# Tento wrapper je zamknutý, lebo dve inštancie proti tomu istému demo účtu
# 48026061 by si rozsypali mriežku a navzájom si zneplatnili cTrader refresh
# tokeny (sú jednorazové a bot ich prepisuje do .env).
set -u
cd "$(dirname "$0")/.."

if [[ "${ALLOW_LOCAL_BOT:-0}" != "1" ]]; then
  cat >&2 <<'LOCK'
⛔ Lokálny beh cTrader bota je zamknutý.

Bot beží na serveri pod systemd:
    ssh hetzner 'systemctl status ctrader-bot'
    ssh hetzner 'journalctl -u ctrader-bot -f'

Ak naozaj chceš bežať lokálne, NAJPRV zastav server, inak budú
obchodovať dve inštancie ten istý účet:
    ssh hetzner 'systemctl stop ctrader-bot'
    ALLOW_LOCAL_BOT=1 ./scripts/run_bot_ctrader.sh
LOCK
  exit 1
fi

if command -v ssh >/dev/null 2>&1; then
  if ssh -o BatchMode=yes -o ConnectTimeout=5 hetzner \
         'systemctl is-active --quiet ctrader-bot' 2>/dev/null; then
    echo "⛔ ctrader-bot BEŽÍ na serveri — odmietam štart druhej inštancie." >&2
    echo "   Zastav ho: ssh hetzner 'systemctl stop ctrader-bot'" >&2
    exit 1
  fi
fi

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

# macOS uspáva stroj (na batérii už po 1 min nečinnosti) → TCP spojenie na
# Spotware zomrie a bot to vidí ako "stream mŕtvy". caffeinate drží idle-sleep
# assertion presne po dobu behu bota (-w PID), takže sa nemení nič globálne.
# POZOR: -s (system sleep) platí len na napájaní zo siete; na batérii ani
# caffeinate nezabráni spánku po zavretí veka.
CAFF=()
if command -v caffeinate >/dev/null 2>&1; then
  CAFF=(caffeinate -dims)   # pole — zsh nerobí word-splitting na "$CAFF"
fi

if [[ -f .env ]]; then
  export $(grep -E '^(CTRADER_)?TELEGRAM_(BOT_TOKEN|CHAT_ID)=' .env | xargs) 2>/dev/null
fi

tg_notify() {
  local token="${CTRADER_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
  [[ -n "$token" && -n "${TELEGRAM_CHAT_ID:-}" ]] || return 0
  curl -s -o /dev/null --max-time 10 \
    "https://api.telegram.org/bot${token}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" -d text="[CTRADER] $1" || true
}

echo "[run_bot_ctrader] štart $(date '+%F %T')"
first=1
backoff=15
crashes=0
while true; do
  started=$(date +%s)
  if [[ $first -eq 1 ]]; then
    first=0
    BOT_RESTARTED=0 "${CAFF[@]}" "$PY" bot_ctrader.py "$@"
  else
    BOT_RESTARTED=1 "${CAFF[@]}" "$PY" bot_ctrader.py "$@"
  fi
  code=$?
  if [[ $code -eq 0 || $code -eq 130 ]]; then
    echo "[run_bot_ctrader] bot skončil čisto (kód $code), končím."
    break
  fi
  ran=$(( $(date +%s) - started ))
  if [[ $ran -gt 300 ]]; then
    backoff=15
    crashes=0
  fi
  crashes=$((crashes + 1))
  if [[ $crashes -eq 1 || $((crashes % 20)) -eq 0 ]]; then
    tg_notify "♻️ cTrader bot spadol (kód $code, ${crashes}. pád) — reštart o ${backoff}s."
  fi
  echo "[run_bot_ctrader] pád (kód $code), reštart o ${backoff}s…"
  sleep "$backoff"
  backoff=$(( backoff * 2 > 300 ? 300 : backoff * 2 ))
done
