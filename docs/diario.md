# Diario di bordo — Betfair Football Trading System

## 07/09/2026 — Primo test paper trading dal vivo: diagnosi e fix di 4 bug bloccanti

- **Primo run end-to-end contro mercati Betfair reali** (paper trading, mai accaduto prima):
  05/09 Inter-Napoli (kick-off 18:00) e Roma-Atalanta (20:45), 06/09 Arsenal-Chelsea
  (15:30). Screener validato correttamente su tutti e tre (criteri LTD rispettati,
  liquidità sufficiente). Nessuna entrata a mercato osservata in nessuno dei tre casi
  fino alla chiusura di questa sessione — `trades_ltd.csv` sempre vuoto.

- **Bug critico trovato e corretto: rilevamento gol/minuto mai stato funzionante.**
  `strategy_LTD.py::_get_score`/`_get_minute` leggevano
  `market_book.market_definition.match_stat` e `.regulationTime` — attributi
  **inesistenti** in betfairlightweight (verificato con grep sull'intero pacchetto
  installato, zero occorrenze). L'eccezione veniva intercettata silenziosamente e i
  metodi ritornavano sempre `(0,0)`/`None`, quindi entrata sempre potenzialmente
  valida ma **green-up su gol e stop loss al 70' mai in grado di scattare**: una volta
  aperta, la posizione sarebbe rimasta aperta fino alla chiusura naturale del mercato.
  Fix: nuovo metodo `_get_live_score_and_minute()` che chiama il vero endpoint Betfair
  `trading.in_play_service.get_scores(event_ids=[...])`, con cache di 15s per non
  interrogare l'endpoint a ogni tick dello stream. Verificato con una chiamata di test
  contro Inter-Napoli live: `match_status=KickOff`, `time_elapsed_seconds=24`,
  `Inter 0-0 Napoli` — dato reale, confermato anche un ritardo di alcuni minuti
  rispetto al tempo reale (coerente con la Delayed App Key, non un bug).

- **Bug critico trovato in flumine 3.1.0 (libreria vendored, non nostro codice):**
  `flumine/worker.py::keep_alive` fa `resp.status == "SUCCESS"` senza controllare
  prima `resp is None` — se `BetfairClient.keep_alive()` intercetta un
  `KeepAliveError` (es. `NO_SESSION`), ritorna `None` per via del `try/except` che non
  ha un `return` esplicito nel ramo eccezione, e il confronto successivo va in
  `AttributeError`, **prima** di raggiungere il fallback `client.login()` che
  rinnoverebbe la sessione. Osservato in produzione la notte 05→06/09: sessione morta
  alle 19:31:21 con `KeepAliveError: API keepAlive FAIL: NO_SESSION`, mai più
  recuperata per le successive ~6 ore (errori `INVALID_SESSION_INFORMATION` ogni
  ~2 minuti su ogni chiamata REST, fino al controllo manuale delle 01:11). Il bot non
  si è mai fermato da solo né ha smesso di apparire "in esecuzione", semplicemente non
  poteva più fare nulla di utile. Fix: monkeypatch mirato in `bot/main.py`
  (`_keep_alive_fixed`, applicato su `flumine.worker.keep_alive` prima di
  `framework.run()`) che copia la funzione originale con l'unica correzione
  `resp is not None and resp.status == ...`. Non tocca il pacchetto vendored.

- **Ipotesi più probabile sulla causa della sessione morta** (non solo un bug latente
  che aspettava di manifestarsi): alle 18:14 del 05/09 è stato lanciato uno script di
  verifica one-off (`backtest/tests/test_inplay_score.py`, poi cancellato) che fa un
  secondo `login_interactive()` sullo **stesso account** mentre il bot principale era
  già collegato dalle 16:11. Betfair consente una sola sessione interattiva attiva per
  account: è plausibile che il secondo login abbia invalidato silenziosamente la
  sessione del bot in corso, con effetto visibile solo un'ora dopo (19:31), quando il
  worker `keep_alive` ha provato a rinnovarla scoprendola già morta. **Lezione
  operativa, non solo di codice**: non lanciare mai script che fanno login sullo
  stesso account Betfair mentre il bot è collegato e sta monitorando un mercato live.

- **Due bug minori introdotti/scoperti durante i rilanci del 06/09, corretti sul
  momento:**
  - `strategy_LTD.py::start()` usava `flumine.client.betting_client` (attributo
    inesistente, `Flumine` espone `clients` plurale) — crash immediato di
    `framework.run()` al primo rilancio dopo il fix del punto precedente. Corretto in
    `flumine.clients.get_default().betting_client`, verificato con un test isolato
    sulla classe `Clients` di flumine.
  - `bot/main.py` chiamava `trading.logout()` nel blocco `finally` di `main()` anche
    se `framework.run()` (context manager) si era già loggato fuori da solo in modo
    pulito nel proprio `__exit__` — il secondo logout su sessione già chiusa falliva
    con `LogoutError: API logout FAIL: INPUT_VALIDATION_ERROR`, innocuo ma con
    traceback finale sporco a ogni Ctrl+C. Rimossa la chiamata ridondante (il caso
    "nessuna strategia eleggibile, mai entrato nel `with`" mantiene il proprio
    `trading.logout()` esplicito, quello sì necessario).

- **Chiuso il gap "nessun ricontrollo quote prima dell'entrata"** (lasciato
  volutamente aperto nelle sessioni precedenti): lo screener valida i criteri LTD
  (home odds < 2.0, draw 3.2–4.5) una sola volta all'avvio, anche 20h prima del
  kick-off; l'entrata usava qualunque quota trovasse al momento, senza riverificare.
  Aggiunto `_recheck_ltd_criteria()` in `strategy_LTD.py`, chiamato da `_enter()`
  prima di piazzare il LAY: rilegge home/draw odds (best back, stesse soglie dello
  screener) e annulla l'entrata (mercato marcato chiuso, non ritenta) se le quote sono
  uscite dal range. Per identificare home/draw è stata riusata `identify_home_and_draw()`
  di `screener.py` (rinominata da `_identify_home_and_draw`, privata, a pubblica)
  invece di duplicare la logica sort_priority già delicata (bug storico del 28/08 su
  questo stesso punto).

- **Stato di fatto a fine sessione**: tutti e quattro i fix sono scritti e verificati
  a livello di sintassi/wiring, ma **nessuno è ancora stato validato contro un'entrata
  reale a mercato** — il 07/09 non c'erano partite eleggibili. Prossima giornata utile
  di campionato: prima vera prova del ciclo completo entrata→gol/stop-loss→uscita con
  P&L registrato in `trades_ltd.csv`.
