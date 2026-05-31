"""Compute backtest metrics from trade list."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from auto_quant.strategies.trade_simulator import Trade


@dataclass
class Metrics:
    trade_count: int = 0
    win_rate: float = 0.0
    avg_trade_return: float = 0.0
    max_single_loss: float = 0.0
    max_single_gain: float = 0.0
    total_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    signals_per_day: float = 0.0
    trading_days: int = 0
    yearly_returns: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "avg_trade_return": self.avg_trade_return,
            "max_single_loss": self.max_single_loss,
            "max_single_gain": self.max_single_gain,
            "total_return": self.total_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "signals_per_day": self.signals_per_day,
            "trading_days": self.trading_days,
            "yearly_returns": self.yearly_returns,
            "warnings": self.warnings,
        }


def compute_metrics(
    trades: list[Trade],
    start: str,
    end: str,
) -> Metrics:
    m = Metrics()
    if not trades:
        m.warnings.append("无交易记录")
        return m

    rets = np.array([t.pnl_pct for t in trades])
    m.trade_count = len(trades)
    m.win_rate = float((rets > 0).mean())
    m.avg_trade_return = float(rets.mean())
    m.max_single_loss = float(rets.min())
    m.max_single_gain = float(rets.max())
    m.total_return = float(np.prod(1 + rets) - 1) if len(rets) else 0.0

    if len(rets) > 1 and rets.std() > 0:
        m.sharpe = float(rets.mean() / rets.std() * np.sqrt(252))

    # equity curve by exit date
    df = pd.DataFrame(
        {
            "exit_time": [t.exit_time for t in trades],
            "pnl_pct": [t.pnl_pct for t in trades],
        }
    ).sort_values("exit_time")
    df["equity"] = (1 + df["pnl_pct"]).cumprod()
    peak = df["equity"].cummax()
    dd = (df["equity"] - peak) / peak
    m.max_drawdown = float(dd.min()) if len(dd) else 0.0

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    days = max((end_ts - start_ts).days, 1)
    m.trading_days = days
    m.signals_per_day = m.trade_count / days

    df["year"] = pd.to_datetime(df["exit_time"]).dt.year.astype(str)
    for y, g in df.groupby("year"):
        m.yearly_returns[y] = float((1 + g["pnl_pct"]).prod() - 1)

    return m
