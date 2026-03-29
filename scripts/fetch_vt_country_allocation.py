"""
從 MoneyDJ「持股分佈(依區域)」頁面抓取 VT ETF 的國家佔比，
統計出美國、日本、歐洲、中國、台灣的比例，
並計算剔除「其他」之後這些區域的相對佔比。

資料來源：
  https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=VT

執行方式：
  python scripts/fetch_vt_country_allocation.py
  make vt-country
"""

import sys
from pathlib import Path

import urllib3
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MONEYDJ_URL = "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=VT"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.moneydj.com/",
}

# ── 歐洲國家清單（繁體中文，含英國）──────────────────────────────────────
EUROPEAN_COUNTRIES = {
    "英國", "法國", "德國", "瑞士", "荷蘭", "瑞典", "丹麥", "西班牙",
    "義大利", "意大利", "芬蘭", "挪威", "比利時", "奧地利", "葡萄牙",
    "愛爾蘭", "盧森堡", "波蘭", "捷克", "匈牙利", "希臘",
}

# ── 目標區域定義 ─────────────────────────────────────────────────────────
TARGET_REGIONS = {
    "美國": {"美國"},
    "日本": {"日本"},
    "中國": {"中國", "中国"},
    "台灣": {"台灣", "臺灣"},
    "歐洲": EUROPEAN_COUNTRIES,
}


# ── 資料抓取 ─────────────────────────────────────────────────────────────

def fetchPage() -> BeautifulSoup:
    resp = requests.get(MONEYDJ_URL, headers=HEADERS, timeout=15, verify=False)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return BeautifulSoup(resp.text, "html.parser")


def findRegionTable(soup: BeautifulSoup):
    """尋找「持股分佈(依區域)」表格。"""
    # 先嘗試已知 MoneyDJ 慣用 ID
    for table_id in ("stable0", "stable1", "divRegion"):
        tbl = soup.find("table", {"id": table_id})
        if tbl and "區域" in tbl.get_text():
            return tbl

    # 備援：找任何含「區域」與「比例」的表格
    for tbl in soup.find_all("table"):
        text = tbl.get_text()
        if "區域" in text and "比例" in text:
            return tbl

    return None


def parseRegionTable(table) -> list[dict]:
    """
    解析區域表格，回傳 [{"country": "美國", "weight": 61.5}, ...]。

    表格欄位：顏色 | 區域 | 投資金額(美元) | 比例(%)
    """
    rows = table.find_all("tr")
    result = []
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 4:
            continue
        country = cells[1].get_text(strip=True)
        pct_text = cells[3].get_text(strip=True).replace(",", "").replace("%", "")
        try:
            pct = float(pct_text)
        except ValueError:
            continue
        # 略過標頭列（「區域」字樣本身）
        if country in ("區域", ""):
            continue
        result.append({"country": country, "weight": pct})
    return result


# ── 彙整與計算 ───────────────────────────────────────────────────────────

def aggregateRegions(holdings: list[dict]) -> dict:
    """
    將每個國家歸入目標區域或「其他」，回傳各區域加總比例（%）。
    """
    country_to_region: dict[str, str] = {}
    for region_label, countries in TARGET_REGIONS.items():
        for c in countries:
            country_to_region[c] = region_label

    region_weights: dict[str, float] = {k: 0.0 for k in TARGET_REGIONS}
    region_weights["其他"] = 0.0
    unmatched: list[dict] = []

    for item in holdings:
        region = country_to_region.get(item["country"])
        if region:
            region_weights[region] += item["weight"]
        else:
            region_weights["其他"] += item["weight"]
            unmatched.append(item)

    total = sum(region_weights.values())
    return {**region_weights, "_total": total, "_unmatched": unmatched}


def calcRelative(region_weights: dict, exclude: set[str] | None = None) -> dict[str, float]:
    """
    剔除指定區域後，剩餘目標區域重新比例化至合計 100%。

    exclude：要剔除的區域名稱集合（預設只剔除「其他」）。
    """
    exclude = exclude or set()
    keys = [k for k in TARGET_REGIONS if k not in exclude]
    target_sum = sum(region_weights[k] for k in keys)
    if target_sum == 0:
        return {k: 0.0 for k in keys}
    return {k: region_weights[k] / target_sum * 100 for k in keys}


# ── 輸出 ─────────────────────────────────────────────────────────────────

def printReport(
    holdings: list[dict],
    region_weights: dict,
    relative_no_other: dict[str, float],
    relative_no_china: dict[str, float],
) -> None:
    total_countries = len(holdings)
    total_weight = region_weights["_total"]

    print("=" * 60)
    print("VT ETF 國家佔比分析（資料來源：MoneyDJ）")
    print("=" * 60)
    print(f"共 {total_countries} 個國家，合計權重：{total_weight:.2f}%\n")

    print("── 各區域原始佔比（含其他） ────────────────────")
    for region in list(TARGET_REGIONS) + ["其他"]:
        w = region_weights[region]
        bar = "█" * int(w)
        print(f"  {region:4s}  {w:6.2f}%  {bar}")
    print()

    print("── 剔除其他後的相對佔比（合計 100%） ──────────")
    for region, w in relative_no_other.items():
        bar = "█" * int(w)
        print(f"  {region:4s}  {w:6.2f}%  {bar}")
    print()

    print("── 剔除其他＋中國後的相對佔比（合計 100%） ────")
    for region, w in relative_no_china.items():
        bar = "█" * int(w)
        print(f"  {region:4s}  {w:6.2f}%  {bar}")
    print()

    if region_weights["_unmatched"]:
        unmatched = sorted(region_weights["_unmatched"], key=lambda x: -x["weight"])
        print("── 歸入「其他」的國家（前 15 名） ──────────────")
        for item in unmatched[:15]:
            print(f"  {item['country']:20s}  {item['weight']:.2f}%")
        if len(unmatched) > 15:
            print(f"  ... 共 {len(unmatched)} 個國家")
        print()

    print("=" * 60)


# ── 主程式 ───────────────────────────────────────────────────────────────

def main():
    print("正在從 MoneyDJ 抓取 VT 國家佔比資料...")
    print(f"來源：{MONEYDJ_URL}\n")

    try:
        soup = fetchPage()
    except requests.RequestException as e:
        print(f"HTTP 錯誤：{e}", file=sys.stderr)
        sys.exit(1)

    table = findRegionTable(soup)
    if table is None:
        print("錯誤：找不到區域配置表格", file=sys.stderr)
        # 輸出所有 table id 供偵錯
        ids = [t.get("id", "(no id)") for t in soup.find_all("table")]
        print(f"  頁面上的 table id：{ids}", file=sys.stderr)
        sys.exit(1)

    holdings = parseRegionTable(table)
    if not holdings:
        print("錯誤：表格解析結果為空", file=sys.stderr)
        sys.exit(1)

    region_weights = aggregateRegions(holdings)
    relative_no_other = calcRelative(region_weights)
    relative_no_china = calcRelative(region_weights, exclude={"中國"})
    printReport(holdings, region_weights, relative_no_other, relative_no_china)


if __name__ == "__main__":
    main()
