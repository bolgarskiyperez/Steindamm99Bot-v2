"""Local demonstration booking ledger. Replace this repository with a PostgreSQL implementation for production."""
import sqlite3
from db import DB
from datetime import datetime

def connect():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    return c

def init():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS test_booking_operations(
            request_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            visit_date TEXT NOT NULL,
            visit_time TEXT NOT NULL,
            party_size INTEGER NOT NULL,
            waitlist INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'pending',
            external_id TEXT UNIQUE,
            external_number TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_test_operations_user ON test_booking_operations(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_test_operations_external ON test_booking_operations(external_id)")

def start(key,uid,name,phone,date,time,count,waitlist):
    init()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT * FROM test_booking_operations WHERE request_key=?",(key,)).fetchone()
        if row:
            if row["user_id"]!=uid: raise ValueError("Запрос принадлежит другому пользователю")
            return False,dict(row)
        c.execute("""INSERT INTO test_booking_operations
            (request_key,user_id,name,phone,visit_date,visit_time,party_size,waitlist)
            VALUES(?,?,?,?,?,?,?,?)""",(key,uid,name,phone,date,time,count,int(waitlist)))
        return True,None

def finish(key,state,external=None,number=None):
    init()
    with connect() as c:
        c.execute("""UPDATE test_booking_operations SET state=?,external_id=COALESCE(?,external_id),
            external_number=COALESCE(?,external_number),updated_at=CURRENT_TIMESTAMP
            WHERE request_key=?""",(state,str(external) if external is not None else None,
                                     str(number) if number is not None else None,key))

def mine(uid):
    init()
    with connect() as c:
        return [dict(x) for x in c.execute("""SELECT request_key,visit_date,visit_time,party_size,
            waitlist,state,external_id,external_number,created_at FROM test_booking_operations
            WHERE user_id=? ORDER BY created_at DESC LIMIT 50""",(uid,)).fetchall()]

def all_bookings(limit=100):
    init()
    with connect() as c:
        return [dict(x) for x in c.execute("""SELECT user_id,name,phone,visit_date,visit_time,party_size,
            waitlist,state,external_id,external_number,created_at FROM test_booking_operations
            ORDER BY created_at DESC LIMIT ?""",(limit,)).fetchall()]

def event_for_known_booking(reserve_id,status):
    init()
    if not str(reserve_id).isdigit():return False
    state={1:"pending_review",2:"pending_review",3:"confirmed",4:"arrived",
           5:"closed",6:"cancelled",8:"cancelled"}.get(status,"updated")
    with connect() as c:
        cursor=c.execute("""UPDATE test_booking_operations SET state=?,updated_at=CURRENT_TIMESTAMP
                            WHERE external_id=?""",(state,str(reserve_id)))
        return cursor.rowcount>0

def summary():
    init()
    with connect() as c:
        booked=c.execute("SELECT COUNT(*) FROM test_booking_operations").fetchone()[0]
        waiting=c.execute("SELECT COUNT(*) FROM test_booking_operations WHERE waitlist=1").fetchone()[0]
        arrived=c.execute("SELECT COUNT(*) FROM test_booking_operations WHERE state='arrived'").fetchone()[0]
        regular=c.execute("""SELECT COUNT(*) FROM (
                SELECT user_id FROM test_booking_operations WHERE state IN ('arrived','closed')
                GROUP BY user_id HAVING COUNT(*)>=5)""").fetchone()[0]
        return {"bookings":booked,"waitlist":waiting,"visits":arrived,"regularGuests":regular}
