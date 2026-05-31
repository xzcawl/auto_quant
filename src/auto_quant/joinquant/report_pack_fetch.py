"""
Fetch JoinQuant backtest detail data locally via Playwright (authenticated fetch).

Builds a pack compatible with report_analysis_tools.analyze_strategy_performance:
  results, orders, balances, risk, bench_name, starting_cash
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd

from auto_quant.joinquant.report_metrics import backtest_id_from_url

JQ_BACKTEST_BASE = "https://www.joinquant.com/algorithm/backtest"
DEFAULT_STARTING_CASH = 100_000.0


def _fetch_json(page, url: str) -> dict[str, Any]:
    return page.evaluate(
        """async (u) => {
            const res = await fetch(u, {credentials: 'include'});
            const text = await res.text();
            if (!text) return {status: res.status, data: null};
            try { return {status: res.status, data: JSON.parse(text)}; }
            catch (e) { return {status: res.status, raw: text.slice(0, 500)}; }
        }""",
        url,
    )


def _api_url(path: str, backtest_id: str) -> str:
    return f"{JQ_BACKTEST_BASE}/{path}?backtestId={backtest_id}"


def _parse_security(stock: str) -> tuple[str, str]:
    m = re.match(r"(.+)\(([^)]+)\)", stock or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return stock or "", stock or ""


def _parse_shares(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).replace("股", "").replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _parse_price(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def chart_series_to_results(
    algo: list[dict[str, Any]],
    bench: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert page chart cumulative return (% points) to get_results()-like rows."""
    if not algo:
        return []
    n = min(len(algo), len(bench) if bench else len(algo))
    rows: list[dict[str, Any]] = []
    for i in range(n):
        pt = algo[i]
        y = pt.get("y")
        if y is None:
            break
        bp = bench[i] if bench else {"y": 0}
        by = bp.get("y") if isinstance(bp, dict) else bp
        ts = pd.Timestamp(int(pt["x"]), unit="ms")
        rows.append(
            {
                "time": ts.strftime("%Y-%m-%d"),
                "returns": float(y) / 100.0,
                "benchmark_returns": float(by or 0) / 100.0,
            }
        )
    return rows


def stats_to_risk(stats: dict[str, Any]) -> dict[str, Any]:
    """Map /stats API fields to get_risk()-like dict."""
    key_map = {
        "algorithm_volatility": "algorithm_volatility",
        "benchmark_volatility": "benchmark_volatility",
        "alpha": "alpha",
        "beta": "beta",
        "sharpe": "sharpe",
        "sortino": "sortino",
        "max_drawdown": "max_drawdown",
        "information": "information_ratio",
        "algorithm_return": "algorithm_return",
        "benchmark_return": "benchmark_return",
        "excess_return": "excess_return",
        "annual_algo_return": "annual_algo_return",
        "annual_bm_return": "annual_bm_return",
        "win_ratio": "win_ratio",
        "profit_loss_ratio": "profit_loss_ratio",
        "win_count": "win_count",
        "lose_count": "lose_count",
        "day_win_ratio": "day_win_ratio",
        "avg_trade_return": "avg_trade_return",
        "avg_position_days": "avg_position_days",
        "turnover_rate": "turnover_rate",
        "trading_days": "trading_days",
    }
    out: dict[str, Any] = {}
    for src, dst in key_map.items():
        v = stats.get(src)
        if v is not None and v != "":
            try:
                if dst in ("win_count", "lose_count", "trading_days"):
                    out[dst] = int(v)
                else:
                    out[dst] = float(v)
            except (TypeError, ValueError):
                out[dst] = v
    return out


def transactions_to_orders(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for tx in transactions:
        name, sec = _parse_security(str(tx.get("stock", "")))
        trans = str(tx.get("transaction", ""))
        action = "open" if "买" in trans else "close"
        filled = _parse_shares(tx.get("trueAmount") or tx.get("amount"))
        if filled <= 0:
            continue
        match_time = tx.get("matchTime") or f"{tx.get('date', '')} {tx.get('time', '')}".strip()
        orders.append(
            {
                "time": match_time,
                "security": sec,
                "security_name": name,
                "action": action,
                "filled": filled,
                "price": _parse_price(tx.get("truePrice") or tx.get("price")),
                "commission": _parse_price(tx.get("commission")),
                "gains": _parse_price(tx.get("gains")),
            }
        )
    return orders


def positions_to_balances(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily position snapshots into balance rows (partial on web API)."""
    by_date: dict[str, dict[str, float]] = {}
    for p in positions:
        d = str(p.get("date", ""))[:10]
        if not d:
            continue
        val = _parse_price(p.get("value"))
        total = _parse_price(p.get("totalValue"))
        slot = by_date.setdefault(d, {"stock": 0.0, "total": 0.0})
        slot["stock"] += val
        if total > 0:
            slot["total"] = total

    balances: list[dict[str, Any]] = []
    for d in sorted(by_date.keys()):
        slot = by_date[d]
        total = slot["total"] or slot["stock"]
        cash = max(total - slot["stock"], 0.0)
        balances.append(
            {
                "time": f"{d} 16:00:00",
                "total_value": total,
                "cash": cash,
            }
        )
    return balances


def infer_starting_cash(
    results: list[dict[str, Any]],
    balances: list[dict[str, Any]],
    *,
    default: float = DEFAULT_STARTING_CASH,
) -> float:
    if balances:
        first = balances[0]
        tv = float(first.get("total_value") or 0)
        ret0 = float(results[0].get("returns", 0)) if results else 0.0
        if tv > 0 and abs(ret0) < 0.5:
            return tv / (1.0 + ret0)
    return default


def fetch_page_chart_series(page) -> tuple[list[dict], list[dict], str]:
    """Read cumulative return chart arrays injected by JQ detail page."""
    data = page.evaluate(
        """() => ({
            algo: window.dataResult || window.initDataResult || [],
            bench: window.dataBenchmark || window.initDataBenchmark || [],
            benchName: (window.initData && window.initData.benchmark_name) || ''
        })"""
    )
    return data.get("algo") or [], data.get("bench") or [], str(data.get("benchName") or "")


def fetch_backtest_pack(page, backtest_id: str, *, wait_ms: int = 8000) -> dict[str, Any]:
    """
    Fetch pack for one backtestId using an authenticated Playwright page.

    Daily returns: from page chart globals (full series).
    Risk stats: /stats API.
    Orders/positions: web API (orders truncated ~99 rows; positions partial).
    """
    detail_url = f"https://www.joinquant.com/algorithm/backtest/detail?backtestId={backtest_id}"
    page.goto(detail_url, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(wait_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=25_000)
    except Exception:
        pass

    algo, bench, bench_name = fetch_page_chart_series(page)
    results = chart_series_to_results(algo, bench)

    stats_resp = _fetch_json(page, _api_url("stats", backtest_id))
    stats = ((stats_resp.get("data") or {}).get("data") or {}) if stats_resp.get("status") == 200 else {}
    risk = stats_to_risk(stats)

    tx_resp = _fetch_json(page, _api_url("transactionInfo", backtest_id))
    tx_body = (tx_resp.get("data") or {}).get("data") or tx_resp.get("data") or {}
    transactions = tx_body.get("transaction") or []
    orders = transactions_to_orders(transactions)

    pos_resp = _fetch_json(page, _api_url("positionInfo", backtest_id))
    pos_body = (pos_resp.get("data") or {}).get("data") or pos_resp.get("data") or {}
    positions = pos_body.get("position") or []
    balances = positions_to_balances(positions)

    starting_cash = infer_starting_cash(results, balances)

    notes: list[str] = []
    if stats.get("win_count") and stats.get("lose_count"):
        expected_trades = int(stats["win_count"]) + int(stats["lose_count"])
        close_orders = sum(1 for o in orders if o.get("action") == "close")
        if close_orders < expected_trades:
            notes.append(
                f"网页 API 仅返回 {len(transactions)} 条成交记录，"
                f"stats 显示约 {expected_trades} 笔平仓；第5–7/10节为样本分析"
            )
    if len(balances) < len(results) // 10:
        notes.append(f"持仓快照仅 {len(balances)} 天，第8节仓位利用率为部分区间")

    return {
        "backtest_id": backtest_id,
        "report_url": detail_url,
        "bench_name": bench_name or "基准指数",
        "starting_cash": starting_cash,
        "results": results,
        "orders": orders,
        "balances": balances,
        "risk": risk,
        "period_risks": {},
        "meta": {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "source": "playwright_pack_fetch",
            "trading_days": len(results),
            "orders_count": len(orders),
            "balances_count": len(balances),
            "notes": notes,
        },
    }


def fetch_backtest_pack_from_url(page, report_url: str, **kwargs: Any) -> dict[str, Any] | None:
    bid = backtest_id_from_url(report_url)
    if not bid:
        return None
    pack = fetch_backtest_pack(page, bid, **kwargs)
    pack["report_url"] = report_url
    return pack
