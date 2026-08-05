"""
数据源封装层

三个数据源各司其职，都是公开免费接口：

  行情报价  腾讯行情 qt.gtimg.cn        —— 支持批量，一次几百只，拿现价 / PE / PB / 市值
  日 K 线   东财 push2his.eastmoney.com —— 前复权日线
  财报分红  akshare（东财财报接口）      —— 分红送配、业绩报表、资产负债表

之所以不用 akshare 的 stock_zh_a_spot_em 和 stock_zh_a_hist：这两个接口打的是
push2.eastmoney.com，实测容易触发限流后直接断连（RemoteDisconnected）。
财报类接口走的是另一套域名，稳定得多，所以保留。
"""

import os
import time
import functools
from datetime import datetime, timedelta

import requests
import pandas as pd

import akshare as ak

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CACHE_TTL = {"spot": 2, "fhps": 168, "yjbb": 168, "zcfz": 168}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
HEADERS = {"User-Agent": UA}

_session = requests.Session()
_session.headers.update(HEADERS)


# ---------------------------------------------------------------- 通用工具


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, key.replace("/", "_").replace(":", "_") + ".pkl")


def cached(kind: str):
    """带过期时间的本地缓存。财报一个季度才变一次，没必要反复拉"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if os.getenv("DR_NO_CACHE") == "1":
                return fn(*args, **kwargs)
            key = f"{kind}_{fn.__name__}_{'_'.join(map(str, args))}"
            path = _cache_path(key)
            if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL.get(kind, 24) * 3600:
                try:
                    return pd.read_pickle(path)
                except Exception:
                    pass
            df = fn(*args, **kwargs)
            if df is not None and len(df):
                try:
                    df.to_pickle(path)
                except Exception:
                    pass
            return df
        return wrapper
    return deco


def retry(times: int = 3, delay: float = 1.5):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for i in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last = e
                    if i < times - 1:
                        time.sleep(delay * (i + 1))
            print(f"  [warn] {fn.__name__}{str(args)[:40]} 失败: {type(last).__name__}")
            return None
        return wrapper
    return deco


def pick_col(df: pd.DataFrame, *keywords, exclude=()):
    """模糊匹配列名。akshare 的列名会随版本变化，硬编码迟早出事"""
    if df is None:
        return None
    for col in df.columns:
        c = str(col)
        if all(k in c for k in keywords) and not any(x in c for x in exclude):
            return col
    return None


def to_num(s):
    if s is None:
        return None
    if hasattr(s, "dtype") and s.dtype.kind in "if":
        return s
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip(),
        errors="coerce",
    )


def market_prefix(code: str) -> str:
    """代码 → 交易所前缀"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def secid(code: str) -> str:
    """代码 → 东财 secid（1 = 沪，0 = 深）"""
    return ("1." if code.startswith("6") else "0.") + code


# ---------------------------------------------------------------- 批量行情


# 腾讯行情返回的 88 段字段中我们关心的位置
TENCENT_FIELDS = {
    "name": 1, "code": 2, "price": 3, "prev_close": 4,
    "change_pct": 32, "high": 33, "low": 34,
    "turnover": 38, "pe": 39, "float_mktcap": 44, "mktcap": 45, "pb": 46,
}


@retry(times=3, delay=2.0)
def _fetch_quote_batch(codes: list) -> list:
    """一次拉一批报价。腾讯这个接口单次 200 只左右很稳"""
    query = ",".join(market_prefix(c) + c for c in codes)
    r = requests.get(f"https://qt.gtimg.cn/q={query}", headers=HEADERS, timeout=25)
    r.encoding = "gbk"
    rows = []
    for line in r.text.strip().split("\n"):
        if '="' not in line:
            continue
        parts = line.split('~')
        if len(parts) < 50:
            continue
        rec = {}
        for k, idx in TENCENT_FIELDS.items():
            v = parts[idx].strip()
            if k in ("name", "code"):
                rec[k] = v
            else:
                try:
                    rec[k] = float(v)
                except (ValueError, TypeError):
                    rec[k] = None
        if rec.get("code") and rec.get("price"):
            rows.append(rec)
    return rows


def get_quotes(codes: list, batch_size: int = 150, verbose: bool = True) -> pd.DataFrame:
    """
    批量获取报价。返回 code / name / price / change_pct / pe / pb / mktcap（亿元）/ turnover

    市值单位是亿元，这是腾讯接口的原生单位，不做换算免得来回出错。
    """
    codes = list(dict.fromkeys(codes))
    out = []
    total_batches = (len(codes) + batch_size - 1) // batch_size

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        rows = _fetch_quote_batch(batch)
        if rows:
            out.extend(rows)
        if verbose and (i // batch_size + 1) % 5 == 0:
            print(f"    报价进度 {i // batch_size + 1}/{total_batches} 批，累计 {len(out)} 条", flush=True)
        time.sleep(0.25)

    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df.drop_duplicates("code").reset_index(drop=True)


# ---------------------------------------------------------------- 日 K 线


def _kline_tencent(code: str, bars: int) -> pd.DataFrame:
    """
    腾讯前复权日线。返回格式 [日期, 开, 收, 高, 低, 成交量(手)]

    高股息股每年除息，不做前复权的话技术指标会在除息日出现假跳空，
    均线、MACD 全部失真，所以必须取 qfq。
    """
    sym = market_prefix(code) + code
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{bars},qfq"
    r = requests.get(url, headers=HEADERS, timeout=25)
    node = (r.json().get("data") or {}).get(sym) or {}
    arr = node.get("qfqday") or node.get("day")
    if not arr:
        return None

    rows = []
    for p in arr:
        if len(p) < 6:
            continue
        try:
            rows.append({
                "date": p[0],
                "open": float(p[1]), "close": float(p[2]),
                "high": float(p[3]), "low": float(p[4]),
                "volume": float(p[5]),
            })
        except (ValueError, TypeError):
            continue
    return pd.DataFrame(rows) if rows else None


def _kline_eastmoney(code: str, bars: int) -> pd.DataFrame:
    """东财前复权日线，作为腾讯不可用时的备选源"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid(code)}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f61"
        f"&klt=101&fqt=1&end=20500101&lmt={bars}"
    )
    r = requests.get(url, headers=HEADERS, timeout=25)
    data = r.json().get("data")
    if not data or not data.get("klines"):
        return None
    rows = []
    for line in data["klines"]:
        p = line.split(",")
        if len(p) < 6:
            continue
        rows.append({
            "date": p[0],
            "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]),
        })
    return pd.DataFrame(rows) if rows else None


def get_kline(code: str, bars: int = 400) -> pd.DataFrame:
    """
    获取前复权日线，腾讯优先、东财兜底。

    两个源都用独立的 requests.get 而非共享 Session——实测共享 Session 在多线程下
    容易被服务端判定为异常连接直接断开。
    """
    for fetcher in (_kline_tencent, _kline_eastmoney):
        for attempt in range(2):
            try:
                df = fetcher(code, bars)
                if df is not None and len(df) >= 120:
                    return df
                break                       # 拿到了但数据太短（次新股），换源也没用
            except Exception:
                time.sleep(0.8 * (attempt + 1))
    return None


# ---------------------------------------------------------------- 分红数据


@cached("fhps")
@retry()
def get_dividend(period: str) -> pd.DataFrame:
    """
    某报告期分红送配。period 形如 '20251231'。

    东财原始字段"现金分红-现金分红比例"是每 10 股派息金额，要除以 10 得到每股股利。
    """
    df = ak.stock_fhps_em(date=period)
    if df is None or not len(df):
        return None

    code_col = pick_col(df, "代码")
    cash_col = pick_col(df, "现金分红", "比例", exclude=("股息",)) or pick_col(df, "派息")
    if code_col is None or cash_col is None:
        return None

    out = pd.DataFrame()
    out["code"] = df[code_col].astype(str).str.zfill(6)
    out["dps"] = to_num(df[cash_col]) / 10.0

    for kws, dst in ((("每股收益",), "eps"), (("总股本",), "total_share")):
        col = pick_col(df, *kws)
        if col is not None:
            out[dst] = to_num(df[col])

    out = out[out["dps"].notna() & (out["dps"] > 0)]
    # 同一报告期可能有预案 / 实施多条记录，取金额最大的
    return out.sort_values("dps", ascending=False).drop_duplicates("code").reset_index(drop=True)


# ---------------------------------------------------------------- 财务数据


@cached("yjbb")
@retry()
def get_performance(period: str) -> pd.DataFrame:
    """业绩报表：ROE、净利同比、营收同比、每股净资产、所处行业"""
    df = ak.stock_yjbb_em(date=period)
    if df is None or not len(df):
        return None

    out = pd.DataFrame()
    out["code"] = df[pick_col(df, "代码")].astype(str).str.zfill(6)
    name_col = pick_col(df, "简称") or pick_col(df, "名称")
    if name_col is not None:
        out["name_fin"] = df[name_col].astype(str)

    mapping = {
        "roe": ("净资产收益率",),
        "profit_yoy": ("净利润", "同比"),
        "revenue_yoy": ("营业总收入", "同比"),
        "eps_report": ("每股收益",),
        "bps": ("每股净资产",),
        "gross_margin": ("销售毛利率",),
    }
    for dst, kws in mapping.items():
        col = pick_col(df, *kws)
        if col is not None:
            out[dst] = to_num(df[col])

    ind_col = pick_col(df, "行业")
    out["industry"] = df[ind_col].astype(str) if ind_col is not None else "未分类"
    return out.drop_duplicates("code").reset_index(drop=True)


@cached("zcfz")
@retry()
def get_balance(period: str) -> pd.DataFrame:
    """资产负债表：取资产负债率"""
    df = ak.stock_zcfz_em(date=period)
    if df is None or not len(df):
        return None
    col = pick_col(df, "资产负债率")
    if col is None:
        return None
    out = pd.DataFrame()
    out["code"] = df[pick_col(df, "代码")].astype(str).str.zfill(6)
    out["debt_ratio"] = to_num(df[col])
    return out.drop_duplicates("code").reset_index(drop=True)


# ---------------------------------------------------------------- 报告期


def recent_annual_periods(n: int = 5) -> list:
    """
    最近 n 个年报报告期，按时间倒序。

    年报在次年 4 月底前披露完毕，所以 5 月之前不把去年的年报期算进来，
    否则会大面积拿到空数据。
    """
    today = datetime.now()
    latest_year = today.year - 1 if today.month >= 5 else today.year - 2
    return [f"{latest_year - i}1231" for i in range(n)]
