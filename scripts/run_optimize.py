#!/usr/bin/env python
"""Run parameter optimization."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_quant.optimizer.loop import run_optimization


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=None)
    args = p.parse_args()
    best = run_optimization(max_rounds=args.rounds)
    print(f"Best: {best.params}")
    print(f"Score: {best.score:.4f}")
    print(f"Report: {best.report_path}")


if __name__ == "__main__":
    main()
