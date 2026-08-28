"""
Entry point del bot Betfair.

Uso:
    python bot/main.py                   # usa strategies.toml (default: paper)
    python bot/main.py --live            # consente LIVE per le strategie con mode="live" in strategies.toml
    python bot/main.py --hours 3         # default hours_ahead se non specificato per strategia
    python bot/main.py --bankroll 500    # default bankroll se non specificato per strategia

Le strategie abilitate, la loro modalità (off/paper/live) e i parametri per-strategia
si configurano in strategies.toml — non da CLI. --live è un gate aggiuntivo: una
strategia con mode="live" in strategies.toml gira comunque in paper finché non è
passato anche --live (o LIVE=true da env, usato dal container).
"""

import argparse
import logging
import os
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv
import betfairlightweight
from betfairlightweight.exceptions import LoginError
from flumine import Flumine, clients
from flumine.streams.betfairmarketstream import BetfairMarketStream

# Aggiunge la root del progetto al path così gli import funzionano da qualunque cwd
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bot.screener import get_eligible_markets_ltd
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

# Registry strategie: nome -> (funzione screener, classe strategia).
# Per aggiungere una nuova strategia: 1) bot/strategy_X.py + screener.py::get_eligible_markets_X
#                                      2) una riga qui sotto
#                                      3) una sezione in strategies.toml — nessun altro file da toccare
STRATEGY_REGISTRY = {
    "ltd": (get_eligible_markets_ltd, LayTheDrawStrategy),
    # "scalping_ou25": (get_eligible_markets_ou25, ScalpingOU25Strategy),  # non ancora implementata
}


def build_client(use_live_key: bool) -> betfairlightweight.APIClient:
    username  = os.getenv("BETFAIR_USERNAME")
    password  = os.getenv("BETFAIR_PASSWORD")
    app_key   = os.getenv("BETFAIR_APP_KEY_LIVE") if use_live_key else os.getenv("BETFAIR_APP_KEY")
    cert_path = os.getenv("BETFAIR_CERT_PATH")
    key_path  = os.getenv("BETFAIR_KEY_PATH")

    app_key_var = "BETFAIR_APP_KEY_LIVE" if use_live_key else "BETFAIR_APP_KEY"
    required = {
        "BETFAIR_USERNAME": username,
        "BETFAIR_PASSWORD": password,
        app_key_var: app_key,
        "BETFAIR_CERT_PATH": cert_path,
        "BETFAIR_KEY_PATH": key_path,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise SystemExit(
            f"Credenziali mancanti nel .env: {', '.join(missing)}\n"
            f"Modifica {ROOT / '.env'} e riprova."
        )

    cert_file, key_file = Path(cert_path), Path(key_path)
    if not cert_file.is_file() or not key_file.is_file():
        # Fail-fast deliberato: se non lo facciamo qui, l'errore avviene dentro
        # BetfairClient.login() di Flumine, che intercetta CertsError (sottoclasse
        # di BetfairError) e lo logga come ERROR senza rilanciarlo — il framework
        # continuerebbe silenziosamente con una sessione non autenticata.
        raise SystemExit(
            f"Certificato/chiave non trovati: {cert_file} / {key_file}\n"
            f"Verifica BETFAIR_CERT_PATH/BETFAIR_KEY_PATH in {ROOT / '.env'}."
        )

    trading = betfairlightweight.APIClient(
        username=username,
        password=password,
        app_key=app_key,
        locale="italy",
        cert_files=(str(cert_file), str(key_file)),
    )
    try:
        trading.login()   # non-interattivo, via certificato — preferito per uso headless
        logger.info(f"Login Betfair OK (cert-based, app_key={'LIVE' if use_live_key else 'DELAYED'})")
    except LoginError as e:
        # L'account non ha ancora il certificato registrato lato Betfair per il login
        # automatizzato (richiede un passo di setup separato sul sito Betfair, non
        # risolvibile da codice — vedi docs/deploy_server.md). Fallback all'endpoint
        # interattivo (che NON richiede comunque un terminale/TTY: e' solo un altro
        # endpoint Betfair, username+password) per non bloccare l'operatività nel
        # frattempo. Da rimuovere una volta completata la registrazione del certificato.
        logger.warning(
            f"Login cert-based fallito ({e}) — fallback a login_interactive(). "
            f"Registra il certificato per il login automatizzato su Betfair: "
            f"https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Non-Interactive+login"
        )
        trading.login_interactive()
        logger.info(f"Login Betfair OK (interactive fallback, app_key={'LIVE' if use_live_key else 'DELAYED'})")
    return trading


def load_strategy_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"File strategie non trovato: {path}")
    with open(path, "rb") as f:
        config = tomllib.load(f)
    for name, cfg in config.items():
        if cfg.get("mode", "off") not in ("off", "paper", "live"):
            raise SystemExit(f"strategies.toml [{name}].mode deve essere off|paper|live")
        if cfg.get("mode", "off") != "off" and name not in STRATEGY_REGISTRY:
            logger.warning(f"'{name}' non è in STRATEGY_REGISTRY — sezione ignorata.")
    return {
        name: cfg for name, cfg in config.items()
        if cfg.get("mode", "off") != "off" and name in STRATEGY_REGISTRY
    }


def main():
    parser = argparse.ArgumentParser(description="Bot Betfair")
    parser.add_argument("--live", action="store_true",
                         help="Consente LIVE per le strategie con mode='live' in strategies.toml")
    parser.add_argument("--hours", type=int, default=6,
                         help="Default hours_ahead se assente nel TOML")
    parser.add_argument("--bankroll", type=float, default=1000,
                         help="Default bankroll se assente nel TOML")
    args = parser.parse_args()

    enabled = load_strategy_config(ROOT / "strategies.toml")
    if not enabled:
        logger.info("Nessuna strategia abilitata in strategies.toml. Bot terminato.")
        return

    strategies_want_live = any(cfg["mode"] == "live" for cfg in enabled.values())
    env_live = os.getenv("LIVE", "").strip().lower() == "true"
    wants_live = strategies_want_live and (args.live or env_live)

    if strategies_want_live and not wants_live:
        logger.warning(
            "strategies.toml chiede live ma manca --live/LIVE=true: "
            "tutte le strategie live vengono ridotte a paper."
        )
        for cfg in enabled.values():
            if cfg["mode"] == "live":
                cfg["mode"] = "paper"

    if wants_live:
        if sys.stdin.isatty():
            confirm = input(
                "\n⚠️  MODALITÀ LIVE — verranno piazzati ordini REALI su Betfair.\n"
                "Digita 'CONFERMO' per procedere: "
            )
            if confirm.strip() != "CONFERMO":
                logger.info("Annullato dall'utente.")
                sys.exit(0)
        else:
            if os.getenv("CONFIRM_LIVE", "").strip().lower() != "yes":
                logger.error(
                    "Ambiente non interattivo: imposta CONFIRM_LIVE=yes per confermare l'avvio live."
                )
                sys.exit(1)
            logger.warning("CONFIRM_LIVE=yes rilevato — avvio LIVE senza conferma manuale (nessun TTY).")

    logger.info("=" * 60)
    logger.info(f"Bot Betfair avviato | live={wants_live} | strategie={list(enabled.keys())}")
    logger.info("=" * 60)

    trading = build_client(use_live_key=wants_live)
    framework = Flumine(client=clients.BetfairClient(trading, paper_trade=not wants_live))

    added = 0
    for name, cfg in enabled.items():
        screener_fn, strategy_cls = STRATEGY_REGISTRY[name]
        hours_ahead = cfg.get("hours_ahead", args.hours)

        logger.info(f"[{name}] screener: ricerca partite nelle prossime {hours_ahead}h...")
        eligible = screener_fn(trading, hours_ahead=hours_ahead)

        if not eligible:
            logger.info(f"[{name}] nessun mercato eleggibile, skip.")
            continue

        logger.info(f"[{name}] {len(eligible)} partita/e eleggibile/i:")
        for m in eligible:
            logger.info(
                f"  {m['event_name']} ({m['competition']}) "
                f"| kick-off {m['kick_off']} "
                f"| draw={m['draw_odds']} home={m['home_odds']} "
                f"| matched={m['matched']:.0f}€"
            )

        stream = BetfairMarketStream(
            market_filter=betfairlightweight.filters.streaming_market_filter(
                market_ids=[m["market_id"] for m in eligible]
            ),
        )
        strategy = strategy_cls(
            stream=stream,
            name=name.upper(),
            bankroll=cfg.get("bankroll", args.bankroll),
            paper_trade=(cfg["mode"] != "live"),
        )
        framework.add_strategy(strategy)
        added += 1
        logger.info(f"[{name}] aggiunta | mode={cfg['mode']} | {len(eligible)} mercato/i")

    if added == 0:
        logger.info("Nessuna strategia con mercati eleggibili ora. Bot terminato.")
        trading.logout()
        return

    logger.info(f"Flumine in esecuzione ({added} strategia/e) — Ctrl+C per fermare")
    try:
        framework.run()
    except KeyboardInterrupt:
        logger.info("Bot fermato dall'utente.")
    finally:
        trading.logout()
        logger.info("Logout Betfair. Fine.")


if __name__ == "__main__":
    main()
