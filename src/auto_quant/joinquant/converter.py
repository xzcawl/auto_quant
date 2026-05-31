"""Convert local best params to JoinQuant strategy skeleton."""

from __future__ import annotations

import json
from pathlib import Path

from auto_quant.config import load_settings, project_root
from auto_quant.joinquant.queue import TaskQueue


def generate_jq_strategy(params: dict) -> str:
    """Generate JoinQuant-compatible strategy code."""
    ma = params.get("ma_trend", 20)
    stop = params.get("stop_loss_pct", -0.03)
    hold = params.get("hold_days", 6)
    fast = params.get("macd_fast", 12)
    slow = params.get("macd_slow", 26)
    signal = params.get("macd_signal", 9)

    return f'''# auto_quant generated - MACD bottom divergence
import jqdata
import pandas as pd
import numpy as np

def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
        open_commission=0.00025, close_commission=0.00025, min_commission=5), type='stock')
    g.universe = get_index_stocks('000300.XSHG')
    g.ma_trend = {ma}
    g.stop_loss = {stop}
    g.hold_days = {hold}
    g.fast, g.slow, g.sig = {fast}, {slow}, {signal}
    g.entry_date = {{}}
    run_daily(trade, time='14:30')

def _macd(close, fast, slow, sig):
    ema_f = close.ewm(span=fast).mean()
    ema_s = close.ewm(span=slow).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=sig).mean()
    return dif, dea

def trade(context):
    for stock in g.universe:
        if stock in context.portfolio.positions:
            pos = context.portfolio.positions[stock]
            cost = pos.avg_cost
            if cost > 0 and (pos.price - cost) / cost <= g.stop_loss:
                order_target(stock, 0)
                g.entry_date.pop(stock, None)
                continue
            days = (context.current_dt.date() - g.entry_date.get(stock, context.current_dt.date())).days
            if days >= g.hold_days:
                order_target(stock, 0)
                g.entry_date.pop(stock, None)
            continue

        bars = get_bars(stock, count=60, unit='30m', fields=['close'])
        if bars is None or len(bars) < 30:
            continue
        close = pd.Series(bars['close'])
        dif, dea = _macd(close, g.fast, g.slow, g.sig)
        # simplified: golden cross on last bar
        if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]:
            daily = get_bars(stock, count=g.ma_trend+5, unit='1d', fields=['close'])
            if daily is None or len(daily) < g.ma_trend:
                continue
            ma = pd.Series(daily['close']).rolling(g.ma_trend).mean().iloc[-1]
            if close.iloc[-1] > ma:
                order_target_value(stock, context.portfolio.total_value / max(len(g.universe), 1) * 0.05)
                g.entry_date[stock] = context.current_dt.date()
'''


def enqueue_best_strategy() -> int:
    best_path = project_root() / "output" / "best_result.json"
    if not best_path.exists():
        settings = load_settings()
        params = settings["strategy"]
    else:
        data = json.loads(best_path.read_text(encoding="utf-8"))
        params = data.get("params", data)

    out_dir = project_root() / "output" / "jq_strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    code_path = out_dir / "macd_divergence_best.py"
    code_path.write_text(generate_jq_strategy(params), encoding="utf-8")

    q = TaskQueue()
    return q.enqueue("macd_divergence_best", str(code_path), priority=10)
