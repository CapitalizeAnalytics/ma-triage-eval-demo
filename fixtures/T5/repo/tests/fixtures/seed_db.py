import sqlite3

USERS = [("alice", "alice@example.com"), ("bob", "bob@example.com")]


def seed(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT, email TEXT)")
    for name, email in USERS:
        conn.execute("INSERT INTO users VALUES ('" + name + "', '" + email + "')")
    conn.commit()
