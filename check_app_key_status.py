"""Verifica read-only dello stato delle App Key (Delayed/Live) associate all'account."""
import os, sys, requests
from pathlib import Path
from dotenv import load_dotenv
import betfairlightweight

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

trading = betfairlightweight.APIClient(
    username=os.getenv("BETFAIR_USERNAME"),
    password=os.getenv("BETFAIR_PASSWORD"),
    app_key=os.getenv("BETFAIR_APP_KEY"),
    locale="italy",
)
trading.login_interactive()
session_token = trading.session_token
print("Login OK\n")

ACCOUNT_API = "https://api.betfair.com/exchange/account/rest/v1.0/"
headers = {
    "X-Authentication": session_token,
    "X-Application":    "",
    "Content-Type":     "application/json",
    "Accept":           "application/json",
}

resp = requests.post(ACCOUNT_API + "getDeveloperAppKeys/", headers=headers, json={}, timeout=10)
print(f"Status HTTP: {resp.status_code}")
result = resp.json()
print(f"Raw: {result}\n")

if isinstance(result, list) and result:
    for app in result:
        print(f"\nApp: {app.get('applicationName')}")
        for v in app.get('appVersions', []):
            key = v.get('applicationKey', '')
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else key
            status = v.get('status') or "N/A"
            print(f"  Status: {status:10s} | Delayed: {v.get('delayData')} | Key: {masked}")
else:
    print(f"Nessuna App Key trovata o risposta inattesa: {result}")

trading.logout()
