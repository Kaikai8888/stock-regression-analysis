"""
初次爬取所有 ETF 歷史資料。
執行方式：python scripts/fetch_initial.py
"""
import sys
import time
import json
from pathlib import Path
from datetime import date, datetime

import urllib3
import yaml
import pandas as pd
import yfinance as yf
import requests

# TWSE API SSL 憑證缺少 Subject Key Identifier，Python requests 預設驗證會失敗。
# TWSE 為台灣官方政府機關，此處停用憑證驗證以確保可連線。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import initDb

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
TWSE_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TWSE_REQUEST_INTERVAL = 2.5   # 秒，避免超過 3 req/5sec 限制


# ──────────────────────────────────────────────
# 頂層流程
# ──────────────────────────────────────────────

def main():
    config = loadConfig()
    conn = initDb()
    print("資料庫初始化完成")

    usTickers = [e for e in config["etfs"] if e["exchange"] == "US"]
    twseTickers = [e for e in config["etfs"] if e["exchange"] == "TWSE"]
    twoTickers = [e for e in config["etfs"] if e["exchange"] == "TWO"]

    fetchUsEtfs(conn, usTickers)
    fetchFxHistory(conn)
    fetchTwseEtfs(conn, twseTickers)
    fetchTwoEtfs(conn, twoTickers)

    conn.close()
    print("\n初次爬取完成！")


def loadConfig(path: str = None) -> dict:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ──────────────────────────────────────────────
# 美國 ETF（yfinance）
# ──────────────────────────────────────────────

def fetchUsEtfs(conn, etfList: list):
    print("\n── 美國 ETF ──────────────────────────")
    for etf in etfList:
        ticker = etf["ticker"]
        print(f"  {ticker} ...", end=" ", flush=True)
        try:
            df = fetchUsEtfHistory(ticker)
            if df.empty:
                print("無資料，跳過")
                updateFetchStatus(conn, ticker, None, "error", "yfinance 回傳空資料")
                continue
            upsertPrices(conn, df, ticker, "USD")
            info = fetchUsEtfInfo(ticker, etf)
            upsertInfo(conn, info)
            lastDate = df["date"].max()
            updateFetchStatus(conn, ticker, lastDate, "ok")
            print(f"OK（{len(df)} 筆，最新 {lastDate}）")
        except Exception as e:
            print(f"錯誤：{e}")
            updateFetchStatus(conn, ticker, None, "error", str(e))


def yfHistToDf(hist: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance history(auto_adjust=False) 結果 → 標準價格 DataFrame。

    auto_adjust=False 時 Close 仍已還原分割、但未還原配息（即實際市價基準），
    Adj Close 則為分割＋配息皆還原的含息價。兩者分別存入 close / adj_close：
    close 給 DCA 模擬（避免配息被重複計算），adj_close 給總報酬類的主報表。
    """
    hist = hist.reset_index()
    hist.columns = [c.lower() for c in hist.columns]
    hist["date"] = pd.to_datetime(hist["date"]).dt.date
    hist = hist.rename(columns={"adj close": "adj_close"})
    cols = ["date", "open", "high", "low", "close", "adj_close", "volume"]
    cols = [c for c in cols if c in hist.columns]
    return hist[cols].dropna(subset=["close"])


def fetchUsEtfHistory(ticker: str) -> pd.DataFrame:
    hist = yf.Ticker(ticker).history(period="max", auto_adjust=False)
    if hist.empty:
        return pd.DataFrame()
    return yfHistToDf(hist)


def fetchUsEtfInfo(ticker: str, configEntry: dict) -> dict:
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName") or configEntry.get("name", ticker)
        inceptionTs = info.get("fundInceptionDate")
        inceptionDate = date.fromtimestamp(inceptionTs) if inceptionTs else None
    except Exception:
        name = configEntry.get("name", ticker)
        inceptionDate = None

    return {
        "ticker": ticker,
        "name": name,
        "exchange": "US",
        "currency": "USD",
        "expense_ratio": configEntry.get("expense_ratio"),
        "inception_date": inceptionDate,
    }


# ──────────────────────────────────────────────
# 匯率 USD→TWD（yfinance）
# ──────────────────────────────────────────────

def cleanFxOutliers(df: pd.DataFrame, threshold: float = 0.15) -> pd.DataFrame:
    """
    修正 yfinance TWD=X 歷史資料中偶爾出現的單日離群壞值（例如曾發現
    2011-10-25、2014-12-31 兩天匯率被錯誤記錄為 1.8、3.67，正常應在 30
    上下；重新抓取仍會拿到同樣的壞值，代表壞資料就在 Yahoo 來源本身）。

    判斷邏輯：某天與前一天的變動、及與後一天的變動都超過 threshold，
    但前一天與後一天彼此相近（代表隔天幾乎完全「彈回」），視為單日離群點，
    以前後兩個有效值線性插值取代，避免污染下游報酬率／波動度計算。
    """
    df = df.sort_values("date").reset_index(drop=True)
    rates = df["rate"].to_numpy()
    isOutlier = [False] * len(rates)

    for i in range(1, len(rates) - 1):
        prevRate, curRate, nextRate = rates[i - 1], rates[i], rates[i + 1]
        if not (prevRate and curRate and nextRate):
            continue
        dropRatio = abs(curRate / prevRate - 1)
        recoverRatio = abs(nextRate / curRate - 1)
        roundTripRatio = abs(nextRate / prevRate - 1)
        if dropRatio > threshold and recoverRatio > threshold and roundTripRatio < threshold / 3:
            isOutlier[i] = True

    if any(isOutlier):
        bad = df.loc[isOutlier, "date"].tolist()
        print(f"    偵測到 {len(bad)} 筆離群匯率資料，已用鄰近日期插值修正：{bad}")
        df.loc[isOutlier, "rate"] = None
        df["rate"] = df["rate"].interpolate(method="linear")

    return df


def fetchFxHistory(conn):
    print("\n── 匯率 USD/TWD ──────────────────────")
    print("  TWD=X ...", end=" ", flush=True)
    try:
        hist = yf.Ticker("TWD=X").history(period="max", auto_adjust=False)
        if hist.empty:
            print("無資料，跳過")
            return
        hist = hist.reset_index()
        hist.columns = [c.lower() for c in hist.columns]
        hist["date"] = pd.to_datetime(hist["date"]).dt.date
        # Yahoo Finance TWD=X = USD per TWD；需反轉為 TWD per USD
        # Yahoo Finance TWD=X 的 close 值即為 TWD per USD（例如 31.43）
        # 直接儲存，不做反轉（舊注解有誤）
        hist["rate"] = hist["close"]
        rows = hist[["date", "rate"]].dropna()
        rows = cleanFxOutliers(rows)
        conn.executemany(
            "INSERT OR REPLACE INTO fx_rates (date, from_ccy, to_ccy, rate) VALUES (?, 'USD', 'TWD', ?)",
            rows.values.tolist()
        )
        print(f"OK（{len(rows)} 筆）")
    except Exception as e:
        print(f"錯誤：{e}")


# ──────────────────────────────────────────────
# TPEx OTC ETF（yfinance TICKER.TWO）
# ──────────────────────────────────────────────

def fetchTwoEtfs(conn, etfList: list):
    """掛牌於 TPEx（OTC 櫃買中心）的 ETF，使用 yfinance TICKER.TWO 抓取。"""
    if not etfList:
        return
    print("\n── TPEx OTC ETF ──────────────────────────")
    for etf in etfList:
        ticker = etf["ticker"]
        print(f"  {ticker} ...", end=" ", flush=True)
        try:
            df = fetchTwoEtfHistory(ticker)
            if df.empty:
                print("無資料，跳過")
                updateFetchStatus(conn, ticker, None, "error", "yfinance TWO 回傳空資料")
                continue
            upsertPrices(conn, df, ticker, "TWD")
            info = buildTwseInfo(ticker, etf)
            info["exchange"] = "TWO"
            upsertInfo(conn, info)
            lastDate = df["date"].max()
            updateFetchStatus(conn, ticker, lastDate, "ok")
            print(f"OK（{len(df)} 筆，最新 {lastDate}）")
        except Exception as e:
            print(f"錯誤：{e}")
            updateFetchStatus(conn, ticker, None, "error", str(e))


def fetchTwoEtfHistory(ticker: str) -> pd.DataFrame:
    hist = yf.Ticker(f"{ticker}.TWO").history(period="max", auto_adjust=False)
    if hist.empty:
        return pd.DataFrame()
    df = yfHistToDf(hist)
    return fixUnadjustedSplits(df) if not df.empty else df


# ──────────────────────────────────────────────
# 台灣 ETF（twstock + TWSE API fallback）
# ──────────────────────────────────────────────

def fetchTwseEtfs(conn, etfList: list):
    print("\n── 台灣 ETF（TWSE）───────────────────")
    for etf in etfList:
        ticker = etf["ticker"]
        print(f"  {ticker} ...", end=" ", flush=True)
        try:
            df = fetchTwseEtfHistory(ticker)
            if df.empty:
                print("無資料，跳過")
                updateFetchStatus(conn, ticker, None, "error", "TWSE 回傳空資料")
                continue
            upsertPrices(conn, df, ticker, "TWD")
            info = buildTwseInfo(ticker, etf)
            upsertInfo(conn, info)
            lastDate = df["date"].max()
            updateFetchStatus(conn, ticker, lastDate, "ok")
            print(f"OK（{len(df)} 筆，最新 {lastDate}）")
        except Exception as e:
            print(f"錯誤：{e}")
            updateFetchStatus(conn, ticker, None, "error", str(e))


def fixUnadjustedSplits(df: pd.DataFrame, threshold: float = 0.30) -> pd.DataFrame:
    """
    修正 yfinance 對台股 ETF 漏記的分割／減資事件。

    曾發現 0050.TW 在 2014-01-02 的 close 從前一日 37.41 直接掉到 9.33
    （比例約 1/4），但 yfinance 的 Stock Splits 動作序列該日卻是 0，導致
    auto_adjust=True 完全沒有回溯調整——連重新整批抓取歷史資料也一樣，
    因為調整演算法本身就沒有這筆分割事件可用。

    判斷邏輯：單日價格變動超過 threshold，且隔天沒有立刻彈回原水準
    （藉此跟「單日壞資料、隔天就恢復」的離群點區分開來），視為真實發生但
    未被來源記錄的分割事件，將該日之前的所有價格依比例縮放，使整條序列
    基準一致。
    """
    df = df.sort_values("date").reset_index(drop=True)
    closes = df["close"].to_numpy()

    for i in range(1, len(closes)):
        prevClose, curClose = closes[i - 1], closes[i]
        if not prevClose or not curClose:
            continue
        ratio = curClose / prevClose
        if abs(ratio - 1) <= threshold:
            continue
        if i + 1 < len(closes) and closes[i + 1]:
            recoverRatio = closes[i + 1] / prevClose
            if abs(recoverRatio - 1) < threshold / 2:
                continue  # 隔天幾乎彈回原水準，屬單日離群壞值而非分割

        print(f"    偵測到疑似未回溯調整的分割事件於 {df['date'].iloc[i]}"
              f"（比例 {ratio:.4f}），已將該日之前價格等比例縮放")
        for col in ["open", "high", "low", "close", "adj_close"]:
            if col in df.columns:
                df.loc[: i - 1, col] = df.loc[: i - 1, col] * ratio
        closes = df["close"].to_numpy()

    return df


def fetchTwseEtfHistory(ticker: str) -> pd.DataFrame:
    """
    主要策略：yfinance TICKER.TW（auto_adjust=False：close 已還原分割、
             未還原配息；adj_close 為含息還原價）
    備用策略：twstock 函式庫 → TWSE STOCK_DAY API（僅有市價，無 adj_close）
    """
    # 優先使用 yfinance，取得還原分割後的歷史價格
    try:
        hist = yf.Ticker(f"{ticker}.TW").history(period="max", auto_adjust=False)
        if not hist.empty:
            df = yfHistToDf(hist)
            if not df.empty:
                return fixUnadjustedSplits(df)
    except Exception:
        pass

    # 備用：twstock
    try:
        import twstock
        stock = twstock.Stock(ticker)
        inceptionYear = 2010
        if hasattr(stock, "realtime") and stock.data:
            pass
        else:
            stock.fetch_from(inceptionYear, 1)
        records = stock.data
        if records:
            rows = []
            for r in records:
                rows.append({
                    "date": r.date.date() if hasattr(r.date, "date") else r.date,
                    "open": float(r.open) if r.open else None,
                    "high": float(r.high) if r.high else None,
                    "low": float(r.low) if r.low else None,
                    "close": float(r.close) if r.close else None,
                    "volume": int(r.capacity) if r.capacity else None,
                })
            df = pd.DataFrame(rows).dropna(subset=["close"])
            if not df.empty:
                return df
    except Exception:
        pass

    # 最終備用：TWSE STOCK_DAY API 逐月爬取
    return fetchTwseViaApi(ticker)


def fetchTwseViaApi(ticker: str, startYear: int = 2010) -> pd.DataFrame:
    """逐月呼叫 TWSE STOCK_DAY API，從 startYear/1 爬到今天。"""
    today = date.today()
    allRows = []

    currentYear = startYear
    currentMonth = 1

    while (currentYear, currentMonth) <= (today.year, today.month):
        dateStr = f"{currentYear}{currentMonth:02d}01"
        try:
            resp = requests.get(
                TWSE_URL,
                params={"response": "json", "date": dateStr, "stockNo": ticker},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
                verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") == "OK" and "data" in data:
                    fields = data.get("fields", [])
                    for row in data["data"]:
                        try:
                            parsed = parseTwseRow(row, currentYear)
                            if parsed:
                                allRows.append(parsed)
                        except Exception:
                            pass
        except Exception:
            pass

        time.sleep(TWSE_REQUEST_INTERVAL)

        currentMonth += 1
        if currentMonth > 12:
            currentMonth = 1
            currentYear += 1

    if not allRows:
        return pd.DataFrame()

    df = pd.DataFrame(allRows).dropna(subset=["close"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def parseTwseRow(row: list, year: int) -> dict | None:
    """
    TWSE STOCK_DAY 回傳格式（欄位順序）：
    [0] 日期（民國年），[1] 成交股數，[2] 成交金額，[3] 開盤價，
    [4] 最高價，[5] 最低價，[6] 收盤價，[7] 漲跌價差，[8] 成交筆數
    """
    if len(row) < 7:
        return None

    # 民國年轉西元年
    dateParts = row[0].replace("/", "-").split("-")
    if len(dateParts) != 3:
        return None
    rocYear = int(dateParts[0])
    adDate = date(rocYear + 1911, int(dateParts[1]), int(dateParts[2]))

    def parsePrice(s):
        s = str(s).replace(",", "").strip()
        return float(s) if s and s != "--" else None

    def parseVol(s):
        s = str(s).replace(",", "").strip()
        return int(s) if s and s != "--" else None

    return {
        "date": adDate,
        "open": parsePrice(row[3]),
        "high": parsePrice(row[4]),
        "low": parsePrice(row[5]),
        "close": parsePrice(row[6]),
        "volume": parseVol(row[1]),
    }


def buildTwseInfo(ticker: str, configEntry: dict) -> dict:
    return {
        "ticker": ticker,
        "name": configEntry.get("name", ticker),
        "exchange": "TWSE",
        "currency": "TWD",
        "expense_ratio": configEntry.get("expense_ratio"),
        "inception_date": None,
    }


# ──────────────────────────────────────────────
# 寫入資料庫
# ──────────────────────────────────────────────

def upsertPrices(conn, df: pd.DataFrame, ticker: str, currency: str):
    # 所有抓取路徑均回傳完整歷史，採整批覆寫：先刪除既有資料再寫入，
    # 避免來源已下架/修正的日期殘留舊值（adj_close 回溯還原也因此保持一致基準）
    df = df.copy()
    df["ticker"] = ticker
    df["currency"] = currency
    for col in ["open", "high", "low", "adj_close", "volume"]:
        if col not in df.columns:
            df[col] = None
    rows = df[["ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "currency"]].values.tolist()
    conn.execute("DELETE FROM etf_prices WHERE ticker = ?", [ticker])
    conn.executemany(
        "INSERT OR REPLACE INTO etf_prices (ticker, date, open, high, low, close, adj_close, volume, currency) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows
    )


def upsertInfo(conn, info: dict):
    conn.execute(
        """
        INSERT OR REPLACE INTO etf_info
            (ticker, name, exchange, currency, expense_ratio, inception_date, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        [
            info["ticker"], info.get("name"), info.get("exchange"),
            info.get("currency"), info.get("expense_ratio"), info.get("inception_date"),
        ]
    )


def updateFetchStatus(conn, ticker: str, lastDate, status: str, errorMsg: str = None):
    conn.execute(
        """
        INSERT OR REPLACE INTO fetch_status
            (ticker, last_fetched_date, last_fetch_ts, status, error_msg)
        VALUES (?, ?, current_timestamp, ?, ?)
        """,
        [ticker, lastDate, status, errorMsg]
    )


if __name__ == "__main__":
    main()
