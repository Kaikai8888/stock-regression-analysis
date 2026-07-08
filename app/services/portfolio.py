"""
投資組合分析服務：計算累積報酬、統計指標，並處理幣別換算與假日對齊。
"""
import numpy as np
import pandas as pd
from datetime import date


def buildPortfolioCumulativeReturns(
    conn,
    portfolioConfig: list,
    startDate,
    endDate,
    baseCurrency: str = "TWD",
) -> dict:
    """
    回傳：{portfolioName: [{date, value}]}（起始值 = 100）

    baseCurrency 傳入非 "TWD" 的值（例如 "NATIVE"）時不做幣別轉換，
    回傳各 ETF 原始計價幣別的報酬序列，供「原幣報酬」對照使用。
    """
    # 1. 取所有需要的 ticker（排除權重為 0 的 ticker，例如 config.yaml 用
    #    "VT": 0 明確標示不投資某標的；若不排除，該 ticker 的價格起始日
    #    仍會拉長「最長期間」查詢區間，導致回傳序列多出一段不該存在、
    #    報酬率被 fillna(0) 硬填為 0% 的空白期）
    allTickers = set()
    for p in portfolioConfig:
        allTickers.update(t for t, w in p["allocations"].items() if w)

    # 2. 載入價格（加入幣別欄位）
    tickerList = list(allTickers)
    placeholders = ",".join(["?" for _ in tickerList])
    startStr = startDate.isoformat() if hasattr(startDate, "isoformat") else str(startDate)
    endStr = endDate.isoformat() if hasattr(endDate, "isoformat") else str(endDate)

    pricesDf = conn.execute(
        f"""
        SELECT ticker, date, close, currency
        FROM etf_prices
        WHERE ticker IN ({placeholders})
          AND date BETWEEN ? AND ?
        ORDER BY ticker, date
        """,
        tickerList + [startStr, endStr],
    ).df()

    if pricesDf.empty:
        return {}

    # 3. 載入匯率
    fxDf = conn.execute(
        "SELECT date, rate FROM fx_rates WHERE date BETWEEN ? AND ? ORDER BY date",
        [startStr, endStr],
    ).df()

    # 4. 換算為 baseCurrency
    pricesTwd = convertToCurrency(pricesDf, fxDf, baseCurrency)

    # 5. 計算各投資組合累積報酬
    result = {}
    for portfolio in portfolioConfig:
        name = portfolio["name"]
        allocations = portfolio["allocations"]
        cumReturns = computePortfolioReturns(pricesTwd, allocations)
        if cumReturns is not None:
            df = cumReturns.reset_index()
            df.columns = ["date", "value"]
            result[name] = df.to_dict("records")

    return result


def convertToCurrency(pricesDf: pd.DataFrame, fxDf: pd.DataFrame, targetCurrency: str) -> pd.DataFrame:
    """
    將 USD 計價的 ETF 轉換為目標幣別（TWD）。
    TWD ETF 不做轉換。
    targetCurrency 傳入非 "TWD" 的值（例如 "NATIVE"）時視為不轉換，
    直接回傳各 ETF 原始計價幣別的價格，供「原幣報酬」對照使用。
    """
    if targetCurrency == "TWD":
        usdMask = pricesDf["currency"] == "USD"
        if usdMask.any() and not fxDf.empty:
            fxSeries = fxDf.set_index("date")["rate"]
            fxSeries.index = pd.to_datetime(fxSeries.index)

            prices = pricesDf.copy()
            prices["date"] = pd.to_datetime(prices["date"])

            # Forward-fill 匯率到所有日期
            dateRange = pd.date_range(prices["date"].min(), prices["date"].max(), freq="D")
            fxFull = fxSeries.reindex(dateRange).ffill().bfill()

            usdRows = prices[usdMask].copy()
            usdRows["fx"] = usdRows["date"].map(fxFull)
            usdRows["close"] = usdRows["close"] * usdRows["fx"]
            usdRows = usdRows.drop(columns=["fx"])

            prices.loc[usdMask, "close"] = usdRows["close"]
            return prices
        return pricesDf
    return pricesDf


def computePortfolioReturns(
    pricesDf: pd.DataFrame,
    allocations: dict,
) -> pd.Series | None:
    """
    計算投資組合的累積報酬序列（起始值 = 100）。
    """
    # 權重為 0 的 ticker 排除在外，避免其價格起始日拉長查詢區間
    tickers = [t for t, w in allocations.items() if w]
    subset = pricesDf[pricesDf["ticker"].isin(tickers)].copy()
    if subset.empty:
        return None

    subset["date"] = pd.to_datetime(subset["date"])
    pivot = subset.pivot(index="date", columns="ticker", values="close").sort_index()

    # 只從「所有 ticker 都已有資料」的最早共同日期開始計算。若從更早的
    # 聯集日期起算，尚未上市的 ticker 會被下面的 fillna(0) 誤當成「當天
    # 報酬 0%」，等於假裝那筆錢閒置不動，嚴重低估回測期間的實際報酬
    # （例如 0050 2009 年才有資料，若混進 2001 年就有資料的 IXN 一起算，
    # 2001-2009 這段會把 0050 的權重當成 0% 報酬，拖累整體報酬率）。
    firstValidDates = [d for d in (pivot[t].first_valid_index() for t in pivot.columns) if d is not None]
    if not firstValidDates:
        return None
    pivot = pivot[pivot.index >= max(firstValidDates)]
    if pivot.empty:
        return None

    # 建立聯集日期索引，各 ticker 各自 forward-fill
    pivot = pivot.reindex(
        pd.date_range(pivot.index.min(), pivot.index.max(), freq="D")
    ).ffill()

    # 只保留至少有一個 ticker 有資料的日期
    pivot = pivot.dropna(how="all")

    # 計算日報酬，缺值填 0（該日不交易）
    # 注意：close 為 yfinance auto_adjust=True 的還原價格，本身已反映
    # 基金每日內扣的實際管理費（費用已內含於市價/淨值中），故不再另行扣除
    # expense_ratio，避免重複扣費。
    dailyReturns = pivot.pct_change().fillna(0)

    # 加權混合（只用有資料的 ticker）
    validTickers = [t for t in tickers if t in dailyReturns.columns]
    totalWeight = sum(allocations[t] for t in validTickers)
    if totalWeight == 0:
        return None

    portfolioReturn = sum(
        dailyReturns[t] * (allocations[t] / totalWeight)
        for t in validTickers
    )

    # 累積乘積
    cumReturns = (1 + portfolioReturn).cumprod() * 100
    cumReturns.index = cumReturns.index.strftime("%Y-%m-%d")
    return cumReturns


def computePortfolioStats(cumReturnsSeries: list) -> dict:
    """
    輸入：[{date, value}] 序列（value 起始 = 100）
    回傳：{total_return, cagr, volatility, sharpe, max_drawdown,
           max_drawdown_start, max_drawdown_end}
    max_drawdown_start/end 為最大回撤發生的期間（前波高點日期 → 最低點日期）。
    """
    if not cumReturnsSeries:
        return {}

    values = pd.Series([r["value"] for r in cumReturnsSeries])
    dailyReturns = values.pct_change().dropna()

    totalReturn = (values.iloc[-1] / values.iloc[0]) - 1
    from datetime import date as _date
    nDays = (_date.fromisoformat(cumReturnsSeries[-1]["date"])
             - _date.fromisoformat(cumReturnsSeries[0]["date"])).days
    nYears = nDays / 365.25
    cagr = (1 + totalReturn) ** (1 / nYears) - 1 if nYears > 0 else 0
    volatility = dailyReturns.std() * np.sqrt(252)
    sharpe = (cagr / volatility) if volatility > 0 else 0

    # Max drawdown，並回推發生的期間：先找最低點（谷底），
    # 再從谷底往前找波段最高點（峰頂）
    rolling_max = values.cummax()
    drawdown = (values - rolling_max) / rolling_max
    troughIdx = int(drawdown.idxmin())
    maxDrawdown = drawdown.iloc[troughIdx]
    peakIdx = int(values.iloc[:troughIdx + 1].idxmax())

    return {
        "total_return": round(totalReturn * 100, 2),
        "cagr": round(cagr * 100, 2),
        "volatility": round(volatility * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(maxDrawdown * 100, 2),
        "max_drawdown_start": cumReturnsSeries[peakIdx]["date"],
        "max_drawdown_end": cumReturnsSeries[troughIdx]["date"],
    }
