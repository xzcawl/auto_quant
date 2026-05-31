#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在【聚宽研究环境】中运行，从 get_backtest 拉取各变体指标并导出 JSON。

用法（研究 Jupyter 单元格）:
    %run scripts/jq_research_export_metrics.py STR-20260520-ETF轮动1.7.1

或复制本文件内容到研究 notebook，修改 PROJECT_ID 后执行。

导出文件默认写入项目目录: research/strategies/<ID>/jq_metrics_export.json
本地再执行:
    python scripts/research.py ablation-refresh-metrics <ID>
    python scripts/research.py ablation-report <ID>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 研究环境无 auto_quant 包时，把仓库 src 加入路径
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PROJECT_ID = sys.argv[1] if len(sys.argv) > 1 else "STR-20260520-ETF轮动1.7.1"


def main():
    import yaml

    from auto_quant.joinquant.report_metrics import (
        backtest_id_from_url,
        extract_metrics_from_pack,
        jq_research_available,
        load_backtest_pack,
    )

    if not jq_research_available():
        print("【错误】未检测到 get_backtest。请在聚宽【研究】环境运行本脚本。")
        return 1

    project_dir = ROOT / "research" / "strategies" / PROJECT_ID
    links_path = project_dir / "jq_links.yaml"
    if not links_path.exists():
        print(f"缺少 {links_path}")
        return 1

    with open(links_path, encoding="utf-8") as f:
        links = yaml.safe_load(f) or {}
    variants = links.get("variants") or {}

    out: dict[str, dict] = {"project_id": PROJECT_ID, "variants": {}}
    for vid, entry in variants.items():
        report_url = entry.get("report_url") or (entry.get("metrics") or {}).get("report_url")
        bid = entry.get("backtest_id") or backtest_id_from_url(str(report_url or ""))
        if not bid:
            print(f"[skip] {vid}: 无 backtestId")
            continue
        print(f"[fetch] {vid} backtestId={bid}")
        pack = load_backtest_pack(bid)
        if not pack:
            print(f"[fail] {vid}: get_backtest_data 返回空")
            continue
        m = extract_metrics_from_pack(pack)
        m["report_url"] = report_url
        m["backtest_id"] = bid
        m["strategy_display_name"] = entry.get("strategy_display_name", "")
        out["variants"][vid] = m
        print(
            f"  年化={m.get('annual_return')} 夏普={m.get('sharpe')} "
            f"回撤={m.get('max_drawdown')} 胜率={m.get('win_rate')}"
        )

    export_path = project_dir / "jq_metrics_export.json"
    export_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入: {export_path}")
    print("本地执行: python scripts/research.py ablation-refresh-metrics", PROJECT_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
