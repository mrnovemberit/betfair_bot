"""
Scarica i minuti esatti dei gol da api.football-data.org (v4).
Output: data/raw/goals_<league>_<season_start>.csv

Tier gratuito: 10 chiamate/minuto → sleep tra le richieste.
Richiede FOOTBALL_DATA_API_KEY in .env
"""

import time
import json
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

# Codici competizione football-data.org v4
COMPETITIONS = {
    "premier_league": "PL",
    "la_liga":        "PD",
    "serie_a":        "SA",
    "bundesliga":     "BL1",
    "ligue_1":        "FL1",
}

# Anno di inizio stagione (2015 = stagione 2015/16)
SEASON_STARTS = list(range(2015, 2024))  # 2015 → 2023

RATE_LIMIT_SLEEP = 7  # secondi tra le chiamate (10 call/min = 6s, +1 margine)


def fetch_matches_for_season(competition_code: str, season_year: int) -> list[dict] | None:
    url = f"{BASE_URL}/competitions/{competition_code}/matches"
    params = {"season": season_year}

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if resp.status_code == 429:
            print(f"  [rate limit] attendo 60s...")
            time.sleep(60)
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"  [warn] {competition_code} {season_year}: HTTP {e.response.status_code} — saltato")
        return None
    except requests.RequestException as e:
        print(f"  [err]  {competition_code} {season_year}: {e}")
        return None

    return resp.json().get("matches", [])


def extract_goal_rows(matches: list[dict]) -> list[dict]:
    rows = []
    for m in matches:
        if m.get("status") not in ("FINISHED",):
            continue

        match_date = m.get("utcDate", "")[:10]
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        match_id = m.get("id")

        goals = m.get("goals", [])
        if not goals:
            # Partita senza dettaglio gol — salva comunque la riga col risultato
            rows.append({
                "match_id":   match_id,
                "date":       match_date,
                "home_team":  home,
                "away_team":  away,
                "minute":     None,
                "extra_time": None,
                "scorer":     None,
                "team":       None,
                "type":       None,
                "fthg":       m["score"]["fullTime"]["home"],
                "ftag":       m["score"]["fullTime"]["away"],
            })
        else:
            for g in goals:
                rows.append({
                    "match_id":   match_id,
                    "date":       match_date,
                    "home_team":  home,
                    "away_team":  away,
                    "minute":     g.get("minute"),
                    "extra_time": g.get("injuryTime"),
                    "scorer":     g.get("scorer", {}).get("name"),
                    "team":       g.get("team", {}).get("name"),
                    "type":       g.get("type"),
                    "fthg":       m["score"]["fullTime"]["home"],
                    "ftag":       m["score"]["fullTime"]["away"],
                })
    return rows


def main():
    if not API_KEY:
        raise SystemExit("FOOTBALL_DATA_API_KEY non trovata in .env")

    all_frames = []

    for league_name, code in COMPETITIONS.items():
        print(f"\n{league_name.upper()}")
        for season_year in SEASON_STARTS:
            dest = RAW_DIR / f"goals_{league_name}_{season_year}.csv"

            if dest.exists():
                print(f"  [skip] {dest.name} già presente")
                all_frames.append(pd.read_csv(dest))
                continue

            print(f"  fetching {code} stagione {season_year}...", end=" ", flush=True)
            matches = fetch_matches_for_season(code, season_year)

            if matches is None:
                continue

            rows = extract_goal_rows(matches)
            if not rows:
                print("0 partite finite")
                continue

            df = pd.DataFrame(rows)
            df["_league"] = league_name
            df["_season_start"] = season_year
            df.to_csv(dest, index=False)
            all_frames.append(df)
            print(f"{len(df)} righe gol ({df['match_id'].nunique()} partite)")

            time.sleep(RATE_LIMIT_SLEEP)

    if not all_frames:
        print("\nNessun dato scaricato.")
        return

    combined = pd.concat(all_frames, ignore_index=True)
    out = RAW_DIR.parent / "processed" / "all_goals_raw.csv"
    combined.to_csv(out, index=False)
    print(f"\nDataset gol combinato: {len(combined)} righe → {out}")


if __name__ == "__main__":
    main()
