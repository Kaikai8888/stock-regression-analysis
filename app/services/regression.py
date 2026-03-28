"""
回歸分析服務：計算各 ETF 相對於基準標的的 alpha、beta、R²。
"""
import numpy as np
import pandas as pd
from scipy import stats


def computeRegression(pricesDf: pd.DataFrame, benchmarkTicker: str) -> dict:
    """
    輸入：
      pricesDf - 欄位 [date, ticker, close]（已幣別換算，正規化前的價格）
    回傳：
      {ticker: {alpha, beta, r_squared, p_value, nObs}}
    """
    pivot = pricesDf.pivot(index="date", columns="ticker", values="close").sort_index()

    if benchmarkTicker not in pivot.columns:
        return {}

    # 日對數報酬
    logReturns = np.log(pivot / pivot.shift(1)).dropna()

    benchmarkReturns = logReturns[benchmarkTicker]
    result = {}

    for ticker in pivot.columns:
        if ticker == benchmarkTicker:
            continue
        tickerReturns = logReturns[ticker]
        # 對齊（inner join on date）
        aligned = pd.concat([benchmarkReturns, tickerReturns], axis=1).dropna()
        if len(aligned) < 10:
            continue
        x = aligned.iloc[:, 0].values
        y = aligned.iloc[:, 1].values

        slope, intercept, rValue, pValue, _ = stats.linregress(x, y)
        result[ticker] = {
            "alpha": round(intercept * 252, 6),    # 年化 alpha
            "beta": round(slope, 4),
            "r_squared": round(rValue ** 2, 4),
            "p_value": round(pValue, 6),
            "n_obs": len(aligned),
        }

    return result


def computeRollingBeta(pricesDf: pd.DataFrame, benchmarkTicker: str, windowDays: int = 60) -> dict:
    """
    回傳：{ticker: [{date, beta}]}
    """
    pivot = pricesDf.pivot(index="date", columns="ticker", values="close").sort_index()
    if benchmarkTicker not in pivot.columns:
        return {}

    logReturns = np.log(pivot / pivot.shift(1)).dropna()
    benchmarkReturns = logReturns[benchmarkTicker]
    result = {}

    for ticker in pivot.columns:
        if ticker == benchmarkTicker:
            continue

        aligned = pd.concat([benchmarkReturns, logReturns[ticker]], axis=1).dropna()
        aligned.columns = ["bench", "ticker"]

        rollingBetas = []
        for i in range(windowDays, len(aligned) + 1):
            window = aligned.iloc[i - windowDays:i]
            if window["bench"].std() == 0:
                continue
            slope, _, _, _, _ = stats.linregress(window["bench"].values, window["ticker"].values)
            rollingBetas.append({
                "date": str(aligned.index[i - 1]),
                "beta": round(slope, 4),
            })
        result[ticker] = rollingBetas

    return result
