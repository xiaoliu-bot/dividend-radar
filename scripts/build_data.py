"""
高股息雷达 · 数据管道主程序

执行顺序：
  1. 拉近 5 年年报分红 → 构建分红股票池，算连续分红年数
  2. 拉业绩报表与资产负债表 → 盈利质量 / 财务安全过滤（先做，因为不用打行情接口，很快）
  3. 批量拉实时报价 → 算股息率，做股息率与市值过滤
  4. 对最终候选逐只拉日线 → 技术指标、评分、买卖信号
  5. 输出 web/data/stocks.json

先用财务数据把池子缩小，再打行情接口，可以把请求量降一个数量级。

用法：
  python build_data.py                 # 完整运行
  python build_data.py --limit 40      # 只分析前 40 只，调试用
  python build_data.py --top 50        # 输出 Top 50
"""

import os
import sys
import json
import argparse
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datasource as ds
from indicators import compute_all
from scoring import (
    score_fundamental, score_technical, detect_signals,
    make_action, composite, grade, assign_grades, _safe,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "web", "data")
os.makedirs(OUT_DIR, exist_ok=True)

# ------------------------------------------------------------------ 筛选参数
CRITERIA = {
    "min_dividend_yield": 3.0,    # 最低股息率 %
    "min_dividend_years": 3,      # 最少连续分红年数
    "min_roe": 8.0,               # 最低净资产收益率 %
    "max_debt_ratio": 75.0,       # 最高资产负债率 %（金融地产另行放宽）
    "min_payout": 15.0,           # 最低分红率 %
    "max_payout": 85.0,           # 最高分红率 %，超过说明分红不可持续
    "min_mktcap": 50.0,           # 最低总市值（亿元），规避流动性风险
    "min_profit_yoy": -35.0,      # 净利润同比下限
}

# 金融地产负债率天然高，单独放宽
HIGH_DEBT_INDUSTRIES = ("银行", "保险", "证券", "多元金融", "房地产")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ 分红池


def build_dividend_table(periods: list) -> pd.DataFrame:
    """
    汇总多个年报期的分红，得出：
      dps_latest       最近一个分红年度的每股股利
      dividend_years   连续分红年数（从最近年度往前数，断档即停）
      eps_latest       对应年度每股收益，用于算分红率
    """
    frames = {}
    for p in periods:
        d = ds.get_dividend(p)
        if d is not None and len(d):
            frames[p] = d.set_index("code")
            log(f"  {p[:4]} 年报分红：{len(d)} 家")
        else:
            log(f"  {p[:4]} 年报分红：无数据")

    if not frames:
        return pd.DataFrame()

    ordered = [p for p in periods if p in frames]
    all_codes = sorted(set().union(*[set(f.index) for f in frames.values()]))

    rows = []
    for code in all_codes:
        dps_latest, eps_latest, years, started = np.nan, np.nan, 0, False
        for p in ordered:
            f = frames[p]
            if code in f.index:
                rec = f.loc[code]
                dps = float(rec["dps"]) if pd.notna(rec["dps"]) else 0.0
                if dps > 0:
                    if not started:
                        dps_latest = dps
                        eps_latest = float(rec["eps"]) if "eps" in f.columns and pd.notna(rec.get("eps")) else np.nan
                        started = True
                    years += 1
                    continue
            if started:
                break     # 连续性断了就停止计数
        if started:
            rows.append({
                "code": code,
                "dps_latest": dps_latest,
                "dividend_years": years,
                "eps_latest": eps_latest,
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 各层筛选


def layer1_clean(df: pd.DataFrame) -> pd.DataFrame:
    """基础清洗：只留沪深主板 / 创业板 / 科创板，剔除 ST"""
    before = len(df)
    df = df[df["code"].str.match(r"^(60|00|30|68)")]
    if "name_fin" in df:
        df = df[~df["name_fin"].str.contains("ST|退|PT", case=False, na=False)]
    log(f"第 1 层 基础清洗：{before} → {len(df)} 只")
    return df.reset_index(drop=True)


def layer3_consistency(df: pd.DataFrame) -> pd.DataFrame:
    """分红持续性 + 分红率健康度"""
    before = len(df)
    df = df[df["dividend_years"] >= CRITERIA["min_dividend_years"]]

    df = df.copy()
    df["payout_ratio"] = np.where(
        df["eps_latest"].fillna(0) > 0,
        df["dps_latest"] / df["eps_latest"] * 100,
        np.nan,
    )
    ok = df["payout_ratio"].isna() | (
        (df["payout_ratio"] >= CRITERIA["min_payout"]) & (df["payout_ratio"] <= CRITERIA["max_payout"])
    )
    df = df[ok]
    log(f"第 3 层 连续分红 ≥ {CRITERIA['min_dividend_years']} 年 + 分红率 {CRITERIA['min_payout']}-{CRITERIA['max_payout']}%：{before} → {len(df)} 只")
    return df.reset_index(drop=True)


def layer4_quality(df: pd.DataFrame) -> pd.DataFrame:
    """盈利质量与财务安全"""
    before = len(df)
    if "industry" not in df:
        df["industry"] = "未分类"
    df["industry"] = df["industry"].fillna("未分类")

    if "roe" in df:
        df = df[df["roe"].fillna(-99) >= CRITERIA["min_roe"]]
    if "profit_yoy" in df:
        df = df[df["profit_yoy"].fillna(0) >= CRITERIA["min_profit_yoy"]]
    if "debt_ratio" in df:
        is_fin = df["industry"].str.contains("|".join(HIGH_DEBT_INDUSTRIES), na=False)
        df = df[is_fin | (df["debt_ratio"].fillna(0) <= CRITERIA["max_debt_ratio"])]

    log(f"第 4 层 ROE ≥ {CRITERIA['min_roe']}% + 财务安全：{before} → {len(df)} 只")
    return df.reset_index(drop=True)


def layer2_yield(df: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    """
    股息率门槛。放在拿到报价之后才能算，
    但过滤逻辑上属于第 2 层——先有股息率，才谈得上高股息。
    """
    m = df.merge(quotes, on="code", how="inner")
    m["dividend_yield"] = m["dps_latest"] / m["price"] * 100

    before = len(m)
    m = m[m["mktcap"].fillna(0) >= CRITERIA["min_mktcap"]]
    log(f"      市值 ≥ {CRITERIA['min_mktcap']:.0f} 亿：{before} → {len(m)} 只")

    before = len(m)
    m = m[m["dividend_yield"] >= CRITERIA["min_dividend_yield"]]
    log(f"第 2 层 股息率 ≥ {CRITERIA['min_dividend_yield']}%：{before} → {len(m)} 只")
    return m.reset_index(drop=True)


# ------------------------------------------------------------------ 技术分析


def analyze_one(rec: dict) -> dict:
    """单只股票的完整技术面分析：拉 K 线 → 算指标 → 评分 → 出信号"""
    k = ds.get_kline(rec["code"], bars=400)
    if k is None or len(k) < 120:
        return None

    df = compute_all(k)
    last = df.iloc[-1]

    fund = score_fundamental(rec)
    tech = score_technical(df)
    signals = detect_signals(df)
    action = make_action(tech["total"], signals, df)
    total = composite(fund, tech)

    tail = df.tail(120)
    kline = [
        [r["date"], round(_safe(r["open"]), 2), round(_safe(r["close"]), 2),
         round(_safe(r["low"]), 2), round(_safe(r["high"]), 2), int(_safe(r["volume"]))]
        for _, r in tail.iterrows()
    ]
    ma_series = {
        f"ma{w}": [None if pd.isna(v) else round(float(v), 2) for v in tail[f"ma{w}"]]
        for w in (5, 20, 60) if f"ma{w}" in tail
    }

    return {
        "code": rec["code"],
        "name": rec.get("name") or rec.get("name_fin") or rec["code"],
        "industry": rec.get("industry", "未分类"),
        "price": round(_safe(rec.get("price")), 2),
        "change_pct": round(_safe(rec.get("change_pct")), 2),

        "dividend_yield": round(_safe(rec.get("dividend_yield")), 2),
        "dividend_years": int(_safe(rec.get("dividend_years"))),
        "dps": round(_safe(rec.get("dps_latest")), 3),
        "payout_ratio": round(_safe(rec.get("payout_ratio")), 1),
        "roe": round(_safe(rec.get("roe")), 2),
        "pe": round(_safe(rec.get("pe")), 2),
        "pb": round(_safe(rec.get("pb")), 2),
        "debt_ratio": round(_safe(rec.get("debt_ratio")), 1),
        "mktcap": round(_safe(rec.get("mktcap")), 1),
        "profit_yoy": round(_safe(rec.get("profit_yoy")), 1),

        "score": total,
        "grade": grade(total),
        "score_fund": fund,
        "score_tech": tech,
        "signals": signals,
        "action": action,

        "indicators": {
            "ma5": round(_safe(last.get("ma5")), 2),
            "ma20": round(_safe(last.get("ma20")), 2),
            "ma60": round(_safe(last.get("ma60")), 2),
            "ma120": round(_safe(last.get("ma120")), 2),
            "macd_dif": round(_safe(last.get("macd_dif")), 3),
            "macd_dea": round(_safe(last.get("macd_dea")), 3),
            "macd_hist": round(_safe(last.get("macd_hist")), 3),
            "rsi": round(_safe(last.get("rsi")), 1),
            "kdj_k": round(_safe(last.get("kdj_k")), 1),
            "kdj_d": round(_safe(last.get("kdj_d")), 1),
            "kdj_j": round(_safe(last.get("kdj_j")), 1),
            "boll_up": round(_safe(last.get("boll_up")), 2),
            "boll_mid": round(_safe(last.get("boll_mid")), 2),
            "boll_low": round(_safe(last.get("boll_low")), 2),
            "atr_pct": round(_safe(last.get("atr_pct")), 2),
            "vol_ratio": round(_safe(last.get("vol_ratio")), 2),
            "ret20": round(_safe(last.get("ret20")), 2),
            "ret60": round(_safe(last.get("ret60")), 2),
            "drawdown": round(_safe(last.get("drawdown")), 2),
        },
        "kline": kline,
        "ma_series": ma_series,
        "last_date": str(last["date"]),
    }


def layer5_technical(df: pd.DataFrame, workers: int = 5) -> list:
    """并发做技术面分析。并发别开太高，行情接口会限流"""
    records = df.to_dict("records")
    results, done, failed = [], 0, 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze_one, r): r for r in records}
        for fut in as_completed(futures):
            done += 1
            try:
                res = fut.result()
                if res:
                    results.append(res)
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"  [error] {futures[fut]['code']}: {type(e).__name__} {e}")
            if done % 25 == 0 or done == len(records):
                log(f"    进度 {done}/{len(records)}（成功 {len(results)}，跳过 {failed}）")

    log(f"第 5 层 技术面分析：{len(results)} 只有效")
    return results


# ------------------------------------------------------------------ 主流程


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="限制进入技术分析的股票数（调试用）")
    ap.add_argument("--top", type=int, default=40, help="最终输出数量")
    ap.add_argument("--workers", type=int, default=5, help="并发线程数")
    args = ap.parse_args()

    log("=" * 58)
    log("高股息雷达 · 开始构建数据")
    log("=" * 58)

    # --- 分红池
    periods = ds.recent_annual_periods(5)
    log(f"分红考察区间：{periods[-1][:4]} — {periods[0][:4]} 年报")
    div = build_dividend_table(periods)
    if not len(div):
        log("分红数据为空，终止")
        sys.exit(1)
    log(f"有连续分红记录的公司：{len(div)} 家")

    # --- 财务数据
    latest = periods[0]
    log(f"拉取 {latest[:4]} 年报财务数据…")
    perf = ds.get_performance(latest)
    bal = ds.get_balance(latest)
    log(f"  业绩表 {0 if perf is None else len(perf)} 条，资产负债表 {0 if bal is None else len(bal)} 条")

    pool = div
    if perf is not None:
        pool = pool.merge(perf, on="code", how="left")
    if bal is not None:
        pool = pool.merge(bal, on="code", how="left")

    # --- 逐层过滤（先做不需要行情的部分）
    pool = layer1_clean(pool)
    pool = layer3_consistency(pool)
    pool = layer4_quality(pool)

    if not len(pool):
        log("财务筛选后无标的，请放宽条件")
        sys.exit(1)

    # --- 批量报价
    log(f"批量拉取 {len(pool)} 只候选的实时报价…")
    quotes = ds.get_quotes(pool["code"].tolist())
    if quotes is None or not len(quotes):
        log("报价获取失败，终止")
        sys.exit(1)
    log(f"  获得 {len(quotes)} 条报价")

    pool = layer2_yield(pool, quotes)
    if not len(pool):
        log("股息率筛选后无标的，请放宽条件")
        sys.exit(1)

    # 按股息率粗排，优先分析回报最高的
    pool = pool.sort_values("dividend_yield", ascending=False).reset_index(drop=True)
    if args.limit:
        pool = pool.head(args.limit)
        log(f"调试模式：仅分析前 {len(pool)} 只")

    log(f"开始技术面分析（{len(pool)} 只，{args.workers} 并发）…")
    stocks = layer5_technical(pool, workers=args.workers)
    if not stocks:
        log("技术面分析全部失败，终止")
        sys.exit(1)

    # 评级在完整候选池上算分位，这样 Top N 的评级反映的是它在整个池子里的真实位置
    assign_grades(stocks)
    stocks.sort(key=lambda x: x["score"], reverse=True)
    top = stocks[:args.top]

    buy_list = [s for s in top if s["action"]["tone"] == "buy"]
    sell_list = [s for s in top if s["action"]["tone"] == "sell"]

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": max(s["last_date"] for s in top),
        "criteria": CRITERIA,
        "stats": {
            "universe": len(div),
            "passed_screen": len(stocks),
            "output": len(top),
            "buy_count": len(buy_list),
            "sell_count": len(sell_list),
            "avg_yield": round(sum(s["dividend_yield"] for s in top) / len(top), 2),
            "avg_score": round(sum(s["score"] for s in top) / len(top), 1),
        },
        "industries": sorted({s["industry"] for s in top}),
        "stocks": top,
    }

    out_path = os.path.join(OUT_DIR, "stocks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    log("=" * 58)
    log(f"完成：输出 {len(top)} 只，文件 {os.path.getsize(out_path) / 1024:.0f} KB")
    log(f"  买入信号 {len(buy_list)} 只 / 卖出信号 {len(sell_list)} 只")
    log(f"  平均股息率 {payload['stats']['avg_yield']}% / 平均评分 {payload['stats']['avg_score']}")
    log(f"  → {out_path}")
    log("=" * 58)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
