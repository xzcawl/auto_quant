"""Persist JoinQuant strategy/report URLs per research project variant."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from auto_quant.config import project_root


def links_path(project_dir: Path) -> Path:
    return project_dir / "jq_links.yaml"


def load_links(project_dir: Path) -> dict[str, Any]:
    p = links_path(project_dir)
    if not p.exists():
        return {"variants": {}}
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {"variants": {}}


def save_variant_links(
    project_dir: Path,
    variant_id: str,
    *,
    strategy_display_name: str,
    edit_url: str = "",
    report_url: str = "",
    backtest_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    task_id: int | None = None,
) -> Path:
    data = load_links(project_dir)
    variants = data.setdefault("variants", {})
    entry = variants.setdefault(variant_id, {})
    entry["strategy_display_name"] = strategy_display_name
    if edit_url:
        entry["edit_url"] = edit_url
    if report_url:
        entry["report_url"] = report_url
    if backtest_id:
        entry["backtest_id"] = backtest_id
    if task_id is not None:
        entry["task_id"] = task_id
    if metrics:
        entry["metrics"] = metrics
    from datetime import datetime

    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")

    p = links_path(project_dir)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return p
