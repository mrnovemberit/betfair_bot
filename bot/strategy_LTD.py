"""
Strategia Lay The Draw via Flumine.

Flusso per ogni mercato eleggibile:
  1. Al kick-off → LAY sul pareggio (liability = 2% bankroll)
  2. Gol segnato  → green-up: BACK pareggio alla quota corrente
  3. 0-0 al 70'   → stop loss: BACK pareggio alla quota corrente (perdita parziale)
  4. Ogni trade loggato in logs/trades_ltd.csv
"""

import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from flumine import BaseStrategy
from flumine.order.trade import Trade
from flumine.order.order import LimitOrder, OrderStatus
from flumine.markets.market import Market
from betfairlightweight.resources import MarketBook

from .screener import (
    identify_home_and_draw,
    LTD_HOME_ODDS_MAX,
    LTD_DRAW_ODDS_MIN,
    LTD_DRAW_ODDS_MAX,
)

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

COMMISSION              = 0.05   # 5% Betfair sui profitti netti
LIABILITY_PCT           = 0.02   # 2% del bankroll per trade
STOP_LOSS_MINUTE        = 70
BANKROLL                = 1000.0  # bankroll iniziale simulato (verrà sovrascritto da main.py)
SCORE_POLL_INTERVAL_SEC = 15   # throttle chiamate a InPlayService (non a ogni tick dello stream)

_CSV_HEADER = [
    "timestamp", "market_id", "event_name", "draw_odds_entry",
    "draw_odds_exit", "outcome", "liability", "pnl_gross", "pnl_net",
]


class LayTheDrawStrategy(BaseStrategy):
    """
    Strategia LTD per Flumine.

    Parametri configurabili (passati via __init__):
        bankroll (float): bankroll corrente in €
        paper_trade (bool): se True nessun ordine reale viene inviato
    """

    LOG_NAME = "ltd"   # -> logs/trades_ltd.csv, distinto da eventuali altre strategie

    def __init__(
        self,
        *args,
        bankroll: float = BANKROLL,
        paper_trade: bool = True,
        skip_criteria_check: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.bankroll            = bankroll
        self.paper_trade         = paper_trade
        self.skip_criteria_check = skip_criteria_check   # solo per test manuale entrata/uscita, vedi main.py --test-entry
        self.trades_csv  = LOGS_DIR / f"trades_{self.LOG_NAME}.csv"
        self._trading    = None   # client betfairlightweight, agganciato in start()

        # Stato interno per mercato: market_id → dict
        self._state: dict[str, dict] = {}

    # ── Log trade (namespacizzato per strategia) ───────────────────────────────

    def _ensure_csv_header(self) -> None:
        if not self.trades_csv.exists():
            with open(self.trades_csv, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(_CSV_HEADER)

    def _log_trade(self, row: dict) -> None:
        self._ensure_csv_header()
        with open(self.trades_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([row.get(k, "") for k in _CSV_HEADER])

    # ── Flumine callbacks ────────────────────────────────────────────────────

    def start(self, flumine) -> None:
        logger.info(
            f"LayTheDrawStrategy avviata | bankroll={self.bankroll}€ "
            f"| paper_trade={self.paper_trade}"
        )
        self._trading = flumine.clients.get_default().betting_client
        self._ensure_csv_header()

    def check_market_book(self, market: Market, market_book: MarketBook) -> bool:
        # Processa solo mercati in-play o a kickoff imminente
        return market_book.status == "OPEN"

    def process_market_book(self, market: Market, market_book: MarketBook) -> None:
        mid = market_book.market_id
        home_id, draw_id = self._resolve_runner_ids(market)
        state = self._state.setdefault(mid, {
            "entered":    False,
            "trade":      None,
            "lay_order":  None,
            "entry_odds": None,
            "event_name": market.market_catalogue.event.name if market.market_catalogue else mid,
            "home_id":    home_id,
            "draw_id":    draw_id,
            "closed":     False,
            "_diag_last_inplay": None,
            "_diag_last_log_ts": 0.0,
        })

        # market.market_catalogue è popolato in modo asincrono da flumine (poller
        # separato, secondi dopo la sottoscrizione allo stream) — al primissimo
        # tick può ancora essere None, quindi _resolve_runner_ids() fallisce.
        # Senza questo ritentativo, draw_id restava None per sempre (bloccato dal
        # controllo sotto), ignorando il mercato per l'intera partita: bug
        # riscontrato il 07/09/2026 durante il test manuale --test-entry, zero
        # entrate su 3 mercati nonostante ~100 minuti di stream attivo.
        if state["draw_id"] is None and draw_id is not None:
            state["home_id"] = home_id
            state["draw_id"] = draw_id
            if market.market_catalogue is not None:
                state["event_name"] = market.market_catalogue.event.name

        if state["closed"]:
            return

        if state["draw_id"] is None:
            return

        self._log_diagnostics(market_book, state)

        if not market_book.inplay:
            return

        score, minute = self._get_live_score_and_minute(market, state)

        # ── Entrata: kick-off e non ancora entrati ────────────────────────
        if not state["entered"] and score == (0, 0):
            self._enter(market, market_book, state)

        # ── Gestione posizione aperta ─────────────────────────────────────
        elif state["entered"] and not state["closed"]:
            # Gol segnato → green-up
            if score != (0, 0):
                logger.info(
                    f"[{state['event_name']}] GOL {score} al {minute}' → green-up"
                )
                self._exit(market, market_book, state, reason="greenup")

            # Stop loss al 70'
            elif minute is not None and minute >= STOP_LOSS_MINUTE and score == (0, 0):
                logger.info(
                    f"[{state['event_name']}] 0-0 al {minute}' → stop loss"
                )
                self._exit(market, market_book, state, reason="stop_loss")

    # ── Entrata e uscita ─────────────────────────────────────────────────────

    def _enter(self, market: Market, market_book: MarketBook, state: dict) -> None:
        if not self.skip_criteria_check and not self._recheck_ltd_criteria(market_book, state):
            state["closed"] = True   # criteri non più validi al kick-off, non ritentare
            return

        draw_runner = self._get_runner(market_book, state["draw_id"])
        if draw_runner is None:
            return

        draw_odds = self._best_lay_price(draw_runner)
        if draw_odds is None:
            logger.warning(f"[{state['event_name']}] nessuna quota lay disponibile")
            return

        liability = self.bankroll * LIABILITY_PCT
        lay_size  = round(liability / (draw_odds - 1), 2)

        logger.info(
            f"[{state['event_name']}] ENTRATA → LAY draw "
            f"@ {draw_odds} | size={lay_size}€ | liability={liability}€"
        )

        trade = Trade(
            market_id=market_book.market_id,
            selection_id=state["draw_id"],
            handicap=0,
            strategy=self,
        )
        lay_order = trade.create_order(
            side="LAY",
            order_type=LimitOrder(price=draw_odds, size=lay_size),
        )

        if not self.paper_trade:
            market.place_order(lay_order)
        else:
            # In paper trading simuliamo l'esecuzione immediata
            logger.info(f"[{state['event_name']}] [PAPER] LAY simulato @ {draw_odds}")

        state["entered"]    = True
        state["trade"]      = trade
        state["lay_order"]  = lay_order
        state["entry_odds"] = draw_odds
        state["lay_size"]   = lay_size
        state["liability"]  = liability

    def _exit(self, market: Market, market_book: MarketBook, state: dict, reason: str) -> None:
        draw_runner = self._get_runner(market_book, state["draw_id"])
        if draw_runner is None:
            return

        exit_odds = self._best_back_price(draw_runner)
        if exit_odds is None:
            logger.warning(f"[{state['event_name']}] nessuna quota back per chiusura")
            return

        entry_odds = state["entry_odds"]
        lay_size   = state["lay_size"]
        liability  = state["liability"]

        # Calcolo P&L
        back_size  = round(lay_size * entry_odds / exit_odds, 2)
        pnl_gross  = lay_size - back_size
        commission = max(pnl_gross, 0) * COMMISSION
        pnl_net    = round(pnl_gross - commission, 4)

        logger.info(
            f"[{state['event_name']}] USCITA ({reason}) → BACK draw "
            f"@ {exit_odds} | P&L netto={pnl_net:+.2f}€"
        )

        if not self.paper_trade and state["trade"] is not None:
            back_order = state["trade"].create_order(
                side="BACK",
                order_type=LimitOrder(price=exit_odds, size=back_size),
            )
            market.place_order(back_order)
        else:
            logger.info(f"[{state['event_name']}] [PAPER] BACK simulato @ {exit_odds}")

        self._log_trade({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "market_id":       market_book.market_id,
            "event_name":      state["event_name"],
            "draw_odds_entry": entry_odds,
            "draw_odds_exit":  exit_odds,
            "outcome":         reason,
            "liability":       liability,
            "pnl_gross":       round(pnl_gross, 4),
            "pnl_net":         pnl_net,
        })

        state["closed"] = True

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_runner_ids(market: Market) -> tuple[int | None, int | None]:
        """
        Identifica (home_id, draw_id) riusando identify_home_and_draw() di
        screener.py — stessa logica già validata lì (pareggio per NOME 'The
        Draw', mai per posizione; casa per sort_priority). Se la catalogue
        non è ancora disponibile, ritorna (None, None): fail-safe, nessuna
        azione finché non si può identificare con certezza.
        """
        if market.market_catalogue is None:
            return None, None
        ids = identify_home_and_draw(market.market_catalogue.runners)
        if ids is None:
            return None, None
        return ids

    def _recheck_ltd_criteria(self, market_book: MarketBook, state: dict) -> bool:
        """
        Ricontrolla i criteri di selezione LTD (home odds, draw odds) subito
        prima dell'entrata. Lo screener li valida una sola volta, ore prima
        del kick-off (hours_ahead fino a 20h) — se nel frattempo le quote si
        sono mosse (notizie di formazione, infortuni) il mercato potrebbe non
        essere più in target. Stesse soglie e stesso prezzo (best back) usati
        dallo screener, per coerenza.
        """
        home_runner = (
            self._get_runner(market_book, state["home_id"])
            if state.get("home_id") is not None else None
        )
        draw_runner = self._get_runner(market_book, state["draw_id"])

        home_odds = self._best_back_price(home_runner) if home_runner else None
        draw_odds = self._best_back_price(draw_runner) if draw_runner else None

        if home_odds is None or draw_odds is None:
            logger.warning(
                f"[{state['event_name']}] ricontrollo entrata: quote non disponibili, skip"
            )
            return False

        if home_odds >= LTD_HOME_ODDS_MAX:
            logger.info(
                f"[{state['event_name']}] entrata annullata al kick-off: "
                f"home_odds={home_odds} >= {LTD_HOME_ODDS_MAX} (era eleggibile allo screening)"
            )
            return False

        if not (LTD_DRAW_ODDS_MIN <= draw_odds <= LTD_DRAW_ODDS_MAX):
            logger.info(
                f"[{state['event_name']}] entrata annullata al kick-off: "
                f"draw_odds={draw_odds} fuori range [{LTD_DRAW_ODDS_MIN}, {LTD_DRAW_ODDS_MAX}]"
            )
            return False

        return True

    @staticmethod
    def _get_runner(market_book: MarketBook, selection_id: int):
        for r in market_book.runners:
            if r.selection_id == selection_id:
                return r
        return None

    @staticmethod
    def _price(price_size) -> float | None:
        """
        Estrae 'price' da una entry di available_to_back/lay. flumine monkey-patcha
        RunnerBookEX (flumine.patching.EX) per ottimizzazione: essendo sempre
        importato in questo processo, available_to_back/lay sono liste di dict
        {'price', 'size'}, non oggetti PriceSize con attributo .price.
        """
        if price_size is None:
            return None
        if isinstance(price_size, dict):
            return price_size.get("price")
        return getattr(price_size, "price", None)

    @staticmethod
    def _best_lay_price(runner) -> float | None:
        avail = runner.ex.available_to_lay if runner.ex else []
        return LayTheDrawStrategy._price(avail[0]) if avail else None

    @staticmethod
    def _best_back_price(runner) -> float | None:
        avail = runner.ex.available_to_back if runner.ex else []
        return LayTheDrawStrategy._price(avail[0]) if avail else None

    @staticmethod
    def _log_diagnostics(market_book: MarketBook, state: dict) -> None:
        """
        Diagnostica temporanea per capire perché l'entrata non è mai scattata
        il 2026-09-05 (Inter-Napoli, Roma-Atalanta) né il 2026-09-06
        (Arsenal-Chelsea) nonostante sessione sana per l'intera partita.
        Logga ogni transizione di market_book.inplay/status e un heartbeat
        ogni 60s finché non si è entrati — da rimuovere una volta capita
        la causa.
        """
        now = time.monotonic()
        inplay = market_book.inplay
        if inplay != state["_diag_last_inplay"]:
            logger.info(
                f"[{state['event_name']}] DIAG inplay {state['_diag_last_inplay']} → {inplay} "
                f"| status={market_book.status}"
            )
            state["_diag_last_inplay"] = inplay
            state["_diag_last_log_ts"] = now
        elif now - state["_diag_last_log_ts"] > 60:
            logger.info(
                f"[{state['event_name']}] DIAG heartbeat inplay={inplay} status={market_book.status}"
            )
            state["_diag_last_log_ts"] = now

    def _get_live_score_and_minute(
        self, market: Market, state: dict
    ) -> tuple[tuple[int, int], int | None]:
        """
        Score e minuto reali via Betfair InPlayService (`trading.in_play_service.get_scores`).

        Non derivabili dallo stream Exchange standard: market_book/market_definition
        non contengono alcun dato di punteggio (verificato su betfairlightweight
        installato — nessun attributo match_stat/regulationTime esiste). Risultato
        cachato per market_id (SCORE_POLL_INTERVAL_SEC) per non interrogare
        l'endpoint a ogni tick dello stream.

        Il minuto viene letto da `result.full_time_elapsed` (hour/min), non da
        `result.time_elapsed_seconds`: quest'ultimo è il tempo del **periodo
        corrente** (si azzera all'intervallo — stessa famiglia di
        `elapsed_regular_time`/`elapsed_added_time`, non cumulativo sull'intera
        partita), mentre `full_time_elapsed` è un campo separato pensato per il
        minuto di gioco complessivo (verificato su
        betfairlightweight/resources/inplayserviceresources.py::Scores). Bug
        trovato il 09/09/2026 su Rangers v St Mirren: con `time_elapsed_seconds`
        lo stop loss al 70' non scattava mai nel secondo tempo (un gol o un 0-0
        al minuto reale 84' veniva visto dal bot come "minuto ~39", cioè
        84' meno la durata del primo tempo) — nessun errore, nessun log,
        semplicemente il trigger `minute >= STOP_LOSS_MINUTE` non si verificava
        mai. Score confermato corretto in entrambi i casi (0-0 reale = 0-0
        letto), solo il minuto era sbagliato.
        """
        cache = state.get("_score_cache")
        now = time.monotonic()
        if cache and (now - cache["ts"]) < SCORE_POLL_INTERVAL_SEC:
            return cache["score"], cache["minute"]

        fallback_score  = cache["score"] if cache else (0, 0)
        fallback_minute = cache["minute"] if cache else None

        event_id = self._get_event_id(market)
        if self._trading is None or event_id is None:
            return fallback_score, fallback_minute

        try:
            scores = self._trading.in_play_service.get_scores(event_ids=[event_id])
        except Exception as e:
            logger.warning(f"[{state['event_name']}] errore recupero score live: {e}")
            return fallback_score, fallback_minute

        if not scores:
            return fallback_score, fallback_minute

        result = scores[0]
        try:
            home = int(result.score.home.score or 0)
            away = int(result.score.away.score or 0)
        except (AttributeError, TypeError, ValueError):
            home, away = fallback_score

        minute = None
        fte = result.full_time_elapsed
        if fte is not None and fte.hour is not None and fte.min is not None:
            minute = fte.hour * 60 + fte.min

        state["_score_cache"] = {"ts": now, "score": (home, away), "minute": minute}
        return (home, away), minute

    @staticmethod
    def _get_event_id(market: Market) -> int | None:
        if market.market_catalogue is None:
            return None
        return market.market_catalogue.event.id
