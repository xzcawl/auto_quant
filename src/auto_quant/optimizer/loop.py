"""Parameter optimization loop with grid search."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_quant.config import load_optimize_space, load_settings, project_root
from auto_quant.data.akshare_loader import get_universe, load_universe_data
from auto_quant.engine.metrics import compute_metrics
from auto_quant.optimizer.guards import apply_guards, is_acceptable
from auto_quant.reports.html_report import render_report
from auto_quant.strategies.macd_divergence import StrategyParams
from auto_quant.strategies.trade_simulator import simulate_universe


@dataclass
class OptimizationBest:
    params: dict[str, Any]
    score: float
    report_path: str
    metrics: dict[str, Any]


def _grid_combinations(space: dict) -> list[dict]:
    keys = list(space.keys())
    values = [space[k] for k in keys]
    combos = []
    for combo in itertools.product(*values):
        combos.append(dict(zip(keys, combo)))
    return combos


def run_optimization(*, max_rounds: int | None = None) -> OptimizationBest:
    settings = load_settings()
    opt_cfg = load_optimize_space("macd")
    space = opt_cfg["search_space"]
    max_combos = opt_cfg.get("max_combinations", 24)
    rounds = max_rounds or settings["optimizer"].get("max_rounds", 20)
    limit = min(rounds, max_combos)

    combos = _grid_combinations(space)[:limit]

    bt = settings["backtest"]
    start, end = bt["start_date"], bt["end_date"]
    train_end = settings["optimizer"].get("train_end", "2023-12-31")
    validate_start = settings["optimizer"].get("validate_start", "2024-01-01")

    symbols = get_universe()
    print(f"Optimization: loading universe ({len(symbols)} symbols)...")
    universe_data = load_universe_data(symbols, start, end)

    log_path = project_root() / "output" / "optimization_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    best_score = float("-inf")
    best: OptimizationBest | None = None

    for i, combo in enumerate(combos):
        params = StrategyParams(
            macd_fast=combo["macd_fast"],
            macd_slow=combo["macd_slow"],
            macd_signal=combo["macd_signal"],
            ma_trend=combo["ma_trend"],
            stop_loss_pct=combo["stop_loss_pct"],
            hold_days=combo["hold_days"],
        )
        trades = simulate_universe(universe_data, params, settings["costs"])
        metrics = compute_metrics(trades, start, end)
        apply_guards(metrics)

        # walk-forward: validate period avg return
        val_trades = [t for t in trades if t.exit_time >= __import__("pandas").Timestamp(validate_start)]
        val_metrics = compute_metrics(val_trades, validate_start, end) if val_trades else metrics

        score = metrics.avg_trade_return
        if val_trades:
            score = 0.6 * metrics.avg_trade_return + 0.4 * val_metrics.avg_trade_return

        record = {
            "round": i + 1,
            "params": combo,
            "score": score,
            "metrics": metrics.to_dict(),
            "acceptable": is_acceptable(metrics),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(
            f"[{i+1}/{len(combos)}] score={score:.4f} trades={metrics.trade_count} "
            f"avg={metrics.avg_trade_return:.4f} warnings={len(metrics.warnings)}"
        )

        if not is_acceptable(metrics):
            continue
        if score > best_score:
            best_score = score
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_opt{i}"
            report_path = render_report(
                run_id=run_id,
                params=combo,
                metrics=metrics,
                trades=trades,
                start=start,
                end=end,
                output_dir=project_root() / "output" / "reports",
            )
            best = OptimizationBest(
                params=combo,
                score=score,
                report_path=str(report_path),
                metrics=metrics.to_dict(),
            )
            # save best
            best_file = project_root() / "output" / "best_result.json"
            best_file.write_text(
                json.dumps(
                    {"params": combo, "score": score, "metrics": metrics.to_dict(), "report": str(report_path)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    if best is None:
        # fallback: last combo
        combo = combos[0]
        params = StrategyParams(**{k: combo[k] for k in combo})
        trades = simulate_universe(universe_data, params, settings["costs"])
        metrics = compute_metrics(trades, start, end)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_fallback"
        report_path = render_report(
            run_id=run_id,
            params=combo,
            metrics=metrics,
            trades=trades,
            start=start,
            end=end,
            output_dir=project_root() / "output" / "reports",
        )
        best = OptimizationBest(params=combo, score=metrics.avg_trade_return, report_path=str(report_path), metrics=metrics.to_dict())

    # hold period sensitivity
    _write_hold_sensitivity(universe_data, best.params, settings)

    return best


def _write_hold_sensitivity(universe_data, base_params: dict, settings: dict) -> None:
    rows = []
    for hd in [3, 4, 5, 6, 7, 8, 10]:
        p = StrategyParams(
            macd_fast=base_params["macd_fast"],
            macd_slow=base_params["macd_slow"],
            macd_signal=base_params["macd_signal"],
            ma_trend=base_params["ma_trend"],
            stop_loss_pct=base_params["stop_loss_pct"],
            hold_days=hd,
        )
        trades = simulate_universe(universe_data, p, settings["costs"])
        m = compute_metrics(trades, settings["backtest"]["start_date"], settings["backtest"]["end_date"])
        rows.append({"hold_days": hd, "avg_return": m.avg_trade_return, "trades": m.trade_count})

    out = project_root() / "output" / "hold_sensitivity.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
