"""Orchestrate research: idea -> backtest -> report -> AI brief."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_quant.config import load_settings, project_root
from auto_quant.research.ai_brief import generate_ai_brief
from auto_quant.research.registry import (
    Project,
    attach_strategy_file,
    create_project,
    list_projects,
    load_project,
    record_run,
    save_meta,
)


def create_project(
    name: str,
    idea: str = "",
    *,
    platform: str = "joinquant",
    tags: list[str] | None = None,
    jq_code_path: str = "",
    idea_file: str | Path | None = None,
) -> Project:
    from auto_quant.research import registry as reg

    if idea_file:
        idea = Path(idea_file).read_text(encoding="utf-8")
    return reg.create_project(
        name, idea, platform=platform, tags=tags, jq_code_path=jq_code_path
    )


def run_backtest_for_project(
    project_id: str,
    *,
    platform: str | None = None,
    dry_run_jq: bool = False,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    project = load_project(project_id)
    plat = platform or project.meta.get("platform", "joinquant")

    if plat == "local_macd":
        return _run_local_macd(project, max_symbols=max_symbols)
    if plat == "joinquant":
        return _run_joinquant(project, dry_run=dry_run_jq)
    raise ValueError(f"未知平台: {plat}，可选 joinquant | local_macd")


def _run_local_macd(project: Project, *, max_symbols: int | None) -> dict[str, Any]:
    from auto_quant.engine.backtest_runner import run_macd_backtest
    from auto_quant.strategies.macd_divergence import StrategyParams

    bt = project.meta.get("backtest", {}).get("local", {})
    lp = project.meta.get("local_params") or {}
    sp = load_settings()["strategy"]
    params = StrategyParams(
        macd_fast=lp.get("macd_fast", sp["macd_fast"]),
        macd_slow=lp.get("macd_slow", sp["macd_slow"]),
        macd_signal=lp.get("macd_signal", sp["macd_signal"]),
        ma_trend=lp.get("ma_trend", sp["ma_trend"]),
        stop_loss_pct=lp.get("stop_loss_pct", sp["stop_loss_pct"]),
        hold_days=lp.get("hold_days", sp["hold_days"]),
    )

    result = run_macd_backtest(
        params=params,
        max_symbols=max_symbols or bt.get("max_symbols"),
        start=bt.get("start_date") or None,
        end=bt.get("end_date") or None,
        save_best=False,
    )

    payload = {
        "type": "local_macd",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "params": params.__dict__,
        "metrics": result.metrics.to_dict(),
        "report_path": result.report_path,
        "warnings": result.metrics.warnings,
    }
    record_run(project, "local", payload)
    return payload


def _run_joinquant(project: Project, *, dry_run: bool) -> dict[str, Any]:
    from auto_quant.joinquant.single import print_task_result, run_strategy_file

    jq_path = project.jq_code_path()
    if not jq_path:
        raise FileNotFoundError(
            f"项目 {project.id} 无聚宽代码。请: research attach {project.id} <path/to.py>"
        )

    task_id = run_strategy_file(
        jq_path,
        name=project.meta.get("name", project.id),
        dry_run=dry_run,
        enqueue_only=False,
    )

    # fetch result from queue if available
    import sqlite3

    metrics: dict[str, Any] = {"task_id": task_id}
    db = project_root() / "storage" / "results.db"
    if db.exists():
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT annual_return, sharpe, max_drawdown, win_rate, raw_json "
                "FROM jq_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row:
            metrics.update(
                {
                    "annual_return": row[0],
                    "sharpe": row[1],
                    "max_drawdown": row[2],
                    "win_rate": row[3],
                }
            )
            if row[4]:
                try:
                    metrics["raw"] = json.loads(row[4])
                except json.JSONDecodeError:
                    pass

    payload = {
        "type": "joinquant",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "jq_code_path": str(jq_path),
        "metrics": metrics,
        "dry_run": dry_run,
    }
    record_run(project, "jq", payload)
    if not dry_run:
        print_task_result(task_id)
    return payload


def write_report(project_id: str) -> Path:
    project = load_project(project_id)
    brief_path = generate_ai_brief(project)

    runs = sorted(project.runs_dir.glob("*.json"), reverse=True)
    run_summaries = []
    for r in runs[:5]:
        data = json.loads(r.read_text(encoding="utf-8"))
        run_summaries.append(f"### {r.name}\n\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```")

    report = f"""# 研究报告 · {project.meta.get('name', project.id)}

- **ID**: `{project.id}`
- **状态**: {project.meta.get('status')}
- **平台**: {project.meta.get('platform')}
- **更新**: {project.meta.get('updated_at')}

## 策略思想

{(project.idea_path.read_text(encoding='utf-8') if project.idea_path.exists() else '')}

## 回测记录

{chr(10).join(run_summaries) or '（无）'}

## AI 分析

请在 Cursor 中打开并讨论：

- [`analysis_request.md`](analysis_request.md)
- 策略代码：`{project.meta.get('jq_code_path') or 'strategy.py'}`

生成分析后，可将结论写入本文件下方「评审结论」节。

## 评审结论

（人工或 AI 填写）

"""
    out = project.path / "report.md"
    out.write_text(report, encoding="utf-8")
    generate_ai_brief(project)
    return out


def set_status(project_id: str, status: str) -> None:
    allowed = {"draft", "implemented", "backtested", "reviewed", "archived"}
    if status not in allowed:
        raise ValueError(f"status 必须是 {allowed}")
    project = load_project(project_id)
    project.meta["status"] = status
    save_meta(project.path, project.meta)
