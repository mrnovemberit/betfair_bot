"""
Screener pre-partita LTD.

Interroga Betfair per le partite in programma nelle prossime ore e restituisce
i market_id eleggibili per la strategia Lay The Draw.

Criteri di selezione (da CLAUDE.md):
  - Lega: Premier League, La Liga, Serie A, Bundesliga, Ligue 1
  - Quota casa (back) < 2.0
  - Quota pareggio tra 3.2 e 4.5
  - Liquidità mercato Match Odds > MIN_LIQUIDITY
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import betfairlightweight
from betfairlightweight import filters

logger = logging.getLogger(__name__)

# Betfair competition IDs per i 5 campionati top
COMPETITION_IDS = {
    "Premier League": "10932509",
    "La Liga":        "117",
    "Serie A":        "81",
    "Bundesliga":     "59",
    "Ligue 1":        "55",
}

DRAW_ODDS_MIN   = 3.2
DRAW_ODDS_MAX   = 4.5
HOME_ODDS_MAX   = 2.0
MIN_LIQUIDITY   = 5000.0   # € matched minimi sul mercato Match Odds
HOURS_AHEAD     = 6        # partite nelle prossime N ore


def get_eligible_markets(
    client: betfairlightweight.APIClient,
    hours_ahead: int = HOURS_AHEAD,
) -> list[dict]:
    """
    Restituisce una lista di dizionari con i dati delle partite eleggibili.

    Ogni dict contiene:
        market_id, event_name, competition, kick_off,
        home_odds, draw_odds, away_odds, matched
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours_ahead)

    market_filter = filters.market_filter(
        event_type_ids=["1"],  # calcio
        competition_ids=list(COMPETITION_IDS.values()),
        market_countries=["GB", "ES", "IT", "DE", "FR"],
        market_type_codes=["MATCH_ODDS"],
        market_start_time={
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to":   window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        in_play_only=False,
    )

    try:
        catalogues = client.betting.list_market_catalogue(
            filter=market_filter,
            market_projection=["COMPETITION", "EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
            max_results=50,
        )
    except Exception as e:
        logger.error(f"list_market_catalogue fallito: {e}")
        return []

    if not catalogues:
        logger.info("Nessun mercato trovato nella finestra temporale")
        return []

    market_ids = [c.market_id for c in catalogues]

    try:
        books = client.betting.list_market_book(
            market_ids=market_ids,
            price_projection=filters.price_projection(
                price_data=["EX_BEST_OFFERS"],
            ),
        )
    except Exception as e:
        logger.error(f"list_market_book fallito: {e}")
        return []

    book_map = {b.market_id: b for b in books}

    eligible = []
    for cat in catalogues:
        book = book_map.get(cat.market_id)
        if book is None:
            continue

        matched = book.total_matched or 0.0
        if matched < MIN_LIQUIDITY:
            logger.debug(f"  skip {cat.market_id}: liquidità {matched:.0f} < {MIN_LIQUIDITY}")
            continue

        runners = cat.runners  # [home, draw, away] in ordine sort_priority
        if not runners or len(runners) < 3:
            continue

        # Prezzi best back per ciascun runner
        def best_back(runner_id: int) -> Optional[float]:
            for r in book.runners:
                if r.selection_id == runner_id:
                    avail = r.ex.available_to_back if r.ex else []
                    return avail[0].price if avail else None
            return None

        runners_sorted = sorted(runners, key=lambda r: r.sort_priority)
        home_id  = runners_sorted[0].selection_id
        draw_id  = runners_sorted[1].selection_id

        home_odds = best_back(home_id)
        draw_odds = best_back(draw_id)

        if home_odds is None or draw_odds is None:
            continue

        if home_odds >= HOME_ODDS_MAX:
            logger.debug(f"  skip {cat.event.name}: home_odds={home_odds} >= {HOME_ODDS_MAX}")
            continue

        if not (DRAW_ODDS_MIN <= draw_odds <= DRAW_ODDS_MAX):
            logger.debug(
                f"  skip {cat.event.name}: draw_odds={draw_odds} "
                f"fuori range [{DRAW_ODDS_MIN}, {DRAW_ODDS_MAX}]"
            )
            continue

        competition_name = cat.competition.name if cat.competition else "N/A"
        eligible.append({
            "market_id":   cat.market_id,
            "event_name":  cat.event.name,
            "competition": competition_name,
            "kick_off":    cat.market_start_time,
            "home_id":     home_id,
            "draw_id":     draw_id,
            "home_odds":   home_odds,
            "draw_odds":   draw_odds,
            "matched":     matched,
        })
        logger.info(
            f"  [OK] {cat.event.name} | draw={draw_odds} | home={home_odds} "
            f"| matched={matched:.0f}€"
        )

    return eligible
