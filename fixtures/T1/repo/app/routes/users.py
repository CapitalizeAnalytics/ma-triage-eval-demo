import sqlite3

from flask import Blueprint, request

from config import DB_PATH

bp = Blueprint("users", __name__)


@bp.route("/users")
def find_user():
    name = request.args.get("name", "")
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT id, email FROM users WHERE name = '" + name + "'"
    rows = conn.execute(query).fetchall()
    return {"users": rows}
