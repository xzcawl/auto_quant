#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地抓取聚宽回测详情并生成 report_analysis_tools 风格 HTML 报告。

用法:
  python scripts/analyze_jq_report.py STR-20260520-五福v5 --top 3
  python scripts/analyze_jq_report.py STR-20260520-五福v5 --variants idea-B7,idea-B7-theme-tail
  python scripts/analyze_jq_report.py STR-20260520-五福v5 --top 3 --use-cache   # 仅用已缓存 pack
  python scripts/analyze_jq_report.py STR-20260520-五福v5 --compare             # 生成对比摘要

输出:
  research/strategies/<ID>/analysis/packs/<variant>.json
  research/strategies/<ID>/analysis/reports/<variant>.html
  research/strategies/<ID>/analysis/compare_summary.md  (--compare)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _project_dir(project_id: str) -> Path:
    return ROOT / "research" / "strategies" / project_id


def _pick_top_variants(metrics: dict[str, dict], n: int) -> list[str]:
    ranked = sorted(
        metrics.items(),
        key=lambda kv: float(kv[1].get("total_return") or -1),
        reverse=True,
    )
    return [vid for vid, _ in ranked[:n]]


def _format_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.2f}%"


def _yearly_returns(pack: dict) -> dict[int, float]:
    import pandas as pd

    from auto_quant.joinquant.report_analysis_tools import calc_annual_row

    results = pack.get("results") or []
    if not results:
        return {}
    df = pd.DataFrame(results)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").set_index("time")
    df["nav"] = 1 + df["returns"]
    df["d_ret"] = df["nav"].pct_change()
    df["d_ret"].iloc[0] = df["returns"].iloc[0]
    df["b_nav"] = 1 + df["benchmark_returns"]
    df["b_ret"] = df["b_nav"].pct_change()
    df["b_ret"].iloc[0] = df["benchmark_returns"].iloc[0]
    out: dict[int, float] = {}
    for y in df.index.year.unique():
        d_y = df[df.index.year == y]
        row = calc_annual_row(d_y["d_ret"], d_y["b_ret"], year=int(y))
        out[int(y)] = float(row.get("策略收益", 0))
    return out


def _top_drawdowns(pack: dict, n: int = 3) -> list[dict]:
    import numpy as np
    import pandas as pd

    results = pack.get("results") or []
    if not results:
        return []
    df = pd.DataFrame(results)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    df["nav"] = 1 + df["returns"]
    df["rolling_max"] = df["nav"].cummax()
    df["drawdown"] = (df["nav"] - df["rolling_max"]) / df["rolling_max"]
    df.loc[df["drawdown"] > -1e-7, "drawdown"] = 0
    df["is_high"] = df["drawdown"] == 0
    df["period_id"] = df["is_high"].cumsum()
    dd_list = []
    for _, group in df.groupby("period_id"):
        if len(group) <= 1:
            continue
        m_dd = group["drawdown"].min()
        if m_dd < -0.001:
            s_date = group["time"].iloc[0]
            v_date = group.loc[group["drawdown"].idxmin(), "time"]
            dd_list.append(
                {
                    "start": s_date.strftime("%Y-%m-%d"),
                    "trough": v_date.strftime("%Y-%m-%d"),
                    "mdd": float(m_dd),
                }
            )
    return sorted(dd_list, key=lambda x: x["mdd"])[:n]


def build_compare_summary(
    project_id: str,
    packs: dict[str, dict],
    metrics: dict[str, dict],
) -> str:
    lines = [
        f"# 高收益变体对比摘要 — {project_id}",
        "",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 回测不代表未来收益。数据来自聚宽回测页本地抓取 pack。",
        "",
        "## 总览",
        "",
        "| 变体 | 总收益 | 年化 | 最大回撤 | 夏普 | 日胜率 | 盈亏比 | 平均持仓天 |",
        "|------|--------|------|----------|------|--------|--------|------------|",
    ]
    for vid, pack in packs.items():
        m = metrics.get(vid, {})
        risk = pack.get("risk") or {}
        lines.append(
            f"| {vid} "
            f"| {_format_pct(m.get('total_return'))} "
            f"| {_format_pct(m.get('annual_return'))} "
            f"| {_format_pct(m.get('max_drawdown'))} "
            f"| {risk.get('sharpe', m.get('sharpe', '-'))} "
            f"| {_format_pct(risk.get('day_win_ratio', m.get('win_rate')))} "
            f"| {risk.get('profit_loss_ratio', m.get('pnl_ratio', '-'))} "
            f"| {risk.get('avg_position_days', '-')} |"
        )

    lines.extend(["", "## 逐年收益（策略）", ""])
    all_years = sorted({y for p in packs.values() for y in _yearly_returns(p).keys()})
    header = "| 变体 | " + " | ".join(str(y) for y in all_years) + " |"
    sep = "|------|" + "|".join(["------"] * len(all_years)) + "|"
    lines.extend([header, sep])
    for vid, pack in packs.items():
        yr = _yearly_returns(pack)
        cells = [_format_pct(yr.get(y)) for y in all_years]
        lines.append(f"| {vid} | " + " | ".join(cells) + " |")

    lines.extend(["", "## 最大回撤 Top3", ""])
    for vid, pack in packs.items():
        lines.append(f"### {vid}")
        for i, dd in enumerate(_top_drawdowns(pack), 1):
            lines.append(
                f"{i}. {dd['start']} → {dd['trough']}: {_format_pct(dd['mdd'])}"
            )
        lines.append("")

    lines.extend(["## 优化方向（基于报告）", ""])
    lines.extend(_optimization_hints(packs, metrics))
    return "\n".join(lines) + "\n"


def _optimization_hints(packs: dict[str, dict], metrics: dict[str, dict]) -> list[str]:
    hints: list[str] = []
    if "idea-B7-theme-tail" in packs and "idea-B7" in packs:
        t_tr = float(metrics.get("idea-B7-theme-tail", {}).get("total_return") or 0)
        b_tr = float(metrics.get("idea-B7", {}).get("total_return") or 0)
        if t_tr > b_tr:
            hints.append(
                f"- **theme-tail** 较 B7 总收益 +{(t_tr - b_tr) * 100:.1f}pp，"
                "可继续网格 `theme_strength` 阈值 / 尾部保留比例（±5%）"
            )
    if "idea-B7-pool-120" in packs:
        m = metrics.get("idea-B7-pool-120", {})
        mdd = float(m.get("max_drawdown") or 0)
        tr = float(m.get("total_return") or 0)
        hints.append(
            f"- **pool-120** 回撤 {_format_pct(mdd)} / 收益 {_format_pct(tr)}，"
            "建议在 115–135 间细扫 dynamic pool TopN"
        )
    # worst year across top variants
    worst: dict[int, list[tuple[str, float]]] = {}
    for vid, pack in packs.items():
        for y, r in _yearly_returns(pack).items():
            worst.setdefault(y, []).append((vid, r))
    if worst:
        weakest_year = min(worst.keys(), key=lambda y: min(r for _, r in worst[y]))
        row = sorted(worst[weakest_year], key=lambda x: x[1])
        hints.append(
            f"- 共同弱势年 **{weakest_year}**（最低 {row[0][0]} {_format_pct(row[0][1])}），"
            "优先检查该年宏观过滤/换仓阈值是否过松"
        )
    hints.append("- 第5–7节基于网页前 ~99 条成交，全样本交易质量以 stats 摘要为准")
    hints.append("- 下一步消融: pool-125/135、theme-tail 参数、switch 阈值 0.02/0.05 交叉")
    return hints


def main() -> int:
    parser = argparse.ArgumentParser(description="本地聚宽回测深度分析报告")
    parser.add_argument("project_id", help="策略项目 ID")
    parser.add_argument("--top", type=int, default=3, help="按 total_return 取前 N 个变体")
    parser.add_argument("--variants", help="逗号分隔变体 ID，优先于 --top")
    parser.add_argument("--use-cache", action="store_true", help="不抓取，仅用已有 pack")
    parser.add_argument("--compare", action="store_true", help="生成 compare_summary.md")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    from auto_quant.config import load_settings
    from auto_quant.joinquant.playwright_runner import _auth_path
    from auto_quant.joinquant.report_analysis_tools import write_analysis_html
    from auto_quant.joinquant.report_metrics import extract_metrics_from_pack, load_metrics_export
    from auto_quant.joinquant.report_pack_fetch import fetch_backtest_pack_from_url
    from auto_quant.joinquant.report_pack_io import load_pack_for_variant, pack_path, save_pack

    project_dir = _project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"项目不存在: {project_dir}")
        return 1

    metrics_path = project_dir / "jq_metrics_export.json"
    metrics = load_metrics_export(metrics_path)
    if not metrics:
        print(f"缺少指标文件: {metrics_path}，请先 refresh-metrics")
        return 1

    if args.variants:
        variant_ids = [v.strip() for v in args.variants.split(",") if v.strip()]
    else:
        variant_ids = _pick_top_variants(metrics, args.top)

    print(f"分析变体: {', '.join(variant_ids)}")

    analysis_dir = project_dir / "analysis" / "reports"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    packs: dict[str, dict] = {}

    if args.use_cache:
        for vid in variant_ids:
            pack = load_pack_for_variant(project_dir, vid)
            if not pack:
                print(f"[skip] {vid}: 无缓存 pack，去掉 --use-cache 重新抓取")
                continue
            packs[vid] = pack
    else:
        auth = _auth_path()
        if not auth.exists():
            print("未找到 config/jq_auth.json，请先 run_jq_batch.py --login")
            return 1
        jq = load_settings()["joinquant"]
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=args.headless or jq.get("headless", False))
            context = browser.new_context(storage_state=str(auth))
            page = context.new_page()
            try:
                for vid in variant_ids:
                    entry = metrics.get(vid) or {}
                    url = entry.get("report_url")
                    if not url:
                        print(f"[skip] {vid}: 无 report_url")
                        continue
                    print(f"[fetch] {vid} …")
                    pack = fetch_backtest_pack_from_url(page, url)
                    if not pack or not pack.get("results"):
                        print(f"[fail] {vid}: 抓取失败或无日收益")
                        continue
                    pack["variant_id"] = vid
                    pack["strategy_display_name"] = entry.get("strategy_display_name", vid)
                    save_pack(pack_path(project_dir, vid), pack)
                    packs[vid] = pack
                    m = extract_metrics_from_pack(pack)
                    print(
                        f"  日序列={len(pack['results'])} 成交={len(pack.get('orders') or [])} "
                        f"总收益={_format_pct(m.get('total_return'))} "
                        f"回撤={_format_pct(m.get('max_drawdown'))}"
                    )
                    page.wait_for_timeout(1200)
            finally:
                browser.close()

    for vid, pack in packs.items():
        entry = metrics.get(vid, {})
        title = entry.get("strategy_display_name") or vid
        subtitle = pack.get("report_url", "")
        html_path = analysis_dir / f"{vid}.html"
        write_analysis_html(pack, html_path, title=title, subtitle=subtitle)
        print(f"[html] {html_path}")

    if args.compare and packs:
        summary = build_compare_summary(args.project_id, packs, metrics)
        summary_path = project_dir / "analysis" / "compare_summary.md"
        summary_path.write_text(summary, encoding="utf-8")
        print(f"[compare] {summary_path}")

    return 0 if packs else 1


if __name__ == "__main__":
    raise SystemExit(main())
