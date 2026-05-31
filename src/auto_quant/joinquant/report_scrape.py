"""Scrape JoinQuant backtest detail page metrics via Playwright (local fallback)."""

from __future__ import annotations

import json
import re
from typing import Any

from auto_quant.joinquant.scraper import parse_metrics_from_text

# 聚宽「收益概述」区为「标签行 + 下一行数值」（见 debug idea2_page.txt）
_LINE_LABEL_MAP = (
    ("策略年化收益", "annual_return"),
    ("年化收益", "annual_return"),
    ("策略收益", "total_return"),
    ("累计收益", "total_return"),
    ("夏普比率", "sharpe"),
    ("最大回撤", "max_drawdown"),
    ("日胜率", "win_rate"),
    ("胜率", "win_rate"),
    ("盈亏比", "pnl_ratio"),
    ("索提诺比率", "sortino"),
    ("信息比率", "information_ratio"),
)

_JS_SCRAPE_LINE_METRICS = (
    """
() => {
  const lines = (document.body.innerText || '').split(/\\n+/).map(s => s.trim()).filter(Boolean);
  const out = {};
  const map = """
    + json.dumps(list(_LINE_LABEL_MAP), ensure_ascii=False)
    + """;
  for (let i = 0; i < lines.length - 1; i++) {
    const lab = lines[i];
    for (const [label, key] of map) {
      if (lab === label) {
        const val = lines[i + 1];
        if (val && /^-?[\\d.]+%?$/.test(val.replace(/,/g, ''))) {
          if (!out[key]) out[key] = val;
        }
      }
    }
  }
  return out;
}
"""
)


def _parse_pct_number(raw: Any, *, key: str = "") -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if not s or s == "-":
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        v = float(s)
        if key == "sharpe":
            return v
        return v
    except ValueError:
        return None


def _parse_line_metrics(text: str) -> dict[str, str]:
    """Python fallback: label line + next line value."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: dict[str, str] = {}
    for i, lab in enumerate(lines[:-1]):
        for label, key in _LINE_LABEL_MAP:
            if lab == label and key not in out:
                val = lines[i + 1]
                if re.match(r"^-?[\d.]+%?$", val.replace(",", "")):
                    out[key] = val
    return out


def normalize_scraped(raw: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {"data_source": "playwright_scrape"}
    mapping = {
        "annual_return": "annual_return",
        "sharpe": "sharpe",
        "max_drawdown": "max_drawdown",
        "win_rate": "win_rate",
        "total_return": "total_return",
        "pnl_ratio": "pnl_ratio",
    }
    for src, dst in mapping.items():
        raw_s = raw.get(src)
        v = _parse_pct_number(raw_s, key=dst)
        if v is not None:
            metrics[dst] = v
        elif raw_s and dst in ("annual_return", "total_return", "win_rate"):
            # 无 % 后缀的裸数字（如 107.08）按百分数处理
            try:
                bare = float(str(raw_s).replace(",", ""))
                if abs(bare) > 1.5:
                    metrics[dst] = bare / 100.0
            except ValueError:
                pass

    sh = metrics.get("sharpe")
    if sh is not None and sh > 3:
        metrics["sharpe"] = sh / 100.0

    mdd = metrics.get("max_drawdown")
    if mdd is not None:
        if mdd > 0:
            mdd = -abs(mdd)
        if abs(mdd) > 1.5:
            mdd = mdd / 100.0
        metrics["max_drawdown"] = mdd

    return metrics


def scrape_report_url(
    page,
    report_url: str,
    *,
    start_date: str = "",
    end_date: str = "",
    wait_complete: bool = False,
) -> dict[str, Any]:
    """Open report detail page and extract metrics."""
    print(f"[scrape] {report_url}")
    if wait_complete:
        from auto_quant.config import load_settings
        from auto_quant.joinquant.report_status import wait_for_report_complete

        jq = dict(load_settings().get("joinquant") or {})
        jq.setdefault("poll_timeout_sec", 1800)
        jq.setdefault("poll_interval_sec", 20)
        wait_for_report_complete(page, report_url, jq)
    page.goto(report_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(4000)
    try:
        page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass
    # 确保在「收益概述」Tab
    try:
        tab = page.get_by_text("收益概述", exact=True)
        if tab.count() > 0:
            tab.first.click(timeout=3000)
            page.wait_for_timeout(1500)
    except Exception:
        pass

    text = page.inner_text("body")
    raw: dict[str, str] = {}
    try:
        raw = page.evaluate(_JS_SCRAPE_LINE_METRICS) or {}
    except Exception:
        pass
    if not raw.get("total_return"):
        raw.update(_parse_line_metrics(text))

    metrics = normalize_scraped(raw)
    parsed = parse_metrics_from_text(text)
    for k, v in parsed.items():
        if metrics.get(k) is None:
            metrics[k] = v

    from auto_quant.joinquant.scraper import backtest_metrics_plausible

    metrics["report_url"] = report_url
    if metrics.get("total_return") is not None and not backtest_metrics_plausible(metrics, text):
        metrics["raw_note"] = "指标疑似未完成回测的占位值，请稍后重抓"
        metrics.pop("total_return", None)
        metrics.pop("annual_return", None)
    if not metrics.get("annual_return") and not metrics.get("total_return"):
        metrics["raw_note"] = "页面抓取未解析到核心指标，请核对 report_url"

    return finalize_scraped_metrics(metrics, start_date=start_date, end_date=end_date)


def finalize_scraped_metrics(
    metrics: dict[str, Any],
    *,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    from auto_quant.joinquant.report_metrics import reconcile_metrics_dates

    # 若已有「策略年化收益」则不再用总收益覆盖年化
    if metrics.get("annual_return") and metrics.get("annual_return_note"):
        pass
    elif start_date and end_date and metrics.get("total_return") and not metrics.get("annual_return"):
        metrics = reconcile_metrics_dates(metrics, start_date, end_date)
    elif start_date and end_date:
        # 仅当抓取年化明显偏低时用总收益推算
        metrics = reconcile_metrics_dates(metrics, start_date, end_date)
    return metrics


def scrape_all_variants(
    project_dir,
    variants: dict[str, Any],
    *,
    headless: bool = False,
    start_date: str = "",
    end_date: str = "",
    only_ids: list[str] | None = None,
) -> dict[str, dict]:
    """Scrape all report_urls in jq_links variants."""
    from auto_quant.config import load_settings
    from auto_quant.joinquant.playwright_runner import _auth_path

    auth = _auth_path()
    if not auth.exists():
        raise FileNotFoundError("未找到登录态 config/jq_auth.json，请先 run_jq_batch.py --login")

    jq = load_settings()["joinquant"]
    from playwright.sync_api import sync_playwright

    out: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless or jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth))
        page = context.new_page()
        try:
            for vid, entry in variants.items():
                if only_ids and vid not in only_ids:
                    continue
                url = entry.get("report_url") or (entry.get("metrics") or {}).get("report_url")
                if not url:
                    print(f"[skip] {vid}: 无 report_url")
                    continue
                try:
                    m = scrape_report_url(
                        page, url, start_date=start_date, end_date=end_date
                    )
                    m["strategy_display_name"] = entry.get("strategy_display_name", "")
                    out[vid] = m
                    tr = m.get("total_return")
                    ar = m.get("annual_return")
                    print(
                        f"[ok] {vid}: 策略收益={tr:.2%} 年化={ar:.2%} "
                        f"夏普={m.get('sharpe')} 回撤={m.get('max_drawdown')}"
                        if tr is not None and ar is not None
                        else f"[ok] {vid}: {m}"
                    )
                except Exception as e:
                    print(f"[fail] {vid}: {e}")
                page.wait_for_timeout(1500)
        finally:
            browser.close()
    return out
