"""Orchestrate data load, simulation, metrics, and report."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_quant.config import load_settings, project_root
from auto_quant.data.akshare_loader import get_universe, load_universe_data
from auto_quant.engine.metrics import Metrics, compute_metrics
from auto_quant.optimizer.guards import apply_guards
from auto_quant.reports.html_report import render_report
from auto_quant.strategies.macd_divergence import StrategyParams
from auto_quant.strategies.trade_simulator import Trade, simulate_universe


@dataclass
class BacktestResult:
    run_id: str
    params: dict[str, Any]
    trade_count: int
    win_rate: float
    avg_trade_return: float
    metrics: Metrics
    trades: list[Trade] = field(default_factory=list)
    report_path: str = ""
    meta_path: str = ""

    def save_best_meta(self) -> None:
        out = project_root() / "output" / "best_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "params": self.params,
            "metrics": self.metrics.to_dict(),
            "report_path": self.report_path,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_macd_backtest(
    *,
    params: StrategyParams | None = None,
    max_symbols: int | None = None,
    start: str | None = None,
    end: str | None = None,
    save_best: bool = True,
) -> BacktestResult:
    settings = load_settings()
    bt_cfg = settings["backtest"]
    start = start or bt_cfg["start_date"]
    end = end or bt_cfg["end_date"]

    sp = settings["strategy"]
    p = params or StrategyParams(
        macd_fast=sp["macd_fast"],
        macd_slow=sp["macd_slow"],
        macd_signal=sp["macd_signal"],
        ma_trend=sp["ma_trend"],
        stop_loss_pct=sp["stop_loss_pct"],
        hold_days=sp["hold_days"],
    )

    symbols = get_universe(max_symbols)
    print(f"Loading {len(symbols)} symbols from {start} to {end}...")
    universe_data = load_universe_data(symbols, start, end)
    print(f"Loaded {len(universe_data)} symbols with daily+30min data.")

    trades = simulate_universe(universe_data, p, settings["costs"])
    metrics = compute_metrics(trades, start, end)
    apply_guards(metrics)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    report_dir = project_root() / "output" / "reports"
    report_path = render_report(
        run_id=run_id,
        params=p.__dict__,
        metrics=metrics,
        trades=trades,
        start=start,
        end=end,
        output_dir=report_dir,
    )

    meta_path = report_dir / f"{run_id}_meta.json"
    meta_path.write_text(
        json.dumps(
            {"params": p.__dict__, "metrics": metrics.to_dict(), "report": str(report_path)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = BacktestResult(
        run_id=run_id,
        params=p.__dict__,
        trade_count=metrics.trade_count,
        win_rate=metrics.win_rate,
        avg_trade_return=metrics.avg_trade_return,
        metrics=metrics,
        trades=trades,
        report_path=str(report_path),
        meta_path=str(meta_path),
    )
    if save_best:
        result.save_best_meta()
    return result
