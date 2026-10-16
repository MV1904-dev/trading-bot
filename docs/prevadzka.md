# Prevádzka a nasadenie

Čo kde beží, ako sa to nasadzuje a čo robiť, keď to prestane chodiť.

> Tento dokument popisuje **cTrader bota** a jeho dashboard — teda to, čo dnes
> reálne obchoduje. `README.md` v koreni popisuje starší XTB prototyp
> (`test_connection.py`, `test_trade.py`, `strategy.py`, `backtest.py`), ktorý
> už v prevádzke nie je.

## Čo kde beží

| Časť | Kde | Ako sa spúšťa |
|---|---|---|
| **Bot** (`bot_ctrader.py`) | server Hetzner, `62.238.48.134`, používateľ `marian`, priečinok `/home/marian/trading-bot` | systemd služba `ctrader-bot` (viď `scripts/ctrader-bot.service`) |
| **Virtuálny bot Strážca** (`paper_bot.py`) | ten istý server | systemd služba `ctrader-paper` (viď `scripts/ctrader-paper.service`); k brokerovi sa nepripája, ceny číta z `data/feed.json` od živého bota |
| **Daily Plan** | ten istý server | `ctrader-plan.timer`, denne 21:35 UTC (Ne–Št); beží ďalej, ale v dashboarde sa už nezobrazuje — plány končia v Supabase a v Telegrame |
| **Dashboard** (`dashboard/`) | Vercel, projekt `trading-bot-dashboard` | automaticky pri každom pushi do `main` |
| **Databáza pre dashboard** | Supabase | — |
| **Notifikácie a príkazy** | Telegram | bot posiela sám; príkazy `/stav`, `/vstup`, `/pauza` |

Bot píše do vlastnej SQLite (`data/bot_ctrader_live.db` pre live,
`data/bot_ctrader.db` pre demo) a **zrkadlí** stav do Supabase. Dashboard číta
výhradne zo Supabase — k brokerovi sa nedostane. Jediná cesta späť k botovi je
tabuľka `commands` (pauza, zatvorenie pozície), ktorú bot sám vyzobáva.

## Nasadenie bota

```bash
ssh hetzner '/home/marian/trading-bot/scripts/bot_deploy.sh main'
```

Stiahne kód, reštartuje službu a počká, kým sa v logu objaví `cTrader pripojený`.
Otvorené pozície to neohrozí — TP-čka žijú na serveri brokera a stav sa po
štarte obnoví z DB.

Stav kedykoľvek:

```bash
ssh hetzner '/home/marian/trading-bot/scripts/bot_status.sh'
```

Vypíše službu, nasadenú vetvu, **či je bot naozaj pripojený k brokerovi**,
swapy a kroky gridu, zlyhania zrkadla do Supabase a chyby aj varovania za 24 h.

> „Služba beží" neznamená „obchoduje". Proces môže žiť a ďalej zrkadliť do
> Supabase, kým je auth reťazec rozbitý — 4. 10. 2026 bol bot takto dva dni
> slepý. Preto má skript samostatný verdikt **SPOJENIE S BROKEROM**: skladá
> sa z toho, čo je v logu novšie — úspešné pripojenie, alebo zlyhanie auth.
> V dashboarde to isté hovorí `broker_connected` v hlavičke.

Praktické aliasy na Mac (`~/.zshrc`):

```bash
alias botstav="ssh hetzner '/home/marian/trading-bot/scripts/bot_status.sh'"
alias botnasad="ssh -t hetzner '/home/marian/trading-bot/scripts/bot_deploy.sh'"
```

## Nasadenie dashboardu

Vercel projekt **`trading-bot-dashboard`** je napojený na GitHub repozitár
`MV1904-dev/trading-bot`. **Každý push do `main` nasadí novú verziu sám** —
netreba spúšťať nič.

Dve nastavenia, bez ktorých to nefunguje:

- **Settings → Build and Deployment → Root Directory = `dashboard`**
  Appka nie je v koreni repozitára. Bez tohto Vercel nenájde `package.json`
  a build zlyhá.
- **Settings → Git → Connected Git Repository** musí ukazovať na
  `MV1904-dev/trading-bot`. Ak sa repozitár v zozname nedá nájsť, chýba
  povolenie na strane GitHubu: <https://github.com/settings/installations> →
  **Vercel → Configure → Repository access** → pridať `trading-bot`.

Premenné prostredia nastavuje Vercel (**Settings → Environments**), nie repozitár:

```
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY
NEXT_PUBLIC_OWNER_EMAIL
```

Anon kľúč je verejný zámerne — prístup k dátam drží RLS na strane Supabase.
Service key do dashboardu **nepatrí**.

Lokálne spustenie (na vlastnom počítači, s `dashboard/.env.local`):

```bash
cd dashboard && npm install && npm run build && npm run start   # http://localhost:3000
```

## Prístupy a tajomstvá

`.env` na serveri (vzor je v `.env.example`):

| Premenná | Na čo |
|---|---|
| `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` | aplikácia v Open API portáli |
| `CTRADER_ACCESS_TOKEN`, `CTRADER_REFRESH_TOKEN` | prístup k účtu; bot si ich sám obnovuje a prepisuje |
| `CTRADER_ACCOUNT_ID` | `48184979` = live účet (login 2079276) |
| `CTRADER_DEMO` | `0` = live, `1` = demo (vlastná DB aj Telegram prefix) |
| `CTRADER_QTY` | objem na vstup v jednotkách |
| `CTRADER_BAND_LOW` | pod touto cenou sa **neotvárajú shorty** (default 1,1250) |
| `CTRADER_BAND_HIGH` | nad touto cenou sa **neotvárajú longy** (default 1,1600) |
| `CTRADER_SWAP_AUTO` | `0` vypne automatiku krokov podľa swapov |
| `CTRADER_GRID_ANCHOR` | kotva gridu: `extreme` (default, bežiace max/min — takto live obchoduje) alebo `entry` (#16, posledný vstup) |
| `PAPER_CAPITAL` | štartovací kapitál virtuálneho účtu (default 15 200 €) — platí len pri prvom štarte, potom je v `data/paper/paper.db` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | notifikácie a príkazy |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | zrkadlenie do dashboardu |
| `ANTHROPIC_API_KEY` | nepovinné; bez neho len nebežia AI posudky |

## Keď prestane obchodovať

**1. Zisti stav:**

```bash
ssh hetzner '/home/marian/trading-bot/scripts/bot_status.sh'
```

**2. Ak sa nevie pripojiť k brokerovi:**

```bash
ssh -t hetzner 'cd /home/marian/trading-bot && sudo -u marian ./.venv/bin/python scripts/ctrader_auth_check.py'
```

Skúsi app auth proti demo aj live endpointu a vypíše účty, ktoré má token
povolené. Rozlíši tým problém aplikácie, endpointu a tokenu.

**3. Ak support pýta logy** (UTC časy, IP, `clientMsgId`):

```bash
ssh -t hetzner 'cd /home/marian/trading-bot && sudo -u marian ./.venv/bin/python scripts/ctrader_authlog.py'
```

**4. Ak vypršali tokeny** (`CH_ACCESS_TOKEN_INVALID`):

```bash
ssh hetzner 'systemctl stop ctrader-bot'
ssh -t hetzner 'cd /home/marian/trading-bot && sudo -u marian bash scripts/ctrader_live_grant.sh'
ssh -t hetzner 'cd /home/marian/trading-bot && sudo -u marian ./scripts/ctrader_live_promote.sh'
ssh hetzner 'systemctl start ctrader-bot'
```

Grant vypíše odkaz, v prehliadači treba **povoliť aj live účet**; kód z
presmerovania platí asi minútu. `ssh -t` je podstatné — bez pridelenej
konzoly sa skript nemá koho na kód spýtať. Keď terminál nie je (agent,
automatizácia), kód sa odovzdá rovno ako argument:

```bash
ssh hetzner 'bash /home/marian/trading-bot/scripts/ctrader_live_grant.sh <KÓD>'
```
 `ctrader_live_promote.sh` potom prepne nové
tokeny na kľúče, ktoré bot číta — musí pritom byť **zastavený**, inak si `.env`
prepíšete navzájom.

**5. Ak dashboard ukazuje staré čísla:** pozri pečiatku dát v hlavičke. Rozlišuje
„Bez spojenia" (proces nežije) a „Dáta zamrznuté" (proces žije, ale príprava
dát zlyháva). Druhý prípad nájdeš v `bot_status.sh` ako zlyhania snapshotu
alebo odmietnutia `PGRST`.

## Na čo si dať pozor

- **Nikdy nepridávať nový kľúč do stavu bota** (`state` v `_refresh_sync_snapshot`)
  bez toho, aby zodpovedajúci stĺpec existoval v tabuľke `bot_state`. Supabase
  odmietne **celý** riadok chybou `PGRST204` a dashboard ticho zamrzne na
  poslednom zapísanom stave. Doplnkové údaje patria do `config` — to je json.
- **Dve inštancie bota naraz** proti jednému účtu si rozsypú mriežku a navzájom
  zneplatnia refresh tokeny. `run_bot_ctrader.sh` preto lokálny beh blokuje.
- **Spotware drží jedno app-auth spojenie naraz.** Diagnostické skripty preto
  púšťaj so zastaveným botom, ak chceš dôveryhodný výsledok.
- **Geometria gridu.** Lab overil len G2B (krok 0,15 % / 0,225 %). Automatika
  podľa swapov nastavuje hustejšie kroky — je to vedomý experiment, nie
  lab-validovaný stav.
- **Kotva gridu sa volí v `.env` (`CTRADER_GRID_ANCHOR`).** Default
  `extreme` = bežiace extrémy, takto live obchoduje: každý medzipokles kotvu
  stiahne, takže vstupy padajú aj 4–6 pipov od seba pri kroku 11,2 (6. 10.
  to pri 31 pozíciách stlačilo margin level na 99,6 %). `entry` = #16, kotva
  je posledný vstup a odstup je vždy aspoň krok — ale lab ukázal, že prázdnu
  stranu v trende zmrazí (40 cyklov namiesto 296 za live obdobie). #16 sa na
  server nikdy nenasadil; prepínač existuje od 16. 10. 2026, aby nasadenie
  kódu samo nezmenilo správanie živého gridu.
- **Pásmo nie je symetrická ochrana.** `band_low` vypína len **shorty**,
  `band_high` len **longy**. Pod dolnou hranou teda longy pribúdajú ďalej —
  pásmo nie je stop. Kto chce zastaviť nakupovanie pri páde, potrebuje
  pauzu vstupov alebo vypnutie strany, nie posun pásma.

## Virtuálny bot Strážca (súťaž od 19. 10. 2026)

Súťažná stratégia (`trading/strategy_guard.py`) beží na **simulovanom**
cTrader účte, nie na demo účte u brokera — Spotware drží na jednu
aplikáciu jedno app-auth spojenie a druhý proces by odpojil živého bota.

- **Ceny:** živý bot každých 10 s prepíše `data/feed.json` (bid, ask,
  extrémy zo spot streamu medzi vzorkami, swapové sadzby účtu). Píše len
  live inštancia. Keď feed zastará (živý bot stojí), virtuálny bot nič
  neotvára a čaká.
- **Účtovanie:** `trading/paper_broker.py` — vstup za ask/bid, TP na
  extrémoch medzi kotáciami, provízia 0,526 €, swap 17:00 NY so sadzbami
  live účtu (streda 3×), marža z väčšej strany, stop-out 50 %.
- **Dáta:** `data/paper/paper.db` (rovnaká schéma ako live), log
  `data/paper/paper_bot.log`, zrkadlo v Supabase s `env='paper'`.
  Dashboard: prepínač **SIM** a stránka **Súťaž** (Ďalšie → Súťaž).
- **Príkazy z dashboardu nečíta** — tabuľka `commands` nemá env a príkazy
  patria živému botovi. V režime SIM preto dashboard skryje pauzu aj
  zatváranie pozícií.

Inštalácia (raz):

```bash
ssh hetzner '/home/marian/trading-bot/scripts/bot_deploy.sh main'     # živý bot začne písať feed
ssh hetzner 'sudo cp /home/marian/trading-bot/scripts/ctrader-paper.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now ctrader-paper'
ssh hetzner 'journalctl -u ctrader-paper -n 20 --no-pager'
```

Overenie na histórii (bez Supabase): `python3 paper_bot.py --replay 2026-08-06`
— musí sedieť so `strategy_lab_vs.py` (232 cyklov, výsledok ≈ +770 €).

Reset súťaže (nový štart s čistým účtom): zastaviť službu, zmazať
`data/paper/paper.db` a v Supabase riadky s `env='paper'`, spustiť službu.
