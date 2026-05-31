"""Strategy research pipeline: idea -> implement -> backtest -> AI analysis."""

from auto_quant.research.ablation import (
    init_ablation,
    load_ablation,
    queue_ablation,
    run_ablation,
    write_ablation_report,
)
from auto_quant.research.ai_brief import generate_ai_brief
from auto_quant.research.pipeline import (
    create_project,
    run_backtest_for_project,
    set_status,
    write_report,
)
from auto_quant.research.registry import attach_strategy_file, list_projects, load_project

__all__ = [
    "attach_strategy_file",
    "create_project",
    "generate_ai_brief",
    "list_projects",
    "load_project",
    "run_backtest_for_project",
    "set_status",
    "write_report",
    "init_ablation",
    "load_ablation",
    "queue_ablation",
    "run_ablation",
    "write_ablation_report",
]
