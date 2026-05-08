"""
Scarica i CSV storici da football-data.co.uk per i 5 campionati top (2015-2024).
Output: data/raw/<league>_<season>.csv
"""

import time
import requests
import pandas as pd
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

LEAGUES = {
    "premier_league": "E0",
    "la_liga":        "SP1",
    "serie_a":        "I1",
    "bundesliga":     "D1",
    "ligue_1":        "F1",
}

# Stagioni 2015/16 → 2023/24
SEASONS = [
    "1516", "1617", "1718", "1819", "1920",
    "2021", "2122", "2223", "2324",
]

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

# Colonne minime richieste dal backtest LTD
REQUIRED_COLS = [
    "Div", "Date", "HomeTeam", "AwayTeam",
    "FTHG", "FTAG", "FTR",          # risultato finale
    "B365H", "B365D", "B365A",      # quote Bet365 (casa/pareggio/trasferta)
]


def fetch_season(league_name: str, code: str, season: str) -> pd.DataFrame | None:
    url = BASE_URL.format(season=season, code=code)
    dest = RAW_DIR / f"{league_name}_{season}.csv"

    if dest.exists():
        print(f"  [skip] {dest.name} già presente")
        return pd.read_csv(dest, low_memory=False)

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"  [warn] {league_name} {season}: HTTP {e.response.status_code} — saltato")
        return None
    except requests.RequestException as e:
        print(f"  [err]  {league_name} {season}: {e}")
        return None

    # football-data.co.uk serve encoding latin-1
    df = pd.read_csv(
        pd.io.common.StringIO(resp.content.decode("latin-1")),
        low_memory=False,
    )

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        print(f"  [warn] {league_name} {season}: colonne mancanti {missing} — salvato comunque")

    df.to_csv(dest, index=False)
    print(f"  [ok]   {dest.name} — {len(df)} partite")
    return df


def main():
    all_frames = []

    for league_name, code in LEAGUES.items():
        print(f"\n{league_name.upper()}")
        for season in SEASONS:
            df = fetch_season(league_name, code, season)
            if df is not None:
                df["_league"] = league_name
                df["_season"] = season
                all_frames.append(df)
            time.sleep(0.5)  # rispetta il server

    if not all_frames:
        print("\nNessun dato scaricato.")
        return

    combined = pd.concat(all_frames, ignore_index=True)
    out = RAW_DIR.parent / "processed" / "all_matches_raw.csv"
    combined.to_csv(out, index=False)
    print(f"\nDataset combinato: {len(combined)} righe → {out}")


if __name__ == "__main__":
    main()
