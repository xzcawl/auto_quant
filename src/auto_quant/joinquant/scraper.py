"""Parse JoinQuant backtest result page (best-effort selectors)."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_metrics_from_text(text: str) -> dict[str, Any]:
    """Extract metrics from page text with regex fallbacks."""
    metrics: dict[str, Any] = {}

    patterns = {
        "annual_return": r"年化收益[^\d-]*(-?[\d.]+)%?",
        "sharpe": r"夏普[^\d-]*(-?[\d.]+)",
        "max_drawdown": r"最大回撤[^\d-]*(-?[\d.]+)%?",
        "win_rate": r"胜率[^\d-]*(-?[\d.]+)%?",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if key in ("annual_return", "max_drawdown", "win_rate") and abs(val) > 1:
                val = val / 100.0
            metrics[key] = val

    # 过滤明显误解析（如页面其它数字）
    if "sharpe" in metrics and (metrics["sharpe"] > 20 or metrics["sharpe"] < -10):
        del metrics["sharpe"]
    if "max_drawdown" in metrics and metrics["max_drawdown"] > 5:
        del metrics["max_drawdown"]
    if "annual_return" in metrics and abs(metrics["annual_return"]) > 50:
        del metrics["annual_return"]

    return metrics


def backtest_metrics_plausible(metrics: dict[str, Any], page_text: str = "") -> bool:
    """Reject placeholder / partial-page parses (e.g. 7% total on a 5y equity run)."""
    if page_text and any(s in page_text for s in ("运行中", "回测中", "编译中", "排队")):
        return False
    tr = metrics.get("total_return")
    ar = metrics.get("annual_return")
    if tr is not None:
        try:
            if float(tr) >= 0.5:
                return True
        except (TypeError, ValueError):
            pass
    if ar is not None:
        try:
            if abs(float(ar)) >= 0.15:
                return True
        except (TypeError, ValueError):
            pass
    if page_text and re.search(
        r"策略收益\s*[\d,]{2,}[.\d]*%|累计收益\s*[\d,]{2,}", page_text
    ):
        return True
    return False


def append_markdown_summary(strategy_name: str, metrics: dict[str, Any], status: str) -> None:
    from auto_quant.config import project_root

    path = project_root() / "output" / "回测结果.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"\n## {strategy_name} ({status})\n"
        f"- 报告: {metrics.get('report_url', 'N/A')}\n"
        f"- 编辑: {metrics.get('edit_url', 'N/A')}\n"
        f"- 年化: {metrics.get('annual_return', 'N/A')}\n"
        f"- 夏普: {metrics.get('sharpe', 'N/A')}\n"
        f"- 最大回撤: {metrics.get('max_drawdown', 'N/A')}\n"
        f"- 胜率: {metrics.get('win_rate', 'N/A')}\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def detect_no_credits(page_text: str) -> bool:
    keywords = ["积分不足", "积分不够", "余额不足", "负积分"]
    return any(k in page_text for k in keywords)
