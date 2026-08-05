"""
技术指标计算模块

纯 pandas / numpy 实现，不依赖 TA-Lib（避免 GitHub Actions 上编译 C 扩展的麻烦）。
KDJ / RSI / ATR 均采用通达信口径的 SMA 平滑，保证和行情软件显示的数值一致。
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=span, adjust=False).mean()


def sma_cn(series: pd.Series, n: int, m: int) -> pd.Series:
    """
    通达信口径的 SMA(X, N, M)：Y = (M * X + (N - M) * Y_prev) / N

    等价于 alpha = M / N 的指数加权平均。国内行情软件的 KDJ、RSI 都用这个，
    直接用 pandas 的 rolling.mean() 算出来的数值会和软件对不上。
    """
    return series.ewm(alpha=m / n, adjust=False).mean()


def add_ma(df: pd.DataFrame, windows=(5, 10, 20, 60, 120, 250)) -> pd.DataFrame:
    """均线系统"""
    for w in windows:
        df[f"ma{w}"] = df["close"].rolling(w, min_periods=max(2, w // 3)).mean()
    return df


def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD：DIF 快慢线之差，DEA 为 DIF 的信号线，HIST 为柱状体（放大 2 倍，国内习惯）"""
    dif = ema(df["close"], fast) - ema(df["close"], slow)
    dea = ema(dif, signal)
    df["macd_dif"] = dif
    df["macd_dea"] = dea
    df["macd_hist"] = (dif - dea) * 2
    return df


def add_kdj(df: pd.DataFrame, n=9, m1=3, m2=3) -> pd.DataFrame:
    """KDJ 随机指标"""
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    span = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["close"] - low_n) / span * 100).fillna(50)
    k = sma_cn(rsv, m1, 1)
    d = sma_cn(k, m2, 1)
    df["kdj_k"] = k
    df["kdj_d"] = d
    df["kdj_j"] = 3 * k - 2 * d
    return df


def add_rsi(df: pd.DataFrame, n=14) -> pd.DataFrame:
    """RSI 相对强弱指标"""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = sma_cn(gain, n, 1)
    avg_loss = sma_cn(loss, n, 1)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50)
    return df


def add_boll(df: pd.DataFrame, n=20, k=2) -> pd.DataFrame:
    """布林带。boll_width 是带宽百分比，用来判断是否处于低波动收敛状态"""
    mid = df["close"].rolling(n, min_periods=max(2, n // 3)).mean()
    std = df["close"].rolling(n, min_periods=max(2, n // 3)).std(ddof=0)
    df["boll_mid"] = mid
    df["boll_up"] = mid + k * std
    df["boll_low"] = mid - k * std
    df["boll_width"] = (df["boll_up"] - df["boll_low"]) / mid.replace(0, np.nan) * 100
    # 价格在布林带中的相对位置，0 = 下轨，1 = 上轨
    rng = (df["boll_up"] - df["boll_low"]).replace(0, np.nan)
    df["boll_pos"] = ((df["close"] - df["boll_low"]) / rng).clip(0, 1)
    return df


def add_atr(df: pd.DataFrame, n=14) -> pd.DataFrame:
    """ATR 真实波幅。用于计算止损距离，比固定百分比止损更贴合个股波动特性"""
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = sma_cn(tr, n, 1)
    df["atr_pct"] = df["atr"] / df["close"].replace(0, np.nan) * 100
    return df


def add_volume(df: pd.DataFrame) -> pd.DataFrame:
    """量能指标"""
    df["vol_ma5"] = df["volume"].rolling(5, min_periods=1).mean()
    df["vol_ma20"] = df["volume"].rolling(20, min_periods=1).mean()
    df["vol_ma60"] = df["volume"].rolling(60, min_periods=1).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma5"].replace(0, np.nan)
    df["vol_trend"] = df["vol_ma5"] / df["vol_ma20"].replace(0, np.nan)
    return df


def add_returns(df: pd.DataFrame) -> pd.DataFrame:
    """区间涨跌幅，用于动量与回撤观察"""
    close = df["close"]
    for period, name in ((5, "ret5"), (20, "ret20"), (60, "ret60"), (250, "ret250")):
        df[name] = close.pct_change(period) * 100
    # 距离近 250 日最高点的回撤
    high250 = close.rolling(250, min_periods=20).max()
    df["drawdown"] = (close / high250 - 1) * 100
    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    一次性算完所有指标。

    入参 df 需要包含列：date, open, high, low, close, volume
    """
    df = df.sort_values("date").reset_index(drop=True).copy()
    add_ma(df)
    add_macd(df)
    add_kdj(df)
    add_rsi(df)
    add_boll(df)
    add_atr(df)
    add_volume(df)
    add_returns(df)
    return df


def cross_up(fast: pd.Series, slow: pd.Series, idx: int = -1) -> bool:
    """判断 idx 位置是否发生金叉（fast 上穿 slow）"""
    try:
        return bool(fast.iloc[idx - 1] <= slow.iloc[idx - 1] and fast.iloc[idx] > slow.iloc[idx])
    except (IndexError, TypeError):
        return False


def cross_down(fast: pd.Series, slow: pd.Series, idx: int = -1) -> bool:
    """判断 idx 位置是否发生死叉（fast 下穿 slow）"""
    try:
        return bool(fast.iloc[idx - 1] >= slow.iloc[idx - 1] and fast.iloc[idx] < slow.iloc[idx])
    except (IndexError, TypeError):
        return False


def crossed_recently(fast: pd.Series, slow: pd.Series, window: int = 5, up: bool = True) -> int:
    """
    最近 window 根 K 线内是否交叉过，返回距今天数（0 = 今天，-1 = 未发生）。
    金叉信号往往不会当天就追，允许看回溯几天更实用。
    """
    n = len(fast)
    for back in range(window):
        idx = n - 1 - back
        if idx < 1:
            break
        hit = cross_up(fast, slow, idx) if up else cross_down(fast, slow, idx)
        if hit:
            return back
    return -1
