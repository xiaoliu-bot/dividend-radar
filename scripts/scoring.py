"""
评分与信号引擎

设计原则：
1. 基本面负责"选什么"，技术面负责"什么时候买"。两者分开算分，最后加权。
2. 每一条信号都要能说清楚触发原因，不做黑箱打分。
3. 分数只是排序工具，不是买入许可证。
"""

import math
import numpy as np
import pandas as pd

from indicators import cross_up, cross_down, crossed_recently


def _clip(x, lo=0.0, hi=100.0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    return float(max(lo, min(hi, x)))


def _safe(v, default=0.0):
    """把 NaN / None / inf 统一成默认值，避免污染 JSON"""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


# ---------------------------------------------------------------- 基本面评分


def score_fundamental(row: dict) -> dict:
    """
    基本面评分（0-100），四个子维度加权：

    - 股息回报 35%：股息率越高越好，但 8% 以上反而扣分（大概率是股价暴跌造成的假高息）
    - 分红持续性 25%：连续分红年数
    - 盈利质量 25%：ROE
    - 财务安全 15%：资产负债率 + 分红率健康度
    """
    detail = {}

    # --- 股息回报
    dy = _safe(row.get("dividend_yield"))
    if dy <= 0:
        s_div = 0.0
    elif dy < 3:
        s_div = dy / 3 * 55                      # 3% 以下线性给分，最高 55
    elif dy <= 6:
        s_div = 55 + (dy - 3) / 3 * 45           # 3%-6% 是黄金区间，最高 100
    elif dy <= 8:
        s_div = 100 - (dy - 6) / 2 * 20          # 6%-8% 开始警惕
    else:
        s_div = max(40.0, 80 - (dy - 8) * 8)     # 8% 以上大概率有陷阱，压分
    detail["dividend"] = _clip(s_div)

    # --- 分红持续性
    years = int(_safe(row.get("dividend_years")))
    s_years = {0: 0, 1: 25, 2: 50, 3: 70, 4: 85}.get(years, 100 if years >= 5 else 0)
    detail["consistency"] = _clip(s_years)

    # --- 盈利质量
    roe = _safe(row.get("roe"))
    if roe <= 0:
        s_roe = 0.0
    elif roe < 8:
        s_roe = roe / 8 * 45
    elif roe <= 20:
        s_roe = 45 + (roe - 8) / 12 * 55
    else:
        s_roe = 100.0
    detail["profitability"] = _clip(s_roe)

    # --- 财务安全：负债率 + 分红率
    debt = _safe(row.get("debt_ratio"), 50.0)
    if debt <= 0:
        s_debt = 60.0
    elif debt < 40:
        s_debt = 100.0
    elif debt < 60:
        s_debt = 100 - (debt - 40) / 20 * 25
    elif debt < 70:
        s_debt = 75 - (debt - 60) / 10 * 30
    else:
        s_debt = max(0.0, 45 - (debt - 70) * 2)

    payout = _safe(row.get("payout_ratio"))
    if payout <= 0:
        s_payout = 40.0
    elif payout < 20:
        s_payout = 50 + payout / 20 * 30
    elif payout <= 60:
        s_payout = 100.0                          # 20%-60% 最健康
    elif payout <= 80:
        s_payout = 100 - (payout - 60) / 20 * 30
    else:
        s_payout = max(20.0, 70 - (payout - 80) * 1.5)

    detail["safety"] = _clip(s_debt * 0.6 + s_payout * 0.4)

    total = (
        detail["dividend"] * 0.35
        + detail["consistency"] * 0.25
        + detail["profitability"] * 0.25
        + detail["safety"] * 0.15
    )
    return {"total": round(_clip(total), 1), "detail": {k: round(v, 1) for k, v in detail.items()}}


# ---------------------------------------------------------------- 技术面评分


def score_trend(df: pd.DataFrame) -> float:
    """趋势维度：均线排列 + 价格位置 + 均线斜率"""
    last = df.iloc[-1]
    score = 0.0

    ma5, ma10, ma20, ma60 = (_safe(last.get(k), np.nan) for k in ("ma5", "ma10", "ma20", "ma60"))
    close = _safe(last["close"])

    # 均线多头排列，逐级给分
    order_pairs = [(ma5, ma10), (ma10, ma20), (ma20, ma60)]
    for a, b in order_pairs:
        if not math.isnan(a) and not math.isnan(b) and a > b:
            score += 12
    # 价格站上关键均线
    if not math.isnan(ma20) and close > ma20:
        score += 14
    if not math.isnan(ma60) and close > ma60:
        score += 14

    # 均线斜率：MA20 与 10 天前比较
    if len(df) > 12 and "ma20" in df:
        prev = _safe(df["ma20"].iloc[-11], np.nan)
        if not math.isnan(prev) and prev > 0 and not math.isnan(ma20):
            slope = (ma20 - prev) / prev * 100
            score += _clip(18 + slope * 6, 0, 36) if slope > 0 else _clip(18 + slope * 9, 0, 18)

    return _clip(score)


def score_momentum(df: pd.DataFrame) -> float:
    """动能维度：MACD + RSI + KDJ"""
    last = df.iloc[-1]
    score = 0.0

    dif, dea = _safe(last.get("macd_dif")), _safe(last.get("macd_dea"))
    hist = _safe(last.get("macd_hist"))
    if dif > dea:
        score += 18
    if dif > 0:
        score += 10
    # 柱状体是否在放大
    if len(df) >= 3:
        h_prev = _safe(df["macd_hist"].iloc[-2])
        if hist > h_prev:
            score += 12

    rsi = _safe(last.get("rsi"), 50)
    if rsi < 30:
        score += 22          # 超卖，反弹概率高
    elif rsi < 45:
        score += 26
    elif rsi <= 65:
        score += 30          # 强而不过热，最理想
    elif rsi <= 75:
        score += 16
    else:
        score += 5           # 过热

    k, d = _safe(last.get("kdj_k"), 50), _safe(last.get("kdj_d"), 50)
    if k > d:
        score += 16
    if k < 25:
        score += 14
    elif k > 85:
        score -= 6

    return _clip(score)


def score_volume(df: pd.DataFrame) -> float:
    """量能维度：温和放量最好，暴量和地量都要警惕"""
    last = df.iloc[-1]
    score = 0.0

    vr = _safe(last.get("vol_ratio"), 1.0)
    if vr < 0.5:
        score += 12          # 极度缩量，缺乏关注
    elif vr < 0.8:
        score += 28
    elif vr <= 1.8:
        score += 55          # 温和放量，健康
    elif vr <= 3.0:
        score += 38
    else:
        score += 15          # 暴量，可能是出货

    vt = _safe(last.get("vol_trend"), 1.0)
    if vt > 1.5:
        score += 22
    elif vt > 1.0:
        score += 32
    elif vt > 0.7:
        score += 20
    else:
        score += 6

    # 波动率不能太夸张，高股息股本来就该稳
    atr_pct = _safe(last.get("atr_pct"), 2.0)
    if atr_pct <= 2.5:
        score += 10
    elif atr_pct <= 4:
        score += 6

    return _clip(score)


def score_technical(df: pd.DataFrame) -> dict:
    """技术面综合分：趋势 40% + 动能 35% + 量能 25%"""
    trend = score_trend(df)
    momentum = score_momentum(df)
    volume = score_volume(df)
    total = trend * 0.40 + momentum * 0.35 + volume * 0.25
    return {
        "total": round(_clip(total), 1),
        "detail": {
            "trend": round(trend, 1),
            "momentum": round(momentum, 1),
            "volume": round(volume, 1),
        },
    }


# ---------------------------------------------------------------- 买卖信号


def detect_signals(df: pd.DataFrame) -> dict:
    """
    扫描买入 / 卖出信号。

    每条信号带 strength（1-3 星）和人话解释。信号数量本身不是买入理由，
    但多条信号共振时，胜率通常比单条高。
    """
    last = df.iloc[-1]
    close = _safe(last["close"])
    buy, sell = [], []

    # ---- 买入信号
    d = crossed_recently(df["macd_dif"], df["macd_dea"], window=5, up=True)
    if d >= 0:
        low_pos = _safe(last.get("macd_dif")) < 0
        buy.append({
            "name": "MACD 金叉" + ("（低位）" if low_pos else ""),
            "desc": f"DIF 于 {d} 天前上穿 DEA" + ("，且处于零轴下方，属于底部反转形态" if low_pos else "，动能转强"),
            "strength": 3 if low_pos else 2,
        })

    d = crossed_recently(df["kdj_k"], df["kdj_d"], window=3, up=True)
    if d >= 0 and _safe(last.get("kdj_k"), 50) < 40:
        buy.append({
            "name": "KDJ 超卖金叉",
            "desc": f"K 值 {_safe(last.get('kdj_k')):.1f} 处于低位并上穿 D 值，短线反弹信号",
            "strength": 2,
        })

    if len(df) >= 6:
        rsi_now, rsi_prev = _safe(last.get("rsi"), 50), _safe(df["rsi"].iloc[-5], 50)
        if rsi_prev < 32 and rsi_now > rsi_prev + 3:
            buy.append({
                "name": "RSI 超卖回升",
                "desc": f"RSI 由 {rsi_prev:.1f} 回升至 {rsi_now:.1f}，抛压衰竭",
                "strength": 2,
            })

    ma20 = _safe(last.get("ma20"), np.nan)
    if not math.isnan(ma20) and len(df) >= 3:
        prev_close = _safe(df["close"].iloc[-2])
        prev_ma20 = _safe(df["ma20"].iloc[-2], np.nan)
        if not math.isnan(prev_ma20) and prev_close <= prev_ma20 < close > ma20:
            buy.append({
                "name": "站上 20 日线",
                "desc": f"收盘价 {close:.2f} 重新站上 MA20（{ma20:.2f}），短期趋势修复",
                "strength": 2,
            })

    bp = _safe(last.get("boll_pos"), 0.5)
    if bp < 0.12 and _safe(last.get("rsi"), 50) < 45:
        buy.append({
            "name": "布林下轨支撑",
            "desc": "股价贴近布林下轨且 RSI 偏低，存在均值回归动力",
            "strength": 2,
        })

    d = crossed_recently(df["ma20"], df["ma60"], window=8, up=True)
    if d >= 0:
        buy.append({
            "name": "均线金三角",
            "desc": f"MA20 于 {d} 天前上穿 MA60，中期趋势由弱转强",
            "strength": 3,
        })

    # ---- 卖出信号
    d = crossed_recently(df["macd_dif"], df["macd_dea"], window=5, up=False)
    if d >= 0:
        high_pos = _safe(last.get("macd_dif")) > 0
        sell.append({
            "name": "MACD 死叉" + ("（高位）" if high_pos else ""),
            "desc": f"DIF 于 {d} 天前下穿 DEA" + ("，高位死叉需警惕回调" if high_pos else "，动能走弱"),
            "strength": 3 if high_pos else 2,
        })

    if _safe(last.get("rsi"), 50) > 76:
        sell.append({
            "name": "RSI 超买",
            "desc": f"RSI 达到 {_safe(last.get('rsi')):.1f}，短期过热",
            "strength": 2,
        })

    d = crossed_recently(df["kdj_k"], df["kdj_d"], window=3, up=False)
    if d >= 0 and _safe(last.get("kdj_k"), 50) > 78:
        sell.append({
            "name": "KDJ 高位死叉",
            "desc": f"K 值 {_safe(last.get('kdj_k')):.1f} 在超买区下穿 D 值",
            "strength": 2,
        })

    if not math.isnan(ma20) and len(df) >= 3:
        prev_close = _safe(df["close"].iloc[-2])
        prev_ma20 = _safe(df["ma20"].iloc[-2], np.nan)
        if not math.isnan(prev_ma20) and prev_close >= prev_ma20 > close < ma20:
            heavy = _safe(last.get("vol_ratio"), 1) > 1.3
            sell.append({
                "name": "跌破 20 日线" + ("（放量）" if heavy else ""),
                "desc": f"收盘 {close:.2f} 跌破 MA20（{ma20:.2f}）" + ("，且成交放大，离场信号较强" if heavy else ""),
                "strength": 3 if heavy else 2,
            })

    if bp > 0.96 and _safe(last.get("rsi"), 50) > 68:
        sell.append({
            "name": "触及布林上轨",
            "desc": "股价冲击布林上轨且 RSI 偏高，短期回落风险上升",
            "strength": 1,
        })

    dd = _safe(last.get("drawdown"))
    if dd < -22 and _safe(last.get("ma20"), np.nan) and close < _safe(last.get("ma60"), close):
        sell.append({
            "name": "中期趋势破位",
            "desc": f"较近一年高点回撤 {abs(dd):.1f}%，且位于 MA60 之下，趋势尚未修复",
            "strength": 2,
        })

    return {"buy": buy, "sell": sell}


def make_action(tech_score: float, signals: dict, df: pd.DataFrame) -> dict:
    """
    综合信号给出操作建议 + 关键价位。

    止损用 2 倍 ATR，这比"固定跌 8% 止损"更科学：波动大的股票给更宽的空间，
    波动小的股票收紧，避免被正常震荡洗出去。
    """
    last = df.iloc[-1]
    close = _safe(last["close"])
    atr = _safe(last.get("atr"), close * 0.02)
    ma20 = _safe(last.get("ma20"), close)
    boll_low = _safe(last.get("boll_low"), close * 0.95)
    boll_up = _safe(last.get("boll_up"), close * 1.05)

    buy_w = sum(s["strength"] for s in signals["buy"])
    sell_w = sum(s["strength"] for s in signals["sell"])
    net = buy_w - sell_w

    if sell_w >= 3 and net < 0:
        action, tone = "减仓 / 回避", "sell"
    elif net >= 4 and tech_score >= 55:
        action, tone = "重点关注买入", "buy"
    elif net >= 2:
        action, tone = "可分批建仓", "buy"
    elif net <= -2:
        action, tone = "谨慎观望", "sell"
    else:
        action, tone = "持有观察", "hold"

    # 买入参考区间：MA20 与布林下轨之间是典型的回踩支撑带
    zone_low = min(ma20, boll_low) if ma20 > 0 else boll_low
    zone_high = max(ma20, close * 0.995)
    if zone_low > zone_high:
        zone_low, zone_high = zone_high * 0.97, zone_high

    return {
        "action": action,
        "tone": tone,
        "buy_weight": buy_w,
        "sell_weight": sell_w,
        "buy_zone": [round(zone_low, 2), round(zone_high, 2)],
        "stop_loss": round(close - 2 * atr, 2),
        "stop_loss_pct": round(-2 * atr / close * 100, 1) if close else 0,
        "target": round(max(boll_up, close + 3 * atr), 2),
        "target_pct": round((max(boll_up, close + 3 * atr) / close - 1) * 100, 1) if close else 0,
    }


def composite(fund: dict, tech: dict, weight_fund: float = 0.5) -> float:
    """总分 = 基本面 × 权重 + 技术面 × (1 - 权重)"""
    return round(fund["total"] * weight_fund + tech["total"] * (1 - weight_fund), 1)


def grade(score: float) -> str:
    """按绝对分数评级。仅在无法计算分位时作为兜底"""
    if score >= 80:
        return "A+"
    if score >= 72:
        return "A"
    if score >= 64:
        return "B+"
    if score >= 56:
        return "B"
    if score >= 48:
        return "C+"
    return "C"


def assign_grades(stocks: list) -> None:
    """
    按候选池内的相对分位评级，原地写回每只股票的 grade 字段。

    为什么不用绝对分数：能走到这一步的股票都已经通过了四层基本面筛选，
    分数天然集中在高位，用固定阈值会出现"全部 A+"，评级就没有意义了。
    评级的作用是在**已经不错的池子里**继续区分优劣。

    但纯相对分位会有"矮子里拔将军"的问题，所以叠加一条绝对下限：
    综合分低于 55 的，最高只能给 C+。
    """
    if not stocks:
        return

    ranked = sorted(stocks, key=lambda x: x["score"], reverse=True)
    n = len(ranked)
    # 分位切点：前 10% / 25% / 45% / 68% / 86% / 其余
    bands = [(0.10, "A+"), (0.25, "A"), (0.45, "B+"), (0.68, "B"), (0.86, "C+")]

    for i, s in enumerate(ranked):
        pct = (i + 1) / n
        g = "C"
        for cut, label in bands:
            if pct <= cut:
                g = label
                break
        # 绝对分数兜底，防止整体质量差时虚高
        if s["score"] < 55 and g in ("A+", "A", "B+"):
            g = "C+"
        elif s["score"] < 65 and g in ("A+", "A"):
            g = "B+"
        s["grade"] = g
        s["percentile"] = round((1 - pct) * 100, 1)
