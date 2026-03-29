import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "etf.duckdb"

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS etf_prices (
        ticker   VARCHAR NOT NULL,
        date     DATE    NOT NULL,
        open     DOUBLE,
        high     DOUBLE,
        low      DOUBLE,
        close    DOUBLE NOT NULL,
        volume   BIGINT,
        currency VARCHAR NOT NULL,
        PRIMARY KEY (ticker, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS etf_info (
        ticker         VARCHAR PRIMARY KEY,
        name           VARCHAR,
        exchange       VARCHAR,
        currency       VARCHAR,
        expense_ratio  DOUBLE,
        inception_date DATE,
        last_updated   TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_status (
        ticker            VARCHAR PRIMARY KEY,
        last_fetched_date DATE,
        last_fetch_ts     TIMESTAMP,
        status            VARCHAR,
        error_msg         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fx_rates (
        date      DATE    NOT NULL,
        from_ccy  VARCHAR NOT NULL,
        to_ccy    VARCHAR NOT NULL,
        rate      DOUBLE  NOT NULL,
        PRIMARY KEY (date, from_ccy, to_ccy)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS etf_tracking_diff (
        ticker           VARCHAR NOT NULL,
        quarter          VARCHAR NOT NULL,  -- 格式：'2025Q4'
        market_return    DOUBLE,            -- 市價季報酬率 (%)
        benchmark_return DOUBLE,            -- 基準指數漲跌幅 (%)
        diff             DOUBLE,            -- 差額 = market_return - benchmark_return (%)
        PRIMARY KEY (ticker, quarter)
    )
    """,
]


def initDb(dbPath: str = None) -> duckdb.DuckDBPyConnection:
    path = dbPath or str(DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(path)
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    return conn


def getReadConn(dbPath: str = None) -> duckdb.DuckDBPyConnection:
    path = dbPath or str(DB_PATH)
    return duckdb.connect(path, read_only=True)
