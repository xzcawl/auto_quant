"""Playwright automation for JoinQuant backtest submission."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from auto_quant.config import load_settings, project_root
from auto_quant.joinquant.queue import TaskQueue
from auto_quant.joinquant.scraper import (
    append_markdown_summary,
    detect_no_credits,
    parse_metrics_from_text,
)

load_dotenv(project_root() / ".env")


def _auth_path() -> Path:
    return project_root() / load_settings()["joinquant"]["auth_state"]


def save_login_state() -> None:
    """Open browser for manual login; persist storage state."""
    from playwright.sync_api import sync_playwright

    settings = load_settings()["joinquant"]
    auth_path = _auth_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.joinquant.com/user/login/index")
        print("请在浏览器中完成登录与验证码，完成后回到终端按 Enter...")
        input()
        context.storage_state(path=str(auth_path))
        browser.close()
    print(f"登录态已保存: {auth_path}")


def _login_if_needed(page) -> None:
    user = os.getenv("JQ_USERNAME")
    pwd = os.getenv("JQ_PASSWORD")
    if not user or not pwd:
        return
    try:
        page.fill('input[name="username"], input#username', user, timeout=3000)
        page.fill('input[name="password"], input#id_password', pwd, timeout=3000)
        page.click('button[type="submit"], .btn-login', timeout=3000)
        page.wait_for_timeout(2000)
    except Exception:
        pass


def submit_backtest(page, code: str, start: str, end: str) -> str:
    """Paste strategy and trigger backtest. Returns page text snapshot."""
    from auto_quant.joinquant.editor_inject import (
        click_run_backtest,
        set_editor_value,
        verify_editor_contains,
    )
    from auto_quant.joinquant.ui_helpers import (
        confirm_backtest_run_dialog,
        dismiss_editor_dialogs,
        set_backtest_dates,
    )

    settings = load_settings()["joinquant"]
    url = settings.get("backtest_url", "https://www.joinquant.com/algorithm/index/edit")

    print(f"[jq] 打开页面: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    dismiss_editor_dialogs(page, max_rounds=3)

    print("[jq] 等待编辑器加载…")
    try:
        page.wait_for_selector(".ace_editor, .monaco-editor", timeout=45000)
    except Exception as e:
        print(f"[jq] 警告: 未检测到 Ace/Monaco 编辑器: {e}")

    text = page.inner_text("body")
    if detect_no_credits(text):
        return text

    print(f"[jq] 注入策略代码（{len(code)} 字符）…")
    injected = set_editor_value(page, code)
    if not injected:
        print("[jq] 错误: 无法写入编辑器（Ace/Monaco 均未成功）。请把 joinquant.backtest_url 设为「已打开该策略」的完整编辑页 URL。")
    else:
        snippet = ""
        for line in code.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                snippet = s[:120]
                break
        if snippet and not verify_editor_contains(page, snippet):
            print("[jq] 警告: 注入后校验未在编辑器中发现策略片段，可能未写入成功。")
        else:
            print("[jq] 编辑器注入完成。")

    page.wait_for_timeout(500)

    set_backtest_dates(page, start, end)

    print("[jq] 尝试点击「运行回测」…")
    clicked = click_run_backtest(page, timeout_ms=20000)
    if not clicked:
        print("[jq] 错误: 未找到可点击的「运行回测」按钮。请在浏览器中手动点一次，或反馈页面截图以便更新选择器。")
    else:
        print("[jq] 已触发运行。")

    page.wait_for_timeout(1500)
    dismiss_editor_dialogs(page, max_rounds=2)
    if clicked:
        confirm_backtest_run_dialog(page, start, end)
    return page.inner_text("body")


def poll_until_done(page, timeout_sec: int, interval_sec: int) -> str:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        text = page.inner_text("body")
        last = text
        if detect_no_credits(text):
            return text
        if any(k in text for k in ("回测完成", "年化收益", "策略收益", "夏普")):
            if "运行中" not in text and "回测中" not in text:
                return text
        time.sleep(interval_sec)
    return last


def run_single_task(
    task_id: int,
    *,
    jq_start: str | None = None,
    jq_end: str | None = None,
) -> None:
    """Run exactly one queued task by id."""
    settings = load_settings()
    jq = dict(settings["joinquant"])
    if jq_start:
        jq["jq_backtest_start"] = jq_start
    if jq_end:
        jq["jq_backtest_end"] = jq_end
    q = TaskQueue()

    from playwright.sync_api import sync_playwright

    if not _auth_path().exists():
        print("未找到登录态，请先运行: python scripts/run_jq_batch.py --login")
        return

    task = q.get_task(task_id)
    if not task:
        print(f"未找到 task_id={task_id}")
        return
    _process_task_with_browser(task, jq, q, sync_playwright)


def _run_task_on_page(page, task, jq: dict, q: TaskQueue) -> dict:
    """Run one task on an existing page (no browser close)."""
    import json
    from pathlib import Path

    from auto_quant.joinquant.strategy_flow import run_named_strategy_backtest

    q.update_status(task.id, "running")
    code = Path(task.jq_code_path).read_text(encoding="utf-8")
    display_name = task.strategy_name
    print(f"提交聚宽回测: {display_name} (task {task.id})")

    metrics = run_named_strategy_backtest(
        page,
        strategy_display_name=display_name,
        code=code,
        start_date=jq.get("jq_backtest_start", "2024-01-01"),
        end_date=jq.get("jq_backtest_end", "2024-12-31"),
        jq=jq,
    )

    if metrics.get("error") == "no_credits" or detect_no_credits(str(metrics)):
        q.update_status(task.id, "skipped_no_credits", json.dumps(metrics, ensure_ascii=False))
        append_markdown_summary(display_name, metrics, "积分不足跳过")
        return metrics

    if not metrics.get("annual_return") and not metrics.get("sharpe"):
        metrics.setdefault("raw_note", "指标未解析，请用 report_url 在聚宽页查看")

    q.save_result(task.id, display_name, metrics)
    q.update_status(task.id, "done", json.dumps(metrics, ensure_ascii=False))
    append_markdown_summary(display_name, metrics, "完成")
    print(f"完成: report_url={metrics.get('report_url')} metrics={metrics}")
    return metrics


def _process_task_with_browser(task, jq, q: TaskQueue, sync_playwright) -> None:
    auth_path = _auth_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth_path))
        page = context.new_page()
        try:
            _run_task_on_page(page, task, jq, q)
        except Exception as e:
            import json

            q.update_status(task.id, "failed", json.dumps({"error": str(e)}))
            print(f"失败: {e}")
        finally:
            browser.close()


def run_tasks_in_one_browser(
    tasks: list,
    jq: dict,
    q: TaskQueue,
    *,
    on_each_done=None,
) -> None:
    """Run multiple tasks in a single browser session (ablation)."""
    from playwright.sync_api import sync_playwright

    auth_path = _auth_path()
    if not auth_path.exists():
        print("未找到登录态，请先运行: python scripts/run_jq_batch.py --login")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth_path))
        page = context.new_page()
        try:
            for task in tasks:
                try:
                    metrics = _run_task_on_page(page, task, jq, q)
                    if on_each_done:
                        on_each_done(task, metrics)
                except Exception as e:
                    import json

                    q.update_status(task.id, "failed", json.dumps({"error": str(e)}))
                    print(f"失败 task {task.id}: {e}")
                page.wait_for_timeout(2000)
        finally:
            browser.close()


def run_batch(*, dry_run: bool = False) -> None:
    settings = load_settings()
    jq = settings["joinquant"]
    q = TaskQueue()

    if dry_run:
        _run_dry_batch(q)
        return

    from playwright.sync_api import sync_playwright

    auth_path = _auth_path()
    if not auth_path.exists():
        print("未找到登录态，请先运行: python scripts/run_jq_batch.py --login")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth_path))
        page = context.new_page()

        while True:
            task = q.next_pending()
            if not task:
                print("队列已空。")
                break

            q.update_status(task.id, "running")
            code = Path(task.jq_code_path).read_text(encoding="utf-8")
            print(f"提交聚宽回测: {task.strategy_name} (task {task.id})")

            try:
                text = submit_backtest(
                    page,
                    code,
                    jq.get("jq_backtest_start", "2024-01-01"),
                    jq.get("jq_backtest_end", "2024-12-31"),
                )
                if detect_no_credits(text):
                    q.update_status(task.id, "skipped_no_credits", json.dumps({"error": "no_credits"}))
                    append_markdown_summary(task.strategy_name, {}, "积分不足跳过")
                    continue

                text = poll_until_done(
                    page,
                    jq.get("poll_timeout_sec", 3600),
                    jq.get("poll_interval_sec", 30),
                )
                if detect_no_credits(text):
                    q.update_status(task.id, "skipped_no_credits")
                    append_markdown_summary(task.strategy_name, {}, "积分不足跳过")
                    continue

                metrics = parse_metrics_from_text(text)
                if not metrics:
                    metrics = {"raw_note": "未能解析指标，请人工查看聚宽页面"}

                q.save_result(task.id, task.strategy_name, metrics)
                q.update_status(task.id, "done", json.dumps(metrics, ensure_ascii=False))
                append_markdown_summary(task.strategy_name, metrics, "完成")
                print(f"完成: {metrics}")

            except Exception as e:
                q.update_status(task.id, "failed", json.dumps({"error": str(e)}))
                print(f"失败 task {task.id}: {e}")

        browser.close()


def _run_dry_batch(q: TaskQueue) -> None:
    """Simulate batch without browser for CI/local test."""
    while True:
        task = q.next_pending()
        if not task:
            break
        q.update_status(task.id, "running")
        metrics = {
            "annual_return": 0.12,
            "sharpe": 1.1,
            "max_drawdown": -0.15,
            "win_rate": 0.52,
            "dry_run": True,
        }
        q.save_result(task.id, task.strategy_name, metrics)
        q.update_status(task.id, "done", json.dumps(metrics))
        append_markdown_summary(task.strategy_name, metrics, "dry_run")
        print(f"[dry-run] done {task.strategy_name}")
