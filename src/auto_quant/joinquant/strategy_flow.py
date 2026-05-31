"""JoinQuant web UI: named strategy + backtest + report URL capture."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from auto_quant.joinquant.editor_inject import (
    set_editor_value,
    verify_editor_contains,
    wait_for_editor_ready,
)
from auto_quant.joinquant.scraper import (
    backtest_metrics_plausible,
    detect_no_credits,
    parse_metrics_from_text,
)
from auto_quant.joinquant.concurrency import (
    page_has_active_backtest,
    submit_backtest_with_slot_control,
)
from auto_quant.joinquant.ui_helpers import (
    click_first as _click_first,
    confirm_backtest_run_dialog,
    dismiss_editor_dialogs,
    select_stock_strategy_on_new,
    set_backtest_dates,
)


def set_strategy_name(page, name: str) -> bool:
    """Set display name on strategy edit page (top title input)."""
    for sel in (
        "input.strategy-title",
        ".strategy-title input",
        ".algorithm-title input",
        "input[placeholder*='策略名']",
        "input[placeholder*='名称']",
        ".name-input input",
        "input.title-input",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.click(timeout=3000)
            loc.fill(name, timeout=5000)
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            return True
        except Exception:
            continue

    # 可编辑标题
    try:
        ok = page.evaluate(
            """(name) => {
                const nodes = document.querySelectorAll('h1,h2,.title,[contenteditable=true]');
                for (const n of nodes) {
                    const t = (n.innerText || '').trim();
                    if (t.includes('策略') || t.length < 40) {
                        n.focus && n.focus();
                        if (n.tagName === 'INPUT') { n.value = name; return true; }
                    }
                }
                return false;
            }""",
            name,
        )
        if ok:
            return True
    except Exception:
        pass

    # 双击页面顶部默认标题区域后输入
    try:
        title_loc = page.get_by_text("这是一个简单的策略", exact=False).first
        if title_loc.count() > 0:
            title_loc.dblclick(timeout=3000)
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(name)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    print(f"[jq] 警告: 未能自动设置策略名，请手动改为: {name}")
    return False


def save_strategy(page) -> None:
    _click_first(
        page,
        [
            'button:has-text("保存")',
            'a:has-text("保存")',
            ".save-btn",
        ],
    )
    try:
        page.keyboard.press("Control+S")
    except Exception:
        pass
    page.wait_for_timeout(1500)


def _extract_backtest_id(url: str) -> str | None:
    if "backtest" not in url.lower():
        return None
    q = parse_qs(urlparse(url).query)
    for key in ("backtestId", "backtest_id", "id"):
        if key in q and q[key]:
            return str(q[key][0])
    m = re.search(r"backtest[/=]([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def capture_latest_backtest_url(page, *, wait_sec: int = 8) -> str:
    """从策略编辑页「回测列表」取最近一次回测详情 URL。"""
    page.wait_for_timeout(wait_sec * 1000)
    try:
        tab = page.get_by_text("回测列表", exact=True)
        if tab.count() > 0:
            tab.first.click(timeout=5000)
            page.wait_for_timeout(1500)
    except Exception:
        pass
    try:
        href = page.locator('a[href*="backtest/detail"]').first.get_attribute("href") or ""
        if href:
            return _abs_url(href)
    except Exception:
        pass
    try:
        html = page.content()
        m = re.search(r"backtest/detail\?backtestId=([a-f0-9]+)", html, re.I)
        if m:
            return f"https://www.joinquant.com/algorithm/backtest/detail?backtestId={m.group(1)}"
    except Exception:
        pass
    return page.url


def wait_for_backtest_report(page, timeout_sec: int, interval_sec: int) -> dict[str, Any]:
    """Wait until report page or metrics visible; return url + parsed metrics."""
    deadline = time.time() + timeout_sec
    last_url = page.url
    last_text = ""

    while time.time() < deadline:
        last_url = page.url
        try:
            last_text = page.inner_text("body")
        except Exception:
            break

        if detect_no_credits(last_text):
            return {"report_url": last_url, "metrics": {}, "no_credits": True}

        # URL 进入回测详情：先记录 URL；指标在 ablation-refresh 阶段再抓
        if "backtest" in last_url.lower() and "edit" not in last_url.lower():
            metrics = parse_metrics_from_text(last_text)
            metrics["report_url"] = last_url
            metrics["backtest_id"] = _extract_backtest_id(last_url)
            if backtest_metrics_plausible(metrics, last_text):
                return {"report_url": last_url, "metrics": metrics}
            metrics.setdefault("raw_note", "回测已提交，指标稍后 refresh 抓取")
            return {"report_url": last_url, "metrics": metrics}

        if page_has_active_backtest(page):
            time.sleep(interval_sec)
            continue

        if "已取消" in last_text:
            time.sleep(interval_sec)
            continue

        if any(k in last_text for k in ("年化收益", "策略收益", "夏普比率", "最大回撤")):
            if "运行中" not in last_text and "回测中" not in last_text and "编译" not in last_text:
                metrics = parse_metrics_from_text(last_text)
                if backtest_metrics_plausible(metrics, last_text):
                    metrics["report_url"] = last_url
                    metrics["backtest_id"] = _extract_backtest_id(last_url)
                    return {"report_url": last_url, "metrics": metrics}

        time.sleep(interval_sec)

    metrics = parse_metrics_from_text(last_text) if last_text else {}
    metrics["report_url"] = last_url
    metrics["backtest_id"] = _extract_backtest_id(last_url)
    metrics["raw_note"] = "回测超时或仍在运行，请打开 report_url 人工查看"
    return {"report_url": last_url, "metrics": metrics}


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return "https://www.joinquant.com" + (href if href.startswith("/") else "/" + href)


def open_or_create_strategy_editor(
    page,
    strategy_display_name: str,
    *,
    list_url: str,
    new_url: str,
) -> str:
    """
    Open existing strategy by name on list page, or create via「新建策略」.
    Returns editor page URL.
    """
    print(f"[jq] 策略列表: {list_url}")
    page.goto(list_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)

    # 已有同名策略 → 取 Code 链接直接进编辑页（比 click 更稳）
    try:
        row = page.locator("tr").filter(has_text=strategy_display_name).first
        if row.count() > 0:
            code_link = row.locator('a:has-text("Code"), td a').first
            href = ""
            try:
                href = code_link.get_attribute("href") or ""
            except Exception:
                pass
            edit_url = _abs_url(href)
            print(f"[jq] 打开已有策略: {strategy_display_name}")
            if edit_url and "edit" in edit_url:
                page.goto(edit_url, wait_until="domcontentloaded", timeout=90000)
            else:
                with page.expect_navigation(timeout=60000, wait_until="domcontentloaded"):
                    row.get_by_text("Code", exact=True).first.click(timeout=15000)
            page.wait_for_timeout(1500)
            dismiss_editor_dialogs(page, max_rounds=3)
            if wait_for_editor_ready(page, timeout_ms=45000):
                set_strategy_name(page, strategy_display_name)
                return page.url
            print("[jq] 警告: 进入编辑页后仍未见编辑器，当前 URL:", page.url)
            return page.url
    except Exception as e:
        print(f"[jq] 列表打开策略失败: {e}")

    # 新建
    print(f"[jq] 新建策略: {strategy_display_name}")
    if not _click_first(
        page,
        [
            'a:has-text("新建策略")',
            'button:has-text("新建策略")',
            'a:has-text("新建")',
            'button:has-text("新建")',
            ".create-strategy",
        ],
    ):
        page.goto(new_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    select_stock_strategy_on_new(page)
    dismiss_editor_dialogs(page, max_rounds=3)
    wait_for_editor_ready(page, timeout_ms=45000)
    set_strategy_name(page, strategy_display_name)
    return page.url


def run_named_strategy_backtest(
    page,
    *,
    strategy_display_name: str,
    code: str,
    start_date: str,
    end_date: str,
    jq: dict[str, Any],
) -> dict[str, Any]:
    """
    Full flow: list → open/create by name → paste code → save → run backtest → capture report URL.
    """
    list_url = jq.get("strategy_list_url", "https://www.joinquant.com/algorithm/index/list")
    new_url = jq.get("strategy_new_url", "https://www.joinquant.com/algorithm/index/new")

    edit_url = open_or_create_strategy_editor(
        page,
        strategy_display_name,
        list_url=list_url,
        new_url=new_url,
    )

    text = page.inner_text("body")
    if detect_no_credits(text):
        return {
            "strategy_name": strategy_display_name,
            "edit_url": edit_url,
            "report_url": page.url,
            "metrics": {"error": "no_credits"},
        }

    dismiss_editor_dialogs(page, max_rounds=3)

    if not wait_for_editor_ready(page, timeout_ms=30000):
        print(f"[jq] 当前 URL: {page.url}")
        print("[jq] 错误: 编辑页未加载，请确认已从列表进入 Code 编辑界面")

    print(f"[jq] 注入代码 ({len(code)} 字符) …")
    injected = set_editor_value(page, code)
    if not injected:
        print("[jq] 错误: 代码注入失败 — 请在浏览器中确认左侧为代码编辑区（非回测结果页）")
        raise RuntimeError("代码注入失败，已中止本变体以免空跑回测")

    if not verify_editor_contains(page, "def initialize"):
        print("[jq] 警告: 注入后未校验到 initialize，请检查编辑器内容")

    set_strategy_name(page, strategy_display_name)
    save_strategy(page)

    dismiss_editor_dialogs(page, max_rounds=2)

    submit_backtest_with_slot_control(
        page,
        start_date,
        end_date,
        jq,
        confirm_dialog_fn=confirm_backtest_run_dialog,
        editor_url=edit_url,
    )

    page.wait_for_timeout(2000)
    fast = bool(jq.get("ablation_fast_submit"))
    if fast:
        report_url = capture_latest_backtest_url(page)
        result = {
            "report_url": report_url,
            "metrics": {
                "raw_note": "ablation_fast_submit：稍后 refresh 抓取指标",
                "report_url": report_url,
                "backtest_id": _extract_backtest_id(report_url),
            },
        }
    else:
        result = wait_for_backtest_report(
            page,
            jq.get("poll_timeout_sec", 3600),
            jq.get("poll_interval_sec", 15),
        )

    metrics = result.get("metrics") or {}
    metrics["strategy_display_name"] = strategy_display_name
    metrics["edit_url"] = edit_url
    metrics["report_url"] = result.get("report_url") or page.url
    metrics["backtest_id"] = result.get("backtest_id") or _extract_backtest_id(metrics["report_url"])
    metrics["backtest_start"] = start_date
    metrics["backtest_end"] = end_date

    print(f"[jq] 报告 URL: {metrics.get('report_url')}")
    return metrics
