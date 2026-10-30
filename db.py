import sqlite3
from pathlib import Path
DB = Path(__file__).parent / "steindamm.db"
def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c
def init():
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, stars INTEGER NOT NULL, comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
def user(uid):
    with connect() as c:
        row=c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None
def save_user(uid,name,phone):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO users(id,name,phone) VALUES(?,?,?)",(uid,name,phone))
def feedback(uid,stars,comment):
    with connect() as c:
        c.execute("INSERT INTO reviews(user_id,stars,comment) VALUES(?,?,?)",(uid,stars,comment))
