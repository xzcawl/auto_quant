"""
Ablation workflow: map idea.md optimization points -> variant .py files ->
sequential JoinQuant runs -> comparison report -> merge hints.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from auto_quant.config import load_settings, project_root
from auto_quant.research.registry import Project, load_project, record_run, save_meta


def parse_idea_sections(idea_path: Path) -> list[dict[str, str]]:
    """Parse `## 1. title` style sections from idea.md."""
    if not idea_path.exists():
        return []
    text = idea_path.read_text(encoding="utf-8")
    sections: list[dict[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"^##\s*(\d+)\.\s*(.+)$", line.strip())
        if m:
            num, title = m.group(1), m.group(2).strip()
            sections.append(
                {
                    "id": f"idea-{num}",
                    "num": num,
                    "title": title[:120],
                    "file": f"variants/idea-{num}.py",
                }
            )
    return sections


def ablation_yaml_path(project: Project) -> Path:
    return project.path / "ablation.yaml"


def init_ablation(project_id: str, *, overwrite: bool = False) -> Path:
    """Create ablation.yaml from idea.md section headers + baseline."""
    project = load_project(project_id)
    path = ablation_yaml_path(project)
    if path.exists() and not overwrite:
        raise FileExistsError(f"已存在 {path}，若需覆盖请加 ablation-init --force")

    sections = parse_idea_sections(project.idea_path)
    variants = []
    for s in sections:
        variants.append(
            {
                "id": s["id"],
                "title": s["title"],
                "file": s["file"],
                "idea_ref": f"idea.md §{s['num']}",
                "enabled": False,
                "notes": "从 strategy.py 复制本文件后仅改本优化点相关代码，再 enabled: true",
            }
        )

    data: dict[str, Any] = {
        "_comment": "同一回测区间对比各变体；日期优先用下方 joinquant，否则用 config/settings.yaml",
        "joinquant_date_range": {
            "start": None,
            "end": None,
        },
        "baseline": {
            "id": "baseline",
            "title": "基线（当前 strategy.py）",
            "file": "strategy.py",
            "enabled": True,
        },
        "variants": variants,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    vdir = project.path / "variants"
    vdir.mkdir(exist_ok=True)
    readme = vdir / "README.md"
    readme.write_text(
        """# 消融变体代码

对每个优化点：

1. 复制项目根目录的 `strategy.py` 为 `variants/idea-N.py`（与 `ablation.yaml` 中 `file` 一致）。
2. **只修改**该优化点相关的参数/分支（其它保持与基线一致）。
3. 在 `ablation.yaml` 里把对应条目的 `enabled` 改为 `true`。
4. 运行：

```bash
python scripts/research.py ablation-run STR-xxx
python scripts/research.py ablation-report STR-xxx
```

所有变体应使用相同回测区间（见 `ablation.yaml` 的 `joinquant_date_range` 或全局 `config/settings.yaml` 的 `joinquant` 段）。
""",
        encoding="utf-8",
    )

    # 记录到 meta 方便发现
    project.meta.setdefault("ablation", {})["config"] = "ablation.yaml"
    save_meta(project.path, project.meta)

    return path


def resolve_jq_dates(project: Project, cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Priority: ablation.yaml joinquant_date_range > meta.yaml backtest.joinquant > settings."""
    cfg = cfg or load_ablation(project.id)
    dr = cfg.get("joinquant_date_range") or {}
    start = dr.get("start")
    end = dr.get("end")
    meta_jq = (project.meta.get("backtest") or {}).get("joinquant") or {}
    if not start:
        start = meta_jq.get("start_date")
    if not end:
        end = meta_jq.get("end_date")
    settings = load_settings()["joinquant"]
    start = start or settings.get("jq_backtest_start", "2024-01-01")
    end = end or settings.get("jq_backtest_end", "2024-12-31")
    return str(start), str(end)


def load_ablation(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    p = ablation_yaml_path(project)
    if not p.exists():
        raise FileNotFoundError(f"缺少 {p}，请先: python scripts/research.py ablation-init {project_id}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_file(project: Project, rel: str) -> Path:
    return (project.path / rel).resolve()


def _metrics_from_sqlite(task_id: int) -> dict[str, Any]:
    import sqlite3

    out: dict[str, Any] = {"task_id": task_id}
    db = project_root() / "storage" / "results.db"
    if not db.exists():
        return out
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT annual_return, sharpe, max_drawdown, win_rate, raw_json "
            "FROM jq_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if row:
        out.update(
            {
                "annual_return": row[0],
                "sharpe": row[1],
                "max_drawdown": row[2],
                "win_rate": row[3],
            }
        )
    return out


def queue_ablation(project_id: str, *, enqueue_only: bool = True) -> list[tuple[str, int]]:
    """Enqueue all enabled baseline + variants. Returns [(variant_id, task_id), ...]."""
    from auto_quant.joinquant.single import enqueue_strategy

    project = load_project(project_id)
    cfg = load_ablation(project_id)
    results: list[tuple[str, int]] = []

    def _enqueue(vid: str, title: str, rel_file: str) -> None:
        fp = _resolve_file(project, rel_file)
        if not fp.exists():
            print(f"[skip] {vid}: 文件不存在 {fp}")
            return
        name = f"{project.id}_{vid}"
        tid = enqueue_strategy(fp, name=name, priority=50)
        results.append((vid, tid))
        print(f"[queue] {vid} -> task_id={tid} ({fp.name})")

    base = cfg.get("baseline") or {}
    if base.get("enabled", True):
        _enqueue(str(base.get("id", "baseline")), str(base.get("title", "baseline")), str(base.get("file", "strategy.py")))

    for v in cfg.get("variants") or []:
        if not v.get("enabled"):
            continue
        _enqueue(str(v["id"]), str(v.get("title", v["id"])), str(v["file"]))

    if not results:
        print("没有 enabled 的变体。请编辑 ablation.yaml 或准备 variants/*.py")
    if not enqueue_only:
        from auto_quant.joinquant.playwright_runner import run_single_task

        for vid, tid in results:
            print(f"\n>>> 运行 {vid} (task {tid})")
            run_single_task(tid)

    return results


def _jq_display_name(project: Project, variant_id: str) -> str:
    """聚宽列表中显示的策略名（可识别变体）。"""
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", project.meta.get("name", "策略"))[:24]
    return f"{base}_{variant_id}"


def _collect_ablation_jobs(cfg: dict[str, Any], *, include_disabled: bool = False) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    base = cfg.get("baseline") or {}
    if include_disabled or base.get("enabled", True):
        jobs.append(
            {
                "id": str(base.get("id", "baseline")),
                "title": str(base.get("title", "基线")),
                "file": str(base.get("file", "strategy.py")),
                "idea_ref": "baseline",
            }
        )
    for v in cfg.get("variants") or []:
        if include_disabled or v.get("enabled"):
            jobs.append(
                {
                    "id": str(v["id"]),
                    "title": str(v.get("title", v["id"])),
                    "file": str(v["file"]),
                    "idea_ref": str(v.get("idea_ref", "")),
                }
            )
    return jobs


def _record_ablation_result(
    project: Project,
    job: dict[str, Any],
    metrics: dict[str, Any],
    jq_start: str,
    jq_end: str,
    rows: list[dict[str, Any]],
    *,
    task_id: int | None = None,
    strategy_display_name: str = "",
) -> dict[str, Any]:
    from auto_quant.joinquant.links_store import save_variant_links

    fp = _resolve_file(project, job.get("file", "strategy.py"))
    payload = _ablation_payload(project, job, fp, jq_start, jq_end, metrics, dry_run=False)
    if task_id is not None:
        payload["task_id"] = task_id
    record_run(project, "jq_ablation", payload, filename_slug=job["id"])
    rows.append(payload)
    name = strategy_display_name or metrics.get("strategy_display_name") or _jq_display_name(
        project, job["id"]
    )
    save_variant_links(
        project.path,
        job["id"],
        strategy_display_name=name,
        edit_url=metrics.get("edit_url", ""),
        report_url=metrics.get("report_url", ""),
        backtest_id=metrics.get("backtest_id"),
        metrics=metrics,
        task_id=task_id,
    )
    print(f"[links] {job['id']} report_url={metrics.get('report_url')}")
    return payload


def check_ablation_status(project_id: str) -> list[dict[str, Any]]:
    """Open jq_links report URLs and print complete / running / failed (no re-submit)."""
    from auto_quant.joinquant.ablation_resume import _report_url_for_variant
    from auto_quant.joinquant.links_store import load_links
    from auto_quant.joinquant.playwright_runner import _auth_path
    from auto_quant.joinquant.report_status import fetch_report_status

    project = load_project(project_id)
    cfg = load_ablation(project_id)
    jobs = _collect_ablation_jobs(cfg)
    links = load_links(project.path)
    auth = _auth_path()
    if not auth.exists():
        print("未找到登录态，请先: python scripts/run_jq_batch.py --login")
        return []

    settings = load_settings()
    jq = settings["joinquant"]
    results: list[dict[str, Any]] = []

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth))
        page = context.new_page()
        try:
            for job in jobs:
                vid = job["id"]
                report_url, edit_url, _ = _report_url_for_variant(links, vid)
                if not report_url:
                    row = {"variant_id": vid, "status": "no_url", "report_url": ""}
                    print(f"{vid}: 无 report_url")
                    results.append(row)
                    continue
                info = fetch_report_status(page, report_url)
                row = {
                    "variant_id": vid,
                    "status": info["status"],
                    "report_url": report_url,
                    "edit_url": edit_url,
                    "has_metrics": info.get("has_metrics"),
                    "metrics": info.get("metrics"),
                }
                results.append(row)
                print(f"{vid}: {info['status']} | {report_url}")
        finally:
            browser.close()
    return results


def run_ablation(project_id: str, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Run enabled variants; skip completed, reserve slots for in-flight (jq_links)."""
    from auto_quant.joinquant.ablation_resume import build_ablation_plan, refresh_running_slot_count
    from auto_quant.joinquant.playwright_runner import _auth_path, _run_task_on_page
    from auto_quant.joinquant.queue import TaskQueue
    from auto_quant.joinquant.report_status import wait_for_report_complete
    from auto_quant.joinquant.single import enqueue_strategy

    project = load_project(project_id)
    cfg = load_ablation(project_id)
    jq_start, jq_end = resolve_jq_dates(project, cfg)
    print(f"聚宽回测区间: {jq_start} ~ {jq_end}")

    jobs = _collect_ablation_jobs(cfg)
    rows: list[dict[str, Any]] = []

    if dry_run:
        for job in jobs:
            fp = _resolve_file(project, job["file"])
            if not fp.exists():
                continue
            metrics = {"dry_run": True, "report_url": "", "edit_url": ""}
            payload = _ablation_payload(project, job, fp, jq_start, jq_end, metrics, dry_run=True)
            record_run(project, "jq_ablation", payload, filename_slug=job["id"])
            rows.append(payload)
        return rows

    auth = _auth_path()
    if not auth.exists():
        print("未找到登录态，请先: python scripts/run_jq_batch.py --login")
        return rows

    settings = load_settings()
    jq = dict(settings["joinquant"])
    jq["jq_backtest_start"] = jq_start
    jq["jq_backtest_end"] = jq_end

    from playwright.sync_api import sync_playwright

    q = TaskQueue()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=jq.get("headless", False))
        context = browser.new_context(storage_state=str(auth))
        page = context.new_page()
        try:
            print("\n[jq] 检查 jq_links 已有回测状态 …")
            plan = build_ablation_plan(page, project.path, jobs)
            wait_jobs: list[dict[str, Any]] = plan["wait"]
            jq["known_running_backtests"] = plan["running_count"]

            for job, metrics in plan["done"]:
                _record_ablation_result(project, job, metrics, jq_start, jq_end, rows)

            run_jobs = plan["run"]
            print(
                f"\n[jq] 计划: 已完成跳过 {len(plan['done'])} 个, "
                f"进行中占位 {len(wait_jobs)} 个, 待触发 {len(run_jobs)} 个"
            )

            for job in run_jobs:
                fp = _resolve_file(project, job["file"])
                if not fp.exists():
                    print(f"[skip] {job['id']}: 文件不存在 {fp}")
                    continue
                jq["known_running_backtests"] = refresh_running_slot_count(page, wait_jobs)
                display = _jq_display_name(project, job["id"])
                tid = enqueue_strategy(fp, name=display, priority=50)
                t = q.get_task(tid)
                if not t:
                    continue
                print(f"\n[run] {job['id']} -> {display} (task {tid})")
                q.update_status(tid, "running")
                try:
                    metrics = _run_task_on_page(page, t, jq, q)
                    _record_ablation_result(
                        project, job, metrics, jq_start, jq_end, rows, task_id=tid, strategy_display_name=display
                    )
                except Exception as e:
                    import json

                    q.update_status(tid, "failed", json.dumps({"error": str(e)}, ensure_ascii=False))
                    print(f"失败 {job['id']}: {e}")

            for job in wait_jobs:
                if job.get("_collected") and job.get("_done_metrics"):
                    _record_ablation_result(
                        project, job, job["_done_metrics"], jq_start, jq_end, rows,
                        strategy_display_name=job.get("_resume_display_name", ""),
                    )
                    continue
                print(f"\n[wait] 等待已有回测: {job['id']}")
                metrics = wait_for_report_complete(
                    page,
                    job["_resume_report_url"],
                    jq,
                    edit_url=job.get("_resume_edit_url", ""),
                    strategy_display_name=job.get("_resume_display_name", ""),
                )
                _record_ablation_result(
                    project, job, metrics, jq_start, jq_end, rows,
                    strategy_display_name=job.get("_resume_display_name", ""),
                )
        finally:
            browser.close()

    return rows


def _ablation_payload(
    project: Project,
    job: dict[str, Any],
    fp: Path,
    jq_start: str,
    jq_end: str,
    metrics: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "type": "joinquant_ablation",
        "ablation_variant_id": job["id"],
        "title": job["title"],
        "idea_ref": job.get("idea_ref", ""),
        "jq_code_path": str(fp.relative_to(project_root())),
        "backtest_start": jq_start,
        "backtest_end": jq_end,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "metrics": metrics,
        "report_url": metrics.get("report_url", ""),
        "edit_url": metrics.get("edit_url", ""),
        "backtest_id": metrics.get("backtest_id"),
        "dry_run": dry_run,
    }


def refresh_ablation_metrics(
    project_id: str,
    *,
    use_export: bool = True,
    use_playwright: bool = True,
    only_variants: list[str] | None = None,
    force_rescrape: bool = False,
) -> int:
    """
    Fill metrics into runs/* from get_backtest (研究环境) or jq_metrics_export.json (本地).

    Returns number of variants updated.
    """
    from auto_quant.joinquant.ablation_resume import _report_url_for_variant
    from auto_quant.joinquant.links_store import load_links, save_variant_links
    from auto_quant.joinquant.report_metrics import (
        backtest_id_from_url,
        fetch_metrics_by_backtest_id,
        fetch_metrics_by_report_url,
        jq_research_available,
        load_metrics_export,
    )

    project = load_project(project_id)
    cfg = load_ablation(project_id)
    jq_start, jq_end = resolve_jq_dates(project, cfg)
    links = load_links(project.path)
    export_path = project.path / "jq_metrics_export.json"

    exported: dict[str, dict[str, Any]] = {}
    if use_export and export_path.exists() and not force_rescrape:
        exported = load_metrics_export(export_path)
        print(f"[metrics] 已加载导出文件: {export_path} ({len(exported)} 个变体)")

    scraped: dict[str, dict[str, Any]] = {}
    need_scrape = force_rescrape or only_variants or (not exported and use_playwright)
    if jq_research_available() and not force_rescrape and not only_variants:
        print("[metrics] 检测到聚宽 get_backtest，将直接从 API 拉取")
    elif need_scrape and use_playwright:
        print("[metrics] 使用 Playwright 从报告页抓取指标（按行解析「策略收益/策略年化收益」）…")
        from auto_quant.joinquant.report_scrape import scrape_all_variants

        variants = links.get("variants") or {}
        scraped = scrape_all_variants(
            project.path,
            variants,
            start_date=jq_start,
            end_date=jq_end,
            only_ids=only_variants,
        )
        if scraped:
            existing_export = load_metrics_export(export_path) if export_path.exists() else {}
            merged_export = {**existing_export, **scraped}
            export_path.write_text(
                json.dumps(
                    {"project_id": project_id, "variants": merged_export},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[metrics] 已更新 {export_path.name}（{len(merged_export)} 个变体）")
            exported = {**exported, **merged_export}
    elif not exported:
        print(
            "[metrics] 本地无 get_backtest。可选:\n"
            f"  1) 聚宽研究: %run scripts/jq_research_export_metrics.py {project_id}\n"
            f"  2) 默认已尝试 Playwright；确认 config/jq_auth.json 有效"
        )
        return 0

    jobs = _collect_ablation_jobs(cfg, include_disabled=bool(only_variants))
    if only_variants:
        jobs = [j for j in jobs if j["id"] in only_variants]
    updated = 0

    for job in jobs:
        vid = job["id"]
        report_url, edit_url, entry = _report_url_for_variant(links, vid)
        bid = entry.get("backtest_id") or backtest_id_from_url(report_url)

        metrics: dict[str, Any] | None = None
        if vid in exported:
            metrics = dict(exported[vid])
        elif vid in scraped:
            metrics = dict(scraped[vid])
        elif report_url and jq_research_available():
            metrics = fetch_metrics_by_report_url(report_url)
        elif bid and jq_research_available():
            metrics = fetch_metrics_by_backtest_id(bid)
            if metrics:
                metrics["report_url"] = report_url
                metrics["backtest_id"] = bid

        if not metrics:
            print(f"[skip] {vid}: 无指标来源")
            continue

        if report_url:
            metrics["report_url"] = report_url
        if edit_url:
            metrics["edit_url"] = edit_url
        if bid:
            metrics["backtest_id"] = bid
        metrics["strategy_display_name"] = entry.get("strategy_display_name") or metrics.get(
            "strategy_display_name", ""
        )
        metrics["backtest_start"] = jq_start
        metrics["backtest_end"] = jq_end

        from auto_quant.joinquant.report_metrics import reconcile_metrics_dates

        metrics = reconcile_metrics_dates(metrics, jq_start, jq_end)

        fp = _resolve_file(project, job["file"])
        payload = _ablation_payload(project, job, fp, jq_start, jq_end, metrics, dry_run=False)
        record_run(project, "jq_ablation", payload, filename_slug=vid)
        save_variant_links(
            project.path,
            vid,
            strategy_display_name=metrics.get("strategy_display_name", ""),
            edit_url=edit_url,
            report_url=report_url,
            backtest_id=bid,
            metrics=metrics,
        )
        print(
            f"[ok] {vid}: 年化={metrics.get('annual_return')} "
            f"夏普={metrics.get('sharpe')} 回撤={metrics.get('max_drawdown')}"
        )
        updated += 1

    return updated


def write_ablation_report(project_id: str) -> Path:
    """Compare all jq_ablation runs vs baseline; write ablation_report.md + merge hints."""
    project = load_project(project_id)
    cfg = load_ablation(project_id)

    runs: list[tuple[Path, dict[str, Any]]] = []
    for p in sorted(project.runs_dir.glob("*_jq_ablation*.json"), key=lambda x: x.stat().st_mtime):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("type") == "joinquant_ablation" or "ablation_variant_id" in data:
            runs.append((p, data))

    baseline_id = str((cfg.get("baseline") or {}).get("id", "baseline"))

    by_id: dict[str, dict[str, Any]] = {}
    for _, data in runs:
        vid = data.get("ablation_variant_id") or data.get("variant_id")
        if not vid:
            continue
        # keep latest per variant
        by_id[vid] = data

    def fnum(x: Any) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    # 合并 jq_links.yaml（含 report_url / 已抓取 metrics）
    links_path = project.path / "jq_links.yaml"
    links_map: dict[str, Any] = {}
    if links_path.exists():
        import yaml

        with open(links_path, encoding="utf-8") as f:
            ld = yaml.safe_load(f) or {}
        links_map = ld.get("variants") or {}

    for vid in list(by_id.keys()):
        merged = _merge_metrics_for_report(by_id[vid].get("metrics") or {}, links_map.get(vid))
        by_id[vid] = {**by_id[vid], "metrics": merged}

    # jq_links 已登记、尚无 runs 的变体（如手工补链的 strategy_B/H）
    for v in cfg.get("variants") or []:
        vid = str(v["id"])
        if vid in by_id:
            continue
        le = links_map.get(vid)
        if not le or not (le.get("report_url") or (le.get("metrics") or {}).get("report_url")):
            continue
        merged = _merge_metrics_for_report(le.get("metrics") or {}, le)
        by_id[vid] = {
            "ablation_variant_id": vid,
            "title": str(v.get("title", vid)),
            "idea_ref": str(v.get("idea_ref", "")),
            "metrics": merged,
        }

    base = by_id.get(baseline_id, {})
    base_m = base.get("metrics") or {}

    lines = [
        f"# 消融实验对比 · {project.meta.get('name', project.id)}",
        "",
        f"- 基线 variant id: `{baseline_id}`",
        f"- 数据来源: `runs/*_jq_ablation*.json`（指标来自 `report_analysis_tools` / `jq_metrics_export.json`）",
        f"- 回测区间: {resolve_jq_dates(project, cfg)[0]} ~ {resolve_jq_dates(project, cfg)[1]}",
        "",
        "## 核心指标表",
        "",
        "| 变体 | 年化 | 夏普率 | 最大回撤 | 日胜率 | 总收益 | 盈亏比 | 年化换手(估) | 报告 | 合并建议 |",
        "|------|------|------|----------|--------|--------|--------|--------------|------|----------|",
    ]

    b_sh = fnum(base_m.get("sharpe"))
    b_dd = fnum(base_m.get("max_drawdown"))
    b_ar = fnum(base_m.get("annual_return"))

    for vid, data in sorted(by_id.items(), key=lambda x: (x[0] != baseline_id, x[0])):
        m = data.get("metrics") or {}
        ar, sh, dd, wr = m.get("annual_return"), m.get("sharpe"), m.get("max_drawdown"), m.get("win_rate")
        hint = _merge_hint(
            vid == baseline_id,
            fnum(sh),
            fnum(dd),
            fnum(ar),
            b_sh,
            b_dd,
            b_ar,
        )
        link = m.get("report_url") or links_map.get(vid, {}).get("report_url") or ""
        link_cell = f"[报告]({link})" if link and link.startswith("http") else "—"
        lines.append(
            f"| `{vid}` | {_fmt_pct(ar)} | {_fmt(sh)} | {_fmt_pct(dd)} | {_fmt_pct(wr)} | "
            f"{_fmt_pct(m.get('total_return'))} | {_fmt(m.get('pnl_ratio'))} | "
            f"{_fmt_money(m.get('turnover_annual'))} | {link_cell} | {hint} |"
        )

    lines.extend(["", "## 自动分析（相对基线）", ""])
    lines.extend(_build_ablation_analysis(by_id, baseline_id, base_m, cfg))

    b7_ext_ids = [vid for vid in sorted(by_id) if vid.startswith("idea-B7-")]
    if b7_ext_ids and baseline_id == "idea-B7":
        ref_tr = fnum((base_m or {}).get("total_return"))
        ref_dd = fnum((base_m or {}).get("max_drawdown"))
        lines.extend(
            [
                "",
                "## §10/§11 idea-B7 扩展专表（对照母版 idea-B7）",
                "",
                f"- 母版 idea-B7：总收益 {_fmt_pct(ref_tr)}，最大回撤 {_fmt_pct(ref_dd)}",
                "",
                "| 变体 | 总收益 | 最大回撤 | 夏普率 | vs B7 总收益 | vs B7 回撤 | 报告 |",
                "|------|--------|----------|--------|--------------|------------|------|",
            ]
        )
        for vid in b7_ext_ids:
            m = (by_id[vid].get("metrics") or {})
            tr, dd, sh = fnum(m.get("total_return")), fnum(m.get("max_drawdown")), fnum(m.get("sharpe"))
            d_tr = f"{(tr - ref_tr) * 100:+.1f}pp" if tr is not None and ref_tr is not None else "—"
            d_dd = f"{(dd - ref_dd) * 100:+.1f}pp" if dd is not None and ref_dd is not None else "—"
            link = m.get("report_url") or links_map.get(vid, {}).get("report_url") or ""
            link_cell = f"[报告]({link})" if link and link.startswith("http") else "—"
            lines.append(
                f"| `{vid}` | {_fmt_pct(tr)} | {_fmt_pct(dd)} | {_fmt(sh)} | {d_tr} | {d_dd} | {link_cell} |"
            )
        lines.append("")

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 指标由 `report_analysis_tools.get_backtest` 同款逻辑计算（收益统计 + 交易质量 + 持仓行为）。",
            "- 本地无研究环境时：在聚宽研究运行 `scripts/jq_research_export_metrics.py` 生成 `jq_metrics_export.json`，再 `ablation-refresh-metrics`。",
            "- **建议合并**：夏普不低于基线且回撤未明显变差（启发式，需人工确认）。",
            "- **数据不足**：缺少年化/夏普/回撤，请先 `ablation-refresh-metrics`。",
            "",
            "## 与 idea.md 对照",
            "",
            "将上表与 `idea.md` 各节预期对照；关注 idea-1 换手、idea-2 走弱期、idea-3 回撤过滤、idea-4 空仓频率、idea-5 与基线差异。",
            "",
        ]
    )

    out = project.path / "ablation_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    _write_ablation_summary(project, cfg, by_id, baseline_id, base_m)
    print(f"已写入: {out}")
    return out


def _write_ablation_summary(
    project: Project,
    cfg: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    baseline_id: str,
    base_m: dict[str, Any],
) -> None:
    """Chinese narrative summary for human / Cursor review."""
    lines = [
        f"# 消融实验总结 · {project.meta.get('name', project.id)}",
        "",
        f"回测区间（来自 meta.yaml / ablation.yaml）："
        f" {resolve_jq_dates(project, cfg)[0]} ~ {resolve_jq_dates(project, cfg)[1]}",
        "",
        "## 各变体相对基线",
        "",
    ]
    b_sh = base_m.get("sharpe")
    b_dd = base_m.get("max_drawdown")
    b_ar = base_m.get("annual_return")

    for vid, data in sorted(by_id.items(), key=lambda x: (x[0] != baseline_id, x[0])):
        m = data.get("metrics") or {}
        title = data.get("title", vid)
        idea_ref = data.get("idea_ref", "")
        lines.append(f"### {vid} — {title}")
        if idea_ref:
            lines.append(f"- 对应: {idea_ref}")
        lines.append(
            f"- 指标: 年化={_fmt_pct(m.get('annual_return'))} 夏普={_fmt(m.get('sharpe'))} "
            f"回撤={_fmt_pct(m.get('max_drawdown'))} 胜率={_fmt_pct(m.get('win_rate'))} "
            f"盈亏比={_fmt(m.get('pnl_ratio'))} 换手={_fmt_money(m.get('turnover_annual'))}"
        )
        if vid == baseline_id:
            lines.append("- **角色**: 基线，其它变体与之对比。")
            lines.append("")
            continue
        notes = []
        try:
            sh, dd, ar = float(m.get("sharpe") or 0), float(m.get("max_drawdown") or 0), float(m.get("annual_return") or 0)
            if b_sh is not None and m.get("sharpe") is not None and sh >= float(b_sh):
                notes.append("夏普不低于基线")
            if b_dd is not None and m.get("max_drawdown") is not None and dd >= float(b_dd) - 0.02:
                notes.append("回撤未明显恶化")
            if b_ar is not None and m.get("annual_return") is not None and ar > float(b_ar):
                notes.append("年化高于基线")
        except (TypeError, ValueError):
            pass
        if not notes:
            notes.append("指标缺失或弱于基线，建议对照聚宽网页日志（换手、走弱期触发等）")
        lines.append(f"- **观察**: {'；'.join(notes)}")
        lines.append("- **是否合并**: 见 `ablation_report.md` 合并建议列 + 对照 `idea.md` 预期。")
        lines.append("")

    lines.extend(
        [
            "## 建议合并顺序（需人工确认）",
            "",
            "1. 先合并 **回撤改善明显** 且与 `idea.md` 机制描述一致的模块（如 idea-3 过滤、idea-2 走弱期）。",
            "2. **idea-1 加速补分** 若夏普略升但换手大增，可降 `accel_weight` 再测一轮。",
            "3. **idea-4** 放宽 10 日过滤后若空仓/511880 频率下降，再决定是否并入主策略。",
            "4. **idea-5** 与基线应对齐；若差异大说明其它逻辑影响了防御路径。",
            "",
            "## Cursor 分析",
            "",
            "在聊天中 @ 本文件、`idea.md`、`ablation_report.md` 及各 `variants/idea-N.py` 差异，",
            "要求按 idea.md §验证建议 解读走弱期占比与换手。",
            "",
            "免责声明：回测不代表未来收益。",
        ]
    )
    summary_path = project.path / "ablation_summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入: {summary_path}")


def _metrics_incomplete(m: dict[str, Any]) -> bool:
    """runs 占位：列表页 URL、backtest_id=list、仅 max_drawdown=1.0 等。"""
    if not m:
        return True
    if m.get("raw_note"):
        return True
    if m.get("backtest_id") == "list":
        return True
    url = str(m.get("report_url") or "")
    if "backtest/list" in url:
        return True
    if m.get("annual_return") is None and m.get("total_return") is None:
        return True
    if m.get("max_drawdown") == 1.0 and m.get("sharpe") is None:
        return True
    try:
        tr = float(m.get("total_return"))
        if 0 < tr < 0.5:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _merge_metrics_for_report(
    run_metrics: dict[str, Any],
    links_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """优先 detail 报告链接；指标缺失时回退 jq_links.yaml 中已抓取的 metrics。"""
    m = dict(run_metrics or {})
    le = links_entry or {}
    link_m = dict(le.get("metrics") or {})

    detail_url = ""
    for url in (le.get("report_url"), link_m.get("report_url"), m.get("report_url")):
        if url and "backtest/detail" in str(url):
            detail_url = str(url)
            break
    if detail_url:
        m["report_url"] = detail_url
        bid = le.get("backtest_id") or link_m.get("backtest_id")
        if bid and bid != "list":
            m["backtest_id"] = bid

    if _metrics_incomplete(m) and link_m:
        for key, val in link_m.items():
            if val is None:
                continue
            if m.get(key) is None:
                m[key] = val
                continue
            if key == "max_drawdown" and m.get(key) == 1.0:
                m[key] = val
        if detail_url:
            m["report_url"] = detail_url
        m.pop("raw_note", None)

    return m


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def _fmt_pct(x: Any) -> str:
    """收益/回撤/胜率：存小数（1.07=107%），展示统一 ×100。"""
    if x is None:
        return "—"
    try:
        v = float(x)
        return f"{v * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_money(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _build_ablation_analysis(
    by_id: dict[str, dict[str, Any]],
    baseline_id: str,
    base_m: dict[str, Any],
    cfg: dict[str, Any],
) -> list[str]:
    """Narrative bullets per variant vs baseline."""
    lines: list[str] = []
    b_ar = base_m.get("annual_return")
    b_sh = base_m.get("sharpe")
    b_dd = base_m.get("max_drawdown")
    b_turn = base_m.get("turnover_annual")

    variant_titles = {str(v["id"]): str(v.get("title", v["id"])) for v in cfg.get("variants") or []}
    variant_titles[baseline_id] = str((cfg.get("baseline") or {}).get("title", "基线"))

    for vid, data in sorted(by_id.items(), key=lambda x: (x[0] != baseline_id, x[0])):
        m = data.get("metrics") or {}
        title = variant_titles.get(vid, data.get("title", vid))
        if vid == baseline_id:
            lines.append(f"- **{vid}**（{title}）：基线。")
            continue
        if m.get("annual_return") is None and m.get("sharpe") is None:
            lines.append(f"- **{vid}**（{title}）：指标缺失，请 `ablation-refresh-metrics`。")
            continue

        parts: list[str] = []
        try:
            ar, sh, dd = float(m.get("annual_return") or 0), float(m.get("sharpe") or 0), float(
                m.get("max_drawdown") or 0
            )
            if b_sh is not None and m.get("sharpe") is not None:
                d = sh - float(b_sh)
                parts.append(f"夏普{'↑' if d >= 0 else '↓'}{abs(d):.2f}")
            if b_dd is not None and m.get("max_drawdown") is not None:
                d = (dd - float(b_dd)) * 100
                parts.append(f"回撤{'改善' if d >= 0 else '恶化'}({d:+.1f}pp)")
            if b_ar is not None and m.get("annual_return") is not None and float(b_ar) != 0:
                rel = (ar / float(b_ar) - 1) * 100
                parts.append(f"年化{'↑' if rel >= 0 else '↓'}{abs(rel):.1f}%")
            if m.get("turnover_annual") and b_turn:
                tr = float(m["turnover_annual"]) / float(b_turn) - 1
                if abs(tr) > 0.15:
                    parts.append(f"换手{'+' if tr > 0 else ''}{tr:.0%} vs 基线")
            if m.get("trade_win_rate") is not None:
                parts.append(f"交易胜率 {float(m['trade_win_rate'])*100:.1f}%")
            if m.get("avg_hold_days") is not None:
                parts.append(f"均持 {float(m['avg_hold_days']):.1f} 天")
        except (TypeError, ValueError):
            parts.append("数值解析异常")
        lines.append(f"- **{vid}**（{title}）：{'；'.join(parts) or '见指标表'}。")

    return lines


def _merge_hint(
    is_baseline: bool,
    sh: float | None,
    dd: float | None,
    ar: float | None,
    b_sh: float | None,
    b_dd: float | None,
    b_ar: float | None,
) -> str:
    if is_baseline:
        return "—（基线）"
    if sh is None and dd is None and ar is None:
        return "数据不足"
    # 启发式：夏普略优且回撤不明显变差
    ok_sh = b_sh is None or sh is None or sh >= b_sh - 0.03
    ok_dd = b_dd is None or dd is None or (dd >= b_dd - 0.03)
    good = ok_sh and ok_dd and (sh is not None and b_sh is not None and sh >= b_sh)
    if good:
        return "可考虑合并"
    if ok_sh and ok_dd:
        return "需人工复核"
    return "暂缓合并"
