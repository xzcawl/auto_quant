"""Event-driven trade simulation from MACD divergence signals."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from auto_quant.strategies.macd_divergence import StrategyParams, build_signals


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    return_pct: float
    pnl_pct: float  # after costs
    hold_bars: int
    exit_reason: str


def simulate_symbol_trades(
    symbol: str,
    min30: pd.DataFrame,
    daily: pd.DataFrame,
    params: StrategyParams,
    *,
    commission: float = 0.00025,
    stamp_tax: float = 0.001,
    slippage: float = 0.001,
) -> list[Trade]:
    sig = build_signals(min30, daily, params)
    trades: list[Trade] = []
    hold_bars = max(8, params.hold_days * 8)
    i = 0
    idx = sig.index.tolist()
    n = len(idx)

    while i < n:
        if not sig["entry"].iloc[i]:
            i += 1
            continue
        entry_price = float(sig["close"].iloc[i]) * (1 + slippage)
        entry_time = idx[i]
        j = i + 1
        exit_reason = "hold_expired"
        exit_price = entry_price
        exit_time = entry_time

        while j < n:
            bar_price = float(sig["close"].iloc[j])
            pnl = (bar_price - entry_price) / entry_price
            bars_held = j - i
            if pnl <= params.stop_loss_pct:
                exit_price = bar_price * (1 - slippage)
                exit_time = idx[j]
                exit_reason = "stop_loss"
                break
            if bars_held >= hold_bars:
                exit_price = bar_price * (1 - slippage)
                exit_time = idx[j]
                exit_reason = "hold_expired"
                break
            j += 1
        else:
            if j > i + 1:
                exit_price = float(sig["close"].iloc[-1]) * (1 - slippage)
                exit_time = idx[-1]
            i += 1
            continue

        gross_ret = (exit_price - entry_price) / entry_price
        cost = commission * 2 + stamp_tax  # sell stamp
        net_ret = gross_ret - cost
        trades.append(
            Trade(
                symbol=symbol,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                return_pct=gross_ret,
                pnl_pct=net_ret,
                hold_bars=j - i,
                exit_reason=exit_reason,
            )
        )
        i = j + 1

    return trades


def simulate_universe(
    universe_data: dict[str, dict[str, pd.DataFrame]],
    params: StrategyParams,
    costs: dict,
) -> list[Trade]:
    all_trades: list[Trade] = []
    for symbol, frames in universe_data.items():
        t = simulate_symbol_trades(
            symbol,
            frames["min30"],
            frames["daily"],
            params,
            commission=costs.get("commission", 0.00025),
            stamp_tax=costs.get("stamp_tax", 0.001),
            slippage=costs.get("slippage", 0.001),
        )
        all_trades.extend(t)
    return all_trades
