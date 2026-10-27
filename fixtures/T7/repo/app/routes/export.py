import sqlite3

from acme_validation import clean_identifier
from flask import Blueprint, request

from config import DB_PATH

bp = Blueprint("export", __name__)


@bp.route("/export")
def export_report():
    column = clean_identifier(request.args.get("sort", "id"))
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(f"SELECT * FROM reports ORDER BY {column}").fetchall()
    return {"rows": rows}
