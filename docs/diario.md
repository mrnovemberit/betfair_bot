# Diario di bordo — Betfair Football Trading System

## 09/09/2026 — Bug minuto stop-loss risolto, esteso il fix del login retry

- **Login retry esteso** (`bot/main.py::_keep_alive_fixed`): il retry automatico dopo un
  keep_alive fallito chiamava solo il login cert-based (`client.login()`), senza il
  fallback a `login_interactive()` che invece esiste in `build_client()` solo per
  l'avvio. Risultato osservato oggi: sessione caduta alle 15:16, mai più recuperata per
  ~55 minuti (loop infinito NO_SESSION → CERT_AUTH_REQUIRED ogni ~2 min) fino al riavvio
  manuale. Nessun impatto sui trade paper in corso (non chiamano l'ordine reale), ma in
  live avrebbe lasciato una posizione scoperta. Fix: il retry ora prova login
  cert-based e, se fallisce con `LoginError`, ricade su `login_interactive()`.

- **Trovata la vera causa per cui lo stop loss al 70' non era mai scattato** (in nessuna
  delle ~8 sessioni di test da giorni): `_get_live_score_and_minute` in
  `strategy_LTD.py` leggeva `result.time_elapsed_seconds` dall'endpoint
  `in_play_service.get_scores()` — campo che si azzera all'intervallo (tempo del
  **periodo corrente**, confermato in
  `betfairlightweight/resources/inplayserviceresources.py::Scores`, che espone anche
  `full_time_elapsed` come campo separato proprio per il minuto cumulativo). Un gol o
  un 0-0 al minuto reale 84' veniva quindi letto dal bot come minuto ~39 (84 meno la
  durata del 1° tempo) — mai abbastanza per superare la soglia di 70'. Il punteggio
  letto era sempre corretto, solo il minuto era sbagliato. Fix: usare
  `full_time_elapsed.hour*60 + full_time_elapsed.min` invece di `time_elapsed_seconds`.

- **Verifica dal vivo**: Rangers v St Mirren (Scottish Premiership), entrata alle
  20:48:36 (LAY draw @7.6, size 3,03€, liability 20€). Alle 22:xx l'utente ha
  controllato il sito Betfair.it: 0-0 confermato al minuto reale 84', quota pareggio
  crollata a 1,41-1,48 (mercato che prezza un pareggio ormai quasi certo) — eppure nessuno
  stop loss nel log, a conferma diretta del bug. La partita si è poi risolta da sola con
  un gol di Rangers al minuto reale ~88-89 (score 1,0): green-up, BACK @19 (quota
  esplosa a fine partita), P&L netto +1,73€. Il fix non era ancora in memoria sul
  processo in corso (serve un riavvio) — non ancora rivalidato con il minuto corretto.

- **Altri 5 trade osservati oggi** tra le sessioni di test (`--test-entry`, criteri LTD
  disattivati): Njardvik v Þróttur (LAY 4.0→BACK 4.5, +0,70€), United IK Nordic v
  Värnamo (4.2→3.95, -0,40€), Al Mokawloon v Al Ahly Cairo (4.4→2.96, -2,86€ — quota
  pareggio crollata di oltre il 30% dopo il gol, coerente col pattern già visto il 07/09
  quando il "favorito reale" non è la squadra di casa), Defensores de Belgrano v Los
  Andes (3.1→3.3, +0,55€), Norwich v Birmingham (3.8→4.3, +0,79€, unica partita
  segnalata dall'utente come in target — Championship inglese, fuori dalle 5 leghe
  validate ma quote in range). Tutti green-up, nessuno stop loss (bug ancora attivo per
  tutta la sessione).

- **Da rivalidare al prossimo run**: con entrambi i fix di oggi attivi (login retry +
  minuto corretto), riprovare fino a osservare un vero stop loss al 70'.

---

## 07/09/2026 (sera) — Bug bloccante entrata risolto, primo ciclo LTD completo dal vivo

- **Contesto**: dopo i 4 fix della sessione mattutina (vedi voce sotto), il bot restava
  comunque senza mai entrare a mercato. L'utente ha chiesto di testare l'entrata/uscita
  senza vincoli di selezione, solo per verificare il meccanismo puro.

- **Aggiunta modalità di test permanente `--test-entry`** (`bot/main.py`,
  `bot/screener.py::get_eligible_markets_test`, `LayTheDrawStrategy(skip_criteria_check=...)`):
  ignora tutti i criteri LTD (lega, quote, liquidità min 5.000€ ridotta a 50€), forza
  sempre paper trade anche se `strategies.toml` chiedesse live. Non tocca il funzionamento
  normale del bot, è un percorso a parte attivabile solo da CLI.

- **Primo tentativo fallito per un bug nella query stessa**: `list_market_book` con
  50 marketIds worldwide (nessun filtro lega/paese) → `APINGException TOO_MUCH_DATA`.
  Corretto riducendo `max_results` a 20 in `get_eligible_markets_test`.

- **Bug reale trovato in `strategy_LTD.py::process_market_book`**: `home_id`/`draw_id`
  venivano risolti da `market.market_catalogue` e salvati nello stato **una sola volta**
  (`setdefault`), al primissimo tick dello stream. `market.market_catalogue` è popolato
  in modo asincrono da un worker separato di flumine (~10s dopo la sottoscrizione,
  confermato nei log: "Adding: X to Betfair markets" poi "Created marketCatalogue for X"
  ~9-10s dopo) — se il primo tick arriva prima (probabile, lo stream può emettere
  immediatamente), `draw_id` restava `None` per sempre: il controllo
  `if state["draw_id"] is None: return` scartava silenziosamente ogni tick successivo,
  senza mai più ritentare, niente log neanche diagnostico. Verificato con zero righe
  `DIAG` in ~100 minuti di stream attivo su 3 mercati. **Quasi certamente la vera causa
  di "nessuna entrata mai osservata" anche nelle sessioni del 05-06/09** — non i bug di
  `_get_score`/`_get_minute` risolti stamattina (quelli bloccavano l'uscita, non
  l'entrata). Fix: ritentare la risoluzione a ogni tick finché non riesce, invece di
  bloccarla al primo fallimento (non cachare `None`, solo il risultato valido).

- **Verificato dal vivo, due volte, nella stessa sessione** (dopo un riavvio PC di
  mezzo che ha interrotto il primo run senza perdite, il codice del fix era già salvato
  su disco):
  - `FC Voluntari v Argeș Pitești` (Romanian Liga I): ENTRATA LAY draw @3.05
    (size 9.76€, liability 20€) → GOL (1,0) → USCITA green-up BACK @3.75 →
    **P&L netto +1,73€**. Quota pareggio salita dopo il gol, come da manuale.
  - `Asteras Tripolis v Iraklis` (Greek Super League): ENTRATA LAY draw @3.3
    (size 8.7€, liability 20€) → GOL (0,1, gol dell'ospite) → USCITA green-up
    BACK @3.0 → **P&L netto -0,87€**. Quota pareggio scesa invece di salire —
    plausibile per liquidità pre-match bassissima (369€ contro il minimo 5.000€
    richiesto in produzione) e lega fuori dai 5 campionati validati nel backtest,
    non un problema del bot. Zero commissione applicata (si applica solo sui
    profitti, non sulle perdite — coerente con la regola in CLAUDE.md).
  - Terzo mercato sottoscritto (`Raslavice v MFK Vranov`, Slovak 3. Liga) non è mai
    entrato: già 1-0 quando il mercato è tornato "OPEN" da uno stato "Sospeso"
    (confermato anche visivamente su Betfair.it dall'utente) — comportamento
    corretto, l'entrata richiede 0-0.

- **Non ancora osservato**: lo stop loss al 70' (0-0 persistente, nessun gol) — in
  entrambi i trade osservati il gol è arrivato molto presto (minuto riportato "0'" da
  `get_scores()`, verosimile artefatto di latenza/qualità dati sulle leghi minori,
  non ha comunque influito sull'esito perché l'uscita dipende solo dal cambio
  punteggio). Prossimo checkpoint da verificare.

- **Trade loggati regolarmente** in `logs/trades_ltd.csv` con timestamp, quote
  entrata/uscita, esito, liability, P&L lordo e netto — formato verificato corretto
  a mano (matematica del green-up: `back_size = lay_size * entry_odds / exit_odds`).

---

## 07/09/2026 (mattina) — Primo test paper trading dal vivo: diagnosi e fix di 4 bug bloccanti

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
