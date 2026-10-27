import subprocess

from flask import Blueprint, request

bp = Blueprint("ping", __name__)


@bp.route("/ping")
def ping():
    host = request.args["host"]
    cmd = f"ping -c 1 {host}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return {"output": result.stdout}
