"""Generate HTML backtest report."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from auto_quant.engine.metrics import Metrics
from auto_quant.strategies.trade_simulator import Trade

RENDER_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>回测报告 {{ run_id }}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #f8f9fa; }
    h1 { color: #1a365d; }
    table { border-collapse: collapse; width: 100%; background: #fff; margin: 1rem 0; }
    th, td { border: 1px solid #ddd; padding: 8px; }
    th { background: #2c5282; color: #fff; }
    .warn { background: #fff3cd; padding: 1rem; border-left: 4px solid #ffc107; }
    .metrics { display: flex; flex-wrap: wrap; gap: 1rem; }
    .metric { padding: 1rem; background: #fff; border-radius: 8px; min-width: 140px; }
    .metric b { font-size: 1.4rem; color: #c05621; display: block; }
    footer { margin-top: 2rem; color: #666; font-size: 0.85rem; }
  </style>
</head>
<body>
  <h1>MACD 底背离策略回测报告</h1>
  <p>区间：{{ start }} ~ {{ end }} | Run: {{ run_id }}</p>
  {% if metrics.warnings %}
  <motion class="warn"><strong>警告</strong><ul>{% for w in metrics.warnings %}<li>{{ w }}</li>{% endfor %}</ul></motion>
  {% endif %}
  <motion class="metrics">
    <motion class="metric">交易笔数<b>{{ metrics.trade_count }}</b></motion>
    <motion class="metric">胜率<b>{{ "%.2f"|format(metrics.win_rate * 100) }}%</b></motion>
    <motion class="metric">平均每笔<b>{{ "%.2f"|format(metrics.avg_trade_return * 100) }}%</b></motion>
    <motion class="metric">最大亏损<b>{{ "%.2f"|format(metrics.max_single_loss * 100) }}%</b></motion>
    <motion class="metric">夏普<b>{{ "%.2f"|format(metrics.sharpe) }}</b></motion>
    <motion class="metric">最大回撤<b>{{ "%.2f"|format(metrics.max_drawdown * 100) }}%</b></motion>
    <motion class="metric">日均信号<b>{{ "%.2f"|format(metrics.signals_per_day) }}</b></motion>
  </motion>
  <h2>参数</h2>
  <table><tr><th>键</th><th>值</th></tr>
  {% for k, v in params.items() %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}
  </table>
  <h2>分年收益</h2>
  <table><tr><th>年份</th><th>收益</th></tr>
  {% for y, r in metrics.yearly_returns.items() %}
  <tr><td>{{ y }}</td><td>{{ "%.2f"|format(r * 100) }}%</td></tr>
  {% endfor %}
  </table>
  <h2>最近交易</h2>
  <table>
    <tr><th>标的</th><th>入场</th><th>出场</th><th>净收益</th><th>原因</th></tr>
    {% for t in trades[:50] %}
    <tr><td>{{ t.symbol }}</td><td>{{ t.entry_time }}</td><td>{{ t.exit_time }}</td>
        <td>{{ "%.2f"|format(t.pnl_pct * 100) }}%</td><td>{{ t.exit_reason }}</td></tr>
    {% endfor %}
  </table>
  <footer>免责声明：回测结果不代表未来收益，仅供研究，请勿据此实盘交易。</footer>
</body>
</html>""".replace("<motion", "<div").replace("</motion>", "</div>"))


def render_report(
    *,
    run_id: str,
    params: dict,
    metrics: Metrics,
    trades: list[Trade],
    start: str,
    end: str,
    output_dir: Path,
) -> Path:
    html = RENDER_TEMPLATE.render(
        run_id=run_id,
        params=params,
        metrics=metrics,
        trades=trades,
        start=start,
        end=end,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_id}.html"
    path.write_text(html, encoding="utf-8")
    return path
