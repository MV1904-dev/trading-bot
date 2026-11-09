#!/bin/bash
# Jednorazový OAuth grant pre LIVE účet: vymení autorizačný kód za tokeny
# a uloží ich do .env ako CTRADER_LIVE_* — teda MIMO kľúčov, ktoré si
# bežiaci bot prepisuje pri vlastnom token refreshi (broker_ctrader ~:176).
# Prepnutie na live ich odtiaľ preberie až so zastaveným botom.
#
# Spustenie z Macu (interaktívne, skript sa spýta na kód):
#   ssh -t hetzner 'bash /home/marian/trading-bot/scripts/ctrader_live_grant.sh'
#
# Bez terminálu (automatizácia, agent, CI) sa kód odovzdá ako argument:
#   ssh hetzner 'bash /home/marian/trading-bot/scripts/ctrader_live_grant.sh <KÓD>'
# Autorizačný kód je jednorazový a platí ~minútu, takže po výmene je mŕtvy —
# v histórii shellu po ňom neostane nič použiteľné. Tokeny, ktoré z neho
# vzniknú, sa vypisujú len do .env, nikdy na výstup.
set -eu
cd /home/marian/trading-bot

CID=$(grep '^CTRADER_CLIENT_ID=' .env | cut -d= -f2)
CSEC=$(grep '^CTRADER_CLIENT_SECRET=' .env | cut -d= -f2)
[ -n "$CID" ] && [ -n "$CSEC" ] || { echo "CHYBA: chýba CLIENT_ID/SECRET v .env."; exit 1; }

# Bežiaci bot si .env prepisuje sám, takže ctrader_live_promote.sh sa
# odmietne spustiť — lenže to je AŽ PO tom, čo si vymenil jednorazový kód
# z prehliadača. Grant tým nezhorí (tokeny sa uložia), ale človek si myslí,
# že je hotovo, a overenie mu potom hlási starý mŕtvy token: presne to sa
# stalo 9. 11. 2026 a stálo to dve kolá. Preto sa pýtame hneď na začiatku.
if command -v systemctl >/dev/null 2>&1 \
   && systemctl is-active --quiet ctrader-bot 2>/dev/null; then
  echo "⛔ ctrader-bot BEŽÍ — najprv 'systemctl stop ctrader-bot'." >&2
  echo "   Inak sa prepnutie tokenov po grante odmietne a kód" >&2
  echo "   z prehliadača (platí ~minútu) medzitým prepadne." >&2
  exit 1
fi

CODE="${1:-}"
if [ -z "$CODE" ]; then
  echo "1) Otvor v prehliadači:"
  echo "   https://openapi.ctrader.com/apps/auth?client_id=${CID}&redirect_uri=https%3A%2F%2Fgoogle.com&scope=trading"
  echo "   Prihlás sa svojím cTrader ID a POVOĽ AJ LIVE účet 2079276 (Account #1)."
  echo "2) Po presmerovaní na google.com skopíruj z adresného riadka hodnotu za ?code="
  echo "   (kód platí ~minútu a je jednorazový)"
  echo
  if [ -t 0 ]; then
    printf "Vlož kód: "
    read -r CODE
  else
    # Sem spadne každé volanie bez pridelenej pseudo-konzoly (ssh bez -t,
    # agent, CI). Predtým to tu umieralo na prázdnom `read` s hláškou
    # o stdin, ktorá nepovedala, čo s tým.
    echo "Stdin nie je terminál, takže sa kód nemám koho spýtať." >&2
    echo "Odovzdaj ho ako argument:" >&2
    echo "   ssh hetzner 'bash $0 <KÓD>'" >&2
    exit 1
  fi
fi
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
