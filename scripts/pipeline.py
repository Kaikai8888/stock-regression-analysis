"""
一鍵執行完整分析流程：

  步驟 1  從 MoneyDJ 抓取所有 ETF 季度追蹤差額（已有上季資料則跳過）
  步驟 2  初始化 / 增量更新歷史股價資料（DuckDB）
  步驟 3  從 MoneyDJ 取得最新台灣 ETF 費用率，更新 config.yaml
  步驟 4  將 config.yaml 費用率同步至 etf_info 資料表
  步驟 5  計算各投資組合 × 各時間區間的年化報酬率，輸出 xlsx 報表

執行方式：
  python scripts/pipeline.py                        # 完整流程
  python scripts/pipeline.py --skip-fetch           # 跳過股價抓取（DB 已是最新時使用）
  python scripts/pipeline.py --skip-expense         # 跳過費用率更新
  python scripts/pipeline.py --skip-tracking-diff   # 跳過追蹤差額抓取
  python scripts/pipeline.py --output my_report.xlsx
  python scripts/pipeline.py --force-initial        # 強制重跑 fetch_initial（完整重抓）
"""
import sys
import argparse
import subprocess
import time
from pathlib import Path
from datetime import date

import yaml
import duckdb

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.db import initDb, DB_PATH

CONFIG_PATH = ROOT / "config.yaml"
REPORTS_DIR = ROOT / "reports"


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETF 投資組合分析一鍵執行")
    parser.add_argument("--skip-fetch",          action="store_true", help="跳過股價資料更新")
    parser.add_argument("--skip-expense",        action="store_true", help="跳過費用率更新")
    parser.add_argument("--skip-tracking-diff",  action="store_true", help="跳過追蹤差額抓取")
    parser.add_argument("--force-initial",       action="store_true", help="強制完整重抓所有歷史資料")
    parser.add_argument("--output",              type=str,            help="報表輸出路徑")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(exist_ok=True)
    outputPath = Path(args.output) if args.output else (
        REPORTS_DIR / f"portfolio_analysis_{date.today()}.xlsx"
    )

    startTime = time.time()
    printBanner()

    # ── Step 1：季度追蹤差額 ─────────────────────────────────
    if not args.skip_tracking_diff:
        step("1", "從 MoneyDJ 抓取所有 ETF 季度追蹤差額")
        runScript("scripts/fetch_tracking_diff.py")
    else:
        print("  [步驟 1] 已跳過（--skip-tracking-diff）\n")

    # ── Step 2：股價資料 ──────────────────────────────────────
    if not args.skip_fetch:
        step("2", "股價資料初始化 / 增量更新")
        if args.force_initial or not dbHasData():
            runScript("scripts/fetch_initial.py", "初次抓取全部歷史資料（耗時較長）")
        else:
            runScript("scripts/update_data.py", label="增量更新至最新交易日")
    else:
        print("  [步驟 2] 已跳過（--skip-fetch）\n")

    # ── Step 3：費用率更新 ────────────────────────────────────
    if not args.skip_expense:
        step("3", "從 MoneyDJ 更新台灣 ETF 費用率 → config.yaml")
        runScript("scripts/fetch_expense_ratios.py", "--update")
    else:
        print("  [步驟 3] 已跳過（--skip-expense）\n")

    # ── Step 4：費用率同步至 DB ───────────────────────────────
    step("4", "將 config.yaml 費用率同步至 etf_info 資料表")
    syncExpenseRatiosToDB()

    # ── Step 5：報酬率分析 ────────────────────────────────────
    step("5", f"計算投資組合報酬率 → {outputPath.name}")
    runScript("scripts/analyze_portfolios.py", "--output", str(outputPath))

    elapsed = time.time() - startTime
    print(f"\n{'='*60}")
    print(f"✓ 完整流程結束，耗時 {elapsed:.0f} 秒")
    print(f"  報表路徑：{outputPath.resolve()}")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────────────────────

def printBanner():
    print("=" * 60)
    print("  ETF 投資組合分析 Pipeline")
    print(f"  執行日期：{date.today()}")
    print("=" * 60)
    print()


def step(num: str, description: str):
    print(f"[步驟 {num}] {description}")
    print("-" * 50)


def runScript(*args, label: str = None):
    """執行 python3 <腳本> [參數...]，失敗時印出警告但繼續執行。"""
    cmd = [sys.executable] + [str(a) for a in args]
    desc = label or " ".join(str(a) for a in args)
    print(f"  執行：{' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=str(ROOT), check=True, text=True,
                                capture_output=False)
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ 腳本執行失敗（exit code {e.returncode}），繼續下一步驟")
    print()


def dbHasData() -> bool:
    """檢查 DuckDB 是否已有股價資料。"""
    if not Path(DB_PATH).exists():
        return False
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        count = conn.execute("SELECT COUNT(*) FROM etf_prices").fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def syncExpenseRatiosToDB():
    """
    讀取 config.yaml 的最新費用率，更新 DuckDB etf_info 資料表。
    確保 analyze_portfolios.py 使用的 DB 費用率與 config.yaml 一致。
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not Path(DB_PATH).exists():
        print("  ⚠ DB 不存在，跳過費用率同步")
        print()
        return

    conn = initDb()
    updated = 0
    for etf in config.get("etfs", []):
        ticker = etf["ticker"]
        er = etf.get("expense_ratio")
        if er is None:
            continue
        # 先確認 ticker 存在於 etf_info
        exists = conn.execute(
            "SELECT COUNT(*) FROM etf_info WHERE ticker = ?", [ticker]
        ).fetchone()[0]
        if exists:
            conn.execute(
                "UPDATE etf_info SET expense_ratio = ?, last_updated = current_timestamp "
                "WHERE ticker = ?",
                [er, ticker]
            )
            updated += 1
        else:
            # 若 DB 尚未有此 ticker 的 info（首次 fetch_initial 後才會有）
            pass

    conn.close()
    print(f"  已更新 {updated} 支 ETF 的費用率（來源：config.yaml）\n")


if __name__ == "__main__":
    main()
