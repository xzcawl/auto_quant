"""Check JoinQuant backtest report URL status (complete / running / failed)."""

from __future__ import annotations

import re
import time
from typing import Any

from auto_quant.joinquant.scraper import detect_no_credits, parse_metrics_from_text

_ACTIVE_MARKERS = (
    "运行中",
    "回测中",
    "编译中",
    "正在编译",
    "正在回测",
    "排队中",
    "排队",
    "加载中",
)

_COMPLETE_MARKERS = (
    "年化收益",
    "策略收益",
    "夏普比率",
    "最大回撤",
    "回测完成",
)


def classify_report_text(text: str) -> str:
    """
    Return: complete | running | failed | no_credits | unknown
    """
    if detect_no_credits(text):
        return "no_credits"
    if "已取消" in text:
        return "failed"
    if any(s in text for s in _ACTIVE_MARKERS):
        return "running"
    if "编译失败" in text or "回测失败" in text:
        if "并行" in text and "最多" in text:
            return "running"
        return "failed"
    metrics = parse_metrics_from_text(text)
    if metrics.get("total_return") is not None or metrics.get("annual_return") is not None:
        from auto_quant.joinquant.scraper import backtest_metrics_plausible

        if backtest_metrics_plausible(metrics, text):
            return "complete"
        return "running"
    if re.search(r"年化\s*[\d.]+%|策略收益\s*[\d.]+%", text):
        return "complete"
    # 仅有指标标签、数值为 -- 时不能视为完成
    if any(s in text for s in _COMPLETE_MARKERS):
        return "unknown"
    return "unknown"


def fetch_report_status(page, report_url: str) -> dict[str, Any]:
    """Open report URL and classify backtest state."""
    print(f"[jq] 检查报告: {report_url}")
    page.goto(report_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    try:
        text = page.inner_text("body")
    except Exception as e:
        return {"status": "unknown", "metrics": {}, "report_url": report_url, "error": str(e)}

    status = classify_report_text(text)
    metrics = parse_metrics_from_text(text)
    if metrics and status in ("unknown", "running"):
        # 有指标且页面无「运行中」→ 视为完成
        if not any(s in text for s in _ACTIVE_MARKERS):
            status = "complete"

    return {
        "status": status,
        "metrics": metrics,
        "report_url": report_url,
        "has_metrics": bool(metrics),
    }


def wait_for_report_complete(
    page,
    report_url: str,
    jq: dict[str, Any],
    *,
    edit_url: str = "",
    strategy_display_name: str = "",
) -> dict[str, Any]:
    """Poll report URL until complete or timeout."""
    timeout = int(jq.get("poll_timeout_sec", 3600))
    interval = int(jq.get("poll_interval_sec", 15))
    deadline = time.time() + timeout

    while time.time() < deadline:
        info = fetch_report_status(page, report_url)
        status = info["status"]
        if status == "complete":
            metrics = dict(info.get("metrics") or {})
            metrics["report_url"] = report_url
            if edit_url:
                metrics["edit_url"] = edit_url
            if strategy_display_name:
                metrics["strategy_display_name"] = strategy_display_name
            print(f"[jq] 回测已完成: {report_url}")
            return metrics
        if status == "failed":
            return {
                "report_url": report_url,
                "edit_url": edit_url,
                "error": "backtest_failed",
                "raw_note": "聚宽报告页显示失败",
            }
        if status == "no_credits":
            return {"report_url": report_url, "error": "no_credits"}

        print(f"[jq] 回测进行中 ({status})，{interval}s 后再查…")
        time.sleep(interval)

    return {
        "report_url": report_url,
        "edit_url": edit_url,
        "raw_note": "等待已有回测完成超时，请打开 report_url 人工查看",
    }
