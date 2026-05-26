"""
Crea App Key Exchange per account betfair.it via REST.
"""
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
print(f"Login OK\n")

ACCOUNT_API = "https://api.betfair.com/exchange/account/rest/v1.0/"
headers = {
    "X-Authentication": session_token,
    "X-Application":    "",
    "Content-Type":     "application/json",
    "Accept":           "application/json",
}

# Crea la App Key
print("Creazione App Key 'BetfairBot'...")
resp = requests.post(
    ACCOUNT_API + "createDeveloperAppKeys/",
    headers=headers,
    json={"appName": "BetfairBot"},
    timeout=10,
)
print(f"Status: {resp.status_code}")
result = resp.json()
print(f"Response: {result}\n")

# Leggi le chiavi create
print("Lettura chiavi dopo creazione...")
resp2 = requests.post(
    ACCOUNT_API + "getDeveloperAppKeys/",
    headers=headers,
    json={},
    timeout=10,
)
result2 = resp2.json()
print(f"Status: {resp2.status_code}")
print(f"Raw response: {result2}")

if isinstance(result2, list) and result2:
    for app in result2:
        print(f"\nApp: {app.get('applicationName')}")
        for v in app.get('appVersions', []):
            print(f"  Status: {v.get('status')}  |  Key: {v.get('applicationKey')}")
elif isinstance(result2, list) and not result2:
    print("Lista vuota — nessuna App Key associata a questo account.")

trading.logout()
