"""Shadow Judge — AI tieňový posudzovateľ signálov. NIKDY nevykonáva.

Garancie:
* exekúcia bota na posudok NIKDY nečaká a nie je ním ovplyvnená —
  submit() spraví len rýchly lokálny DB insert a odpáli daemon vlákno;
  API volanie beží mimo hlavnej slučky
* pri chybe API / chýbajúcom kľúči sa zaloguje "no_opinion" a bot ide ďalej
* zápisy: tabuľka shadow_judgments v DB inštancie; outcome obchodu sa
  dopáruje cez trade_id (link() po otvorení)

Volanie: claude-haiku, temperature 0, fixná šablóna promptu (posledných
50 H1 sviečok, ATR, pásma, pozície + floating, kalendár, čas, signál).
Vyžaduje sa čistý JSON {"decision","confidence","reason"}.

Náklady sa logujú per volanie z presných usage tokenov (Haiku 4.5:
~1 USD/M vstup, ~5 USD/M výstup — konštanty PRICE_IN/PRICE_OUT).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
PRICE_IN = 1.00 / 1_000_000       # USD za vstupný token (Haiku 4.5, orientačne)
PRICE_OUT = 5.00 / 1_000_000
H1_KEEP = 60                      # držíme ~60 H1 sviečok, do promptu ide 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    strategy TEXT, side TEXT, price REAL, tp REAL, sl REAL,
    blocked TEXT DEFAULT '',
    trade_id INTEGER,
    decision TEXT NOT NULL DEFAULT 'pending',
    confidence INTEGER, reason TEXT,
    input_tokens INTEGER, output_tokens INTEGER,
    cost_usd REAL, latency_ms INTEGER, model TEXT
);
"""

SYSTEM_PROMPT = (
    "Si prísny, skeptický risk-manažér FX obchodného systému. Dostaneš "
    "kontext trhu a jeden obchodný signál. Tvoja úloha: povedať, či by si "
    "tento KONKRÉTNY vstup v tomto KONKRÉTNOM kontexte pustil (approve) "
    "alebo zastavil (veto). Nehodnotíš stratégiu ako celok, len tento vstup "
    "teraz. Odpovedz VÝHRADNE jedným JSON objektom bez akéhokoľvek ďalšieho "
    'textu: {"decision": "approve" alebo "veto", "confidence": 0-100, '
    '"reason": "jedna stručná veta po slovensky"}'
)


class ShadowJudge:
    def __init__(self, db_path: Path, api_key: str, *,
                 model: str = DEFAULT_MODEL,
                 band_lo: float = 1.1200, band_hi: float = 1.1600):
        self.db_path = str(db_path)
        self.api_key = api_key or ""
        self.model = model
        self.band_lo, self.band_hi = band_lo, band_hi
        self.enabled = bool(self.api_key)
        self._warned = False
        self._lock = threading.Lock()
        self._h1: deque = deque(maxlen=H1_KEEP)   # (ts,o,h,l,c) uzavreté
        self._cur = None                          # rozpracovaný H1 bar
        with self._conn() as con:
            con.executescript(SCHEMA)
        if not self.enabled:
            log.warning("ShadowJudge: ANTHROPIC_API_KEY chýba — posudky "
                        "sa nebudú generovať (bot beží normálne).")

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    # ---------------------------------------------------------------- H1 feed
    def bootstrap_candles(self, candles: list) -> None:
        """Naplní H1 históriu. Prijíma dicty {'time','o','h','l','c'}
        alebo objekty s .ts/.open/.high/.low/.close (ľubovoľné TF ≤ H1)."""
        for cd in candles:
            if isinstance(cd, dict):
                ts, o, h, l, c = cd["time"], cd["o"], cd["h"], cd["l"], cd["c"]
            else:
                ts, o, h, l, c = cd.ts, cd.open, cd.high, cd.low, cd.close
            self._feed(float(ts), float(o), float(h), float(l), float(c))
        if self._cur:
            self._h1.append(tuple(self._cur))
            self._cur = None

    def on_price(self, mid: float, ts: float | None = None) -> None:
        """Živé ceny z ticku bota — agregácia do H1."""
        t = ts if ts is not None else time.time()
        self._feed(t, mid, mid, mid, mid)

    def _feed(self, ts: float, o: float, h: float, l: float, c: float) -> None:
        bucket = int(ts // 3600) * 3600
        if self._cur is None or self._cur[0] != bucket:
            if self._cur is not None:
                self._h1.append(tuple(self._cur))
            self._cur = [bucket, o, h, l, c]
        else:
            self._cur[2] = max(self._cur[2], h)
            self._cur[3] = min(self._cur[3], l)
            self._cur[4] = c

    # ---------------------------------------------------------------- submit
    def submit(self, strategy: str, side: str, price: float, tp: float,
               sl: float, atr: float, open_positions: dict,
               floating: float, events: list[str],
               blocked: str = "") -> int | None:
        """Zaloguje signál a odpáli async posudok. Vráti judgment id
        (alebo None, ak je sudca vypnutý). NIKDY neblokuje."""
        if not self.enabled:
            if not self._warned:
                self._warned = True
                log.info("ShadowJudge vypnutý (bez API kľúča).")
            return None
        try:
            with self._lock, self._conn() as con:
                cur = con.execute(
                    "INSERT INTO shadow_judgments(ts,strategy,side,price,tp,"
                    "sl,blocked,model) VALUES (?,?,?,?,?,?,?,?)",
                    (time.time(), strategy, side, price, tp, sl, blocked,
                     self.model))
                jid = cur.lastrowid
        except sqlite3.Error as exc:
            log.warning("ShadowJudge insert zlyhal: %s", exc)
            return None
        prompt = self._build_prompt(strategy, side, price, tp, sl, atr,
                                    open_positions, floating, events, blocked)
        threading.Thread(target=self._worker, args=(jid, prompt),
                         daemon=True, name=f"shadow-{jid}").start()
        return jid

    def link(self, jid: int | None, trade_id: int) -> None:
        if jid is None:
            return
        try:
            with self._lock, self._conn() as con:
                con.execute("UPDATE shadow_judgments SET trade_id=? WHERE id=?",
                            (trade_id, jid))
        except sqlite3.Error as exc:
            log.warning("ShadowJudge link zlyhal: %s", exc)

    # ---------------------------------------------------------------- prompt
    def _build_prompt(self, strategy, side, price, tp, sl, atr,
                      open_positions, floating, events, blocked) -> str:
        now = datetime.now(timezone.utc)
        dni = ["pondelok", "utorok", "streda", "štvrtok", "piatok",
               "sobota", "nedeľa"]
        candles = list(self._h1)[-50:]
        lines = [f"{datetime.fromtimestamp(int(t), tz=timezone.utc):%m-%d %H}h "
                 f"O:{o:.5f} H:{h:.5f} L:{l:.5f} C:{c:.5f}"
                 for t, o, h, l, c in candles]
        if price < self.band_lo:
            band = f"POD dolným pásmom {self.band_lo} (grid tu berie len longy)"
        elif price > self.band_hi:
            band = f"NAD horným pásmom {self.band_hi} (grid tu berie len shorty)"
        else:
            band = (f"vnútri pásiem {self.band_lo}–{self.band_hi} "
                    f"({100 * (price - self.band_lo) / (self.band_hi - self.band_lo):.0f} % výšky)")
        sl_txt = f"{sl:.5f}" if sl else "bez SL (gridová logika)"
        ev_txt = "; ".join(events) if events else "žiadne"
        return (
            f"ČAS: {now:%Y-%m-%d %H:%M} UTC ({dni[now.weekday()]})\n"
            f"PÁR: EURUSD\n\n"
            f"POSLEDNÝCH {len(lines)} H1 SVIEČOK:\n" + "\n".join(lines) + "\n\n"
            f"ATR(14) na TF signálu: {atr:.5f}\n"
            f"POZÍCIA V PÁSMACH: {band}\n"
            f"OTVORENÉ POZÍCIE: {open_positions.get('long', 0)} long, "
            f"{open_positions.get('short', 0)} short; "
            f"floating P/L {floating:+.2f}\n"
            f"DNEŠNÉ HIGH-IMPACT UDALOSTI: {ev_txt}\n"
            + (f"POZN.: signál bol exekučnou vrstvou blokovaný ({blocked})\n"
               if blocked else "")
            + f"\nSIGNÁL NA POSÚDENIE:\n"
            f"  stratégia: {strategy}\n"
            f"  smer: {side.upper()}\n"
            f"  vstupná cena: ~{price:.5f}\n"
            f"  take-profit: {tp:.5f}\n"
            f"  stop-loss: {sl_txt}\n"
        )

    # ---------------------------------------------------------------- worker
    def _worker(self, jid: int, prompt: str) -> None:
        t0 = time.time()
        decision, conf, reason = "no_opinion", None, ""
        in_t = out_t = 0
        cost = 0.0
        try:
            body = json.dumps({
                "model": self.model,
                "max_tokens": 150,
                "temperature": 0,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(API_URL, data=body, method="POST",
                                         headers={
                                             "Content-Type": "application/json",
                                             "x-api-key": self.api_key,
                                             "anthropic-version": "2023-06-01",
                                         })
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            text = "".join(b.get("text", "") for b in resp.get("content", []))
            usage = resp.get("usage", {})
            in_t = int(usage.get("input_tokens", 0))
            out_t = int(usage.get("output_tokens", 0))
            cost = in_t * PRICE_IN + out_t * PRICE_OUT
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                parsed = json.loads(m.group(0))
                d = str(parsed.get("decision", "")).lower()
                if d in ("approve", "veto"):
                    decision = d
                    conf = max(0, min(100, int(parsed.get("confidence", 0))))
                    reason = str(parsed.get("reason", ""))[:300]
                else:
                    reason = f"neplatné decision: {d[:60]}"
            else:
                reason = f"bez JSON: {text[:120]}"
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:150]}"
        except Exception as exc:  # noqa: BLE001 — sudca nesmie nič zhodiť
            reason = f"{type(exc).__name__}: {str(exc)[:150]}"
        latency = int((time.time() - t0) * 1000)
        try:
            with self._lock, self._conn() as con:
                con.execute(
                    "UPDATE shadow_judgments SET decision=?, confidence=?, "
                    "reason=?, input_tokens=?, output_tokens=?, cost_usd=?, "
                    "latency_ms=? WHERE id=?",
                    (decision, conf, reason, in_t, out_t, cost, latency, jid))
        except sqlite3.Error as exc:
            log.warning("ShadowJudge update zlyhal: %s", exc)
        log.info("Shadow #%d: %s (conf %s, %d ms, $%.4f) %s",
                 jid, decision, conf, latency, cost, reason[:60])
