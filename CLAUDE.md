# CLAUDE.md — Betfair Football Trading System

## Contesto del progetto

Sistema di trading sportivo automatizzato su **Betfair Exchange** focalizzato sul calcio.
Parte di un portfolio multi-track di reddito semi-passivo. Il progetto segue una roadmap
sequenziale: backtest → paper trading → live micro-scale → scale-up.

**Obiettivo finanziario**: €100–300/mese con bankroll iniziale €500–1.000  
**Vincolo operativo**: sistema deve girare autonomamente (turni da pompiere, nessuna supervisione attiva)  
**Broker/Exchange**: Betfair Exchange (.it) — licenza ADM Italia, API ufficiale disponibile  

---

## Strategie implementate (in ordine di priorità)

### 1. Lay The Draw (LTD) — PRIORITÀ PRINCIPALE
- **Logica**: LAY sul pareggio pre-match → gol segnato → quote pareggio salgono → BACK per chiudere (green-up)
- **Edge**: ~92% delle partite ha almeno un gol. Non serve prevedere il vincitore
- **Criteri selezione**:
  - Squadra di casa favorita (quota match odds < 2.0)
  - Quota pareggio tra 3.2 e 4.5
  - Meno del 20% di 0-0 nelle ultime 10 partite delle due squadre
  - Campionati: Premier League, La Liga, Serie A, Bundesliga, Ligue 1
  - xG elevati per entrambe le squadre
- **Gestione rischio**:
  - Stop loss temporale: chiusura in perdita parziale se ancora 0-0 al 70°
  - Liability max: 2–3% del bankroll per trade
  - Opzionale: back sullo 0-0 come copertura parziale
- **Automazione target**: 100% — il bot piazza il lay, monitora il live score, fa green-up automatico dopo il gol

### 2. Scalping Under/Over 2.5 Goals (Time Decay) — SECONDA PRIORITÀ
- **Logica**: In una partita 0-0, le quote Under 2.5 scendono per time decay. BACK Under ad alta quota (min 15–25), LAY Under 10–15 minuti dopo
- **Edge**: Basato sulla fisica del mercato, non su previsioni
- **Criteri selezione**:
  - Partita ancora 0-0 tra il 15° e il 25° minuto
  - Liquidità mercato O/U > €5.000 matched
  - Quota Under 2.5 tra 1.50 e 1.80 all'entrata
- **Exit**: automatico se gol segnato (stop loss immediato) o dopo 10–15 min
- **Automazione target**: 100% — la più semplice da automatizzare (trigger puramente time/score-based)

### 3. Pre-Match Swing — FASE FUTURA
- Sfrutta movimenti quote pre-kick-off (formazioni, infortuni)
- Automazione solo parziale — richiede valutazione umana iniziale
- Da attivare solo dopo validazione delle prime due

---

## Stack tecnico

```
Linguaggio:     Python 3.11+
Ambiente:       VS Code locale, Windows, RTX 3060
Librerie core:  betfairlightweight, Flumine
Dati match:     football-data.co.uk (risultati + quote O/U storiche, CSV gratuiti)
Dati gol:       football-data.org REST API (minuti esatti dei gol, gratuita)
Live score:     Betfair API in-play (score aggiornato via stream)
Infrastruttura: mini-PC Linux domestico (Ubuntu 24.04, Docker, restart: unless-stopped) — vedi docs/deploy_server.md
Tracking:       Google Sheets o CSV locale (P&L, win rate, Sharpe/Sortino)
```

### Librerie Python richieste
```
betfairlightweight>=2.18.0   # wrapper Betfair API-NG
flumine>=3.0.0               # framework esecuzione strategie
pandas>=2.0.0                # solo per backtest/*.py, non usato dal bot live
requests                     # football-data.org API
python-dotenv                # gestione credenziali
```
`requirements.txt` (root) = set completo per sviluppo/backtest in locale.
`deploy/requirements-bot.txt` = solo runtime del bot (niente pandas/numpy), pinnato
esatto, usato dall'immagine Docker — vedi `docs/deploy_server.md`.

---

## Struttura del progetto

```
betfair-football/
│
├── CLAUDE.md                    # questo file
├── .env                         # credenziali Betfair (NON committare)
├── .env.example
├── requirements.txt              # dipendenze dev/backtest (pandas incluso)
├── strategies.toml               # ON/OFF/paper/live per strategia — unico file da
│                                  # toccare per accendere/spegnere/aggiungere una strategia
├── .dockerignore
│
├── data/
│   ├── raw/                     # CSV da football-data.co.uk
│   └── processed/               # dati normalizzati per backtest
│
├── backtest/
│   ├── 01_fetch_match_data.py   # scarica risultati + quote storiche
│   ├── 02_fetch_goal_minutes.py # recupera minuti gol da football-data.org
│   ├── 03_merge_dataset.py      # unisce i due dataset
│   ├── 04_backtest_LTD.py       # logica backtest LTD
│   ├── 05_stats_report.py       # calcola metriche (win rate, Sharpe, Sortino, expectancy)
│   ├── results/                 # output backtest completi (CSV trade simulati, metriche finali)
│   └── tests/                   # run di validazione su campioni ridotti, prove script
│
├── bot/
│   ├── screener.py              # fetch generico + get_eligible_markets_ltd()
│   ├── strategy_LTD.py          # strategia LTD via Flumine (log namespacizzato)
│   ├── strategy_scalping.py     # strategia scalping O/U 2.5 — non ancora scritta
│   └── main.py                  # entry point: login cert-based, registry strategie, avvio Flumine
│
├── deploy/                       # containerizzazione (deploy sul server domestico)
│   ├── Dockerfile                # solo dipendenze runtime, niente codice (bind mount)
│   ├── requirements-bot.txt      # dipendenze pinnate, runtime-only (niente pandas/numpy)
│   ├── docker-compose.yml        # 1 servizio long-running, rete isolata "betfair"
│   └── .env.example              # variabili Docker Compose (PUID/GID, TZ, LIVE, mem limit)
│
├── docs/
│   ├── deploy_server.md          # runbook passo-passo per il deploy sul server
│   └── systemd/                  # unit scritte a mano: riavvio giornaliero screener (08:00)
│       ├── betfair-bot-restart.service
│       └── betfair-bot-restart.timer
│
└── logs/
    └── trades_ltd.csv            # log trade LTD (una futura strategia avrà il proprio CSV)
```

---

## Fase attuale: DEPLOY DOCKER SUL SERVER DOMESTICO (aggiornato 28/08/2026)

### Completato
- **[Containerizzazione + deploy server domestico]** il 28/08/2026: bot spostato dal piano "VPS Hetzner" (mai eseguito) a un mini-PC Linux domestico già in produzione per altri 3 progetti Docker indipendenti (videosorveglianza, n8n, Interactive Brokers). Corretti due bug bloccanti mai emersi prima (verificati leggendo il sorgente `flumine==3.1.0` installato, non ipotizzati): `market_filter` non è più accettato direttamente da `BaseStrategy` (va incapsulato in un `BetfairMarketStream`), e `SimulatedClient` non può ricevere dati live (va usato `BetfairClient(paper_trade=True)`) — il bot non aveva **mai** completato un run end-to-end fino ad ora. Sostituito `login_interactive()` con login non-interattivo via certificato (`trading.login()`, certificati già presenti in `certs/` ma mai usati). Aggiunto `strategies.toml` (tri-stato off/paper/live per strategia, stesso concetto del sistema IB gemello) e registry strategie in `bot/main.py`. Log trade namespacizzati per strategia (`logs/trades_ltd.csv`). Creati `deploy/` (Dockerfile, compose, requirements pinnati) e `docs/deploy_server.md` (runbook). Screener gira una sola volta all'avvio (Flumine non supporta re-screening nativo) — mitigato con `hours_ahead=20` + riavvio giornaliero schedulato alle 08:00 via systemd timer (`docs/systemd/`), stesso pattern già collaudato per il Gateway IB su questo server. Deploy vero e proprio sul server non ancora eseguito (richiede un utente con permessi docker/sudo diverso da quello SSH già configurato, confinato a `/opt/trading`).
- **[App Key sbloccata + screener validato]** il 26/05/2026: App Key creata con nome univoco `LTDBot_mnera_2026` (il nome "BetfairBot" non era globalmente unico). Delayed key attiva, Live key creata ma non ancora attiva — valori memorizzati in `.env` (`BETFAIR_APP_KEY` e `BETFAIR_APP_KEY_LIVE`, non committati). Screener testato su mercati reali: login OK, zero errori API. DNS issue intermittente risolto con `ipconfig /flushdns`.
- **[Debug endpoint API + ticket Developer Support]** il 26/05/2026: confermato che API Exchange betfair.it è disponibile per uso personale. Identificato e corretto bug endpoint: `api.betfair.it` → `api.betfair.com` in `check_account.py` e `create_app_key.py`. Confermato che creazione self-service App Key è bloccata lato server. Aperto ticket al Betfair Developer Support per provisioning manuale. Creato venv Python con dipendenze.
- **[Bot LTD costruito]** il 15/05/2026: scritti `bot/screener.py`, `bot/strategy_LTD.py`, `bot/main.py` con logica completa (green-up automatico + stop loss al 70').
- **[Backtest LTD completato]** in sessione precedente: 4.037 trade simulati su 5 campionati 2015-2024. Win rate 85%, expectancy +1.96€/trade, Sortino 201, max drawdown 2.1%. Verdetto: PROCEDI AL PAPER TRADING.
- **[Pipeline backtest completata]**: tutti e 5 gli script (`01_fetch` → `05_stats_report`) eseguiti e funzionanti. Report e equity curve disponibili in `backtest/results/`.

### Blocco attuale
- **Live App Key** (`BETFAIR_APP_KEY_LIVE` in `.env`) ancora inattiva — verificato via API il 28/08/2026 (`active: False`), da attivare quando pronti per il live trading. Non blocca il paper trading (usa la Delayed key).
- Stagioni club 2026/27 ripartite (fine agosto) — non più un blocco.

### Prossimo step
1. Verificare in locale via Docker (vedi "Verifica end-to-end" in `docs/deploy_server.md`), poi eseguire il deploy vero e proprio sul server domestico (richiede permessi docker/sudo — vedi `docs/deploy_server.md`)
2. Avvio paper trading ≥4 settimane (in locale e/o sul server una volta deployato)
3. Backtest scalping O/U 2.5 (seconda strategia — dati già disponibili)
4. Attivare Live App Key quando pronti per il live trading

### Dataset target
- **Campionati**: Premier League, La Liga, Serie A, Bundesliga, Ligue 1
- **Periodo storico**: 2015–2024 (stagioni complete)
- **Fonte risultati + quote**: `football-data.co.uk` — file CSV per stagione/campionato
- **Fonte minuti gol**: `football-data.org` API REST (endpoint `/competitions/{id}/matches`)

### Logica backtest LTD (pseudocodice)
```
Per ogni partita nel dataset:
  1. Filtra per criteri di selezione (quota casa, quota X, %0-0)
  2. Simula LAY sul pareggio alla quota X pre-match
  3. Se gol segnato prima del 70°:
       → simula BACK al pareggio alla quota post-gol
       → calcola P&L (considerare commissione Betfair 5%)
  4. Se ancora 0-0 al 70°:
       → simula chiusura forzata (stop loss parziale)
  5. Se partita finisce 0-0:
       → registra perdita piena (liability)
  6. Aggrega: win rate, expectancy, Sharpe, Sortino, max drawdown
```

### Metriche di validazione richieste
- **Win rate** > 70% per procedere al paper trading
- **Expectancy** > 0 per ogni configurazione testata
- **Sortino ratio** > 1.0 (preferito a Sharpe per la struttura asimmetrica SL/TP)
- **Max drawdown** < 20% del bankroll simulato
- **Numero trade**: minimo 200 per significatività statistica

---

## Credenziali e configurazione

File `.env` richiesto (non incluso nel repo, vedi `.env.example`):
```
BETFAIR_USERNAME=xxx
BETFAIR_PASSWORD=xxx
BETFAIR_APP_KEY=xxx        # Delayed key
BETFAIR_APP_KEY_LIVE=xxx   # Live key — attivazione manuale richiesta lato Betfair
FOOTBALL_DATA_API_KEY=xxx  # da api.football-data.org (tier gratuito), opzionale per il bot
BETFAIR_CERT_PATH=/app/certs/client-2048.crt   # path container (via Docker, locale o server)
BETFAIR_KEY_PATH=/app/certs/client-2048.key
```

Login non-interattivo via certificato **implementato** (`bot/main.py::build_client`,
`trading.login()` con `cert_files=`) — non più `login_interactive()`. Certificati già
generati in `certs/` (gitignored):
- `client-2048.crt` e `client-2048.key`
- Generati seguendo: https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Non-Interactive+login

---

## Regole operative (filosofia del progetto)

1. **Valida prima di scalare** — nessun live trading prima di 200+ trade nel backtest con metriche positive
2. **Paper trading obbligatorio** — almeno 4 settimane di simulazione prima del live
3. **Un pezzo alla volta** — LTD prima, scalping O/U solo dopo validazione LTD
4. **Automazione non negoziabile** — ogni strategia deve girare senza supervisione attiva
5. **Liability max 2–3% per trade** — regola rigida, non bypassare
6. **Logga tutto** — ogni trade (simulato o live) va in `logs/trades_<strategia>.csv` (es. `trades_ltd.csv`) con timestamp, quote, esito, P&L
7. **Commissione sempre inclusa** — calcola sempre il 5% Betfair sui profitti netti nei P&L

---

## Prossimi step immediati

- [x] Completare `01_fetch_match_data.py` — ✅ eseguito, 45 CSV scaricati
- [x] Completare `02_fetch_goal_minutes.py` — ✅ eseguito, 5 file goals 2023
- [x] Completare `03_merge_dataset.py` — ✅ eseguito, `ltd_dataset.csv` generato
- [x] Implementare `04_backtest_LTD.py` — ✅ eseguito, 4.037 trade simulati
- [x] Generare report metriche con `05_stats_report.py` — ✅ win rate 85%, Sortino 201
- [x] Costruire bot paper trading (`screener.py`, `strategy_LTD.py`, `main.py`) — ✅ completato
- [x] **Sblocco App Key** via supporto Betfair Italy (chat live) — ✅ 26/05/2026
- [x] Test screener su mercati reali Betfair — ✅ 26/05/2026
- [x] Containerizzazione Docker + fix bug bloccanti (mai girato end-to-end prima) — ✅ 28/08/2026
- [ ] Verifica end-to-end in locale via Docker (vedi `docs/deploy_server.md`)
- [ ] Deploy sul server domestico (richiede permessi docker/sudo sul server)
- [ ] Avvio paper trading ≥4 settimane: `docker compose -f deploy/docker-compose.yml up -d bot`

---

## Riferimenti utili

- **football-data.co.uk**: https://www.football-data.co.uk/data.php (colonne: B365H, B365D, B365A per quote Bet365)
- **football-data.org API docs**: https://docs.football-data.org/general/v4/index.html
- **betfairlightweight docs**: https://github.com/liampauling/betfair
- **Flumine docs**: https://github.com/liampauling/flumine
- **Betfair API docs**: https://docs.developer.betfair.com
- **Betfair Historic Data (per backtest quote exchange)**: https://historicdata.betfair.com
- **Caan Berry (strategia LTD)**: https://caanberry.com/tennis-trading-strategies/
- **Goal Profits (LTD avanzato)**: https://www.goalprofits.com

---

## Note su Betfair Italia

- Betfair.it opera con **licenza ADM** — completamente legale in Italia
- **Commissione standard**: 5% sui profitti netti (non sullo stake)
- **Premium Charge**: applicata ai trader molto profittevoli (>€250k lifetime profit) — non rilevante nella fase iniziale
- **BetFlag Exchange**: alternativa italiana senza Premium Charge, meno liquido
- Profitti da trading sportivo soggetti a tassazione italiana — gestione fiscale autonoma
