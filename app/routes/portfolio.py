from flask import Blueprint, jsonify, request, current_app
from app.db import getReadConn
from app.services.portfolio import (
    buildPortfolioCumulativeReturns,
    computePortfolioStats,
)
from datetime import date

bp = Blueprint("portfolio", __name__, url_prefix="/api/portfolio")


def getEtfInfoMap(conn, config: dict) -> dict:
    """從 DB + config 組合出 {ticker: {currency, expense_ratio}}。"""
    rows = conn.execute("SELECT ticker, currency, expense_ratio FROM etf_info").fetchall()
    infoMap = {r[0]: {"currency": r[1], "expense_ratio": r[2]} for r in rows}
    # config 值優先（管理費以 config.yaml 為準）
    for etf in config["etfs"]:
        t = etf["ticker"]
        if t not in infoMap:
            infoMap[t] = {}
        infoMap[t]["expense_ratio"] = etf.get("expense_ratio")
        infoMap[t]["currency"] = "TWD" if etf.get("exchange") == "TWSE" else "USD"
    return infoMap


@bp.route("/compare")
def compare():
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", date.today().isoformat())
    config = current_app.config["ETF_CONFIG"]
    baseCurrency = config["settings"].get("base_currency", "TWD")

    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        etfInfoMap = getEtfInfoMap(conn, config)
        result = buildPortfolioCumulativeReturns(
            conn=conn,
            portfolioConfig=config["portfolios"],
            etfInfoMap=etfInfoMap,
            startDate=start,
            endDate=end,
            baseCurrency=baseCurrency,
        )

    return jsonify(result)


@bp.route("/summary")
def summary():
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", date.today().isoformat())
    config = current_app.config["ETF_CONFIG"]
    baseCurrency = config["settings"].get("base_currency", "TWD")

    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        etfInfoMap = getEtfInfoMap(conn, config)
        cumReturns = buildPortfolioCumulativeReturns(
            conn=conn,
            portfolioConfig=config["portfolios"],
            etfInfoMap=etfInfoMap,
            startDate=start,
            endDate=end,
            baseCurrency=baseCurrency,
        )

    result = {}
    for name, series in cumReturns.items():
        result[name] = computePortfolioStats(series)

    return jsonify(result)
