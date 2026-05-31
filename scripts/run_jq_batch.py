#!/usr/bin/env python
"""JoinQuant batch backtest queue."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--login", action="store_true", help="Save JoinQuant login state")
    p.add_argument("--from-best", action="store_true", help="Enqueue best local strategy")
    p.add_argument("--dry-run", action="store_true", help="Simulate without browser")
    args = p.parse_args()

    if args.login:
        from auto_quant.joinquant.playwright_runner import save_login_state

        save_login_state()
        return

    if args.from_best:
        from auto_quant.joinquant.converter import enqueue_best_strategy

        tid = enqueue_best_strategy()
        print(f"Enqueued task id={tid}")

    from auto_quant.joinquant.playwright_runner import run_batch

    run_batch(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
