import os

from flask import Blueprint, abort, request, send_file

bp = Blueprint("files", __name__)
BASE_DIR = os.path.realpath("/srv/app/uploads")


@bp.route("/download")
def download():
    requested = request.args.get("name", "")
    path = os.path.realpath(os.path.join(BASE_DIR, requested))
    if not path.startswith(BASE_DIR + os.sep):
        abort(400)
    return send_file(path)
