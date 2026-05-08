"""
Normalizza i CSV di football-data.co.uk e aggiunge la colonna goal_timing.
Calibra la probabilità "gol prima del 70'" per i gol del 2° tempo usando
i dati esatti di football-data.org (2023-2024).

Output: data/processed/ltd_dataset.csv
        data/processed/calibration.json
"""

import json
import pandas as pd
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

LEAGUES = ["premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1"]
SEASONS  = ["1516","1617","1718","1819","1920","2021","2122","2223","2324"]

REQUIRED = ["HomeTeam","AwayTeam","FTHG","FTAG","HTHG","HTAG","B365H","B365D","B365A"]

SEASON_YEAR_MAP = {
    "1516": 1516, "1617": 1617, "1718": 1718, "1819": 1819, "1920": 1920,
    "2021": 2021, "2122": 2122, "2223": 2223, "2324": 2324,
}


# ── helpers ──────────────────────────────────────────────────────────────────

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rimuove BOM e spazi dai nomi colonna."""
    df.columns = [c.lstrip("﻿").strip() for c in df.columns]
    return df


def parse_date(df: pd.DataFrame) -> pd.DataFrame:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            df["date"] = pd.to_datetime(df["Date"], format=fmt, dayfirst=True)
            return df
        except Exception:
            continue
    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df


def load_match_csvs() -> pd.DataFrame:
    frames = []
    for league in LEAGUES:
        for season in SEASONS:
            path = RAW_DIR / f"{league}_{season}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, low_memory=False)
            df = normalize_cols(df)
            missing = [c for c in REQUIRED if c not in df.columns]
            if missing:
                print(f"  [warn] {league} {season}: mancano {missing} — saltato")
                continue
            df = df[REQUIRED + ["Date"]].copy()
            df = parse_date(df)
            df["league"] = league
            df["season"] = season
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ── calibrazione p_early da dati football-data.org 2023-2024 ─────────────────

def calibrate_p_second_half_early() -> float:
    """
    Stima P(primo gol ≤ 70' | primo gol nel 2° tempo) dai file goal 2023-2024.
    Se nessun file disponibile, restituisce il default basato su letteratura (~0.62).
    """
    frames = []
    for league in LEAGUES:
        for year in [2023, 2024]:
            path = RAW_DIR / f"goals_{league}_{year}.csv"
            if path.exists():
                frames.append(pd.read_csv(path))

    if not frames:
        print("  [calibration] nessun dato esatto disponibile — uso default 0.62")
        return 0.62

    goals = pd.concat(frames, ignore_index=True)
    goals = goals.dropna(subset=["minute"])

    # primo gol per match
    first_goal = (
        goals.sort_values("minute")
             .groupby("match_id", as_index=False)
             .first()
    )

    second_half = first_goal[first_goal["minute"] > 45]
    if len(second_half) == 0:
        return 0.62

    p_early = (second_half["minute"] <= 70).mean()
    n = len(second_half)
    print(f"  [calibration] {n} partite con primo gol nel 2° tempo → "
          f"P(≤70') = {p_early:.3f}")
    return float(p_early)


# ── pipeline principale ───────────────────────────────────────────────────────

def add_goal_timing(df: pd.DataFrame) -> pd.DataFrame:
    df["ht_goals"] = df["HTHG"].fillna(0).astype(int) + df["HTAG"].fillna(0).astype(int)
    df["ft_goals"] = df["FTHG"].fillna(0).astype(int) + df["FTAG"].fillna(0).astype(int)

    conditions = [
        df["ht_goals"] > 0,
        (df["ht_goals"] == 0) & (df["ft_goals"] > 0),
    ]
    choices = ["first_half", "second_half"]
    df["goal_timing"] = pd.Series(
        pd.Categorical(
            pd.array(
                [choices[0] if c0 else (choices[1] if c1 else "no_goal")
                 for c0, c1 in zip(conditions[0], conditions[1])]
            )
        )
    )
    return df


def clean_dataset(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
        "FTHG": "fthg", "FTAG": "ftag",
        "HTHG": "hthg", "HTAG": "htag",
        "B365H": "b365h", "B365D": "b365d", "B365A": "b365a",
    })
    df = df.dropna(subset=["b365d", "b365h", "date"])
    df = df[df["b365d"] > 1.0]
    return df


def main():
    print("Caricamento CSV match...")
    df = load_match_csvs()
    print(f"  {len(df)} partite caricate da {df['league'].nunique()} campionati")

    print("\nNormalizzazione e goal timing...")
    df = add_goal_timing(df)
    df = clean_dataset(df)
    print(f"  {len(df)} partite dopo pulizia")

    print("\nCalibrazione p_second_half_early...")
    p_early = calibrate_p_second_half_early()

    calibration = {"p_second_half_early": p_early}
    cal_path = PROCESSED_DIR / "calibration.json"
    with open(cal_path, "w") as f:
        json.dump(calibration, f, indent=2)
    print(f"  Salvato {cal_path}")

    out = PROCESSED_DIR / "ltd_dataset.csv"
    df.to_csv(out, index=False)
    print(f"\nDataset LTD: {len(df)} righe -> {out}")
    print(df["goal_timing"].value_counts().to_string())


if __name__ == "__main__":
    main()
