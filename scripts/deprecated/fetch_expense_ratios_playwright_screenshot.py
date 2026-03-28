"""
【已廢棄 — 改用 scripts/fetch_expense_ratios.py（MoneyDJ 版）】

=== 腳本說明 ===
用 Playwright headless Chromium 對各投信官方費率頁面截全頁圖，
存放於 ./tmp/expense_<ticker>.png，供人工或 AI 圖像辨識費用數字。

=== 執行結果摘要 ===

成功截圖（6/7 支）：
  0050   → tmp/expense_0050.png   ✅ 元大台灣50  頁面載入正常
  00646  → tmp/expense_00646.png  ✅ 元大S&P500  頁面載入正常
  00679B → tmp/expense_00679B.png ✅ 元大美債20年 頁面載入正常
  00662  → tmp/expense_00662.png  ✅ 富邦NASDAQ  頁面載入正常
  00709  → tmp/expense_00709.png  ✅ 富邦歐洲    頁面載入正常
  00645  → tmp/expense_00645.png  ✅ 富邦日本    頁面載入正常

失敗（1/7 支）：
  00735  → tmp/expense_00735.png  ❌ 國泰臺韓科技
    原因：cathaysite.com.tw 由 Edgesuite CDN 封鎖 headless browser，
          回傳「Access Denied」，截圖只記錄到錯誤頁面。
    已嘗試 Firefox + 不同 User-Agent，結果相同。

=== 根本問題 ===
1. 全頁截圖解析度不足：頁面高度超過 3000px，費用數字縮小至無法精確辨識。
2. 各投信官方頁面顯示的是「費率結構（分層計費）」，
   而非「近一年總費用率」這個年化總數字：
     - 元大投信：顯示經理費分層（依規模），保管費分層
       例：0050 現行經理費約 0.0828%（依當前規模計），保管費 0.025%
       → 總費用率需加上指數授權費、審計費等，官方頁面未直接揭露
     - 富邦投信：顯示固定管理費 + 保管費分層，無總費用率
       例：00709 管理費 0.50% + 保管費 0.29% = 0.79%（其他費用未計入）
3. 即使截圖清晰，仍需人工/AI 逐張辨識，無法自動更新 config.yaml。

=== 結論 ===
此方法不適合作為自動化費率更新工具。
請改用 MoneyDJ 版本（scripts/fetch_expense_ratios.py），
該版本直接抓取含「近一年總費用率」的第三方彙整頁面。

=== 原始執行方式 ===
python scripts/deprecated/fetch_expense_ratios_playwright_screenshot.py
"""
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

TMP_DIR = Path(__file__).parent.parent.parent / "tmp"

# 各 ETF 官方費率頁面（已透過 TWSE 官方 ETF 清單確認為合法投信機構網址）
ETF_PAGES = [
    # 元大投信 yuantaetfs.com
    {"ticker": "0050",   "url": "https://www.yuantaetfs.com/product/detail/0050/Basic_information"},
    {"ticker": "00646",  "url": "https://www.yuantaetfs.com/product/detail/00646/Basic_information"},
    {"ticker": "00679B", "url": "https://www.yuantaetfs.com/product/detail/00679B/Basic_information"},
    # 富邦投信 websys.fsit.com.tw
    {"ticker": "00662",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00662"},
    {"ticker": "00709",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00709"},
    {"ticker": "00645",  "url": "https://websys.fsit.com.tw/FubonETF/Fund/Profile.aspx?stkId=00645"},
    # 國泰投信 cathaysite.com.tw — ❌ Access Denied，headless browser 被 CDN 封鎖
    {"ticker": "00735",  "url": "https://www.cathaysite.com.tw/ETF/detail/EAW"},
]


def main():
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-TW",
        )

        for etf in ETF_PAGES:
            ticker = etf["ticker"]
            url = etf["url"]
            out = TMP_DIR / f"expense_{ticker}.png"

            print(f"  {ticker} ... ", end="", flush=True)
            try:
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)

                # 等待費用相關區塊出現
                for keyword in ["費用", "經理費", "expense", "fee"]:
                    try:
                        page.wait_for_selector(
                            f"text=/{keyword}/i", timeout=5000
                        )
                        break
                    except Exception:
                        pass

                # 額外等待確保動態資料載入完成
                time.sleep(2)

                page.screenshot(path=str(out), full_page=True)
                page.close()
                print(f"OK → {out.name}")
            except Exception as e:
                print(f"錯誤：{e}")

        context.close()
        browser.close()

    print(f"\n截圖完成，存放於：{TMP_DIR}")


if __name__ == "__main__":
    main()
