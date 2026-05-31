#!/usr/bin/env python
"""
策略研究工作流 CLI

  思想 -> 建档 -> 实现/挂载代码 -> 回测 -> 报告 -> Cursor AI 分析
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import argparse

    p = argparse.ArgumentParser(description="策略研究流水线")
    sub = p.add_subparsers(dest="cmd", required=True)

    # new
    n = sub.add_parser("new", help="新建策略研究项目")
    n.add_argument("name", help="策略名称，如 五福ETF轮动")
    n.add_argument("--idea", default="", help="策略思想（一段话）")
    n.add_argument("--idea-file", help="从文件读取策略思想")
    n.add_argument("--platform", choices=["joinquant", "local_macd"], default="joinquant")
    n.add_argument("--tag", action="append", default=[], dest="tags")
    n.add_argument("--jq", help="已有聚宽 .py 路径，写入 meta 并复制到项目")

    # list
    sub.add_parser("list", help="列出所有研究项目")

    # show
    s = sub.add_parser("show", help="显示项目路径与状态")
    s.add_argument("id", help="项目 ID 或部分匹配名")

    # attach
    a = sub.add_parser("attach", help="挂载/复制策略代码到项目")
    a.add_argument("id")
    a.add_argument("file", help="源 .py 文件路径")

    # run
    r = sub.add_parser("run", help="执行回测（按 meta.platform）")
    r.add_argument("id")
    r.add_argument("--platform", choices=["joinquant", "local_macd"])
    r.add_argument("--dry-run", action="store_true", help="聚宽模拟，不打开浏览器")
    r.add_argument("--symbols", type=int, help="本地回测股票数")

    # report
    rep = sub.add_parser("report", help="生成 report.md + analysis_request.md（给 Cursor）")
    rep.add_argument("id")
    rep.add_argument("--open-brief", action="store_true", help="打印 analysis_request.md 路径")

    # status
    st = sub.add_parser("status", help="更新项目状态")
    st.add_argument("id")
    st.add_argument("value", choices=["draft", "implemented", "backtested", "reviewed", "archived"])

    # import
    imp = sub.add_parser("import", help="从已有 jq 文件快速创建项目")
    imp.add_argument("file", help="如 output/jq_strategies/test_wufu_V5.0_max.py")
    imp.add_argument("--name", help="显示名称，默认取文件名")
    imp.add_argument("--idea", default="")

    # ablation
    ab0 = sub.add_parser("ablation-init", help="从 idea.md 生成 ablation.yaml + variants/README")
    ab0.add_argument("id")
    ab0.add_argument("--force", action="store_true", help="覆盖已有 ablation.yaml")

    ab1 = sub.add_parser("ablation-queue", help="将 enabled 变体入队（可选立即跑）")
    ab1.add_argument("id")
    ab1.add_argument("--run", action="store_true", help="入队后对每个 task 依次 run_single_task")

    ab2 = sub.add_parser("ablation-run", help="依次回测每个 enabled 变体并写入 runs/*_jq_ablation*.json")
    ab2.add_argument("id")
    ab2.add_argument("--dry-run", action="store_true", help="不写聚宽，只写占位 JSON")

    ab2b = sub.add_parser(
        "ablation-check",
        help="检查 jq_links 中已有 report_url 状态（完成/进行中），不重新触发",
    )
    ab2b.add_argument("id")

    ab2c = sub.add_parser(
        "ablation-refresh-metrics",
        help="从 get_backtest 或 jq_metrics_export.json 填充 runs 指标并更新报告",
    )
    ab2c.add_argument("id")
    ab2c.add_argument(
        "--no-export",
        action="store_true",
        help="不使用 jq_metrics_export.json，仅尝试研究环境 get_backtest",
    )
    ab2c.add_argument(
        "--no-playwright",
        action="store_true",
        help="禁用 Playwright 报告页抓取（默认在无 export 时自动启用）",
    )
    ab2c.add_argument(
        "--variant",
        action="append",
        dest="variants",
        metavar="ID",
        help="仅刷新指定变体，如 --variant idea-2（可重复）",
    )
    ab2c.add_argument(
        "--rescrape",
        action="store_true",
        help="忽略 jq_metrics_export.json，强制重新抓取报告页",
    )

    ab3 = sub.add_parser("ablation-report", help="汇总消融结果 + 合并建议表")
    ab3.add_argument("id")

    ab4 = sub.add_parser(
        "ablation-build",
        help="从 strategy.py 生成 variants/idea-1..5 及合并 idea-12/13/23/123",
    )
    ab4.add_argument("id", nargs="?", default="STR-20260520-ETF轮动1.7.1")

    lk = sub.add_parser("links", help="查看项目 jq_links.yaml（报告 URL）")
    lk.add_argument("id")

    args = p.parse_args()

    from auto_quant.research import (
        attach_strategy_file,
        create_project,
        generate_ai_brief,
        list_projects,
        load_project,
        run_backtest_for_project,
        set_status,
        write_report,
    )
    from auto_quant.research.ablation import (
        check_ablation_status,
        init_ablation,
        queue_ablation,
        refresh_ablation_metrics,
        run_ablation,
        write_ablation_report,
    )

    if args.cmd == "new":
        proj = create_project(
            args.name,
            idea=args.idea,
            idea_file=args.idea_file,
            platform=args.platform,
            tags=args.tags,
        )
        if args.jq:
            attach_strategy_file(proj, args.jq)
        print(f"已创建: {proj.path}")
        print(f"  编辑思想: {proj.idea_path}")
        print(f"  下一步: python scripts/research.py run {proj.id}")
        return

    if args.cmd == "list":
        items = list_projects()
        if not items:
            print("暂无项目。运行: python scripts/research.py new \"我的策略\"")
            return
        print(f"{'ID':<32} {'状态':<12} {'平台':<12} 名称")
        print("-" * 72)
        for pr in items:
            print(
                f"{pr.id:<32} {pr.meta.get('status','?'):<12} "
                f"{pr.meta.get('platform','?'):<12} {pr.meta.get('name','')}"
            )
        return

    if args.cmd == "show":
        pr = load_project(args.id)
        print(f"路径: {pr.path}")
        print(f"状态: {pr.meta.get('status')} | 平台: {pr.meta.get('platform')}")
        print(f"思想: {pr.idea_path}")
        print(f"代码: {pr.jq_code_path() or pr.strategy_path or '（未挂载）'}")
        print(f"最新运行: {pr.meta.get('latest_run') or '无'}")
        return

    if args.cmd == "attach":
        pr = load_project(args.id)
        dest = attach_strategy_file(pr, args.file)
        print(f"已挂载: {dest}")
        return

    if args.cmd == "run":
        payload = run_backtest_for_project(
            args.id,
            platform=args.platform,
            dry_run_jq=args.dry_run,
            max_symbols=args.symbols,
        )
        print("回测完成，摘要:")
        print(json_dumps(payload))
        pr = load_project(args.id)
        write_report(pr.id)
        print(f"\n报告: {pr.path / 'report.md'}")
        print(f"Cursor AI: 在聊天中 @ {pr.path / 'analysis_request.md'}")
        return

    if args.cmd == "report":
        pr = load_project(args.id)
        path = write_report(pr.id)
        brief = generate_ai_brief(pr)
        print(f"报告: {path}")
        print(f"AI 分析稿: {brief}")
        if args.open_brief:
            print(f"\n--- 在 Cursor 中打开并 @ 该文件 ---\n{brief.read_text(encoding='utf-8')[:2000]}...")
        return

    if args.cmd == "status":
        set_status(args.id, args.value)
        print(f"已更新 {args.id} -> {args.value}")
        return

    if args.cmd == "import":
        f = Path(args.file)
        name = args.name or f.stem
        proj = create_project(
            name,
            idea=args.idea or f"从 {f.name} 导入的聚宽策略",
            platform="joinquant",
        )
        attach_strategy_file(proj, f)
        print(f"已导入: {proj.id}")
        print(f"  运行回测: python scripts/research.py run {proj.id}")
        return

    if args.cmd == "ablation-init":
        path = init_ablation(args.id, overwrite=args.force)
        print(f"已生成: {path}")
        print("下一步: 按 variants/README 复制 strategy.py；编辑 ablation.yaml 将变体 enabled: true；然后:")
        print(f"  python scripts/research.py ablation-run {args.id}")
        return

    if args.cmd == "ablation-queue":
        queue_ablation(args.id, enqueue_only=not args.run)
        if not args.run:
            print("仅入队。执行: python scripts/research.py ablation-queue <id> --run")
        return

    if args.cmd == "ablation-run":
        from auto_quant.research.ablation import _collect_ablation_jobs, load_ablation, refresh_ablation_metrics

        run_ablation(args.id, dry_run=args.dry_run)
        if not args.dry_run:
            cfg = load_ablation(args.id)
            enabled_ids = [j["id"] for j in _collect_ablation_jobs(cfg)]
            print(f"\n[metrics] ablation-run 仅写入 report_url；正在抓取指标 → jq_metrics_export.json …")
            try:
                n = refresh_ablation_metrics(
                    args.id,
                    only_variants=enabled_ids,
                    force_rescrape=True,
                )
                print(f"[metrics] 已抓取 {n} 个变体指标")
            except Exception as e:
                print(f"[metrics] 自动抓取失败: {e}")
                print(f"  请手动: python scripts/research.py ablation-refresh-metrics {args.id} --rescrape")
        write_ablation_report(args.id)
        print("已更新 ablation_report.md；可与 idea.md 对照后决定是否合并到 strategy.py")
        return

    if args.cmd == "ablation-check":
        check_ablation_status(args.id)
        return

    if args.cmd == "ablation-refresh-metrics":
        n = refresh_ablation_metrics(
            args.id,
            use_export=not args.no_export,
            use_playwright=not args.no_playwright,
            only_variants=args.variants,
            force_rescrape=args.rescrape or bool(args.variants),
        )
        if n:
            write_ablation_report(args.id)
            print(f"已更新 {n} 个变体指标并生成 ablation_report.md / ablation_summary.md")
        else:
            print("未更新任何变体。请在聚宽研究环境运行 jq_research_export_metrics.py 后重试。")
        return

    if args.cmd == "ablation-report":
        write_ablation_report(args.id)
        return

    if args.cmd == "links":
        pr = load_project(args.id)
        p = pr.path / "jq_links.yaml"
        if not p.exists():
            print(f"尚无 {p}，请先 ablation-run")
            return
        print(p.read_text(encoding="utf-8"))
        return

    if args.cmd == "ablation-build":
        import subprocess
        import sys as _sys

        pid = getattr(args, "id", None) or "STR-20260520-ETF轮动1.7.1"
        if "五福" in pid:
            script = ROOT / "scripts" / "build_wufu_ablation_variants.py"
        else:
            script = ROOT / "scripts" / "build_ablation_variants.py"
        subprocess.check_call([_sys.executable, str(script)], cwd=str(ROOT))
        print(f"变体已生成 ({pid})。请确认 ablation.yaml 中 enabled: true 后执行 ablation-run")
        return


def json_dumps(obj):
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
