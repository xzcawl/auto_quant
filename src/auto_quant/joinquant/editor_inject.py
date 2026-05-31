"""Inject strategy code into JoinQuant web editor (Ace / Monaco / iframe)."""

from __future__ import annotations

import re
from typing import Any

ACE_SET_JS = """
(code) => {
  try {
    if (typeof ace === 'undefined') return { ok: false, why: 'no_ace' };
    const nodes = document.querySelectorAll('.ace_editor');
    if (!nodes.length) return { ok: false, why: 'no_ace_editor_node' };
    for (const n of nodes) {
      try {
        const ed = ace.edit(n);
        if (ed && typeof ed.setValue === 'function') {
          ed.setValue(code, -1);
          ed.clearSelection();
          ed.resize && ed.resize();
          const v = ed.getValue();
          return { ok: v.length > 100, len: v.length, why: 'ace.setValue' };
        }
      } catch (e) {}
    }
    return { ok: false, why: 'ace_edit_failed' };
  } catch (e) {
    return { ok: false, why: String(e) };
  }
}
"""


def wait_for_editor_ready(page, timeout_ms: int = 60000) -> bool:
    """Wait until strategy code editor is present (main page or iframe)."""
    deadline = timeout_ms / 1000
    import time

    start = time.time()
    while time.time() - start < deadline:
        # 已离开列表页
        url = page.url
        if "list" not in url.split("/")[-1] and ("edit" in url or "algorithm" in url):
            pass

        for frame in [page] + list(page.frames):
            try:
                if frame.locator(".ace_editor").count() > 0:
                    frame.locator(".ace_editor").first.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        try:
            page.wait_for_selector(".ace_editor, .monaco-editor, .ace_text-input", timeout=3000)
            return True
        except Exception:
            page.wait_for_timeout(800)
    return False


def _inject_in_frame(frame, code: str) -> dict[str, Any]:
    try:
        return frame.evaluate(ACE_SET_JS, code) or {"ok": False}
    except Exception as e:
        return {"ok": False, "why": str(e)}


def set_editor_value(page, code: str) -> bool:
    """Set full strategy source; tries main document + all iframes."""
    if not wait_for_editor_ready(page, timeout_ms=60000):
        print("[jq] 警告: 超时未检测到 .ace_editor")

    # 1) 各 frame 注入（聚宽编辑器常在 iframe）
    for i, frame in enumerate([page] + list(page.frames)):
        result = _inject_in_frame(frame, code)
        if result.get("ok"):
            print(f"[jq] 代码注入成功 (frame={i}, {result.get('why')}, len={result.get('len')})")
            return True
        if i == 0 and result.get("why"):
            print(f"[jq] 主页面注入: {result.get('why')}")

    # 2) Monaco
    try:
        ok = page.evaluate(
            """(code) => {
                try {
                    const eds = window.monaco?.editor?.getEditors?.() || [];
                    for (const ed of eds) {
                        const m = ed.getModel?.();
                        if (m?.setValue) { m.setValue(code); return true; }
                    }
                } catch (e) {}
                return false;
            }""",
            code,
        )
        if ok:
            print("[jq] Monaco 注入成功")
            return True
    except Exception:
        pass

    # 3) 点击编辑区 + 剪贴板粘贴（大文件兜底）
    try:
        editor = page.locator(".ace_editor").first
        if editor.count() > 0:
            page.evaluate(
                """async (code) => {
                    await navigator.clipboard.writeText(code);
                }""",
                code,
            )
            editor.click(timeout=5000)
            page.wait_for_timeout(300)
            page.keyboard.press("Control+A")
            page.wait_for_timeout(100)
            page.keyboard.press("Control+V")
            page.wait_for_timeout(500)
            if verify_editor_contains(page, "initialize"):
                print("[jq] 剪贴板粘贴成功")
                return True
    except Exception as e:
        print(f"[jq] 剪贴板方式失败: {e}")

    # 4) ace_text-input 分块
    try:
        inp = page.locator(".ace_text-input").first
        if inp.count() > 0:
            inp.click(force=True)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            chunk = 5000
            for i in range(0, len(code), chunk):
                page.keyboard.insert_text(code[i : i + chunk])
            page.wait_for_timeout(300)
            if verify_editor_contains(page, "initialize"):
                print("[jq] insert_text 注入成功")
                return True
    except Exception as e:
        print(f"[jq] insert_text 失败: {e}")

    return False


def verify_editor_contains(page, snippet: str, max_len: int = 80) -> bool:
    needle = snippet.strip()[:max_len] if snippet else ""
    if not needle:
        return True

    check_js = """
    (needle) => {
      try {
        if (typeof ace !== 'undefined') {
          for (const n of document.querySelectorAll('.ace_editor')) {
            const v = ace.edit(n).getValue();
            if (v.indexOf(needle) >= 0) return true;
          }
        }
      } catch (e) {}
      return false;
    }
    """
    for frame in [page] + list(page.frames):
        try:
            if frame.evaluate(check_js, needle):
                return True
        except Exception:
            continue
    return False


_JS_CLICK_BACKTEST = """
() => {
  const labels = ['运行回测', '完整回测', '回测', '编译运行', '运行'];
  const nodes = [
    ...document.querySelectorAll(
      'button, a, span, div, input[type=button], [role=button], .btn, .ant-btn'
    ),
  ];
  for (const want of labels) {
    for (const el of nodes) {
      const t = (el.innerText || el.value || el.title || el.getAttribute('aria-label') || '').trim();
      if (!t || t.length > 24) continue;
      if (want === '回测') {
        if (!t.includes('回测') || t.includes('历史') || t.includes('删除')) continue;
      } else if (t !== want && !t.includes(want)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const st = window.getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || st.pointerEvents === 'none') continue;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      try {
        el.scrollIntoView({ block: 'center', inline: 'center' });
        el.click();
        return { ok: true, text: t };
      } catch (e) {}
    }
  }
  return { ok: false };
}
"""


def click_run_backtest(page, timeout_ms: int = 15000) -> bool:
    """Click backtest run control on strategy editor toolbar."""
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass

    for i, frame in enumerate([page] + list(page.frames)):
        try:
            result = frame.evaluate(_JS_CLICK_BACKTEST) or {}
            if result.get("ok"):
                print(f"[jq] 已点击回测按钮 (frame={i}): 「{result.get('text')}」")
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue

    candidates = [
        page.get_by_role("button", name="运行回测"),
        page.get_by_role("button", name=re.compile(r"运行.*回测")),
        page.get_by_text("运行回测", exact=False),
        page.locator('button:has-text("运行回测")'),
        page.locator('a:has-text("运行回测")'),
        page.locator('span:has-text("运行回测")'),
        page.locator('.btn:has-text("运行回测")'),
        page.locator('.ant-btn:has-text("运行回测")'),
        page.get_by_role("button", name="完整回测"),
        page.locator('button:has-text("完整回测")'),
        page.locator('button:has-text("回测")').filter(has_not_text="历史"),
        page.get_by_role("button", name="编译运行"),
        page.locator('button:has-text("编译运行")'),
        page.locator('[title*="回测"]'),
    ]
    for loc in candidates:
        try:
            if loc.count() == 0:
                continue
            btn = loc.first
            btn.scroll_into_view_if_needed(timeout=3000)
            btn.click(timeout=timeout_ms, force=True)
            print("[jq] 已点击回测按钮 (Playwright locator)")
            page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False
