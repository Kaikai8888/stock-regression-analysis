"""
投資組合分析服務：計算累積報酬、統計指標，並處理幣別換算與假日對齊。
"""
import numpy as np
import pandas as pd
from datetime import date


def buildPortfolioCumulativeReturns(
    conn,
    portfolioConfig: list,
    etfInfoMap: dict,
    startDate,
    endDate,
    baseCurrency: str = "TWD",
) -> dict:
    """
    回傳：{portfolioName: [{date, value}]}（起始值 = 100）

    etfInfoMap: {ticker: {currency, expense_ratio}}
    """
    # 1. 取所有需要的 ticker
    allTickers = set()
    for p in portfolioConfig:
        allTickers.update(p["allocations"].keys())

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
        cumReturns = computePortfolioReturns(pricesTwd, allocations, etfInfoMap)
        if cumReturns is not None:
            df = cumReturns.reset_index()
            df.columns = ["date", "value"]
            result[name] = df.to_dict("records")

    return result


def convertToCurrency(pricesDf: pd.DataFrame, fxDf: pd.DataFrame, targetCurrency: str) -> pd.DataFrame:
    """
    將 USD 計價的 ETF 轉換為目標幣別（TWD）。
    TWD ETF 不做轉換。
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
    etfInfoMap: dict,
) -> pd.Series | None:
    """
    計算投資組合的累積報酬序列（起始值 = 100）。
    """
    tickers = list(allocations.keys())
    subset = pricesDf[pricesDf["ticker"].isin(tickers)].copy()
    if subset.empty:
        return None

    subset["date"] = pd.to_datetime(subset["date"])
    pivot = subset.pivot(index="date", columns="ticker", values="close").sort_index()

    # 建立聯集日期索引，各 ticker 各自 forward-fill
    pivot = pivot.reindex(
        pd.date_range(pivot.index.min(), pivot.index.max(), freq="D")
    ).ffill()

    # 只保留至少有一個 ticker 有資料的日期
    pivot = pivot.dropna(how="all")

    # 計算日報酬，缺值填 0（該日不交易）
    dailyReturns = pivot.pct_change().fillna(0)

    # 扣管理費（年化費率 / 252）
    for ticker in tickers:
        if ticker in dailyReturns.columns:
            info = etfInfoMap.get(ticker, {})
            expenseRatio = info.get("expense_ratio") or 0.0
            dailyReturns[ticker] = dailyReturns[ticker] - expenseRatio / 252

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
    回傳：{total_return, cagr, volatility, sharpe, max_drawdown}
    """
    if not cumReturnsSeries:
        return {}

    values = pd.Series([r["value"] for r in cumReturnsSeries])
    dailyReturns = values.pct_change().dropna()

    totalReturn = (values.iloc[-1] / values.iloc[0]) - 1
    nDays = len(values)
    nYears = nDays / 252
    cagr = (1 + totalReturn) ** (1 / nYears) - 1 if nYears > 0 else 0
    volatility = dailyReturns.std() * np.sqrt(252)
    sharpe = (cagr / volatility) if volatility > 0 else 0

    # Max drawdown
    rolling_max = values.cummax()
    drawdown = (values - rolling_max) / rolling_max
    maxDrawdown = drawdown.min()

    return {
        "total_return": round(totalReturn * 100, 2),
        "cagr": round(cagr * 100, 2),
        "volatility": round(volatility * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(maxDrawdown * 100, 2),
    }
