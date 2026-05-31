#!/usr/bin/env python
"""Submit and run a single JoinQuant strategy file."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import argparse

    p = argparse.ArgumentParser(description="单独调试一只聚宽策略")
    p.add_argument(
        "--file",
        "-f",
        required=True,
        help="策略 .py 路径，例如 output/jq_strategies/test_wufu_V5.0_max.py",
    )
    p.add_argument("--name", help="任务名称（默认取文件名）")
    p.add_argument("--start", help="回测开始 YYYY-MM-DD（默认从 research/.../meta.yaml 读取）")
    p.add_argument("--end", help="回测结束 YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="模拟提交，不打开浏览器")
    p.add_argument("--enqueue-only", action="store_true", help="只入队，稍后 batch 执行")
    p.add_argument("--show", type=int, metavar="TASK_ID", help="查看已有任务结果")
    args = p.parse_args()

    from auto_quant.joinquant.single import print_task_result, run_strategy_file

    if args.show is not None:
        print_task_result(args.show)
        return

    task_id = run_strategy_file(
        args.file,
        name=args.name,
        start_date=args.start,
        end_date=args.end,
        dry_run=args.dry_run,
        enqueue_only=args.enqueue_only,
    )
    if not args.enqueue_only and not args.dry_run:
        print_task_result(task_id)


if __name__ == "__main__":
    main()
