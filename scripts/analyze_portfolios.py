"""
計算各投資組合在不同時間區間的年化報酬率（CAGR），輸出 Excel 報表。

重複使用 app/services/portfolio.py 的核心計算邏輯。

時間區間：最長期間、10年、5年、3年、1年、6個月、3個月、1個月
統計指標：CAGR、總報酬、年化波動度、Sharpe Ratio、最大回撤

執行方式：
  python scripts/analyze_portfolios.py
  python scripts/analyze_portfolios.py --output reports/analysis_2026.xlsx
"""
import sys
import argparse
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import numpy as np
import yaml
import duckdb
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers as xlnumbers
)
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import DB_PATH
from app.services.portfolio import (
    buildPortfolioCumulativeReturns,
    computePortfolioStats,
)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
REPORTS_DIR = Path(__file__).parent.parent / "reports"

# 分析時間區間（名稱, relativedelta 或 None=最長）
PERIODS = [
    ("最長期間",  None),
    ("15年",      relativedelta(years=15)),
    ("10年",      relativedelta(years=10)),
    ("5年",       relativedelta(years=5)),
    ("3年",       relativedelta(years=3)),
    ("1年",       relativedelta(years=1)),
    ("6個月",     relativedelta(months=6)),
    ("3個月",     relativedelta(months=3)),
    ("1個月",     relativedelta(months=1)),
]

STATS_COLS = ["CAGR", "總報酬", "年化波動度", "Sharpe", "最大回撤"]

# ETF 個別分析時間區間（由短到長）
ETF_PERIODS = [
    ("1個月",    relativedelta(months=1)),
    ("3個月",    relativedelta(months=3)),
    ("6個月",    relativedelta(months=6)),
    ("1年",      relativedelta(years=1)),
    ("3年",      relativedelta(years=3)),
    ("5年",      relativedelta(years=5)),
    ("10年",     relativedelta(years=10)),
    ("15年",     relativedelta(years=15)),
    ("最長期間", None),
]

# 統計指標（年化波動度／Sharpe／最大回撤）額外計算的期間
STAT_PERIOD_NAMES = ["10年", "15年", "最長期間"]

# Excel 樣式常數
COLOR_HEADER   = "1F3864"   # 深藍
COLOR_SUBHEAD  = "2F5496"   # 中藍
COLOR_PERIOD   = "D6E4F0"   # 淺藍
COLOR_POS      = "C6EFCE"   # 淡綠
COLOR_NEG      = "FFC7CE"   # 淡紅
COLOR_NEUTRAL  = "FFFFFF"


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="投資組合報酬率分析 → Excel 報表")
    parser.add_argument("--output", type=str, help="輸出 Excel 路徑（預設：reports/portfolio_analysis_YYYY-MM-DD.xlsx）")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(exist_ok=True)
    outputPath = Path(args.output) if args.output else (
        REPORTS_DIR / f"portfolio_analysis_{date.today()}.xlsx"
    )

    config = loadConfig()

    if not Path(DB_PATH).exists():
        print("⚠ 資料庫不存在，請先執行 scripts/fetch_initial.py")
        sys.exit(1)

    conn = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        etfInfoMap = buildEtfInfoMap(conn, config)
        today = getLatestDate(conn)
        if today is None:
            print("⚠ 資料庫無價格資料，請先執行 scripts/fetch_initial.py")
            sys.exit(1)

        print(f"資料最新日期：{today}")
        print(f"分析 {len(config['portfolios'])} 個投資組合 × {len(PERIODS)} 個時間區間\n")

        allResults = []
        for portfolio in config["portfolios"]:
            print(f"  計算：{portfolio['name']} ...", end=" ", flush=True)
            result = analyzePortfolio(conn, portfolio, today)
            allResults.append(result)
            print("完成")

        print(f"\n分析 {len(config['etfs'])} 隻 ETF 個別報酬率 ...")
        etfResults = analyzeEtfs(conn, config, etfInfoMap, today)
        print("完成")

    finally:
        conn.close()

    exportToExcel(allResults, config, outputPath, etfResults)
    print(f"\n報表已輸出至：{outputPath}")


# ─────────────────────────────────────────────────────────────
# 資料載入
# ─────────────────────────────────────────────────────────────

def loadConfig() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def buildEtfInfoMap(conn, config: dict) -> dict:
    """
    建立 {ticker: {name, exchange, currency, expense_ratio}}。
    名稱優先從 config.yaml 取（DB 可能缺 TWSE ETF 的中文名）。
    費用率優先從 config.yaml 取（最新，由 fetch_expense_ratios.py 更新）。
    """
    dbRows = conn.execute(
        "SELECT ticker, name, exchange, currency, expense_ratio FROM etf_info"
    ).fetchall()
    dbMap = {r[0]: {"name": r[1], "exchange": r[2], "currency": r[3], "expense_ratio": r[4]}
             for r in dbRows}

    result = {}
    for etf in config["etfs"]:
        ticker = etf["ticker"]
        db = dbMap.get(ticker, {})
        result[ticker] = {
            "name":          etf.get("name") or db.get("name") or ticker,
            "exchange":      etf.get("exchange", db.get("exchange", "")),
            "currency":      "TWD" if etf.get("exchange") == "TWSE" else "USD",
            "expense_ratio": etf.get("expense_ratio") or db.get("expense_ratio") or 0.0,
        }
    return result


def getLatestDate(conn) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM etf_prices").fetchone()
    if row and row[0]:
        d = row[0]
        return d if isinstance(d, date) else date.fromisoformat(str(d))
    return None


def getEarliestDate(conn) -> date | None:
    row = conn.execute("SELECT MIN(date) FROM etf_prices").fetchone()
    if row and row[0]:
        d = row[0]
        return d if isinstance(d, date) else date.fromisoformat(str(d))
    return None


def getEtfEarliestDates(conn) -> dict:
    """回傳 {ticker: date}，各 ETF 在 etf_prices 中最早的日期（代表上市/收錄起點）。"""
    rows = conn.execute("SELECT ticker, MIN(date) FROM etf_prices GROUP BY ticker").fetchall()
    result = {}
    for ticker, d in rows:
        if d:
            result[ticker] = d if isinstance(d, date) else date.fromisoformat(str(d))
    return result


# ─────────────────────────────────────────────────────────────
# 投資組合分析
# ─────────────────────────────────────────────────────────────

def analyzePortfolio(conn, portfolio: dict, today: date) -> dict:
    """
    對單一投資組合計算各時間區間的統計指標。
    回傳結構：
      {
        "name": str,
        "allocations": {ticker: weight},
        "periods": {
          "10年": {"cagr": float, "total_return": float, "volatility": float,
                   "sharpe": float, "max_drawdown": float,
                   "start": date, "end": date, "years": float} | None,
          ...
        }
      }
    """
    result = {
        "name": portfolio["name"],
        "description": portfolio.get("description", ""),
        "allocations": portfolio["allocations"],
        "periods": {},
    }

    for periodName, delta in PERIODS:
        if delta is None:
            # 最長期間：從資料庫最早日期開始
            earliest = getEarliestDate(conn)
            startDate = earliest if earliest else date(2010, 1, 1)
        else:
            startDate = today - delta

        stats = computePeriodStats(conn, portfolio, startDate, today)
        result["periods"][periodName] = stats

    return result


def computePeriodStats(
    conn, portfolio: dict, startDate: date, endDate: date, baseCurrency: str = "TWD"
) -> dict | None:
    """
    計算單一時間區間的統計，回傳 None 表示該區間無足夠資料。
    baseCurrency="NATIVE" 時不做幣別轉換，回傳原幣報酬（僅適用於單一 ticker
    組成的假投資組合，用於跟 TWD 換算後的報酬做對照）。
    """
    cumReturnsDict = buildPortfolioCumulativeReturns(
        conn,
        [portfolio],
        startDate,
        endDate,
        baseCurrency=baseCurrency,
    )

    series = cumReturnsDict.get(portfolio["name"])
    if not series or len(series) < 2:
        return None

    stats = computePortfolioStats(series)
    if not stats:
        return None

    # 加上期間起訖日期和年數
    actualStart = date.fromisoformat(series[0]["date"])
    actualEnd   = date.fromisoformat(series[-1]["date"])
    days = (actualEnd - actualStart).days
    years = days / 365.25

    return {
        **stats,
        "start": actualStart,
        "end":   actualEnd,
        "years": round(years, 1),
    }


# ─────────────────────────────────────────────────────────────
# 個別 ETF 分析
# ─────────────────────────────────────────────────────────────

def analyzeEtfs(conn, config: dict, etfInfoMap: dict, today: date) -> list:
    """
    對 config 中每隻 ETF 計算各時間區間的 CAGR。
    每個時間區間同時計算「換算 TWD」與「原幣」兩組統計，方便對照匯率效應
    （例如美元升值可能讓 TWD 報酬高於原幣報酬，反之亦然）。
    回傳：[{ticker, name, exchange, currency, expense_ratio, inception,
            periods: {periodName: {"twd": stats|None, "native": stats|None}}}]
    """
    etfEarliestDates = getEtfEarliestDates(conn)
    results = []

    for etf in config["etfs"]:
        ticker = etf["ticker"]
        info = etfInfoMap.get(ticker, {})
        inception = etfEarliestDates.get(ticker)
        isNativeTwd = info.get("currency") == "TWD"

        # 以單一 ETF 組成 100% 的假投資組合來複用現有計算邏輯
        syntheticPortfolio = {"name": ticker, "allocations": {ticker: 1.0}}
        periods = {}

        for periodName, delta in ETF_PERIODS:
            if delta is None:
                startDate = inception if inception else date(2010, 1, 1)
            else:
                startDate = today - delta

            twdStats = computePeriodStats(conn, syntheticPortfolio, startDate, today, baseCurrency="TWD")
            # TWD 計價的 ETF 原幣即為 TWD，直接沿用同一份結果即可，不必重算
            nativeStats = twdStats if isNativeTwd else computePeriodStats(
                conn, syntheticPortfolio, startDate, today, baseCurrency="NATIVE"
            )
            periods[periodName] = {"twd": twdStats, "native": nativeStats}

        results.append({
            "ticker":        ticker,
            "name":          info.get("name", ticker),
            "exchange":      info.get("exchange", ""),
            "currency":      info.get("currency", "USD"),
            "expense_ratio": info.get("expense_ratio", 0.0),
            "inception":     inception,
            "periods":       periods,
        })

    return results


# ─────────────────────────────────────────────────────────────
# Excel 輸出
# ─────────────────────────────────────────────────────────────

def exportToExcel(allResults: list, config: dict, outputPath: Path, etfResults: list):
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "CAGR 年化報酬率"
    writeCagrSheet(ws1, allResults)

    ws2 = wb.create_sheet("統計指標（最長期間）")
    writeStatsSheet(ws2, allResults)

    ws3 = wb.create_sheet("投資組合成分")
    writeCompositionSheet(ws3, allResults, config)

    ws4 = wb.create_sheet("各 ETF 年化報酬率")
    writeEtfSheet(ws4, etfResults)

    ws5 = wb.create_sheet("各 ETF 風險指標")
    writeEtfRiskSheet(ws5, etfResults)

    wb.save(outputPath)


# ── Sheet 1：CAGR 年化報酬率 ───────────────────────────────────

def writeCagrSheet(ws, allResults: list):
    periodNames = [p[0] for p in PERIODS]
    today = date.today()

    # 標題
    ws.merge_cells("A1:A2")
    ws["A1"] = "投資組合"
    ws.merge_cells(f"B1:{get_column_letter(1 + len(periodNames))}1")
    ws["B1"] = f"各時間區間年化報酬率 CAGR（換算 TWD，含息還原價＝股息再投資之總報酬，已內含基金實際管理費，資料截至 {today}）"

    # 副標題（期間名稱）
    for i, pName in enumerate(periodNames, start=2):
        ws.cell(row=2, column=i, value=pName)

    # 樣式：標題列
    headerFill = PatternFill("solid", fgColor=COLOR_HEADER)
    headerFont = Font(color="FFFFFF", bold=True, size=11)
    for cell in ws[1] + ws[2]:
        cell.fill = headerFill
        cell.font = headerFont
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 22

    # 資料列
    row = 3
    for portfolioResult in allResults:
        desc = portfolioResult.get("description", "")
        nameValue = f"{portfolioResult['name']}\n{desc}" if desc else portfolioResult["name"]
        nameCell = ws.cell(row=row, column=1, value=nameValue)
        nameFill = PatternFill("solid", fgColor=COLOR_PERIOD)
        nameCell.fill = nameFill
        nameCell.font = Font(bold=True)
        nameCell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        if desc:
            ws.row_dimensions[row].height = 30

        for col, pName in enumerate(periodNames, start=2):
            stats = portfolioResult["periods"].get(pName)
            cell = ws.cell(row=row, column=col)

            if stats is None:
                cell.value = "資料不足"
                cell.alignment = Alignment(horizontal="center")
            else:
                cagr = stats.get("cagr")
                if cagr is not None:
                    cagrDecimal = cagr / 100
                    if pName == "最長期間" and stats.get("years"):
                        cell.value = cagrDecimal
                        cell.comment = None
                        # 最長期間加備注
                        from openpyxl.comments import Comment
                        c = Comment(
                            f"期間：{stats['start']} → {stats['end']}（{stats['years']}年）",
                            "auto"
                        )
                        cell.comment = c
                    else:
                        cell.value = cagrDecimal
                    cell.number_format = "0.00%"
                    # 顏色
                    if cagrDecimal >= 0:
                        cell.fill = PatternFill("solid", fgColor=COLOR_POS)
                    else:
                        cell.fill = PatternFill("solid", fgColor=COLOR_NEG)
                    cell.alignment = Alignment(horizontal="right")
                else:
                    cell.value = "資料不足"
                    cell.alignment = Alignment(horizontal="center")

        ws.row_dimensions[row].height = 20
        row += 1

    # 最長期間補充行（顯示年數）
    ws.cell(row=row, column=1, value="（最長期間起始年份）").font = Font(italic=True, color="888888")
    for col, portfolioResult in enumerate(allResults, start=2):
        stats = portfolioResult["periods"].get("最長期間")
        if stats and stats.get("start"):
            ws.cell(row=row, column=col, value=f"自 {stats['start'].year}，共 {stats['years']} 年")
            ws.cell(row=row, column=col).font = Font(italic=True, color="888888", size=9)
            ws.cell(row=row, column=col).alignment = Alignment(horizontal="center")

    # 欄寬
    ws.column_dimensions["A"].width = 16
    for i in range(2, 2 + len(periodNames)):
        ws.column_dimensions[get_column_letter(i)].width = 12

    # 凍結標題
    ws.freeze_panes = "B3"


# ── Sheet 2：統計指標（最長期間）────────────────────────────────

def writeStatsSheet(ws, allResults: list):
    headers = ["投資組合", "期間起始", "期間結束", "年數", "CAGR", "總報酬",
               "年化波動度", "Sharpe Ratio", "最大回撤"]

    # 標題
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = PatternFill("solid", fgColor=COLOR_SUBHEAD)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.row_dimensions[1].height = 22

    pctFormat = "0.00%"
    for row, pr in enumerate(allResults, start=2):
        stats = pr["periods"].get("最長期間")
        desc = pr.get("description", "")
        nameValue = f"{pr['name']}\n{desc}" if desc else pr["name"]
        nameCell = ws.cell(row=row, column=1, value=nameValue)
        nameCell.font = Font(bold=True)
        nameCell.alignment = Alignment(horizontal="left", wrap_text=True)
        if desc:
            ws.row_dimensions[row].height = 30

        if stats:
            ws.cell(row=row, column=2, value=str(stats.get("start", ""))).alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=3, value=str(stats.get("end", ""))).alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=4, value=stats.get("years")).number_format = "0.0"
            ws.cell(row=row, column=4).alignment = Alignment(horizontal="center")

            for col, key in [(5, "cagr"), (6, "total_return"), (7, "volatility"), (9, "max_drawdown")]:
                val = stats.get(key)
                c = ws.cell(row=row, column=col)
                if val is not None:
                    c.value = val / 100
                    c.number_format = pctFormat
                    c.alignment = Alignment(horizontal="right")
                    if col in (5, 6):
                        c.fill = PatternFill("solid", fgColor=COLOR_POS if val >= 0 else COLOR_NEG)

            sharpe = stats.get("sharpe")
            if sharpe is not None:
                c = ws.cell(row=row, column=8, value=sharpe)
                c.number_format = "0.000"
                c.alignment = Alignment(horizontal="right")

    # 欄寬
    widths = [16, 12, 12, 8, 10, 10, 12, 13, 10]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "B2"


# ── Sheet 3：投資組合成分 ─────────────────────────────────────

def writeCompositionSheet(ws, allResults: list, config: dict):
    # 建立 ETF 資訊查找表
    etfLookup = {e["ticker"]: e for e in config["etfs"]}

    headers = ["投資組合", "代號", "名稱", "配置比例", "費用率（年）", "幣別", "交易所"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = PatternFill("solid", fgColor=COLOR_HEADER)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 22

    row = 2
    for pr in allResults:
        fillColor = COLOR_PERIOD
        for ticker, weight in pr["allocations"].items():
            etf = etfLookup.get(ticker, {})
            ws.cell(row=row, column=1, value=pr["name"]).fill = PatternFill("solid", fgColor=fillColor)
            ws.cell(row=row, column=1).font = Font(bold=True)
            ws.cell(row=row, column=2, value=ticker)
            ws.cell(row=row, column=3, value=etf.get("name", ticker))

            wCell = ws.cell(row=row, column=4, value=weight)
            wCell.number_format = "0%"
            wCell.alignment = Alignment(horizontal="right")

            erCell = ws.cell(row=row, column=5, value=etf.get("expense_ratio", 0))
            erCell.number_format = "0.00%"
            erCell.alignment = Alignment(horizontal="right")

            ws.cell(row=row, column=6, value="TWD" if etf.get("exchange") == "TWSE" else "USD")
            ws.cell(row=row, column=7, value=etf.get("exchange", ""))

            for c in range(1, 8):
                ws.cell(row=row, column=c).alignment = Alignment(
                    horizontal=ws.cell(row=row, column=c).alignment.horizontal or "left",
                    vertical="center"
                )
            row += 1

        # 空白分隔行
        row += 1

    widths = [16, 10, 36, 10, 12, 8, 8]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"


# ── Sheet 4：各 ETF 年化報酬率 ────────────────────────────────

STAT_METRIC_HEADERS = ["年化波動度", "Sharpe", "最大回撤", "最大回撤期間"]


def _writePairedHeader(ws, subheadFill, subheadFont, headerAlign, colTwd: int, label: str):
    """在 row2/row3 寫入「跨兩欄合併的指標名稱 + TWD／原幣子標題」，
    供各報表共用。"""
    colNative = colTwd + 1
    ws.merge_cells(start_row=2, start_column=colTwd, end_row=2, end_column=colNative)
    cell = ws.cell(row=2, column=colTwd, value=label)
    cell.fill = subheadFill
    cell.font = subheadFont
    cell.alignment = headerAlign
    for col, subLabel in [(colTwd, "TWD"), (colNative, "原幣")]:
        subCell = ws.cell(row=3, column=col, value=subLabel)
        subCell.fill = subheadFill
        subCell.font = Font(color="FFFFFF", size=9)
        subCell.alignment = headerAlign


def writeEtfSheet(ws, etfResults: list):
    """
    每個時間區間拆成「換算 TWD」與「原幣」兩欄並排對照，方便看出匯率效應
    （例如美元升值時 TWD 欄位可能高於原幣欄位，反之亦然）；TWD 計價的
    ETF（台股）兩欄數字會完全相同，因為本來就不需要轉換。
    風險相關指標（年化波動度／Sharpe／最大回撤）另見「各 ETF 風險指標」分頁。
    """
    periodNames = [p[0] for p in ETF_PERIODS]
    today = date.today()
    fixedHeaders = ["代號", "名稱", "交易所", "費用率", "建立時間"]
    nFixed = len(fixedHeaders)
    nCols = nFixed + len(periodNames) * 2

    # 第一列：大標題
    lastCol = get_column_letter(nCols)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"各 ETF 年化報酬率 CAGR（含息還原價＝股息再投資之總報酬；TWD＝換算台幣後報酬，"
        f"原幣＝未轉換之當地計價貨幣報酬；已內含基金實際管理費，資料截至 {today}）"
    )
    ws["A1"].fill = PatternFill("solid", fgColor=COLOR_HEADER)
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=11)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # 第二、三列：欄位標題（固定欄位跨列合併；時間區間欄位跨兩欄合併後，
    # 第三列再分別標示 TWD／原幣）
    subheadFill = PatternFill("solid", fgColor=COLOR_SUBHEAD)
    subheadFont = Font(color="FFFFFF", bold=True)
    headerAlign = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col, h in enumerate(fixedHeaders, start=1):
        ws.merge_cells(start_row=2, start_column=col, end_row=3, end_column=col)
        cell = ws.cell(row=2, column=col, value=h)
        cell.fill = subheadFill
        cell.font = subheadFont
        cell.alignment = headerAlign

    for i, pName in enumerate(periodNames):
        _writePairedHeader(ws, subheadFill, subheadFont, headerAlign, nFixed + 1 + i * 2, pName)

    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 16

    # 資料列
    pctFormat = "0.00%"
    startRow = 4
    for rowOffset, etf in enumerate(etfResults):
        row = startRow + rowOffset
        ws.cell(row=row, column=1, value=etf["ticker"]).font = Font(bold=True)
        ws.cell(row=row, column=2, value=etf["name"])
        ws.cell(row=row, column=3, value=etf["exchange"]).alignment = Alignment(horizontal="center")

        erCell = ws.cell(row=row, column=4, value=etf["expense_ratio"] or 0)
        erCell.number_format = "0.00%"
        erCell.alignment = Alignment(horizontal="right")

        # 建立時間（ETF 在資料庫中最早的日期）
        inception = etf.get("inception")
        incCell = ws.cell(row=row, column=5, value=str(inception) if inception else "—")
        incCell.alignment = Alignment(horizontal="center")

        # 各時間區間 CAGR（TWD／原幣並排）
        for i, pName in enumerate(periodNames):
            colTwd = nFixed + 1 + i * 2
            colNative = colTwd + 1
            periodStats = etf["periods"].get(pName) or {}

            for col, stats in [(colTwd, periodStats.get("twd")), (colNative, periodStats.get("native"))]:
                cell = ws.cell(row=row, column=col)
                cagr = stats.get("cagr") if stats else None

                if cagr is None:
                    cell.value = "資料不足"
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = Font(color="888888", italic=True, size=9)
                    continue

                cagrDecimal = cagr / 100
                cell.value = cagrDecimal
                cell.number_format = pctFormat
                cell.alignment = Alignment(horizontal="right")
                cell.fill = PatternFill("solid", fgColor=COLOR_POS if cagrDecimal >= 0 else COLOR_NEG)

                # 最長期間加備注顯示起訖年份
                if pName == "最長期間" and stats.get("start"):
                    from openpyxl.comments import Comment
                    cell.comment = Comment(
                        f"期間：{stats['start']} → {stats['end']}（{stats['years']}年）",
                        "auto"
                    )

        ws.row_dimensions[row].height = 18

    # 欄寬
    fixedWidths = [10, 32, 10, 10, 12]
    for i, w in enumerate(fixedWidths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i in range(nFixed + 1, nCols + 1):
        ws.column_dimensions[get_column_letter(i)].width = 10

    ws.freeze_panes = ws.cell(row=startRow, column=nFixed + 1).coordinate


# ── Sheet 5：各 ETF 風險指標 ──────────────────────────────────

def writeEtfRiskSheet(ws, etfResults: list):
    """
    單一標的風險相關指標：年化波動度、Sharpe、最大回撤及其發生期間，
    分別計算 10年／15年／最長期間三個區間，TWD／原幣並列對照。
    獨立成頁籤是因為併入「各 ETF 年化報酬率」會讓表格過寬。
    """
    today = date.today()
    fixedHeaders = ["代號", "名稱", "交易所", "建立時間"]
    nFixed = len(fixedHeaders)
    statGroups = [(p, m) for p in STAT_PERIOD_NAMES for m in STAT_METRIC_HEADERS]
    nCols = nFixed + len(statGroups) * 2
    statsStartCol = nFixed + 1

    # 第一列：大標題
    lastCol = get_column_letter(nCols)
    ws.merge_cells(f"A1:{lastCol}1")
    ws["A1"] = (
        f"各 ETF 風險指標（含息還原價計算；TWD＝換算台幣後報酬，原幣＝未轉換之當地計價貨幣報酬；"
        f"資料截至 {today}）"
    )
    ws["A1"].fill = PatternFill("solid", fgColor=COLOR_HEADER)
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=11)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # 第二、三列：欄位標題
    subheadFill = PatternFill("solid", fgColor=COLOR_SUBHEAD)
    subheadFont = Font(color="FFFFFF", bold=True)
    headerAlign = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col, h in enumerate(fixedHeaders, start=1):
        ws.merge_cells(start_row=2, start_column=col, end_row=3, end_column=col)
        cell = ws.cell(row=2, column=col, value=h)
        cell.fill = subheadFill
        cell.font = subheadFont
        cell.alignment = headerAlign

    for i, (statPeriod, metricName) in enumerate(statGroups):
        _writePairedHeader(
            ws, subheadFill, subheadFont, headerAlign,
            statsStartCol + i * 2, f"{statPeriod}\n{metricName}",
        )

    ws.row_dimensions[2].height = 30
    ws.row_dimensions[3].height = 16

    # 資料列
    pctFormat = "0.00%"
    startRow = 4
    for rowOffset, etf in enumerate(etfResults):
        row = startRow + rowOffset
        ws.cell(row=row, column=1, value=etf["ticker"]).font = Font(bold=True)
        ws.cell(row=row, column=2, value=etf["name"])
        ws.cell(row=row, column=3, value=etf["exchange"]).alignment = Alignment(horizontal="center")

        inception = etf.get("inception")
        incCell = ws.cell(row=row, column=4, value=str(inception) if inception else "—")
        incCell.alignment = Alignment(horizontal="center")

        for i, (statPeriod, metricName) in enumerate(statGroups):
            colTwd = statsStartCol + i * 2
            colNative = colTwd + 1
            periodStats = etf["periods"].get(statPeriod) or {}

            for col, stats in [(colTwd, periodStats.get("twd")), (colNative, periodStats.get("native"))]:
                cell = ws.cell(row=row, column=col)
                if not stats or stats.get("volatility") is None:
                    cell.value = "資料不足"
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = Font(color="888888", italic=True, size=9)
                    continue

                if metricName == "年化波動度":
                    cell.value = stats["volatility"] / 100
                    cell.number_format = pctFormat
                    cell.alignment = Alignment(horizontal="right")
                elif metricName == "Sharpe":
                    cell.value = stats["sharpe"]
                    cell.number_format = "0.000"
                    cell.alignment = Alignment(horizontal="right")
                elif metricName == "最大回撤":
                    cell.value = stats["max_drawdown"] / 100
                    cell.number_format = pctFormat
                    cell.alignment = Alignment(horizontal="right")
                    cell.fill = PatternFill(
                        "solid", fgColor=COLOR_NEG if stats["max_drawdown"] < 0 else COLOR_NEUTRAL
                    )
                else:  # 最大回撤期間
                    cell.value = f"{stats['max_drawdown_start']} ~ {stats['max_drawdown_end']}"
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = Font(size=9)

        ws.row_dimensions[row].height = 18

    # 欄寬
    fixedWidths = [10, 32, 10, 12]
    for i, w in enumerate(fixedWidths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i, (_, metricName) in enumerate(statGroups):
        colTwd = statsStartCol + i * 2
        width = 22 if metricName == "最大回撤期間" else 11
        ws.column_dimensions[get_column_letter(colTwd)].width = width
        ws.column_dimensions[get_column_letter(colTwd + 1)].width = width

    ws.freeze_panes = ws.cell(row=startRow, column=nFixed + 1).coordinate


if __name__ == "__main__":
    main()
