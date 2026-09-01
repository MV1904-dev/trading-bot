# Trading bot – XTB xAPI (demo)

Sada Python skriptov na prácu s obchodnou platformou **XTB** cez jej rozhranie
[xAPI](http://developers.xstore.pro/documentation/). Projekt pokrýva celý reťazec
od overenia pripojenia, cez testovací obchod na demo účte, až po samostatný modul
so stratégiou a jej backtest na historických dátach.

Všetko je navrhnuté a testované proti **demo účtu XTB**.

---

## Čo projekt robí

1. **Overí pripojenie** k XTB serveru a prihlásenie údajmi z `.env`
   (`test_connection.py`).
2. **Vykoná kompletný testovací obchod** na demo účte – otvorí minimálnu pozíciu
   so stop lossom a take profitom, počká, zatvorí ju a vypíše zisk/stratu
   (`test_trade.py`).
3. **Poskytuje obchodnú stratégiu** ako čistý, znovupoužiteľný modul – breakout
   s filtrom trendu cez EMA a stopmi podľa ATR (`strategy.py`).
4. **Otestuje stratégiu na histórii** – stiahne 5-minútové sviečky z XTB,
   odsimuluje obchodovanie na účte s 10 000 EUR a vypíše štatistiky
   (`backtest.py`).

---

## Štruktúra súborov

| Súbor | Popis |
|---|---|
| `test_connection.py` | Test pripojenia. Pripojí sa cez WebSocket na demo endpoint, prihlási sa údajmi z `.env`, načíta bid/ask pre `EURUSD`, korektne sa odhlási. Slúži na rýchle overenie, či sú prihlasovacie údaje a sieť v poriadku. |
| `test_trade.py` | Test celého obchodného cyklu na demo účte: prihlásenie → `getSymbol` pre `BITCOIN` → posledných 20 sviečok M5 → otvorenie minimálneho BUY obchodu (SL −1 %, TP +2 %) → overenie stavu transakcie → 30 s držanie → zatvorenie a výpis zisku/straty → odhlásenie. |
| `strategy.py` | Logika stratégie bez akýchkoľvek závislostí na xAPI. Obsahuje indikátory (EMA, ATR, klzavé maximum/minimum), dátové typy `Candle` a `Signal`, konfiguráciu `StrategyConfig` a triedu `Strategy`. Používa ho backtest aj prípadný živý bot. |
| `backtest.py` | Backtest stratégie na dátach z XTB (`EURUSD`, `GOLD`, `BITCOIN`). Stiahne a nacachuje históriu do `data/`, spustí event-driven simuláciu na spoločnom účte a vypíše štatistiky (počet obchodov, win rate, P/L, max. drawdown, profit factor). Zoznam obchodov uloží do CSV. |
| `xtb_socket.py` | Pomocný transport – tenký wrapper nad priamym TLS socketom XTB (`xapia/xapib.x-station.eu:5124`). Používa ho `test_trade.py`, pretože verejná proxy má zakázané obchodovanie. |
| `.env.example` | Vzor konfiguračného súboru s prázdnymi premennými. |
| `requirements.txt` | Python závislosti (`websocket-client`, `python-dotenv`). |

> Repozitár obsahuje aj ďalšie, experimentálne súbory (`bot.py`, `bot_ctrader.py`,
> `dashboard.py`, `strategy_lab_*.py`, `trading/`, …), ktoré nie sú súčasťou tohto
> odovzdania a majú vlastnú konfiguráciu.

---

## Inštalácia

Potrebný je **Python 3.9+**.

```bash
# 1) naklonuj repozitár a vojdi do priečinka
git clone <URL repozitára>
cd trading-bot

# 2) (odporúčané) vytvor virtuálne prostredie
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3) nainštaluj závislosti
pip install -r requirements.txt
```

---

## Nastavenie `.env`

Prihlasovacie údaje sa nikdy nepíšu do kódu – čítajú sa zo súboru `.env`,
ktorý je v `.gitignore` a **necommituje sa**.

```bash
cp .env.example .env
```

Potom do `.env` doplň údaje k svojmu XTB **demo** účtu:

```env
XTB_USER_ID=1234567
XTB_PASSWORD=tvoje-heslo
```

| Premenná | Význam |
|---|---|
| `XTB_USER_ID` | Číslo demo účtu – príde ti e-mailom po registrácii demo účtu na stránke XTB. |
| `XTB_PASSWORD` | Heslo k tomuto demo účtu. |

---

## Spustenie

### 1. `test_connection.py` – overenie pripojenia

```bash
python3 test_connection.py
```

Očakávaný výstup:

```
Pripojené na wss://ws.xapi.pro/demo
Prihlásenie úspešné.

=== EURUSD ===
  Bid:    1.08123
  Ask:    1.08135
  Spread: 0.00012

Odhlásené.
Spojenie uzavreté.
```

Skript vracia exit kód `0` pri úspechu a `1` pri chybe (chýbajúce údaje,
nesprávne heslo, nedostupný server).

---

### 2. `test_trade.py` – testovací obchod

```bash
python3 test_trade.py
```

> **Pozor:** skript zadáva **skutočnú obchodnú transakciu**. Spúšťaj ho výhradne
> na DEMO účte. Celý beh trvá cca minútu (30 s sa pozícia drží otvorená).

Skrátený očakávaný výstup:

```
[10:15:02] Pripojené na xapia.x-station.eu:5124 (TLS)
[10:15:03] Prihlasujem sa na XTB demo…
[10:15:03] Prihlásenie úspešné.
[10:15:04] Symbol BITCOIN:
    Bid:            64120.5
    Ask:            64145.0
    Spread:         24.5
    Min. objem:     0.01 lot
[10:15:05] Posledných 20 sviečok M5 (digits=2):
  Čas                        Open         High          Low        Close      Objem
  ...
[10:15:06] Otváram BUY 0.01 lot BITCOIN @ ~64145.0 (SL 63503.55 = -1 %, TP 65427.9 = +2 %)…
[10:15:07] Stav obchodu č. 123456789: PRIJATÝ (ACCEPTED)
[10:15:08] Otvorená pozícia č. 987654321 @ 64145.0, objem 0.01 lot.
[10:15:08] Držím pozíciu 30 s…
[10:15:39] Zatváram pozíciu č. 987654321 @ ~64150.0…
[10:15:40] === VÝSLEDOK OBCHODU ===
    Pozícia č.:     987654321
    Vstup / výstup: 64145.0 → 64150.0
    Zisk/strata:    +0.05 USD  (ZISK, realizovaný (z histórie))
[10:15:41] Odhlásené.
```

Parametre obchodu sa dajú upraviť na začiatku súboru: `SYMBOL`, `HOLD_SECONDS`,
`SL_PCT`, `TP_PCT`.

---

### 3. `strategy.py` – modul so stratégiou

Nespúšťa sa samostatne, importuje sa. Pravidlá:

* **Signál** – prerazenie maxima/minima posledných `breakout_lookback` sviečok
  (predvolene 20).
* **Filter trendu** – long len ak je cena nad EMA(50), short len ak je pod ňou.
* **Stop loss** – `1.5 × ATR(14)`, **take profit** – `2 × SL` (RRR 1:2).
* **Obchodné hodiny** – predvolene 9–21 h; symboly v `always_open`
  (napr. `BITCOIN`) obchodujú nepretržite.
* Maximálne **1 pozícia na symbol** – toto obmedzenie vynucuje volajúci kód.

Príklad použitia:

```python
from strategy import Strategy, StrategyConfig, Candle

strat = Strategy(StrategyConfig(breakout_lookback=20, ema_period=50, tp_rr=2.0))
signal = strat.latest_signal("BITCOIN", candles)   # candles: list[Candle]
if signal:
    print(signal.side, signal.entry, signal.sl, signal.tp)
```

---

### 4. `backtest.py` – backtest stratégie

```bash
python3 backtest.py            # použije cache v data/, ak chýba, stiahne históriu
python3 backtest.py --refresh  # vynúti opätovné stiahnutie histórie
python3 backtest.py --offline  # nesťahuje nič, beží len z cache
```

Prvý beh potrebuje `.env` (sťahuje históriu z XTB). Dáta sa nacachujú do
priečinka `data/` (`<SYMBOL>_M5.csv` + `<SYMBOL>_meta.json`), ktorý je
v `.gitignore`.

Skrátený očakávaný výstup:

```
Sťahujem históriu pre: EURUSD, GOLD, BITCOIN …
  EURUSD: 12480 sviečok uložených do data/EURUSD_M5.csv
EURUSD: 12480 sviečok (2025-01-02 → 2025-03-14), spread 0.00012

================================================================
VÝSLEDKY BACKTESTU  (kapitál 10,000 EUR, risk 1%/obchod)
================================================================
Symbol      Obch.    Win%          P/L       MaxDD              PF
----------------------------------------------------------------
EURUSD         42   38.1%      -120.45      310.20            0.87
GOLD           37   40.5%       210.30      280.10            1.15
BITCOIN        51   35.3%       340.75      450.60            1.22
----------------------------------------------------------------
SPOLU         130   37.7%       430.60      520.40            1.09
================================================================
Konečný kapitál: 10,430.60 EUR
Zoznam 130 obchodov uložený do data/backtest_trades.csv
```

(Konkrétne čísla závisia od toho, koľko histórie demo server práve vráti.)

**Model simulácie** – zjednodušenia sú zámerné a zdokumentované:

* Vstup na `close` signálnej sviečky, ceny sviečok sa berú ako mid.
* Spread sa účtuje ako jednorazový náklad na obchod.
* Veľkosť pozície tak, aby strata na SL bola ≈ 1 % aktuálneho kapitálu
  (zaokrúhlené na `lotStep`, minimálne `lotMin`).
* Ak sviečka pretne SL aj TP naraz, počíta sa **SL** (horší scenár).

---

## Riešenie problémov

| Problém | Riešenie |
|---|---|
| `CHYBA: V .env súbore chýba XTB_USER_ID alebo XTB_PASSWORD` | Vytvor `.env` podľa `.env.example` a doplň obe hodnoty. |
| `CHYBA: Prihlásenie zlyhalo` | Skontroluj číslo účtu a heslo; over, či ide o **demo** účet. |
| `Nepodarilo sa pripojiť` | Skontroluj internetové pripojenie a to, že odchádzajúce spojenia na porty 443 / 5124 nie sú blokované firewallom. |
| Backtest hlási chýbajúce dáta | Spusti ho bez `--offline`, aby si stiahol históriu. |

> Poznámka: pôvodné hosty `ws.xtb.com` boli vypnuté 14. 3. 2025. Skripty preto
> používajú proxy `ws.xapi.pro` (len na čítanie dát) a priame endpointy
> `xapia/xapib.x-station.eu:5124` na obchodovanie.
