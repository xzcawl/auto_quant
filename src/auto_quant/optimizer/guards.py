"""Rule-based guards for overfitting and executability."""

from __future__ import annotations

from auto_quant.config import load_settings
from auto_quant.engine.metrics import Metrics


def apply_guards(metrics: Metrics) -> list[str]:
    settings = load_settings()
    opt = settings.get("optimizer", {})
    warnings: list[str] = []

    max_spd = opt.get("max_signals_per_day", 5)
    if metrics.signals_per_day > max_spd:
        warnings.append(f"日均信号数 {metrics.signals_per_day:.2f} 超过阈值 {max_spd}，可能不可执行")

    max_loss = opt.get("max_single_loss_pct", -0.15)
    if metrics.max_single_loss < max_loss:
        warnings.append(
            f"最大单笔亏损 {metrics.max_single_loss:.2%} 超过风控阈值 {max_loss:.2%}"
        )

    if metrics.trade_count < 10:
        warnings.append("交易笔数过少，统计意义不足")

    if metrics.win_rate < 0.4 and metrics.trade_count > 20:
        warnings.append("胜率偏低，需人工复核逻辑")

    metrics.warnings.extend(warnings)
    return warnings


def is_acceptable(metrics: Metrics) -> bool:
    """Hard reject only extreme cases."""
    settings = load_settings()
    opt = settings.get("optimizer", {})
    if metrics.signals_per_day > opt.get("max_signals_per_day", 5) * 2:
        return False
    if metrics.max_single_loss < opt.get("max_single_loss_pct", -0.15) * 1.5:
        return False
    return metrics.trade_count >= 1
