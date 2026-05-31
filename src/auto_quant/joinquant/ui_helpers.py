"""JoinQuant web UI helpers: dialogs, strategy type, backtest date range."""

from __future__ import annotations

import re
from typing import Any

# 编辑页常见引导/公告弹窗按钮文案（按优先级）
_DISMISS_BUTTON_TEXTS = (
    "我知道了",
    "知道了",
    "确定",
    "好的",
    "关闭",
    "跳过",
    "暂不",
    "以后再说",
    "不再提示",
    "取消",
    "稍后",
)

def click_first(page, selectors: list[str], timeout_ms: int = 8000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


_DISMISS_SELECTORS = (
    ".ant-modal-close",
    ".modal-close",
    ".close-btn",
    "button.close",
    '[aria-label="Close"]',
    ".layui-layer-close",
)


def dismiss_editor_dialogs(page, *, max_rounds: int = 3) -> int:
    """
    Close onboarding / notice modals on the strategy editor (often 2 in a row).
    Returns number of dialogs dismissed.
    """
    closed = 0
    for _ in range(max_rounds):
        hit = False
        for text in _DISMISS_BUTTON_TEXTS:
            try:
                for loc in (
                    page.get_by_role("button", name=text),
                    page.locator(f'button:has-text("{text}")'),
                    page.locator(f'a:has-text("{text}")'),
                    page.locator(f'span:has-text("{text}")'),
                ):
                    if loc.count() == 0:
                        continue
                    btn = loc.first
                    if not btn.is_visible():
                        continue
                    btn.click(timeout=2500)
                    page.wait_for_timeout(600)
                    closed += 1
                    hit = True
                    print(f"[jq] 已关闭提示窗: 「{text}」")
                    break
                if hit:
                    break
            except Exception:
                continue
        if not hit:
            for sel in _DISMISS_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    loc.click(timeout=2000)
                    page.wait_for_timeout(500)
                    closed += 1
                    hit = True
                    print(f"[jq] 已关闭提示窗 (selector: {sel})")
                    break
                except Exception:
                    continue
        if not hit:
            break
    return closed


def select_stock_strategy_on_new(page) -> bool:
    """On「新建策略」模板页，选择「股票策略」再进入代码编辑器。"""
    candidates = [
        page.get_by_text("股票策略", exact=True),
        page.locator('div:has-text("股票策略")').first,
        page.locator('[class*="strategy-type"]:has-text("股票")').first,
        page.locator('li:has-text("股票策略")').first,
        page.locator('.card:has-text("股票策略")').first,
    ]
    for loc in candidates:
        try:
            if loc.count() == 0:
                continue
            if not loc.is_visible():
                continue
            loc.click(timeout=8000)
            page.wait_for_timeout(2000)
            print("[jq] 已选择策略类型: 股票策略")
            return True
        except Exception:
            continue
    return False


def _normalize_date(s: str) -> str:
    s = str(s).strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return s


def _fill_one_input(inp, value: str) -> None:
    try:
        inp.click(timeout=3000)
        inp.fill(value, timeout=5000)
        inp.press("Enter")
    except Exception:
        try:
            inp.click(force=True)
            inp.press("Control+A")
            inp.press("Backspace")
            inp.type(value, delay=20)
            inp.press("Enter")
        except Exception:
            pass


def set_backtest_dates(page, start: str, end: str) -> bool:
    """
    Set backtest start/end on editor toolbar or in the「运行回测」dialog.
    Dates should be YYYY-MM-DD (e.g. from meta.yaml backtest.joinquant).
    """
    start = _normalize_date(start)
    end = _normalize_date(end)
    print(f"[jq] 设置回测区间: {start} ~ {end}")
    ok = False

    label_pairs = [
        ("开始时间", start),
        ("结束时间", end),
        ("开始日期", start),
        ("结束日期", end),
        ("开始", start),
        ("结束", end),
    ]
    for label, value in label_pairs:
        try:
            page.get_by_label(label).fill(value, timeout=2000)
            ok = True
        except Exception:
            pass
        try:
            loc = page.get_by_placeholder(label)
            if loc.count() > 0:
                loc.first.fill(value, timeout=2000)
                ok = True
        except Exception:
            pass

    # Ant Design 日期选择器（聚宽编辑页常见）
    try:
        pickers = page.locator(".ant-picker-input input")
        n = pickers.count()
        if n >= 2:
            _fill_one_input(pickers.nth(0), start)
            page.wait_for_timeout(300)
            _fill_one_input(pickers.nth(1), end)
            ok = True
    except Exception:
        pass

    try:
        inputs = page.locator('input[type="date"]')
        if inputs.count() >= 2:
            inputs.nth(0).fill(start)
            inputs.nth(1).fill(end)
            ok = True
    except Exception:
        pass

    # JS：按 placeholder/name/邻近文案定位
    try:
        result: dict[str, Any] = page.evaluate(
            """([start, end]) => {
                function fire(inp, v) {
                    inp.focus();
                    const desc = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    );
                    if (desc && desc.set) desc.set.call(inp, v);
                    else inp.value = v;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const all = Array.from(document.querySelectorAll('input'));
                const score = (inp) => {
                    const ph = (inp.placeholder || '').toLowerCase();
                    const nm = (inp.name || '').toLowerCase();
                    const id = (inp.id || '').toLowerCase();
                    const near = (inp.closest('label,div,span')?.innerText || '').toLowerCase();
                    let s = 0;
                    if (/开始|start|from/.test(ph + nm + id + near)) s += 2;
                    if (/结束|end|to/.test(ph + nm + id + near)) s += 2;
                    if (/日期|date|时间/.test(ph + nm + id + near)) s += 1;
                    if (/^\\d{4}-\\d{2}-\\d{2}$/.test(inp.value || '')) s += 1;
                    return s;
                };
                const ranked = all
                    .filter(i => i.offsetParent !== null && !i.disabled)
                    .map(i => ({ i, s: score(i) }))
                    .filter(x => x.s > 0)
                    .sort((a, b) => b.s - a.s);
                let startInp = ranked.find(x => /开始|start|from/.test(
                    (x.i.placeholder+x.i.name+x.i.id+(x.i.closest('label,div')?.innerText||'')).toLowerCase()
                ))?.i;
                let endInp = ranked.find(x => /结束|end|to/.test(
                    (x.i.placeholder+x.i.name+x.i.id+(x.i.closest('label,div')?.innerText||'')).toLowerCase()
                ))?.i;
                if (!startInp && ranked.length >= 2) {
                    startInp = ranked[ranked.length - 1].i;
                    endInp = ranked[ranked.length - 2].i;
                }
                if (startInp && endInp) {
                    fire(startInp, start);
                    fire(endInp, end);
                    return { ok: true, startVal: startInp.value, endVal: endInp.value };
                }
                const plain = all.filter(i =>
                    i.offsetParent && /^\\d{4}-\\d{2}-\\d{2}$/.test(i.value || i.placeholder || '')
                );
                if (plain.length >= 2) {
                    fire(plain[0], start);
                    fire(plain[1], end);
                    return { ok: true, startVal: plain[0].value, endVal: plain[1].value };
                }
                return { ok: false };
            }""",
            [start, end],
        )
        if result.get("ok"):
            ok = True
            print(
                f"[jq] JS 写入日期: start={result.get('startVal')} end={result.get('endVal')}"
            )
    except Exception as e:
        print(f"[jq] JS 设置日期失败: {e}")

    if not ok:
        print("[jq] 警告: 未能确认回测日期已写入，将在点击「运行回测」后于弹窗中重试")
    return ok


def confirm_backtest_run_dialog(page, start: str, end: str) -> bool:
    """After clicking「运行回测」, fill dates in modal and confirm."""
    try:
        page.wait_for_selector(
            ".ant-modal, .modal, [role='dialog'], .layui-layer",
            timeout=8000,
        )
    except Exception:
        return False

    dismiss_editor_dialogs(page, max_rounds=2)
    set_backtest_dates(page, start, end)

    confirm_texts = ("运行回测", "开始回测", "确定", "提交", "运行")
    for text in confirm_texts:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=5000)
                page.wait_for_timeout(1500)
                print(f"[jq] 回测弹窗已确认: 「{text}」")
                return True
        except Exception:
            continue
        try:
            loc = page.locator(f'button:has-text("{text}")').first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=5000)
                page.wait_for_timeout(1500)
                print(f"[jq] 回测弹窗已确认: 「{text}」")
                return True
        except Exception:
            continue
    return False
