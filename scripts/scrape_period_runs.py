#!/usr/bin/env python
"""Line-parse JQ report metrics into period run JSON files."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from auto_quant.joinquant.playwright_runner import _auth_path


def parse_body(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = {}
    labels = [
        ("策略收益", "total_return"),
        ("策略年化收益", "annual_return"),
        ("年化收益", "annual_return"),
        ("超额收益", "excess_return"),
        ("基准收益", "benchmark_return"),
        ("夏普比率", "sharpe"),
        ("最大回撤", "max_drawdown"),
        ("胜率", "win_rate"),
        ("盈亏比", "pnl_ratio"),
    ]
    for i, lab in enumerate(lines[:-1]):
        for label, key in labels:
            if lines[i] == label and key not in out:
                val = lines[i + 1]
                if key == "sharpe":
                    try:
                        out[key] = float(val.replace(",", ""))
                    except ValueError:
                        pass
                elif "%" in val:
                    out[key] = float(val.replace("%", "")) / 100.0
    if "max_drawdown" in out and out["max_drawdown"] > 0:
        out["max_drawdown"] = -out["max_drawdown"]
    return out


def patch(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    url = data.get("report_url") or (data.get("metrics", {}).get("raw") or {}).get("report_url")
    if not url:
        print("skip", path.name)
        return {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(storage_state=str(_auth_path())).new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        try:
            page.get_by_text("收益概述", exact=True).first.click(timeout=2000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        m = parse_body(page.inner_text("body"))
        browser.close()
    metrics = data.setdefault("metrics", {})
    for k, v in m.items():
        metrics[k] = v
    metrics["raw"] = {**(metrics.get("raw") or {}), **m}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.name, m)
    return m


def main():
    proj = ROOT / "research/strategies/STR-20260529-新策略/runs"
    for pat in ("*2021_2026*.json", "*2026_ytd*.json"):
        for p in sorted(proj.glob(pat), key=lambda x: x.stat().st_mtime, reverse=True)[:1]:
            patch(p)


if __name__ == "__main__":
    main()
