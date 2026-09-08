#!/bin/bash
# Prepne tokeny z grantu (CTRADER_LIVE_*) na kľúče, ktoré bot naozaj číta
# (CTRADER_ACCESS_TOKEN / CTRADER_REFRESH_TOKEN).
#
# Prečo samostatný krok: ctrader_live_grant.sh ukladá čerstvé tokeny bokom,
# lebo bežiaci bot si CTRADER_ACCESS_TOKEN/REFRESH_TOKEN pri každom refreshi
# prepisuje sám (Spotware refresh tokeny sú jednorazové). Prepnúť sa preto
# smie len so ZASTAVENÝM botom, inak si obe strany prepíšu .env navzájom
# a skončí to presne tam, kde sme boli: CH_ACCESS_TOKEN_INVALID.
#
# Použitie (na serveri, ako marian):
#   systemctl stop ctrader-bot
#   ./scripts/ctrader_live_promote.sh
#   ./.venv/bin/python scripts/ctrader_auth_check.py
#   systemctl start ctrader-bot
set -eu
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "CHYBA: .env sa nenašiel."; exit 1; }

if command -v systemctl >/dev/null 2>&1 \
   && systemctl is-active --quiet ctrader-bot 2>/dev/null; then
  echo "⛔ ctrader-bot BEŽÍ — najprv 'systemctl stop ctrader-bot'." >&2
  echo "   Bežiaci bot si .env prepisuje a prepnutie by sa stratilo." >&2
  exit 1
fi

AT=$(grep '^CTRADER_LIVE_ACCESS_TOKEN=' .env | cut -d= -f2- || true)
RT=$(grep '^CTRADER_LIVE_REFRESH_TOKEN=' .env | cut -d= -f2- || true)
if [ -z "${AT:-}" ] || [ -z "${RT:-}" ]; then
  echo "CHYBA: v .env nie sú CTRADER_LIVE_ACCESS_TOKEN / _REFRESH_TOKEN." >&2
  echo "       Najprv spusti ./scripts/ctrader_live_grant.sh" >&2
  exit 1
fi

BAK=".env.bak.$(date +%s)"
cp -p .env "$BAK"

# Staré kľúče von a nové na koniec — duplicitný riadok by rozhodoval podľa
# poradia (python-dotenv berie posledný), a na to sa nechceme spoliehať.
grep -v '^CTRADER_ACCESS_TOKEN=' .env | grep -v '^CTRADER_REFRESH_TOKEN=' > .env.tmp
printf 'CTRADER_ACCESS_TOKEN=%s\nCTRADER_REFRESH_TOKEN=%s\n' "$AT" "$RT" >> .env.tmp
chown --reference=.env .env.tmp 2>/dev/null || true
chmod --reference=.env .env.tmp 2>/dev/null || true
mv .env.tmp .env

echo "✓ Tokeny prepnuté (záloha: $BAK)."
echo "  Over ich: ./.venv/bin/python scripts/ctrader_auth_check.py"
echo "  Potom:    systemctl start ctrader-bot"
