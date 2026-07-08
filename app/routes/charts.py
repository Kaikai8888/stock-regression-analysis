from flask import Blueprint, jsonify, request, current_app
from app.db import getReadConn
import pandas as pd

bp = Blueprint("charts", __name__, url_prefix="/api/prices")


@bp.route("/<ticker>")
def prices(ticker: str):
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", "2099-12-31")
    dbPath = current_app.config["DB_PATH"]

    with getReadConn(dbPath) as conn:
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM etf_prices
            WHERE ticker = ? AND date BETWEEN ? AND ?
            ORDER BY date
        """, [ticker.upper(), start, end]).fetchall()

    cols = ["date", "open", "high", "low", "close", "volume"]
    data = [dict(zip(cols, r)) for r in rows]
    return jsonify(data)


@bp.route("/normalized")
def normalized():
    """
    回傳多支 ETF 的正規化收盤價（起始值 = 100），
    USD ETF 換算為 TWD（或 config 指定的 base_currency）。
    """
    tickersParam = request.args.get("tickers", "")
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", "2099-12-31")
    tickers = [t.strip().upper() for t in tickersParam.split(",") if t.strip()]

    if not tickers:
        return jsonify({})

    dbPath = current_app.config["DB_PATH"]
    baseCurrency = current_app.config["ETF_CONFIG"]["settings"].get("base_currency", "TWD")

    with getReadConn(dbPath) as conn:
        placeholders = ",".join(["?" for _ in tickers])
        pricesDf = conn.execute(
            f"SELECT ticker, date, close, currency FROM etf_prices "
            f"WHERE ticker IN ({placeholders}) AND date BETWEEN ? AND ? ORDER BY ticker, date",
            tickers + [start, end]
        ).df()

        fxDf = conn.execute(
            "SELECT date, rate FROM fx_rates WHERE date BETWEEN ? AND ? ORDER BY date",
            [start, end]
        ).df()

    if pricesDf.empty:
        return jsonify({})

    from app.services.portfolio import convertToCurrency
    converted = convertToCurrency(pricesDf, fxDf, baseCurrency)

    result = {}
    for ticker, group in converted.groupby("ticker"):
        group = group.sort_values("date")
        firstClose = group["close"].iloc[0]
        if firstClose and firstClose != 0:
            group = group.copy()
            group["value"] = (group["close"] / firstClose * 100).round(4)
            result[ticker] = group[["date", "value"]].rename(columns={"date": "date"}).to_dict("records")

    return jsonify(result)


@bp.route("/regression")
def regression():
    """
    回傳各 ETF 相對於 benchmark 的回歸統計數據。
    先將所有 ETF 換算為統一幣別（base_currency），避免台股 ETF（TWD）
    與美股 benchmark（USD）直接用不同幣別的日報酬做迴歸，污染 beta/alpha。
    """
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", "2099-12-31")
    config = current_app.config["ETF_CONFIG"]
    benchmarkTicker = config["settings"].get("benchmark", "VT")
    baseCurrency = config["settings"].get("base_currency", "TWD")
    tickers = [e["ticker"] for e in config["etfs"]]

    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        placeholders = ",".join(["?" for _ in tickers])
        pricesDf = conn.execute(
            f"SELECT ticker, date, close, currency FROM etf_prices "
            f"WHERE ticker IN ({placeholders}) AND date BETWEEN ? AND ? ORDER BY ticker, date",
            tickers + [start, end]
        ).df()

        fxDf = conn.execute(
            "SELECT date, rate FROM fx_rates WHERE date BETWEEN ? AND ? ORDER BY date",
            [start, end]
        ).df()

    if pricesDf.empty:
        return jsonify({})

    from app.services.portfolio import convertToCurrency
    from app.services.regression import computeRegression, computeRollingBeta
    pricesDf = convertToCurrency(pricesDf, fxDf, baseCurrency)
    regressionStats = computeRegression(pricesDf, benchmarkTicker)
    rollingBetas = computeRollingBeta(pricesDf, benchmarkTicker, windowDays=60)

    return jsonify({
        "benchmark": benchmarkTicker,
        "stats": regressionStats,
        "rolling_beta": rollingBetas,
    })
