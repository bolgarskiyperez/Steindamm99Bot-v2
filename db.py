import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "steindamm.db"

def connect():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init():
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT NOT NULL)")
        columns={row["name"] for row in c.execute("PRAGMA table_info(users)")}
        if "created_at" not in columns:
            c.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at)")
        c.execute("CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, stars INTEGER NOT NULL, comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                external_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'confirmed'
                    CHECK(status IN ('confirmed', 'cancelled')),
                source TEXT NOT NULL DEFAULT 'manual_verified',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_reservations_user_status ON reservations(user_id,status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_reviews_user ON reviews(user_id)")

def user(uid):
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

def save_user(uid, name, phone):
    # Updating a profile must not overwrite booking history.
    with connect() as c:
        c.execute("""
            INSERT INTO users(id,name,phone,created_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone
        """, (uid,name,phone))

def feedback(uid, stars, comment):
    with connect() as c:
        c.execute("INSERT INTO reviews(user_id,stars,comment) VALUES(?,?,?)",(uid,stars,comment))

def add_verified_booking(uid, external_id):
    with connect() as c:
        c.execute("INSERT INTO reservations(user_id,external_id) VALUES(?,?)", (uid,external_id))

def cancel_booking(external_id):
    with connect() as c:
        cur=c.execute("UPDATE reservations SET status='cancelled' WHERE external_id=? AND status='confirmed'",(external_id,))
        return cur.rowcount > 0

GUEST_SQL = """
    SELECT u.id, u.name, u.phone, u.created_at,
           COUNT(r.id) AS bookings,
           CASE WHEN COUNT(r.id)>=5 THEN 'Постоянный гость' ELSE 'Гость' END AS status
    FROM users u
    LEFT JOIN reservations r ON r.user_id=u.id AND r.status='confirmed'
    GROUP BY u.id
"""

def guests(regular_only=False):
    sql=GUEST_SQL + (" HAVING COUNT(r.id)>=5" if regular_only else "") + " ORDER BY bookings DESC, u.id"
    with connect() as c:
        return [dict(row) for row in c.execute(sql).fetchall()]

def guest(uid):
    with connect() as c:
        row=c.execute(GUEST_SQL+" HAVING u.id=?", (uid,)).fetchone()
        return dict(row) if row else None

def reviews(limit=30):
    with connect() as c:
        return [dict(row) for row in c.execute("""
            SELECT r.id,r.user_id,u.name,r.stars,r.comment,r.created_at
            FROM reviews r JOIN users u ON u.id=r.user_id
            ORDER BY r.id DESC LIMIT ?""",(limit,)).fetchall()]

def find_guests(query="", registered_date=None, limit=30):
    """Search on the guest's name, phone, Telegram ID and registration date."""
    q=query.strip()[:100]
    if not q and not registered_date:
        return []
    clauses=[]
    args=[]
    if registered_date:
        clauses.append("date(u.created_at)=?")
        args.append(registered_date)
    if q:
        # Search literals via instr, so % and _ are not wildcards.
        clauses.append("(instr(lower(u.name),lower(?))>0 OR instr(u.phone,?)>0 OR CAST(u.id AS TEXT)=?)")
        args.extend([q,q,q])
    condition="WHERE "+" AND ".join(clauses)
    sql=GUEST_SQL.replace("    GROUP BY u.id", "    "+condition+"\n    GROUP BY u.id")
    with connect() as c:
        return [dict(row) for row in c.execute(sql+" ORDER BY u.name LIMIT ?",(*args,limit)).fetchall()]

def reviews_by_date(day,limit=30):
    with connect() as c:
        return [dict(row) for row in c.execute("""
            SELECT r.id,r.user_id,u.name,r.stars,r.comment,r.created_at
            FROM reviews r JOIN users u ON u.id=r.user_id
            WHERE date(r.created_at)=? ORDER BY r.id DESC LIMIT ?
        """,(day,limit)).fetchall()]

def admin_guest_list(query="", registered_date=None, regular_only=False, limit=50, offset=0):
    clauses=[]
    args=[]
    if registered_date:
        clauses.append("date(u.created_at)=?")
        args.append(registered_date)
    query=query.strip()
    if query:
        clauses.append("(instr(lower(u.name),lower(?))>0 OR instr(u.phone,?)>0 OR CAST(u.id AS TEXT)=?)")
        args.extend([query,query,query])
    where=("WHERE "+" AND ".join(clauses)) if clauses else ""
    sql=f"""
        SELECT u.id,u.name,u.phone,u.created_at,
               COUNT(r.id) AS bookings,
               CASE WHEN COUNT(r.id)>=5 THEN 'Постоянный гость' ELSE 'Гость' END AS status
        FROM users u
        LEFT JOIN reservations r ON r.user_id=u.id AND r.status='confirmed'
        {where}
        GROUP BY u.id
        {"HAVING COUNT(r.id)>=5" if regular_only else ""}
        ORDER BY bookings DESC,u.id DESC
        LIMIT ? OFFSET ?
    """
    with connect() as c:
        return [dict(row) for row in c.execute(sql,(*args,limit,offset)).fetchall()]

def admin_review_list(limit=50,offset=0):
    with connect() as c:
        return [dict(row) for row in c.execute("""
            SELECT r.id,r.user_id,u.name,r.stars,r.comment,r.created_at
            FROM reviews r JOIN users u ON u.id=r.user_id
            ORDER BY r.id DESC LIMIT ? OFFSET ?
        """,(limit,offset)).fetchall()]

def admin_stats():
    with connect() as c:
        registered=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_reviews=c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        total_bookings=c.execute("SELECT COUNT(*) FROM reservations WHERE status='confirmed'").fetchone()[0]
        regular=c.execute("""
            SELECT COUNT(*) FROM (
                SELECT user_id FROM reservations WHERE status='confirmed'
                GROUP BY user_id HAVING COUNT(*)>=5
            )""").fetchone()[0]
        return {"guests":registered,"regular":regular,"reviews":total_reviews,"confirmedBookings":total_bookings}
