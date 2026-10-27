import re

_DISALLOWED = re.compile(r"[^A-Za-z0-9 ]")


def sanitize_term(value):
    return _DISALLOWED.sub("", value)[:64]
