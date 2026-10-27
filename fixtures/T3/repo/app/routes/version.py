import sqlite3

from flask import Blueprint, request

from config import DB_PATH

bp = Blueprint("version", __name__)


@bp.route("/version")
def app_version():
    key = request.args.get("key", "version")
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT v FROM config WHERE k = '" + key + "'").fetchone()
    return {"version": row[0] if row else None}
