#!/usr/bin/env python
"""Build variants/idea-*.py from strategy.py (singles + merges 12/13/23/123)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "research" / "strategies" / "STR-20260520-ETF轮动1.7.1"
BASE = PROJ / "strategy.py"
VARIANTS = PROJ / "variants"

OVERSEAS_POOL = [
    "518880.XSHG", "501018.XSHG", "161226.XSHE", "159985.XSHE", "159980.XSHE",
    "513310.XSHG", "159518.XSHE", "159509.XSHE", "513100.XSHG", "513520.XSHG",
    "513500.XSHG", "159502.XSHE", "513400.XSHG", "513030.XSHG", "513290.XSHG",
    "520830.XSHG", "159529.XSHE",
    "513090.XSHG", "513120.XSHG", "513180.XSHG", "513330.XSHG", "513750.XSHG",
]

ACCEL_INIT_SNIPPET = (
    "    g.short_momentum_threshold = 0.0\n"
    "    g.accel_weight = 0.15  # ablation idea-1\n"
)

ACCEL_SCORE_SNIPPET = """
        # ===== idea-1: 5日加速补分（非年化 收益×R²×权重）=====
        if len(price_series) >= 6 and getattr(g, 'accel_weight', 0) > 0:
            seg = price_series[-6:]
            ret5 = seg[-1] / seg[0] - 1
            y5 = np.log(seg)
            x5 = np.arange(len(y5))
            w5 = np.linspace(1, 2, len(y5))
            s5, ic5 = np.polyfit(x5, y5, 1, w=w5)
            ss_res5 = np.sum(w5 * (y5 - (s5 * x5 + ic5)) ** 2)
            ss_tot5 = np.sum(w5 * (y5 - np.mean(y5)) ** 2)
            r2_5 = 1 - ss_res5 / ss_tot5 if ss_tot5 != 0 else 0
            score = score + ret5 * max(r2_5, 0) * g.accel_weight

"""

DAY_FILTER_BLOCK = """        # ===== 6. 近3日单日跌幅过滤（排除） =====
        if len(price_series) >= 4:
            day1 = price_series[-1] / price_series[-2]
            day2 = price_series[-2] / price_series[-3]
            day3 = price_series[-3] / price_series[-4]
            if min(day1, day2, day3) < g.loss:
                log.info(f"⚠️ {etf} {name} 近3日有单日跌幅超{(1-g.loss)*100:.1f}%，直接排除")
                return None"""

DRAWDOWN_FILTER_BLOCK = """        # ===== 6. idea-3: 近3日相对局部高点回撤 =====
        if len(price_series) >= 3:
            high_3d = np.max(price_series[-3:])
            dd_high = current_price / high_3d - 1 if high_3d > 0 else 0
            if dd_high < g.loss_drawdown_3d:
                log.info(f"⚠️ {etf} {name} 近3日相对高点回撤{dd_high*100:.2f}% < {g.loss_drawdown_3d*100:.1f}%，排除")
                return None"""

WEAK_FUNCS = '''

# ==================== idea-2: A股走弱期与海外子池 ====================
def update_weak_regime(context):
    weak_count = 0
    for idx in g.weak_regime_indices:
        try:
            hist = attribute_history(idx, 11, '1d', ['close'])
            if hist is None or len(hist) < 10:
                continue
            ma10 = hist['close'][-10:].mean()
            if hist['close'].iloc[-1] < ma10:
                weak_count += 1
        except Exception:
            continue
    was = g.in_weak_regime
    g.in_weak_regime = weak_count >= 3
    if g.in_weak_regime and not was:
        g.weak_regime_entry_date = context.current_dt.date()
        g.etf_pool = list(g.overseas_pool)
        log.info(f"走弱期开启({weak_count}/4指数<MA10)，切换海外子池{len(g.etf_pool)}只")
    elif not g.in_weak_regime:
        g.etf_pool = list(g.etf_pool_full)
        g.weak_regime_entry_date = None
        if was:
            log.info("走弱期结束，恢复全池")


def check_weak_regime_max_hold(context):
    if not g.in_weak_regime or not g.weak_regime_entry_date:
        return
    days = (context.current_dt.date() - g.weak_regime_entry_date).days
    if days < g.weak_regime_max_days:
        return
    log.info(f"走弱期已满{days}日，强制清仓并恢复全池")
    for sec in list(context.portfolio.positions.keys()):
        pos = context.portfolio.positions[sec]
        if pos.total_amount > 0:
            smart_order_target_value(sec, 0, context)
    g.in_weak_regime = False
    g.etf_pool = list(g.etf_pool_full)
    g.weak_regime_entry_date = None
    g.rankings_cache = {'date': None, 'data': None}

'''

MERGE_COMBOS: dict[str, tuple[str, list[str]]] = {
    "idea-12": ("合并 idea-1 加速补分 + idea-2 走弱期/海外池", ["idea-1", "idea-2"]),
    "idea-13": ("合并 idea-1 加速补分 + idea-3 高点回撤过滤", ["idea-1", "idea-3"]),
    "idea-23": ("合并 idea-2 走弱期/海外池 + idea-3 高点回撤过滤", ["idea-2", "idea-3"]),
    "idea-123": (
        "合并 idea-1 + idea-2 + idea-3（三项优化）",
        ["idea-1", "idea-2", "idea-3"],
    ),
}


def apply_idea1(code: str) -> str:
    if "g.accel_weight" not in code:
        code = code.replace(
            "    g.short_momentum_threshold = 0.0\n",
            ACCEL_INIT_SNIPPET,
            1,
        )
    if "idea-1: 5日加速补分" not in code:
        needle = "        score = annualized_returns * r_squared\n\n"
        if needle in code and DAY_FILTER_BLOCK.split("\n")[0] in code:
            code = code.replace(
                needle,
                needle + ACCEL_SCORE_SNIPPET + "\n",
                1,
            )
    return code


def apply_idea2(code: str) -> str:
    if "g.overseas_pool" in code and "def update_weak_regime" in code:
        return code
    pool_lines = ",\n        ".join(f'"{c}"' for c in OVERSEAS_POOL)
    code = code.replace(
        "    # ---------- 交易调度 ----------",
        f"""    g.etf_pool_full = list(g.etf_pool)
    g.overseas_pool = [
        {pool_lines}
    ]
    g.weak_regime_indices = ['000300.XSHG', '000905.XSHG', '399006.XSHE', '000852.XSHG']
    g.in_weak_regime = False
    g.weak_regime_entry_date = None
    g.weak_regime_max_days = 20

    # ---------- 交易调度 ----------""",
        1,
    )
    code = code.replace(
        "    run_daily(check_positions, time='09:10')",
        """    run_daily(update_weak_regime, time='09:40')
    run_daily(check_weak_regime_max_hold, time='13:55')
    run_daily(check_positions, time='09:10')""",
        1,
    )
    if "def update_weak_regime" not in code:
        code = code.replace(
            "# ==================== 卖出模块 ====================",
            WEAK_FUNCS + "# ==================== 卖出模块 ====================",
            1,
        )
    return code


def apply_idea3(code: str) -> str:
    if "g.loss_drawdown_3d" not in code:
        code = code.replace(
            "    g.loss = 0.97                      # 近3日单日跌幅阈值（排除）",
            "    g.loss = 0.97                      # 保留（未用于本变体）\n"
            "    g.loss_drawdown_3d = -0.05         # ablation idea-3: 相对3日高点回撤阈值",
            1,
        )
    if DAY_FILTER_BLOCK in code:
        code = code.replace(DAY_FILTER_BLOCK, DRAWDOWN_FILTER_BLOCK, 1)
    return code


def build_merged(code: str, patches: list[str]) -> str:
    for p in patches:
        if p == "idea-1":
            code = apply_idea1(code)
        elif p == "idea-2":
            code = apply_idea2(code)
        elif p == "idea-3":
            code = apply_idea3(code)
    return code


def write_variant(vid: str, code: str, note: str = "") -> None:
    header = f"# ABLATION_VARIANT={vid}\n"
    if note:
        header += f"# {note}\n"
    (VARIANTS / f"{vid}.py").write_text(header + code, encoding="utf-8")


def build_singles(base: str) -> None:
    write_variant("idea-1", build_merged(base, ["idea-1"]), "5日加速补分")
    write_variant("idea-2", build_merged(base, ["idea-2"]), "走弱期+海外池")
    write_variant("idea-3", build_merged(base, ["idea-3"]), "3日高点回撤")
    v4 = base.replace(
        "    g.short_momentum_threshold = 0.0",
        "    g.short_momentum_threshold = -0.03  # ablation idea-4",
        1,
    )
    v4 = v4.replace(
        """        if g.use_short_momentum_filter and short_annualized < g.short_momentum_threshold:
            log.debug(f"{etf} {name} 短期动量{short_annualized*100:.1f}% < 阈值{g.short_momentum_threshold*100:.1f}%，过滤")
            return None""",
        """        if g.use_short_momentum_filter and short_return < g.short_momentum_threshold:
            log.debug(f"{etf} {name} 10日收益{short_return*100:.1f}% < 阈值{g.short_momentum_threshold*100:.1f}%，过滤")
            return None""",
        1,
    )
    write_variant("idea-4", v4, "10日-3%对照")
    v5 = base.replace(
        '    log.info("========== 策略初始化完成 ==========")',
        '    log.info("========== 策略初始化完成 [idea-5 防御511880对照] ==========")',
        1,
    )
    write_variant("idea-5", v5, "511880对照")


def build_merges(base: str) -> None:
    for vid, (title, patches) in MERGE_COMBOS.items():
        code = build_merged(base, patches)
        write_variant(vid, code, title)


def main() -> None:
    VARIANTS.mkdir(exist_ok=True)
    base = BASE.read_text(encoding="utf-8")
    build_singles(base)
    build_merges(base)
    all_files = sorted(VARIANTS.glob("idea-*.py"))
    print("Built:", [p.name for p in all_files])


if __name__ == "__main__":
    main()
