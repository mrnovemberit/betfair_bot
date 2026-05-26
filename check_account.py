"""Verifica saldo account e tenta creazione App Key."""
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

ACCOUNT_API = "https://api.betfair.it/exchange/account/rest/v1.0/"
headers = {
    "X-Authentication": session_token,
    "X-Application":    "",
    "Content-Type":     "application/json",
    "Accept":           "application/json",
}

# Controlla il saldo
print("=== Saldo account ===")
resp = requests.post(ACCOUNT_API + "getAccountFunds/",
    headers=headers, json={"wallet": "UK wallet"}, timeout=10)
print(f"Status: {resp.status_code} | {resp.json()}\n")

# Controlla i dettagli account
print("=== Dettagli account ===")
resp2 = requests.post(ACCOUNT_API + "getAccountDetails/",
    headers=headers, json={}, timeout=10)
print(f"Status: {resp2.status_code} | {resp2.json()}\n")

# Tenta creazione chiave
print("=== Tentativo createDeveloperAppKeys ===")
resp3 = requests.post(ACCOUNT_API + "createDeveloperAppKeys/",
    headers=headers, json={"appName": "BetfairBot"}, timeout=10)
print(f"Status: {resp3.status_code} | {resp3.json()}")

trading.logout()
