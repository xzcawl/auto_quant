#!/usr/bin/env python
"""Re-scrape metrics for regime variant run JSON files."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright
from auto_quant.joinquant.playwright_runner import _auth_path
from scrape_period_runs import parse_body


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
        page.wait_for_timeout(8000)
        try:
            page.get_by_text("收益概述", exact=True).first.click(timeout=3000)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        m = parse_body(page.inner_text("body"))
        browser.close()
    metrics = data.setdefault("metrics", {})
    for k, v in m.items():
        metrics[k] = v
    metrics["raw"] = {**(metrics.get("raw") or {}), **m, "data_source": "playwright_line_parse"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.name, m)
    return m


def main():
    runs = ROOT / "research/strategies/STR-20260529-新策略/runs"
    for p in sorted(runs.glob("*regime*.json")) + sorted(runs.glob("*idea-*.json")):
        patch(p)


if __name__ == "__main__":
    main()
