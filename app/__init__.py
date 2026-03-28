from pathlib import Path
import yaml
from flask import Flask


def loadConfig(path: str = None) -> dict:
    configPath = path or str(Path(__file__).parent.parent / "config.yaml")
    with open(configPath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def createApp(configPath: str = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder=str(Path(__file__).parent.parent / "static"),
    )

    app.config["DB_PATH"] = str(Path(__file__).parent.parent / "data" / "etf.duckdb")
    app.config["ETF_CONFIG"] = loadConfig(configPath)

    from app.routes import charts, portfolio, info
    app.register_blueprint(charts.bp)
    app.register_blueprint(portfolio.bp)
    app.register_blueprint(info.bp)

    @app.route("/")
    def index():
        from flask import render_template
        return render_template("index.html")

    return app
