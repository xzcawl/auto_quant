"""JoinQuant parallel backtest slot detection and wait."""

from __future__ import annotations

import re
import time
from typing import Any

from auto_quant.joinquant.editor_inject import click_run_backtest

# 账户并行编译/回测上限提示（见聚宽编辑页弹窗）
_PARALLEL_LIMIT_MARKERS = (
    "并行编译或回测数量最多",
    "并行编译或回测",
    "最多2个",
)

_ACTIVE_STATUS = (
    "运行中",
    "编译中",
    "回测中",
    "正在编译",
    "正在回测",
    "排队中",
    "排队",
)


def detect_parallel_limit_error(page) -> bool:
    """True if the「最多 N 个并行回测」modal is visible."""
    try:
        modal = page.locator(".ant-modal, .modal, [role='dialog'], .layui-layer").filter(
            has_text=re.compile("并行|编译失败|最多")
        )
        if modal.count() > 0:
            t = modal.first.inner_text(timeout=2000)
            return any(m in t for m in _PARALLEL_LIMIT_MARKERS)
    except Exception:
        pass
    try:
        text = page.inner_text("body")
        return any(m in text for m in _PARALLEL_LIMIT_MARKERS) and "最多" in text
    except Exception:
        return False


def dismiss_parallel_limit_modal(page) -> bool:
    """Close the parallel-limit error modal (X only, not「提高数量」)."""
    try:
        modal = page.locator(".ant-modal, .modal, [role='dialog']").filter(
            has_text=re.compile("并行|编译失败")
        )
        if modal.count() == 0:
            return False
        close = modal.first.locator(
            ".ant-modal-close, .close, button.close, .layui-layer-close"
        )
        if close.count() > 0:
            close.first.click(timeout=3000)
            page.wait_for_timeout(500)
            print("[jq] 已关闭「并行回测已满」提示窗")
            return True
    except Exception:
        pass
    for sel in (".ant-modal-close", ".layui-layer-close"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(400)
                return True
        except Exception:
            continue
    return False


def count_active_backtests(page, jq: dict[str, Any]) -> int:
    """
    Count in-flight backtests on JoinQuant (list / backtest pages).
    Returns -1 if the page could not be parsed.
    """
    urls: list[str] = []
    for key in ("backtest_queue_url", "strategy_list_url", "backtest_list_url"):
        u = jq.get(key)
        if u and u not in urls:
            urls.append(u)
    if not urls:
        urls.append("https://www.joinquant.com/algorithm/index/list")

    best = -1
    for url in urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(2000)
            n: int = page.evaluate(
                """(statuses) => {
                    const active = statuses;
                    let count = 0;
                    const seen = new Set();
                    const rows = document.querySelectorAll('tr, li, .list-item, .backtest-item');
                    rows.forEach(el => {
                        const t = (el.innerText || '').trim();
                        if (!t || t.length > 500) return;
                        if (!active.some(s => t.includes(s))) return;
                        const key = t.slice(0, 80);
                        if (seen.has(key)) return;
                        seen.add(key);
                        count++;
                    });
                    if (count > 0) return count;
                    const body = document.body.innerText || '';
                    let total = 0;
                    for (const s of active) {
                        const re = new RegExp(s, 'g');
                        const m = body.match(re);
                        if (m) total += m.length;
                    }
                    return Math.min(total, 10);
                }""",
                list(_ACTIVE_STATUS),
            )
            if isinstance(n, int) and n >= 0:
                best = max(best, n)
        except Exception:
            continue
    return best if best >= 0 else 0


def wait_for_backtest_slot(
    page,
    jq: dict[str, Any],
    *,
    max_parallel: int | None = None,
) -> None:
    """Block until account has fewer than max_parallel active backtests."""
    max_parallel = max_parallel or int(jq.get("max_parallel_backtests", 2))
    interval = int(jq.get("parallel_wait_interval_sec", 45))
    timeout = int(jq.get("parallel_wait_timeout_sec", 7200))
    deadline = time.time() + timeout
    list_url = jq.get("strategy_list_url", "")

    while time.time() < deadline:
        n = count_active_backtests(page, jq)
        known = int(jq.get("known_running_backtests", 0) or 0)
        n = max(n, known)
        if n < 0:
            n = max_parallel  # conservative: assume full

        if n < max_parallel:
            print(f"[jq] 并行回测槽位可用: 进行中约 {n} 个，上限 {max_parallel}")
            if list_url and "list" in list_url:
                # 回到列表/编辑流程由调用方 goto；此处仅记录
                pass
            return

        extra = f"（含已记录进行中 {known} 个）" if known else ""
        print(
            f"[jq] 并行回测已满（约 {n}/{max_parallel}）{extra}，"
            f"{interval}s 后重新检测…"
        )
        time.sleep(interval)

    raise TimeoutError(
        f"等待聚宽回测槽位超时（>{timeout}s），仍有约 {max_parallel} 个并行任务"
    )


def page_has_active_backtest(page) -> bool:
    """Editor / report page still shows running indicators."""
    try:
        text = page.inner_text("body")
    except Exception:
        return False
    if detect_parallel_limit_error(page):
        return True
    return any(s in text for s in _ACTIVE_STATUS) and "历史" not in text[:200]


def ensure_strategy_editor_page(page, editor_url: str, *, timeout_ms: int = 60000) -> None:
    """Return to code editor after slot check navigated to list page."""
    from auto_quant.joinquant.editor_inject import wait_for_editor_ready

    if not editor_url or "edit" not in editor_url:
        return
    if "edit" in page.url and "algorithm/index/edit" in page.url:
        wait_for_editor_ready(page, timeout_ms=15000)
        return
    print(f"[jq] 回到策略编辑页: {editor_url[:80]}…")
    page.goto(editor_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1500)
    wait_for_editor_ready(page, timeout_ms=45000)


def submit_backtest_with_slot_control(
    page,
    start_date: str,
    end_date: str,
    jq: dict[str, Any],
    *,
    confirm_dialog_fn,
    editor_url: str = "",
) -> bool:
    """
    Wait for a free slot, click「运行回测」, handle parallel-limit modal with retry.
    Returns True if backtest was successfully submitted.
    """
    from auto_quant.joinquant.ui_helpers import set_backtest_dates

    max_parallel = int(jq.get("max_parallel_backtests", 2))
    interval = int(jq.get("parallel_wait_interval_sec", 45))
    max_attempts = int(jq.get("parallel_submit_max_attempts", 120))
    edit_url = editor_url or page.url

    for attempt in range(1, max_attempts + 1):
        wait_for_backtest_slot(page, jq, max_parallel=max_parallel)
        ensure_strategy_editor_page(page, edit_url)

        set_backtest_dates(page, start_date, end_date)
        print(f"[jq] 点击运行回测 (尝试 {attempt}) …")
        clicked = click_run_backtest(page, timeout_ms=25000)
        if not clicked:
            from auto_quant.joinquant.ui_helpers import click_first

            click_first(page, ['button:has-text("编译运行")', 'a:has-text("编译运行")'])
            page.wait_for_timeout(3000)
            clicked = click_run_backtest(page, timeout_ms=15000)

        page.wait_for_timeout(1500)

        if detect_parallel_limit_error(page):
            dismiss_parallel_limit_modal(page)
            print(f"[jq] 并行回测已满，{interval}s 后重试提交…")
            time.sleep(interval)
            continue

        if clicked:
            confirm_dialog_fn(page, start_date, end_date)

        if detect_parallel_limit_error(page):
            dismiss_parallel_limit_modal(page)
            time.sleep(interval)
            continue

        if clicked:
            print("[jq] 回测已提交")
            return True

        print("[jq] 未点到运行回测，稍后重试…")
        time.sleep(interval)

    raise RuntimeError("多次尝试后仍无法提交回测（并行槽位或按钮）")
