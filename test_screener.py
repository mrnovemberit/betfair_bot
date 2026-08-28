"""Test screener: verifica che la connessione e il filtro partite funzionino."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import betfairlightweight

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from bot.screener import get_eligible_markets_ltd

trading = betfairlightweight.APIClient(
    username=os.getenv("BETFAIR_USERNAME"),
    password=os.getenv("BETFAIR_PASSWORD"),
    app_key=os.getenv("BETFAIR_APP_KEY"),
    locale="italy",
)
trading.login_interactive()
print("Login OK\n")

print("Ricerca partite eleggibili nelle prossime 6 ore...")
eligible = get_eligible_markets_ltd(trading, hours_ahead=6)

if eligible:
    print(f"\n{len(eligible)} partita/e eleggibile/i:")
    for m in eligible:
        print(f"  {m['event_name']} | draw={m['draw_odds']} | home={m['home_odds']} | matched={m['matched']:.0f}€")
else:
    print("Nessuna partita eleggibile trovata (normale se non ci sono partite in questo momento).")
    print("\nProvo a cercare nelle prossime 48h per verifica...")
    eligible48 = get_eligible_markets_ltd(trading, hours_ahead=48)
    if eligible48:
        print(f"{len(eligible48)} partita/e nelle prossime 48h:")
        for m in eligible48:
            print(f"  {m['event_name']} | draw={m['draw_odds']} | home={m['home_odds']} | matched={m['matched']:.0f}€")
    else:
        print("Nessuna partita nei prossimi 2 giorni che rispetti i filtri LTD.")

trading.logout()
print("\nLogout OK")
