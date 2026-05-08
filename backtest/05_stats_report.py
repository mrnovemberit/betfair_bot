"""
Calcola e stampa le metriche di validazione del backtest LTD.
Salva il report in backtest/results/ltd_report.txt e il grafico equity curve
in backtest/results/equity_curve.png.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
BANKROLL    = 1000.0

THRESHOLDS = {
    "win_rate":    0.70,
    "expectancy":  0.0,
    "sortino":     1.0,
    "max_drawdown": 0.20,
    "min_trades":  200,
}


# ── metriche ──────────────────────────────────────────────────────────────────

def win_rate(trades: pd.DataFrame) -> float:
    wins = trades["outcome"].isin(["greenup_fh", "greenup_sh"])
    return wins.mean()


def expectancy(trades: pd.DataFrame) -> float:
    """Expectancy per trade in €."""
    return trades["pnl"].mean()


def sharpe(trades: pd.DataFrame) -> float:
    r = trades["pnl"]
    return (r.mean() / r.std()) * np.sqrt(len(r)) if r.std() > 0 else 0.0


def sortino(trades: pd.DataFrame) -> float:
    r = trades["pnl"]
    downside = r[r < 0]
    dd_std = downside.std() if len(downside) > 0 else 1e-9
    return (r.mean() / dd_std) * np.sqrt(len(r))


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return abs(dd.min())


def equity_curve(trades: pd.DataFrame) -> pd.Series:
    return BANKROLL + trades["pnl"].cumsum()


# ── report per sotto-segmento ─────────────────────────────────────────────────

def segment_stats(trades: pd.DataFrame, label: str) -> str:
    if len(trades) == 0:
        return f"  {label}: nessun trade\n"
    wr = win_rate(trades)
    exp = expectancy(trades)
    so  = sortino(trades)
    eq  = equity_curve(trades)
    mdd = max_drawdown(eq)
    pnl = trades["pnl"].sum()
    return (
        f"  {label}: {len(trades)} trade | "
        f"win={wr:.1%} | exp={exp:+.2f}€ | sortino={so:.2f} | "
        f"mdd={mdd:.1%} | P&L={pnl:+.0f}€\n"
    )


# ── grafico equity curve ──────────────────────────────────────────────────────

def plot_equity(trades: pd.DataFrame):
    eq = equity_curve(trades)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle("Backtest LTD — Equity Curve", fontsize=14)

    ax1.plot(range(len(eq)), eq.values, color="steelblue", linewidth=1)
    ax1.axhline(BANKROLL, color="gray", linestyle="--", linewidth=0.8)
    ax1.set_ylabel("Bankroll (€)")
    ax1.grid(True, alpha=0.3)

    rolling_pnl = trades["pnl"].rolling(50).mean()
    ax2.bar(range(len(trades)), trades["pnl"], color=[
        "green" if p > 0 else "red" for p in trades["pnl"]
    ], alpha=0.4, width=1)
    ax2.plot(range(len(trades)), rolling_pnl, color="navy", linewidth=1.5,
             label="Media mobile 50 trade")
    ax2.axhline(0, color="gray", linestyle="--", linewidth=0.8)
    ax2.set_xlabel("Trade #")
    ax2.set_ylabel("P&L per trade (€)")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = RESULTS_DIR / "equity_curve.png"
    plt.savefig(path, dpi=150)
    plt.close()
    return path


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    trades_path = RESULTS_DIR / "ltd_trades.csv"
    if not trades_path.exists():
        raise SystemExit("Esegui prima 04_backtest_LTD.py")

    trades = pd.read_csv(trades_path, parse_dates=["date"])

    wr   = win_rate(trades)
    exp  = expectancy(trades)
    so   = sortino(trades)
    sh   = sharpe(trades)
    eq   = equity_curve(trades)
    mdd  = max_drawdown(eq)
    n    = len(trades)
    pnl_total = trades["pnl"].sum()
    roi  = pnl_total / BANKROLL * 100

    # Verifica soglie
    passed = {
        "win_rate":    wr >= THRESHOLDS["win_rate"],
        "expectancy":  exp >= THRESHOLDS["expectancy"],
        "sortino":     so >= THRESHOLDS["sortino"],
        "max_drawdown": mdd <= THRESHOLDS["max_drawdown"],
        "min_trades":  n >= THRESHOLDS["min_trades"],
    }

    lines = []
    lines.append("=" * 60)
    lines.append("BACKTEST LTD — REPORT METRICHE")
    lines.append("=" * 60)
    lines.append(f"Periodo:        {trades['date'].min().date()} -> {trades['date'].max().date()}")
    lines.append(f"Campionati:     {trades['league'].nunique()}")
    lines.append(f"Trade totali:   {n}")
    lines.append(f"Bankroll:       {BANKROLL:.0f} €")
    lines.append(f"Liability/trade:{BANKROLL * 0.02:.0f} € (2%)")
    lines.append("")
    lines.append("--- METRICHE PRINCIPALI ---")
    lines.append(f"Win rate:       {wr:.1%}  {'OK' if passed['win_rate'] else 'FAIL'}  (soglia >70%)")
    lines.append(f"Expectancy:     {exp:+.3f} € {'OK' if passed['expectancy'] else 'FAIL'}  (soglia >0)")
    lines.append(f"Sortino ratio:  {so:.2f}   {'OK' if passed['sortino'] else 'FAIL'}  (soglia >1.0)")
    lines.append(f"Sharpe ratio:   {sh:.2f}")
    lines.append(f"Max drawdown:   {mdd:.1%}  {'OK' if passed['max_drawdown'] else 'FAIL'}  (soglia <20%)")
    lines.append(f"P&L totale:     {pnl_total:+.0f} €")
    lines.append(f"ROI:            {roi:+.1f}%")
    lines.append("")
    lines.append("--- DISTRIBUZIONE OUTCOME ---")
    for outcome, count in trades["outcome"].value_counts().items():
        lines.append(f"  {outcome:<20} {count:>5}  ({count/n:.1%})")
    lines.append("")
    lines.append("--- PER CAMPIONATO ---")
    for league in sorted(trades["league"].unique()):
        lines.append(segment_stats(trades[trades["league"] == league], league))
    lines.append("--- PER STAGIONE ---")
    for season in sorted(trades["season"].unique()):
        lines.append(segment_stats(trades[trades["season"] == season], season))
    lines.append("")
    lines.append("--- VERDETTO ---")
    all_passed = all(passed.values())
    lines.append("PROCEDI AL PAPER TRADING" if all_passed else "NON PROCEDERE — rivedere parametri")
    for k, v in passed.items():
        lines.append(f"  {'[OK]' if v else '[FAIL]'} {k}")
    lines.append("=" * 60)

    report = "\n".join(lines)
    print(report)

    report_path = RESULTS_DIR / "ltd_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport salvato: {report_path}")

    chart_path = plot_equity(trades)
    print(f"Equity curve:  {chart_path}")


if __name__ == "__main__":
    main()
