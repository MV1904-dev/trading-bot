#!/bin/zsh
# Výmena OAuth autorizačného kódu za live access+refresh token (cTrader).
#
# Použitie:
#   ./scripts/ctrader_live_token.sh
#   → vypýta si kód, vypíše tokeny
#
# POZOR: autorizačný kód je JEDNORAZOVÝ a platí ~minútu. Skript spusti
# hneď po autorizácii. client_id/secret sa berú z .env automaticky.
#
# Autorizačný odkaz (otvor v prehliadači, potom skopíruj časť URL za ?code=):
#   https://openapi.ctrader.com/apps/auth?client_id=<CLIENT_ID>&redirect_uri=https%3A%2F%2Fgoogle.com&scope=trading

set -u
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "CHYBA: .env sa nenašiel."; exit 1; }
CID=$(grep '^CTRADER_CLIENT_ID=' .env | cut -d= -f2)
CSEC=$(grep '^CTRADER_CLIENT_SECRET=' .env | cut -d= -f2)
[[ -n "$CID" && -n "$CSEC" ]] || { echo "CHYBA: chýba CLIENT_ID/SECRET v .env."; exit 1; }

REDIRECT="${1:-https://google.com}"

echo "1) Otvor v prehliadači (ak si tak ešte neurobil):"
echo "   https://openapi.ctrader.com/apps/auth?client_id=${CID}&redirect_uri=https%3A%2F%2Fgoogle.com&scope=trading"
echo "2) Po povolení ťa presmeruje na google.com — skopíruj hodnotu za ?code="
echo
printf "Vlož autorizačný kód: "
read -r CODE
[[ -n "$CODE" ]] || { echo "CHYBA: prázdny kód."; exit 1; }

echo
echo "Vymieňam kód za tokeny (redirect_uri=$REDIRECT)…"
RESP=$(curl -s -G "https://openapi.ctrader.com/apps/token" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=$CODE" \
  --data-urlencode "redirect_uri=$REDIRECT" \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSEC")

echo
if echo "$RESP" | grep -q "accessToken"; then
  echo "✓ ÚSPECH — ulož si tieto tokeny (napr. do PASS):"
  echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
  echo
  echo "POZN.: sú to LIVE tokeny so scope 'trading' (prístup k reálnym peniazom)."
  echo "Do .env ich NEVKLADAJ, pokiaľ nechceš spustiť live obchodovanie."
else
  echo "✗ NEPODARILO SA:"
  echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
  echo
  echo "Najčastejšia príčina: kód medzitým expiroval (platí ~minútu) alebo"
  echo "už bol použitý. Vygeneruj nový cez odkaz v kroku 1 a spusti znova."
fi
