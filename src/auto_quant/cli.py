"""CLI entry point."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto-quant", description="Local quant + JoinQuant automation")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bt = sub.add_parser("backtest", help="Run single MACD divergence backtest")
    p_bt.add_argument("--symbols", type=int, default=None, help="Max symbols from universe")

    p_opt = sub.add_parser("optimize", help="Run parameter optimization loop")
    p_opt.add_argument("--rounds", type=int, default=None)

    p_jq = sub.add_parser("jq-batch", help="Submit queued strategies to JoinQuant")
    p_jq.add_argument("--from-best", action="store_true", help="Enqueue best local result first")
    p_jq.add_argument("--login", action="store_true", help="Open browser to save login state")
    p_jq.add_argument("--dry-run", action="store_true", help="Simulate JQ batch without browser")

    # research workflow (delegate to scripts/research.py logic)
    p_res = sub.add_parser("research", help="Strategy research pipeline")
    p_res_sub = p_res.add_subparsers(dest="research_cmd", required=True)
    # minimal: research list / research run ID — full CLI in scripts/research.py
    p_res_list = p_res_sub.add_parser("list", help="List research projects")
    p_res_run = p_res_sub.add_parser("run", help="Run backtest for project")
    p_res_run.add_argument("id")
    p_res_run.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "backtest":
        from auto_quant.engine.backtest_runner import run_macd_backtest

        result = run_macd_backtest(max_symbols=args.symbols)
        print(f"Done. Trades={result.trade_count} WinRate={result.win_rate:.2%} "
              f"AvgReturn={result.avg_trade_return:.2%} Report={result.report_path}")
        return 0

    if args.command == "optimize":
        from auto_quant.optimizer.loop import run_optimization

        best = run_optimization(max_rounds=args.rounds)
        print(f"Best params: {best.params} score={best.score:.4f} report={best.report_path}")
        return 0

    if args.command == "jq-batch":
        if args.login:
            from auto_quant.joinquant.playwright_runner import save_login_state

            save_login_state()
            return 0
        from auto_quant.joinquant.playwright_runner import run_batch

        if args.from_best:
            from auto_quant.joinquant.converter import enqueue_best_strategy

            enqueue_best_strategy()
        run_batch(dry_run=args.dry_run)
        return 0

    if args.command == "research":
        from auto_quant.research import list_projects, run_backtest_for_project, write_report

        if args.research_cmd == "list":
            for pr in list_projects():
                print(f"{pr.id}\t{pr.meta.get('status')}\t{pr.meta.get('name')}")
            return 0
        if args.research_cmd == "run":
            run_backtest_for_project(args.id, dry_run_jq=args.dry_run)
            write_report(args.id)
            print(f"Report updated. Open analysis_request.md in Cursor.")
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
