"""
增量更新腳本：查詢每支 ETF 上次抓到哪天，往後補抓新資料。

執行方式：
  python scripts/update_data.py --once         # 執行一次後結束
  python scripts/update_data.py --schedule     # 常駐，每 24 小時執行一次
  python scripts/update_data.py --hours 12     # 常駐，每 12 小時執行一次

系統 cron 範例（每個工作日早上 8 點）：
  0 8 * * 1-5 cd /path/to/stock-regression && python scripts/update_data.py --once
"""
import sys
import time
import argparse
from pathlib import Path
from datetime import date, timedelta

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import initDb
from scripts.fetch_initial import (
    loadConfig,
    fetchUsEtfHistory,
    fetchUsEtfInfo,
    fetchTwoEtfHistory,
    fetchTwseEtfHistory,
    buildTwseInfo,
    upsertPrices,
    upsertInfo,
    updateFetchStatus,
    cleanFxOutliers,
)

import yfinance as yf
import pandas as pd

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


# ──────────────────────────────────────────────
# 頂層流程
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETF 資料增量更新")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", default=True, help="執行一次後結束（預設）")
    group.add_argument("--schedule", action="store_true", help="常駐執行，定時更新")
    parser.add_argument("--hours", type=int, default=24, help="常駐模式的更新間隔（小時，預設 24）")
    args = parser.parse_args()

    if args.schedule:
        runScheduled(args.hours)
    else:
        config = loadConfig()
        conn = initDb()
        runUpdate(conn, config)
        conn.close()
        print("\n更新完成！")


def runUpdate(conn, config: dict):
    stale = getTickersNeedingUpdate(conn, config)
    if not stale:
        print("所有 ETF 資料均為最新，無需更新。")
        return

    print(f"\n需要更新的 ETF：{[t['ticker'] for t in stale]}")

    for item in stale:
        ticker = item["ticker"]
        exchange = item["exchange"]
        since = item["lastFetchedDate"]
        etfConfig = item["config"]

        print(f"\n  {ticker}（since {since}）...", end=" ", flush=True)
        try:
            if exchange == "US":
                df = fetchUsEtfIncremental(ticker, since)
                currency = "USD"
            elif exchange == "TWO":
                df = fetchTwoEtfIncremental(ticker, since)
                currency = "TWD"
            else:
                df = fetchTwseEtfIncremental(ticker, since)
                currency = "TWD"

            if df.empty:
                print("無新資料")
                updateFetchStatus(conn, ticker, since, "ok")
                continue

            upsertPrices(conn, df, ticker, currency)
            lastDate = df["date"].max()
            updateFetchStatus(conn, ticker, lastDate, "ok")
            print(f"OK（全量覆寫 {len(df)} 筆，最新 {lastDate}）")
        except Exception as e:
            print(f"錯誤：{e}")
            updateFetchStatus(conn, ticker, since, "error", str(e))

    # 更新匯率
    updateFx(conn, config)


def runScheduled(intervalHours: int):
    from apscheduler.schedulers.background import BackgroundScheduler

    print(f"常駐模式啟動，每 {intervalHours} 小時更新一次")
    config = loadConfig()
    conn = initDb()

    def job():
        print(f"\n[{date.today()}] 開始更新...")
        runUpdate(conn, config)

    scheduler = BackgroundScheduler()
    scheduler.add_job(job, "interval", hours=intervalHours)
    scheduler.start()

    job()  # 啟動時立即執行一次

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        conn.close()
        print("\n排程已停止")


# ──────────────────────────────────────────────
# 查詢需要更新的 ETF
# ──────────────────────────────────────────────

def getTickersNeedingUpdate(conn, config: dict) -> list[dict]:
    """
    回傳需要更新的 ETF 清單。
    條件：last_fetched_date < 昨天，或 status = 'error'，或從未抓過。
    """
    yesterday = date.today() - timedelta(days=1)
    configMap = {e["ticker"]: e for e in config["etfs"]}

    rows = conn.execute("""
        SELECT ticker, last_fetched_date, status
        FROM fetch_status
    """).fetchall()
    fetchedMap = {r[0]: {"lastDate": r[1], "status": r[2]} for r in rows}

    result = []
    for etf in config["etfs"]:
        ticker = etf["ticker"]
        fetched = fetchedMap.get(ticker)

        if fetched is None:
            # 從未抓過
            since = None
        elif fetched["status"] == "error":
            since = fetched["lastDate"]
        elif fetched["lastDate"] is None or fetched["lastDate"] < yesterday:
            since = fetched["lastDate"]
        else:
            continue  # 已是最新

        result.append({
            "ticker": ticker,
            "exchange": etf["exchange"],
            "lastFetchedDate": since,
            "config": etf,
        })

    return result


# ──────────────────────────────────────────────
# 增量爬取
# ──────────────────────────────────────────────

# adj_close（含息還原價）是「回溯調整」：每逢新的除息事件，整段歷史的
# adj_close 都會改寫。若只增量追加新日期，舊資料列的 adj_close 會停留在
# 舊的還原基準，整條序列基準不一致。因此更新一律全量重抓並整批覆寫
# （yfinance 一次請求即可取得全部歷史，資料量小，成本可忽略）。

def fetchUsEtfIncremental(ticker: str, since) -> pd.DataFrame:
    return fetchUsEtfHistory(ticker)


def fetchTwoEtfIncremental(ticker: str, since) -> pd.DataFrame:
    """TPEx OTC ETF 更新，使用 yfinance TICKER.TWO 全量重抓。"""
    return fetchTwoEtfHistory(ticker)


def fetchTwseEtfIncremental(ticker: str, since) -> pd.DataFrame:
    # fetchTwseEtfHistory 內部已含 twstock → TWSE API 備援
    return fetchTwseEtfHistory(ticker)


# ──────────────────────────────────────────────
# 匯率增量更新
# ──────────────────────────────────────────────

def updateFx(conn, config: dict):
    # 只有存在 USD ETF 時才需要更新匯率
    hasUsd = any(e["exchange"] == "US" for e in config["etfs"])
    if not hasUsd:
        return

    print("\n  匯率 USD/TWD ...", end=" ", flush=True)
    try:
        lastDate = conn.execute(
            "SELECT MAX(date) FROM fx_rates"
        ).fetchone()[0]

        if lastDate:
            startStr = lastDate.isoformat() if hasattr(lastDate, "isoformat") else str(lastDate)
            hist = yf.Ticker("TWD=X").history(start=startStr, auto_adjust=False)
        else:
            hist = yf.Ticker("TWD=X").history(period="max", auto_adjust=False)

        if hist.empty:
            print("無新資料")
            return

        hist = hist.reset_index()
        hist.columns = [c.lower() for c in hist.columns]
        hist["date"] = pd.to_datetime(hist["date"]).dt.date
        # Yahoo Finance TWD=X 的 close 值即為 TWD per USD（例如 31.43），直接儲存
        hist["rate"] = hist["close"]
        rows = hist[["date", "rate"]].dropna()
        rows = cleanFxOutliers(rows)

        if lastDate:
            rows = rows[rows["date"] > lastDate]

        if rows.empty:
            print("無新資料")
            return

        conn.executemany(
            "INSERT OR REPLACE INTO fx_rates (date, from_ccy, to_ccy, rate) VALUES (?, 'USD', 'TWD', ?)",
            rows.values.tolist()
        )
        print(f"OK（新增 {len(rows)} 筆）")
    except Exception as e:
        print(f"錯誤：{e}")


if __name__ == "__main__":
    main()
