from flask import Blueprint, jsonify, request, current_app
from app.db import getReadConn
from app.services.portfolio import (
    buildPortfolioCumulativeReturns,
    computePortfolioStats,
)
from datetime import date

bp = Blueprint("portfolio", __name__, url_prefix="/api/portfolio")


@bp.route("/compare")
def compare():
    start = request.args.get("start", "2020-01-01")
    end = request.args.get("end", date.today().isoformat())
    config = current_app.config["ETF_CONFIG"]
    baseCurrency = config["settings"].get("base_currency", "TWD")

    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        result = buildPortfolioCumulativeReturns(
            conn=conn,
            portfolioConfig=config["portfolios"],
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
        cumReturns = buildPortfolioCumulativeReturns(
            conn=conn,
            portfolioConfig=config["portfolios"],
            startDate=start,
            endDate=end,
            baseCurrency=baseCurrency,
        )

    result = {}
    for name, series in cumReturns.items():
        result[name] = computePortfolioStats(series)

    return jsonify(result)
