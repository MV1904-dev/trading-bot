#!/bin/bash
# Jednorazový OAuth grant pre LIVE účet: vymení autorizačný kód za tokeny
# a uloží ich do .env ako CTRADER_LIVE_* — teda MIMO kľúčov, ktoré si
# bežiaci bot prepisuje pri vlastnom token refreshi (broker_ctrader ~:176).
# Prepnutie na live ich odtiaľ preberie až so zastaveným botom.
#
# Spustenie z Macu:  ssh -t hetzner 'bash /home/marian/trading-bot/scripts/ctrader_live_grant.sh'
set -eu
cd /home/marian/trading-bot

CID=$(grep '^CTRADER_CLIENT_ID=' .env | cut -d= -f2)
CSEC=$(grep '^CTRADER_CLIENT_SECRET=' .env | cut -d= -f2)
[ -n "$CID" ] && [ -n "$CSEC" ] || { echo "CHYBA: chýba CLIENT_ID/SECRET v .env."; exit 1; }

echo "1) Otvor v prehliadači:"
echo "   https://openapi.ctrader.com/apps/auth?client_id=${CID}&redirect_uri=https%3A%2F%2Fgoogle.com&scope=trading"
echo "   Prihlás sa svojím cTrader ID a POVOĽ AJ LIVE účet 2079276 (Account #1)."
echo "2) Po presmerovaní na google.com skopíruj z adresného riadka hodnotu za ?code="
echo "   (kód platí ~minútu a je jednorazový)"
echo
printf "Vlož kód: "
read -r CODE
[ -n "$CODE" ] || { echo "CHYBA: prázdny kód."; exit 1; }

RESP=$(curl -s -G "https://openapi.ctrader.com/apps/token" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=$CODE" \
  --data-urlencode "redirect_uri=https://google.com" \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSEC")

AT=$(printf '%s' "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('accessToken',''))" 2>/dev/null || true)
RT=$(printf '%s' "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('refreshToken',''))" 2>/dev/null || true)

if [ -z "$AT" ] || [ -z "$RT" ]; then
  echo "✗ Výmena zlyhala:"
  printf '%s\n' "$RESP" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$RESP"
  echo "Najčastejšie: kód expiroval alebo už bol použitý — vygeneruj nový a spusti znova."
  exit 1
fi

cp -p .env ".env.bak.$(date +%s)"
grep -v '^CTRADER_LIVE_ACCESS_TOKEN=' .env | grep -v '^CTRADER_LIVE_REFRESH_TOKEN=' > .env.tmp
printf 'CTRADER_LIVE_ACCESS_TOKEN=%s\nCTRADER_LIVE_REFRESH_TOKEN=%s\n' "$AT" "$RT" >> .env.tmp
chown --reference=.env .env.tmp 2>/dev/null || true
chmod --reference=.env .env.tmp 2>/dev/null || true
mv .env.tmp .env

echo "✓ Live tokeny uložené do .env ako CTRADER_LIVE_* (bot ich zatiaľ nečíta)."
echo "  Teraz povedz Claudovi, že grant je hotový — overí účty a prepne bota."
