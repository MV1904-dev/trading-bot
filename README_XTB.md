# XTB xAPI – test pripojenia (demo)

Jednoduchý Python skript na overenie pripojenia k [XTB xAPI](http://developers.xstore.pro/documentation/)
cez WebSocket na **demo** účte.

## Čo skript robí

1. Pripojí sa na `wss://ws.xtb.com/demo`.
2. Prihlási sa príkazom `login` s údajmi z `.env`.
3. Zavolá `getSymbol` pre `EURUSD` a vypíše aktuálny **bid / ask** (a spread).
4. Korektne sa odhlási (`logout`) a uzavrie spojenie.
5. Pri akejkoľvek chybe vypíše zrozumiteľnú hlášku, čo zlyhalo.

## Nastavenie

1. Doplň prihlasovacie údaje k demo účtu do `.env` (súbor je v `.gitignore`, necommituje sa):

   ```env
   XTB_USER_ID=<číslo tvojho demo účtu>
   XTB_PASSWORD=<heslo>
   ```

2. Nainštaluj závislosti:

   ```bash
   pip install -r requirements.txt
   ```

## Spustenie

```bash
python3 test_connection.py
```

Očakávaný výstup pri úspechu:

```
Pripojené na wss://ws.xtb.com/demo
Prihlásenie úspešné.

=== EURUSD ===
  Bid:    1.08123
  Ask:    1.08135
  Spread: 0.00012

Odhlásené.
Spojenie uzavreté.
```

Skript vracia exit kód `0` pri úspechu a `1` pri chybe (napr. chýbajúce údaje,
nesprávne heslo, nedostupný server).

## Poznámka k demo účtu

Demo prihlasovacie údaje získaš registráciou demo účtu na stránke XTB.
`userId` je číslo účtu, ktoré ti príde pri založení demo účtu.
