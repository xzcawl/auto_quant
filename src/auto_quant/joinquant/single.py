"""Run a single JoinQuant strategy file (enqueue + submit)."""

from __future__ import annotations

import json
from pathlib import Path

from auto_quant.config import project_root
from auto_quant.joinquant.playwright_runner import run_batch, run_single_task
from auto_quant.joinquant.queue import TaskQueue


def enqueue_strategy(file_path: str | Path, *, name: str | None = None, priority: int = 100) -> int:
    """Add one strategy file to the task queue."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"策略文件不存在: {path}")
    if path.suffix != ".py":
        raise ValueError("策略文件必须是 .py")

    strategy_name = name or path.stem
    q = TaskQueue()
    return q.enqueue(strategy_name, str(path), priority=priority)


def _jq_dates_from_research_meta(file_path: Path) -> tuple[str | None, str | None]:
    """If file under research/strategies/<ID>/, read meta.yaml joinquant dates."""
    import yaml

    for folder in (file_path.parent, file_path.parent.parent):
        meta = folder / "meta.yaml"
        if not meta.is_file():
            continue
        try:
            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        jq = (data.get("backtest") or {}).get("joinquant") or {}
        start = jq.get("start_date")
        end = jq.get("end_date")
        if start and end:
            return str(start), str(end)
    return None, None


def run_strategy_file(
    file_path: str | Path,
    *,
    name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    enqueue_only: bool = False,
) -> int:
    """
    单独调试一只聚宽策略：
    - enqueue_only: 只入队，不提交
    - 否则入队后立即跑 Playwright 提交（仅处理该任务）
    """
    task_id = enqueue_strategy(file_path, name=name)
    print(f"已入队 task_id={task_id} -> {Path(file_path).resolve()}")

    if enqueue_only:
        print("仅入队。稍后可执行: python scripts/run_jq_batch.py")
        return task_id

    path = Path(file_path).resolve()
    if not start_date or not end_date:
        auto_start, auto_end = _jq_dates_from_research_meta(path)
        start_date = start_date or auto_start
        end_date = end_date or auto_end
    if start_date and end_date:
        print(f"聚宽回测区间: {start_date} ~ {end_date}")

    if dry_run:
        run_batch(dry_run=True)
    else:
        run_single_task(task_id, jq_start=start_date, jq_end=end_date)
    return task_id


def print_task_result(task_id: int) -> None:
    """Print latest result for a task from SQLite."""
    import sqlite3

    db = project_root() / "storage" / "results.db"
    if not db.exists():
        print("尚无回测数据库，请先提交策略。")
        return

    with sqlite3.connect(db) as c:
        task = c.execute(
            "SELECT strategy_name, status, result_json FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        rows = c.execute(
            "SELECT annual_return, sharpe, max_drawdown, win_rate, raw_json, created_at "
            "FROM jq_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()

    if not task:
        print(f"未找到 task_id={task_id}")
        return

    name, status, result_json = task
    print(f"\n=== {name} (task {task_id}) ===")
    print(f"状态: {status}")
    if result_json:
        print(f"任务结果 JSON:\n{json.dumps(json.loads(result_json), ensure_ascii=False, indent=2)}")
    if rows:
        ann, sharpe, mdd, wr, raw, created = rows
        print(f"\n指标 (记录时间 {created}):")
        print(f"  年化收益: {ann}")
        print(f"  夏普: {sharpe}")
        print(f"  最大回撤: {mdd}")
        print(f"  胜率: {wr}")
    print(f"\nMarkdown 汇总: {project_root() / 'output' / '回测结果.md'}")
