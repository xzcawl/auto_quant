"""Save/load JoinQuant backtest data packs for offline report analysis."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def default_pack_dir(project_dir: Path) -> Path:
    return project_dir / "analysis" / "packs"


def pack_path(project_dir: Path, variant_id: str) -> Path:
    return default_pack_dir(project_dir) / f"{variant_id}.json"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_pack(path: Path, pack: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return path


def load_pack(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_pack_for_variant(project_dir: Path, variant_id: str) -> dict[str, Any] | None:
    return load_pack(pack_path(project_dir, variant_id))
