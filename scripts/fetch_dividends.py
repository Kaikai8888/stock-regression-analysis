"""
抓取 ETF 歷史配息資料，儲存至 etf_dividends 資料表。

資料來源（依序嘗試）：
  1. yfinance Ticker.dividends — 完整歷史配息，且金額與價格同為「分割還原」
     基準（例如 0050 於 2025-06 一拆四後，分割前的每單位配息會等比例縮小），
     與 etf_prices 的單位數計算基準一致。
  2. MoneyDJ Basic0005（備援）— 僅近二年紀錄：
     https://www.moneydj.com/ETF/X/Basic/Basic0005.xdjhtm?etfid=TICKER[.TW]

抓取欄位：
  - 除息日 (ex_date)
  - 配息總額 (amount_per_unit)：每單位配息金額（原始幣別）
  - 幣別 (currency)

說明：
  yfinance 提供完整歷史，DCA 分析僅在極少數缺資料年份需要以年均估算
  （標注「估算」）。僅剩 MoneyDJ 備援資料（近二年）時估算年份較多。

每次執行時，若 DB 中已有該 ticker 近 45 天的配息資料，則跳過重抓。
強制更新請刪除 etf_dividends 中的相關資料後再執行。

執行方式：
  python scripts/fetch_dividends.py
"""
import sys
import time
from datetime import date, datetime
from pathlib import Path

import urllib3
import requests
import yfinance as yf
from bs4 import BeautifulSoup
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import initDb, DB_PATH

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

MONEYDJ_BASE_URL = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0005.xdjhtm?etfid={etfid}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.moneydj.com/",
}

REQUEST_DELAY = 1.5  # 秒


def _buildEtfId(ticker: str, exchange: str) -> str:
    """台灣掛牌 ETF 加 .TW 後綴；美股不加。"""
    if exchange in ("TWSE", "TWO"):
        return f"{ticker}.TW"
    return ticker


def _buildYfSymbol(ticker: str, exchange: str) -> str:
    """yfinance 代號：TWSE 加 .TW、TPEx 加 .TWO、美股不加。"""
    if exchange == "TWSE":
        return f"{ticker}.TW"
    if exchange == "TWO":
        return f"{ticker}.TWO"
    return ticker


def fetchDividendsYfinance(ticker: str, exchange: str) -> list[dict]:
    """
    從 yfinance 抓取完整歷史配息。
    回傳 [{ticker, ex_date, amount_per_unit, currency}, ...]，失敗回傳空列表。
    """
    currency = "TWD" if exchange in ("TWSE", "TWO") else "USD"
    try:
        series = yf.Ticker(_buildYfSymbol(ticker, exchange)).dividends
    except Exception as e:
        print(f"    yfinance 請求失敗：{e}")
        return []
    if series is None or series.empty:
        return []

    return [
        {
            "ticker":          ticker,
            "ex_date":         ts.date(),
            "amount_per_unit": float(amount),
            "currency":        currency,
        }
        for ts, amount in series.items()
        if amount and amount > 0
    ]


def _parseDate(text: str) -> date | None:
    """'2025/07/18' → date(2025, 7, 18)，解析失敗回傳 None。"""
    try:
        return datetime.strptime(text.strip(), "%Y/%m/%d").date()
    except ValueError:
        return None


def _parseFloat(text: str) -> float | None:
    cleaned = text.strip().replace(",", "").replace("--", "").replace("－", "-")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _findDividendTable(soup):
    """
    尋找配息紀錄表格。
    依序嘗試常見 ID，再 fallback 至「含『除息日』文字的表格」。
    """
    for table_id in ["ctl00_ctl00_MainContent_MainContent_divRawDividend",
                     "divDividend", "stable0", "stable1"]:
        tbl = soup.find("table", {"id": table_id})
        if tbl:
            return tbl

    for tbl in soup.find_all("table"):
        if "除息日" in tbl.get_text():
            return tbl

    return None


def parseDividends(ticker: str, exchange: str) -> list[dict]:
    """
    從 MoneyDJ Basic0005 頁面解析配息紀錄。
    回傳 [{ticker, ex_date, amount_per_unit, currency}, ...]。
    空列表表示無資料或解析失敗。
    """
    etf_id = _buildEtfId(ticker, exchange)
    url = MONEYDJ_BASE_URL.format(etfid=etf_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        print(f"    請求失敗：{e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = _findDividendTable(soup)
    if table is None:
        return []

    all_rows = table.find_all("tr")
    if len(all_rows) < 2:
        return []

    # 解析表頭，找到「除息日」和「配息總額」的欄位索引
    header_cells = all_rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True) for c in header_cells]

    ex_date_col  = next((i for i, h in enumerate(headers) if "除息日" in h), None)
    amount_col   = next((i for i, h in enumerate(headers) if "配息總額" in h or "配息金額" in h), None)
    currency_col = next((i for i, h in enumerate(headers) if "幣別" in h), None)

    if ex_date_col is None or amount_col is None:
        return []

    default_currency = "TWD" if exchange in ("TWSE", "TWO") else "USD"

    results = []
    for tr in all_rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if len(cells) <= max(ex_date_col, amount_col):
            continue

        ex_date = _parseDate(cells[ex_date_col])
        amount  = _parseFloat(cells[amount_col])

        raw_currency = (
            cells[currency_col] if currency_col is not None and currency_col < len(cells) else ""
        )
        currency = (
            raw_currency
            .replace("台幣", "TWD")
            .replace("美元", "USD")
            .strip()
            or default_currency
        )

        if ex_date is None or amount is None or amount <= 0:
            continue

        results.append({
            "ticker":          ticker,
            "ex_date":         ex_date,
            "amount_per_unit": amount,
            "currency":        currency,
        })

    return results


def upsertDividends(conn, ticker: str, rows: list[dict]) -> None:
    """
    將配息資料整批覆寫入 DB（先刪後插）。
    配息金額在分割後會整段回溯還原（與價格同基準），不可增量混用新舊基準。
    """
    if not rows:
        return
    conn.execute("DELETE FROM etf_dividends WHERE ticker = ?", [ticker])
    conn.executemany(
        """
        INSERT INTO etf_dividends (ticker, ex_date, amount_per_unit, currency)
        VALUES (?, ?, ?, ?)
        """,
        [(r["ticker"], r["ex_date"], r["amount_per_unit"], r["currency"]) for r in rows],
    )


def hasRecentData(conn, ticker: str) -> bool:
    """
    若 DB 中已有該 ticker 近 45 天內的配息資料，視為已是最新，跳過重抓。
    45 天確保每季至少更新一次。
    """
    count = conn.execute(
        """
        SELECT COUNT(*) FROM etf_dividends
        WHERE ticker = ?
          AND ex_date >= current_date - INTERVAL 45 DAY
        """,
        [ticker],
    ).fetchone()[0]
    return count > 0


def loadConfig() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = loadConfig()
    all_etfs = [
        {"ticker": etf["ticker"], "exchange": etf.get("exchange", "US")}
        for etf in config.get("etfs", [])
    ]

    print("ETF 配息紀錄抓取（yfinance 完整歷史，MoneyDJ 近二年備援）")
    print("=" * 60)

    conn = initDb(str(DB_PATH))
    try:
        for etf in all_etfs:
            ticker   = etf["ticker"]
            exchange = etf["exchange"]

            if hasRecentData(conn, ticker):
                print(f"  [{ticker}] 已有近期配息資料，跳過")
                continue

            print(f"  [{ticker}]", end="  ", flush=True)
            rows = fetchDividendsYfinance(ticker, exchange)
            source = "yfinance"
            if not rows:
                rows = parseDividends(ticker, exchange)
                source = "MoneyDJ"
                time.sleep(REQUEST_DELAY)

            if rows:
                upsertDividends(conn, ticker, rows)
                ex_dates = sorted(r["ex_date"] for r in rows)
                print(f"已儲存 {len(rows)} 筆（{ex_dates[0]} ~ {ex_dates[-1]}，來源 {source}）")
            else:
                print("無配息資料或解析失敗（無配息 ETF？）")
    finally:
        conn.close()

    print("=" * 60)
    print("完成。")


if __name__ == "__main__":
    main()
