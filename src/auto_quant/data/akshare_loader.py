"""AKShare data download, cache, and basic cleaning."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

import akshare as ak
import pandas as pd

from auto_quant.config import load_settings, project_root

Freq = Literal["daily", "30min"]


def _cache_path(symbol: str, freq: Freq, start: str, end: str) -> Path:
    settings = load_settings()
    cache_dir = project_root() / settings["data"]["cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace(".", "_")
    return cache_dir / f"{safe}_{freq}_{start}_{end}.parquet"


def _normalize_df(df: pd.DataFrame, freq: Freq) -> pd.DataFrame:
    col_map = {
        "日期": "datetime",
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "datetime" not in df.columns:
        raise ValueError(f"Missing datetime column: {df.columns.tolist()}")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"])
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    # 停牌/无成交：volume=0 的 bar 剔除
    if "volume" in df.columns:
        df = df[df["volume"].fillna(0) > 0]
    df = df.set_index("datetime")
    if freq == "daily":
        df = df[["open", "high", "low", "close", "volume"]].copy()
    else:
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        df = df[cols].copy()
    return df


def _resample_daily_to_30min(daily: pd.DataFrame) -> pd.DataFrame:
    """Fallback when minute history unavailable: 4 synthetic bars per day."""
    rows = []
    for ts, row in daily.iterrows():
        base = pd.Timestamp(ts).normalize()
        for hour in (10, 11, 13, 14):
            rows.append(
                {
                    "datetime": base + pd.Timedelta(hours=hour, minutes=30),
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] / 4 if row["volume"] else 0,
                }
            )
    df = pd.DataFrame(rows).set_index("datetime")
    return df.sort_index()


def _symbol_to_ak(symbol: str) -> tuple[str, str]:
    """000001.XSHE -> (000001, sz)"""
    code, exch = symbol.split(".")
    prefix = "sh" if exch.upper() in ("XSHG", "SH") else "sz"
    return code, prefix


def fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    code, _ = _symbol_to_ak(symbol)
    start_s = start.replace("-", "")
    end_s = end.replace("-", "")
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_s,
        end_date=end_s,
        adjust="qfq",
    )
    return _normalize_df(df, "daily")


def fetch_30min(symbol: str, start: str, end: str) -> pd.DataFrame:
    code, _ = _symbol_to_ak(symbol)
    # 东财分钟线仅提供近期数据（约1~3个月），无法覆盖多年回测
    df = ak.stock_zh_a_hist_min_em(symbol=code, period="30", adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame()
    out = _normalize_df(df, "30min")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)
    clipped = out[(out.index >= start_ts) & (out.index < end_ts)]
    # 若请求区间无数据，使用 API 返回的全部近期数据
    return clipped if len(clipped) >= 50 else out


def load_bars(
    symbol: str,
    freq: Freq,
    start: str,
    end: str,
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    path = _cache_path(symbol, freq, start, end)
    if use_cache and path.exists():
        return pd.read_parquet(path)

    if freq == "daily":
        df = fetch_daily(symbol, start, end)
    else:
        df = fetch_30min(symbol, start, end)

    if not df.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    return df


def get_universe(max_symbols: int | None = None) -> list[str]:
    settings = load_settings()
    mode = settings["universe"]["mode"]
    limit = max_symbols or settings["universe"]["max_symbols"]

    if mode == "custom":
        symbols = settings["universe"].get("custom_symbols") or []
        return symbols[:limit]

    # 沪深300成分
    try:
        df = ak.index_stock_cons(symbol="000300")
        code_col = "品种代码" if "品种代码" in df.columns else df.columns[1]
        codes = df[code_col].astype(str).str.zfill(6).tolist()
    except Exception:
        codes = ["000001", "600000", "600519", "000858", "601318"]

    if not codes:
        codes = ["000001", "600000", "600519", "000858", "601318"]

    symbols: list[str] = []
    for code in codes[: limit * 2]:
        if code.startswith(("6", "9")):
            symbols.append(f"{code}.XSHG")
        else:
            symbols.append(f"{code}.XSHE")
        if len(symbols) >= limit:
            break
    return symbols[:limit]


def load_universe_data(
    symbols: list[str],
    start: str,
    end: str,
    *,
    delay_sec: float = 0.3,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Return {symbol: {daily: df, min30: df}}."""
    result: dict[str, dict[str, pd.DataFrame]] = {}
    for i, sym in enumerate(symbols):
        try:
            daily = load_bars(sym, "daily", start, end)
            min30 = load_bars(sym, "30min", start, end)
            if daily.empty:
                continue
            if min30.empty or len(min30) < 50:
                # AKShare 免费 30min 历史有限：用日线重采样为 30min 近似（仅研究用）
                min30 = _resample_daily_to_30min(daily)
            if len(min30) < 50:
                continue
            result[sym] = {"daily": daily, "min30": min30}
        except Exception as e:
            print(f"[warn] skip {sym}: {e}")
        if delay_sec and i < len(symbols) - 1:
            time.sleep(delay_sec)
    return result
