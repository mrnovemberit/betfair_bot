"""
Backtest strategia Lay The Draw (LTD).

Modello P&L:
  - LAY draw pre-match a quote B365D (proxy Betfair)
  - Gol nel 1° tempo  (< 45'): green-up con moltiplicatore 2.0
  - Gol nel 2° tempo (45-70'): green-up con moltiplicatore 1.6
  - Ancora 0-0 al 70': stop loss (chiusura forzata)
  - Casi incerti (gol 2° tempo, minuto ignoto): outcome assegnato con
    probabilità calibrata (p_early dal file calibration.json)
  - Commissione Betfair: 5% sui profitti netti

Output: backtest/results/ltd_trades.csv
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
RESULTS_DIR   = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

BANKROLL        = 1000.0   # € simulati
LIABILITY_PCT   = 0.02     # 2% del bankroll per trade
COMMISSION      = 0.05

# Moltiplicatori quote post-gol (draw_odds_post = draw_odds_pre * M)
M_FIRST_HALF    = 2.0   # gol < 45'
M_SECOND_HALF   = 1.6   # gol 45-70'

# Fattore di chiusura stop loss al 70' (quote draw scendono del ~42%)
STOP_LOSS_FACTOR = 0.58  # draw al 70' 0-0 ≈ pre_odds * 0.58

RANDOM_SEED = 42

# ── filtri selezione LTD ──────────────────────────────────────────────────────

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Criteri di selezione LTD da CLAUDE.md."""
    mask = (
        (df["b365h"] < 2.0) &           # casa favorita
        (df["b365d"] >= 3.2) &          # quota pareggio in range
        (df["b365d"] <= 4.5)
    )
    return df[mask].copy()


# ── calcolo P&L ───────────────────────────────────────────────────────────────

def lay_stake(liability: float, odds: float) -> float:
    return liability / (odds - 1)


def profit_greenup(liability: float, odds: float, multiplier: float) -> float:
    """Profitto netto dopo green-up (dopo commissione)."""
    s = lay_stake(liability, odds)
    back_stake = s * odds / (odds * multiplier)  # equivale a s / multiplier
    gross = s - back_stake
    return gross * (1 - COMMISSION)


def loss_stop_loss(liability: float, odds: float) -> float:
    """Perdita netta da stop loss al 70' (negativa)."""
    s = lay_stake(liability, odds)
    close_odds = odds * STOP_LOSS_FACTOR
    back_stake = s * odds / close_odds
    return s - back_stake  # negativo


# ── simulazione ───────────────────────────────────────────────────────────────

def simulate(df: pd.DataFrame, p_early: float) -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    liability = BANKROLL * LIABILITY_PCT

    records = []
    for _, row in df.iterrows():
        odds   = row["b365d"]
        timing = row["goal_timing"]

        if timing == "first_half":
            outcome = "greenup_fh"
            pnl     = profit_greenup(liability, odds, M_FIRST_HALF)

        elif timing == "second_half":
            # minuto esatto ignoto: assegna outcome probabilisticamente
            if rng.random() < p_early:
                outcome = "greenup_sh"
                pnl     = profit_greenup(liability, odds, M_SECOND_HALF)
            else:
                outcome = "stop_loss"
                pnl     = loss_stop_loss(liability, odds)

        else:  # no_goal → stop loss al 70'
            outcome = "stop_loss"
            pnl     = loss_stop_loss(liability, odds)

        records.append({
            "date":        row["date"],
            "league":      row["league"],
            "season":      row["season"],
            "home_team":   row["home_team"],
            "away_team":   row["away_team"],
            "draw_odds":   odds,
            "home_odds":   row["b365h"],
            "goal_timing": timing,
            "outcome":     outcome,
            "liability":   liability,
            "pnl":         round(pnl, 4),
        })

    return pd.DataFrame(records)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    dataset_path = PROCESSED_DIR / "ltd_dataset.csv"
    cal_path     = PROCESSED_DIR / "calibration.json"

    if not dataset_path.exists():
        raise SystemExit("Esegui prima 03_merge_dataset.py")

    df = pd.read_csv(dataset_path, parse_dates=["date"])

    with open(cal_path) as f:
        calibration = json.load(f)
    p_early = calibration["p_second_half_early"]
    print(f"p_second_half_early (calibrazione): {p_early:.3f}")

    print(f"\nPartite totali nel dataset: {len(df)}")
    selected = apply_filters(df)
    print(f"Partite selezionate (filtri LTD): {len(selected)}")

    if len(selected) < 200:
        print("[warn] meno di 200 trade — risultati non statisticamente significativi")

    trades = simulate(selected, p_early)
    trades = trades.sort_values("date").reset_index(drop=True)

    out = RESULTS_DIR / "ltd_trades.csv"
    trades.to_csv(out, index=False)
    print(f"\nTrade simulati: {len(trades)} -> {out}")
    print(trades["outcome"].value_counts().to_string())
    print(f"\nP&L totale: {trades['pnl'].sum():.2f} €")


if __name__ == "__main__":
    main()
