"""
Entry point del bot LTD.

Uso:
    python bot/main.py                   # paper trading (default)
    python bot/main.py --live            # LIVE (soldi reali — usare con cautela)
    python bot/main.py --hours 3         # finestra di ricerca partite (default 6)
    python bot/main.py --bankroll 500    # bankroll in € (default 1000)

In paper trading nessun ordine reale viene inviato a Betfair.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import betfairlightweight
from flumine import Flumine, clients

# Aggiunge la root del progetto al path così gli import funzionano da qualunque cwd
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bot.screener import get_eligible_markets
from bot.strategy_LTD import LayTheDrawStrategy

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def build_client(paper_trade: bool) -> betfairlightweight.APIClient:
    username = os.getenv("BETFAIR_USERNAME")
    password = os.getenv("BETFAIR_PASSWORD")
    app_key  = os.getenv("BETFAIR_APP_KEY")
    cert_path = os.getenv("BETFAIR_CERT_PATH")
    key_path  = os.getenv("BETFAIR_KEY_PATH")

    missing = [k for k, v in {
        "BETFAIR_USERNAME": username,
        "BETFAIR_PASSWORD": password,
        "BETFAIR_APP_KEY":  app_key,
        "BETFAIR_CERT_PATH": cert_path,
        "BETFAIR_KEY_PATH":  key_path,
    }.items() if not v]

    if missing:
        raise SystemExit(
            f"Credenziali mancanti nel .env: {', '.join(missing)}\n"
            f"Modifica {ROOT / '.env'} e riprova."
        )

    trading = betfairlightweight.APIClient(
        username=username,
        password=password,
        app_key=app_key,
        locale="italy",
    )
    trading.login_interactive()
    logger.info(f"Login Betfair OK (paper_trade={paper_trade})")
    return trading


def main():
    parser = argparse.ArgumentParser(description="Bot LTD Betfair")
    parser.add_argument("--live",      action="store_true", help="Modalità live (soldi reali)")
    parser.add_argument("--hours",     type=int,   default=6,    help="Ore avanti per la ricerca partite")
    parser.add_argument("--bankroll",  type=float, default=1000, help="Bankroll in €")
    args = parser.parse_args()

    paper_trade = not args.live

    if not paper_trade:
        confirm = input(
            "\n⚠️  MODALITÀ LIVE — verranno piazzati ordini REALI su Betfair.\n"
            "Digita 'CONFERMO' per procedere: "
        )
        if confirm.strip() != "CONFERMO":
            print("Annullato.")
            sys.exit(0)

    logger.info("=" * 60)
    logger.info(f"Bot LTD avviato | paper={paper_trade} | bankroll={args.bankroll}€")
    logger.info("=" * 60)

    # Connessione Betfair
    trading = build_client(paper_trade)

    # Screener: trova partite eleggibili
    logger.info(f"Screener: ricerca partite nelle prossime {args.hours}h...")
    eligible = get_eligible_markets(trading, hours_ahead=args.hours)

    if not eligible:
        logger.info("Nessuna partita eleggibile trovata. Bot terminato.")
        return

    logger.info(f"{len(eligible)} partita/e eleggibile/i:")
    for m in eligible:
        logger.info(
            f"  {m['event_name']} ({m['competition']}) "
            f"| kick-off {m['kick_off']} "
            f"| draw={m['draw_odds']} home={m['home_odds']} "
            f"| matched={m['matched']:.0f}€"
        )

    # Avvio Flumine
    market_ids = [m["market_id"] for m in eligible]

    if paper_trade:
        framework_client = clients.SimulatedClient()
    else:
        framework_client = clients.BetfairClient(trading)

    framework = Flumine(client=framework_client)

    strategy = LayTheDrawStrategy(
        market_filter=betfairlightweight.filters.streaming_market_filter(
            market_ids=market_ids,
        ),
        bankroll=args.bankroll,
        paper_trade=paper_trade,
    )
    framework.add_strategy(strategy)

    logger.info("Flumine in esecuzione — Ctrl+C per fermare")
    try:
        framework.run()
    except KeyboardInterrupt:
        logger.info("Bot fermato dall'utente.")
    finally:
        trading.logout()
        logger.info("Logout Betfair. Fine.")


if __name__ == "__main__":
    main()
