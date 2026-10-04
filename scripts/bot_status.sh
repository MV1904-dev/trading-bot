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

say "SWAPY A KROKY GRIDU (posledné, čo bot videl)"
# Číta sa z DB, nie od brokera — bežiaceho bota to nijako neruší a Spotware
# aj tak drží len jedno app-auth spojenie naraz.
DB="data/bot_ctrader_live.db"
grep -q "^CTRADER_DEMO=1" .env 2>/dev/null && DB="data/bot_ctrader.db"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
"$PY" - "$DB" <<'PYEOF' || echo "(DB sa nepodarilo prečítať)"
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
meta = dict(con.execute("SELECT key, value FROM meta"))
print(f"DB: {sys.argv[1]}")

# _check_swap_drift ukladá "long|short", _apply_swap_regime "short|long".
raw = meta.get("swap_rates", "")
if raw:
    sl, ss = raw.split("|")
    print(f"swap long : {float(sl):+.4f}   (záporné = platíš)")
    print(f"swap short: {float(ss):+.4f}")
else:
    print("swap_rates: zatiaľ nezaznamenané")

steps = meta.get("grid_steps", "")
if steps:
    ks, kl = (float(x) for x in steps.split("|"))
    fmt = lambda v: f"{v:.3%}" if v else "VYPNUTÁ STRANA"
    print(f"krok short: {fmt(ks)}")
    print(f"krok long : {fmt(kl)}")
    print("(prahy: ≤0,25 €/deň → 0,10 % | ≤0,50 → 0,13 % | "
          "≤1,00 → 0,15 % | nad 1 € strana VYP)")
else:
    print("grid_steps: automatika zatiaľ nezasiahla, platia lab konštanty")
PYEOF

if [ -z "$(journalctl -u ctrader-bot -n 1 --no-pager 2>/dev/null | grep -v '^-- ')" ]; then
  say "LOG"
  echo "journal je prázdny alebo nečitateľný pod týmto používateľom"
  echo "(skús ako root: ssh hetzner '$0')."
  exit 0
fi

say "SPOJENIE S BROKEROM"
# Pozor: „služba beží" neznamená „obchoduje". Proces môže žiť a ďalej
# zrkadliť do Supabase, kým je auth reťazec rozbitý (4. 10. 2026: zlyhala
# obnova access tokenu a bot bol dva dni slepý, pritom status hlásil OK).
# Verdikt sa preto skladá z toho, čo je v logu NOVŠIE: úspešné pripojenie,
# alebo zlyhanie auth.
AUTH_BAD="Token refresh zlyhal|cTrader auth zlyhal|App auth zamietnutý|Account auth zamietnutý|Auth reťazec zopakujem"
jt() { journalctl -u ctrader-bot --no-pager -o short-unix 2>/dev/null \
         | grep -E "$1" | tail -1 | cut -d. -f1; }
ok_ts=$(jt "cTrader pripojený|LIVE pripojený")
bad_ts=$(jt "$AUTH_BAD")
now_ts=$(date +%s)
age() {
  [ -n "${1:-}" ] || { echo "nikdy"; return; }
  m=$(( (now_ts - $1) / 60 ))
  [ "$m" -lt 90 ] && echo "pred $m min" || echo "pred $((m / 60)) h"
}
if [ -n "${ok_ts:-}" ] && { [ -z "${bad_ts:-}" ] || [ "$ok_ts" -gt "$bad_ts" ]; }; then
  echo "✅ pripojený ($(age "$ok_ts"))"
else
  echo "⛔ NEPRIPOJENÝ — proces žije, ale k brokerovi sa nedostal."
  echo "   posledné pripojenie:    $(age "${ok_ts:-}")"
  echo "   posledné zlyhanie auth: $(age "${bad_ts:-}")"
  echo "   nové vstupy stoja; TP otvorených pozícií bežia na serveri brokera."
  journalctl -u ctrader-bot --no-pager 2>/dev/null \
    | grep -E "$AUTH_BAD" | tail -3
  echo "   → ak ide o token, pozri docs/prevadzka.md, časť „Ak vypršali tokeny\"."
fi
journalctl -u ctrader-bot --no-pager | grep -E "Obnova stavu" | tail -1

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

say "CHYBY A VAROVANIA za 24 h (posledných 10)"
# Len -p err nestačí: opakovanie auth reťazca a neúspešný zápis tokenov
# logujú WARNING, takže rozbitý bot vyzeral ako „žiadne chyby".
journalctl -u ctrader-bot --since "-24h" --no-pager -p warning \
  | grep -v "^-- " | tail -10 || echo "(žiadne)"
echo
