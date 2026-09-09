#!/bin/bash
# Stav bota na jeden pohľad — nahrádza sadu dlhých journalctl príkazov,
# ktoré sa inak musia ručne kopírovať do terminálu.
#
#   ssh hetzner '/home/marian/trading-bot/scripts/bot_status.sh'
set -u
cd "$(dirname "$0")/.."

hr() { printf '%s\n' "────────────────────────────────────────────"; }
say() { printf '\n%s\n' "$1"; hr; }

say "SLUŽBA"
if systemctl is-active --quiet ctrader-bot 2>/dev/null; then
  echo "beží (od $(systemctl show -p ActiveEnterTimestamp --value ctrader-bot 2>/dev/null))"
  echo "PID: $(systemctl show -p MainPID --value ctrader-bot 2>/dev/null)"
else
  echo "⛔ NEBEŽÍ (alebo systemd nie je dostupný)"
fi

say "KÓD"
echo "vetva:  $(git rev-parse --abbrev-ref HEAD)"
echo "commit: $(git log -1 --pretty='%h %s')"
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️  pracovný strom má lokálne zmeny"
fi

if [ -z "$(journalctl -u ctrader-bot -n 1 --no-pager 2>/dev/null | grep -v '^-- ')" ]; then
  say "LOG"
  echo "journal je prázdny alebo nečitateľný pod týmto používateľom"
  echo "(skús ako root: ssh hetzner '$0')."
  exit 0
fi

say "SPOJENIE (posledné)"
journalctl -u ctrader-bot --no-pager | grep -E "cTrader pripojený|LIVE pripojený|Obnova stavu" \
  | tail -3 || echo "(nič)"

say "GRID KROKY (swapová automatika)"
journalctl -u ctrader-bot --since "-24h" --no-pager | grep "grid kroky" | tail -2 \
  || echo "(za 24 h nič — kroky sa menia len pri zmene nákladu držania)"

say "ZRKADLO DO SUPABASE za 24 h"
snap=$(journalctl -u ctrader-bot --since "-24h" --no-pager | grep -c "snapshot zlyhal")
pg=$(journalctl -u ctrader-bot --since "-24h" --no-pager | grep -c "PGRST")
echo "zlyhaní prípravy snapshotu: $snap"
echo "odmietnutí Supabase (PGRST): $pg"
[ "$snap" = "0" ] && [ "$pg" = "0" ] && echo "✅ zrkadlo v poriadku" \
  || echo "⚠️  dashboard môže ukazovať starý stav"

say "CHYBY za 24 h (posledných 10)"
journalctl -u ctrader-bot --since "-24h" --no-pager -p err \
  | grep -v "^-- " | tail -10 || echo "(žiadne)"
echo
