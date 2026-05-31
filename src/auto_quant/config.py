"""Load YAML settings from project root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return ROOT


def load_settings(path: Path | None = None) -> dict[str, Any]:
    p = path or ROOT / "config" / "settings.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_optimize_space(name: str = "macd") -> dict[str, Any]:
    p = ROOT / "config" / "optimize" / f"{name}.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)
