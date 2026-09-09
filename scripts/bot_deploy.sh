#!/bin/bash
# Nasadenie: stiahne kód, reštartuje bota a overí, že sa pripojil.
#
#   ssh hetzner '/home/marian/trading-bot/scripts/bot_deploy.sh'          # aktuálna vetva
#   ssh hetzner '/home/marian/trading-bot/scripts/bot_deploy.sh main'     # prepne na main
#
# Reštartuje ŽIVÉHO bota na live účte, preto vypisuje, čo mení, a na konci
# ukáže, či sa spojenie naozaj nadviazalo. Otvorené pozície to neohrozuje —
# TP-čka žijú na serveri brokera a stav sa po štarte obnoví z DB.
set -eu
cd "$(dirname "$0")/.."

TARGET="${1:-}"
OWNER=$(stat -c '%U' .git)
asowner() { if [ "$(id -un)" = "$OWNER" ]; then "$@"; else sudo -u "$OWNER" "$@"; fi; }

echo "vetva pred:  $(git rev-parse --abbrev-ref HEAD)  $(git log -1 --pretty=%h)"

asowner git fetch origin --quiet
if [ -n "$TARGET" ]; then
  asowner git checkout "$TARGET"
fi
BRANCH=$(git rev-parse --abbrev-ref HEAD)
asowner git pull --ff-only origin "$BRANCH"

echo "vetva po:    $BRANCH  $(git log -1 --pretty='%h %s')"

echo
echo "reštartujem ctrader-bot…"
systemctl restart ctrader-bot

# Štart trvá pár sekúnd: app auth, account auth, symbol, warmup barov.
for _ in $(seq 1 20); do
  sleep 1
  if journalctl -u ctrader-bot --since "-1 min" --no-pager \
       | grep -q "cTrader pripojený"; then
    break
  fi
done

echo
journalctl -u ctrader-bot --since "-2 min" --no-pager \
  | grep -E "pripojený|balance|Obnova stavu|ERROR" | tail -8 \
  || echo "(zatiaľ nič — pozri 'bot_status.sh' o chvíľu)"

echo
systemctl is-active --quiet ctrader-bot \
  && echo "✅ služba beží" || echo "⛔ služba NEBEŽÍ — pozri journalctl -u ctrader-bot"
