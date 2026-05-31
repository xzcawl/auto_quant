"""Generate Cursor-ready analysis brief from project + backtest data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_quant.research.registry import Project


ANALYSIS_QUESTIONS = """
请作为量化研究员，基于下方材料分析该策略，并按节输出：

1. **逻辑有效性**：核心假设是什么？在 A 股/ETF 市场是否合理？
2. **回测可信度**：是否存在未来函数、偷价、手续费遗漏、生存偏差？
3. **风险结构**：最大回撤、尾部风险、持仓集中度、止损是否有效？
4. **可执行性**：容量、滑点、换手率、涨跌停与流动性约束？
5. **优化建议**：参数、过滤条件、持仓规则的具体改进（可附伪代码）。
6. **结论**：是否值得进入下一轮回测 / 模拟盘？风险评级（低/中/高）。

引用项目内策略代码时请指出文件名与行号范围（若可推断）。
"""


def generate_ai_brief(project: Project, *, extra_notes: str = "") -> Path:
    idea = project.idea_path.read_text(encoding="utf-8") if project.idea_path.exists() else ""
    meta = project.meta
    latest = _load_latest_run(project)

    jq_path = project.jq_code_path()
    code_hint = ""
    if jq_path and jq_path.exists():
        lines = jq_path.read_text(encoding="utf-8").splitlines()
        preview = "\n".join(lines[:80])
        if len(lines) > 80:
            preview += f"\n... （共 {len(lines)} 行，完整代码见 `{jq_path.relative_to(project.path.parent.parent)}`）"
        code_hint = preview

    metrics_block = _format_metrics(latest)

    brief = f"""# AI 分析请求 · {meta.get('name', project.id)}

> **给 Cursor**：在聊天中 `@` 本文件，并视需要 `@` 策略源码、`joinquant-skill` 或 `tushare-data` skill。
> 项目 ID：`{project.id}` | 平台：`{meta.get('platform')}` | 状态：`{meta.get('status')}`

---

## 策略思想（用户提供）

{idea.strip() or '（未填写 idea.md）'}

---

## 回测结果摘要

{metrics_block}

---

## 策略代码预览

```
{code_hint or '（尚未挂载 strategy.py / jq_code_path）'}
```

---

## 用户补充说明

{extra_notes.strip() or '（无）'}

---

## 请 AI 回答的问题

{ANALYSIS_QUESTIONS.strip()}

---

## 工作流下一步（人工）

- [ ] 根据 AI 建议修改 `idea.md` 或 `strategy.py`
- [ ] 重新执行：`python scripts/research.py run {project.id}`
- [ ] 满意后标记 reviewed：见 `research.py status`
"""
    out = project.path / "analysis_request.md"
    out.write_text(brief, encoding="utf-8")
    return out


def _load_latest_run(project: Project) -> dict[str, Any] | None:
    import json

    fname = project.meta.get("latest_run")
    if not fname:
        runs = sorted(project.runs_dir.glob("*.json"), reverse=True)
        if not runs:
            return None
        fname = runs[0].name
    path = project.runs_dir / fname
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _format_metrics(run: dict[str, Any] | None) -> str:
    if not run:
        return "（尚无回测记录，请先 `research run <id>`）"

    lines = [
        f"- **运行类型**: {run.get('type', 'unknown')}",
        f"- **时间**: {run.get('timestamp', '-')}",
    ]
    m = run.get("metrics") or run
    for key in (
        "trade_count",
        "win_rate",
        "avg_trade_return",
        "sharpe",
        "max_drawdown",
        "annual_return",
        "signals_per_day",
        "task_id",
        "report_path",
    ):
        if key in m and m[key] is not None:
            val = m[key]
            if isinstance(val, float) and abs(val) < 10:
                val = f"{val:.4f}"
            lines.append(f"- **{key}**: {val}")

    warnings = run.get("warnings") or (m.get("warnings") if isinstance(m, dict) else None)
    if warnings:
        lines.append("- **warnings**:")
        for w in warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)
