"""
【已廢棄 — 改用 scripts/fetch_expense_ratios.py（MoneyDJ 版）】

=== 腳本說明 ===
用 Playwright 載入各投信官方費率頁面，以 inner_text() 擷取 DOM 文字，
再用 regex 解析「總費用率」、「年費率」等關鍵字附近的百分比數字。

=== 執行結果摘要 ===

所有 7 支 ETF 均無法自動解析出「近一年總費用率」：

【元大投信（0050、00646、00679B）】
  頁面顯示「費率結構」（分層計費），而非「近一年總費用率」。
  頁面文字中找不到「總費用率」、「ETF費用」等欄位標題，
  regex 無法對應到正確數字。

  手動從 DOM 文字中擷取到的資料（僅供參考）：
    0050   經理費 約0.0828%（依規模，資料日期 2026/03/25），保管費 0.025%
           → 這是「現行混合費率」，非「近一年總費用率」
    00646  費率分層：0.50% / 0.40% / 0.30% / 0.11% / 0.10% / 0.08% / 0.06%
           → 為管理費各分層上限，非總費用率
    00679B 費率分層：0.12% / 0.10% / 0.07% / 0.06% / 0.04%
           → 管理費 + 保管費各分層，非總費用率

  根本原因：元大官網只揭露費率結構，「近一年總費用率」
            頁面提示需至「投信投顧公會網站 境內基金各項資料」查詢。

【富邦投信（00662、00709、00645）】
  頁面只顯示固定費率（管理費 + 保管費），無總費用率欄位。
  regex 搜尋「年費率」、「總費用率」均未找到。

  手動從 DOM 文字中擷取到的資料：
    00662  管理費 0.30%，保管費 0.16~0.21%（依規模分層）
           → 管理費 + 最高保管費 = 0.51%，與 config 值相符
    00709  管理費 0.50%，保管費 0.29%（固定）
           → 合計 0.79%，與 config 值完全吻合
    00645  管理費 0.50%，保管費 0.20%（固定）
           → 合計 0.70%，但 config 填 0.81%（差 0.11%，可能為指數授權費等雜費）

【國泰投信（00735）】
  cathaysite.com.tw → Access Denied，無法取得任何資料。
  已嘗試 Chromium / Firefox、不同 User-Agent，均被 Edgesuite CDN 封鎖。

=== 根本問題 ===
各投信官方頁面揭露的是「費率上限結構」，
「近一年總費用率」（年度實際總開銷比率，含指數授權費、審計費等）
需從年報或第三方彙整平台取得。

=== 結論 ===
此方法在元大、富邦頁面只能取得「費率結構」，無法取得可直接寫入
config.yaml 的「近一年總費用率」。請改用 MoneyDJ 版本。

=== 原始執行方式 ===
python scripts/deprecated/extract_expense_ratios_dom_text.py
"""
import re
import time
from playwright.sync_api import sync_playwright

ETF_PAGES = [
    {"ticker": "0050",   "url": "https://www.yuantaetfs.com/product/detail/0050/Basic_information",   "source": "yuanta"},
    {"ticker": "00646",  "url": "https://www.yuantaetfs.com/product/detail/00646/Basic_information",  "source": "yuanta"},
    {"ticker": "00679B", "url": "https://www.yuantaetfs.com/product/detail/00679B/Basic_information", "source": "yuanta"},
    {"ticker": "00662",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00662", "source": "fubon"},
    {"ticker": "00709",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00709", "source": "fubon"},
    {"ticker": "00645",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00645", "source": "fubon"},
    # ❌ Access Denied — cathaysite.com.tw 封鎖 headless browser
    {"ticker": "00735",  "url": "https://www.cathaysite.com.tw/ETF/detail/EAW", "source": "cathay"},
]

YUANTA_PATTERNS = [
    r"近一年總費用率[^\d]*?(\d+\.\d+)\s*%",
    r"總費用率[^\d]*?(\d+\.\d+)\s*%",
    r"費用率.*?(\d+\.\d+)\s*%",
]

FUBON_PATTERNS = [
    r"年費率[^\d]*?(\d+\.\d+)\s*%",
    r"總費用率[^\d]*?(\d+\.\d+)\s*%",
    r"費用率[^\d]*?(\d+\.\d+)\s*%",
]


def findRate(content: str, patterns: list) -> tuple:
    for pat in patterns:
        m = re.search(pat, content, re.DOTALL)
        if m:
            rate = float(m.group(1)) / 100
            return rate, m.group(0).strip()
    return None, None


def main():
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )

        for etf in ETF_PAGES:
            ticker = etf["ticker"]
            url = etf["url"]
            source = etf["source"]
            print(f"\n[{ticker}] 載入 {url}", flush=True)

            try:
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(2)
                content = page.inner_text("body")

                if source == "yuanta":
                    rate, matched = findRate(content, YUANTA_PATTERNS)
                elif source == "fubon":
                    rate, matched = findRate(content, FUBON_PATTERNS)
                else:
                    if "Access Denied" in content or len(content) < 100:
                        rate, matched = None, "Access Denied"
                    else:
                        rate, matched = findRate(content, FUBON_PATTERNS + YUANTA_PATTERNS)

                results[ticker] = {"rate": rate, "matched": matched}

                if rate is not None:
                    print(f"  → 費用率: {rate * 100:.2f}%")
                else:
                    all_pcts = re.findall(r"(\d+\.\d+)\s*%", content)
                    print(f"  → 無法解析。頁面百分比數字：{all_pcts[:10]}")

                page.close()

            except Exception as e:
                print(f"  → 錯誤：{e}")
                results[ticker] = {"rate": None, "matched": str(e)}

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
