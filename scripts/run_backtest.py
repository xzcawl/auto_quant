#!/usr/bin/env python
"""Run single MACD backtest."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_quant.engine.backtest_runner import run_macd_backtest


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=int, default=None)
    args = p.parse_args()
    r = run_macd_backtest(max_symbols=args.symbols)
    print(f"Report: {r.report_path}")
    print(f"Trades={r.trade_count} WinRate={r.win_rate:.2%} AvgReturn={r.avg_trade_return:.2%}")


if __name__ == "__main__":
    main()
