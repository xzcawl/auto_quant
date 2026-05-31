# -*- coding: utf-8 -*-
"""
Name：聚宽策略回测结果分析工具 
Author: 策略手艺人
Date  : 2026/04/07
"""
from datetime import datetime
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

try:
    from IPython.display import display, HTML
except ImportError:
    class HTML:  # noqa: N801
        def __init__(self, data):
            self.data = data

    def display(obj):
        print(obj)

# ----------------- 配置区 -----------------
# 回测ID：从聚宽回测页面 URL 中复制，格式如 backtestId=xxxxxxxx
# https://www.joinquant.com/algorithm/backtest/detail?backtestId=a8747d41c7f7bbe27549d29e61b6e052
BACKTEST_ID = '86f92717885e88ab7e40dfa224f90b79'

# -------- 统计模块开关（True=显示，False=隐藏）--------
SHOW_SECTIONS = {
    1:  True,   # 【收益统计】      总收益/年化/夏普/最大回撤/日胜率/alpha/beta，最核心的一张总表
    2:  True,   # 【年度收益统计】  逐年拆解策略 vs 基准收益、回撤、波动率、夏普，快速看各年表现
    3:  True,   # 【最大回撤概览】  TOP N 次回撤的跌落日期、谷底、解套日期及解套天数
    4:  True,   # 【月度盈亏统计】  热力表格，红绿色块直观展示每月盈亏率，快速定位高/低点
    5:  True,   # 【个股盈亏 Top N】盈利/亏损各前N只股票，用于发现"提款机"和"绊脚石"
    6:  True,   # 【交易质量分析】  胜率、平均盈亏、盈亏比、期望值、卡尔马，衡量策略"手艺"
    7:  True,   # 【持仓行为分析】  平均持仓天数、短/中/长线分布、年化换手额，判断风格定位
    8:  True,   # 【仓位利用率分析】空仓/满仓天数比例，资金使用效率评估（依赖 get_balances）
    9:  True,   # 【官方风险指标】  直接读取聚宽内置的 alpha/beta/sortino/信息比率，与回测页对齐
    10: True,   # 【交易时间分布】  按星期/月份统计胜率+平均盈亏，发现时间规律（如某月容易亏）
}

REF_RATE   = 0.04 # 无风险利率，用于计算夏普比率（默认4%，可根据当前国债收益率调整）
TOP_N      = 10   # 第3节：显示的最大回撤次数（如10=展示最惨的前10次回撤）
STOCK_TOP_N = 20  # 第5节：个股盈亏排行显示的股票数量（盈利/亏损各取 N 只）

# -------- HTML 输出收集（本地报告）--------
_active_sink: list[str] | None = None


def _emit(obj):
    """IPython display 或写入 html_sink 列表。"""
    global _active_sink
    if _active_sink is not None:
        if isinstance(obj, HTML):
            _active_sink.append(obj.data)
        elif hasattr(obj, "to_html"):
            _active_sink.append(obj.to_html())
        else:
            _active_sink.append(str(obj))
    else:
        display(obj)


# ========== 核心评价及精确量化引擎 ==========

def get_backtest_data(backtest_id):
    try:
        gt = get_backtest(backtest_id)
        bench_name = '基准指数'
        starting_cash = None
        try:
            params = gt.get_params()
            if 'benchmark' in params:
                bench_info = get_security_info(params['benchmark'])
                bench_name = bench_info.display_name if bench_info else bench_name
            # 获取初始资金（优先顺序：initial_cash > capital_base > starting_cash）
            if 'initial_cash' in params:
                starting_cash = float(params['initial_cash'])
            elif 'capital_base' in params:
                starting_cash = float(params['capital_base'])
            elif 'starting_cash' in params:
                starting_cash = float(params['starting_cash'])
        except:
            pass

        # 获取交易订单记录
        orders = []
        try:
            orders = gt.get_orders()
        except Exception as e:
            print(f'[警告] 获取交易记录失败: {e}')
            print('[提示] 请确认回测ID是否属于自己的账号，交易相关分析（第5/6/7/10节）将跳过，其余模块正常运行')
            # 不 return None，继续执行收益/回撤/风险指标等不依赖订单的分析

        # 获取每日市値（得到初始资金）
        if starting_cash is None:
            try:
                balances = gt.get_balances()
                if balances:
                    starting_cash = float(balances[0].get('cash', None) or 0) or None
            except Exception:
                pass

        # 获取每日市値
        balances = []
        try:
            balances = gt.get_balances()
            if balances and starting_cash is None:
                starting_cash = float(balances[0].get('cash', None) or 0) or None
        except Exception:
            pass

        # 获取官方风险指标
        risk = {}
        period_risks = {}
        try:
            risk = gt.get_risk() or {}
            period_risks = gt.get_period_risks() or {}
        except Exception:
            pass

        # 获取回测结果
        try:
            results = gt.get_results()
        except Exception as e:
            print(f'[警告] 获取回测结果失败: {e}')
            return None

        return {
            'results':      results,
            'bench_name':   bench_name,
            'starting_cash': starting_cash,
            'orders':       orders,
            'balances':     balances,
            'risk':         risk,
            'period_risks': period_risks,
        }
    except NameError:
        print("【错误】未检测到聚宽 get_backtest 函数。请确认是否在聚宽研究环境下运行。")
        return None
    except Exception as e:
        print(f'[警告] 获取回测数据失败: {e}')
        return None


def calc_max_drawdown(nav_series):
    rolling_max = nav_series.cummax()
    return ((nav_series - rolling_max) / rolling_max).min()


# 1. 总体定统指标获取函数
def get_performance_row(strat_ret, bench_ret, target_type, bench_name='基准指数'):
    days = len(strat_ret)
    if days == 0: return {}
    s_nav = (1 + strat_ret).cumprod()
    b_nav = (1 + bench_ret).cumprod()
    a_ret = strat_ret - bench_ret
    a_nav = (1 + a_ret).cumprod()

    s_tot = s_nav.iloc[-1] - 1
    b_tot = b_nav.iloc[-1] - 1
    a_tot = a_nav.iloc[-1] - 1

    s_ann = (1 + s_tot) ** (250 / days) - 1 if days > 0 else 0
    b_ann = (1 + b_tot) ** (250 / days) - 1 if days > 0 else 0
    a_ann = (1 + a_tot) ** (250 / days) - 1 if days > 0 else 0

    s_mdd = calc_max_drawdown(s_nav)
    b_mdd = calc_max_drawdown(b_nav)
    a_mdd = calc_max_drawdown(a_nav)

    s_vol = strat_ret.std(ddof=1) * np.sqrt(250) if len(strat_ret) > 1 else np.nan
    b_vol = bench_ret.std(ddof=1) * np.sqrt(250) if len(bench_ret) > 1 else np.nan
    a_vol = a_ret.std(ddof=1) * np.sqrt(250) if len(a_ret) > 1 else np.nan

    s_shp = (s_ann - REF_RATE) / s_vol if s_vol > 0 else np.nan
    b_shp = (b_ann - REF_RATE) / b_vol if b_vol > 0 else np.nan

    if len(strat_ret) > 1 and b_vol > 0:
        cov_matrix = np.cov(strat_ret, bench_ret)
        beta = cov_matrix[0, 1] / np.var(bench_ret, ddof=1)
    else:
        beta = np.nan

    alpha = (s_ann - REF_RATE) - beta * (b_ann - REF_RATE)
    s_win = (strat_ret > 0).sum() / days
    b_win = (bench_ret > 0).sum() / days
    a_win = (a_ret > 0).sum() / days

    cols = ['策略名', '总收益', '年化收益', '夏普比率', '最大回撤', '收益波动率', '日胜率', 'beta', 'alpha']
    if target_type == 1:
        return dict(zip(cols, ['本策略', s_tot, s_ann, s_shp, s_mdd, s_vol, s_win, beta, alpha]))
    elif target_type == 2:
        return dict(zip(cols, [bench_name, b_tot, b_ann, b_shp, b_mdd, b_vol, b_win, np.nan, np.nan]))
    else:
        return dict(zip(cols, ['相对收益', a_tot, a_ann, np.nan, a_mdd, a_vol, a_win,
                               beta - 1 if not pd.isna(beta) else np.nan, alpha]))


# 2. 年度汇总获取函数
def calc_annual_row(d_ret, b_ret, year):
    days = len(d_ret)
    if days == 0: return {}
    s_nav = (1 + d_ret).cumprod()
    b_nav = (1 + b_ret).cumprod()
    a_ret = d_ret - b_ret

    s_tot = s_nav.iloc[-1] - 1
    b_tot = b_nav.iloc[-1] - 1
    a_tot = (1 + a_ret).cumprod().iloc[-1] - 1

    s_mdd = calc_max_drawdown(s_nav)
    b_mdd = calc_max_drawdown(b_nav)

    s_ann = (1 + s_tot) ** (250 / days) - 1 if days > 0 else 0
    tracking_error = a_ret.std(ddof=1) * np.sqrt(250) if len(a_ret) > 1 else np.nan
    win = (d_ret > 0).sum() / days
    vol = d_ret.std(ddof=1) * np.sqrt(250) if len(d_ret) > 1 else np.nan
    shp = (s_ann - REF_RATE) / vol if (not pd.isna(vol) and vol > 0) else np.nan

    return {
        '年份': str(year),
        '策略收益': s_tot, '基准收益': b_tot,
        '策略最大回撤': s_mdd, '基准最大回撤': b_mdd,
        '跟踪误差': tracking_error, '日胜率': win,
        '波动率': vol, '夏普比率': shp
    }


# ========== 新增：月度盈亏计算引擎 ==========

def calc_monthly_pnl(df, starting_cash=None):
    """
    计算每月的盈亏率和盈亏额。
    - 盈亏率 = (月末NAV / 月初NAV) - 1
    - 盈亏额 = 月初组合市值 * 盈亏率（如无初始资金则以10^6归一化）
    返回 dict: {year: {month: {'rate': float, 'amount': float}}}
    """
    base_cash = float(starting_cash) if starting_cash else 1_000_000.0

    # 月度重采样，取每月最后一个交易日
    monthly = df['nav'].resample('ME').last()
    # 月初（即上月末）—— shift(1) 会导致第一个月的起点为 NaN
    monthly_start = monthly.shift(1)
    # 修复首月：用回测起点前的 NAV 补全（第一日 NAV / (1+第一日收益率) = 起点净值1.0处）
    pre_start_nav = df['nav'].iloc[0] / (1 + df['d_ret'].iloc[0])
    monthly_start.iloc[0] = pre_start_nav
    # 月度盈亏率
    monthly_rate = monthly / monthly_start - 1

    result = {}
    for ts, rate in monthly_rate.items():
        if pd.isna(rate):
            continue
        year = ts.year
        month = ts.month
        # 计算月初的绝对市值
        start_nav = monthly_start[ts]
        month_start_value = base_cash * start_nav
        amount = month_start_value * rate

        if year not in result:
            result[year] = {}
        result[year][month] = {'rate': rate, 'amount': amount}

    # 计算年度汇总（复利叠加月度收益率）
    for year, months in result.items():
        yearly_nav = df[df.index.year == year]['nav']
        if yearly_nav.empty:
            continue
        yr_start = yearly_nav.iloc[0] / (1 + df[df.index.year == year]['d_ret'].iloc[0])
        yr_end = yearly_nav.iloc[-1]
        yr_rate = yr_end / yr_start - 1
        yr_start_value = base_cash * yr_start
        yr_amount = yr_start_value * yr_rate
        result[year][0] = {'rate': yr_rate, 'amount': yr_amount}  # key=0 表示全年

    return result, base_cash


def render_monthly_table(monthly_data, base_cash, starting_cash=None):
    """
    渲染月度盈亏热力双行 HTML 表格。
    每格显示：上行=盈亏率，下行=盈亏额
    正值红色，负值绿色（A股惯例）
    """
    months_label = ['1月', '2月', '3月', '4月', '5月', '6月',
                    '7月', '8月', '9月', '10月', '11月', '12月', '年度']
    month_keys = list(range(1, 13)) + [0]  # 0=年度汇总

    html = f"""
    <h3 style='color:#444;margin-top:20px;'>4. 月度盈亏统计</h3>
    <table style='border-collapse:collapse;width:100%;font-size:13px;font-family:Arial,sans-serif;'>
    <thead>
      <tr style='background:#F6F6F6;'>
        <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;font-weight:bold;'>年份</th>
    """
    for m in months_label:
        html += f"<th style='padding:8px 12px;border:1px solid #DDD;text-align:center;font-weight:bold;'>{m}</th>"
    html += "</tr></thead><tbody>"

    for year in sorted(monthly_data.keys(), reverse=True):
        html += f"<tr>"
        # 年份列
        html += f"<td style='padding:8px 12px;border:1px solid #DDD;text-align:center;font-weight:bold;background:#FAFAFA;'>{year}</td>"

        for mk in month_keys:
            data = monthly_data[year].get(mk)
            if data is None:
                html += "<td style='padding:8px 12px;border:1px solid #DDD;text-align:center;color:#BBB;'>-</td>"
                continue

            rate = data['rate']
            amount = data['amount']
            is_annual = (mk == 0)

            # 颜色：正红负绿（A股惯例）
            color = '#D32F2F' if rate >= 0 else '#388E3C'
            bg = '#FFF5F5' if rate >= 0 else '#F5FFF5'
            if is_annual:
                bg = '#FFF0E0' if rate >= 0 else '#F0FFF0'
                font_weight = 'bold'
            else:
                font_weight = 'normal'

            rate_str = f"{rate*100:+.2f}%"

            html += f"""
            <td style='padding:6px 10px;border:1px solid #DDD;text-align:center;
                        background:{bg};font-weight:{font_weight};'>
              <div style='color:{color};font-size:13px;'>{rate_str}</div>
            </td>"""

        html += "</tr>"

    html += "</tbody></table>"
    return html


# ========== 新增：个股盈亏 Top30 ==========

def calc_stock_pnl(orders):
    """
    基于 gt.get_orders() 的订单记录计算个股已实现盈亏。
    关键字段（聚宽标准）：
      action       : 'open'=买入 / 'close'=卖出
      filled       : 实际成交量
      price        : 成交均价
      commission   : 单笔手续费
      gains        : 单笔已实现盈亏（卖出时有效）
      security     : 标的代码
      security_name: 标的名称
    """
    if not orders:
        return []

    df = pd.DataFrame(orders)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)

    # 字段兼容：如果没有 gains 字段，用 FIFO 计算
    has_gains = 'gains' in df.columns
    # 过滤未成交订单（filled == 0）
    if 'filled' in df.columns:
        df = df[df['filled'] > 0].copy()
    if 'commission' not in df.columns:
        df['commission'] = 0
    df['commission'] = pd.to_numeric(df['commission'], errors='coerce').fillna(0)

    # ------- 方案A：直接用 gains 字段汇总 -------
    if has_gains:
        df['gains'] = pd.to_numeric(df['gains'], errors='coerce').fillna(0)

        # 旧版 pandas 兼容写法，分开聚合
        gains_sum      = df.groupby('security')['gains'].sum()
        commission_sum = df.groupby('security')['commission'].sum()
        trade_count    = df.groupby('security')['security'].count()
        filled_sum     = df.groupby('security')['filled'].sum() if 'filled' in df.columns else pd.Series(dtype=float)

        # 剩余持仓：open filled - close filled
        if 'action' in df.columns:
            buy_df    = df[df['action'] == 'open'].groupby('security')['filled'].sum()
            sell_df   = df[df['action'] == 'close'].groupby('security')['filled'].sum()
            remaining = (buy_df.fillna(0) - sell_df.fillna(0)).clip(lower=0)
        else:
            remaining = pd.Series(dtype=float)

        rows = []
        for sec in gains_sum.index:
            if 'security_name' in df.columns:
                name_series = df[df['security'] == sec]['security_name']
                name = name_series.iloc[0] if not name_series.empty else sec
            else:
                try:
                    name = get_security_info(sec).display_name
                except Exception:
                    name = sec
            g   = gains_sum.get(sec, 0)
            com = commission_sum.get(sec, 0)
            net_pnl = g - com
            rows.append({
                '股票代码': sec[:6],
                '股票名称': name,
                '净盈亏(元)': net_pnl,
                '已实现盈亏': g,
                '手续费': -com,
                '剩余持仓(股)': remaining.get(sec, 0),
                '交易次数': trade_count.get(sec, 0),
            })
        return sorted(rows, key=lambda x: x['净盈亏(元)'], reverse=True)

    # ------- 方案B：如果字段中没有 gains，则用 FIFO 推算 -------
    result = {}
    for _, row in df.iterrows():
        sec  = row['security']
        name = row.get('security_name', sec)
        action = row.get('action', 'open')
        filled = float(row.get('filled', row.get('amount', 0)))
        price  = float(row.get('price', 0))
        commission = float(row.get('commission', 0))

        if sec not in result:
            result[sec] = {
                'name': name,
                'buy_queue': [],
                'realized_pnl': 0.0,
                'total_commission': 0.0,
                'trade_count': 0,
                'remaining_shares': 0.0,
            }
        r = result[sec]
        r['total_commission'] += commission
        r['trade_count'] += 1

        if action == 'open':  # 买入
            r['buy_queue'].append([filled, price])
            r['remaining_shares'] += filled
        else:                 # 卖出
            sell_left = filled
            while sell_left > 0 and r['buy_queue']:
                head_n, head_p = r['buy_queue'][0]
                matched = min(sell_left, head_n)
                r['realized_pnl'] += matched * (price - head_p)
                if matched >= head_n:
                    r['buy_queue'].pop(0)
                else:
                    r['buy_queue'][0][0] -= matched
                sell_left -= matched
                r['remaining_shares'] = max(0, r['remaining_shares'] - matched)

    rows = []
    for sec, r in result.items():
        net_pnl = r['realized_pnl'] - r['total_commission']
        rows.append({
            '股票代码': sec[:6],
            '股票名称': r['name'],
            '净盈亏(元)': net_pnl,
            '已实现盈亏': r['realized_pnl'],
            '手续费': -r['total_commission'],
            '剩余持仓(股)': int(r['remaining_shares']),
            '交易次数': r['trade_count'],
        })
    return sorted(rows, key=lambda x: x['净盈亏(元)'], reverse=True)


def render_stock_pnl_table(rows, title, is_profit=True, top_n=20):
    """渲染个股盈亏排行 HTML 表格"""
    # 严格过滤：盈利只取 >0，亏损只取 <0
    if is_profit:
        filtered = [r for r in rows if r['净盈亏(元)'] > 0]
    else:
        filtered = [r for r in rows if r['净盈亏(元)'] < 0]
        filtered = sorted(filtered, key=lambda x: x['净盈亏(元)'])  # 亏损从大到小
    selected = filtered[:top_n]
    if not selected:
        return f"<p style='color:#999;'>{title}：暂无数据</p>"

    cols = ['排名', '股票代码', '股票名称', '净盈亏(元)', '已实现盈亏', '手续费', '剩余持仓(股)', '交易次数']

    html = f"""
    <h4 style='color:#555;margin:16px 0 8px;'>{title}</h4>
    <table style='border-collapse:collapse;width:100%;font-size:13px;font-family:Arial,sans-serif;'>
    <thead>
      <tr style='background:#F6F6F6;'>
    """
    for c in cols:
        html += f"<th style='padding:8px 12px;border:1px solid #DDD;text-align:center;font-weight:bold;'>{c}</th>"
    html += "</tr></thead><tbody>"

    for i, row in enumerate(selected, 1):
        pnl = row['净盈亏(元)']
        color = '#D32F2F' if pnl >= 0 else '#388E3C'
        bg = '#FFF8F8' if pnl >= 0 else '#F8FFF8'
        if i % 2 == 0:
            bg = '#FAFAFA' if pnl >= 0 else '#F5FBF5'

        def fmt_num(v):
            if pd.isna(v): return '-'
            return f"{v:+,.0f}"

        html += f"""
        <tr style='background:{bg};border-bottom:1px solid #EEE;'>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:#888;'>{i}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{row['股票代码']}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{row['股票名称']}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:{color};font-weight:bold;'>{fmt_num(pnl)}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:{color};'>{fmt_num(row['已实现盈亏'])}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:#888;'>{fmt_num(row['手续费'])}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{row['剩余持仓(股)']}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{row['交易次数']}</td>
        </tr>"""

    html += "</tbody></table>"
    return html


# ========== 核心渲染库（V2原版保留） ==========

def n_to_s(val, mode='%', precision=2):
    if pd.isna(val) or np.isinf(val): return '-'
    if mode == '%': return f"{val*100:.{precision}f}%"
    return f"{val:.{precision}f}"


common_th_props = [
    ('background-color', '#F6F6F6'), ('color', '#333333'),
    ('font-weight', 'bold'), ('text-align', 'center'),
    ('border-bottom', '1px solid #DDDDDD'), ('padding', '10px 15px')
]


def render_overall_style(df):
    def row_styler(row):
        return ['background-color: #FFFFFF; color: #333333; font-weight: normal; '
                'border-bottom: 1px solid #EAEAEA; text-align: center;'] * len(row)

    fmt = {
        '总收益': lambda x: n_to_s(x), '年化收益': lambda x: n_to_s(x),
        '夏普比率': lambda x: n_to_s(x, 'f'), '最大回撤': lambda x: n_to_s(x),
        '收益波动率': lambda x: n_to_s(x), '日胜率': lambda x: n_to_s(x),
        'beta': lambda x: n_to_s(x, 'f'), 'alpha': lambda x: n_to_s(x)
    }
    sty = (df.style.apply(row_styler, axis=1)
             .format(fmt)
             .set_properties(**{'padding': '10px 15px', 'border-right': '1px solid #EAEAEA'})
             .set_table_styles([{'selector': 'th', 'props': common_th_props}]))
    try: sty = sty.hide_index()
    except:
        try: sty = sty.hide(axis="index")
        except: pass
    return sty


def render_annual_style(df):
    def comp_styler(row):
        styles = ['background-color: #FFFFFF; border-bottom: 1px solid #EAEAEA; '
                  'text-align: center; color: #333333;'] * len(row)
        idx_cols = list(row.index)
        for s_col, b_col in [('策略收益', '基准收益'), ('策略最大回撤', '基准最大回撤')]:
            if s_col in idx_cols and b_col in idx_cols:
                sr, br = row[s_col], row[b_col]
                si, bi = idx_cols.index(s_col), idx_cols.index(b_col)
                if not pd.isna(sr) and not pd.isna(br):
                    if sr >= br:
                        styles[si] += ' color: #D32F2F;'
                        styles[bi] += ' color: #388E3C;'
                    else:
                        styles[si] += ' color: #388E3C;'
                        styles[bi] += ' color: #D32F2F;'
        return styles

    fmt = {
        '策略收益': lambda x: n_to_s(x), '基准收益': lambda x: n_to_s(x),
        '策略最大回撤': lambda x: n_to_s(x), '基准最大回撤': lambda x: n_to_s(x),
        '跟踪误差': lambda x: n_to_s(x), '日胜率': lambda x: n_to_s(x),
        '波动率': lambda x: n_to_s(x), '夏普比率': lambda x: n_to_s(x, 'f')
    }
    sty = (df.style.apply(comp_styler, axis=1)
             .format(fmt)
             .set_properties(**{'padding': '10px 15px', 'border-right': '1px solid #EAEAEA'})
             .set_table_styles([{'selector': 'th', 'props': common_th_props}]))
    try: sty = sty.hide_index()
    except:
        try: sty = sty.hide(axis="index")
        except: pass
    return sty


def render_drawdown_style(df):
    def dd_styler(row):
        styles = ['background-color: #FFFFFF; border-bottom: 1px solid #EAEAEA; '
                  'text-align: center; color: #333333;'] * len(row)
        idx_cols = list(row.index)
        if '最大回撤' in idx_cols:
            styles[idx_cols.index('最大回撤')] += ' color: #388E3C; font-weight: bold;'
        if '解套周期(天)' in idx_cols:
            styles[idx_cols.index('解套周期(天)')] += ' color: #D32F2F; font-weight: bold;'
        return styles

    sty = (df.style.apply(dd_styler, axis=1)
             .format({'最大回撤': lambda x: n_to_s(x)})
             .set_properties(**{'padding': '10px 15px', 'border-right': '1px solid #EAEAEA'})
             .set_table_styles([{'selector': 'th', 'props': common_th_props}]))
    try: sty = sty.hide_index()
    except:
        try: sty = sty.hide(axis="index")
        except: pass
    return sty


# ========== 业务主流程 ==========

def analyze_strategy_performance(returns_data_pack, html_sink=None):
    global _active_sink
    prev_sink = _active_sink
    if html_sink is not None:
        _active_sink = html_sink
    try:
        _analyze_strategy_performance_impl(returns_data_pack)
    finally:
        _active_sink = prev_sink


def _analyze_strategy_performance_impl(returns_data_pack):
    if not returns_data_pack or 'results' not in returns_data_pack:
        return
    returns_data = returns_data_pack['results']
    if not returns_data:
        return

    bench_name = returns_data_pack.get('bench_name', '基准指数')
    starting_cash = returns_data_pack.get('starting_cash', None)
    orders = returns_data_pack.get('orders', [])
    meta = returns_data_pack.get('meta') or {}
    notes = meta.get('notes') or []

    df = pd.DataFrame(returns_data)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    df.set_index('time', inplace=True)

    df['nav'] = 1 + df['returns']
    df['d_ret'] = df['nav'].pct_change()
    df['d_ret'].iloc[0] = df['returns'].iloc[0]

    df['b_nav'] = 1 + df['benchmark_returns']
    df['b_ret'] = df['b_nav'].pct_change()
    df['b_ret'].iloc[0] = df['benchmark_returns'].iloc[0]

    if notes:
        note_html = "".join(
            f"<p style='color:#888;font-size:12px;margin:4px 0;'>ℹ️ {n}</p>" for n in notes
        )
        _emit(HTML(f"<div style='margin:8px 0 16px 0;'>{note_html}</div>"))

    # ---------- 1. 收益统计 ----------
    if SHOW_SECTIONS.get(1, True):
        _emit(HTML("<h3 style='color: #444;'>1. 收益统计</h3>"))
        r1 = get_performance_row(df['d_ret'], df['b_ret'], 1, bench_name)
        r2 = get_performance_row(df['d_ret'], df['b_ret'], 2, bench_name)
        r3 = get_performance_row(df['d_ret'], df['b_ret'], 3, bench_name)
        cols = ['策略名', '总收益', '年化收益', '夏普比率', '最大回撤', '收益波动率', '日胜率', 'beta', 'alpha']
        _emit(render_overall_style(pd.DataFrame([r1, r2, r3], columns=cols)))

    # ---------- 2. 年度收益统计 ----------
    if SHOW_SECTIONS.get(2, True):
        _emit(HTML("<h3 style='color: #444; margin-top:20px;'>2. 年度收益统计</h3>"))
        df['year'] = df.index.year
        an_rows = []
        for y in df['year'].unique():
            d_y = df[df['year'] == y]
            an_rows.append(calc_annual_row(d_y['d_ret'], d_y['b_ret'], year=y))
        cols_ann = ['年份', '策略收益', '基准收益', '策略最大回撤', '基准最大回撤', '跟踪误差', '日胜率', '波动率', '夏普比率']
        _emit(render_annual_style(pd.DataFrame(an_rows, columns=cols_ann)))

    # ---------- 3. 最大 N 次回撤 ----------
    if SHOW_SECTIONS.get(3, True):
        _emit(HTML(f"<h3 style='color: #444; margin-top:20px;'>3. 最大 {TOP_N} 次回撤 概览</h3>"))
        df_dd = df.reset_index()
        df_dd['rolling_max'] = df_dd['nav'].cummax()
        df_dd['drawdown'] = (df_dd['nav'] - df_dd['rolling_max']) / df_dd['rolling_max']
        df_dd.loc[df_dd['drawdown'] > -1e-7, 'drawdown'] = 0
        df_dd['is_high'] = (df_dd['drawdown'] == 0)
        df_dd['period_id'] = df_dd['is_high'].cumsum()

        dd_list = []
        for pid, group in df_dd.groupby('period_id'):
            if len(group) <= 1:
                continue
            m_dd = group['drawdown'].min()
            if m_dd < -0.001:
                s_date = group['time'].iloc[0]
                v_date = group.loc[group['drawdown'].idxmin(), 'time']
                r_date, r_days = None, np.nan
                last_idx = group.index[-1]
                if last_idx + 1 < len(df_dd):
                    r_date = df_dd.loc[last_idx + 1, 'time']
                    r_days = (r_date - s_date).days
                dd_list.append({
                    '跌落日期': s_date.strftime('%Y-%m-%d'),
                    '谷底日期': v_date.strftime('%Y-%m-%d'),
                    '最大回撤': m_dd,
                    '解套日期': r_date.strftime('%Y-%m-%d') if r_date else '尚待解套',
                    '解套周期(天)': r_days if not pd.isna(r_days) else '-'
                })

        top_dd = sorted(dd_list, key=lambda x: x['最大回撤'])[:min(TOP_N, len(dd_list))]
        td_df = pd.DataFrame(top_dd)[['跌落日期', '谷底日期', '最大回撤', '解套日期', '解套周期(天)']]
        _emit(render_drawdown_style(td_df))

    # ---------- 4. 月度盈亏统计 ----------
    if SHOW_SECTIONS.get(4, True):
        monthly_data, base_cash = calc_monthly_pnl(df, starting_cash)
        monthly_html = render_monthly_table(monthly_data, base_cash, starting_cash)
        _emit(HTML(monthly_html))

    # ---------- 5. 个股盈亏 Top20 ----------
    if SHOW_SECTIONS.get(5, True):
        if orders:
            _emit(HTML(f"<h3 style='color:#444;margin-top:24px;'>5. 个股盈亏排行 Top{STOCK_TOP_N}</h3>"))
            stock_rows = calc_stock_pnl(orders)
            _emit(HTML(render_stock_pnl_table(stock_rows, f'🏆 盈利 Top{STOCK_TOP_N}', is_profit=True,  top_n=STOCK_TOP_N)))
            _emit(HTML(render_stock_pnl_table(stock_rows, f'💔 亏损 Top{STOCK_TOP_N}', is_profit=False, top_n=STOCK_TOP_N)))
        else:
            _emit(HTML("<p style='color:#999;margin-top:20px;'>⚠️ 未获取到交易记录，无法计算个股盈亏</p>"))

    # ---------- 6. 交易质量分析 ----------
    if SHOW_SECTIONS.get(6, True) and orders:
        _emit(HTML("<h3 style='color:#444;margin-top:24px;'>6. 交易质量分析</h3>"))
        _emit(HTML(render_trade_quality(orders, starting_cash, df)))

    # ---------- 7. 持仓行为分析 ----------
    if SHOW_SECTIONS.get(7, True) and orders:
        _emit(HTML("<h3 style='color:#444;margin-top:24px;'>7. 持仓行为分析</h3>"))
        _emit(HTML(render_holding_behavior(orders)))

    # ---------- 8. 仓位利用率分析 ----------
    if SHOW_SECTIONS.get(8, True):
        balances = returns_data_pack.get('balances', [])
        if balances:
            _emit(HTML("<h3 style='color:#444;margin-top:24px;'>8. 仓位利用率分析</h3>"))
            _emit(HTML(render_position_utilization(balances, starting_cash)))

    # ---------- 9. 官方风险指标 ----------
    if SHOW_SECTIONS.get(9, True):
        risk        = returns_data_pack.get('risk', {})
        period_risks = returns_data_pack.get('period_risks', {})
        if risk:
            _emit(HTML("<h3 style='color:#444;margin-top:24px;'>9. 官方风险指标</h3>"))
            _emit(HTML(render_official_risk(risk, period_risks)))
            _emit(HTML(render_stats_trade_summary(risk)))

    # ---------- 10. 交易时间分布 ----------
    if SHOW_SECTIONS.get(10, True) and orders:
        _emit(HTML("<h3 style='color:#444;margin-top:24px;'>10. 交易时间分布</h3>"))
        _emit(HTML(render_trade_time_dist(orders)))


# ==================== 第6节：交易质量分析 ====================

def render_trade_quality(orders, starting_cash, df_nav):
    od = pd.DataFrame(orders)
    od['time'] = pd.to_datetime(od['time'])
    if 'filled' in od.columns:
        od = od[od['filled'] > 0].copy()
    if 'gains' not in od.columns:
        return "<p style='color:#999;'>缺少 gains 字段，无法计算交易质量</p>"
    od['gains'] = pd.to_numeric(od['gains'], errors='coerce').fillna(0)
    if 'commission' not in od.columns:
        od['commission'] = 0
    od['commission'] = pd.to_numeric(od['commission'], errors='coerce').fillna(0)

    # 只取卖出订单（close）
    if 'action' in od.columns:
        close_od = od[od['action'] == 'close']
    else:
        close_od = od  # 无 action 字段时全部当卖出处理

    # gains 在聚宽中已是扣完手续费后的净收益，不再重复扣减
    net_gains = close_od['gains']

    total        = len(close_od)
    win          = (net_gains > 0).sum()
    loss         = (net_gains <= 0).sum()   # gains=0 视为亏损（手续费已扣）
    win_rate     = win / total if total > 0 else 0
    avg_win      = net_gains[net_gains > 0].mean() if win > 0 else 0
    avg_loss     = net_gains[net_gains < 0].mean() if loss > 0 else 0
    pnl_ratio    = abs(avg_win / avg_loss) if avg_loss != 0 else float('nan')
    expected     = win_rate * avg_win + (1 - win_rate) * avg_loss
    best_trade   = net_gains.max() if total > 0 else 0
    worst_trade  = net_gains.min() if total > 0 else 0
    total_commission = od['commission'].sum()

    # 卡尔马比率
    days = len(df_nav)
    s_tot = (1 + df_nav['d_ret']).cumprod().iloc[-1] - 1
    ann   = (1 + s_tot) ** (250 / days) - 1 if days > 0 else 0
    mdd_nav = (1 + df_nav['d_ret']).cumprod()
    mdd   = ((mdd_nav - mdd_nav.cummax()) / mdd_nav.cummax()).min()
    calmar = ann / abs(mdd) if mdd != 0 else float('nan')

    def fv(v, fmt=',.0f', color=None):
        if pd.isna(v) or np.isinf(v): return '-'
        s = ("{:" + fmt + "}").format(v)
        if color:
            return f"<span style='color:{color};font-weight:bold;'>{s}</span>"
        return s

    rows = [
        ('总交易笔数',   fv(total, ',.0f')),
        ('盈利笔数',   fv(win,   ',.0f', '#D32F2F')),
        ('亏损笔数',   fv(loss,  ',.0f', '#388E3C')),
        ('交易胜率',   f"<span style='color:{'#D32F2F' if win_rate>=0.5 else '#388E3C'};font-weight:bold;'>{win_rate*100:.2f}%</span>"),
        ('平均盈利(元)', fv(avg_win,  ',.0f', '#D32F2F')),
        ('平均亏损(元)', fv(avg_loss, ',.0f', '#388E3C')),
        ('盈亏比',     fv(pnl_ratio, '.2f')),
        ('单笔期望收益(元)', fv(expected, ',.0f', '#D32F2F' if expected>=0 else '#388E3C')),
        ('最佳单笔(元)', fv(best_trade,  ',.0f', '#D32F2F')),
        ('最差单笔(元)', fv(worst_trade, ',.0f', '#388E3C')),
        ('总手续费(元)', fv(-total_commission, ',.0f', '#888')),
        ('卡尔马比率',  fv(calmar, '.2f')),
    ]

    html = "<table style='border-collapse:collapse;width:60%;font-size:13px;'>"
    for label, val in rows:
        html += f"<tr><td style='padding:7px 16px;border:1px solid #E0E0E0;background:#FAFAFA;font-weight:bold;width:40%;'>{label}</td>"
        html += f"<td style='padding:7px 16px;border:1px solid #E0E0E0;text-align:right;'>{val}</td></tr>"
    html += "</table>"
    return html


# ==================== 第7节：持仓行为分析 ====================

def render_holding_behavior(orders):
    od = pd.DataFrame(orders)
    od['time'] = pd.to_datetime(od['time'])
    if 'action' not in od.columns or 'security' not in od.columns:
        return "<p style='color:#999;'>缺少 action 字段，无法计算持仓天数</p>"
    if 'filled' in od.columns:
        od = od[od['filled'] > 0].copy()

    buy_od  = od[od['action'] == 'open' ][['security', 'time']].rename(columns={'time': 'buy_time'})
    sell_od = od[od['action'] == 'close'][['security', 'time']].rename(columns={'time': 'sell_time'})

    # 按股票+时序 FIFO 配对
    buy_od  = buy_od.sort_values('buy_time').reset_index(drop=True)
    sell_od = sell_od.sort_values('sell_time').reset_index(drop=True)

    pairs = []
    buy_queues = {}
    for _, r in buy_od.iterrows():
        sec = r['security']
        if sec not in buy_queues:
            buy_queues[sec] = []
        buy_queues[sec].append(r['buy_time'])
    for _, r in sell_od.iterrows():
        sec = r['security']
        if sec in buy_queues and buy_queues[sec]:
            bt = buy_queues[sec].pop(0)
            days_held = (r['sell_time'] - bt).days
            pairs.append(days_held)

    if not pairs:
        return "<p style='color:#999;'>没有匹配到买卖配对，无法分析持仓天数</p>"

    pairs_s = pd.Series(pairs)
    avg_d  = pairs_s.mean()
    med_d  = pairs_s.median()
    max_d  = pairs_s.max()
    min_d  = pairs_s.min()
    d1  = (pairs_s < 5).sum()
    d2  = ((pairs_s >= 5) & (pairs_s < 20)).sum()
    d3  = (pairs_s >= 20).sum()
    total = len(pairs_s)

    # 年化换手率
    if 'filled' in od.columns and 'price' in od.columns:
        od['turnover'] = pd.to_numeric(od['filled'], errors='coerce') * pd.to_numeric(od['price'], errors='coerce')
        total_buy = od[od['action'] == 'open']['turnover'].sum()
        date_range = (od['time'].max() - od['time'].min()).days
        ann_turnover = (total_buy / max(date_range, 1)) * 250 if date_range > 0 else 0
    else:
        ann_turnover = float('nan')

    rows = [
        ('配对成功笔数', f"{total}"),
        ('平均持仓天数', f"{avg_d:.1f} 天"),
        ('中位持仓天数', f"{med_d:.1f} 天"),
        ('最长持仓',   f"{int(max_d)} 天"),
        ('最短持仓',   f"{int(min_d)} 天"),
        ('<5天短线程',  f"{d1}笔 ({d1/total*100:.1f}%)"),
        ('5~20天中线程', f"{d2}笔 ({d2/total*100:.1f}%)"),
        ('>20天长线程',  f"{d3}笔 ({d3/total*100:.1f}%)"),
        ('年化成交额(元)', f"{ann_turnover:,.0f}" if not pd.isna(ann_turnover) else '-'),
    ]

    html = "<table style='border-collapse:collapse;width:60%;font-size:13px;'>"
    for label, val in rows:
        html += f"<tr><td style='padding:7px 16px;border:1px solid #E0E0E0;background:#FAFAFA;font-weight:bold;'>{label}</td>"
        html += f"<td style='padding:7px 16px;border:1px solid #E0E0E0;text-align:right;'>{val}</td></tr>"
    html += "</table>"
    return html


# ==================== 第8节：仓位利用率分析 ====================

def render_position_utilization(balances, starting_cash):
    bl = pd.DataFrame(balances)
    if bl.empty:
        return "<p style='color:#999;'>没有 balances 数据</p>"
    bl['time'] = pd.to_datetime(bl['time'])

    # 尝试计算持仓市値 = 总资产 - 现金
    has_total   = 'total_value' in bl.columns or 'value' in bl.columns
    has_cash    = 'cash' in bl.columns
    has_locked  = 'locked_cash' in bl.columns

    tv_col   = 'total_value' if 'total_value' in bl.columns else ('value' if 'value' in bl.columns else None)
    cash_col = 'cash' if has_cash else None

    if tv_col and cash_col:
        bl['stock_value'] = pd.to_numeric(bl[tv_col], errors='coerce') - pd.to_numeric(bl[cash_col], errors='coerce')
        bl['util_rate']   = bl['stock_value'] / pd.to_numeric(bl[tv_col], errors='coerce')
        avg_util   = bl['util_rate'].mean()
        empty_days = (bl['util_rate'] < 0.05).sum()
        full_days  = (bl['util_rate'] > 0.95).sum()
        total_days = len(bl)
    elif tv_col:
        avg_util   = float('nan')
        empty_days = 0
        full_days  = 0
        total_days = len(bl)
    else:
        return "<p style='color:#999;'>缺少 total_value / cash 字段，无法分析仓位</p>"

    rows = [
        ('回测总交易日', f"{total_days} 天"),
        ('平均仓位利用率', f"{avg_util*100:.1f}%" if not pd.isna(avg_util) else '-'),
        ('空仓天数(<5%)', f"{empty_days} 天 ({empty_days/total_days*100:.1f}%)"),
        ('满仓天数(>95%)', f"{full_days} 天 ({full_days/total_days*100:.1f}%)"),
    ]
    if tv_col:
        final_nav = pd.to_numeric(bl[tv_col], errors='coerce').iloc[-1]
        if starting_cash:
            total_ret = final_nav / starting_cash - 1
            rows.insert(0, ('初始资金(元)', f"{starting_cash:,.0f}"))
            rows.append(('期末资产(元)', f"{final_nav:,.0f}"))
            rows.append(('总收益(元)', f"{final_nav - starting_cash:+,.0f}"))

    html = "<table style='border-collapse:collapse;width:60%;font-size:13px;'>"
    for label, val in rows:
        html += f"<tr><td style='padding:7px 16px;border:1px solid #E0E0E0;background:#FAFAFA;font-weight:bold;'>{label}</td>"
        html += f"<td style='padding:7px 16px;border:1px solid #E0E0E0;text-align:right;'>{val}</td></tr>"
    html += "</table>"
    return html


# ==================== 第9节：官方风险指标 ====================

def render_official_risk(risk, period_risks):
    label_map = {
        'algorithm_volatility':  '策略波动率',
        'benchmark_volatility':  '基准波动率',
        'alpha':                 'Alpha',
        'beta':                  'Beta',
        'sharpe':                '夏普比率',
        'sortino':               'Sortino比率',
        'max_drawdown':          '最大回撤',
        'information_ratio':     '信息比率',
        'calmar':                '卡尔马比率',
        'algorithm_return':      '策略总收益',
        'benchmark_return':      '基准总收益',
        'excess_return':         '超额收益',
        'downside_risk':         '下行风险',
        'tracking_error':        '跟踪误差',
    }

    html = "<table style='border-collapse:collapse;width:60%;font-size:13px;'>"
    for key, label in label_map.items():
        val = risk.get(key)
        if val is None:
            continue
        try:
            fval = float(val)
            if key in ['algorithm_return', 'benchmark_return', 'excess_return',
                       'max_drawdown', 'algorithm_volatility', 'benchmark_volatility',
                       'downside_risk', 'tracking_error']:
                display_val = f"{fval*100:.2f}%"
            else:
                display_val = f"{fval:.4f}"
        except Exception:
            display_val = str(val)
        html += f"<tr><td style='padding:7px 16px;border:1px solid #E0E0E0;background:#FAFAFA;font-weight:bold;'>{label}</td>"
        html += f"<td style='padding:7px 16px;border:1px solid #E0E0E0;text-align:right;'>{display_val}</td></tr>"
    html += "</table>"
    return html


def render_stats_trade_summary(risk: dict) -> str:
    """Full-sample trade stats from JQ /stats (when web orders are truncated)."""
    rows = []
    mapping: list[tuple] = [
        ("win_count", "盈利笔数", False),
        ("lose_count", "亏损笔数", False),
        ("win_ratio", "胜率", True),
        ("profit_loss_ratio", "盈亏比", False),
        ("avg_position_days", "平均持仓天数", False),
        ("turnover_rate", "年化换手率", True),
        ("day_win_ratio", "日胜率", True),
        ("avg_trade_return", "单笔平均收益", True),
    ]
    for key, label, is_pct in mapping:
        val = risk.get(key)
        if val is None:
            continue
        try:
            fval = float(val)
            display_val = f"{fval * 100:.2f}%" if is_pct else f"{fval:.4f}"
        except (TypeError, ValueError):
            display_val = str(val)
        rows.append((label, display_val))
    if not rows:
        return ""
    html = "<h4 style='color:#555;margin:12px 0 6px 0;'>聚宽 stats 全样本交易摘要</h4>"
    html += "<table style='border-collapse:collapse;width:55%;font-size:13px;'>"
    for label, val in rows:
        html += (
            f"<tr><td style='padding:7px 16px;border:1px solid #E0E0E0;background:#FAFAFA;font-weight:bold;'>"
            f"{label}</td>"
            f"<td style='padding:7px 16px;border:1px solid #E0E0E0;text-align:right;'>{val}</td></tr>"
        )
    html += "</table>"
    return html


# ==================== 第10节：交易时间分布 ====================

def render_trade_time_dist(orders):
    od = pd.DataFrame(orders)
    od['time'] = pd.to_datetime(od['time'])
    if 'action' in od.columns and 'filled' in od.columns:
        od = od[(od['action'] == 'close') & (od['filled'] > 0)].copy()
    if 'gains' in od.columns:
        od['gains'] = pd.to_numeric(od['gains'], errors='coerce').fillna(0)
    else:
        od['gains'] = 0

    od['weekday'] = od['time'].dt.weekday   # 0=周一
    od['month']   = od['time'].dt.month
    od['year']    = od['time'].dt.year

    weekday_names = ['周一', '周二', '周三', '周四', '周五']
    month_names   = [str(m) + '月' for m in range(1, 13)]

    def make_dist_html(group_col, labels, title):
        rows_html = ''
        for i, label in enumerate(labels):
            sub = od[od[group_col] == i if group_col == 'weekday' else od[group_col] == (i + 1)]
            cnt = len(sub)
            wins = (sub['gains'] > 0).sum() if cnt > 0 else 0
            wr   = wins / cnt if cnt > 0 else 0
            avg  = sub['gains'].mean() if cnt > 0 else 0
            color = '#D32F2F' if wr >= 0.5 else '#388E3C'
            bar_w = int(wr * 100)
            rows_html += f"""
            <tr>
              <td style='padding:6px 12px;border:1px solid #EEE;font-weight:bold;background:#FAFAFA;'>{label}</td>
              <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{cnt}</td>
              <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:{color};font-weight:bold;'>{wr*100:.1f}%
                <div style='background:#EEE;height:6px;border-radius:3px;margin-top:3px;'>
                  <div style='background:{color};width:{bar_w}%;height:6px;border-radius:3px;'></div>
                </div>
              </td>
              <td style='padding:6px 12px;border:1px solid #EEE;text-align:right;color:{color};'>{avg:+,.0f}</td>
            </tr>"""
        return f"""
        <h4 style='color:#555;margin:16px 0 8px 0;'>{title}</h4>
        <table style='border-collapse:collapse;width:55%;font-size:13px;'>
          <thead><tr style='background:#F6F6F6;'>
            <th style='padding:8px 12px;border:1px solid #DDD;'>{group_col=='weekday' and '星期' or '月份'}</th>
            <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>交易笔数</th>
            <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>胜率</th>
            <th style='padding:8px 12px;border:1px solid #DDD;text-align:right;'>平均盈亏(元)</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>"""

    # 按星期几
    wd_html = '<table style="border-collapse:collapse;width:55%;font-size:13px;">'
    wd_html += """<thead><tr style='background:#F6F6F6;'>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:left;'>星期</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>交易笔数</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>胜率</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:right;'>平均盈亏(元)</th>
    </tr></thead><tbody>"""
    for i, label in enumerate(weekday_names):
        sub = od[od['weekday'] == i]
        cnt = len(sub)
        wins = (sub['gains'] > 0).sum() if cnt > 0 else 0
        wr   = wins / cnt if cnt > 0 else 0
        avg  = sub['gains'].mean() if cnt > 0 else 0
        color = '#D32F2F' if wr >= 0.5 else '#388E3C'
        bar_w = int(wr * 100)
        wd_html += f"""
        <tr>
          <td style='padding:6px 12px;border:1px solid #EEE;font-weight:bold;background:#FAFAFA;'>{label}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{cnt}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:{color};font-weight:bold;'>{wr*100:.1f}%
            <div style='background:#EEE;height:6px;border-radius:3px;margin-top:3px;'>
              <div style='background:{color};width:{bar_w}%;height:6px;border-radius:3px;'></div>
            </div>
          </td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:right;color:{color};'>{avg:+,.0f}</td>
        </tr>"""
    wd_html += '</tbody></table>'

    # 按月份
    mo_html = '<table style="border-collapse:collapse;width:55%;font-size:13px;">'
    mo_html += """<thead><tr style='background:#F6F6F6;'>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:left;'>月份</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>交易笔数</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:center;'>胜率</th>
      <th style='padding:8px 12px;border:1px solid #DDD;text-align:right;'>平均盈亏(元)</th>
    </tr></thead><tbody>"""
    for m in range(1, 13):
        sub = od[od['month'] == m]
        cnt = len(sub)
        wins = (sub['gains'] > 0).sum() if cnt > 0 else 0
        wr   = wins / cnt if cnt > 0 else 0
        avg  = sub['gains'].mean() if cnt > 0 else 0
        color = '#D32F2F' if wr >= 0.5 else '#388E3C'
        bar_w = int(wr * 100)
        mo_html += f"""
        <tr>
          <td style='padding:6px 12px;border:1px solid #EEE;font-weight:bold;background:#FAFAFA;'>{m}月</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;'>{cnt}</td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:center;color:{color};font-weight:bold;'>{wr*100:.1f}%
            <div style='background:#EEE;height:6px;border-radius:3px;margin-top:3px;'>
              <div style='background:{color};width:{bar_w}%;height:6px;border-radius:3px;'></div>
            </div>
          </td>
          <td style='padding:6px 12px;border:1px solid #EEE;text-align:right;color:{color};'>{avg:+,.0f}</td>
        </tr>"""
    mo_html += '</tbody></table>'

    return f"""
    <div style='display:flex;gap:40px;flex-wrap:wrap;'>
      <div>
        <h4 style='color:#555;margin:0 0 8px 0;'>📅 按星期几分布</h4>{wd_html}
      </div>
      <div>
        <h4 style='color:#555;margin:0 0 8px 0;'>📆 按月份分布</h4>{mo_html}
      </div>
    </div>"""


# ========== 入口 ==========

def analyze_strategy_to_html(
    pack: dict,
    *,
    title: str = "",
    subtitle: str = "",
) -> str:
    """Run full analysis and return standalone HTML document."""
    parts: list[str] = []
    doc_title = title or "聚宽回测分析报告"
    parts.append(
        f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"/>
<title>{doc_title}</title>
<style>
body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 24px 32px; color: #333; }}
h1 {{ font-size: 22px; margin-bottom: 4px; }}
.sub {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
table {{ margin-bottom: 12px; }}
</style>
</head><body>"""
    )
    if title:
        parts.append(f"<h1>{title}</h1>")
    if subtitle:
        parts.append(f"<p class='sub'>{subtitle}</p>")
    analyze_strategy_performance(pack, html_sink=parts)
    parts.append("</body></html>")
    return "\n".join(parts)


def write_analysis_html(pack: dict, path, **kwargs):
    """Write analyze_strategy_to_html output to file."""
    from pathlib import Path as P

    out = P(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(analyze_strategy_to_html(pack, **kwargs), encoding="utf-8")
    return out


if __name__ == "__main__":
    bt_data_pack = get_backtest_data(BACKTEST_ID)
    if bt_data_pack:
        analyze_strategy_performance(bt_data_pack)
   