"""
Screener pre-partita.

Fetch generico di mercati Betfair su una finestra oraria (con filtro di liquidità),
più la selezione dei criteri specifici per ciascuna strategia.

LTD (Lay The Draw) — criteri di selezione (da CLAUDE.md):
  - Lega: Premier League, La Liga, Serie A, Bundesliga, Ligue 1
  - Quota casa (back) < 2.0
  - Quota pareggio tra 3.2 e 4.5
  - Liquidità mercato Match Odds > LTD_MIN_LIQUIDITY
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import betfairlightweight
from betfairlightweight import filters

logger = logging.getLogger(__name__)

# Betfair competition IDs per i 5 campionati top
LTD_COMPETITION_IDS = {
    "Premier League": "10932509",
    "La Liga":        "117",
    "Serie A":        "81",
    "Bundesliga":     "59",
    "Ligue 1":        "55",
}

LTD_DRAW_ODDS_MIN = 3.2
LTD_DRAW_ODDS_MAX = 4.5
LTD_HOME_ODDS_MAX = 2.0
LTD_MIN_LIQUIDITY = 5000.0   # € matched minimi sul mercato Match Odds
LTD_HOURS_AHEAD   = 6        # default se non specificato in strategies.toml


def identify_home_and_draw(runners: list) -> Optional[tuple]:
    """
    Identifica (home_id, draw_id) per un mercato MATCH_ODDS.

    Il pareggio va cercato per NOME ('The Draw'), MAI per posizione: su Betfair
    il sort_priority per il calcio è [Casa, Trasferta, Pareggio], non
    [Casa, Pareggio, Trasferta] come ci si potrebbe aspettare — verificato su
    dati reali il 28/08/2026 (Milan-Venezia: sort_priority 1=Milan, 2=Venezia,
    3='The Draw'). Usare la posizione per il pareggio significa scambiarlo con
    la squadra ospite: bug presente nella versione originale di questo file,
    che avrebbe fatto operare qualunque strategia sulla squadra ospite invece
    che sul pareggio.

    La squadra di casa resta identificata per posizione (sort_priority=1):
    convenzione Betfair stabile, il runner di casa compare sempre per primo,
    coerente con l'ordine "Casa v Trasferta" nel nome evento.
    """
    if not runners or len(runners) < 3:
        return None
    runners_sorted = sorted(runners, key=lambda r: r.sort_priority)
    draw = next((r for r in runners_sorted if r.runner_name == "The Draw"), None)
    if draw is None:
        return None
    home = runners_sorted[0]
    if home.selection_id == draw.selection_id:
        return None
    return home.selection_id, draw.selection_id


def _price(price_size) -> Optional[float]:
    """
    Estrae il campo 'price' da una entry di available_to_back/available_to_lay.

    flumine monkey-patcha RunnerBookEX (flumine.patching.EX) per ottimizzazione:
    se flumine è importato nello stesso processo (sempre il caso in bot/main.py,
    mai in test_screener.py), available_to_back/lay diventano liste di dict
    {'price', 'size'} invece di oggetti PriceSize con attributo .price — stessa
    chiamata API, struttura dati diversa a seconda di cosa è stato importato.
    """
    if price_size is None:
        return None
    if isinstance(price_size, dict):
        return price_size.get("price")
    return getattr(price_size, "price", None)


def _fetch_catalogues_and_books(
    client: betfairlightweight.APIClient,
    *,
    event_type_ids: list[str],
    competition_ids: Optional[list[str]],
    market_countries: Optional[list[str]],
    market_type_codes: list[str],
    hours_ahead: int,
    min_liquidity: float,
    max_results: int = 50,
) -> list[tuple]:
    """
    Fetch generico: catalogo + book Betfair filtrati per finestra oraria e
    liquidità minima. Nessuna logica di selezione prezzo (specifica per strategia).

    Ritorna una lista di tuple (catalogue, book).
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours_ahead)

    market_filter = filters.market_filter(
        event_type_ids=event_type_ids,
        competition_ids=competition_ids,
        market_countries=market_countries,
        market_type_codes=market_type_codes,
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
            max_results=max_results,
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

    pairs = []
    for cat in catalogues:
        book = book_map.get(cat.market_id)
        if book is None:
            continue
        matched = book.total_matched or 0.0
        if matched < min_liquidity:
            logger.info(f"  skip {cat.event.name}: liquidità {matched:.0f}€ < {min_liquidity:.0f}€")
            continue
        pairs.append((cat, book))

    return pairs


def get_eligible_markets_ltd(
    client: betfairlightweight.APIClient,
    hours_ahead: int = LTD_HOURS_AHEAD,
) -> list[dict]:
    """
    Restituisce una lista di dizionari con i dati delle partite eleggibili per LTD.

    Ogni dict contiene:
        market_id, event_name, competition, kick_off,
        home_odds, draw_odds, away_odds, matched
    """
    pairs = _fetch_catalogues_and_books(
        client,
        event_type_ids=["1"],  # calcio
        competition_ids=list(LTD_COMPETITION_IDS.values()),
        market_countries=["GB", "ES", "IT", "DE", "FR"],
        market_type_codes=["MATCH_ODDS"],
        hours_ahead=hours_ahead,
        min_liquidity=LTD_MIN_LIQUIDITY,
    )

    eligible = []
    for cat, book in pairs:
        ids = identify_home_and_draw(cat.runners)
        if ids is None:
            continue
        home_id, draw_id = ids

        # Prezzi best back per ciascun runner
        def best_back(runner_id: int) -> Optional[float]:
            for r in book.runners:
                if r.selection_id == runner_id:
                    avail = r.ex.available_to_back if r.ex else []
                    return _price(avail[0]) if avail else None
            return None

        home_odds = best_back(home_id)
        draw_odds = best_back(draw_id)

        if home_odds is None or draw_odds is None:
            continue

        if home_odds >= LTD_HOME_ODDS_MAX:
            logger.info(f"  skip {cat.event.name}: home_odds={home_odds} >= {LTD_HOME_ODDS_MAX}")
            continue

        if not (LTD_DRAW_ODDS_MIN <= draw_odds <= LTD_DRAW_ODDS_MAX):
            logger.info(
                f"  skip {cat.event.name}: draw_odds={draw_odds} "
                f"fuori range [{LTD_DRAW_ODDS_MIN}, {LTD_DRAW_ODDS_MAX}]"
            )
            continue

        matched = book.total_matched or 0.0
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


def get_eligible_markets_test(
    client: betfairlightweight.APIClient,
    hours_ahead: int = 6,
    max_results: int = 3,
) -> list[dict]:
    """
    Versione SENZA criteri di selezione LTD — solo per un test manuale one-off
    di entrata/uscita automatica dal mercato (kick-off → lay pareggio → gol o
    70' → green-up/stop loss). Nessun filtro su lega, paese, quota casa/pareggio:
    prende le prime `max_results` partite di calcio (qualunque livello) più
    vicine al kick-off nella finestra oraria, purché identificabile il pareggio
    e con liquidità minima per avere prezzi disponibili.

    NON USARE per il paper/live trading normale: bypassa l'edge validato nel
    backtest (home favorita, draw 3.2-4.5, leghe top). Solo per verificare che
    il meccanismo di entrata/uscita scatti correttamente.
    """
    pairs = _fetch_catalogues_and_books(
        client,
        event_type_ids=["1"],       # calcio, qualunque lega
        competition_ids=None,       # nessuna restrizione di competizione
        market_countries=None,      # nessuna restrizione di paese
        market_type_codes=["MATCH_ODDS"],
        hours_ahead=hours_ahead,
        min_liquidity=50.0,         # minimo per avere prezzi lay/back reali
        # 20 invece del default 50: senza filtro lega/paese list_market_book con
        # EX_BEST_OFFERS su troppi marketIds insieme sbatte contro il limite
        # Betfair TOO_MUCH_DATA (osservato con 50 mercati worldwide il 07/09/2026)
        max_results=20,
    )

    candidates = []
    for cat, book in pairs:
        ids = identify_home_and_draw(cat.runners)
        if ids is None:
            continue
        home_id, draw_id = ids

        def best_back(runner_id: int) -> Optional[float]:
            for r in book.runners:
                if r.selection_id == runner_id:
                    avail = r.ex.available_to_back if r.ex else []
                    return _price(avail[0]) if avail else None
            return None

        home_odds = best_back(home_id)
        draw_odds = best_back(draw_id)
        if home_odds is None or draw_odds is None:
            continue

        matched = book.total_matched or 0.0
        competition_name = cat.competition.name if cat.competition else "N/A"
        candidates.append({
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

    candidates.sort(key=lambda m: m["kick_off"])
    selected = candidates[:max_results]
    for m in selected:
        logger.info(
            f"  [TEST, no filtri] {m['event_name']} ({m['competition']}) | "
            f"kick-off {m['kick_off']} | draw={m['draw_odds']} home={m['home_odds']} "
            f"| matched={m['matched']:.0f}€"
        )
    return selected
