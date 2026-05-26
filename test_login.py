"""Test rapido connessione Betfair — cancella dopo uso."""
import os
from pathlib import Path
from dotenv import load_dotenv
import betfairlightweight

load_dotenv(Path(__file__).parent / ".env")

username  = os.getenv("BETFAIR_USERNAME")
password  = os.getenv("BETFAIR_PASSWORD")
app_key   = os.getenv("BETFAIR_APP_KEY")
cert_path = os.getenv("BETFAIR_CERT_PATH")
key_path  = os.getenv("BETFAIR_KEY_PATH")

print(f"Username:     {username}")
print(f"App key:      {app_key[:10]}...")
print(f"Cert dir:     {str(Path(cert_path).parent)}")
print(f"Cert esiste:  {Path(cert_path).exists()}")
print(f"Key esiste:   {Path(key_path).exists()}")
print()
print("Tentativo login interattivo (senza certificato)...")

trading = betfairlightweight.APIClient(
    username=username,
    password=password,
    app_key=app_key,
    locale="italy",
)

trading.login_interactive()
print("LOGIN OK!")
print(f"Session token: {str(trading.session_token)[:20]}...")
trading.logout()
print("Logout OK")
