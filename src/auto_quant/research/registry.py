"""File-based strategy research registry under research/strategies/."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from auto_quant.config import load_settings, project_root

RESEARCH_ROOT = project_root() / "research" / "strategies"
TEMPLATE_ROOT = project_root() / "research" / "templates"


@dataclass
class Project:
    id: str
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def idea_path(self) -> Path:
        return self.path / "idea.md"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.yaml"

    @property
    def runs_dir(self) -> Path:
        return self.path / "runs"

    @property
    def strategy_path(self) -> Path | None:
        p = self.path / "strategy.py"
        return p if p.exists() else None

    def jq_code_path(self) -> Path | None:
        rel = self.meta.get("jq_code_path") or ""
        if not rel:
            sp = self.strategy_path
            return sp
        p = project_root() / rel
        return p if p.exists() else None


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name.strip())
    return s.strip("-")[:40] or "strategy"


def new_id(name: str) -> str:
    return f"STR-{datetime.now().strftime('%Y%m%d')}-{_slug(name)}"


def create_project(
    name: str,
    idea: str,
    *,
    platform: str = "joinquant",
    tags: list[str] | None = None,
    jq_code_path: str = "",
) -> Project:
    pid = new_id(name)
    path = RESEARCH_ROOT / pid
    if path.exists():
        raise FileExistsError(f"项目已存在: {pid}")

    path.mkdir(parents=True)
    (path / "runs").mkdir()

    shutil.copy(TEMPLATE_ROOT / "idea.md", path / "idea.md")
    if idea.strip():
        (path / "idea.md").write_text(idea.strip() + "\n", encoding="utf-8")

    settings = load_settings()
    bt = settings["backtest"]
    jq = settings["joinquant"]
    now = datetime.now().isoformat(timespec="seconds")

    meta = {
        "id": pid,
        "name": name,
        "status": "draft",
        "platform": platform,
        "tags": tags or [],
        "created_at": now,
        "updated_at": now,
        "jq_code_path": jq_code_path,
        "local_params": {},
        "backtest": {
            "local": {
                "start_date": bt["start_date"],
                "end_date": bt["end_date"],
                "max_symbols": settings["universe"]["max_symbols"],
            },
            "joinquant": {
                "start_date": jq.get("jq_backtest_start", "2024-01-01"),
                "end_date": jq.get("jq_backtest_end", "2024-12-31"),
            },
        },
        "latest_run": None,
        "notes": "",
    }
    save_meta(path, meta)
    return Project(id=pid, path=path, meta=meta)


def save_meta(path: Path, meta: dict[str, Any]) -> None:
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path / "meta.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)


def load_project(project_id: str) -> Project:
    path = RESEARCH_ROOT / project_id
    if not path.is_dir():
        # fuzzy match by suffix
        matches = [p for p in RESEARCH_ROOT.iterdir() if p.is_dir() and project_id in p.name]
        if len(matches) == 1:
            path = matches[0]
        else:
            raise FileNotFoundError(f"未找到项目: {project_id}")

    with open(path / "meta.yaml", encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    return Project(id=meta.get("id", path.name), path=path, meta=meta)


def list_projects() -> list[Project]:
    if not RESEARCH_ROOT.exists():
        return []
    out: list[Project] = []
    for p in sorted(RESEARCH_ROOT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and (p / "meta.yaml").exists():
            out.append(load_project(p.name))
    return out


def attach_strategy_file(project: Project, source: str | Path) -> Path:
    """Copy external .py into project as strategy.py and update meta."""
    src = Path(source).resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    dest = project.path / "strategy.py"
    shutil.copy2(src, dest)
    rel = dest.relative_to(project_root()).as_posix()
    project.meta["jq_code_path"] = rel
    project.meta["status"] = "implemented"
    save_meta(project.path, project.meta)
    return dest


def record_run(
    project: Project,
    run_type: str,
    payload: dict[str, Any],
    *,
    filename_slug: str | None = None,
) -> Path:
    """Save one backtest run under runs/. Optional filename_slug for ablation variants."""
    project.runs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = ""
    if filename_slug:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", filename_slug.strip())[:40]
        if safe:
            slug = "_" + safe
    fname = f"{ts}_{run_type}{slug}.json"
    path = project.runs_dir / fname
    import json

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    project.meta["latest_run"] = fname
    project.meta["status"] = "backtested"
    save_meta(project.path, project.meta)
    return path
