"""
定期定額（DCA）投資分析 → Excel 報表

功能：
  - 使用者輸入每月投入金額和每月第幾個交易日投資
  - 依各投資組合比例攤分至各標的
  - 依實際股價計算定期定額的總報酬率與不同期長的年化報酬率（含 IRR）
  - 依 DB 內的季度追蹤差額，估算追蹤誤差帶來的報酬率影響

執行方式：
  python scripts/dca_analysis.py
  python scripts/dca_analysis.py --monthly 5000 --day 5
  python scripts/dca_analysis.py --monthly 5000 --day 5 --output reports/dca_2026.xlsx
"""
import sys
import argparse
from pathlib import Path
from datetime import date
from collections import defaultdict, Counter

import pandas as pd
import numpy as np
import yaml
import duckdb
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import DB_PATH

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
REPORTS_DIR = Path(__file__).parent.parent / "reports"

# 分析時間區間
PERIODS = [
    ("最長期間", None),
    ("15年",     relativedelta(years=15)),
    ("10年",     relativedelta(years=10)),
    ("5年",      relativedelta(years=5)),
    ("3年",      relativedelta(years=3)),
    ("1年",      relativedelta(years=1)),
    ("6個月",    relativedelta(months=6)),
    ("3個月",    relativedelta(months=3)),
    ("1個月",    relativedelta(months=1)),
]

# Excel 色系
COLOR_HEADER  = "1F3864"
COLOR_SUBHEAD = "2F5496"
COLOR_PERIOD  = "D6E4F0"
COLOR_POS     = "C6EFCE"
COLOR_NEG     = "FFC7CE"
COLOR_ALT     = "F2F7FB"


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="定期定額投資分析 → Excel 報表")
    parser.add_argument("--monthly", type=float, help="每月固定投入金額（TWD）")
    parser.add_argument("--day",     type=int,   help="每月第幾個交易日投資（1~20）")
    parser.add_argument("--output",  type=str,   help="輸出 Excel 路徑")
    args = parser.parse_args()

    monthlyAmount = args.monthly
    if monthlyAmount is None:
        while True:
            try:
                val = input("請輸入每月固定投入金額（TWD，例如 5000）：").strip()
                monthlyAmount = float(val.replace(",", ""))
                if monthlyAmount > 0:
                    break
                print("  金額必須大於 0，請重新輸入。")
            except ValueError:
                print("  請輸入有效的數字。")

    nthDay = args.day
    if nthDay is None:
        while True:
            try:
                val = input("請輸入每月第幾個交易日投資（1~20，例如 5）：").strip()
                nthDay = int(val)
                if 1 <= nthDay <= 20:
                    break
                print("  請輸入 1 到 20 之間的整數。")
            except ValueError:
                print("  請輸入有效的整數。")

    REPORTS_DIR.mkdir(exist_ok=True)
    outputPath = Path(args.output) if args.output else (
        REPORTS_DIR / f"dca_analysis_{date.today()}.xlsx"
    )

    config = loadConfig()
    if not Path(DB_PATH).exists():
        print("⚠ 資料庫不存在，請先執行 scripts/fetch_initial.py")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        etfInfoMap = buildEtfInfoMap(conn, config)
        today = getLatestDate(conn)
        if today is None:
            print("⚠ 資料庫無價格資料")
            sys.exit(1)
        earliestDate = getEarliestDate(conn)

        print(f"\n資料範圍：{earliestDate} → {today}")
        print(f"每月投入：{monthlyAmount:,.0f} TWD，每月第 {nthDay} 個交易日")
        print(f"分析 {len(config['portfolios'])} 個投資組合 × {len(PERIODS)} 個時間區間\n")

        allTickers = [e["ticker"] for e in config["etfs"]]
        pricesTwd  = loadPricesTwd(conn, allTickers, earliestDate, today)
        trackingDf = loadTrackingDiffs(conn, allTickers)
        dividendsDf = loadDividendsRaw(conn, allTickers)
        fxSeries    = loadFxSeries(conn, earliestDate, today)

        print(f"配息資料：{len(dividendsDf)} 筆（{dividendsDf['ticker'].nunique() if not dividendsDf.empty else 0} 支 ETF）")
        divCalendars = buildDividendCalendars(allTickers, dividendsDf, fxSeries, earliestDate, today)

        feeConfig = config.get("dca", {}).get("fees", None)
        if feeConfig:
            tw = feeConfig.get("tw", {})
            us = feeConfig.get("us", {})
            print(f"手續費設定：台股 {tw.get('fee_twd', 0)} TWD/筆（最低 {tw.get('min_investment_twd', 0)} TWD）｜"
                  f"美股 {us.get('fee_usd', 0)} USD/筆（最低 {us.get('min_investment_usd', 0)} USD）")

        allResults    = []
        allDivResults = []
        for portfolio in config["portfolios"]:
            print(f"  計算：{portfolio['name']} ...", end=" ", flush=True)
            result = analyzePortfolio(
                portfolio, etfInfoMap, pricesTwd, trackingDf,
                monthlyAmount, nthDay, today, earliestDate,
                feeConfig=feeConfig, fxSeries=fxSeries,
            )
            allResults.append(result)

            divResult = analyzePortfolioWithDividends(
                portfolio, pricesTwd, divCalendars,
                monthlyAmount, nthDay, today, earliestDate,
                etfInfoMap=etfInfoMap, feeConfig=feeConfig, fxSeries=fxSeries,
            )
            allDivResults.append(divResult)
            print("完成")

        print()
    finally:
        conn.close()

    exportToExcel(allResults, allDivResults, divCalendars, config, outputPath, monthlyAmount, nthDay)
    print(f"報表已輸出至：{outputPath}")


# ─────────────────────────────────────────────────────────────
# 資料載入
# ─────────────────────────────────────────────────────────────

def loadConfig() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def buildEtfInfoMap(conn, config: dict) -> dict:
    dbRows = conn.execute(
        "SELECT ticker, name, exchange, currency, expense_ratio FROM etf_info"
    ).fetchall()
    dbMap = {r[0]: {"name": r[1], "exchange": r[2], "currency": r[3], "expense_ratio": r[4]}
             for r in dbRows}
    result = {}
    for etf in config["etfs"]:
        ticker = etf["ticker"]
        db = dbMap.get(ticker, {})
        result[ticker] = {
            "name":          etf.get("name") or db.get("name") or ticker,
            "exchange":      etf.get("exchange", db.get("exchange", "")),
            "currency":      "TWD" if etf.get("exchange") in ("TWSE", "TWO") else "USD",
            "expense_ratio": etf.get("expense_ratio") or db.get("expense_ratio") or 0.0,
        }
    return result


def getLatestDate(conn) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM etf_prices").fetchone()
    if row and row[0]:
        d = row[0]
        return d if isinstance(d, date) else date.fromisoformat(str(d))
    return None


def getEarliestDate(conn) -> date | None:
    row = conn.execute("SELECT MIN(date) FROM etf_prices").fetchone()
    if row and row[0]:
        d = row[0]
        return d if isinstance(d, date) else date.fromisoformat(str(d))
    return None


def getPortfolioCommonStartDate(pricesTwd: pd.DataFrame, allocations: dict) -> date | None:
    """
    回傳投資組合「所有（權重 > 0 的）成分標的皆已有價格資料」的最早共同
    日期，而非資料庫裡任一標的最早出現的日期。

    若用全域最早日期當作「最長期間」起點，尚未上市的標的在 DCA 模擬
    時，該月份分配到的資金會被 runDcaSimulation 靜默略過（price 為
    NaN），但 total_invested 仍照全額計算，等於帳面上算有投入、實際卻
    沒買到任何東西，變相低估報酀率。與 app/services/portfolio.py 的
    最長期間修正邏輯一致。
    """
    tickers = [t for t, w in allocations.items() if w]
    if not tickers:
        return None
    firstDates = []
    for t in tickers:
        tickerDates = pricesTwd.loc[pricesTwd["ticker"] == t, "date"]
        if tickerDates.empty:
            return None
        firstDates.append(tickerDates.min())
    commonStart = max(firstDates)
    return commonStart.date() if hasattr(commonStart, "date") else commonStart


def loadPricesTwd(conn, tickers: list, startDate: date, endDate: date) -> pd.DataFrame:
    """載入所有 ETF 價格並換算為 TWD。"""
    placeholders = ",".join(["?" for _ in tickers])
    pricesDf = conn.execute(
        f"""
        SELECT ticker, date, close, currency
        FROM etf_prices
        WHERE ticker IN ({placeholders})
          AND date BETWEEN ? AND ?
        ORDER BY ticker, date
        """,
        tickers + [startDate.isoformat(), endDate.isoformat()],
    ).df()

    if pricesDf.empty:
        return pricesDf

    pricesDf["date"] = pd.to_datetime(pricesDf["date"])

    fxDf = conn.execute(
        "SELECT date, rate FROM fx_rates WHERE date BETWEEN ? AND ? ORDER BY date",
        [startDate.isoformat(), endDate.isoformat()],
    ).df()
    fxDf["date"] = pd.to_datetime(fxDf["date"])

    # 換算 USD → TWD
    usdMask = pricesDf["currency"] == "USD"
    if usdMask.any() and not fxDf.empty:
        fxSeries = fxDf.set_index("date")["rate"]
        dateRange = pd.date_range(pricesDf["date"].min(), pricesDf["date"].max(), freq="D")
        fxFull = fxSeries.reindex(dateRange).ffill().bfill()
        usdRows = pricesDf[usdMask].copy()
        usdRows["fx"] = usdRows["date"].map(fxFull)
        usdRows["close"] = usdRows["close"] * usdRows["fx"]
        pricesDf = pricesDf.copy()
        pricesDf.loc[usdMask, "close"] = usdRows["close"].values

    return pricesDf


def loadTrackingDiffs(conn, tickers: list) -> pd.DataFrame:
    """載入季度追蹤差額資料。"""
    try:
        placeholders = ",".join(["?" for _ in tickers])
        df = conn.execute(
            f"""
            SELECT ticker, quarter, market_return, benchmark_return, diff
            FROM etf_tracking_diff
            WHERE ticker IN ({placeholders})
            ORDER BY ticker, quarter
            """,
            tickers,
        ).df()
        return df
    except Exception:
        return pd.DataFrame(columns=["ticker", "quarter", "market_return", "benchmark_return", "diff"])


def loadDividendsRaw(conn, tickers: list) -> pd.DataFrame:
    """
    載入 etf_dividends 資料表中的配息紀錄。
    回傳欄位：ticker, ex_date, amount_per_unit, currency。
    若資料表尚未建立（舊版 DB），回傳空 DataFrame。
    """
    try:
        placeholders = ",".join(["?" for _ in tickers])
        df = conn.execute(
            f"""
            SELECT ticker, ex_date, amount_per_unit, currency
            FROM etf_dividends
            WHERE ticker IN ({placeholders})
            ORDER BY ticker, ex_date
            """,
            tickers,
        ).df()
        if not df.empty:
            df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
        return df
    except Exception:
        return pd.DataFrame(columns=["ticker", "ex_date", "amount_per_unit", "currency"])


def loadFxSeries(conn, startDate: date, endDate: date) -> pd.Series:
    """
    載入 USD→TWD 匯率時間序列，以日期為 index，做前向填充。
    用於將 USD 計價的股息換算為 TWD。
    """
    fxDf = conn.execute(
        "SELECT date, rate FROM fx_rates WHERE date BETWEEN ? AND ? ORDER BY date",
        [startDate.isoformat(), endDate.isoformat()],
    ).df()
    if fxDf.empty:
        return pd.Series(dtype=float)
    fxDf["date"] = pd.to_datetime(fxDf["date"])
    fxSeries = fxDf.set_index("date")["rate"]
    dateRange = pd.date_range(fxSeries.index.min(), fxSeries.index.max(), freq="D")
    return fxSeries.reindex(dateRange).ffill().bfill()


def buildDividendCalendars(
    tickers: list,
    dividendsDf: pd.DataFrame,
    fxSeries: pd.Series,
    startDate: date,
    endDate: date,
) -> dict:
    """
    為所有 ticker 建立完整的配息日曆（涵蓋整個模擬期間）。

    邏輯：
      - 配息資料以 yfinance 完整歷史為主（備援 MoneyDJ 僅近二年）；無資料的
        年份以「完整年度的年均配息金額」估算，並標記 is_estimated=True。
      - 金額一律換算為 TWD。

    回傳：
      {
        ticker: {
          "calendar": [{"ex_date": date, "amount_per_unit_twd": float, "is_estimated": bool}],
          "info": {
            "has_actual_data": bool,
            "years_with_data": int,
            "years_estimated": int,
            "avg_annual_per_unit": float,
            "typical_months": list[int],
          }
        }
      }
    """
    result = {}

    for ticker in tickers:
        tickerDf = dividendsDf[dividendsDf["ticker"] == ticker].copy() if not dividendsDf.empty else pd.DataFrame()

        if tickerDf.empty:
            result[ticker] = {
                "calendar": [],
                "info": {"has_actual_data": False, "years_with_data": 0,
                         "years_estimated": 0, "avg_annual_per_unit": 0.0,
                         "typical_months": []},
            }
            continue

        tickerDf = tickerDf.sort_values("ex_date")

        # USD → TWD 換算
        if "currency" in tickerDf.columns and not fxSeries.empty:
            usd_mask = tickerDf["currency"] == "USD"
            if usd_mask.any():
                def _toTwd(row):
                    ts = pd.Timestamp(row["ex_date"])
                    idx = fxSeries.index.searchsorted(ts, side="right") - 1
                    fx = float(fxSeries.iloc[max(idx, 0)])
                    return row["amount_per_unit"] * fx
                tickerDf.loc[usd_mask, "amount_per_unit"] = (
                    tickerDf[usd_mask].apply(_toTwd, axis=1)
                )

        all_events = [
            {"ex_date": row["ex_date"], "amount_per_unit_twd": float(row["amount_per_unit"])}
            for _, row in tickerDf.iterrows()
        ]

        # 按年彙總，找出「完整年度」＝資料窗口的內部年份（嚴格介於首尾資料
        # 年之間）。首年可能自基金成立日或來源窗口起點才開始、末年通常尚未
        # 結束，都只涵蓋部分月份；對月配息 ETF 若誤當完整年度，年均配息會被
        # 大幅低估（曾發現 IEF 低估約 9%、SHY 約 14%）。
        annual_totals: dict[int, float] = {}
        for ev in all_events:
            yr = ev["ex_date"].year
            annual_totals[yr] = annual_totals.get(yr, 0.0) + ev["amount_per_unit_twd"]

        firstDataYear = min(annual_totals)
        lastDataYear  = max(annual_totals)
        complete_years = {yr: total for yr, total in annual_totals.items()
                          if firstDataYear < yr < lastDataYear}
        if not complete_years:
            complete_years = annual_totals

        avg_annual = sum(complete_years.values()) / len(complete_years)

        # 找典型配息月份（覆蓋 ≥ 80% 筆數的最少月份組合）
        month_counter = Counter(e["ex_date"].month for e in all_events)
        total_count = len(all_events)
        top_months: list[int] = []
        for m, _ in month_counter.most_common():
            top_months.append(m)
            if sum(month_counter[x] for x in top_months) / total_count >= 0.8:
                break
        typical_months = sorted(top_months) or [1, 7]   # 預設：半年配

        num_payments   = len(typical_months)
        amount_per_pay = avg_annual / num_payments if num_payments > 0 else 0.0

        # 建立模擬期間內的完整日曆
        actual_dates_in_period = {
            e["ex_date"] for e in all_events
            if startDate <= e["ex_date"] <= endDate
        }
        calendar: list[dict] = [
            {"ex_date": e["ex_date"], "amount_per_unit_twd": e["amount_per_unit_twd"],
             "is_estimated": False}
            for e in all_events
            if startDate <= e["ex_date"] <= endDate
        ]

        # 補估缺乏資料的年份／月份：
        #   - 全年無資料 → 補估所有典型月份
        #   - 該年有資料但落在資料窗口起點之後 → 補估「窗口起點之前」的缺漏
        #     月份（例如來源僅近二年時，窗口起點當年的前幾個月會被截斷）
        first_actual = min(e["ex_date"] for e in all_events)
        years_estimated = 0
        for yr in range(startDate.year, endDate.year + 1):
            yearHasData = any(e["ex_date"].year == yr for e in all_events)
            added = False
            for m in typical_months:
                try:
                    est_date = date(yr, m, 15)
                except ValueError:
                    est_date = date(yr, m, 28)
                if not (startDate <= est_date <= endDate):
                    continue
                if yearHasData:
                    if est_date >= first_actual:
                        continue   # 窗口內的月份以實際資料為準，不補估
                    if any(e["ex_date"].year == yr and e["ex_date"].month == m
                           for e in all_events):
                        continue   # 該月已有實際配息，避免重複
                if est_date in actual_dates_in_period:
                    continue
                calendar.append({
                    "ex_date":            est_date,
                    "amount_per_unit_twd": amount_per_pay,
                    "is_estimated":        True,
                })
                added = True
            if added:
                years_estimated += 1

        calendar.sort(key=lambda e: e["ex_date"])

        result[ticker] = {
            "calendar": calendar,
            "info": {
                "has_actual_data":     True,
                "years_with_data":     len(complete_years),
                "years_estimated":     years_estimated,
                "avg_annual_per_unit": round(avg_annual, 4),
                "typical_months":      typical_months,
            },
        }

    return result


# ─────────────────────────────────────────────────────────────
# DCA 模擬
# ─────────────────────────────────────────────────────────────

def buildPricePivot(pricesTwd: pd.DataFrame, tickers: list) -> pd.DataFrame:
    """
    建立前向填充的價格 pivot：index=日期, columns=ticker, values=TWD 收盤價。
    """
    subset = pricesTwd[pricesTwd["ticker"].isin(tickers)].copy()
    if subset.empty:
        return pd.DataFrame()
    pivot = subset.pivot(index="date", columns="ticker", values="close").sort_index()
    dateRange = pd.date_range(pivot.index.min(), pivot.index.max(), freq="D")
    pivot = pivot.reindex(dateRange).ffill()
    return pivot


def getMonthlyInvestmentDates(
    pricesTwd: pd.DataFrame, tickers: list, startDate: date, endDate: date, nthDay: int
) -> list[date]:
    """
    回傳每個月第 nthDay 個交易日（任一 ticker 有資料即視為交易日）。
    若該月交易日不足 nthDay 天，取最後一個交易日。
    """
    subset = pricesTwd[pricesTwd["ticker"].isin(tickers)]
    allDates = sorted(
        d.date() for d in pd.to_datetime(subset["date"]).unique()
        if startDate <= d.date() <= endDate
    )

    monthly: dict[tuple, list] = defaultdict(list)
    for d in allDates:
        monthly[(d.year, d.month)].append(d)

    investDates = []
    for key in sorted(monthly.keys()):
        days = monthly[key]
        idx = min(nthDay - 1, len(days) - 1)
        investDates.append(days[idx])

    return investDates


def _getFxAtDate(fxSeries: pd.Series, ts: pd.Timestamp) -> float:
    """從 fxSeries 取得特定日期的 USD→TWD 匯率（前向填充）。"""
    if fxSeries is None or fxSeries.empty:
        return 31.0   # 備用預設值
    idx = fxSeries.index.searchsorted(ts, side="right") - 1
    return float(fxSeries.iloc[max(idx, 0)])


def _applyFee(
    investAmountTwd: float,
    currency: str,
    feeConfig: dict | None,
    fxRate: float,
) -> tuple[float, float]:
    """
    套用手續費並檢查最低申購額。

    回傳 (淨投入金額 TWD, 手續費 TWD)。
    若低於最低申購額，回傳 (0.0, 0.0) 表示跳過此筆。
    """
    if not feeConfig:
        return investAmountTwd, 0.0

    if currency == "TWD":
        cfg = feeConfig.get("tw", {})
        feeTwd = cfg.get("fee_twd", 0.0)
        minTwd = cfg.get("min_investment_twd", 0.0)
    else:  # USD
        cfg = feeConfig.get("us", {})
        feeTwd = cfg.get("fee_usd", 0.0) * fxRate
        minTwd = cfg.get("min_investment_usd", 0.0) * fxRate

    if investAmountTwd < minTwd:
        return 0.0, 0.0

    netAmount = investAmountTwd - feeTwd
    if netAmount <= 0:
        return 0.0, 0.0

    return netAmount, feeTwd


def runDcaSimulation(
    portfolio: dict,
    pricesTwd: pd.DataFrame,
    monthlyAmount: float,
    nthDay: int,
    startDate: date,
    endDate: date,
    etfInfoMap: dict | None = None,
    feeConfig: dict | None = None,
    fxSeries: pd.Series | None = None,
) -> tuple[list, dict] | tuple[None, None]:
    """
    模擬定期定額，回傳 (transactions, summary)。

    transactions: [{date, etfs: {ticker: {amount, price, units, cumUnits}}}]
    summary: {total_invested, total_fees, final_value, start, end, num_months,
              cashflow_dates, cashflow_amounts}
    """
    tickers = list(portfolio["allocations"].keys())
    allocations = portfolio["allocations"]

    pricePivot = buildPricePivot(pricesTwd, tickers)
    if pricePivot.empty:
        return None, None

    investDates = getMonthlyInvestmentDates(pricesTwd, tickers, startDate, endDate, nthDay)
    if not investDates:
        return None, None

    # 正規化配置比例（避免因資料缺失造成 0 / 0）
    validTickers = [t for t in tickers if t in pricePivot.columns]
    totalWeight = sum(allocations[t] for t in validTickers)
    if totalWeight <= 0:
        return None, None

    unitsPerTicker: dict[str, float] = {t: 0.0 for t in validTickers}
    transactions: list[dict] = []
    cashflowDates: list[date] = []
    cashflowAmounts: list[float] = []
    totalFees = 0.0

    for d in investDates:
        dt = pd.Timestamp(d)
        # 取 d 當日或之前最近的 pivot index
        loc = pricePivot.index.searchsorted(dt, side="right") - 1
        if loc < 0:
            continue
        actualDt = pricePivot.index[loc]
        fxRate = _getFxAtDate(fxSeries, actualDt) if fxSeries is not None else 31.0

        row: dict[str, dict] = {}
        for ticker in validTickers:
            price = pricePivot.loc[actualDt, ticker]
            if pd.isna(price) or price <= 0:
                continue
            weight = allocations[ticker] / totalWeight
            grossAmount = monthlyAmount * weight
            currency = (etfInfoMap or {}).get(ticker, {}).get("currency", "TWD")
            netAmount, feeTwd = _applyFee(grossAmount, currency, feeConfig, fxRate)
            if netAmount <= 0:
                continue
            totalFees += feeTwd
            units = netAmount / price
            unitsPerTicker[ticker] += units
            row[ticker] = {
                "amount":   round(grossAmount, 2),
                "fee":      round(feeTwd, 4),
                "price":    round(float(price), 4),
                "units":    round(units, 6),
                "cumUnits": round(unitsPerTicker[ticker], 6),
            }

        transactions.append({"date": d, "etfs": row})
        cashflowDates.append(d)
        cashflowAmounts.append(-monthlyAmount)   # 現金流出為負

    if not transactions:
        return None, None

    # 最終淨值（使用 pivot 最後一日的價格）
    latestDt = pricePivot.index[-1]
    finalValue = sum(
        unitsPerTicker[t] * float(pricePivot.loc[latestDt, t])
        for t in validTickers
        if not pd.isna(pricePivot.loc[latestDt, t])
    )

    totalInvested = monthlyAmount * len(transactions)
    cashflowDates.append(latestDt.date())
    cashflowAmounts.append(finalValue)    # 現金流入為正

    summary = {
        "total_invested":    totalInvested,
        "total_fees":        round(totalFees, 0),
        "final_value":       finalValue,
        "start":             transactions[0]["date"],
        "end":               transactions[-1]["date"],
        "num_months":        len(transactions),
        "cashflow_dates":    cashflowDates,
        "cashflow_amounts":  cashflowAmounts,
    }
    return transactions, summary


def runDcaWithDividends(
    portfolio: dict,
    pricesTwd: pd.DataFrame,
    divCalendars: dict,
    monthlyAmount: float,
    nthDay: int,
    startDate: date,
    endDate: date,
    etfInfoMap: dict | None = None,
    feeConfig: dict | None = None,
    fxSeries: pd.Series | None = None,
) -> tuple[list, dict] | tuple[None, None]:
    """
    模擬含股息再投資的定期定額。
    與 runDcaSimulation 邏輯相同，但在每個配息除息日自動將股息買回股份。
    股息再投資同樣收取手續費並受最低申購額限制；未達門檻的配息累積為
    現金，達門檻後於下次配息日買回，期末仍未投入的現金計入最終淨值。

    divCalendars: buildDividendCalendars() 的回傳值。
    """
    tickers     = list(portfolio["allocations"].keys())
    allocations = portfolio["allocations"]

    pricePivot = buildPricePivot(pricesTwd, tickers)
    if pricePivot.empty:
        return None, None

    investDates = getMonthlyInvestmentDates(pricesTwd, tickers, startDate, endDate, nthDay)
    if not investDates:
        return None, None

    validTickers = [t for t in tickers if t in pricePivot.columns]
    totalWeight  = sum(allocations[t] for t in validTickers)
    if totalWeight <= 0:
        return None, None

    unitsPerTicker: dict[str, float] = {t: 0.0 for t in validTickers}
    pendingCashTwd: dict[str, float] = {t: 0.0 for t in validTickers}
    transactions: list[dict] = []
    cashflowDates:   list[date]  = []
    cashflowAmounts: list[float] = []
    totalDivIncomeTwd = 0.0
    totalFees         = 0.0

    # 建立合併配息日曆：{ex_date: {ticker: amount_twd}}
    combined_divs: dict[date, dict[str, float]] = {}
    for t in validTickers:
        cal = divCalendars.get(t, {}).get("calendar", [])
        for ev in cal:
            ex_dt = ev["ex_date"]
            if startDate <= ex_dt <= endDate:
                combined_divs.setdefault(ex_dt, {})[t] = ev["amount_per_unit_twd"]
    sorted_div_dates = sorted(combined_divs.keys())
    div_ptr = 0

    def _processDividends(up_to_date: date) -> None:
        """
        處理所有 ≤ up_to_date 且尚未處理的配息事件（股息再投資亦收手續費）。
        配息先累積至 pendingCashTwd，達最低申購額才買回；未達門檻的現金
        保留至之後的配息日再嘗試，不會憑空消失。
        """
        nonlocal div_ptr, totalDivIncomeTwd, totalFees
        while div_ptr < len(sorted_div_dates) and sorted_div_dates[div_ptr] <= up_to_date:
            ex_date        = sorted_div_dates[div_ptr]
            ticker_amounts = combined_divs[ex_date]
            ex_ts          = pd.Timestamp(ex_date)
            loc            = pricePivot.index.searchsorted(ex_ts, side="right") - 1
            if loc >= 0:
                fx_at_ex = _getFxAtDate(fxSeries, ex_ts) if fxSeries is not None else 31.0
                for t, div_amt in ticker_amounts.items():
                    if unitsPerTicker.get(t, 0) <= 0 or div_amt <= 0:
                        continue
                    price = float(pricePivot.iloc[loc][t]) if t in pricePivot.columns else float("nan")
                    if pd.isna(price) or price <= 0:
                        continue
                    div_income = unitsPerTicker[t] * div_amt
                    totalDivIncomeTwd += div_income
                    pendingCashTwd[t] += div_income
                    currency = (etfInfoMap or {}).get(t, {}).get("currency", "TWD")
                    netIncome, feeTwd = _applyFee(pendingCashTwd[t], currency, feeConfig, fx_at_ex)
                    if netIncome > 0:
                        totalFees += feeTwd
                        unitsPerTicker[t] += netIncome / price
                        pendingCashTwd[t] = 0.0
            div_ptr += 1

    for d in investDates:
        # 先結算截至今日的所有股息
        _processDividends(d)

        dt  = pd.Timestamp(d)
        loc = pricePivot.index.searchsorted(dt, side="right") - 1
        if loc < 0:
            continue
        actualDt = pricePivot.index[loc]
        fxRate = _getFxAtDate(fxSeries, actualDt) if fxSeries is not None else 31.0

        row: dict[str, dict] = {}
        for t in validTickers:
            price = pricePivot.loc[actualDt, t]
            if pd.isna(price) or price <= 0:
                continue
            weight = allocations[t] / totalWeight
            grossAmount = monthlyAmount * weight
            currency = (etfInfoMap or {}).get(t, {}).get("currency", "TWD")
            netAmount, feeTwd = _applyFee(grossAmount, currency, feeConfig, fxRate)
            if netAmount <= 0:
                continue
            totalFees += feeTwd
            units = netAmount / price
            unitsPerTicker[t] += units
            row[t] = {
                "amount":   round(grossAmount, 2),
                "fee":      round(feeTwd, 4),
                "price":    round(float(price), 4),
                "units":    round(units, 6),
                "cumUnits": round(unitsPerTicker[t], 6),
            }

        transactions.append({"date": d, "etfs": row})
        cashflowDates.append(d)
        cashflowAmounts.append(-monthlyAmount)

    if not transactions:
        return None, None

    # 結算最後一個投資日之後到 endDate 的股息
    _processDividends(endDate)

    latestDt   = pricePivot.index[-1]
    finalValue = sum(
        unitsPerTicker[t] * float(pricePivot.loc[latestDt, t])
        for t in validTickers
        if not pd.isna(pricePivot.loc[latestDt, t])
    ) + sum(pendingCashTwd.values())   # 未達申購門檻的配息現金仍屬資產

    totalInvested = monthlyAmount * len(transactions)
    cashflowDates.append(latestDt.date())
    cashflowAmounts.append(finalValue)

    summary = {
        "total_invested":    totalInvested,
        "total_fees":        round(totalFees, 0),
        "final_value":       finalValue,
        "total_div_income":  round(totalDivIncomeTwd, 0),
        "start":             transactions[0]["date"],
        "end":               transactions[-1]["date"],
        "num_months":        len(transactions),
        "cashflow_dates":    cashflowDates,
        "cashflow_amounts":  cashflowAmounts,
    }
    return transactions, summary


# ─────────────────────────────────────────────────────────────
# 績效計算
# ─────────────────────────────────────────────────────────────

def computeXirr(cf_dates: list[date], cf_amounts: list[float]) -> float | None:
    """
    計算不規則現金流的內部報酬率（IRR / XIRR）。
    使用 Newton-Raphson 迭代法；收斂失敗回傳 None。

    cf_amounts 慣例：流出為負（投資），流入為正（贖回）。
    """
    if len(cf_dates) < 2 or len(cf_dates) != len(cf_amounts):
        return None

    t0 = cf_dates[0]
    day_fracs = [(d - t0).days / 365.25 for d in cf_dates]

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** t for cf, t in zip(cf_amounts, day_fracs))

    def npv_deriv(rate: float) -> float:
        return sum(-t * cf / (1.0 + rate) ** (t + 1.0)
                   for cf, t in zip(cf_amounts, day_fracs))

    rate = 0.10   # 初始猜測值
    for _ in range(200):
        f  = npv(rate)
        df = npv_deriv(rate)
        if abs(df) < 1e-14:
            break
        new_rate = rate - f / df
        # 避免率跳至無效區間
        new_rate = max(new_rate, -0.9999)
        if abs(new_rate - rate) < 1e-9:
            return new_rate if abs(npv(new_rate)) < 1.0 else None
        rate = new_rate

    return rate if abs(npv(rate)) < 1.0 else None


def computeDcaStats(summary: dict) -> dict | None:
    """計算 DCA 統計，回傳 None 表示資料不足。"""
    if not summary:
        return None

    invested   = summary["total_invested"]
    finalValue = summary["final_value"]
    if invested <= 0 or finalValue <= 0:
        return None

    totalReturn = (finalValue - invested) / invested
    start, end = summary["start"], summary["end"]
    nDays  = (end - start).days
    nYears = nDays / 365.25 if nDays > 0 else 0

    # 簡單年化報酬率（以總投入視為期初一次投入）
    simpleCagr = (finalValue / invested) ** (1.0 / nYears) - 1 if nYears > 0 else totalReturn

    # IRR（金額加權年化報酬率，更準確反映 DCA 時間加權）
    irr = computeXirr(summary["cashflow_dates"], summary["cashflow_amounts"])

    return {
        "total_invested":   round(invested, 0),
        "total_fees":       summary.get("total_fees", 0),
        "final_value":      round(finalValue, 0),
        "total_return":     round(totalReturn * 100, 2),
        "simple_cagr":      round(simpleCagr * 100, 2),
        "irr":              round(irr * 100, 2) if irr is not None else None,
        "num_months":       summary["num_months"],
        "start":            start,
        "end":              end,
        "years":            round(nYears, 1),
        "total_div_income": summary.get("total_div_income", 0),
    }


def analyzePortfolioWithDividends(
    portfolio: dict,
    pricesTwd: pd.DataFrame,
    divCalendars: dict,
    monthlyAmount: float,
    nthDay: int,
    today: date,
    earliestDate: date,
    etfInfoMap: dict | None = None,
    feeConfig: dict | None = None,
    fxSeries: pd.Series | None = None,
) -> dict:
    """
    對單一投資組合計算各時間區間的含股息再投資 DCA 績效。
    回傳結構與 analyzePortfolio 相同，另增 div_info 欄位。
    """
    result: dict = {
        "name":     portfolio["name"],
        "periods":  {},
    }

    commonStart = getPortfolioCommonStartDate(pricesTwd, portfolio["allocations"]) or earliestDate

    for periodName, delta in PERIODS:
        periodStart = commonStart if delta is None else today - delta
        startDate = max(periodStart, commonStart)
        _txns, summary = runDcaWithDividends(
            portfolio, pricesTwd, divCalendars,
            monthlyAmount, nthDay, startDate, today,
            etfInfoMap=etfInfoMap, feeConfig=feeConfig, fxSeries=fxSeries,
        )
        result["periods"][periodName] = computeDcaStats(summary)

    # 收集此組合用到的股息資訊（供 Excel 備注）
    div_info = {
        t: divCalendars.get(t, {}).get("info", {"has_actual_data": False})
        for t in portfolio["allocations"]
    }
    result["div_info"] = div_info

    return result


def analyzePortfolio(
    portfolio: dict,
    etfInfoMap: dict,
    pricesTwd: pd.DataFrame,
    trackingDf: pd.DataFrame,
    monthlyAmount: float,
    nthDay: int,
    today: date,
    earliestDate: date,
    feeConfig: dict | None = None,
    fxSeries: pd.Series | None = None,
) -> dict:
    """對單一投資組合計算各時間區間的 DCA 績效與追蹤誤差分析。"""
    result: dict = {
        "name":        portfolio["name"],
        "allocations": portfolio["allocations"],
        "periods":     {},
        "tracking":    computePortfolioTrackingError(portfolio, etfInfoMap, trackingDf, earliestDate, today),
    }

    commonStart = getPortfolioCommonStartDate(pricesTwd, portfolio["allocations"]) or earliestDate

    for periodName, delta in PERIODS:
        periodStart = commonStart if delta is None else today - delta
        startDate = max(periodStart, commonStart)
        _txns, summary = runDcaSimulation(
            portfolio, pricesTwd, monthlyAmount, nthDay, startDate, today,
            etfInfoMap=etfInfoMap, feeConfig=feeConfig, fxSeries=fxSeries,
        )
        result["periods"][periodName] = computeDcaStats(summary)

    return result


# ─────────────────────────────────────────────────────────────
# 追蹤誤差分析
# ─────────────────────────────────────────────────────────────

def quarterEndDate(quarter: str) -> date:
    """'2025Q4' → date(2025, 12, 31) 等"""
    year = int(quarter[:4])
    q    = int(quarter[5])
    month_end = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    m, d = month_end[q]
    return date(year, m, d)


def computePortfolioTrackingError(
    portfolio: dict,
    etfInfoMap: dict,
    trackingDf: pd.DataFrame,
    startDate: date,
    endDate: date,
) -> dict | None:
    """
    計算各 ETF 在分析期間的追蹤差額統計，以及投資組合加權後的影響估算。

    回傳：
    {
        "per_etf": {ticker: {num_quarters, total_diff, avg_quarterly_diff, annualized_diff, weight}},
        "portfolio_weighted": {avg_quarterly_diff, annualized_diff, total_diff}
    }
    """
    if trackingDf.empty:
        return None

    allocations = portfolio["allocations"]
    perEtf: dict[str, dict] = {}

    for ticker, weight in allocations.items():
        etfData = trackingDf[trackingDf["ticker"] == ticker].copy()
        if etfData.empty:
            continue

        # 篩選至分析期間內的已完成季度
        etfData = etfData[etfData["quarter"].apply(
            lambda q: startDate <= quarterEndDate(q) <= endDate
        )]
        if etfData.empty:
            continue

        diffs = etfData["diff"].dropna()
        if diffs.empty:
            continue

        numQ    = len(diffs)
        totalDiff   = float(diffs.sum())
        avgQDiff    = float(diffs.mean())
        annualizedTE = avgQDiff * 4   # 4 季 = 1 年

        perEtf[ticker] = {
            "num_quarters":      numQ,
            "total_diff":        round(totalDiff, 2),
            "avg_quarterly_diff": round(avgQDiff, 2),
            "annualized_diff":   round(annualizedTE, 2),
            "weight":            weight,
        }

    if not perEtf:
        return None

    # 投資組合加權（只對有追蹤差額資料的 ETF 做加權）
    totalWeight = sum(perEtf[t]["weight"] for t in perEtf)
    if totalWeight > 0:
        wtAvgQ    = sum(perEtf[t]["avg_quarterly_diff"] * perEtf[t]["weight"] for t in perEtf) / totalWeight
        wtTotal   = sum(perEtf[t]["total_diff"]         * perEtf[t]["weight"] for t in perEtf) / totalWeight
        wtAnn     = wtAvgQ * 4
    else:
        wtAvgQ = wtTotal = wtAnn = 0.0

    return {
        "per_etf": perEtf,
        "portfolio_weighted": {
            "avg_quarterly_diff": round(wtAvgQ, 2),
            "annualized_diff":    round(wtAnn, 2),
            "total_diff":         round(wtTotal, 2),
        },
    }


# ─────────────────────────────────────────────────────────────
# Excel 輸出
# ─────────────────────────────────────────────────────────────

def exportToExcel(
    allResults: list,
    allDivResults: list,
    divCalendars: dict,
    config: dict,
    outputPath: Path,
    monthlyAmount: float,
    nthDay: int,
):
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "DCA CAGR 各期間"
    writeCagrSheet(ws1, allResults, monthlyAmount, nthDay)

    ws2 = wb.create_sheet("各期間詳細統計")
    writeDetailSheet(ws2, allResults, monthlyAmount, nthDay)

    ws3 = wb.create_sheet("追蹤誤差分析")
    writeTrackingSheet(ws3, allResults, config)

    ws4 = wb.create_sheet("含股息再投資 CAGR")
    writeDivCagrSheet(ws4, allDivResults, allResults, monthlyAmount, nthDay)

    ws5 = wb.create_sheet("含股息再投資詳細")
    writeDivDetailSheet(ws5, allDivResults, divCalendars, config, monthlyAmount, nthDay)

    wb.save(outputPath)


# ─── 通用樣式輔助 ─────────────────────────────────────────────

def _headerCell(ws, row: int, col: int, value, bgColor: str = COLOR_HEADER,
                fontColor: str = "FFFFFF", bold: bool = True,
                size: int = 10, wrap: bool = False, hAlign: str = "center"):
    c = ws.cell(row=row, column=col, value=value)
    c.fill = PatternFill("solid", fgColor=bgColor)
    c.font = Font(color=fontColor, bold=bold, size=size)
    c.alignment = Alignment(horizontal=hAlign, vertical="center", wrap_text=wrap)
    return c


def _colorCell(ws, row: int, col: int, value, numFmt: str = "0.00%",
               positive_is_good: bool = True, hAlign: str = "right"):
    c = ws.cell(row=row, column=col, value=value)
    c.number_format = numFmt
    c.alignment = Alignment(horizontal=hAlign)
    if value is not None:
        is_pos = value >= 0
        is_good = is_pos if positive_is_good else not is_pos
        c.fill = PatternFill("solid", fgColor=COLOR_POS if is_good else COLOR_NEG)
    return c


# ─── Sheet 1：DCA CAGR 各期間 ────────────────────────────────

def writeCagrSheet(ws, allResults: list, monthlyAmount: float, nthDay: int):
    periodNames = [p[0] for p in PERIODS]
    today = date.today()

    # 第一列：大標題
    lastCol = get_column_letter(1 + len(periodNames) * 2)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"定期定額年化報酬率（IRR / 金額加權，以未含息市價計算、不含配息）"
        f"  ▸  每月 {monthlyAmount:,.0f} TWD，第 {nthDay} 個交易日"
        f"  ▸  資料截至 {today}"
    )
    _headerCell(ws, 1, 1, ws["A1"].value, size=11, wrap=True)
    ws.row_dimensions[1].height = 28

    # 第二列：說明副欄（期間名稱 × 兩個指標：IRR, 簡單年化）
    ws.merge_cells("A2:A3")
    _headerCell(ws, 2, 1, "投資組合", bgColor=COLOR_SUBHEAD)

    col = 2
    for pName in periodNames:
        ws.merge_cells(f"{get_column_letter(col)}2:{get_column_letter(col+1)}2")
        _headerCell(ws, 2, col, pName, bgColor=COLOR_SUBHEAD, wrap=True)
        _headerCell(ws, 3, col,   "IRR",   bgColor=COLOR_SUBHEAD, size=9)
        _headerCell(ws, 3, col+1, "簡單CAGR", bgColor=COLOR_SUBHEAD, size=9, wrap=True)
        col += 2

    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 20

    # 資料列
    row = 4
    for i, pr in enumerate(allResults):
        fillColor = COLOR_ALT if i % 2 == 1 else "FFFFFF"
        nameCell = ws.cell(row=row, column=1, value=pr["name"])
        nameCell.fill = PatternFill("solid", fgColor=COLOR_PERIOD)
        nameCell.font = Font(bold=True)
        nameCell.alignment = Alignment(horizontal="left", vertical="center")

        col = 2
        for pName in periodNames:
            stats = pr["periods"].get(pName)
            for key in ("irr", "simple_cagr"):
                c = ws.cell(row=row, column=col)
                if stats is None:
                    c.value = "資料不足"
                    c.alignment = Alignment(horizontal="center")
                    c.font = Font(color="888888", italic=True, size=9)
                else:
                    val = stats.get(key)
                    if val is None:
                        c.value = "—"
                        c.alignment = Alignment(horizontal="center")
                    else:
                        c.value = val / 100
                        c.number_format = "0.00%"
                        c.alignment = Alignment(horizontal="right")
                        c.fill = PatternFill("solid", fgColor=COLOR_POS if val >= 0 else COLOR_NEG)
                    if key == "irr" and stats and stats.get("start"):
                        fees = stats.get("total_fees", 0) or 0
                        fee_line = f"\n總手續費：{fees:,.0f} TWD" if fees > 0 else ""
                        c.comment = Comment(
                            f"期間：{stats['start']} → {stats['end']}\n"
                            f"投入月數：{stats['num_months']}月（{stats['years']}年）\n"
                            f"總投入：{stats['total_invested']:,.0f} TWD"
                            f"{fee_line}\n"
                            f"最終淨值：{stats['final_value']:,.0f} TWD",
                            "auto"
                        )
                col += 1

        ws.row_dimensions[row].height = 20
        row += 1

    # 欄寬
    ws.column_dimensions["A"].width = 16
    for i in range(2, col):
        ws.column_dimensions[get_column_letter(i)].width = 10

    ws.freeze_panes = "B4"


# ─── Sheet 2：各期間詳細統計 ──────────────────────────────────

def writeDetailSheet(ws, allResults: list, monthlyAmount: float, nthDay: int):
    today = date.today()
    periodNames = [p[0] for p in PERIODS]

    # 大標題
    lastCol = get_column_letter(9)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"定期定額各期間詳細統計（以未含息市價計算、不含配息）  ▸  每月 {monthlyAmount:,.0f} TWD，第 {nthDay} 個交易日  ▸  資料截至 {today}"
    )
    _headerCell(ws, 1, 1, ws["A1"].value, size=11, wrap=True)
    ws.row_dimensions[1].height = 28

    headers = ["投資組合", "期間", "投入月數", "投入年數", "總投入 (TWD)",
               "總手續費 (TWD)", "最終淨值 (TWD)", "總報酬率", "IRR（金額加權）", "簡單年化 CAGR"]
    for col, h in enumerate(headers, start=1):
        _headerCell(ws, 2, col, h, bgColor=COLOR_SUBHEAD, wrap=True)
    ws.row_dimensions[2].height = 30

    row = 3
    for i, pr in enumerate(allResults):
        for j, pName in enumerate(periodNames):
            stats = pr["periods"].get(pName)
            altFill = PatternFill("solid", fgColor=COLOR_ALT if j % 2 == 1 else "FFFFFF")

            nameCell = ws.cell(row=row, column=1, value=pr["name"] if j == 0 else "")
            if j == 0:
                nameCell.fill = PatternFill("solid", fgColor=COLOR_PERIOD)
                nameCell.font = Font(bold=True)
            nameCell.alignment = Alignment(horizontal="left", vertical="center")

            ws.cell(row=row, column=2, value=pName).alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=2).fill = altFill

            if stats is None:
                for col in range(3, 11):
                    c = ws.cell(row=row, column=col, value="資料不足")
                    c.alignment = Alignment(horizontal="center")
                    c.font = Font(color="888888", italic=True, size=9)
                    c.fill = altFill
            else:
                def _set(col, value, fmt=None, colorize=False, good_if_pos=True):
                    c = ws.cell(row=row, column=col, value=value)
                    c.fill = altFill
                    if fmt:
                        c.number_format = fmt
                    if colorize and value is not None:
                        is_good = value >= 0 if good_if_pos else value < 0
                        c.fill = PatternFill("solid", fgColor=COLOR_POS if is_good else COLOR_NEG)
                    return c

                _set(3, stats.get("num_months"),    "#,##0").alignment = Alignment(horizontal="right")
                _set(4, stats.get("years"),         "0.0").alignment = Alignment(horizontal="right")
                _set(5, stats.get("total_invested"), "#,##0").alignment = Alignment(horizontal="right")
                _set(6, stats.get("total_fees"),     "#,##0").alignment = Alignment(horizontal="right")
                _set(7, stats.get("final_value"),    "#,##0").alignment = Alignment(horizontal="right")

                tr = stats.get("total_return")
                _colorCell(ws, row, 8, tr / 100 if tr is not None else None)

                irr = stats.get("irr")
                irrCell = _colorCell(ws, row, 9, irr / 100 if irr is not None else None)
                if stats.get("start"):
                    irrCell.comment = Comment(
                        f"起始：{stats['start']}\n結束：{stats['end']}",
                        "auto"
                    )

                sc = stats.get("simple_cagr")
                _colorCell(ws, row, 10, sc / 100 if sc is not None else None)

            ws.row_dimensions[row].height = 18
            row += 1

        # 空白分隔行
        for col in range(1, 11):
            ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="F0F0F0")
        row += 1

    widths = [16, 10, 10, 10, 18, 15, 18, 12, 16, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "C3"


# ─── Sheet 3：追蹤誤差分析 ────────────────────────────────────

def writeTrackingSheet(ws, allResults: list, config: dict):
    """
    上半部：投資組合加權追蹤誤差彙總
    下半部：各 ETF 在各組合中的追蹤差額詳細數字

    說明：
      - 追蹤差額（diff）= 市價季報酬率 - 基準指數漲跌幅
      - 正值表示 ETF 季報酬超越基準；負值表示落後基準
      - 年化追蹤誤差 ≈ 平均季差額 × 4
      - 期間累計追蹤差額 ≈ 各季差額加總
      - 實際 DCA 報酬率已內含追蹤誤差，此表僅呈現追蹤品質供參考
    """
    today = date.today()

    ws.merge_cells("A1:H1")
    ws["A1"] = f"追蹤誤差分析（季度追蹤差額）  ▸  資料截至 {today}"
    _headerCell(ws, 1, 1, ws["A1"].value, size=11)
    ws.row_dimensions[1].height = 26

    # ── 說明文字 ──────────────────────────────────────────────
    ws.merge_cells("A2:H2")
    ws["A2"] = (
        "追蹤差額（diff）= 市價季報酬率 − 基準指數漲跌幅｜"
        "正值：ETF 超越指數；負值：ETF 落後指數｜"
        "年化追蹤誤差 ≈ 平均季差額 × 4｜"
        "實際 DCA 報酬率已內含追蹤誤差，本表呈現追蹤品質供參考"
    )
    c = ws["A2"]
    c.fill = PatternFill("solid", fgColor="EBF3FB")
    c.font = Font(italic=True, size=9, color="444444")
    c.alignment = Alignment(horizontal="left", wrap_text=True)
    ws.row_dimensions[2].height = 28

    row = 3

    # ── 投資組合加權彙總 ──────────────────────────────────────
    ws.merge_cells(f"A{row}:H{row}")
    ws.cell(row=row, column=1, value="▌ 投資組合加權追蹤誤差彙總（最長期間）")
    _headerCell(ws, row, 1, ws.cell(row=row, column=1).value, bgColor=COLOR_SUBHEAD, size=10)
    ws.row_dimensions[row].height = 22
    row += 1

    summary_headers = ["投資組合", "涵蓋最長期間追蹤差額季數（加權）",
                        "期間累計追蹤差額（%）", "平均每季追蹤差額（%）", "年化追蹤誤差估算（%）",
                        "備注"]
    for col, h in enumerate(summary_headers, start=1):
        _headerCell(ws, row, col, h, bgColor=COLOR_PERIOD,
                    fontColor="1F3864", bold=True, size=9, wrap=True)
    ws.row_dimensions[row].height = 30
    row += 1

    for pr in allResults:
        t = pr.get("tracking")
        ws.cell(row=row, column=1, value=pr["name"]).font = Font(bold=True)
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="left")

        if t is None:
            for col in range(2, 7):
                ws.cell(row=row, column=col, value="無追蹤差額資料")
                ws.cell(row=row, column=col).font = Font(color="888888", italic=True, size=9)
        else:
            pw = t["portfolio_weighted"]
            # 估計涵蓋季數（取各 ETF 最大值作為代表）
            max_q = max((v["num_quarters"] for v in t["per_etf"].values()), default=0)
            ws.cell(row=row, column=2, value=max_q).alignment = Alignment(horizontal="right")
            ws.cell(row=row, column=2).number_format = "#,##0"

            _colorCell(ws, row, 3, pw["total_diff"] / 100 if pw["total_diff"] is not None else None)
            _colorCell(ws, row, 4, pw["avg_quarterly_diff"] / 100 if pw["avg_quarterly_diff"] is not None else None)
            _colorCell(ws, row, 5, pw["annualized_diff"] / 100 if pw["annualized_diff"] is not None else None)

            # 備注
            note = ""
            ann = pw["annualized_diff"]
            if ann is not None:
                if ann < -0.5:
                    note = f"ETF 年均落後指數 {abs(ann):.2f}%，追蹤品質待改善"
                elif ann < 0:
                    note = f"ETF 年均落後指數 {abs(ann):.2f}%，追蹤品質尚可"
                elif ann < 0.5:
                    note = f"ETF 年均超越指數 {ann:.2f}%，追蹤品質良好"
                else:
                    note = f"ETF 年均超越指數 {ann:.2f}%"
            ws.cell(row=row, column=6, value=note).font = Font(italic=True, size=9, color="444444")

        ws.row_dimensions[row].height = 18
        row += 1

    row += 1

    # ── 各組合 ETF 追蹤差額明細 ───────────────────────────────
    ws.merge_cells(f"A{row}:H{row}")
    ws.cell(row=row, column=1, value="▌ 各 ETF 追蹤差額明細（按投資組合列示）")
    _headerCell(ws, row, 1, ws.cell(row=row, column=1).value, bgColor=COLOR_SUBHEAD, size=10)
    ws.row_dimensions[row].height = 22
    row += 1

    detail_headers = ["投資組合", "ETF 代號", "ETF 名稱", "配置比例",
                       "資料季數", "期間累計差額（%）",
                       "平均每季差額（%）", "年化追蹤誤差估算（%）"]
    for col, h in enumerate(detail_headers, start=1):
        _headerCell(ws, row, col, h, bgColor=COLOR_PERIOD,
                    fontColor="1F3864", bold=True, size=9, wrap=True)
    ws.row_dimensions[row].height = 30
    row += 1

    etfLookup = {e["ticker"]: e for e in config["etfs"]}

    for pr in allResults:
        t = pr.get("tracking")
        if t is None:
            ws.cell(row=row, column=1, value=pr["name"]).font = Font(bold=True)
            ws.cell(row=row, column=2, value="無追蹤差額資料").font = Font(color="888888", italic=True, size=9)
            row += 1
            continue

        per_etf = t["per_etf"]
        for j, (ticker, data) in enumerate(per_etf.items()):
            ws.cell(row=row, column=1, value=pr["name"] if j == 0 else "").font = Font(bold=True)
            ws.cell(row=row, column=1).alignment = Alignment(horizontal="left")

            ws.cell(row=row, column=2, value=ticker).font = Font(bold=True)
            etf = etfLookup.get(ticker, {})
            ws.cell(row=row, column=3, value=etf.get("name", ticker))

            wCell = ws.cell(row=row, column=4, value=data["weight"])
            wCell.number_format = "0%"
            wCell.alignment = Alignment(horizontal="right")

            ws.cell(row=row, column=5, value=data["num_quarters"]).alignment = Alignment(horizontal="right")
            _colorCell(ws, row, 6, data["total_diff"] / 100 if data["total_diff"] is not None else None)
            _colorCell(ws, row, 7, data["avg_quarterly_diff"] / 100 if data["avg_quarterly_diff"] is not None else None)
            _colorCell(ws, row, 8, data["annualized_diff"] / 100 if data["annualized_diff"] is not None else None)

            ws.row_dimensions[row].height = 18
            row += 1

        row += 1  # 空白分隔行

    # 欄寬
    widths = [16, 10, 24, 10, 10, 18, 18, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A3"


# ─── Sheet 4：含股息再投資 CAGR ──────────────────────────────────

def writeDivCagrSheet(
    ws,
    allDivResults: list,
    allBaseResults: list,
    monthlyAmount: float,
    nthDay: int,
):
    """
    含股息再投資年化報酬率（IRR / 簡單CAGR）彙總表。
    格式與 Sheet 1 相同，另附有無股息差異欄，以及底部估算說明。
    """
    periodNames = [p[0] for p in PERIODS]
    today       = date.today()

    # 大標題
    lastCol = get_column_letter(1 + len(periodNames) * 2)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"定期定額年化報酬率（含股息再投資）"
        f"  ▸  每月 {monthlyAmount:,.0f} TWD，第 {nthDay} 個交易日"
        f"  ▸  資料截至 {today}"
    )
    _headerCell(ws, 1, 1, ws["A1"].value, size=11, wrap=True)
    ws.row_dimensions[1].height = 28

    # 說明副標題
    ws.merge_cells(f"A2:{lastCol}2")
    ws["A2"] = (
        "股息再投資：配息除息日以當日收盤價買回股份；未達最低申購額的配息累積為現金，達門檻後買回，期末剩餘現金計入淨值。"
        "配息資料取自 yfinance 完整歷史（備援 MoneyDJ 近二年），無資料年份以完整年度的年均配息金額估算（標記「*」）。"
        "無配息資料的 ETF 不納入股息再投資計算。"
    )
    c = ws["A2"]
    c.fill = PatternFill("solid", fgColor="EBF3FB")
    c.font = Font(italic=True, size=9, color="444444")
    c.alignment = Alignment(horizontal="left", wrap_text=True)
    ws.row_dimensions[2].height = 32

    # 表頭
    ws.merge_cells("A3:A4")
    _headerCell(ws, 3, 1, "投資組合", bgColor=COLOR_SUBHEAD)

    col = 2
    for pName in periodNames:
        ws.merge_cells(f"{get_column_letter(col)}3:{get_column_letter(col+1)}3")
        _headerCell(ws, 3, col, pName, bgColor=COLOR_SUBHEAD, wrap=True)
        _headerCell(ws, 4, col,   "IRR（含息）",      bgColor=COLOR_SUBHEAD, size=9, wrap=True)
        _headerCell(ws, 4, col+1, "簡單CAGR（含息）", bgColor=COLOR_SUBHEAD, size=9, wrap=True)
        col += 2

    ws.row_dimensions[3].height = 22
    ws.row_dimensions[4].height = 22

    # 建立 base results 查詢字典
    baseByName = {pr["name"]: pr for pr in allBaseResults}

    # 資料列
    row = 5
    for i, pr in enumerate(allDivResults):
        nameCell = ws.cell(row=row, column=1, value=pr["name"])
        nameCell.fill = PatternFill("solid", fgColor=COLOR_PERIOD)
        nameCell.font = Font(bold=True)
        nameCell.alignment = Alignment(horizontal="left", vertical="center")

        # 此組合是否有估算的股息（用於標記 *）
        has_estimated = any(
            info.get("years_estimated", 0) > 0
            for info in pr.get("div_info", {}).values()
        )
        if has_estimated:
            nameCell.value = pr["name"] + " *"

        baseResult = baseByName.get(pr["name"], {})

        col = 2
        for pName in periodNames:
            stats     = pr["periods"].get(pName)
            baseStats = baseResult.get("periods", {}).get(pName)

            for key in ("irr", "simple_cagr"):
                c = ws.cell(row=row, column=col)
                if stats is None:
                    c.value     = "資料不足"
                    c.alignment = Alignment(horizontal="center")
                    c.font      = Font(color="888888", italic=True, size=9)
                else:
                    val = stats.get(key)
                    if val is None:
                        c.value     = "—"
                        c.alignment = Alignment(horizontal="center")
                    else:
                        c.value         = val / 100
                        c.number_format = "0.00%"
                        c.alignment     = Alignment(horizontal="right")
                        c.fill          = PatternFill("solid", fgColor=COLOR_POS if val >= 0 else COLOR_NEG)

                    # 在 tooltip 中加入與基礎版的差異
                    base_val = (baseStats or {}).get(key)
                    if key == "irr" and stats and stats.get("start"):
                        diff_str = ""
                        if val is not None and base_val is not None:
                            diff_str = f"\n含息 vs 不含息 IRR：{val - base_val:+.2f}%"
                        div_income = stats.get("total_div_income", 0)
                        c.comment = Comment(
                            f"期間：{stats['start']} → {stats['end']}\n"
                            f"投入月數：{stats['num_months']}月（{stats['years']}年）\n"
                            f"總投入：{stats['total_invested']:,.0f} TWD\n"
                            f"最終淨值：{stats['final_value']:,.0f} TWD\n"
                            f"股息再投入累計：{div_income:,.0f} TWD"
                            + diff_str,
                            "auto"
                        )
                col += 1

        ws.row_dimensions[row].height = 20
        row += 1

    # 估算說明
    row += 1
    ws.merge_cells(f"A{row}:{lastCol}{row}")
    ws.cell(row=row, column=1,
            value="* 此組合含有部份 ETF 於無配息資料的年份採用均值估算（詳見「含股息再投資詳細」分頁）").font = Font(
                italic=True, size=9, color="666666")
    ws.row_dimensions[row].height = 16

    # 欄寬
    ws.column_dimensions["A"].width = 18
    for i in range(2, col):
        ws.column_dimensions[get_column_letter(i)].width = 11

    ws.freeze_panes = "B5"


# ─── Sheet 5：含股息再投資詳細統計 ────────────────────────────────

def writeDivDetailSheet(
    ws,
    allDivResults: list,
    divCalendars: dict,
    config: dict,
    monthlyAmount: float,
    nthDay: int,
):
    """
    含股息再投資詳細統計：年化報酬率、總報酬率，以及各 ETF 股息估算說明。
    """
    today       = date.today()
    periodNames = [p[0] for p in PERIODS]

    # 大標題
    lastCol = get_column_letter(11)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"定期定額含股息再投資詳細統計"
        f"  ▸  每月 {monthlyAmount:,.0f} TWD，第 {nthDay} 個交易日"
        f"  ▸  資料截至 {today}"
    )
    _headerCell(ws, 1, 1, ws["A1"].value, size=11, wrap=True)
    ws.row_dimensions[1].height = 28

    # 說明列
    ws.merge_cells(f"A2:{lastCol}2")
    ws["A2"] = (
        "無配息資料的年份以所有有資料年份的年均配息金額估算（下方表格有各 ETF 詳細說明）。"
        "股息再投入金額不計入「總投入」，但計入「最終淨值」。"
    )
    c = ws["A2"]
    c.fill = PatternFill("solid", fgColor="EBF3FB")
    c.font = Font(italic=True, size=9, color="444444")
    c.alignment = Alignment(horizontal="left", wrap_text=True)
    ws.row_dimensions[2].height = 24

    # 表頭
    headers = ["投資組合", "期間", "投入月數", "投入年數", "總投入 (TWD)",
               "總手續費 (TWD)", "股息再投入累計 (TWD)", "最終淨值 (TWD)",
               "總報酬率", "IRR（含息）", "簡單CAGR（含息）"]
    for col, h in enumerate(headers, start=1):
        _headerCell(ws, 3, col, h, bgColor=COLOR_SUBHEAD, wrap=True)
    ws.row_dimensions[3].height = 32

    row = 4
    for i, pr in enumerate(allDivResults):
        for j, pName in enumerate(periodNames):
            stats   = pr["periods"].get(pName)
            altFill = PatternFill("solid", fgColor=COLOR_ALT if j % 2 == 1 else "FFFFFF")

            nameCell = ws.cell(row=row, column=1, value=pr["name"] if j == 0 else "")
            if j == 0:
                nameCell.fill = PatternFill("solid", fgColor=COLOR_PERIOD)
                nameCell.font = Font(bold=True)
            nameCell.alignment = Alignment(horizontal="left", vertical="center")

            ws.cell(row=row, column=2, value=pName).alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=2).fill = altFill

            if stats is None:
                for col_ in range(3, 12):
                    c = ws.cell(row=row, column=col_, value="資料不足")
                    c.alignment = Alignment(horizontal="center")
                    c.font      = Font(color="888888", italic=True, size=9)
                    c.fill      = altFill
            else:
                def _set(c_, value, fmt=None, colorize=False, good_if_pos=True):
                    cell = ws.cell(row=row, column=c_, value=value)
                    cell.fill = altFill
                    if fmt:
                        cell.number_format = fmt
                    if colorize and value is not None:
                        is_good = value >= 0 if good_if_pos else value < 0
                        cell.fill = PatternFill("solid", fgColor=COLOR_POS if is_good else COLOR_NEG)
                    return cell

                _set(3, stats.get("num_months"),    "#,##0").alignment = Alignment(horizontal="right")
                _set(4, stats.get("years"),         "0.0").alignment   = Alignment(horizontal="right")
                _set(5, stats.get("total_invested"), "#,##0").alignment = Alignment(horizontal="right")
                _set(6, stats.get("total_fees"),     "#,##0").alignment = Alignment(horizontal="right")
                div_income = stats.get("total_div_income", 0)
                _set(7, div_income, "#,##0").alignment = Alignment(horizontal="right")
                _set(8, stats.get("final_value"),   "#,##0").alignment = Alignment(horizontal="right")

                tr = stats.get("total_return")
                _colorCell(ws, row, 9, tr / 100 if tr is not None else None)

                irr = stats.get("irr")
                _colorCell(ws, row, 10, irr / 100 if irr is not None else None)

                sc = stats.get("simple_cagr")
                _colorCell(ws, row, 11, sc / 100 if sc is not None else None)

            ws.row_dimensions[row].height = 18
            row += 1

        # 空白分隔行
        for col_ in range(1, 12):
            ws.cell(row=row, column=col_).fill = PatternFill("solid", fgColor="F0F0F0")
        row += 1

    # ── 股息估算說明區塊 ──────────────────────────────────────
    row += 1
    ws.merge_cells(f"A{row}:{lastCol}{row}")
    ws.cell(row=row, column=1, value="▌ 各 ETF 配息資料說明")
    _headerCell(ws, row, 1, ws.cell(row=row, column=1).value, bgColor=COLOR_SUBHEAD, size=10)
    ws.row_dimensions[row].height = 22
    row += 1

    note_headers = ["ETF", "有實際配息資料", "有資料年份數",
                    "估算年份數", "年均配息（每單位，TWD）",
                    "典型配息月份", "說明"]
    for col_, h in enumerate(note_headers, start=1):
        _headerCell(ws, row, col_, h, bgColor=COLOR_PERIOD,
                    fontColor="1F3864", bold=True, size=9, wrap=True)
    ws.row_dimensions[row].height = 28
    row += 1

    all_tickers = [e["ticker"] for e in config.get("etfs", [])]
    for ticker in all_tickers:
        info = divCalendars.get(ticker, {}).get("info", {})
        has_data     = info.get("has_actual_data", False)
        yrs_with     = info.get("years_with_data", 0)
        yrs_est      = info.get("years_estimated", 0)
        avg_annual   = info.get("avg_annual_per_unit", 0.0)
        typ_months   = info.get("typical_months", [])

        month_str = "、".join(f"{m}月" for m in typ_months) if typ_months else "—"
        if not has_data:
            note = "無配息資料，不納入股息再投資計算"
        elif yrs_est == 0:
            note = "所有模擬期間均有實際配息資料"
        else:
            note = (
                f"部分年份缺實際資料；{yrs_est} 年（全年或部分月份）以年均 "
                f"{avg_annual:.4f} TWD/單位估算（{month_str}配息）"
            )

        ws.cell(row=row, column=1, value=ticker).font = Font(bold=True)
        ws.cell(row=row, column=2, value="是" if has_data else "否")
        ws.cell(row=row, column=3, value=yrs_with if has_data else 0).number_format = "#,##0"
        ws.cell(row=row, column=4, value=yrs_est).number_format  = "#,##0"
        c_avg = ws.cell(row=row, column=5, value=avg_annual if has_data else None)
        c_avg.number_format = "0.0000"
        ws.cell(row=row, column=6, value=month_str)
        ws.cell(row=row, column=7, value=note).font = Font(italic=True, size=9, color="444444")

        for col_ in range(1, 8):
            ws.cell(row=row, column=col_).alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 18
        row += 1

    # 欄寬
    widths = [18, 10, 10, 10, 18, 15, 18, 18, 12, 14, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "C4"


if __name__ == "__main__":
    main()
