"""
Extract JoinQuant backtest metrics for ablation reports.

Uses the same data layer as report_analysis_tools.py (get_backtest API in 聚宽研究环境).
Locally: merge jq_metrics_export.json exported from research notebook.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from auto_quant.joinquant.report_analysis_tools import (
    REF_RATE,
    calc_max_drawdown,
    get_backtest_data,
    get_performance_row,
)

# 消融对比必备 + idea.md 相关扩展
ABLATION_COMPARE_KEYS = (
    "annual_return",
    "sharpe",
    "max_drawdown",
    "win_rate",
    "total_return",
    "alpha",
    "beta",
    "volatility",
    "trade_win_rate",
    "pnl_ratio",
    "calmar",
    "turnover_annual",
    "avg_hold_days",
    "trade_count",
    "empty_position_ratio",
)


def annualize_total_return(
    total_return: float,
    start_date: str,
    end_date: str,
    *,
    use_trading_days: bool = True,
) -> float | None:
    """
    由区间总收益推算年化（与 report_analysis_tools.get_performance_row 一致：250 日/年）。

    total_return: 小数形式，如 1.0708 表示 +107.08%
    """
    from datetime import datetime

    try:
        d0 = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    calendar_days = max((d1 - d0).days, 1)
    if use_trading_days:
        n = max(int(calendar_days * 250 / 365.25), 1)
        return (1.0 + total_return) ** (250.0 / n) - 1.0
    return (1.0 + total_return) ** (365.25 / calendar_days) - 1.0


def reconcile_metrics_dates(
    metrics: dict[str, Any],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """
    页面抓取的「年化」常与「策略总收益」不一致；有总收益时按回测区间重算年化。
    """
    tr = metrics.get("total_return")
    if tr is None or not start_date or not end_date:
        return metrics

    computed = annualize_total_return(float(tr), start_date, end_date)
    if computed is None:
        return metrics

    scraped = metrics.get("annual_return")
    # 页面已有「策略年化收益」且与总收益量级合理 → 保留页面值
    if scraped is not None and tr > 0.2 and scraped >= tr * 0.3:
        return metrics
    replace = scraped is None
    if scraped is not None and tr > 0.2:
        if computed > scraped * 1.5 or scraped < tr * 0.15:
            replace = True
    if replace:
        metrics["annual_return"] = computed
        metrics["annual_return_note"] = (
            f"由总收益 {tr:.2%} 与区间 {start_date}~{end_date} 按 250 交易日/年推算"
        )
    return metrics


def backtest_id_from_url(url: str) -> str | None:
    if not url:
        return None
    q = parse_qs(urlparse(url).query)
    for key in ("backtestId", "backtest_id", "id"):
        if key in q and q[key]:
            return str(q[key][0])
    m = re.search(r"backtestId=([a-f0-9]+)", url, re.I)
    return m.group(1) if m else None


def _resolve_get_backtest() -> Callable[[str], Any] | None:
    """JoinQuant research injects get_backtest globally."""
    try:
        fn = get_backtest  # type: ignore[name-defined]
        if callable(fn):
            return fn
    except NameError:
        pass
    return None


def load_backtest_pack(backtest_id: str, *, project_dir: Path | None = None) -> dict[str, Any] | None:
    """Load raw backtest pack; JQ research get_backtest or cached local pack."""
    if _resolve_get_backtest():
        return get_backtest_data(backtest_id)
    if project_dir is not None:
        from auto_quant.joinquant.report_pack_io import default_pack_dir, load_pack

        packs_dir = default_pack_dir(project_dir)
        if packs_dir.is_dir():
            for path in packs_dir.glob("*.json"):
                pack = load_pack(path)
                if pack and pack.get("backtest_id") == backtest_id:
                    return pack
    return None


def _nav_frame_from_results(results: list[dict]) -> pd.DataFrame | None:
    if not results:
        return None
    df = pd.DataFrame(results)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    df.set_index("time", inplace=True)
    df["nav"] = 1 + df["returns"]
    df["d_ret"] = df["nav"].pct_change()
    df["d_ret"].iloc[0] = df["returns"].iloc[0]
    df["b_nav"] = 1 + df["benchmark_returns"]
    df["b_ret"] = df["b_nav"].pct_change()
    df["b_ret"].iloc[0] = df["benchmark_returns"].iloc[0]
    return df


def calc_trade_quality_metrics(
    orders: list[dict],
    starting_cash: float | None,
    df_nav: pd.DataFrame,
) -> dict[str, Any]:
    if not orders:
        return {}
    od = pd.DataFrame(orders)
    od["time"] = pd.to_datetime(od["time"])
    if "filled" in od.columns:
        od = od[od["filled"] > 0].copy()
    if "gains" not in od.columns:
        return {}
    od["gains"] = pd.to_numeric(od["gains"], errors="coerce").fillna(0)
    close_od = od[od["action"] == "close"] if "action" in od.columns else od
    net_gains = close_od["gains"]
    total = len(close_od)
    if total == 0:
        return {}
    win = int((net_gains > 0).sum())
    loss = int((net_gains <= 0).sum())
    win_rate = win / total
    avg_win = float(net_gains[net_gains > 0].mean()) if win > 0 else 0.0
    avg_loss = float(net_gains[net_gains < 0].mean()) if loss > 0 else 0.0
    pnl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else None
    expected = win_rate * avg_win + (1 - win_rate) * avg_loss

    days = len(df_nav)
    s_tot = (1 + df_nav["d_ret"]).cumprod().iloc[-1] - 1
    ann = (1 + s_tot) ** (250 / days) - 1 if days > 0 else 0.0
    mdd_nav = (1 + df_nav["d_ret"]).cumprod()
    mdd = float(((mdd_nav - mdd_nav.cummax()) / mdd_nav.cummax()).min())
    calmar = ann / abs(mdd) if mdd != 0 else None

    return {
        "trade_count": total,
        "trade_win_rate": win_rate,
        "pnl_ratio": pnl_ratio,
        "expected_pnl_per_trade": expected,
        "calmar": calmar,
        "total_commission": float(od["commission"].sum()) if "commission" in od.columns else None,
    }


def calc_holding_behavior_metrics(orders: list[dict]) -> dict[str, Any]:
    if not orders:
        return {}
    od = pd.DataFrame(orders)
    od["time"] = pd.to_datetime(od["time"])
    if "action" not in od.columns or "security" not in od.columns:
        return {}
    if "filled" in od.columns:
        od = od[od["filled"] > 0].copy()

    buy_od = od[od["action"] == "open"][["security", "time"]].rename(columns={"time": "buy_time"})
    sell_od = od[od["action"] == "close"][["security", "time"]].rename(columns={"time": "sell_time"})
    buy_queues: dict[str, list] = {}
    for _, r in buy_od.iterrows():
        buy_queues.setdefault(r["security"], []).append(r["buy_time"])
    pairs: list[int] = []
    for _, r in sell_od.iterrows():
        sec = r["security"]
        if buy_queues.get(sec):
            bt = buy_queues[sec].pop(0)
            pairs.append((r["sell_time"] - bt).days)

    out: dict[str, Any] = {}
    if pairs:
        pairs_s = pd.Series(pairs)
        out["avg_hold_days"] = float(pairs_s.mean())
        out["median_hold_days"] = float(pairs_s.median())
        out["hold_pairs"] = len(pairs)

    if "filled" in od.columns and "price" in od.columns:
        od["turnover"] = pd.to_numeric(od["filled"], errors="coerce") * pd.to_numeric(
            od["price"], errors="coerce"
        )
        total_buy = od[od["action"] == "open"]["turnover"].sum()
        date_range = (od["time"].max() - od["time"].min()).days
        if date_range > 0 and not np.isnan(total_buy):
            out["turnover_annual"] = float((total_buy / date_range) * 250)

    return out


def calc_position_metrics(balances: list[dict], starting_cash: float | None) -> dict[str, Any]:
    if not balances:
        return {}
    bl = pd.DataFrame(balances)
    bl["time"] = pd.to_datetime(bl["time"])
    tv_col = "total_value" if "total_value" in bl.columns else ("value" if "value" in bl.columns else None)
    if not tv_col or "cash" not in bl.columns:
        return {}
    bl["stock_value"] = pd.to_numeric(bl[tv_col], errors="coerce") - pd.to_numeric(
        bl["cash"], errors="coerce"
    )
    bl["util_rate"] = bl["stock_value"] / pd.to_numeric(bl[tv_col], errors="coerce")
    total_days = len(bl)
    empty_days = int((bl["util_rate"] < 0.05).sum())
    return {
        "empty_position_ratio": empty_days / total_days if total_days else None,
        "avg_position_util": float(bl["util_rate"].mean()),
    }


def _safe_float(x: Any) -> float | None:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def extract_metrics_from_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """
    Core metrics for ablation compare (aligned with report_analysis_tools §1/6/7/9).
    """
    results = pack.get("results") or []
    df = _nav_frame_from_results(results)
    if df is None or df.empty:
        return {}

    bench_name = pack.get("bench_name", "基准指数")
    starting_cash = pack.get("starting_cash")
    orders = pack.get("orders") or []
    balances = pack.get("balances") or []
    risk = pack.get("risk") or {}

    row = get_performance_row(df["d_ret"], df["b_ret"], 1, bench_name)
    metrics: dict[str, Any] = {
        "total_return": _safe_float(row.get("总收益")),
        "annual_return": _safe_float(row.get("年化收益")),
        "sharpe": _safe_float(row.get("夏普比率")),
        "max_drawdown": _safe_float(row.get("最大回撤")),
        "win_rate": _safe_float(row.get("日胜率")),
        "volatility": _safe_float(row.get("收益波动率")),
        "alpha": _safe_float(row.get("alpha")),
        "beta": _safe_float(row.get("beta")),
        "data_source": "report_analysis_tools",
    }

    # 官方风险指标优先（与聚宽回测页对齐）
    if risk:
        for src, dst in (
            ("sharpe", "sharpe"),
            ("max_drawdown", "max_drawdown"),
            ("algorithm_return", "total_return"),
            ("algorithm_volatility", "volatility"),
            ("alpha", "alpha"),
            ("beta", "beta"),
            ("calmar", "calmar"),
            ("sortino", "sortino"),
            ("information_ratio", "information_ratio"),
        ):
            v = _safe_float(risk.get(src))
            if v is not None:
                metrics[dst] = v

    metrics.update(calc_trade_quality_metrics(orders, starting_cash, df))
    metrics.update(calc_holding_behavior_metrics(orders))
    metrics.update(calc_position_metrics(balances, starting_cash))

    if metrics.get("win_rate") is None and metrics.get("trade_win_rate") is not None:
        metrics["win_rate"] = metrics["trade_win_rate"]

    return metrics


def fetch_metrics_by_backtest_id(backtest_id: str) -> dict[str, Any] | None:
    pack = load_backtest_pack(backtest_id)
    if not pack:
        return None
    return extract_metrics_from_pack(pack)


def fetch_metrics_by_report_url(report_url: str) -> dict[str, Any] | None:
    bid = backtest_id_from_url(report_url)
    if not bid:
        return None
    m = fetch_metrics_by_backtest_id(bid)
    if m:
        m["report_url"] = report_url
        m["backtest_id"] = bid
    return m


def load_metrics_export(path: Path) -> dict[str, dict[str, Any]]:
    """Load JSON from 聚宽研究环境运行 scripts/jq_research_export_metrics.py 的输出。"""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "variants" in data:
        return data["variants"]
    return data if isinstance(data, dict) else {}


def jq_research_available() -> bool:
    return _resolve_get_backtest() is not None
