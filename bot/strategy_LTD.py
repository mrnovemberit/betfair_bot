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
from datetime import datetime, timezone
from pathlib import Path

from flumine import BaseStrategy
from flumine.order.trade import Trade
from flumine.order.order import LimitOrder, OrderStatus
from flumine.markets.market import Market
from betfairlightweight.resources import MarketBook

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

COMMISSION       = 0.05   # 5% Betfair sui profitti netti
LIABILITY_PCT    = 0.02   # 2% del bankroll per trade
STOP_LOSS_MINUTE = 70
BANKROLL         = 1000.0  # bankroll iniziale simulato (verrà sovrascritto da main.py)

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

    def __init__(self, *args, bankroll: float = BANKROLL, paper_trade: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.bankroll    = bankroll
        self.paper_trade = paper_trade
        self.trades_csv  = LOGS_DIR / f"trades_{self.LOG_NAME}.csv"

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
        self._ensure_csv_header()

    def check_market_book(self, market: Market, market_book: MarketBook) -> bool:
        # Processa solo mercati in-play o a kickoff imminente
        return market_book.status == "OPEN"

    def process_market_book(self, market: Market, market_book: MarketBook) -> None:
        mid = market_book.market_id
        state = self._state.setdefault(mid, {
            "entered":    False,
            "trade":      None,
            "lay_order":  None,
            "entry_odds": None,
            "event_name": market.market_catalogue.event.name if market.market_catalogue else mid,
            "draw_id":    self._get_draw_runner_id(market),
            "closed":     False,
        })

        if state["closed"]:
            return

        if state["draw_id"] is None:
            return

        score = self._get_score(market_book)
        minute = self._get_minute(market_book)
        in_play = market_book.inplay

        # ── Entrata: kick-off e non ancora entrati ────────────────────────
        if in_play and not state["entered"] and score == (0, 0):
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
    def _get_draw_runner_id(market: Market) -> int | None:
        """
        Identifica il selection_id del pareggio per NOME ('The Draw'), mai per
        posizione. RunnerBook (i dati di market_book) non include runner_name —
        serve market.market_catalogue.runners, già in cache in Flumine.

        Bug corretto il 28/08/2026: la versione precedente usava
        sorted(market_book.runners)[1], assumendo sort_priority
        [Casa, Pareggio, Trasferta]. Su Betfair per il calcio è invece
        [Casa, Trasferta, Pareggio] — con la posizione, il bot avrebbe fatto
        LAY/BACK sulla squadra ospite invece che sul pareggio. Verificato su
        dati reali (Milan-Venezia: sort_priority 1=Milan, 2=Venezia,
        3='The Draw'). Se la catalogue non è ancora disponibile, ritorna None
        (fail-safe: nessuna azione finché non si può identificare con certezza,
        invece di operare sulla selezione sbagliata).
        """
        if market.market_catalogue is None:
            return None
        for r in market.market_catalogue.runners:
            if r.runner_name == "The Draw":
                return r.selection_id
        return None

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
    def _get_score(market_book: MarketBook) -> tuple[int, int]:
        try:
            score = market_book.market_definition.match_stat
            # Betfair espone lo score nel market_definition
            home = score.home_score if score else 0
            away = score.away_score if score else 0
            return (int(home or 0), int(away or 0))
        except Exception:
            return (0, 0)

    @staticmethod
    def _get_minute(market_book: MarketBook) -> int | None:
        try:
            return market_book.market_definition.regulationTime
        except Exception:
            return None
