import sqlite3

from flask import Blueprint, request

from app.security import sanitize_term
from config import DB_PATH

bp = Blueprint("search", __name__)


@bp.route("/search")
def search():
    term = sanitize_term(request.args.get("q", ""))
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, title FROM docs WHERE title LIKE '%" + term + "%'").fetchall()
    return {"results": rows}
