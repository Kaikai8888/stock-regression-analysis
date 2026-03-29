"""
從 MoneyDJ「報酬走勢」頁籤抓取台灣 ETF 的季度追蹤差額，儲存至 etf_tracking_diff 資料表。

資料來源：
  https://www.moneydj.com/ETF/X/Basic/Basic0009.xdjhtm?etfid=TICKER.TW

抓取欄位：
  - 市價季報酬率（%）
  - 基準指數漲跌幅（%）
  - 差額（%）

執行邏輯：
  每次執行時先判斷「上一個已完成季度」，若 DB 已有該 ticker 的該季資料則跳過；
  否則抓取頁面上所有可用季度的歷史資料並一次寫入。

執行方式：
  python scripts/fetch_tracking_diff.py
"""
import re
import sys
import time
from datetime import date
from pathlib import Path

import urllib3
import requests
from bs4 import BeautifulSoup
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import initDb, DB_PATH

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

MONEYDJ_BASE_URL = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0009.xdjhtm?etfid={etfid}"
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


def getLastCompletedQuarter() -> str:
    """
    回傳上一個已完成季度的字串，格式為 'YYYYQN'。

    範例（今天 2026-03-29，Q1 2026 尚未結束）：
      月份 1-3  → 上年 Q4
      月份 4-6  → 本年 Q1
      月份 7-9  → 本年 Q2
      月份 10-12 → 本年 Q3
    """
    today = date.today()
    year, month = today.year, today.month
    if month <= 3:
        return f"{year - 1}Q4"
    elif month <= 6:
        return f"{year}Q1"
    elif month <= 9:
        return f"{year}Q2"
    else:
        return f"{year}Q3"


def hasQuarterData(conn, ticker: str, quarter: str) -> bool:
    """檢查 DB 中是否已有該 ticker 的該季資料。"""
    count = conn.execute(
        "SELECT COUNT(*) FROM etf_tracking_diff WHERE ticker = ? AND quarter = ?",
        [ticker, quarter],
    ).fetchone()[0]
    return count > 0


def _parseFloat(text: str) -> float | None:
    """將 '3.40' 或 '-1.23' 等字串轉為 float，無法解析則回傳 None。"""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace("--", "").replace("－", "-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _buildQuarterMapFromRows(rows: list) -> dict[int, str]:
    """
    從表格的前兩列（裸 <tr>，無 thead/tbody）建立 {欄位索引: 'YYYYQN'} 對應表。

    MoneyDJ stable3 結構：
      rows[0]：<th rowspan='2'>項目</th> <th colspan='1'>2026</th> <th colspan='4'>2025</th> ...
      rows[1]：<th>Q1</th> <th>Q4</th> <th>Q3</th> ...（無 rowspan 的第一格）
    """
    if len(rows) < 2:
        return {}

    # 展開年份欄（第 0 格是 rowspan=2 的列標題，跳過）
    year_sequence = []
    for th in rows[0].find_all(["th", "td"]):
        # 有 rowspan 的是列標題欄，跳過
        if th.get("rowspan"):
            continue
        text = th.get_text(strip=True)
        try:
            colspan = int(th.get("colspan", 1))
        except ValueError:
            colspan = 1
        year_sequence.extend([text] * colspan)

    # 第二列是季度標籤（Q1/Q4/Q3/Q2...）
    quarter_labels = [
        th.get_text(strip=True) for th in rows[1].find_all(["th", "td"])
    ]

    quarter_map = {}
    for i, q_text in enumerate(quarter_labels):
        year_text = year_sequence[i] if i < len(year_sequence) else ""
        year_match = re.search(r"(\d{4})", year_text)
        q_match = re.search(r"Q([1-4])", q_text, re.IGNORECASE)
        if year_match and q_match:
            # 資料欄在 cells 中的索引 = i + 1（第 0 格是列標題 <th>）
            quarter_map[i + 1] = f"{year_match.group(1)}Q{q_match.group(1)}"

    return quarter_map


def _findQuarterlyTable(soup) -> BeautifulSoup | None:
    """
    找到季報酬率表格。
    MoneyDJ 頁面上 id='stable3' 為季報酬率，id='stable2' 為月報酬率。
    備援方式：找任何含「市價季報酬率」文字的表格。
    """
    table = soup.find("table", {"id": "stable3"})
    if table:
        return table

    for tbl in soup.find_all("table"):
        if "市價季報酬率" in tbl.get_text():
            return tbl

    return None


def _buildEtfId(ticker: str, exchange: str) -> str:
    """
    組合 MoneyDJ 查詢用的 etfid。
    台灣掛牌 ETF（TWSE/TWO）加 .TW 後綴；美股不加。
    """
    if exchange in ("TWSE", "TWO"):
        return f"{ticker}.TW"
    return ticker


def parseTrackingDiff(ticker: str, exchange: str) -> list[dict]:
    """
    從 MoneyDJ Basic0009 頁面解析所有可用季度的追蹤差額資料。

    回傳 [{ticker, quarter, market_return, benchmark_return, diff}, ...]
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
    table = _findQuarterlyTable(soup)
    if table is None:
        print("    找不到季度報酬表格")
        return []

    # 表格使用裸 <tr>，沒有 <thead>/<tbody>
    all_rows = table.find_all("tr")
    if len(all_rows) < 5:  # 至少：年份列、季度列、市價列、基準列、差額列
        print(f"    表格列數不足（{len(all_rows)} 列）")
        return []

    quarter_map = _buildQuarterMapFromRows(all_rows)
    if not quarter_map:
        print("    無法解析欄位與季度對應")
        return []

    key_map = {
        "市價季報酬率": "market",
        "基準指數漲跌幅": "benchmark",
        "差額": "diff",
    }
    row_data: dict[str, dict[str, float | None]] = {
        "market": {},
        "benchmark": {},
        "diff": {},
    }

    # 從第 2 列（index 2）開始是資料列
    for tr in all_rows[2:]:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row_header = cells[0].get_text(strip=True)
        row_key = next(
            (v for k, v in key_map.items() if k in row_header), None
        )
        if row_key is None:
            continue

        for col_idx, quarter in quarter_map.items():
            if col_idx < len(cells):
                val = _parseFloat(cells[col_idx].get_text(strip=True))
                row_data[row_key][quarter] = val

    # 組合輸出
    all_quarters = set(row_data["market"].keys()) | set(row_data["benchmark"].keys())
    results = []
    for quarter in sorted(all_quarters):
        market = row_data["market"].get(quarter)
        benchmark = row_data["benchmark"].get(quarter)
        diff = row_data["diff"].get(quarter)
        if diff is None and market is not None and benchmark is not None:
            diff = round(market - benchmark, 2)
        results.append({
            "ticker": ticker,
            "quarter": quarter,
            "market_return": market,
            "benchmark_return": benchmark,
            "diff": diff,
        })

    return results


def upsertTrackingDiff(conn, rows: list[dict]) -> None:
    """將季度追蹤差額資料寫入 DB（upsert）。"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO etf_tracking_diff (ticker, quarter, market_return, benchmark_return, diff)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (ticker, quarter) DO UPDATE SET
            market_return    = excluded.market_return,
            benchmark_return = excluded.benchmark_return,
            diff             = excluded.diff
        """,
        [
            (r["ticker"], r["quarter"], r["market_return"], r["benchmark_return"], r["diff"])
            for r in rows
        ],
    )


def loadConfig() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = loadConfig()
    # 所有 ETF（台灣 TWSE/TWO + 美股 US）
    all_etfs = [
        {"ticker": etf["ticker"], "exchange": etf.get("exchange", "US")}
        for etf in config.get("etfs", [])
    ]

    last_quarter = getLastCompletedQuarter()

    print("MoneyDJ ETF 季度追蹤差額抓取")
    print(f"來源：{MONEYDJ_BASE_URL.format(etfid='TICKER')}")
    print(f"上一季：{last_quarter}")
    print("=" * 60)

    conn = initDb(str(DB_PATH))

    try:
        for etf in all_etfs:
            ticker = etf["ticker"]
            exchange = etf["exchange"]
            if hasQuarterData(conn, ticker, last_quarter):
                print(f"  [{ticker}] 已有 {last_quarter} 資料，跳過")
                continue

            print(f"  [{ticker}]", end="  ", flush=True)
            rows = parseTrackingDiff(ticker, exchange)
            if rows:
                upsertTrackingDiff(conn, rows)
                quarters = sorted(r["quarter"] for r in rows)
                print(f"已儲存 {len(rows)} 季（{quarters[0]} ~ {quarters[-1]}）")
            else:
                print("無資料或解析失敗")

            time.sleep(REQUEST_DELAY)
    finally:
        conn.close()

    print("=" * 60)
    print("完成。")



if __name__ == "__main__":
    main()
