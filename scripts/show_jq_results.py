#!/usr/bin/env python
"""List JoinQuant backtest results from SQLite."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import sqlite3

    from auto_quant.config import project_root

    db = project_root() / "storage" / "results.db"
    if not db.exists():
        print("尚无结果。请先运行 run_jq_single.py 或 run_jq_batch.py")
        return

    with sqlite3.connect(db) as c:
        rows = c.execute(
            """
            SELECT t.id, t.strategy_name, t.status, t.jq_code_path,
                   r.annual_return, r.sharpe, r.max_drawdown, r.win_rate, r.created_at
            FROM tasks t
            LEFT JOIN jq_results r ON r.task_id = t.id
            ORDER BY t.id DESC
            LIMIT 20
            """
        ).fetchall()

    print(f"{'ID':<4} {'状态':<18} {'年化':<8} {'夏普':<8} {'回撤':<8} 策略名")
    print("-" * 70)
    for rid, name, status, path, ann, sharpe, mdd, wr, created in rows:
        print(
            f"{rid:<4} {status:<18} {str(ann or '-'):<8} {str(sharpe or '-'):<8} "
            f"{str(mdd or '-'):<8} {name}"
        )
        print(f"     文件: {path}")
        if created:
            print(f"     记录: {created}")
    print(f"\n详情: python scripts/run_jq_single.py --show <TASK_ID>")
    print(f"Markdown: {project_root() / 'output' / '回测结果.md'}")


if __name__ == "__main__":
    main()
