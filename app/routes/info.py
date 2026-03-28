from flask import Blueprint, jsonify, current_app
from app.db import getReadConn

bp = Blueprint("info", __name__, url_prefix="/api/info")


@bp.route("/etfs")
def etfs():
    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        rows = conn.execute("""
            SELECT
                i.ticker, i.name, i.exchange, i.currency,
                i.expense_ratio, i.inception_date, i.last_updated,
                s.last_fetched_date, s.status, s.error_msg
            FROM etf_info i
            LEFT JOIN fetch_status s ON i.ticker = s.ticker
            ORDER BY i.exchange, i.ticker
        """).fetchall()

        cols = ["ticker", "name", "exchange", "currency", "expense_ratio",
                "inception_date", "last_updated", "last_fetched_date", "status", "error_msg"]
        result = [dict(zip(cols, row)) for row in rows]

        # 加入 config 中有但 DB 還沒有的 ETF
        config = current_app.config["ETF_CONFIG"]
        existingTickers = {r["ticker"] for r in result}
        for etf in config["etfs"]:
            if etf["ticker"] not in existingTickers:
                result.append({
                    "ticker": etf["ticker"],
                    "name": etf.get("name"),
                    "exchange": etf.get("exchange"),
                    "currency": "TWD" if etf.get("exchange") == "TWSE" else "USD",
                    "expense_ratio": etf.get("expense_ratio"),
                    "inception_date": None,
                    "last_updated": None,
                    "last_fetched_date": None,
                    "status": "not_fetched",
                    "error_msg": None,
                })

    return jsonify(result)


@bp.route("/fetch_status")
def fetchStatus():
    dbPath = current_app.config["DB_PATH"]
    with getReadConn(dbPath) as conn:
        rows = conn.execute("""
            SELECT ticker, last_fetched_date, last_fetch_ts, status, error_msg
            FROM fetch_status
            ORDER BY ticker
        """).fetchall()
        cols = ["ticker", "last_fetched_date", "last_fetch_ts", "status", "error_msg"]
        result = [dict(zip(cols, row)) for row in rows]
    return jsonify(result)
