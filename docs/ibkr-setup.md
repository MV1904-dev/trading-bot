# Prepojenie na IBKR paper účet (IB Gateway + `broker_ibkr.py`)

Tento návod prepojí projekt s **paper (demo)** účtom Interactive Brokers cez
**IB Gateway**. Kroky 1–3 sa robia na tvojom Macu (GUI aplikácia), kroky 4–5
sú už kód v tomto repozitári.

> ⚠️ **Dôležité:** IB Gateway musí bežať na tom istom počítači, z ktorého
> spúšťaš Python skripty (počúva len na `127.0.0.1`). Cloud/CI prostredie sa
> na tvoj lokálny Gateway nepripojí — kroky 1–3 a živý test (krok 5) prebehnú
> na tvojom Macu.

---

## 1. Stiahnutie a inštalácia IB Gateway (macOS, stable)

1. Otvor: <https://www.interactivebrokers.com/en/trading/ibgateway-stable.php>
2. Stiahni **IB Gateway – Stable** pre **macOS** (súbor `.dmg`).
3. Otvor `.dmg` a presuň **IB Gateway** do `Applications`.
4. Pri prvom spustení macOS spýta na povolenie
   (*System Settings → Privacy & Security → Open Anyway*), potvrď.

> Alternatíva cez Homebrew (ak ho používaš):
> `brew install --cask ib-gateway`

---

## 2. Prihlásenie — režim Paper Trading

Do Gateway sa prihlásiš **sám v jeho okne** (heslo sem nikam nepíšeme).

1. Spusti **IB Gateway**.
2. V prihlasovacom okne vyber režim **Paper Trading** (nie Live).
3. Zadaj svoje **paper** prihlasovacie meno **`marianbot`** a heslo.
4. Klikni **Log In**. Po prihlásení sa otvorí malé okno Gateway s ponukou
   *Configure*.

> Paper účet má vlastné (iné) heslo než živý účet. Ak ho nemáš,
> vygeneruj/pozri ho v Client Portal → *Settings → Paper Trading Account*.

---

## 3. Nastavenie API v Gateway

V okne Gateway otvor **Configure → Settings** (ikona ozubeného kolieska).

**`API → Settings`:**

- ✅ **Enable ActiveX and Socket Clients**
- **Socket port:** `4002`  ← paper Gateway
- ⬜ **Read-Only API** — necháš **vypnuté** (odškrtnuté), aby sa dali zadávať
  a rušiť príkazy
- **Trusted IPs:** pridaj `127.0.0.1` (a nič viac — teda len localhost)
- ⬜ *Allow connections from localhost only* — nechaj zapnuté ak je táto
  voľba prítomná

**`API → Precautions`** (voliteľné, ale odporúčané pre bota):

- ✅ Bypass Order Precautions for API Orders — zamedzí blokujúcim dialógom,
  ktoré by inak API príkaz „zasekli“ na potvrdenie.

**Auto restart namiesto auto-logout** (`Configure → Settings → Lock and Exit`):

- Zvoľ **Auto restart** (nie *Auto logout*). Gateway sa tak raz denne sám
  reštartuje a **neodhlási** — spojenie a API zostanú funkčné bez toho, aby
  si sa musel ráno znova prihlasovať.
- Ak vidíš *Restart time*, nastav ho na čas mimo obchodovania (napr. 03:00).

Klikni **OK / Apply**. Port `4002` teraz počúva na `127.0.0.1`.

> Rýchla kontrola v termináli: `nc -z 127.0.0.1 4002 && echo "port 4002 OK"`

---

## 4. Python knižnica a modul `broker_ibkr.py`

Modul je už v repozitári: [`trading/broker_ibkr.py`](../trading/broker_ibkr.py).
Je navrhnutý tak, aby ho zdieľal **bot** (živé volania) aj **backtest**
(offline čítanie CSV cache).

Inštalácia závislostí:

```bash
python3 -m venv .venv && source .venv/bin/activate   # odporúčané
pip install -r trading/requirements.txt              # ib_async + pandas
```

Čo modul ponúka (trieda `IBKRBroker`):

| Oblasť       | Metódy |
|--------------|--------|
| Pripojenie   | `connect()`, `disconnect()`, context manager `with IBKRBroker() as ib:` |
| Stav účtu    | `account_summary()`, `positions()`, `portfolio()` |
| Kotácie      | `quote(contract)` → `bid/ask/mid/spread` |
| História     | `history()`, `history_deep()`, `history_cached()` (CSV cache v `data/`) |
| Príkazy      | `market_order()`, `limit_order()`, `stop_order()`, `cancel()`, `cancel_all()`, `open_orders()` |
| Kontrakty    | `forex("EURUSD")`, `stock("AAPL")`, `qualify()` |
| Backtest     | `IBKRBroker.load_cached("EURUSD", "5 mins")` — číta CSV **bez pripojenia** |

Príklad (bot):

```python
from trading.broker_ibkr import IBKRBroker

with IBKRBroker(port=4002) as ib:              # 4002 = Gateway paper
    print(ib.account_summary())
    print(ib.quote(ib.forex("EURUSD")))
    df = ib.history_cached(ib.forex("EURUSD"), bar_size="5 mins")
    # ib.market_order(ib.forex("EURUSD"), "BUY", 20000)
```

Príklad (backtest, úplne offline):

```python
from trading.broker_ibkr import IBKRBroker
df = IBKRBroker.load_cached("EURUSD", "5 mins")   # číta data/EURUSD_M5.csv
```

---

## 5. Test — stav účtu, živý EURUSD a M5 história

Gateway musí bežať (kroky 1–3). Potom:

```bash
pip install -r trading/requirements.txt

# rýchla kontrola pripojenia (stiahne len jeden nedávny chunk):
python -m trading.smoke_test --quick -v

# plný beh: hlboká M5 história EURUSD do data/EURUSD_M5.csv
python -m trading.smoke_test -v
```

Skript:

1. pripojí sa na Gateway (`127.0.0.1:4002`),
2. vypíše **stav paper účtu** (NetLiquidation, Cash, BuyingPower, pozície),
3. vypíše **živý bid/ask EURUSD**,
4. stiahne **M5 históriu EURUSD tak hlboko, ako IBKR dovolí**, a uloží ju do
   `data/EURUSD_M5.csv`.

### Cache správanie

- **Prvý beh:** modul kráča späť v čase (chunk po chunku), kým IBKR vracia
  dáta — teda stiahne históriu tak hlboko, ako je dostupná. IBKR limituje
  tempo (~60 historických requestov / 10 min), preto medzi requestami čaká
  ~10 s; prvý plný back-fill môže chvíľu trvať.
- **Ďalšie behy:** načíta `data/EURUSD_M5.csv` a **dosťahuje len nové sviečky**
  od poslednej uloženej — rýchlo. Dáta sa spájajú, deduplikujú podľa času a
  uložia späť.
- CSV súbory v `data/` sú v `.gitignore` (nekomitujú sa); `data/.gitkeep`
  drží priečinok v repozitári.

### Poznámky / riešenie problémov

- **`Could not connect ...`** → Gateway nebeží, alebo je zlý port
  (paper = `4002`, live = `4001`, TWS paper = `7497`), alebo v API nastaveniach
  nie je `127.0.0.1` medzi *Trusted IPs*.
- **Prázdny bid/ask** → trh je zatvorený (víkend) alebo chýba FX market-data
  subscription. Historické `MIDPOINT` dáta fungujú aj tak; modul si
  v prípade potreby vyžiada oneskorené dáta (`market_data_type`).
- **`clientId already in use`** → beží iné pripojenie s rovnakým `clientId`;
  zmeň `--client-id` (napr. `--client-id 18`).
- FX používa `whatToShow="MIDPOINT"` (na IDEALPRO nie je `TRADES`).
  Pre akcie použi `whatToShow="TRADES"`.
