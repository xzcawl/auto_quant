"""Resume ablation: skip completed variants, reserve slots for in-flight backtests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_quant.joinquant.links_store import load_links
from auto_quant.joinquant.report_status import fetch_report_status


def _report_url_for_variant(links: dict[str, Any], variant_id: str) -> tuple[str, str, dict]:
    """Return (report_url, edit_url, full entry)."""
    entry = (links.get("variants") or {}).get(variant_id) or {}
    report = entry.get("report_url") or (entry.get("metrics") or {}).get("report_url") or ""
    edit = entry.get("edit_url") or (entry.get("metrics") or {}).get("edit_url") or ""
    return str(report), str(edit), entry


def build_ablation_plan(page, project_dir: Path, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Inspect jq_links.yaml report URLs before enqueue.

    Returns:
        done: list[(job, metrics)] — skip re-run
        wait: list[job] — in-flight, count toward parallel slots
        run: list[job] — need full submit flow
        running_count: int
    """
    links = load_links(project_dir)
    done: list[tuple[dict[str, Any], dict[str, Any]]] = []
    wait: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    for job in jobs:
        vid = job["id"]
        report_url, edit_url, entry = _report_url_for_variant(links, vid)
        display = entry.get("strategy_display_name") or ""

        if not report_url:
            print(f"[jq] {vid}: 无历史 report_url，将新建回测")
            run.append(job)
            continue

        info = fetch_report_status(page, report_url)
        status = info["status"]
        print(f"[jq] {vid}: 状态={status}")

        if status == "complete":
            metrics = dict(info.get("metrics") or {})
            metrics["report_url"] = report_url
            if edit_url:
                metrics["edit_url"] = edit_url
            if display:
                metrics["strategy_display_name"] = display
            metrics.setdefault("backtest_id", entry.get("backtest_id"))
            if not metrics.get("annual_return") and not metrics.get("sharpe"):
                metrics["raw_note"] = metrics.get("raw_note") or "已完成，指标请见聚宽报告页"
            print(f"[jq] {vid}: 已完成，跳过重新触发")
            done.append((job, metrics))
            continue

        if status in ("running", "unknown"):
            job_copy = dict(job)
            job_copy["_resume_report_url"] = report_url
            job_copy["_resume_edit_url"] = edit_url
            job_copy["_resume_display_name"] = display
            note = "进行中" if status == "running" else "状态未知，按进行中计入并行槽位"
            print(f"[jq] {vid}: {note}，不重新触发 -> {report_url}")
            wait.append(job_copy)
            continue

        if status == "failed":
            print(f"[jq] {vid}: 上次失败，将重新触发回测")
            run.append(job)
            continue

        if status == "no_credits":
            metrics = {"report_url": report_url, "error": "no_credits"}
            done.append((job, metrics))
            continue

        run.append(job)

    running_count = len(wait)
    if running_count:
        print(f"[jq] 已有 {running_count} 个进行中的回测计入并行槽位")
    return {
        "done": done,
        "wait": wait,
        "run": run,
        "running_count": running_count,
    }


def refresh_running_slot_count(page, wait_jobs: list[dict[str, Any]]) -> int:
    """Re-check wait list; return how many are still running."""
    n = 0
    for job in wait_jobs:
        if job.get("_collected"):
            continue
        url = job.get("_resume_report_url", "")
        if not url:
            continue
        info = fetch_report_status(page, url)
        if info["status"] == "complete":
            job["_collected"] = True
            job["_done_metrics"] = info.get("metrics") or {}
            job["_done_metrics"]["report_url"] = url
            if job.get("_resume_edit_url"):
                job["_done_metrics"]["edit_url"] = job["_resume_edit_url"]
        elif info["status"] in ("running", "unknown"):
            n += 1
        else:
            n += 1
    return n
