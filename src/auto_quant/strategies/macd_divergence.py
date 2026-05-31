"""30min MACD bottom divergence + daily MA trend filter."""

from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt
import numpy as np
import pandas as pd


@dataclass
class StrategyParams:
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ma_trend: int = 20
    stop_loss_pct: float = -0.03
    hold_days: int = 6


def compute_macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist}, index=close.index)


def detect_bottom_divergence(
    price: pd.Series,
    dif: pd.Series,
    lookback: int = 10,
) -> pd.Series:
    """Price at window low while DIF forms higher low (bullish divergence proxy)."""
    signals = pd.Series(False, index=price.index)
    for i in range(lookback, len(price)):
        p_win = price.iloc[i - lookback : i + 1]
        d_win = dif.iloc[i - lookback : i + 1]
        if price.iloc[i] <= p_win.min() * 1.001 and dif.iloc[i] > d_win.min():
            signals.iloc[i] = True
    return signals


def build_signals(
    min30: pd.DataFrame,
    daily: pd.DataFrame,
    params: StrategyParams,
) -> pd.DataFrame:
    """Build entry timestamps on 30min bars aligned with daily MA filter."""
    macd = compute_macd(min30["close"], params.macd_fast, params.macd_slow, params.macd_signal)
    div = detect_bottom_divergence(min30["close"], macd["dif"])
    golden = (macd["dif"] > macd["dea"]) & (macd["dif"].shift(1) <= macd["dea"].shift(1))
    # 底背离与金叉很少同一根 K 线：金叉时要求近期（8根bar内）出现过背离
    div_recent = div.rolling(8, min_periods=1).max().astype(bool)
    entry_raw = div_recent & golden

    daily_ma = daily["close"].rolling(params.ma_trend).mean()
    daily_trend = daily["close"] > daily_ma
    daily_trend = daily_trend.reindex(min30.index, method="ffill").fillna(False)

    entries = entry_raw & daily_trend
    out = min30.copy()
    out["entry"] = entries
    out["dif"] = macd["dif"]
    out["dea"] = macd["dea"]
    return out


class MacdDivergenceStrategy(bt.Strategy):
    """Single-symbol backtrader strategy driven by precomputed entry series."""

    params = (
        ("entry_series", None),
        ("stop_loss_pct", -0.03),
        ("hold_bars", 48),  # ~6 trading days * 8 bars/day for 30min
        ("stake", 100),
    )

    def __init__(self):
        self.entry_series = self.p.entry_series or {}
        self.entry_idx = {ts: True for ts in self.entry_series if self.entry_series[ts]}
        self.entry_bar = 0
        self.entry_price = 0.0

    def next(self):
        dt = self.data.datetime.datetime(0)
        if self.position:
            pnl_pct = (self.data.close[0] - self.entry_price) / self.entry_price
            bars_held = len(self) - self.entry_bar
            if pnl_pct <= self.p.stop_loss_pct or bars_held >= self.p.hold_bars:
                self.close()
            return

        if dt in self.entry_idx and self.entry_idx[dt]:
            self.buy(size=self.p.stake)
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]

    def notify_trade(self, trade):
        if trade.isclosed:
            self.broker._trade_returns.append(trade.pnlcomm / trade.value if trade.value else 0)


class MultiSymbolBacktest:
    """Run signals across many symbols and collect trades."""

    def __init__(self, universe_data: dict, params: StrategyParams, settings: dict):
        self.universe_data = universe_data
        self.params = params
        self.settings = settings

    def run(self) -> list[dict]:
        hold_bars = max(8, self.params.hold_days * 8)
        trades: list[dict] = []
        initial = self.settings["backtest"]["initial_cash"]
        commission = self.settings["costs"]["commission"]
        stamp = self.settings["costs"]["stamp_tax"]
        slip = self.settings["costs"]["slippage"]

        for symbol, frames in self.universe_data.items():
            daily, min30 = frames["daily"], frames["min30"]
            sig_df = build_signals(min30, daily, self.params)
            entry_map = {ts: bool(v) for ts, v in sig_df["entry"].items() if v}

            if not entry_map:
                continue

            cerebro = bt.Cerebro()
            data = bt.feeds.PandasData(dataname=min30)
            cerebro.adddata(data)
            cerebro.broker.setcash(initial / max(len(self.universe_data), 1))
            cerebro.broker.setcommission(commission=commission)
            cerebro.broker.set_slippage_perc(slip)
            cerebro.addstrategy(
                MacdDivergenceStrategy,
                entry_series=entry_map,
                stop_loss_pct=self.params.stop_loss_pct,
                hold_bars=hold_bars,
                stake=100,
            )
            cerebro.run()
            # Extract closed trades from strategy
            strat = cerebro.runstrats[0][0]
            for t in getattr(strat, "_closed_trades", []):
                trades.append({"symbol": symbol, **t})

        return trades
