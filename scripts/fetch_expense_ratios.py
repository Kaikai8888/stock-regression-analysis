"""
從 MoneyDJ 抓取台灣 ETF 近一年總管理費用率，供對照或自動更新 config.yaml。

資料來源：https://www.moneydj.com/ETF/X/Basic/Basic0004.xdjhtm?etfid=TICKER.TW
費用欄位：「總管理費用(%)」= 年化總費用率，含經理費、保管費、手續費、交易稅及其他費用。

US ETF（VT、IXN、IEF）不在此腳本範圍，其費率請至各官方網站確認（config.yaml 有連結）。

執行方式：
  python scripts/fetch_expense_ratios.py           # 只查詢，不修改 config.yaml
  python scripts/fetch_expense_ratios.py --update  # 查詢後自動更新 config.yaml
"""
import argparse
import re
import time
from pathlib import Path

import urllib3
import requests
from bs4 import BeautifulSoup
import yaml

# MoneyDJ 的 SSL 憑證缺少 Subject Key Identifier，Python requests 會驗證失敗。
# 此站為已知合法台灣財金資訊平台，此處停用憑證驗證以確保可連線。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

MONEYDJ_URL = "https://www.moneydj.com/ETF/X/Basic/Basic0004.xdjhtm?etfid={ticker}.TW"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.moneydj.com/",
}

# 只抓台灣 ETF；US ETF 費率以官方頁面為準，每年手動確認
TWSE_TICKERS = ["0050", "00646", "00679B", "00662", "00709", "00645", "00735"]

REQUEST_DELAY = 1.5  # 秒，避免對 MoneyDJ 造成過多請求


def fetchExpenseRatio(ticker: str) -> float | None:
    """
    從 MoneyDJ Basic0004 頁面抓取單支 ETF 的「總管理費用(%)」。

    頁面 HTML 結構：
      <tr>
        <td>總管理費用(%)</td>
        <td>0.54 (含 0.24 非管理費用)</td>
      </tr>

    回傳費用率小數（例如 0.0054），失敗回傳 None。
    """
    url = MONEYDJ_URL.format(ticker=ticker)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        # MoneyDJ 的 Content-Type 宣告 ISO-8859-1，但實際內容是 UTF-8
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # MoneyDJ 頁面結構：<th>總管理費用(%)</th><td>0.22 (含 0.07 非管理費用)</td>
        for th in soup.find_all("th"):
            if "總管理費用" in th.get_text():
                # 找同一 row 中緊接在此 <th> 之後的 <td>
                td = th.find_next_sibling("td")
                if td:
                    raw = td.get_text(strip=True)
                    # 格式："0.22 (含 0.07 非管理費用)" → 取第一個數字
                    m = re.search(r"(\d+\.?\d*)", raw)
                    if m:
                        return float(m.group(1)) / 100

    except requests.RequestException as e:
        print(f"    請求失敗：{e}")
    except Exception as e:
        print(f"    解析失敗：{e}")

    return None


def loadConfig() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def updateConfigFile(newRates: dict) -> None:
    """
    以 regex 替換 config.yaml 中對應 ticker 的 expense_ratio 值。
    使用文字替換而非 yaml.dump，確保保留原有注解。
    """
    text = CONFIG_PATH.read_text(encoding="utf-8")

    for ticker, rate in newRates.items():
        # 找到 "ticker: XXXX" 後緊接著的 expense_ratio 行並替換數值
        # 跨行匹配：ticker 行 → 任意行 → expense_ratio 行
        pattern = (
            r'(ticker:\s*["\']?' + re.escape(ticker) + r'["\']?'
            r'(?:\n(?!.*ticker:).*)*?\n\s*expense_ratio:\s*)\d+\.\d+'
        )
        replacement = rf'\g<1>{rate:.4f}'
        new_text = re.sub(pattern, replacement, text)
        if new_text != text:
            text = new_text
            print(f"    [{ticker}] expense_ratio → {rate:.4f}  ({rate * 100:.2f}%)")
        else:
            print(f"    [{ticker}] ⚠ 未能在 config.yaml 找到對應 ticker，請手動確認")

    CONFIG_PATH.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="從 MoneyDJ 查詢台灣 ETF 總費用率（--update 自動寫入 config.yaml）"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="將 MoneyDJ 取得的費用率直接更新到 config.yaml",
    )
    args = parser.parse_args()

    print("MoneyDJ 台灣 ETF 費用率查詢")
    print(f"來源：{MONEYDJ_URL.format(ticker='TICKER')}")
    print("=" * 60)

    fetchedRates = {}
    for ticker in TWSE_TICKERS:
        print(f"  [{ticker}]", end="  ", flush=True)
        rate = fetchExpenseRatio(ticker)
        fetchedRates[ticker] = rate
        if rate is not None:
            print(f"總管理費用率：{rate * 100:.2f}%")
        else:
            print("無法取得")
        time.sleep(REQUEST_DELAY)

    # 對照 config.yaml 現值
    print()
    print("=" * 60)
    print("對照 config.yaml 現值")
    print("=" * 60)

    config = loadConfig()
    configRates = {
        e["ticker"]: e.get("expense_ratio")
        for e in config.get("etfs", [])
        if e.get("exchange") == "TWSE"
    }

    changedRates = {}
    for ticker in TWSE_TICKERS:
        fetched = fetchedRates.get(ticker)
        current = configRates.get(ticker)

        if fetched is None:
            status = "⚠ 無法取得"
            current_str = f"{current * 100:.2f}%" if current else "N/A"
            print(f"  {ticker:8s}  {status}  (config 現值：{current_str})")
        else:
            current_str = f"{current * 100:.2f}%" if current else "N/A"
            diff = (fetched - current) if current else 0
            diff_str = f"  Δ{diff * 100:+.2f}%" if current else ""
            changed = current is not None and abs(diff) > 0.0001
            mark = "→ 需更新" if changed else "✓ 一致"
            print(
                f"  {ticker:8s}  MoneyDJ：{fetched * 100:.2f}%  config：{current_str}{diff_str}  {mark}"
            )
            if changed:
                changedRates[ticker] = fetched

    # 更新 config.yaml
    print()
    if changedRates:
        print(f"  共 {len(changedRates)} 支 ETF 費用率有異動：{list(changedRates.keys())}")
        if args.update:
            print("  更新 config.yaml...")
            updateConfigFile(changedRates)
            print("  完成。")
        else:
            print("  加上 --update 旗標可自動更新 config.yaml")
    else:
        print("  所有費用率與 config.yaml 一致，無需更新。")


if __name__ == "__main__":
    main()
